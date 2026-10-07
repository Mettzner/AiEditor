"""Single-flight: trabalhos idênticos ao mesmo tempo (mesma chave) rodam UMA vez; os demais esperam o resultado.

Dentro do processo, um lock por chave. Entre processos (API e worker, ou dois workers), uma concessão na tabela
cache_lease com validade: quem não a obtém espera o resultado aparecer (lookup) ou a concessão vencer. O
resultado em si fica onde o chamador o guarda (cache de busca, cache de visão...).
"""
from __future__ import annotations

import os
import threading
import time
import uuid
from typing import Callable, TypeVar

from sqlalchemy import text

from .db import engine

T = TypeVar("T")
OWNER = f"{os.getpid()}:{uuid.uuid4().hex[:8]}"
LEASE_SECONDS = 90.0
WAIT_POLL = 0.25

_guard = threading.Lock()
_locks: dict[str, threading.Lock] = {}


def _lock(key: str) -> threading.Lock:
    with _guard:
        lock = _locks.get(key)
        if lock is None:
            lock = _locks[key] = threading.Lock()
        return lock


def acquire(key: str, seconds: float = LEASE_SECONDS) -> bool:
    t = time.time()
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM cachelease WHERE key = :k AND expires_at <= :t"), {"k": key, "t": t})
        res = conn.execute(text("INSERT OR IGNORE INTO cachelease (key, owner, expires_at) VALUES (:k, :o, :e)"),
                           {"k": key, "o": OWNER, "e": t + seconds})
        return res.rowcount == 1


def release(key: str) -> None:
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM cachelease WHERE key = :k AND owner = :o"), {"k": key, "o": OWNER})


def purge() -> None:
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM cachelease WHERE expires_at <= :t"), {"t": time.time()})


def run(key: str, lookup: Callable[[], T | None], compute: Callable[[], T], seconds: float = LEASE_SECONDS) -> T:
    """lookup() devolve o resultado já pronto (ou None); compute() faz o trabalho e o guarda onde lookup lê."""
    hit = lookup()
    if hit is not None:
        return hit
    with _lock(key):
        hit = lookup()
        if hit is not None:
            return hit
        while not acquire(key, seconds):  # outro processo está calculando: espera o resultado ou a concessão vencer
            time.sleep(WAIT_POLL)
            hit = lookup()
            if hit is not None:
                return hit
        try:
            hit = lookup()  # pode ter chegado entre a espera e a concessão
            if hit is not None:
                return hit
            return compute()
        finally:
            release(key)
