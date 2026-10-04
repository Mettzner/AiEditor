"""Motor de overlays animados: cada template de direção é uma função `t → Image RGBA 1920×1080`, desenhada
quadro a quadro com PIL e codificada sem perdas, com transparência (FFV1), para o renderer sobrepor à cena.

Aqui ficam as curvas de easing e os utilitários de desenho que os templates compartilham.
"""
from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Callable

from PIL import Image, ImageDraw, ImageFont

from . import ffmpeg

W, H = 1920, 1080
FPS = 30

FrameFn = Callable[[float], Image.Image]


# ---------- easing --------------------------------------------------------------------------------------------

def clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return lo if x < lo else hi if x > hi else x


def progress(t: float, start: float, dur: float) -> float:
    """Fração 0–1 de uma animação que começa em `start` e dura `dur`."""
    return 1.0 if dur <= 0 else clamp((t - start) / dur)


def ease_out_cubic(x: float) -> float:
    return 1 - (1 - x) ** 3


def ease_in_cubic(x: float) -> float:
    return x ** 3


def ease_in_out(x: float) -> float:
    return 4 * x ** 3 if x < 0.5 else 1 - (-2 * x + 2) ** 3 / 2


def ease_out_back(x: float, s: float = 1.4) -> float:
    return 1 + (s + 1) * (x - 1) ** 3 + s * (x - 1) ** 2


def envelope(t: float, dur: float, fade_in: float, fade_out: float) -> float:
    """Opacidade 0–1 com entrada e saída suaves."""
    a = ease_out_cubic(progress(t, 0, fade_in)) if fade_in > 0 else 1.0
    b = 1 - ease_in_cubic(progress(t, dur - fade_out, fade_out)) if fade_out > 0 else 1.0
    return min(a, b)


# ---------- desenho -------------------------------------------------------------------------------------------

def hex_rgba(c: str, alpha: int = 255) -> tuple[int, int, int, int]:
    c = c.lstrip("#")
    return int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16), alpha


def blank() -> Image.Image:
    return Image.new("RGBA", (W, H), (0, 0, 0, 0))


def fade(img: Image.Image, opacity: float) -> Image.Image:
    """Multiplica o canal alfa (opacidade global da camada)."""
    if opacity >= 0.999:
        return img
    if opacity <= 0.001:
        return blank() if img.size == (W, H) else Image.new("RGBA", img.size, (0, 0, 0, 0))
    a = img.getchannel("A").point(lambda v: int(v * opacity))
    img.putalpha(a)
    return img


def tracked_width(font: ImageFont.FreeTypeFont, text: str, tracking: float) -> float:
    """Largura do texto com espaçamento extra entre letras (`tracking` em px)."""
    if not text:
        return 0.0
    return sum(font.getlength(ch) for ch in text) + tracking * (len(text) - 1)


def draw_tracked(d: ImageDraw.ImageDraw, xy: tuple[float, float], text: str, font: ImageFont.FreeTypeFont,
                 tracking: float, fill: tuple[int, int, int, int]) -> None:
    x, y = xy
    for ch in text:
        d.text((x, y), ch, font=font, fill=fill)
        x += font.getlength(ch) + tracking


def wrap_words(words: list[str], font: ImageFont.FreeTypeFont, max_width: float) -> list[list[int]]:
    """Quebra em linhas equilibradas; devolve os índices das palavras de cada linha."""
    lines: list[list[int]] = [[]]
    width = 0.0
    space = font.getlength(" ")
    for i, w in enumerate(words):
        wl = font.getlength(w)
        if lines[-1] and width + space + wl > max_width:
            lines.append([])
            width = 0.0
        width += (space if lines[-1] else 0) + wl
        lines[-1].append(i)
    return lines


# ---------- números (contador animado) ------------------------------------------------------------------------

NUMBER = re.compile(r"\d{1,3}(?:[.,\s]\d{3})+(?:[.,]\d+)?|\d+(?:[.,]\d+)?")


def find_counter(text: str) -> dict | None:
    """O primeiro número do texto, se fizer sentido contar até ele ("47 testigos", "$200 Billion", "1.500").

    Anos (4 dígitos entre 1000 e 2100, sem separador) não contam: "1987 — Ohio" subindo de 0 fica estranho.
    """
    m = NUMBER.search(text)
    if not m:
        return None
    raw = m.group(0)
    digits = re.sub(r"\D", "", raw)
    if re.fullmatch(r"\d{4}", raw) and 1000 <= int(raw) <= 2100:
        return None
    # separador decimal: o último separador seguido de 1–2 dígitos ("3,5", "2.75"); o resto é milhar
    dec_m = re.search(r"([.,])(\d{1,2})$", raw)
    if dec_m and not re.fullmatch(r"\d{1,3}(?:[.,\s]\d{3})+", raw):
        decimals = len(dec_m.group(2))
        dec_sep = dec_m.group(1)
        int_part = raw[: dec_m.start()]
    else:
        decimals, dec_sep, int_part = 0, "", raw
    seps = re.findall(r"[.,\s]", int_part)
    thousands = seps[0] if seps else ""
    value = int(digits) / (10 ** decimals)
    if value < 2:
        return None
    return {"start": m.start(), "end": m.end(), "value": value, "decimals": decimals, "dec_sep": dec_sep,
            "thousands": thousands}


def format_counter(c: dict, value: float) -> str:
    d = c["decimals"]
    s = f"{value:,.{d}f}"  # 1,234.5
    int_part, _, frac = s.partition(".")
    int_part = int_part.replace(",", c["thousands"]) if c["thousands"] else int_part.replace(",", "")
    return int_part + (c["dec_sep"] + frac if d else "")


def counter_text(text: str, c: dict, fraction: float) -> str:
    v = c["value"] * ease_out_cubic(clamp(fraction))
    if not c["decimals"]:
        v = math.floor(v + 1e-9)
    return text[: c["start"]] + format_counter(c, v) + text[c["end"]:]


# ---------- codificação ---------------------------------------------------------------------------------------

def encode(frame_fn: FrameFn, duration: float, out: Path) -> Path:
    """Desenha `frame_fn(t)` em todos os quadros de `duration` e grava um .mkv com alfa."""
    frames = max(1, int(round(duration * FPS)))
    tmp = out.with_name(out.stem + ".tmp" + out.suffix)

    ffmpeg.encode_frames((frame_fn(i / FPS).tobytes() for i in range(frames)), (W, H), FPS, tmp)
    tmp.replace(out)
    return out
