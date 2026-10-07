"""Mídia autorizada (Fase D1): o usuário associa um arquivo que tem direito de usar a uma referência do YouTube.

Só com essa associação um trecho do vídeo pesquisado entra no render no modo "referência". O upload é em streaming,
com limite de tamanho, conferido pelo ffprobe (precisa ter vídeo) e identificado pelo hash do conteúdo. O app não
baixa nada do YouTube aqui: guarda a URL como origem e a declaração de autorização informada pelo usuário.
"""
from __future__ import annotations

import re
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlmodel import Session, select

from ..config import DATA_DIR, job_dir, load_settings
from ..db import get_session
from ..fsutil import unique_temp
from ..models import AuthorizedMedia, Production
from ..pipeline import media_index

router = APIRouter(tags=["media"])
VIDEO_EXT = {"mp4", "mov", "mkv", "webm", "m4v"}
CHUNK = 1 << 20
YT_ID = re.compile(r"(?:v=|youtu\.be/|shorts/|embed/)([A-Za-z0-9_-]{11})")


def authorized_dir() -> Path:
    d = DATA_DIR / "authorized"
    d.mkdir(parents=True, exist_ok=True)
    return d


def youtube_id(url: str | None) -> str | None:
    if not url:
        return None
    m = YT_ID.search(url)
    if m:
        return m.group(1)
    return url if re.fullmatch(r"[A-Za-z0-9_-]{11}", url or "") else None


def _card(m: AuthorizedMedia) -> dict:
    return {"id": m.id, "youtube_id": m.youtube_id, "source_url": m.source_url, "original_name": m.original_name,
            "author": m.author, "license": m.license, "rights_note": m.rights_note, "obtained_how": m.obtained_how,
            "duration": m.duration, "width": m.width, "height": m.height, "sha256": m.sha256,
            "verified_at": m.verified_at, "available": Path(m.local_path).exists()}


@router.get("/authorized-media")
def list_media(s: Session = Depends(get_session)):
    return [_card(m) for m in s.exec(select(AuthorizedMedia).order_by(AuthorizedMedia.created_at.desc()))]


@router.post("/authorized-media")
async def add_media(file: UploadFile = File(...), rights_note: str = Form(...), youtube_url: str = Form(""),
                    author: str = Form(""), license: str = Form(""), s: Session = Depends(get_session)):
    if not rights_note.strip():
        raise HTTPException(422, "Descreva a autorização (conteúdo próprio, permissão do autor, contrato...)")
    ext = (file.filename or "").rsplit(".", 1)[-1].lower()
    if ext not in VIDEO_EXT:
        raise HTTPException(400, f"Envie um vídeo ({', '.join(sorted(VIDEO_EXT))})")
    yid = youtube_id(youtube_url)
    if youtube_url and not yid:
        raise HTTPException(422, "Link do YouTube não reconhecido")
    max_bytes = int((load_settings().get("upload") or {}).get("max_video_mb", 4096)) * 1024 * 1024
    tmp = unique_temp(authorized_dir() / f"upload.{ext}", "upload")
    got = 0
    try:
        with tmp.open("wb") as f:
            while chunk := await file.read(CHUNK):
                got += len(chunk)
                if got > max_bytes:
                    raise HTTPException(413, f"Vídeo passa do limite de {max_bytes // (1024 * 1024)} MB")
                f.write(chunk)
        try:
            meta = media_index.probe_video(tmp)
        except Exception as e:  # noqa: BLE001
            raise HTTPException(400, "O arquivo não é um vídeo legível") from e
        if not meta["has_video"] or meta["duration"] <= 0:
            raise HTTPException(400, "O arquivo não tem trilha de vídeo")
        digest = media_index.file_hash(tmp)
        dest = authorized_dir() / f"{digest[:24]}.{ext}"
        if dest.exists():
            tmp.unlink(missing_ok=True)
        else:
            tmp.replace(dest)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    m = AuthorizedMedia(youtube_id=yid, source_url=youtube_url or None, local_path=str(dest), sha256=digest,
                        original_name=(file.filename or "")[:200], author=author.strip()[:200],
                        license=license.strip()[:200], rights_note=rights_note.strip()[:500],
                        duration=meta["duration"], width=meta["width"], height=meta["height"])
    s.add(m)
    s.commit()
    s.refresh(m)
    return _card(m)


@router.delete("/authorized-media/{media_id}")
def delete_media(media_id: int, s: Session = Depends(get_session)):
    m = s.get(AuthorizedMedia, media_id)
    if not m:
        raise HTTPException(404, "Mídia não encontrada")
    path = Path(m.local_path)
    s.delete(m)
    s.commit()
    still_used = s.exec(select(AuthorizedMedia).where(AuthorizedMedia.local_path == str(path))).first()
    if not still_used:
        path.unlink(missing_ok=True)
    return {"ok": True}


@router.get("/productions/{production_id}/references")
def production_references(production_id: int, s: Session = Depends(get_session)):
    """Referências do YouTube pesquisadas por cena, com o estado (só referência × arquivo autorizado disponível)."""
    import json

    if not s.get(Production, production_id):
        raise HTTPException(404, "Produção não encontrada")
    path = job_dir(production_id) / "references.json"
    data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"scenes": {}}
    linked = {m.youtube_id for m in s.exec(select(AuthorizedMedia)) if m.youtube_id}
    for items in data.get("scenes", {}).values():
        for r in items:
            if r.get("youtube_id") in linked:
                r["status"], r["pending"] = "authorized_available", None
    return data
