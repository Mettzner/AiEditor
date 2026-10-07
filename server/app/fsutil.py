"""Gravação atômica com temporário exclusivo (nunca dois escritores no mesmo .tmp/.part)."""
from __future__ import annotations

import os
import time
import uuid
from pathlib import Path


def unique_temp(dest: Path, tag: str = "tmp") -> Path:
    """Temporário ao lado do destino (mesmo disco: o replace é atômico), único por escritor."""
    return dest.with_name(f"{dest.name}.{os.getpid()}.{uuid.uuid4().hex[:8]}.{tag}")


def replace_with_retry(tmp: Path, dest: Path, tries: int = 8) -> None:
    """No Windows o replace falha (PermissionError) enquanto outro processo lê o destino: tenta de novo."""
    for i in range(tries):
        try:
            tmp.replace(dest)
            return
        except PermissionError:
            if i == tries - 1:
                raise
            time.sleep(0.02 * (i + 1))


def atomic_write_bytes(path: Path, data: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = unique_temp(path)
    try:
        tmp.write_bytes(data)
        replace_with_retry(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)
    return path


def atomic_write_text(path: Path, data: str, encoding: str = "utf-8") -> Path:
    return atomic_write_bytes(path, data.encode(encoding))
