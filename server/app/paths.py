"""Caminhos centralizados (EMPACOTAMENTO_INSTALADOR_WINDOWS.md §3.1).

Empacotado (PyInstaller): recursos em sys._MEIPASS e dados do usuário em %LOCALAPPDATA%\\AiEditor, nunca na pasta
do programa. Desenvolvimento: recursos na raiz do repositório e dados em <repo>/data, como sempre foi.
AIEDITOR_DATA sobrescreve a pasta de dados nos dois modos (testes, teste de fumaça do build).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

APP_NAME = "AiEditor"
FROZEN = bool(getattr(sys, "frozen", False))
REPO_ROOT = Path(__file__).resolve().parents[2]


def resource_dir() -> Path:
    """Recursos empacotados (web/, bin/, resources/, VERSION)."""
    if FROZEN:
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return REPO_ROOT


def _ensure(p: Path) -> Path:
    p.mkdir(parents=True, exist_ok=True)
    return p


def data_dir() -> Path:
    env = os.environ.get("AIEDITOR_DATA")
    if env:
        return _ensure(Path(env))
    if FROZEN:
        from platformdirs import user_data_dir

        # appauthor=False + roaming=False → %LOCALAPPDATA%\AiEditor
        return _ensure(Path(user_data_dir(APP_NAME, appauthor=False, roaming=False)))
    return _ensure(REPO_ROOT / "data")


def jobs_dir() -> Path:
    return _ensure(data_dir() / "jobs")


def cache_dir() -> Path:
    return _ensure(data_dir() / "cache")


def music_dir() -> Path:
    return _ensure(data_dir() / "music")


def uploads_dir() -> Path:
    return _ensure(data_dir() / "uploads")


def logs_dir() -> Path:
    return _ensure(data_dir() / "logs")


def models_dir() -> Path:
    return _ensure(data_dir() / "models")


def tmp_dir() -> Path:
    return _ensure(data_dir() / "tmp")


def bundled_bin_dir() -> Path | None:
    """bin/ com ffmpeg.exe e ffprobe.exe empacotados; None em desenvolvimento (usa o PATH)."""
    d = resource_dir() / "bin"
    return d if (d / "ffmpeg.exe").exists() else None


def ffmpeg_path() -> str | None:
    d = bundled_bin_dir()
    return str(d / "ffmpeg.exe") if d else None


def ffprobe_path() -> str | None:
    d = bundled_bin_dir()
    return str(d / "ffprobe.exe") if d and (d / "ffprobe.exe").exists() else None


def web_dir() -> Path | None:
    """Frontend exportado (estático). Empacotado: <recursos>/web. Desenvolvimento: só com AIEDITOR_SERVE_WEB=1
    (serve web/out depois de um `npm run build`); normalmente o frontend roda no `next dev`."""
    if FROZEN:
        d = resource_dir() / "web"
    elif os.environ.get("AIEDITOR_SERVE_WEB") == "1":
        d = REPO_ROOT / "web" / "out"
    else:
        return None
    return d if (d / "index.html").exists() else None


def app_version() -> str:
    for p in (resource_dir() / "VERSION", REPO_ROOT / "VERSION"):
        if p.exists():
            return p.read_text(encoding="utf-8").strip()
    return "0.0.0"
