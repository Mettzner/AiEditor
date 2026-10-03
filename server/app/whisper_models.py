"""Modelo de transcrição (faster-whisper) baixado na primeira execução (EMPACOTAMENTO_INSTALADOR_WINDOWS.md §3.4).

O modelo não vai no instalador (deixaria o setup muito grande): o assistente inicial baixa o escolhido para
<dados>/models/faster-whisper-<tamanho>, com progresso. No app instalado, sem modelo baixado, a criação de
produções fica bloqueada. Em desenvolvimento, sem a pasta local, vale o cache padrão do Hugging Face (como antes).
"""
from __future__ import annotations

import logging
import os
import threading
import time
from pathlib import Path

from . import paths

log = logging.getLogger("aieditor.models")

# tamanho aproximado do download (MB), para a barra de progresso
SIZES_MB = {"tiny": 75, "base": 145, "small": 484, "medium": 1530}
LABELS = {"base": "base (leve, ~145 MB)", "small": "small (recomendado, ~484 MB)", "medium": "medium (~1,5 GB)",
          "tiny": "tiny (~75 MB)"}
RECOMMENDED = "small"

_state: dict = {"size": None, "status": "idle", "bytes": 0, "total": 0, "error": None}
_lock = threading.Lock()


def model_dir(size: str) -> Path:
    return paths.models_dir() / f"faster-whisper-{size}"


def is_downloaded(size: str) -> bool:
    d = model_dir(size)
    return (d / "model.bin").exists() and (d / "config.json").exists()


def model_ready(size: str) -> bool:
    """Pode transcrever? No app instalado só com o modelo na pasta local."""
    return is_downloaded(size) or not paths.FROZEN


def model_source(size: str) -> str:
    """O que passar ao WhisperModel: a pasta local se existir; em desenvolvimento, o nome (cache do HF)."""
    if is_downloaded(size):
        return str(model_dir(size))
    if paths.FROZEN:
        raise FileNotFoundError(f"Modelo de transcrição '{size}' não baixado. Baixe em Configuração → Transcrição.")
    return size


def _dir_bytes(d: Path) -> int:
    total = 0
    for root, _, files in os.walk(d):
        for f in files:
            try:
                total += (Path(root) / f).stat().st_size
            except OSError:
                pass
    return total


def status() -> dict:
    with _lock:
        s = dict(_state)
    if s["status"] == "downloading" and s["size"]:
        s["bytes"] = _dir_bytes(model_dir(s["size"]))
    s["progress"] = round(min(0.99, s["bytes"] / s["total"]), 3) if s["total"] and s["status"] == "downloading" \
        else (1.0 if s["status"] == "done" else 0.0)
    s["downloaded"] = {k: is_downloaded(k) for k in SIZES_MB}
    return s


def start_download(size: str) -> dict:
    if size not in SIZES_MB:
        raise ValueError(f"Modelo desconhecido: {size}")
    with _lock:
        if _state["status"] == "downloading":
            return dict(_state)
        _state.update(size=size, status="downloading", bytes=0, total=SIZES_MB[size] * 1024 * 1024, error=None)
    threading.Thread(target=_download, args=(size,), daemon=True, name="whisper-download").start()
    return status()


def _download(size: str) -> None:
    os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")  # sem console no app instalado
    started = time.monotonic()
    try:
        from faster_whisper.utils import download_model

        download_model(size, output_dir=str(model_dir(size)))
        from .config import update_settings

        update_settings({"transcription": {"model": size}})
        with _lock:
            _state.update(status="done", bytes=_dir_bytes(model_dir(size)))
        log.info("modelo whisper '%s' baixado em %.0fs", size, time.monotonic() - started)
    except Exception as e:  # noqa: BLE001
        log.exception("download do modelo whisper '%s' falhou", size)
        with _lock:
            _state.update(status="error", error=f"{type(e).__name__}: {e}"[:500])
