from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel
from sqlmodel import Session, select

from .. import desktop
from ..config import SECRET_PROVIDERS, get_secret, load_settings, save_settings, set_secret
from ..db import get_session
from ..directions import image_path, list_directions
from ..models import ProviderPrice
from ..pipeline.render import ffmpeg
from ..providers.darkvi import client as darkvi_client
from ..providers.darkvi import tts as darkvi_tts
from ..providers.llm import gemini
from ..providers.llm.anthropic import AnthropicLLM
from ..providers.youtube import client as youtube
from ..providers.youtube import quota as yt_quota
from ..providers.music import library
from ..providers.sfx import freesound
from ..providers.storage import gdrive
from ..providers.stock import REGISTRY

router = APIRouter(tags=["settings"])


class SettingsIn(BaseModel):
    settings: dict[str, Any] | None = None
    # valor = nova chave; "" apaga; ausente = mantém
    secrets: dict[str, str] | None = None


def _secrets_status() -> dict[str, bool]:
    return {p: bool(get_secret(p)) for p in SECRET_PROVIDERS}


@router.get("/settings")
def get_settings():
    return {"settings": load_settings(), "secrets": _secrets_status(), "google_connected": gdrive.is_connected(),
            "google_client_embedded": gdrive.client_available() and not get_secret("google_oauth_client")}


@router.put("/settings")
def put_settings(body: SettingsIn):
    if body.settings is not None:
        save_settings(body.settings)
    for provider, value in (body.secrets or {}).items():
        if provider not in SECRET_PROVIDERS:
            raise HTTPException(400, f"Provedor desconhecido: {provider}")
        if provider == "google_oauth_client" and value:
            try:
                json.loads(value)
            except ValueError as e:
                raise HTTPException(400, "O client OAuth do Google deve ser o JSON baixado do Cloud Console") from e
        set_secret(provider, value or None)
    return get_settings()


@router.post("/settings/test/{provider}")
def test_provider(provider: str):
    try:
        if provider == "darkvi":
            return darkvi_client.validate()
        if provider in REGISTRY:
            return REGISTRY[provider].test()
        if provider == "anthropic":
            return AnthropicLLM().test()
        if provider == "google":
            return gdrive.test()
        if provider == "youtube":
            return youtube.test()
        if provider == "gemini":
            return gemini.test()
        if provider == "freesound":
            return freesound.test()
        if provider == "ffmpeg":
            enc = ffmpeg.encoders()
            # h264_amf: listado E funcionando neste PC (codifica quadros de teste)
            return {"ok": True, "detail": {"libx264": "libx264" in enc,
                                           "h264_amf": "h264_amf" in enc and ffmpeg.amf_available(),
                                           "path": ffmpeg.ffmpeg_bin()}}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "detail": str(e)}
    raise HTTPException(404, f"Teste não implementado para '{provider}'")


@router.get("/prices")
def get_prices(s: Session = Depends(get_session)):
    return list(s.exec(select(ProviderPrice).order_by(ProviderPrice.provider)))


class PriceIn(BaseModel):
    id: int
    price: float


@router.put("/prices")
def put_prices(body: list[PriceIn], s: Session = Depends(get_session)):
    for item in body:
        row = s.get(ProviderPrice, item.id)
        if row:
            row.price = item.price
            s.add(row)
    s.commit()
    return list(s.exec(select(ProviderPrice).order_by(ProviderPrice.provider)))


@router.get("/directions")
def directions():
    return list_directions()


@router.get("/directions/{direction_id}/image")
def direction_image(direction_id: str):
    path = image_path(direction_id)
    if not path or not path.exists():
        raise HTTPException(404)
    return FileResponse(path)


@router.get("/tts/voices")
def tts_voices():
    try:
        return darkvi_tts.list_voices()
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"Não foi possível listar as vozes da Darkvi: {e}") from e


@router.get("/music/tracks")
def music_tracks():
    return library.load()


class TrackIn(BaseModel):
    file: str
    mood: list[str] = []
    license: str = ""
    source: str = "manual"


@router.put("/music/tracks")
def update_tracks(body: list[TrackIn]):
    tracks = {t["file"]: t for t in library.load()}
    for t in body:
        if t.file in tracks:
            tracks[t.file].update(mood=t.mood, license=t.license, source=t.source)
    library.save(list(tracks.values()))
    return list(tracks.values())


@router.get("/auth/google/start")
def google_start(request: Request):
    """No app de desktop, o login abre no navegador padrão (o Google recusa OAuth dentro de WebView)."""
    try:
        url = gdrive.start_auth(str(request.base_url))
    except gdrive.DriveNotConfigured as e:
        raise HTTPException(400, str(e)) from e
    opened = desktop.is_desktop() and desktop.open_external(url)
    return {"url": url, "opened": opened}


@router.get("/auth/google/callback", response_class=HTMLResponse)
def google_callback(state: str, code: str):
    try:
        gdrive.finish_auth(state, code)
        msg = "Conta Google conectada. Pode fechar esta aba."
    except Exception as e:  # noqa: BLE001
        msg = f"Falha ao conectar: {e}"
    return f"<html><body style='font-family:sans-serif;padding:40px'><h2>{msg}</h2></body></html>"


@router.get("/youtube/quota")
def youtube_quota():
    return yt_quota.status()


