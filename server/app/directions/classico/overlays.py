"""Templates animados do Clássico: destaque (lower third com contador), título de capítulo (letterbox),
card de lugar e data (máquina de escrever), citação em tela cheia (palavras no ritmo da narração) e light leak.

Cada template recebe o overlay da timeline e o estilo do canal e devolve `t → Image RGBA 1920×1080`
(t em segundos desde o início do overlay). `SFX` diz quais efeitos sonoros acompanham cada animação.
"""
from __future__ import annotations

import os
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from ...pipeline.render import anim
from ...pipeline.render.anim import (H, W, blank, clamp, draw_tracked, ease_in_cubic, ease_out_cubic, envelope, fade,
                                     hex_rgba, progress, tracked_width)
from ...pipeline.render.fonts import find_font


def _font(style: dict, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(find_font(style["font"]), size)


def _quote_font(style: dict, size: int) -> ImageFont.FreeTypeFont:
    """Serifada itálica para citações (Georgia, presente em todo Windows); senão, a fonte do canal."""
    fonts = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
    for name in ("georgiai.ttf", "Georgia Italic.ttf"):
        if (fonts / name).exists():
            return ImageFont.truetype(str(fonts / name), size)
    return _font(style, size)


# ---------- destaque (lower third) ----------------------------------------------------------------------------

def highlight(o: dict, style: dict) -> anim.FrameFn:
    """Filete cresce, caixa abre da esquerda, texto desliza; números contam de 0 até o valor."""
    text = o["text"].upper()
    dur = o["end"] - o["start"]
    font = _font(style, 52)
    counter = anim.find_counter(text)
    l, t, r, b = font.getbbox(text)
    pad_x, pad_y = 34, 20
    x, y = 110, H - 330
    box_w, box_h = int(r - l + pad_x * 2), int(b - t + pad_y * 2)
    primary, accent = hex_rgba(style["color_primary"]), hex_rgba(style["color_accent"], 235)

    def frame(tt: float) -> Image.Image:
        img = blank()
        d = ImageDraw.Draw(img)
        out = ease_in_cubic(progress(tt, dur - 0.4, 0.4))
        strip = ease_out_cubic(progress(tt, 0, 0.25)) * box_h
        if strip >= 1:
            d.rectangle((x - 12, y + box_h - strip, x - 4, y + box_h),
                        fill=primary[:3] + (int(255 * (1 - out)),))
        width = int(box_w * ease_out_cubic(progress(tt, 0.12, 0.45)) * (1 - out))
        if width >= 2:
            box = Image.new("RGBA", (box_w, box_h), accent)
            layer = Image.new("RGBA", (box_w, box_h), (0, 0, 0, 0))
            label = anim.counter_text(text, counter, progress(tt, 0.25, 1.2)) if counter else text
            slide = 24 * (1 - ease_out_cubic(progress(tt, 0.2, 0.5)))
            ImageDraw.Draw(layer).text((pad_x - l - slide, pad_y - t), label, font=font, fill=primary)
            box.alpha_composite(fade(layer, ease_out_cubic(progress(tt, 0.2, 0.4))))
            img.alpha_composite(box.crop((0, 0, width, box_h)), (x, y))
        return img

    return frame


# ---------- título de capítulo --------------------------------------------------------------------------------

def chapter_title(o: dict, style: dict) -> anim.FrameFn:
    """Barras de cinema entram, número do capítulo sobe, título fecha o espaçamento entre letras e um filete
    cresce do centro."""
    label = o["text"].upper()
    dur = o["end"] - o["start"]
    font = _font(style, 84)
    small = _font(style, 40)
    number = f"{int(o['index']):02d}" if o.get("index") else ""
    primary, accent = hex_rgba(style["color_primary"]), hex_rgba(style["color_accent"])
    _, t, _, b = font.getbbox(label)
    ty = (H - (b - t)) / 2 - t
    final_w = tracked_width(font, label, 6)
    bars = 120

    def frame(tt: float) -> Image.Image:
        img = blank()
        d = ImageDraw.Draw(img)
        p_in = ease_out_cubic(progress(tt, 0, 0.6))
        p_out = ease_in_cubic(progress(tt, dur - 0.45, 0.45))
        keep = 1 - p_out
        d.rectangle((0, H // 2 - 125, W, H // 2 + 125), fill=(0, 0, 0, int(110 * p_in * keep)))
        bh = bars * p_in * keep
        if bh >= 1:
            d.rectangle((0, 0, W, bh), fill=(0, 0, 0, 255))
            d.rectangle((0, H - bh, W, H), fill=(0, 0, 0, 255))
        layer = blank()
        ld = ImageDraw.Draw(layer)
        tracking = 30 + (6 - 30) * ease_out_cubic(progress(tt, 0.15, 1.2))
        tw = tracked_width(font, label, tracking)
        draw_tracked(ld, ((W - tw) / 2, ty), label, font, tracking, primary)
        line_w = min(W - 200, final_w + 80) * ease_out_cubic(progress(tt, 0.45, 0.7))
        if line_w >= 2:
            ld.rectangle(((W - line_w) / 2, H // 2 + 70, (W + line_w) / 2, H // 2 + 76), fill=accent)
        img.alpha_composite(fade(layer, ease_out_cubic(progress(tt, 0.15, 0.6)) * keep))
        if number:  # "— 02 —" acima do título
            num = blank()
            nd = ImageDraw.Draw(num)
            p = ease_out_cubic(progress(tt, 0.1, 0.6))
            nw = tracked_width(small, number, 10)
            ny = H // 2 - 108 + 16 * (1 - p)
            _, nt, _, nb = small.getbbox(number)
            draw_tracked(nd, ((W - nw) / 2, ny - nt), number, small, 10, accent)
            mid, dash = ny + (nb - nt) / 2, 46 * p
            nd.rectangle(((W - nw) / 2 - 22 - dash, mid - 1, (W - nw) / 2 - 22, mid + 1), fill=accent)
            nd.rectangle(((W + nw) / 2 + 22, mid - 1, (W + nw) / 2 + 22 + dash, mid + 1), fill=accent)
            img.alpha_composite(fade(num, p * keep))
        return img

    return frame


# ---------- card de lugar e data (máquina de escrever) --------------------------------------------------------

def typewriter_times(o: dict) -> list[float]:
    """Instante (desde o início do overlay) em que cada caractere aparece."""
    n = len(o["text"])
    type_dur = min(1.8, max(0.6, 0.055 * n))
    return [0.35 + type_dur * i / max(1, n) for i in range(n)]


def place_card(o: dict, style: dict) -> anim.FrameFn:
    text = o["text"]
    dur = o["end"] - o["start"]
    font = _font(style, 46)
    primary, accent = hex_rgba(style["color_primary"]), hex_rgba(style["color_accent"])
    times = typewriter_times(o)
    typed_at = times[-1] if times else 0.35
    l, t, r, b = font.getbbox(text)
    x, y = 110, H - 250  # acima da faixa das legendas
    grad = blank()
    gd = ImageDraw.Draw(grad)
    for i in range(340):  # degradê de baixo para cima, o suficiente para o texto ler sobre qualquer imagem
        gd.line((0, H - i, W, H - i), fill=(0, 0, 0, int(150 * (1 - i / 340) ** 1.6)))

    def frame(tt: float) -> Image.Image:
        env = envelope(tt, dur, 0, 0.4)
        img = fade(grad.copy(), ease_out_cubic(progress(tt, 0, 0.4)))
        d = ImageDraw.Draw(img)
        lw = min(220, r - l) * ease_out_cubic(progress(tt, 0, 0.45))
        if lw >= 1:
            d.rectangle((x, y - 22, x + lw, y - 16), fill=accent)
        shown = text[: sum(1 for c in times if c <= tt)]
        d.text((x - l, y - t), shown, font=font, fill=primary)
        typing = tt < typed_at + 0.1
        if (typing or int((tt - typed_at) * 3) % 2 == 0) and tt < typed_at + 1.0 and tt >= 0.3:
            cx = x + font.getlength(shown) + 6
            d.rectangle((cx, y - 2, cx + 4, y + (b - t) + 4), fill=accent)
        return fade(img, env)

    return frame


# ---------- citação em tela cheia -----------------------------------------------------------------------------

def quote(o: dict, style: dict) -> anim.FrameFn:
    """Frase marcante da narração em tela cheia; cada palavra surge quando é falada (`word_times`)."""
    text = o["text"].strip().strip("\"“”«»")
    dur = o["end"] - o["start"]
    words = text.split()
    font = _quote_font(style, 64)
    mark_font = _quote_font(style, 240)
    primary, accent = hex_rgba(style["color_primary"]), hex_rgba(style["color_accent"])
    times = o.get("word_times")
    if not times or len(times) != len(words):
        span = min(dur * 0.55, 0.32 * len(words))
        times = [0.3 + span * i / max(1, len(words)) for i in range(len(words))]
    lines = anim.wrap_words(words, font, 1400)
    line_h = 92
    _, ft, _, fb = font.getbbox("Ág")
    y0 = (H - len(lines) * line_h) / 2
    space = font.getlength(" ")
    pos: dict[int, tuple[float, float]] = {}
    left = W
    for li, idx in enumerate(lines):
        lw = sum(font.getlength(words[i]) for i in idx) + space * (len(idx) - 1)
        x = (W - lw) / 2
        left = min(left, x)
        for i in idx:
            pos[i] = (x, y0 + li * line_h - ft)
            x += font.getlength(words[i]) + space

    def frame(tt: float) -> Image.Image:
        env = envelope(tt, dur, 0.35, 0.45)
        img = Image.new("RGBA", (W, H), (0, 0, 0, int(95 * env)))
        layer = blank()
        d = ImageDraw.Draw(layer)
        p = ease_out_cubic(progress(tt, 0, 0.6))
        d.text((left - 70, y0 - 150 - 20 * (1 - p)), "“", font=mark_font, fill=accent[:3] + (int(230 * p),))
        for i, w in enumerate(words):
            a = ease_out_cubic(progress(tt, times[i] - 0.05, 0.3))
            if a <= 0:
                continue
            x, y = pos[i]
            d.text((x, y + 14 * (1 - a)), w, font=font, fill=primary[:3] + (int(255 * a),))
        img.alpha_composite(layer)
        return fade(img, env) if env < 1 else img

    return frame


# ---------- light leak ----------------------------------------------------------------------------------------

def light_leak(o: dict, style: dict) -> anim.FrameFn:
    """Brilho quente que atravessa a tela nas mudanças de época (desenhado em baixa resolução e ampliado)."""
    dur = o["end"] - o["start"]
    w, h = 320, 180
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)

    def frame(tt: float) -> Image.Image:
        p = clamp(tt / dur)
        strength = float(np.sin(np.pi * p)) ** 1.3 * 0.55
        cx, cy = w * (-0.2 + 1.4 * p), h * (0.35 + 0.2 * np.sin(p * 3))
        d1 = np.exp(-(((xx - cx) / (w * 0.32)) ** 2 + ((yy - cy) / (h * 0.55)) ** 2))
        d2 = np.exp(-(((xx - cx * 0.8 - w * 0.1) / (w * 0.18)) ** 2 + ((yy - h * 0.7) / (h * 0.3)) ** 2))
        alpha = np.clip((d1 * 0.85 + d2 * 0.6) * strength, 0, 1)
        rgba = np.zeros((h, w, 4), np.uint8)
        rgba[..., 0] = 255
        rgba[..., 1] = np.clip(120 + 70 * d1, 0, 255).astype(np.uint8)
        rgba[..., 2] = np.clip(40 + 40 * d2, 0, 255).astype(np.uint8)
        rgba[..., 3] = (alpha * 255).astype(np.uint8)
        return Image.fromarray(rgba, "RGBA").resize((W, H), Image.BILINEAR)

    return frame


ANIMATED = {"highlight": highlight, "chapter": chapter_title, "place_card": place_card, "quote": quote,
            "light_leak": light_leak}

# efeitos sonoros de cada animação: (categoria, segundos desde o início do overlay)
SFX = {
    "highlight": lambda o: [("swoosh_soft", 0.08)],
    "chapter": lambda o: [("impact", 0.12)],
    "place_card": lambda o: [("click", t) for t, ch in zip(typewriter_times(o), o["text"]) if not ch.isspace()],
    "quote": lambda o: [("swoosh_soft", 0.0)],
}
