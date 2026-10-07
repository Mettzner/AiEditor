"""Worker: consome a fila de produções.  Uso: python -m app.worker"""
from __future__ import annotations

import logging
import threading
import time
from datetime import timedelta
from concurrent.futures import Future, ThreadPoolExecutor

from sqlmodel import select, update

from ..config import load_settings
from ..db import init_db, session_scope
from ..models import Production, now
from .runner import run_production

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("aieditor.worker")

# Uma produção por vez na etapa de render (§10); as demais etapas podem rodar em paralelo.
RENDER_LOCK = threading.Semaphore(1)


def _recover() -> None:
    """Produções interrompidas por queda do worker voltam para a fila e retomam da etapa pendente."""
    with session_scope() as s:
        s.exec(update(Production).where(Production.status == "running").values(status="queued", updated_at=now()))
        s.exec(update(Production).where(Production.status == "cancel_requested")
               .values(status="cancelled", updated_at=now()))
        # a API caiu entre receber o áudio e liberar a produção: nunca roda com áudio incerto
        stale = now() - timedelta(minutes=10)
        s.exec(update(Production).where(Production.status == "preparing", Production.updated_at < stale)
               .values(status="failed", error="Upload do áudio não terminou; crie a produção de novo",
                       step_label="Falhou", updated_at=now()))
        s.commit()


def _wake_waiting() -> None:
    """Produções aguardando lote voltam para a fila quando chega a hora de conferir (sem laço ocupando o worker)."""
    with session_scope() as s:
        s.exec(update(Production).where(Production.status == "waiting_provider", Production.resume_at <= now())
               .values(status="queued", step_label="Conferindo lote do provedor", updated_at=now()))
        s.commit()


def _claim_next() -> int | None:
    with session_scope() as s:
        candidate = s.exec(select(Production.id).where(Production.status == "queued")
                           .order_by(Production.created_at)).first()
        if candidate is None:
            return None
        res = s.exec(update(Production).where(Production.id == candidate, Production.status == "queued")
                     .values(status="running", step_label="Iniciando", updated_at=now()))
        s.commit()
        return candidate if res.rowcount else None


def main() -> None:
    init_db()
    _recover()
    try:  # dados de API vencidos não ficam guardados (TTL por provedor)
        from ..pipeline.select.search import purge_expired

        log.info("cache de busca: %d resultado(s) vencido(s) removido(s)", purge_expired())
    except Exception as e:  # noqa: BLE001
        log.warning("limpeza do cache de busca falhou: %s", e)
    max_parallel = int(load_settings()["worker"]["max_parallel_productions"])
    pool = ThreadPoolExecutor(max_workers=max_parallel, thread_name_prefix="prod")
    active: dict[int, Future] = {}
    log.info("worker pronto (até %s produções em paralelo)", max_parallel)
    while True:
        for pid, fut in list(active.items()):
            if fut.done():
                active.pop(pid)
                if fut.exception():
                    log.exception("produção %s terminou com exceção", pid, exc_info=fut.exception())
        _wake_waiting()
        while len(active) < max_parallel:
            pid = _claim_next()
            if pid is None:
                break
            log.info("iniciando produção %s", pid)
            active[pid] = pool.submit(run_production, pid, RENDER_LOCK)
        time.sleep(1.5)


if __name__ == "__main__":
    main()
