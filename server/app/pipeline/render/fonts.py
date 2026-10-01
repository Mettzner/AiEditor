"""Resolve um nome de fonte ("Montserrat Bold") para um arquivo .ttf/.otf."""
from __future__ import annotations

import os
import re
from functools import lru_cache
from pathlib import Path

from ...config import load_settings

FALLBACKS = ["arialbd.ttf", "segoeuib.ttf", "arial.ttf", "DejaVuSans-Bold.ttf"]


def _dirs() -> list[Path]:
    dirs = [Path(load_settings()["fonts_dir"])]
    windir = os.environ.get("WINDIR", r"C:\Windows")
    dirs += [Path(windir) / "Fonts", Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "Windows" / "Fonts"]
    return [d for d in dirs if d.exists()]


def _key(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


@lru_cache(maxsize=64)
def find_font(name: str) -> str:
    wanted = _key(name)
    files = [f for d in _dirs() for f in d.iterdir() if f.suffix.lower() in (".ttf", ".otf")]
    for f in files:
        if _key(f.stem) == wanted:
            return str(f)
    for f in files:
        if _key(f.stem).startswith(wanted):
            return str(f)
    for fb in FALLBACKS:
        for d in _dirs():
            if (d / fb).exists():
                return str(d / fb)
    raise FileNotFoundError(f"Fonte '{name}' não encontrada; coloque o .ttf em {load_settings()['fonts_dir']}")


def is_exact(name: str) -> bool:
    try:
        return _key(Path(find_font(name)).stem).startswith(_key(name))
    except FileNotFoundError:
        return False
