"""Saúde, informações do app, primeira execução e ganchos do desktop (EMPACOTAMENTO_INSTALADOR_WINDOWS.md)."""
from __future__ import annotations

import logging
import os
import threading
import time

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, func, select

from .. import desktop, paths, whisper_models
from ..config import get_secret, load_settings, update_settings
from ..db import get_session
from ..models import Channel, Production
from ..pipeline.render import ffmpeg
from ..providers.storage import gdrive

log = logging.getLogger("aieditor.system")
router = APIRouter(tags=["system"])

LATEST_URL = os.environ.get("AIEDITOR_LATEST_URL", "https://mettzner.github.io/AiEditor/latest.json")
KEY_PROVIDERS = ["anthropic", "gemini", "darkvi", "pexels", "pixabay", "youtube"]
_latest: dict = {"checked": 0.0, "data": None}
_latest_lock = threading.Lock()


@router.get("/health")
def health():
    return {"ok": True, "version": paths.app_version(), "ffmpeg": ffmpeg.available()}


def _version_tuple(v: str) -> tuple:
    return tuple(int(x) if x.isdigit() else 0 for x in v.strip().lstrip("v").split("."))


def latest_release() -> dict | None:
    """latest.json público ({version, url, notes}); consultado no máximo 1x a cada 6 h por processo."""
    with _latest_lock:
        if time.time() - _latest["checked"] < 6 * 3600:
            return _latest["data"]
        _latest["checked"] = time.time()
    try:
        r = httpx.get(LATEST_URL, timeout=5, follow_redirects=True)
        data = r.json() if r.status_code == 200 else None
    except (httpx.HTTPError, ValueError):
        data = None
    with _latest_lock:
        _latest["data"] = data if isinstance(data, dict) and data.get("version") else None
        return _latest["data"]


@router.get("/app/info")
def app_info():
    version = paths.app_version()
    latest = latest_release()
    update = None
    if latest and _version_tuple(latest["version"]) > _version_tuple(version):
        update = {"version": latest["version"], "url": latest.get("url"), "notes": latest.get("notes", "")}
    return {"version": version, "desktop": desktop.is_desktop(), "frozen": paths.FROZEN,
            "data_dir": str(paths.data_dir()), "update": update,
            "ffmpeg": {"available": ffmpeg.available(), "version": ffmpeg.version(), "amf": ffmpeg.amf_available()}}


@router.post("/app/show")
def app_show():
    """Chamado por uma 2ª execução do atalho: traz a janela existente para frente."""
    return {"ok": desktop.bring_to_front()}


class UrlIn(BaseModel):
    url: str


@router.post("/app/open-url")
def app_open_url(body: UrlIn):
    return {"ok": desktop.open_external(body.url)}


# ---------------------------------------------------------------- primeira execução (§4)

@router.get("/setup/status")
def setup_status(s: Session = Depends(get_session)):
    settings = load_settings()
    keys = {p: bool(get_secret(p)) for p in KEY_PROVIDERS}
    channels = s.exec(select(func.count()).select_from(Channel)).one()
    model = settings["transcription"]["model"]
    done = bool((settings.get("setup") or {}).get("done")) or channels > 0
    return {
        "needs_setup": not done,
        "keys": keys,
        # para começar: narração (Darkvi ou áudio enviado) e ao menos um banco de vídeo
        "minimum_ok": (keys["pexels"] or keys["pixabay"]),
        "google": {"connected": gdrive.is_connected(), "client": gdrive.client_available()},
        "model": {"size": model, "ready": whisper_models.model_ready(model),
                  "download": whisper_models.status(), "options": whisper_models.LABELS,
                  "recommended": whisper_models.RECOMMENDED},
        "channels": channels,
        "desktop": desktop.is_desktop(),
    }


class ModelIn(BaseModel):
    size: str = whisper_models.RECOMMENDED


@router.post("/setup/model")
def setup_model(body: ModelIn):
    try:
        return whisper_models.start_download(body.size)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@router.get("/setup/model")
def setup_model_status():
    return whisper_models.status()


@router.post("/setup/done")
def setup_done():
    update_settings({"setup": {"done": True}})
    return {"ok": True}


@router.get("/app/busy")
def app_busy(s: Session = Depends(get_session)):
    """Há produção em andamento? O launcher pergunta antes de fechar a janela."""
    n = s.exec(select(func.count()).select_from(Production)
               .where(Production.status.in_(["queued", "running", "cancel_requested"]))).one()  # type: ignore[attr-defined]
    return {"busy": n > 0, "count": n}

