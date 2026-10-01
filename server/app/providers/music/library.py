"""Biblioteca local de músicas (data/music + tracks.json) (§11)."""
from __future__ import annotations

import json
import re
import threading
from pathlib import Path

from ...config import load_settings

AUDIO_EXT = {".mp3", ".wav", ".m4a", ".ogg", ".flac"}
_lock = threading.Lock()


def _dir() -> Path:
    d = Path(load_settings()["music"]["library_dir"])
    d.mkdir(parents=True, exist_ok=True)
    return d


def _index_path() -> Path:
    return _dir() / "tracks.json"


def load() -> list[dict]:
    """Lê tracks.json e inclui arquivos novos da pasta (sem tags) automaticamente."""
    with _lock:
        idx = _index_path()
        tracks = json.loads(idx.read_text(encoding="utf-8")) if idx.exists() else []
        known = {t["file"] for t in tracks}
        for f in sorted(_dir().iterdir()):
            if f.suffix.lower() in AUDIO_EXT and f.name not in known:
                tracks.append({"file": f.name, "mood": [], "bpm": None, "duration": None, "source": "manual",
                               "license": "", "uses": {}})
        tracks = [t for t in tracks if (_dir() / t["file"]).exists()]
        idx.write_text(json.dumps(tracks, indent=2, ensure_ascii=False), encoding="utf-8")
        return tracks


def save(tracks: list[dict]) -> None:
    with _lock:
        _index_path().write_text(json.dumps(tracks, indent=2, ensure_ascii=False), encoding="utf-8")


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-zà-ÿ]+", text.lower()) if len(w) > 2}


def pick(mood: str, channel_id: int | None) -> Path | None:
    tracks = load()
    if not tracks:
        return None
    want = _words(mood)
    ch = str(channel_id or 0)

    def score(t: dict) -> tuple[float, int]:
        tags = _words(" ".join(t.get("mood", [])))
        return (len(want & tags) / max(1, len(want)), -int(t.get("uses", {}).get(ch, 0)))

    best = max(tracks, key=score)
    best.setdefault("uses", {})[ch] = int(best["uses"].get(ch, 0)) + 1
    save(tracks)
    return _dir() / best["file"]
