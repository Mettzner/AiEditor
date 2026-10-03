"""Migrações versionadas do SQLite (EMPACOTAMENTO_INSTALADOR_WINDOWS.md §3.3).

Rodam a cada inicialização (API, worker e launcher), em ordem, uma única vez cada. A versão aplicada fica na
tabela `schema_version`. Regras:
- Uma atualização do app NUNCA apaga dados: só cria tabelas, adiciona colunas e índices, ou corrige valores.
- Tabelas novas saem do `SQLModel.metadata.create_all` (idempotente); colunas novas em tabelas existentes
  precisam de uma migração aqui (o create_all não altera tabelas que já existem).
- Para mudar o esquema: acrescente uma função `_mNNN_descricao` no fim de MIGRATIONS. Nunca edite uma já publicada.
"""
from __future__ import annotations

import logging
from typing import Callable

from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine

log = logging.getLogger("aieditor.migrations")


def _columns(conn: Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(text(f"PRAGMA table_info('{table}')"))}


def add_column(conn: Connection, table: str, column: str, ddl: str) -> None:
    """Adiciona a coluna se ainda não existir (ex.: add_column(c, "production", "notes", "TEXT"))."""
    if column not in _columns(conn, table):
        conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}"))


def _m001_baseline(conn: Connection) -> None:
    """Esquema da versão 1.0.0: todas as tabelas já foram criadas pelo create_all."""


MIGRATIONS: list[tuple[int, str, Callable[[Connection], None]]] = [
    (1, "baseline 1.0.0", _m001_baseline),
]


def current_version(engine: Engine) -> int:
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL, name TEXT, "
                          "applied_at TEXT DEFAULT CURRENT_TIMESTAMP)"))
        return int(conn.execute(text("SELECT COALESCE(MAX(version), 0) FROM schema_version")).scalar() or 0)


def migrate(engine: Engine) -> int:
    """Aplica as migrações pendentes, cada uma na sua transação. Devolve a versão final."""
    version = current_version(engine)
    for number, name, fn in MIGRATIONS:
        if number <= version:
            continue
        with engine.begin() as conn:  # BEGIN IMMEDIATE implícito no 1º write: API e worker não aplicam em dobro
            again = int(conn.execute(text("SELECT COALESCE(MAX(version), 0) FROM schema_version")).scalar() or 0)
            if number <= again:
                continue
            fn(conn)
            conn.execute(text("INSERT INTO schema_version (version, name) VALUES (:v, :n)"), {"v": number, "n": name})
        log.info("migração %03d aplicada: %s", number, name)
        version = number
    return version
