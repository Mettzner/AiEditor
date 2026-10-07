from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from sqlalchemy import event
from sqlmodel import Session, SQLModel, create_engine

from .config import DB_PATH

engine = create_engine(
    f"sqlite:///{DB_PATH}",
    connect_args={"check_same_thread": False, "timeout": 30},
)


@event.listens_for(engine, "connect")
def _sqlite_pragmas(dbapi_conn, _):  # API e worker são processos distintos → WAL
    cur = dbapi_conn.cursor()
    cur.execute("PRAGMA journal_mode=WAL")
    cur.execute("PRAGMA busy_timeout=30000")
    cur.execute("PRAGMA foreign_keys=ON")
    cur.close()


def init_db() -> None:
    from . import models  # noqa: F401  (registra as tabelas)

    from .migrations import migrate

    backup_before_migrations()
    SQLModel.metadata.create_all(engine)
    migrate(engine)  # migrações versionadas (schema_version); nunca apagam dados
    models.seed_defaults()


BACKUPS_KEPT = 3


def backup_before_migrations() -> Path | None:
    """Com migração pendente num banco que já tem dados, copia o banco antes (API de backup do SQLite, consistente
    mesmo com o outro processo aberto). Guarda as 3 cópias mais recentes ao lado do banco."""
    import sqlite3
    from datetime import datetime

    from .migrations import MIGRATIONS, current_version

    if not DB_PATH.exists() or DB_PATH.stat().st_size == 0:
        return None
    version = current_version(engine)
    if version >= MIGRATIONS[-1][0]:
        return None
    dest = DB_PATH.with_name(f"{DB_PATH.stem}.v{version}.{datetime.now():%Y%m%d-%H%M%S}.bak")
    src = sqlite3.connect(DB_PATH)
    try:
        out = sqlite3.connect(dest)
        with out:
            src.backup(out)
        out.close()
    finally:
        src.close()
    for old in sorted(DB_PATH.parent.glob(f"{DB_PATH.stem}.v*.bak"))[:-BACKUPS_KEPT]:
        old.unlink(missing_ok=True)
    return dest


@contextmanager
def session_scope() -> Iterator[Session]:
    with Session(engine) as s:
        yield s


def get_session() -> Iterator[Session]:
    with Session(engine) as s:
        yield s
