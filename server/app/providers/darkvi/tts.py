"""TTS assíncrono da Darkvi (§6.1)."""
from __future__ import annotations

import time
from pathlib import Path
from typing import Callable

from .client import DarkviError, _pick, call

MAX_CHARS = 80_000
DONE = {"DONE", "COMPLETED", "SUCCESS"}
FAILED = {"FAILED", "ERROR", "CANCELLED", "CANCELED"}


def list_voices() -> list[dict]:
    body = call("GET", "/tts/voices")
    voices = body if isinstance(body, list) else (_pick(body, "voices", "items", "data") or [])
    out = []
    for v in voices:
        if isinstance(v, str):
            out.append({"id": v, "name": v})
            continue
        out.append({
            "id": v.get("id") or v.get("voice") or v.get("name"),
            "name": v.get("name") or v.get("id"),
            "language": v.get("language") or v.get("lang"),
            "accent": v.get("accent"),
            "preview_url": v.get("preview_url") or v.get("previewUrl") or v.get("sample"),
        })
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
               timeout_seconds: float = 1800, on_poll: Callable[[str], None] | None = None) -> None:
    created = call("POST", "/tts", json={"text": text, "voice": voice, "title": title[:120]})
    job_id = _pick(created, "id", "ttsId", "jobId")
    if not job_id:
        raise DarkviError(f"Resposta do POST /tts sem id: {created!r}"[:300])
    deadline = time.monotonic() + timeout_seconds
    while True:
        status_body = call("GET", f"/tts/{job_id}")
        status = str(_pick(status_body, "status", "state") or "").upper()
        if on_poll:
            on_poll(status)
        if status in DONE:
            break
        if status in FAILED:
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
