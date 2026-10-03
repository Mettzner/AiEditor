"""TTS assíncrono da Darkvi (§6.1)."""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Callable

from ..http import json_or_raise, request
from .client import BASE_URL, DarkviError, _pick, call

MAX_CHARS = 80_000
DONE = {"DONE", "COMPLETED", "SUCCESS"}
FAILED = {"FAILED", "ERROR", "CANCELLED", "CANCELED"}


LANG_NAMES = {"english": "en", "portuguese": "pt", "spanish": "es", "french": "fr", "german": "de",
              "italian": "it", "japanese": "ja", "korean": "ko"}
_voices_cache: tuple[float, list[dict]] | None = None
VOICES_TTL = 3600


def _lang(code: str | None) -> str | None:
    if not code:
        return None
    code = code.strip().lower()
    return LANG_NAMES.get(code, code)


def list_voices() -> list[dict]:
    """GET /api/tts/voices (rota pública) → narradores normalizados, em cache por 1 h.

    Resposta da Darkvi: [{idApi, name, novidade, language, accent, OtherLanguage, age, Urlpreview}].
    O `idApi` é o valor enviado em `voice` no POST /tts; na tela só aparece o nome.
    """
    global _voices_cache
    if _voices_cache and time.monotonic() - _voices_cache[0] < VOICES_TTL:
        return _voices_cache[1]
    resp = request("GET", f"{BASE_URL}/tts/voices")
    body = json_or_raise(resp, "Darkvi /tts/voices")
    raw = body if isinstance(body, list) else (_pick(body, "voices", "items", "data") or [])
    out = []
    for v in raw:
        vid = v.get("idApi") or v.get("id")
        if not vid:
            continue
        language = _lang(v.get("language"))
        languages = sorted({_lang(x) for x in v.get("OtherLanguage") or [] if x} | ({language} if language else set()))
        out.append({
            "id": vid,
            "name": v.get("name") or vid,
            "language": language,
            "languages": languages,
            "accent": v.get("accent"),
            "age": v.get("age"),
            "is_new": bool(v.get("novidade")),
            "preview_url": v.get("Urlpreview") or v.get("preview_url"),
        })
    out.sort(key=lambda x: (not x["is_new"], x["name"].lower()))
    _voices_cache = (time.monotonic(), out)
    return out


def split_text(text: str, limit: int = MAX_CHARS) -> list[str]:
    if len(text) <= limit:
        return [text]
    chunks, current = [], ""
    for para in text.split("\n"):
        if len(current) + len(para) + 1 > limit and current:
            chunks.append(current)
            current = ""
        current = f"{current}\n{para}" if current else para
    if current:
        chunks.append(current)
    return chunks


def synthesize(text: str, voice: str, title: str, out_mp3: Path, out_srt: Path, *, poll_seconds: float = 4,
               timeout_seconds: float = 1800, on_poll: Callable[[str], None] | None = None,
               state_file: Path | None = None) -> None:
    """Cria (ou retoma) um TTS. O id fica em state_file: um retry nunca gera (e cobra) a narração de novo."""
    job_id = None
    if state_file and state_file.exists():
        job_id = json.loads(state_file.read_text(encoding="utf-8")).get("id")
    if not job_id:
        created = call("POST", "/tts", json={"text": text, "voice": voice, "title": title[:120]})
        job_id = _pick(created, "id", "ttsId", "jobId")
        if not job_id:
            raise DarkviError(f"Resposta do POST /tts sem id: {created!r}"[:300])
        if state_file:
            state_file.write_text(json.dumps({"id": job_id, "voice": voice}), encoding="utf-8")
    deadline = time.monotonic() + timeout_seconds
    while True:
        status_body = call("GET", f"/tts/{job_id}")
        status = str(_pick(status_body, "status", "state") or "").upper()
        if on_poll:
            on_poll(status)
        if status in DONE:
            break
        if status in FAILED:
            if state_file:
                state_file.unlink(missing_ok=True)  # falhou do lado da Darkvi: o retry cria outro
            raise DarkviError(f"TTS {job_id} terminou com status {status}", body=status_body)
        if time.monotonic() > deadline:
            raise DarkviError(f"TTS {job_id} excedeu o tempo limite ({timeout_seconds:.0f}s)")
        time.sleep(poll_seconds)
    audio = call("GET", f"/tts/audios/{job_id}", raw=True)
    out_mp3.parent.mkdir(parents=True, exist_ok=True)
    out_mp3.write_bytes(audio.content)
    try:
        srt = call("GET", f"/tts/srt/{job_id}", raw=True)
        out_srt.write_bytes(srt.content)
    except DarkviError:
        pass  # o SRT é só validação/fallback
