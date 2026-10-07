"""YouTube Data API v3 (só Creative Commons) + download do trecho com yt-dlp (§7.3)."""
from __future__ import annotations

import os
import re
import time
from pathlib import Path

from ...config import get_secret, load_settings
from ..http import ProviderError, request
from ..stock.base import Candidate
from . import quota

API = "https://www.googleapis.com/youtube/v3"
DAILY_REASONS = {"quotaExceeded", "dailyLimitExceeded", "dailyLimitExceededUnreg"}
MINUTE_REASONS = {"rateLimitExceeded", "userRateLimitExceeded"}
CREDENTIAL_REASONS = {"keyInvalid", "keyExpired", "accessNotConfigured", "ipRefererBlocked", "forbidden",
                      "API_KEY_INVALID", "API_KEY_SERVICE_BLOCKED"}
LICENSE = "Creative Commons BY (YouTube)"
RETRIES = 2  # 5xx/rede: cada nova tentativa é um pedido novo e reserva cota de novo


class QuotaExhausted(ProviderError):
    """Sem cota para o pedido. `reactive=True` quando foi o Google que recusou; kind: daily | minute."""

    def __init__(self, message: str, reactive: bool = False, kind: str = "daily", bucket: str = ""):
        super().__init__(message, 403 if reactive else None)
        self.reactive = reactive
        self.kind = kind
        self.bucket = bucket


class CredentialError(ProviderError):
    """Chave ausente/inválida, API desativada no projeto ou restrição de origem: não é falta de cota."""


def _key() -> str:
    key = get_secret("youtube")
    if not key:
        raise CredentialError("Chave da YouTube Data API não configurada", 401)
    return key


def _reasons(resp) -> set[str]:
    try:
        err = resp.json().get("error", {})
    except ValueError:
        return set()
    out = {e.get("reason") for e in err.get("errors", []) or [] if e.get("reason")}
    out |= {d.get("reason") for d in err.get("details", []) or [] if isinstance(d, dict) and d.get("reason")}
    return out


def _get(path: str, params: dict, endpoint: str) -> dict:
    """Um pedido à Data API. Reserva a cota do endpoint antes de CADA envio (retentativas incluídas)."""
    key = _key()  # sem chave o pedido não sai: nada é reservado
    attempt = 0
    while True:
        try:
            res = quota.reserve(endpoint)
        except quota.QuotaBlocked as e:
            raise QuotaExhausted(str(e), kind=e.kind, bucket=e.bucket) from e
        try:
            resp = request("GET", f"{API}/{path}", params={**params, "key": key}, retries=0)
        except ProviderError:
            res.uncertain()  # saiu e não voltou (ou não se sabe): continua contado
            if attempt >= RETRIES:
                raise
            attempt += 1
            time.sleep(1.5 * attempt)
            continue
        if resp.status_code in (400, 401, 403, 429):
            reasons = _reasons(resp)
            if reasons & DAILY_REASONS:
                quota.mark_exhausted(bucket=res.bucket)
                raise QuotaExhausted("O YouTube recusou por cota diária esgotada", reactive=True, kind="daily",
                                     bucket=res.bucket)
            if reasons & MINUTE_REASONS or resp.status_code == 429:
                quota.block_minute(res.bucket)
                raise QuotaExhausted("O YouTube recusou por limite de pedidos por minuto", reactive=True,
                                     kind="minute", bucket=res.bucket)
            if reasons & CREDENTIAL_REASONS or resp.status_code in (401, 403):
                detail = ", ".join(sorted(reasons)) or str(resp.status_code)
                raise CredentialError(f"YouTube {path}: credencial recusada ({detail})", resp.status_code,
                                      resp.text[:300])
        if resp.status_code >= 500 and attempt < RETRIES:
            attempt += 1
            time.sleep(1.5 * attempt)
            continue
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
    return search_page(query, per_page, lang)[0]


def search_page(query: str, per_page: int = 50, lang: str = "en", page_token: str | None = None,
                ) -> tuple[list[Candidate], str | None]:
    """Uma página de busca (search.list, até 50 resultados) + detalhes (videos.list) → (candidatos, próxima página).

    Cada página é uma chamada externa nova: quem pagina decide quando parar (orçamento e utilidade)."""
    params = {
        "part": "snippet", "type": "video", "q": query, "maxResults": max(1, min(50, int(per_page))),
        "videoLicense": "creativeCommon", "videoDefinition": "high", "relevanceLanguage": lang,
        "safeSearch": "strict",
    }
    if page_token:
        params["pageToken"] = page_token
    body = _get("search", params, "search")
    next_token = body.get("nextPageToken")
    ids = [it["id"]["videoId"] for it in body.get("items", []) if it.get("id", {}).get("videoId")]
    if not ids:
        return [], next_token
    # part=player com maxWidth devolve embedWidth/embedHeight na proporção do vídeo (mesmo custo de 1 unidade):
    # é o único jeito de saber pela API se o vídeo é vertical (Shorts), antes de avaliar e baixar
    details = _get("videos", {"part": "snippet,contentDetails,status,player", "id": ",".join(ids),
                              "maxWidth": 1280}, "videos")
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
            # hq1/hq2/hq3: miniaturas automáticas de posição APROXIMADA (≈ 25/50/75%); servem para descobrir o
            # assunto, não garantem o corte (o trecho final é conferido depois do download)
            preview_frames=[f"https://i.ytimg.com/vi/{vid}/hq{i}.jpg" for i in (1, 2, 3)],
            frame_positions=[0.25, 0.5, 0.75],
            published_at=sn.get("publishedAt", ""), channel_id=sn.get("channelId", ""),
            description=(sn.get("description") or "")[:500],
        ))
    return out, next_token


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
    _get("videos", {"part": "id", "id": "jNQXAC9IVRw"}, "videos")
    st = quota.status()
    unit = "chamadas" if st["unit"] == "calls" else "un."
    return {"ok": True, "detail": f"chave válida · ≈ {st['searches_left']} busca(s) restante(s) hoje "
                                  f"({st['available']} {unit} no bucket de busca)"}
