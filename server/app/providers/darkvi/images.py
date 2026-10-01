"""Geração de imagens via Darkvi (§8.1)."""
from __future__ import annotations

import time
from datetime import datetime, timezone
from pathlib import Path

from ...config import update_settings
from ..http import download
from .client import DarkviError, _pick, call
from .ratelimit import IMAGE_BUCKET
from .tts import DONE, FAILED

PROMPT_LIMIT = 1000


class QuotaExhausted(DarkviError):
    pass


class ReferenceNotAllowed(DarkviError):
    pass


def build_prompt(visual_intent: str, visual_style: str) -> str:
    parts = [visual_intent.strip()]
    if visual_style:
        parts.append(f"Visual style: {visual_style.strip()}")
    parts.append("Cinematic photograph, 16:9, no text, no watermark, no logos.")
    prompt = ". ".join(p.rstrip(".") for p in parts if p)
    if len(prompt) > PROMPT_LIMIT:  # prioriza o visual_intent e corta o resto
        prompt = prompt[: PROMPT_LIMIT - 1].rsplit(" ", 1)[0]
    return prompt


def _save_quota(body: dict) -> None:
    remaining = _pick(body, "remaining")
    limit = _pick(body, "limit")
    if remaining is not None:
        update_settings({"darkvi": {"remaining": remaining, "limit": limit,
                                    "updated_at": datetime.now(timezone.utc).isoformat()}})


def upload_reference(path: Path) -> str:
    with path.open("rb") as f:
        try:
            body = call("POST", "/v1/images/reference", files={"file": (path.name, f)})
        except DarkviError as e:
            if e.status == 403:
                raise ReferenceNotAllowed("Plano Darkvi não permite imagem de referência", 403) from e
            raise
    key = _pick(body, "key", "referencePath", "path")
    if not key:
        raise DarkviError(f"Upload de referência sem key: {body!r}"[:300])
    return key


def generate(prompt: str, dest: Path, reference_key: str | None = None, poll_seconds: float = 4,
             timeout_seconds: float = 600) -> Path:
    IMAGE_BUCKET.acquire()
    payload = {"prompt": prompt[:PROMPT_LIMIT], "aspect": "16:9"}
    if reference_key:
        payload["referencePath"] = reference_key
    try:
        created = call("POST", "/v1/images", json=payload)
    except DarkviError as e:
        if e.status == 429:
            raise QuotaExhausted("Limite de imagens da Darkvi atingido", 429, e.body) from e
        if e.status == 403 and reference_key:
            raise ReferenceNotAllowed("Plano Darkvi não permite imagem de referência", 403) from e
        raise
    _save_quota(created)
    image_id = _pick(created, "id", "imageId")
    if not image_id:
        raise DarkviError(f"POST /v1/images sem id: {created!r}"[:300])
    deadline = time.monotonic() + timeout_seconds
    while True:
        body = call("GET", f"/v1/images/{image_id}")
        status = str(_pick(body, "status", "state") or "").upper()
        if status in DONE:
            break
        if status in FAILED:
            raise DarkviError(f"Imagem {image_id} falhou ({status})", body=body)
        if time.monotonic() > deadline:
            raise DarkviError(f"Imagem {image_id} excedeu o tempo limite")
        time.sleep(poll_seconds)
    url = _pick(body, "url", "imageUrl", "signedUrl")
    if not url:
        raise DarkviError(f"Imagem {image_id} sem URL: {body!r}"[:300])
    return download(url, dest)  # URL assinada vale 7 dias: baixa na hora
