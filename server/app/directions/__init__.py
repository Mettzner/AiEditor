"""Direções são plugins: manifest.json + prompt.md + overlays.py (§9.1)."""
from __future__ import annotations

import importlib
import json
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType

BASE = Path(__file__).parent


@dataclass
class Direction:
    id: str
    manifest: dict
    prompt: str
    overlays: ModuleType | None

    @property
    def params(self) -> dict:
        return self.manifest.get("params", {})


def list_directions() -> list[dict]:
    out = []
    for d in sorted(BASE.iterdir()):
        mf = d / "manifest.json"
        if d.is_dir() and mf.exists():
            m = json.loads(mf.read_text(encoding="utf-8"))
            m["id"] = d.name
            out.append(m)
    return out


def get_direction(direction_id: str) -> Direction:
    d = BASE / direction_id
    if not (d / "manifest.json").exists():
        raise KeyError(f"Direção '{direction_id}' não existe")
    manifest = json.loads((d / "manifest.json").read_text(encoding="utf-8"))
    prompt = (d / "prompt.md").read_text(encoding="utf-8") if (d / "prompt.md").exists() else ""
    overlays = importlib.import_module(f"{__name__}.{direction_id}.overlays") if (d / "overlays.py").exists() else None
    return Direction(direction_id, manifest, prompt, overlays)


def image_path(direction_id: str) -> Path | None:
    m = json.loads((BASE / direction_id / "manifest.json").read_text(encoding="utf-8"))
    img = m.get("image")
    return (BASE / direction_id / img) if img else None
