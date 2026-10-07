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


def _tables(conn: Connection) -> set[str]:
    return {r[0] for r in conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))}


def add_column(conn: Connection, table: str, column: str, ddl: str) -> None:
    """Adiciona a coluna se ainda não existir (ex.: add_column(c, "production", "notes", "TEXT"))."""
    if column not in _columns(conn, table):
        conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}"))


def _m001_baseline(conn: Connection) -> None:
    """Esquema da versão 1.0.0: todas as tabelas já foram criadas pelo create_all."""


def _m002_quota_buckets_and_cache_status(conn: Connection) -> None:
    """Cota do YouTube por bucket (search.list tem bucket próprio, em chamadas) e cache de busca com status/TTL.

    O saldo único legado (yt_quota, em unidades: busca=100, detalhes=1) é copiado para o bucket "legacy" na mesma
    unidade, sem conversão. Só o dia corrente ganha uma estimativa conservadora nos buckets novos (cada busca do
    regime antigo = 1 search.list + 1 videos.list), marcada como origin="migrated_estimate".
    """
    tables = _tables(conn)
    if "searchcache" in tables:
        add_column(conn, "searchcache", "status", "VARCHAR DEFAULT 'ok' NOT NULL")
        add_column(conn, "searchcache", "expires_at", "DATETIME")
        add_column(conn, "searchcache", "error", "VARCHAR")
        add_column(conn, "searchcache", "next_page_token", "VARCHAR")
        conn.execute(text("UPDATE searchcache SET status = CASE WHEN results = '[]' THEN 'empty' ELSE 'ok' END"))
    if "ytquota" not in tables or "quotausage" not in tables:  # ytquota: nome que o SQLModel deu à YtQuota
        return
    legacy_table = "ytquota"
    from .providers.youtube.quota import pacific_day

    today = pacific_day()
    rows = list(conn.execute(text(f"SELECT day, used, exhausted FROM {legacy_table}")))
    for day, used, exhausted in rows:
        conn.execute(text("INSERT OR IGNORE INTO quotausage (id, provider, bucket, day, unit, used, attempts, uncertain, "
                          "exhausted, origin, updated_at) VALUES (:id, 'youtube', 'legacy', :day, 'units', :used, 0, 0, "
                          ":ex, 'legacy_yt_quota', CURRENT_TIMESTAMP)"),
                     {"id": f"youtube:legacy:{day}", "day": day, "used": int(used or 0), "ex": bool(exhausted)})
        if day != today or not used:
            continue
        searches = -(-int(used) // 101)  # arredonda para cima: incerteza conta contra a cota
        for bucket, unit in (("search", "calls"), ("default", "units")):
            conn.execute(text("INSERT OR IGNORE INTO quotausage (id, provider, bucket, day, unit, used, attempts, "
                              "uncertain, exhausted, origin, updated_at) VALUES (:id, 'youtube', :b, :day, :u, :n, 0, "
                              "0, :ex, 'migrated_estimate', CURRENT_TIMESTAMP)"),
                         {"id": f"youtube:{bucket}:{day}", "b": bucket, "day": day, "u": unit, "n": searches,
                          "ex": bool(exhausted) and bucket == "search"})


def _m003_price_metadata(conn: Connection) -> None:
    """Preço com moeda, data de conferência e regime. Preços antigos ficam sem data (= não conferidos)."""
    if "providerprice" not in _tables(conn):
        return
    add_column(conn, "providerprice", "currency", "VARCHAR DEFAULT 'USD' NOT NULL")
    add_column(conn, "providerprice", "as_of", "VARCHAR")
    add_column(conn, "providerprice", "regime", "VARCHAR DEFAULT 'standard' NOT NULL")
    conn.execute(text("UPDATE providerprice SET regime = 'plan' WHERE note = 'incluso no plano'"))


MIGRATIONS: list[tuple[int, str, Callable[[Connection], None]]] = [
    (1, "baseline 1.0.0", _m001_baseline),
    (2, "cota do YouTube por bucket e status do cache de busca", _m002_quota_buckets_and_cache_status),
    (3, "preços com moeda, data e regime", _m003_price_metadata),
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
