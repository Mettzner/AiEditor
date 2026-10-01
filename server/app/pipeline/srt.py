"""Leitura/escrita mínima de SRT."""
from __future__ import annotations

import re
from pathlib import Path

_TIME = re.compile(r"(\d+):(\d+):(\d+)[,.](\d+)")


def _t(s: str) -> float:
    h, m, sec, ms = _TIME.match(s.strip()).groups()  # type: ignore[union-attr]
    return int(h) * 3600 + int(m) * 60 + int(sec) + int(ms.ljust(3, "0")[:3]) / 1000


def _fmt(t: float) -> str:
    ms = int(round(t * 1000))
    return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"


def parse(path: Path) -> list[dict]:
    cues = []
    for block in re.split(r"\r?\n\r?\n", path.read_text(encoding="utf-8-sig").strip()):
        lines = [l for l in block.splitlines() if l.strip()]
        idx = next((i for i, l in enumerate(lines) if "-->" in l), None)
        if idx is None:
            continue
        a, b = lines[idx].split("-->")
        cues.append({"start": _t(a), "end": _t(b), "text": " ".join(lines[idx + 1:])})
    return cues


def merge_srts(parts: list[tuple[Path, float]], dest: Path) -> None:
    out, n = [], 1
    for path, offset in parts:
        for c in parse(path):
            out.append(f"{n}\n{_fmt(c['start'] + offset)} --> {_fmt(c['end'] + offset)}\n{c['text']}\n")
            n += 1
    dest.write_text("\n".join(out), encoding="utf-8")
