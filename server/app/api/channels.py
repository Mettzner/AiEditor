from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel
from sqlmodel import Session, select

from ..config import DATA_DIR
from ..db import get_session
from ..models import Channel, Preset
from ..purge import PurgeError, purge_channel

router = APIRouter(prefix="/channels", tags=["channels"])


class ChannelIn(BaseModel):
    name: str
    preset: Preset = Preset()


def _out(c: Channel) -> dict[str, Any]:
    return {"id": c.id, "name": c.name, "preset": Preset.model_validate(c.preset).model_dump(),
            "created_at": c.created_at}


@router.get("")
def list_channels(s: Session = Depends(get_session)):
    return [_out(c) for c in s.exec(select(Channel).order_by(Channel.name))]


@router.get("/{channel_id}")
def get_channel(channel_id: int, s: Session = Depends(get_session)):
    c = s.get(Channel, channel_id)
    if not c:
        raise HTTPException(404, "Canal não encontrado")
    return _out(c)


@router.post("")
def create_channel(body: ChannelIn, s: Session = Depends(get_session)):
    c = Channel(name=body.name, preset=body.preset.model_dump())
    s.add(c)
    s.commit()
    s.refresh(c)
    return _out(c)


@router.put("/{channel_id}")
def update_channel(channel_id: int, body: ChannelIn, s: Session = Depends(get_session)):
    c = s.get(Channel, channel_id)
    if not c:
        raise HTTPException(404, "Canal não encontrado")
    old = Preset.model_validate(c.preset)
    new = body.preset
    if new.reference_image != old.reference_image:
        new.reference_key = None  # imagem trocada: reenvia à Darkvi na próxima geração
    c.name = body.name
    c.preset = new.model_dump()
    s.add(c)
    s.commit()
    s.refresh(c)
    return _out(c)


@router.delete("/{channel_id}")
def delete_channel(channel_id: int, s: Session = Depends(get_session)):
    c = s.get(Channel, channel_id)
    if not c:
        raise HTTPException(404, "Canal não encontrado")
    try:
        removed = purge_channel(s, c)  # com todas as produções do canal, arquivos incluídos
    except PurgeError as e:
        raise HTTPException(409, str(e)) from e
    s.commit()
    return {"ok": True, "productions_removed": removed}


@router.post("/{channel_id}/reference")
async def upload_reference(channel_id: int, file: UploadFile = File(...), s: Session = Depends(get_session)):
    c = s.get(Channel, channel_id)
    if not c:
        raise HTTPException(404, "Canal não encontrado")
    ext = (file.filename or "ref.png").rsplit(".", 1)[-1].lower()
    dest = DATA_DIR / "references" / f"channel_{channel_id}.{ext}"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(await file.read())
    c.preset = {**c.preset, "reference_image": str(dest), "reference_key": None}
    s.add(c)
    s.commit()
    return _out(c)
