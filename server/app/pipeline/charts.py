"""Gráfico determinístico (Fase B3): números com fonte viram um gráfico de barras desenhado pelo código.

Nunca por IA de imagem (que inventa números e eixos). Só é desenhado quando a cena traz data_points E
data_source; a fonte aparece no próprio quadro e nos créditos. O PNG entra como imagem (movimento lento no
render), com validação "deterministic": os valores são exatamente os do roteiro, não uma interpretação visual.
"""
from __future__ import annotations

import logging
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

log = logging.getLogger("aieditor.charts")
W, H = 1920, 1080
BG, FG, MUTED, BAR = (18, 18, 20), (240, 240, 240), (150, 150, 155), (230, 57, 70)


def _font(size: int, bold: bool = True) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    try:
        from .render.fonts import find_font

        return ImageFont.truetype(find_font("Montserrat Bold" if bold else "Arial"), size)
    except Exception:  # noqa: BLE001 — sem a fonte do canal, a padrão do Pillow
        try:
            return ImageFont.truetype("arialbd.ttf" if bold else "arial.ttf", size)
        except OSError:
            return ImageFont.load_default()


def _fmt(v: float) -> str:
    return f"{v:,.0f}" if abs(v) >= 100 else (f"{v:.1f}".rstrip("0").rstrip(".") if v != int(v) else str(int(v)))


def render_bar_chart(points: list[dict], source: str, dest: Path, title: str = "", accent=BAR) -> Path:
    """points: [{label, value, unit}] (até 12). Valores negativos ou zero aparecem como barra vazia."""
    if not points or not (source or "").strip():
        raise ValueError("gráfico precisa de dados e de fonte")
    points = points[:12]
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    top = 140 if title else 90
    if title:
        d.text((120, 60), title[:80], fill=FG, font=_font(56))
    left, right, bottom = 160, W - 120, H - 190
    vmax = max((float(p["value"]) for p in points), default=1.0) or 1.0
    n = len(points)
    slot = (right - left) / n
    bar_w = slot * 0.62
    label_font, value_font = _font(34, bold=False), _font(40)
    d.line([(left, bottom), (right, bottom)], fill=MUTED, width=3)
    for i, p in enumerate(points):
        v = max(0.0, float(p["value"]))
        h = (bottom - top - 60) * v / vmax
        x0 = left + i * slot + (slot - bar_w) / 2
        d.rectangle([x0, bottom - h, x0 + bar_w, bottom], fill=accent)
        text = _fmt(float(p["value"])) + (f" {p.get('unit')}" if p.get("unit") else "")
        tw = d.textlength(text, font=value_font)
        d.text((x0 + bar_w / 2 - tw / 2, bottom - h - 52), text, fill=FG, font=value_font)
        lw = d.textlength(str(p["label"])[:18], font=label_font)
        d.text((x0 + bar_w / 2 - lw / 2, bottom + 18), str(p["label"])[:18], fill=MUTED, font=label_font)
    d.text((120, H - 90), f"Fonte: {source.strip()[:140]}", fill=MUTED, font=_font(30, bold=False))
    dest.parent.mkdir(parents=True, exist_ok=True)
    img.save(dest, "PNG")
    return dest


def chart_entry(scene: dict, dest: Path, accent_hex: str | None = None) -> dict | None:
    """Entrada da seleção para uma cena de dados com fonte; None se a cena não tem o que desenhar."""
    points, source = scene.get("data_points") or [], (scene.get("data_source") or "").strip()
    if not points or not source:
        return None
    accent = BAR
    if accent_hex and len(accent_hex.lstrip("#")) == 6:
        h = accent_hex.lstrip("#")
        accent = tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))
    render_bar_chart(points, source, dest, title=scene.get("chapter_title") or "", accent=accent)
    return {"source": "chart", "source_used": "chart", "is_image": True, "provider": "aieditor",
            "external_id": None, "asset": f"assets/{dest.name}", "method": "chart", "score": None,
            "data_source": source, "license": "gerado pelo AiEditor a partir dos dados citados", "in": 0.0,
            "in_point": 0.0, "out_point": None,
            "validation": {"status": "validated", "score_basis": "deterministic",
                           "reasons": [f"valores do roteiro, fonte: {source}"]}}


def draw_charts(ctx, sel, plan: dict) -> int:
    """Desenha o gráfico de cada cena com dados e fonte ainda sem asset; devolve quantos entraram."""
    n = 0
    for scene in plan["scenes"]:
        if scene["id"] in sel.selection or not scene.get("data_points"):
            continue
        try:
            entry = chart_entry(scene, ctx.path("assets", f"{scene['id']}_chart.png"), ctx.config.color_accent)
        except Exception as e:  # noqa: BLE001 — sem gráfico, a cena segue a cadeia normal
            log.warning("cena %s: gráfico falhou: %s", scene["id"], e)
            continue
        if entry:
            with sel.lock:
                sel.selection[scene["id"]] = entry
            n += 1
    if n:
        sel.save()
    return n
