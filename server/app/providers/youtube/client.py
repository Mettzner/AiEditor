"""YouTube Data API v3 (só Creative Commons) + download do trecho com yt-dlp (§7.3)."""
from __future__ import annotations

import os
import re
from pathlib import Path

from ...config import get_secret, load_settings
from ..http import ProviderError, request
from ..stock.base import Candidate
from . import quota

API = "https://www.googleapis.com/youtube/v3"
QUOTA_REASONS = {"quotaExceeded", "dailyLimitExceeded", "rateLimitExceeded"}
LICENSE = "Creative Commons BY (YouTube)"


class QuotaExhausted(ProviderError):
    """Cota do dia esgotada. `reactive=True` quando foi o Google que recusou (HTTP 403)."""

    def __init__(self, message: str, reactive: bool = False):
        super().__init__(message, 403 if reactive else None)
        self.reactive = reactive


def _key() -> str:
    key = get_secret("youtube")
    if not key:
        raise ProviderError("Chave da YouTube Data API não configurada", 401)
    return key


def _get(path: str, params: dict, cost: int) -> dict:
    quota.spend(cost)  # registrado antes: conta mesmo se a chamada falhar
    resp = request("GET", f"{API}/{path}", params={**params, "key": _key()})
    if resp.status_code == 403:
        try:
            errors = resp.json().get("error", {}).get("errors", [])
        except ValueError:
            errors = []
        if any(e.get("reason") in QUOTA_REASONS for e in errors):
            quota.mark_exhausted()
            raise QuotaExhausted("O YouTube recusou por cota esgotada", reactive=True)
    if resp.status_code >= 400:
        raise ProviderError(f"YouTube {path}: HTTP {resp.status_code}", resp.status_code, resp.text[:300])
    return resp.json()


def _iso_duration(s: str) -> float:
    m = re.fullmatch(r"P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", s or "")
    if not m:
        return 0.0
    d, h, mi, se = (int(x or 0) for x in m.groups())
    return float(d * 86400 + h * 3600 + mi * 60 + se)


def search(query: str, per_page: int = 15, lang: str = "en") -> list[Candidate]:
    if not quota.can_search():
        raise QuotaExhausted("Cota do YouTube de hoje esgotada (reserva de segurança atingida)")
    body = _get("search", {
        "part": "snippet", "type": "video", "q": query, "maxResults": min(50, per_page),
        "videoLicense": "creativeCommon", "videoDefinition": "high", "relevanceLanguage": lang,
        "safeSearch": "strict",
    }, quota.SEARCH_COST)
    ids = [it["id"]["videoId"] for it in body.get("items", []) if it.get("id", {}).get("videoId")]
    if not ids:
        return []
    # part=player com maxWidth devolve embedWidth/embedHeight na proporção do vídeo (mesmo custo de 1 unidade):
    # é o único jeito de saber pela API se o vídeo é vertical (Shorts), antes de avaliar e baixar
    details = _get("videos", {"part": "snippet,contentDetails,status,player", "id": ",".join(ids),
                              "maxWidth": 1280}, quota.VIDEOS_COST)
    sel = load_settings()["selection"]
    lo, hi = sel.get("min_aspect", 1.55), sel.get("max_aspect", 2.0)
    out = []
    for v in details.get("items", []):
        if v.get("status", {}).get("license") != "creativeCommon":  # confirma a licença
            continue
        sn, cd = v.get("snippet", {}), v.get("contentDetails", {})
        vid = v["id"]
        aspect = player_aspect(v.get("player") or {})
        if (aspect is not None and not lo <= aspect <= hi) or "#shorts" in (sn.get("title", "") or "").lower():
            continue
        out.append(Candidate(
            provider="youtube", external_id=vid,
            title=" ".join([sn.get("title", "")] + sn.get("tags", [])[:15]),
            duration=_iso_duration(cd.get("duration", "")),
            # a API não informa a resolução; videoDefinition=high garante ao menos 720p
            width=1280 if cd.get("definition") == "hd" else 640, height=720 if cd.get("definition") == "hd" else 360,
            page_url=f"https://www.youtube.com/watch?v={vid}",
            thumbnail=f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg", query=query,
            author=sn.get("channelTitle", ""), category=str(sn.get("categoryId", "")),
            author_url=f"https://www.youtube.com/channel/{sn.get('channelId', '')}", license=LICENSE,
            # hq1/hq2/hq3 ≈ 25/50/75% do vídeo; hqdefault entra só no desempate (4 frames)
            preview_frames=[f"https://i.ytimg.com/vi/{vid}/hq{i}.jpg" for i in (1, 2, 3)],
            frame_positions=[0.25, 0.5, 0.75],
        ))
    return out


def player_aspect(player: dict) -> float | None:
    """Proporção do vídeo pelo player embutido (embedWidth/embedHeight); None se a API não informar."""
    try:
        w, h = float(player["embedWidth"]), float(player["embedHeight"])
    except (KeyError, TypeError, ValueError):
        return None
    return w / h if h > 0 else None


def download_segment(video_id: str, start: float, end: float, dest: Path) -> Path:
    """Baixa só o trecho [start, end] em até 1080p, sem áudio (a narração cobre o som)."""
    import yt_dlp
    from yt_dlp.utils import download_range_func

    from ...pipeline.render import ffmpeg

    dest.parent.mkdir(parents=True, exist_ok=True)
    ff_dir = str(Path(ffmpeg.ffmpeg_bin()).parent)
    # o download parcial do yt-dlp checa o ffmpeg só pelo PATH (ignora ffmpeg_location)
    if ff_dir.lower() not in os.environ.get("PATH", "").lower():
        os.environ["PATH"] = ff_dir + os.pathsep + os.environ.get("PATH", "")
    sel = load_settings()["selection"]
    lo, hi = sel.get("min_aspect", 1.55), sel.get("max_aspect", 2.0)
    # a Data API não informa a resolução: a proporção horizontal é exigida aqui, na escolha do formato
    shape = f"[height<=1080][height>={sel.get('youtube_min_height', 720)}][aspect_ratio>={lo}][aspect_ratio<={hi}]"
    opts = {
        "format": f"bv*{shape}[ext=mp4]/bv*{shape}/b{shape}",
        "outtmpl": str(dest.with_suffix(".%(ext)s")),
        "download_ranges": download_range_func(None, [(max(0.0, start), end)]),
        "force_keyframes_at_cuts": True,
        "ffmpeg_location": ff_dir,
        "quiet": True, "no_warnings": True, "noprogress": True,
        "merge_output_format": "mp4",
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.download([f"https://www.youtube.com/watch?v={video_id}"])
    produced = sorted(dest.parent.glob(dest.stem + ".*"), key=lambda p: p.stat().st_mtime, reverse=True)
    produced = [p for p in produced if p.suffix not in (".part", ".ytdl")]
    if not produced:
        raise ProviderError(f"yt-dlp não gerou arquivo para {video_id}")
    if produced[0] != dest:
        produced[0].replace(dest)
    video = next((st for st in ffmpeg.probe(dest)["streams"] if st.get("codec_type") == "video"), None)
    if not video or not (lo <= video["width"] / max(1, video["height"]) <= hi):
        dest.unlink(missing_ok=True)
        raise ProviderError(f"{video_id}: proporção fora de {lo}–{hi} ({video and (video['width'], video['height'])})")
    return dest


def max_duration() -> float:
    return float(load_settings()["youtube"].get("max_duration", 1800))


def test() -> dict:
    _get("videos", {"part": "id", "id": "jNQXAC9IVRw"}, quota.VIDEOS_COST)
    st = quota.status()
    return {"ok": True, "detail": f"chave válida · cota restante hoje: {st['available']} un. "
                                  f"(≈ {st['searches_left']} buscas)"}
