"""Exclusão definitiva: produção ou canal excluído não deixa nada para trás (registros, pasta do job, ativos
marcados como usados). Sem sobras, um id reaproveitado pelo SQLite nunca herda arquivos de outra produção."""
from __future__ import annotations

import logging
import os
import shutil
import stat
from pathlib import Path

from sqlmodel import Session, delete, select

from .config import DATA_DIR, JOBS_DIR
from .models import Channel, Issue, Production, ProductionStep, UsedAsset

log = logging.getLogger("aieditor.purge")

ACTIVE = ("running", "cancel_requested")


class PurgeError(Exception):
    pass


def _rmtree(path: Path) -> None:
    def retry_writable(func, p, _exc):  # arquivo somente leitura: libera e tenta de novo
        os.chmod(p, stat.S_IWRITE)
        func(p)

    if path.exists():
        shutil.rmtree(path, onexc=retry_writable)


def remove_job_files(production_id: int) -> None:
    """Apaga a pasta do job. Falha (arquivo aberto em outro programa) vira PurgeError antes de mexer no banco."""
    try:
        _rmtree(JOBS_DIR / str(production_id))
    except OSError as e:
        raise PurgeError(f"Não foi possível apagar os arquivos da produção {production_id}: feche o vídeo ou a "
                         f"pasta se estiverem abertos e tente de novo ({e.strerror or e})") from e


def purge_production(s: Session, p: Production) -> None:
    if p.status in ACTIVE:
        raise PurgeError("Cancele a produção antes de excluir")
    remove_job_files(p.id)  # type: ignore[arg-type]
    s.exec(delete(Issue).where(Issue.production_id == p.id))  # type: ignore[arg-type]
    s.exec(delete(ProductionStep).where(ProductionStep.production_id == p.id))  # type: ignore[arg-type]
    s.exec(delete(UsedAsset).where(UsedAsset.production_id == p.id))  # type: ignore[arg-type]
    s.delete(p)


def purge_channel(s: Session, c: Channel) -> int:
    """Exclui o canal com todas as produções dele. Devolve quantas produções foram apagadas."""
    productions = list(s.exec(select(Production).where(Production.channel_id == c.id)))
    if any(p.status in ACTIVE for p in productions):
        raise PurgeError("Há produção deste canal em andamento; cancele antes de excluir o canal")
    for p in productions:
        purge_production(s, p)
    s.exec(delete(UsedAsset).where(UsedAsset.channel_id == c.id))  # type: ignore[arg-type]
    for ref in (DATA_DIR / "references").glob(f"channel_{c.id}.*"):
        ref.unlink(missing_ok=True)
    s.delete(c)
    return len(productions)


def purge_orphans(s: Session) -> None:
    """Na inicialização: apaga pastas de job sem produção no banco (exclusões antigas, que só apagavam o registro),
    a pasta jobs/_antigos e registros filhos de produções que não existem mais."""
    ids = set(s.exec(select(Production.id)))
    for d in JOBS_DIR.iterdir() if JOBS_DIR.is_dir() else []:
        orphan = d.name.isdigit() and int(d.name) not in ids or d.name == "_antigos"
        if d.is_dir() and orphan:
            try:
                _rmtree(d)
                log.info("pasta órfã apagada: %s", d)
            except OSError as e:
                log.warning("pasta órfã não apagada (%s): %s", d, e)
    for model in (Issue, ProductionStep, UsedAsset):
        s.exec(delete(model).where(model.production_id.not_in(ids)))  # type: ignore[attr-defined]
    s.commit()
