"""Folha de miniaturas: N linhas numeradas × frames em ordem temporal, numa única imagem JPEG.

Os frames vêm de graça das APIs (Pexels video_pictures, Pixabay thumbnail, YouTube hq1–3); nada de vídeo é
baixado para avaliar (AJUSTE_ESTILO_CONTEXTUAL.md §10.4).
"""
from __future__ import annotations

import hashlib
import io
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from ...config import CACHE_DIR
from ...providers.http import download
from ...providers.stock.base import Candidate

FRAME_W, FRAME_H = 320, 180
LABEL_W = 56
GAP = 6
_frames_dir = CACHE_DIR / "frames"


def _cached(url: str) -> Path | None:
    _frames_dir.mkdir(parents=True, exist_ok=True)
    path = _frames_dir / (hashlib.sha1(url.encode()).hexdigest() + ".jpg")
    if path.exists() and path.stat().st_size > 0:
        return path
    try:
        return download(url, path)
    except Exception:  # noqa: BLE001 — frame faltando vira célula vazia
        return None


def frame_bytes(frame: Path | str | None) -> bytes:
    """Bytes de um frame (caminho local ou URL já baixada para o cache); nunca baixa. Vazio se indisponível."""
    if isinstance(frame, str):
        path = _frames_dir / (hashlib.sha1(frame.encode()).hexdigest() + ".jpg")
    else:
        path = frame
    try:
        return Path(path).read_bytes() if path else b""
    except OSError:
        return b""


def pick_frames(c: Candidate, n: int) -> tuple[list[str], list[float]]:
    """Escolhe até n frames espaçados (URLs) e suas posições relativas no clipe."""
    urls, pos = c.preview_frames, c.positions()
    if len(urls) <= n:
        return list(urls), list(pos)
    idx = [round(i * (len(urls) - 1) / (n - 1)) for i in range(n)] if n > 1 else [len(urls) // 2]
    return [urls[i] for i in idx], [pos[i] for i in idx]


def build_sheet(rows: list[list[Path | str | None]]) -> bytes:
    """rows: lista de linhas; cada linha, lista de caminhos locais ou URLs dos frames."""
    flat = [f for r in rows for f in r if isinstance(f, str)]
    with ThreadPoolExecutor(max_workers=8) as pool:
        fetched = dict(zip(flat, pool.map(_cached, flat)))
    cols = max((len(r) for r in rows), default=1)
    w = LABEL_W + cols * (FRAME_W + GAP)
    h = len(rows) * (FRAME_H + GAP)
    sheet = Image.new("RGB", (w, h), (20, 20, 20))
    draw = ImageDraw.Draw(sheet)
    try:
        font = ImageFont.truetype("arialbd.ttf", 30)
    except OSError:
        font = ImageFont.load_default()
    for r, frames in enumerate(rows):
        y = r * (FRAME_H + GAP)
        draw.text((10, y + FRAME_H // 2 - 18), str(r + 1), fill=(255, 220, 0), font=font)
        for k, f in enumerate(frames):
            path = fetched.get(f) if isinstance(f, str) else f
            x = LABEL_W + k * (FRAME_W + GAP)
            if path and Path(path).exists():
                try:
                    with Image.open(path) as im:
                        im = im.convert("RGB")
                        im.thumbnail((FRAME_W, FRAME_H))
                        sheet.paste(im, (x + (FRAME_W - im.width) // 2, y + (FRAME_H - im.height) // 2))
                except OSError:
                    pass
            draw.text((x + 6, y + 4), str(k + 1), fill=(255, 255, 255), font=ImageFont.load_default())
    buf = io.BytesIO()
    sheet.save(buf, "JPEG", quality=82)
    return buf.getvalue()
