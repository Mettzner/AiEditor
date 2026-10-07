"""Wrappers finos sobre ffmpeg/ffprobe."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import threading
import time
from functools import lru_cache
from pathlib import Path
from typing import Callable, Iterable, Sequence

from ...config import load_settings

_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class FFmpegError(RuntimeError):
    def __init__(self, message: str, log: str = ""):
        super().__init__(message)
        self.log = log


class FFmpegCancelled(FFmpegError):
    """A produção foi cancelada: o processo do ffmpeg foi encerrado no meio."""


def _fresh_windows_path() -> str:
    """PATH atual do registro (Máquina + Usuário). Processos abertos antes de instalar o FFmpeg,
    como um terminal do VS Code, herdam um PATH antigo que ainda não tem a pasta nova."""
    if os.name != "nt":
        return ""
    import winreg

    parts = []
    for root, key in ((winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"),
                      (winreg.HKEY_CURRENT_USER, "Environment")):
        try:
            with winreg.OpenKey(root, key) as k:
                parts.append(os.path.expandvars(winreg.QueryValueEx(k, "Path")[0]))
        except OSError:
            pass
    return os.pathsep.join(parts)


def _winget_dirs() -> list[str]:
    base = Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WinGet"
    dirs = [str(p) for p in sorted((base / "Packages").glob("Gyan.FFmpeg*/*/bin"), reverse=True)]
    return dirs + [str(base / "Links")]


@lru_cache(maxsize=8)
def _locate(name: str, configured: str | None) -> str | None:
    from ... import paths

    bundled = paths.bundled_bin_dir()  # app instalado: bin\ffmpeg.exe ao lado do executável
    if bundled and (bundled / f"{name}.exe").exists():
        return str(bundled / f"{name}.exe")
    if configured:
        candidate = Path(configured) / f"{name}.exe"
        if candidate.exists():
            return str(candidate)
    return (shutil.which(name)
            or shutil.which(name, path=_fresh_windows_path())
            or shutil.which(name, path=os.pathsep.join(_winget_dirs())))


def _tool(name: str) -> str:
    found = _locate(name, load_settings().get("ffmpeg_dir"))
    if not found:
        _locate.cache_clear()  # tenta de novo na próxima chamada (ex.: instalado depois)
        raise FFmpegError(f"{name} não encontrado (instale o FFmpeg ou defina a pasta dele em Configuração → Render)")
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


@lru_cache(maxsize=1)
def amf_available() -> bool:
    """O encoder h264_amf funciona neste PC? Estar listado não basta (aparece mesmo sem placa AMD): codifica alguns
    quadros de teste. O render rápido por GPU só é oferecido quando isto é verdade (§3.5)."""
    try:
        proc = subprocess.run([ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                               "color=c=black:s=256x144:r=30:d=0.2", "-c:v", "h264_amf", "-f", "null", "-"],
                              capture_output=True, timeout=30, creationflags=_CREATE_NO_WINDOW)
        return proc.returncode == 0
    except (FFmpegError, OSError, subprocess.TimeoutExpired):
        return False


def version() -> str | None:
    try:
        out = subprocess.run([ffmpeg_bin(), "-version"], capture_output=True, text=True, timeout=15,
                             creationflags=_CREATE_NO_WINDOW).stdout
        return out.splitlines()[0] if out else None
    except (FFmpegError, OSError, subprocess.TimeoutExpired):
        return None


STALL_SECONDS = 120


def run(args: Sequence[str], cwd: Path | None = None, on_progress: Callable[[float], None] | None = None,
        total_seconds: float | None = None, stall_seconds: float = STALL_SECONDS,
        cancel: Callable[[], bool] | None = None) -> None:
    """Executa ffmpeg. Com on_progress, lê `-progress pipe:1` e reporta segundos processados.

    Se o tempo processado não avançar por `stall_seconds`, o processo é encerrado (FFmpegError) em vez de
    deixar a produção presa para sempre.
    """
    cmd = [ffmpeg_bin(), "-hide_banner", "-nostdin", "-y", "-loglevel", "error"]
    if on_progress:
        cmd += ["-progress", "pipe:1", "-nostats"]
    cmd += list(args)
    if not on_progress and not cancel:
        proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                              creationflags=_CREATE_NO_WINDOW)
        if proc.returncode != 0:
            raise FFmpegError(f"ffmpeg saiu com código {proc.returncode}", proc.stderr[-6000:])
        return
    if not on_progress:  # só cancelamento: espera o processo conferindo a cada segundo
        with tempfile.TemporaryFile(mode="w+", encoding="utf-8", errors="replace") as err:
            proc = subprocess.Popen(cmd, cwd=cwd, stdout=subprocess.DEVNULL, stderr=err, stdin=subprocess.DEVNULL,
                                    creationflags=_CREATE_NO_WINDOW)
            while proc.poll() is None:
                if cancel and cancel():
                    proc.kill()
                    proc.wait()
                    raise FFmpegCancelled("produção cancelada durante o ffmpeg")
                time.sleep(0.5)
            err.seek(0)
            stderr = err.read()
        if proc.returncode != 0:
            raise FFmpegError(f"ffmpeg saiu com código {proc.returncode}", stderr[-6000:])
        return
    # stderr vai para arquivo: com PIPE, um stderr cheio bloqueia o ffmpeg enquanto lemos o stdout
    with tempfile.TemporaryFile(mode="w+", encoding="utf-8", errors="replace") as err:
        proc = subprocess.Popen(cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=err, stdin=subprocess.DEVNULL,
                                text=True, encoding="utf-8", errors="replace", creationflags=_CREATE_NO_WINDOW)
        assert proc.stdout
        state = {"secs": -1.0, "changed": time.monotonic(), "stalled": False, "cancelled": False}

        def watchdog() -> None:
            while proc.poll() is None:
                if cancel and cancel():
                    state["cancelled"] = True
                    proc.kill()
                    return
                if time.monotonic() - state["changed"] > stall_seconds:
                    state["stalled"] = True
                    proc.kill()
                    return
                time.sleep(1)

        threading.Thread(target=watchdog, daemon=True).start()
        for line in proc.stdout:
            if line.startswith("out_time_us=") or line.startswith("out_time_ms="):
                try:
                    secs = int(line.split("=", 1)[1]) / 1_000_000
                except ValueError:
                    continue
                if secs > state["secs"]:
                    state["secs"], state["changed"] = secs, time.monotonic()
                on_progress(min(1.0, secs / total_seconds) if total_seconds else secs)
        code = proc.wait()
        if state["cancelled"]:
            raise FFmpegCancelled("produção cancelada durante o ffmpeg")
        if state["stalled"]:
            err.seek(0)
            raise FFmpegError(f"ffmpeg ficou {stall_seconds:.0f}s sem avançar (parado em {state['secs']:.1f}s)",
                              err.read()[-6000:])
        err.seek(0)
        stderr = err.read()
    if code != 0:
        raise FFmpegError(f"ffmpeg saiu com código {code}", stderr[-6000:])


def encode_frames(frames: Iterable[bytes], size: tuple[int, int], fps: int, out: Path) -> None:
    """Codifica quadros RGBA crus (PIL `tobytes()`) num vídeo com transparência sem perdas (FFV1 em .mkv)."""
    cmd = [ffmpeg_bin(), "-hide_banner", "-nostdin", "-y", "-loglevel", "error", "-f", "rawvideo",
           "-pix_fmt", "rgba", "-s", f"{size[0]}x{size[1]}", "-r", str(fps), "-i", "pipe:0",
           "-c:v", "ffv1", "-pix_fmt", "bgra", str(out)]
    with tempfile.TemporaryFile(mode="w+b") as err:
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=err,
                                creationflags=_CREATE_NO_WINDOW)
        assert proc.stdin
        try:
            for frame in frames:
                proc.stdin.write(frame)
        except BrokenPipeError:
            pass
        finally:
            proc.stdin.close()
        code = proc.wait()
        err.seek(0)
        stderr = err.read().decode("utf-8", "replace")
    if code != 0:
        raise FFmpegError(f"ffmpeg saiu com código {code} ao codificar overlay", stderr[-6000:])


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
