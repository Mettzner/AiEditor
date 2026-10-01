"""Templates de overlay do Clássico: destaque (lower-third) e título de capítulo.

Cada função devolve um PNG 1920×1080 com transparência, sobreposto pelo renderer.
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from ...pipeline.render.fonts import find_font

W, H = 1920, 1080


def _hex(c: str, alpha: int = 255) -> tuple[int, int, int, int]:
    c = c.lstrip("#")
    return int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16), alpha


def highlight(text: str, style: dict, dest: Path) -> Path:
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    font = ImageFont.truetype(find_font(style["font"]), 52)
    label = text.upper()
    l, t, r, b = d.textbbox((0, 0), label, font=font)
    pad_x, pad_y = 34, 20
    x, y = 110, H - 300
    box = (x, y, x + (r - l) + pad_x * 2, y + (b - t) + pad_y * 2)
    d.rectangle((x - 12, y, x - 4, box[3]), fill=_hex(style["color_primary"]))
    d.rectangle(box, fill=_hex(style["color_accent"], 235))
    d.text((x + pad_x - l, y + pad_y - t), label, font=font, fill=_hex(style["color_primary"]))
    img.save(dest)
    return dest


def chapter_title(text: str, style: dict, dest: Path) -> Path:
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rectangle((0, H // 2 - 110, W, H // 2 + 110), fill=(0, 0, 0, 120))
    font = ImageFont.truetype(find_font(style["font"]), 84)
    label = text.upper()
    l, t, r, b = d.textbbox((0, 0), label, font=font)
    d.text(((W - (r - l)) / 2 - l, (H - (b - t)) / 2 - t), label, font=font, fill=_hex(style["color_primary"]))
    lw = min(W - 200, (r - l) + 80)
    d.rectangle(((W - lw) // 2, H // 2 + 70, (W + lw) // 2, H // 2 + 76), fill=_hex(style["color_accent"]))
    img.save(dest)
    return dest


TEMPLATES = {"highlight": highlight, "chapter": chapter_title}
