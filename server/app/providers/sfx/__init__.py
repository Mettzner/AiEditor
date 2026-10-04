"""Biblioteca local de efeitos sonoros: data/sfx/<categoria>/ + sfx.json.

Ordem de preferência por categoria: sons colocados à mão na pasta → sons baixados do Freesound (CC0, ou
CC BY se permitido) → sons sintetizados (sempre disponíveis, sem rede). Faltando sons e havendo chave do
Freesound, `ensure` baixa alguns uma única vez; depois a biblioteca serve offline.
"""
from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from typing import Callable

from ...config import get_secret, load_settings
from ..http import download
from . import freesound, synth

log = logging.getLogger("aieditor.sfx")

AUDIO_EXT = {".mp3", ".wav", ".m4a", ".ogg", ".flac"}
CATEGORIES = synth.CATEGORIES
SYNTH_VARIANTS = 3
_lock = threading.Lock()


def _dir() -> Path:
    d = Path(load_settings()["sfx"]["library_dir"])
    d.mkdir(parents=True, exist_ok=True)
    return d


def _index_path() -> Path:
    return _dir() / "sfx.json"


def load() -> list[dict]:
    """Lê sfx.json e inclui arquivos novos das pastas de categoria (fonte "manual")."""
    with _lock:
        base = _dir()
        idx = _index_path()
        sounds = json.loads(idx.read_text(encoding="utf-8")) if idx.exists() else []
        known = {s["file"] for s in sounds}
        for cat in CATEGORIES:
            folder = base / cat
            folder.mkdir(exist_ok=True)
            for f in sorted(folder.iterdir()):
                rel = f"{cat}/{f.name}"
                if f.suffix.lower() in AUDIO_EXT and rel not in known:
                    sounds.append({"file": rel, "category": cat, "source": "manual", "license": "", "author": "",
                                   "page_url": ""})
        sounds = [s for s in sounds if (base / s["file"]).exists()]
        idx.write_text(json.dumps(sounds, indent=2, ensure_ascii=False), encoding="utf-8")
        return sounds


def _save(sounds: list[dict]) -> None:
    with _lock:
        _index_path().write_text(json.dumps(sounds, indent=2, ensure_ascii=False), encoding="utf-8")


def ensure(categories: set[str], issue: Callable[[str, str], None] | None = None) -> None:
    """Completa as categorias pedidas com sons do Freesound, se houver chave e a opção estiver ligada."""
    cfg = load_settings()["sfx"]
    if not cfg.get("freesound", True) or not get_secret("freesound"):
        return
    sounds = load()
    want = int(cfg.get("per_category", 4))
    changed = False
    for cat in sorted(categories):
        have = [s for s in sounds if s["category"] == cat]
        if len(have) >= want:
            continue
        try:
            found = freesound.search(cat, limit=want * 2, allow_attribution=bool(cfg.get("allow_attribution")))
        except Exception as e:  # noqa: BLE001
            if issue:
                issue(f"Freesound indisponível ({e}); usando efeitos sintetizados", repr(e))
            return
        known = {s.get("freesound_id") for s in have}
        for r in found:
            if len(have) >= want:
                break
            if r["id"] in known:
                continue
            rel = f"{cat}/freesound_{r['id']}.mp3"
            try:
                download(r["preview"], _dir() / rel)
            except Exception as e:  # noqa: BLE001
                log.warning("download do som %s falhou: %r", r["id"], e)
                continue
            entry = {"file": rel, "category": cat, "source": "freesound", "freesound_id": r["id"],
                     "name": r["name"], "license": r["license"], "author": r["author"], "page_url": r["page_url"]}
            sounds.append(entry)
            have.append(entry)
            changed = True
    if changed:
        _save(sounds)


def _synth(category: str, variant: int) -> dict:
    folder = _dir() / "_synth"
    folder.mkdir(exist_ok=True)
    dest = folder / f"{category}_{variant}.wav"
    if not dest.exists():
        synth.generate(category, variant, dest)
    return {"file": str(dest), "category": category, "source": "synth", "license": "", "author": "", "page_url": ""}


def pick(category: str, key: int, sounds: list[dict] | None = None) -> dict:
    """Um som da categoria. `key` alterna entre as opções de forma determinística (retomadas dão o mesmo som)."""
    pool = [s for s in (sounds if sounds is not None else load()) if s["category"] == category]
    if pool:
        s = pool[key % len(pool)]
        return {**s, "file": str(_dir() / s["file"])}
    return _synth(category, key % SYNTH_VARIANTS)
