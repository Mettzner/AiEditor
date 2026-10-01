"""Caminhos, configuração persistida (settings.json) e segredos (keyring)."""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any

import keyring
from keyring.errors import PasswordDeleteError

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = Path(os.environ.get("AIEDITOR_DATA", ROOT / "data"))
JOBS_DIR = DATA_DIR / "jobs"
CACHE_DIR = DATA_DIR / "cache"
MUSIC_DIR = DATA_DIR / "music"
UPLOADS_DIR = DATA_DIR / "uploads"
DB_PATH = DATA_DIR / "aieditor.db"
SETTINGS_PATH = DATA_DIR / "settings.json"

for _d in (DATA_DIR, JOBS_DIR, CACHE_DIR, MUSIC_DIR, UPLOADS_DIR):
    _d.mkdir(parents=True, exist_ok=True)

KEYRING_SERVICE = "AiEditor"

# Provedores com chave de API (guardadas no Windows Credential Manager).
SECRET_PROVIDERS = [
    "darkvi",
    "pexels",
    "pixabay",
    "youtube",
    "anthropic",
    "openai",
    "gemini",
    "fal",
    "elevenlabs",
    "google_oauth_client",  # JSON do client OAuth (Desktop/Web) do Google Cloud
    "google_refresh_token",
]

DEFAULT_SETTINGS: dict[str, Any] = {
    "stock_providers": [
        {"id": "pexels", "enabled": True, "priority": 1},
        {"id": "pixabay", "enabled": True, "priority": 2},
        {"id": "storyblocks", "enabled": False, "priority": 3},
        {"id": "shutterstock", "enabled": False, "priority": 4},
    ],
    "youtube": {"enabled": True, "creative_commons_only": True},
    "ai_image_providers": [
        {"id": "darkvi", "enabled": True, "priority": 1},
    ],
    "ai_video_providers": [
        {"id": "fal", "enabled": False, "priority": 1, "model": "veo3.1-lite"},
    ],
    "llm": {
        "plan": {"provider": "anthropic", "model": "claude-opus-5-5", "effort": "medium"},
        "direct": {"provider": "anthropic", "model": "claude-opus-5-5", "effort": "low"},
        "rewrite": {"provider": "anthropic", "model": "claude-opus-5-5", "effort": "low"},
    },
    "render": {
        "mode": "quality",  # quality (libx264) | fast (h264_amf)
        "x264_preset": "veryfast",
        "crf": 20,
        "amf_bitrate": "12M",
        "parallel_scenes": 3,
        "intermediate_crf": 16,
    },
    "transcription": {"model": "small", "compute_type": "int8", "divergence_warn": 0.15},
    "selection": {"min_score": 6.0, "per_page": 15, "parallel_scenes": 4},
    "darkvi": {"remaining": None, "limit": None, "updated_at": None, "tts_poll_seconds": 4, "tts_timeout_seconds": 1800},
    "music": {"library_dir": str(MUSIC_DIR), "default_volume_db": -22},
    "folders": {"cache": str(CACHE_DIR), "jobs": str(JOBS_DIR), "auto_cleanup": False},
    "fonts_dir": str(DATA_DIR / "fonts"),
    "words_per_minute": {"en": 150, "pt": 145, "es": 150},
    "worker": {"max_parallel_productions": 2},
}

_lock = threading.Lock()


def _deep_merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load_settings() -> dict[str, Any]:
    with _lock:
        if SETTINGS_PATH.exists():
            stored = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
            return _deep_merge(DEFAULT_SETTINGS, stored)
        return json.loads(json.dumps(DEFAULT_SETTINGS))


def save_settings(settings: dict[str, Any]) -> dict[str, Any]:
    with _lock:
        tmp = SETTINGS_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(settings, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(SETTINGS_PATH)
    return settings


def update_settings(patch: dict[str, Any]) -> dict[str, Any]:
    return save_settings(_deep_merge(load_settings(), patch))


def get_secret(provider: str) -> str | None:
    env = os.environ.get(f"AIEDITOR_{provider.upper()}_KEY")
    if env:
        return env
    if provider == "anthropic" and os.environ.get("ANTHROPIC_API_KEY"):
        return os.environ["ANTHROPIC_API_KEY"]
    return keyring.get_password(KEYRING_SERVICE, provider)


def set_secret(provider: str, value: str | None) -> None:
    if value:
        keyring.set_password(KEYRING_SERVICE, provider, value)
    else:
        try:
            keyring.delete_password(KEYRING_SERVICE, provider)
        except PasswordDeleteError:
            pass


def job_dir(production_id: int) -> Path:
    d = JOBS_DIR / str(production_id)
    d.mkdir(parents=True, exist_ok=True)
    return d
