"""Legendas .ass a partir dos tempos por palavra (blocos de 1–2 linhas, ~5–7 palavras)."""
from __future__ import annotations

import re
from pathlib import Path

MAX_WORDS = 7
LINE_CHARS = 42
ALIGN = {"bottom": 2, "middle": 5, "top": 8}


def _ass_color(hex_color: str, alpha: int = 0) -> str:
    c = hex_color.lstrip("#")
    r, g, b = c[0:2], c[2:4], c[4:6]
    return f"&H{alpha:02X}{b}{g}{r}".upper()


def _ts(t: float) -> str:
    cs = int(round(t * 100))
    return f"{cs // 360000}:{cs // 6000 % 60:02d}:{cs // 100 % 60:02d}.{cs % 100:02d}"


def _family(font: str) -> tuple[str, bool]:
    m = re.match(r"(.+?)\s+(Bold|Black|ExtraBold|SemiBold)$", font, re.I)
    return (m.group(1), True) if m else (font, False)


def cues(words: list[dict]) -> list[dict]:
    out, cur = [], []
    for i, w in enumerate(words):
        cur.append(w)
        text = " ".join(x["text"] for x in cur)
        gap = words[i + 1]["start"] - w["end"] if i + 1 < len(words) else 99
        if (len(cur) >= MAX_WORDS or len(text) >= LINE_CHARS * 2 - 10 or gap > 0.6
                or re.search(r"[.!?…]['\")”’]*$", w["text"])):
            out.append(cur)
            cur = []
    if cur:
        out.append(cur)
    result = []
    for k, c in enumerate(out):
        nxt = out[k + 1][0]["start"] if k + 1 < len(out) else c[-1]["end"] + 1
        result.append({"start": c[0]["start"], "end": min(c[-1]["end"] + 0.3, nxt),
                       "text": " ".join(x["text"] for x in c)})
    return result


def _wrap(text: str) -> str:
    if len(text) <= LINE_CHARS:
        return text
    words = text.split()
    best, best_diff = 1, 1e9
    for k in range(1, len(words)):
        a, b = " ".join(words[:k]), " ".join(words[k:])
        diff = abs(len(a) - len(b))
        if diff < best_diff:
            best, best_diff = k, diff
    return " ".join(words[:best]) + r"\N" + " ".join(words[best:])


def write_ass(words: list[dict], style: dict, dest: Path) -> Path:
    family, bold = _family(style["font"])
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: 1920
PlayResY: 1080
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{family},{style['size']},{_ass_color(style['color'])},{_ass_color(style['color'])},{_ass_color(style['outline_color'])},{_ass_color('#000000', 0x80)},{-1 if bold else 0},0,0,0,100,100,0,0,1,{style['outline']},1,{ALIGN.get(style['position'], 2)},120,120,{style['margin_v']},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines = []
    for c in cues(words):
        text = _wrap(c["text"].replace("{", "(").replace("}", ")"))
        lines.append(f"Dialogue: 0,{_ts(c['start'])},{_ts(c['end'])},Default,,0,0,0,,{text}")
    dest.write_text(header + "\n".join(lines) + "\n", encoding="utf-8-sig")
    return dest
