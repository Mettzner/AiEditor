"""Acervos históricos (CONTEXTO_PROFUNDO_DO_ROTEIRO.md §5): pinturas, gravuras, fotos e filmes de arquivo grátis.

- Wikimedia Commons: API MediaWiki (busca no namespace de arquivos + imageinfo/extmetadata com a licença).
- Library of Congress: API JSON de fotos (www.loc.gov/photos/?fo=json).
- Internet Archive: busca avançada (filmes) + metadata do item (arquivo de vídeo, miniaturas e licença).

Só entra material em domínio público, CC0 ou CC BY, com autor e licença registrados para os créditos.
Qualquer coisa sem licença clara (ou com SA/NC/ND) é descartada.
"""
from __future__ import annotations

import html
import logging
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from ...config import load_settings
from ..http import ProviderError, json_or_raise, request
from .base import Candidate, Rendition

log = logging.getLogger("aieditor.archives")

ARCHIVE_PROVIDERS = ("wikimedia", "loc", "internet_archive")
# As APIs pedem um User-Agent que identifique a ferramenta
HEADERS = {"User-Agent": "AiEditor/0.1 (https://github.com/Mettzner/AiEditor; historical archive search)"}
COMMONS = "https://commons.wikimedia.org/w/api.php"
LOC = "https://www.loc.gov/photos/"
IA_SEARCH = "https://archive.org/advancedsearch.php"
IA_META = "https://archive.org/metadata/{id}"
IA_DOWNLOAD = "https://archive.org/download/{id}/{name}"
VIDEO_FORMATS = ("h.264", "h.264 IA", "MPEG4", "512Kb MPEG4", "h.264 HD")


def _cfg() -> dict:
    return load_settings().get("archives", {})


def _strip_html(text: str) -> str:
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", text or "")).split())


# ---------------------------------------------------------------- licenças

def free_license(code: str, short_name: str = "") -> str | None:
    """Nome da licença se for livre o bastante (domínio público, CC0 ou CC BY); None caso contrário."""
    c = (code or "").strip().lower()
    n = (short_name or "").strip().lower()
    if any(x in c or x in n for x in ("-sa", "-nc", "-nd", " sa ", " nc ", " nd ", "share alike", "sharealike",
                                       "noncommercial", "noderiv")):
        return None
    if c.startswith("pd") or c == "cc0" or "public domain" in n or n.startswith("pd") or n == "cc0":
        return short_name or ("CC0" if c == "cc0" else "Public domain")
    if re.fullmatch(r"cc-by-\d(\.\d)?", c) or re.fullmatch(r"cc by \d(\.\d)?", n):
        return short_name or c.upper()
    return None


def ia_license(url: str) -> str | None:
    u = (url or "").lower()
    if "publicdomain" in u:
        return "Public domain"
    if re.search(r"creativecommons\.org/licenses/by/\d", u):
        return "CC BY"
    return None


def loc_license(item: dict) -> str | None:
    """LoC: só "No known restrictions" / domínio público explícito nos campos de direitos."""
    texts = []
    for k in ("rights_advisory", "rights", "rights_information"):
        v = item.get(k)
        texts += v if isinstance(v, list) else [v] if v else []
    t = " ".join(str(x) for x in texts).lower()
    if "no known restrictions" in t:
        return "No known restrictions (Library of Congress)"
    if "public domain" in t:
        return "Public domain (Library of Congress)"
    return None


# ---------------------------------------------------------------- Wikimedia Commons

def _commons_preview(url: str, thumb: str | None) -> str:
    if thumb and "/thumb/" in thumb:
        return re.sub(r"/\d+px-", "/640px-", thumb)
    return thumb or url


def search_commons(query: str, per_page: int = 10) -> list[Candidate]:
    params = {"action": "query", "format": "json", "generator": "search", "gsrnamespace": 6,
              "gsrsearch": f"{query} filetype:bitmap", "gsrlimit": min(per_page * 2, 30), "prop": "imageinfo",
              "iiprop": "url|size|mime|extmetadata", "iiurlwidth": 2560,
              "iiextmetadatafilter": "LicenseShortName|License|Artist|ImageDescription|ObjectName|DateTimeOriginal"}
    body = json_or_raise(request("GET", COMMONS, params=params, headers=HEADERS), "Wikimedia Commons")
    pages = sorted(((body.get("query") or {}).get("pages") or {}).values(), key=lambda p: p.get("index", 0))
    out = []
    for p in pages:
        info = (p.get("imageinfo") or [{}])[0]
        meta = {k: (v or {}).get("value", "") for k, v in (info.get("extmetadata") or {}).items()}
        lic = free_license(str(meta.get("License", "")), str(meta.get("LicenseShortName", "")))
        if not lic or not info.get("url") or not str(info.get("mime", "")).startswith("image/"):
            continue
        name = re.sub(r"\.\w+$", "", p.get("title", "").removeprefix("File:"))
        desc = _strip_html(str(meta.get("ImageDescription", "")))[:200]
        date = _strip_html(str(meta.get("DateTimeOriginal", "")))[:40]
        w, h = info.get("width") or 0, info.get("height") or 0
        download_url = info.get("thumburl") or info["url"]
        if info.get("thumburl") and w > 2560:
            h, w = round(h * 2560 / w), 2560
        out.append(Candidate(
            provider="wikimedia", external_id=str(p.get("pageid")), title=" ".join(x for x in [name, desc, date] if x),
            duration=0.0, width=w, height=h, page_url=info.get("descriptionurl") or "",
            thumbnail=_commons_preview(info["url"], info.get("thumburl")),
            renditions=[Rendition(download_url, w, h)], query=query,
            author=_strip_html(str(meta.get("Artist", "")))[:120] or "Wikimedia Commons",
            author_url=info.get("descriptionurl") or "", license=lic, is_image=True,
            preview_frames=[_commons_preview(info["url"], info.get("thumburl"))]))
        if len(out) >= per_page:
            break
    return out


# ---------------------------------------------------------------- Library of Congress

_loc_blocked_until = 0.0
_loc_lock = threading.Lock()


def search_loc(query: str, per_page: int = 10) -> list[Candidate]:
    """A LoC às vezes responde com o desafio do Cloudflare (403): pausa a fonte por 1 h, sem travar a seleção."""
    global _loc_blocked_until
    with _loc_lock:
        if time.time() < _loc_blocked_until:
            return []
    resp = request("GET", LOC, params={"q": query, "fo": "json", "c": per_page}, headers=HEADERS, retries=1)
    if resp.status_code == 403 or "json" not in resp.headers.get("content-type", ""):
        with _loc_lock:
            _loc_blocked_until = time.time() + 3600
        raise ProviderError("Library of Congress recusou a busca (desafio anti-robô); fonte pausada por 1 h",
                            resp.status_code)
    body = json_or_raise(resp, "Library of Congress")
    out = []
    for item in body.get("results") or []:
        lic = loc_license(item)
        images = [u for u in item.get("image_url") or [] if isinstance(u, str)]
        if not lic or not images:
            continue
        big = images[-1].split("#")[0]
        small = (images[1] if len(images) > 1 else images[0]).split("#")[0]
        title = item.get("title") or ""
        if isinstance(title, list):
            title = " ".join(title)
        out.append(Candidate(
            provider="loc", external_id=str(item.get("id") or item.get("url") or big), title=str(title)[:250],
            duration=0.0, width=0, height=0, page_url=item.get("url") or item.get("id") or "", thumbnail=small,
            renditions=[Rendition(big, 0, 0)], query=query,
            author=", ".join(item.get("contributor") or [])[:120] if isinstance(item.get("contributor"), list)
            else str(item.get("contributor") or "Library of Congress"),
            license=lic, is_image=True, preview_frames=[small]))
    return out


# ---------------------------------------------------------------- Internet Archive

def _length(raw) -> float:
    try:
        if isinstance(raw, str) and ":" in raw:
            secs = 0.0
            for part in raw.split(":"):
                secs = secs * 60 + float(part)
            return secs
        return float(raw or 0)
    except ValueError:
        return 0.0


def _ia_item(doc: dict, query: str, max_bytes: int) -> Candidate | None:
    ident = doc["identifier"]
    meta = json_or_raise(request("GET", IA_META.format(id=ident), headers=HEADERS), "Internet Archive")
    lic = ia_license((meta.get("metadata") or {}).get("licenseurl") or doc.get("licenseurl", ""))
    if not lic:
        return None
    files = meta.get("files") or []
    videos = [f for f in files if f.get("format") in VIDEO_FORMATS and f.get("name", "").lower().endswith(".mp4")
              and int(f.get("size") or 0) <= max_bytes]
    if not videos:
        return None
    best = max(videos, key=lambda f: (int(f.get("height") or 0), -int(f.get("size") or 0)))
    thumbs = sorted(f["name"] for f in files if f.get("format") == "Thumbnail")
    duration = _length(best.get("length"))
    if thumbs and len(thumbs) > 15:
        thumbs = [thumbs[round(i * (len(thumbs) - 1) / 14)] for i in range(15)]
    frames = [IA_DOWNLOAD.format(id=ident, name=t) for t in thumbs]
    w, h = int(best.get("width") or 0), int(best.get("height") or 0)
    md = meta.get("metadata") or {}
    creator = md.get("creator") or doc.get("creator") or "Internet Archive"
    title = md.get("title") or doc.get("title") or ident
    return Candidate(
        provider="internet_archive", external_id=ident,
        title=" ".join(str(x) for x in [title if isinstance(title, str) else " ".join(title), md.get("year") or "",
                                        " ".join(md.get("subject")) if isinstance(md.get("subject"), list)
                                        else md.get("subject") or ""])[:300],
        duration=duration, width=w, height=h, page_url=f"https://archive.org/details/{ident}",
        thumbnail=frames[len(frames) // 2] if frames else f"https://archive.org/services/img/{ident}",
        renditions=[Rendition(IA_DOWNLOAD.format(id=ident, name=best["name"]), w, h)], query=query,
        author=creator if isinstance(creator, str) else ", ".join(creator), license=lic,
        preview_frames=frames or [f"https://archive.org/services/img/{ident}"])


def search_ia_films(query: str, per_page: int = 6) -> list[Candidate]:
    # sem o filtro de licença na busca, o topo vem cheio de uploads sem licença (descartados de qualquer forma)
    lic = r"(licenseurl:*publicdomain* OR licenseurl:*licenses\/by\/*)"
    params = [("q", f"({query}) AND mediatype:(movies) AND {lic}"), ("fl[]", "identifier"), ("fl[]", "title"),
              ("fl[]", "licenseurl"), ("fl[]", "creator"), ("rows", str(per_page * 3)), ("output", "json")]
    body = json_or_raise(request("GET", IA_SEARCH, params=params, headers=HEADERS), "Internet Archive")
    docs = [d for d in (body.get("response") or {}).get("docs") or [] if ia_license(d.get("licenseurl", ""))]
    max_bytes = int(float(_cfg().get("max_video_mb", 300)) * 1024 * 1024)
    out: list[Candidate] = []
    with ThreadPoolExecutor(max_workers=4) as pool:
        for c in pool.map(lambda d: _safe(_ia_item, d, query, max_bytes), docs[:per_page]):
            if c:
                out.append(c)
    return out


def _safe(fn, *args):
    try:
        return fn(*args)
    except ProviderError as e:
        log.info("acervo: item ignorado (%s)", e)
        return None


# ---------------------------------------------------------------- adapter

class HistoricalArchives:
    """Interface de banco (search / search_photos) sobre as três fontes, cada uma ligável em settings.archives."""

    id = "archives"

    def sources(self) -> dict:
        return {"wikimedia": True, "loc": True, "internet_archive": True, **(_cfg().get("sources") or {})}

    def search_photos(self, query: str, per_page: int = 10, lang: str = "en", **_) -> list[Candidate]:
        src = self.sources()
        jobs = [fn for name, fn in (("wikimedia", search_commons), ("loc", search_loc)) if src.get(name)]
        out: list[Candidate] = []
        errors = []
        for fn in jobs:
            try:
                out += fn(query, per_page)
            except ProviderError as e:
                errors.append(str(e))
        if errors and not out:
            raise ProviderError("; ".join(errors))
        return out

    def search(self, query: str, per_page: int = 6, lang: str = "en", **_) -> list[Candidate]:
        if not self.sources().get("internet_archive"):
            return []
        return search_ia_films(query, min(per_page, 8))

    def test(self) -> dict:
        res = search_commons("victorian painting", per_page=2)
        return {"ok": True, "detail": f"Wikimedia Commons: {len(res)} resultado(s) com licença livre"}


ARCHIVES = HistoricalArchives()
