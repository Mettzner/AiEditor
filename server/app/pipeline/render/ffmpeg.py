"""Wrappers finos sobre ffmpeg/ffprobe."""
from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Callable, Sequence

from ...config import load_settings

_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class FFmpegError(RuntimeError):
    def __init__(self, message: str, log: str = ""):
        super().__init__(message)
        self.log = log


def _tool(name: str) -> str:
    configured = load_settings().get("ffmpeg_dir")
    if configured:
        candidate = Path(configured) / f"{name}.exe"
        if candidate.exists():
            return str(candidate)
    found = shutil.which(name)
    if not found:
        raise FFmpegError(f"{name} não encontrado no PATH (instale o FFmpeg ou defina ffmpeg_dir na Configuração)")
    return found


def ffmpeg_bin() -> str:
    return _tool("ffmpeg")


def ffprobe_bin() -> str:
    return _tool("ffprobe")


def available() -> bool:
    try:
        ffmpeg_bin()
        ffprobe_bin()
        return True
    except FFmpegError:
        return False


def run(args: Sequence[str], cwd: Path | None = None, on_progress: Callable[[float], None] | None = None,
        total_seconds: float | None = None) -> None:
    """Executa ffmpeg. Com on_progress, lê `-progress pipe:1` e reporta segundos processados."""
    cmd = [ffmpeg_bin(), "-hide_banner", "-nostdin", "-y", "-loglevel", "error"]
    if on_progress:
        cmd += ["-progress", "pipe:1", "-nostats"]
    cmd += list(args)
    if not on_progress:
        proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                              creationflags=_CREATE_NO_WINDOW)
        if proc.returncode != 0:
            raise FFmpegError(f"ffmpeg saiu com código {proc.returncode}", proc.stderr[-6000:])
        return
    # stderr vai para arquivo: com PIPE, um stderr cheio bloqueia o ffmpeg enquanto lemos o stdout
    with tempfile.TemporaryFile(mode="w+", encoding="utf-8", errors="replace") as err:
        proc = subprocess.Popen(cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=err, stdin=subprocess.DEVNULL,
                                text=True, encoding="utf-8", errors="replace", creationflags=_CREATE_NO_WINDOW)
        assert proc.stdout
        for line in proc.stdout:
            if line.startswith("out_time_us=") or line.startswith("out_time_ms="):
                try:
                    secs = int(line.split("=", 1)[1]) / 1_000_000
                except ValueError:
                    continue
                on_progress(min(1.0, secs / total_seconds) if total_seconds else secs)
        code = proc.wait()
        err.seek(0)
        stderr = err.read()
    if code != 0:
        raise FFmpegError(f"ffmpeg saiu com código {code}", stderr[-6000:])


def probe(path: Path) -> dict:
    proc = subprocess.run([ffprobe_bin(), "-v", "error", "-print_format", "json", "-show_format", "-show_streams",
                           str(path)], capture_output=True, text=True, encoding="utf-8", errors="replace",
                          creationflags=_CREATE_NO_WINDOW)
    if proc.returncode != 0:
        raise FFmpegError(f"ffprobe falhou em {path.name}", proc.stderr)
    return json.loads(proc.stdout)


def duration(path: Path) -> float:
    return float(probe(path)["format"]["duration"])


def encoders() -> str:
    proc = subprocess.run([ffmpeg_bin(), "-hide_banner", "-encoders"], capture_output=True, text=True,
                          creationflags=_CREATE_NO_WINDOW)
    return proc.stdout


def decode_mono_f32(path: Path, sample_rate: int = 16000) -> bytes:
    """Decodifica para PCM float32 mono (entrada do whisper), sem depender do PyAV."""
    proc = subprocess.run([ffmpeg_bin(), "-v", "error", "-i", str(path), "-ac", "1", "-ar", str(sample_rate),
                           "-f", "f32le", "pipe:1"], capture_output=True, creationflags=_CREATE_NO_WINDOW)
    if proc.returncode != 0:
        raise FFmpegError(f"falha ao decodificar {path.name}", proc.stderr.decode("utf-8", "replace"))
    return proc.stdout
