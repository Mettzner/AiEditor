"""Cota da YouTube Data API por bucket, com reserva atômica no SQLite (vale entre API e worker).

Regime oficial conferido em 2026-10-06 (developers.google.com/youtube/v3/determine_quota_cost): search.list tem
bucket próprio (padrão 100 chamadas/dia, 1 por chamada); os demais endpoints de leitura (videos.list = 1) usam o
bucket comum de 10.000 unidades/dia. As cotas zeram à meia-noite do horário do Pacífico. Os limites efetivos do
projeto podem ser outros (aumento aprovado pelo Google): ficam editáveis em settings["youtube"]["buckets"].

Regimes (settings["youtube"]["quota_accounting_mode"]):
- "separate_buckets" (padrão): um saldo por bucket, cada um na sua unidade.
- "legacy_units": o saldo único antigo (busca=100, detalhes=1 em `daily_quota`), para quem ainda precisar dele.

Regras:
- Cada pedido realmente enviado é reservado ANTES do envio (retentativas e páginas também). Se a chamada falha sem
  resposta, a reserva fica (resultado incerto conta contra a cota); só é devolvida quando o pedido com certeza
  não saiu (ex.: chave ausente).
- A reserva é um UPDATE condicional (used + n <= teto): atômico no SQLite mesmo com vários processos.
- Recusa reativa do Google: limite diário esgota o bucket até a virada do dia; limite por minuto bloqueia o
  bucket por alguns segundos; falha de credencial não é tratada como cota (ver client.CredentialError).
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import text

from ...config import load_settings
from ...db import engine

PACIFIC = ZoneInfo("America/Los_Angeles")
PROVIDER = "youtube"
MINUTE_BLOCK_SECONDS = 60

# endpoint → bucket e custo por regime
ENDPOINTS = {
    "separate_buckets": {"search": ("search", 1), "videos": ("default", 1)},
    "legacy_units": {"search": ("legacy", 100), "videos": ("legacy", 1)},
}
BUCKET_UNIT = {"search": "calls", "default": "units", "legacy": "units"}
DEFAULT_BUCKETS = {"search": {"daily_limit": 100, "reserve": 5}, "default": {"daily_limit": 10_000, "reserve": 200}}
# compatibilidade com o regime legado (estimativa e testes antigos)
SEARCH_COST = 100
VIDEOS_COST = 1


class QuotaBlocked(Exception):
    """Sem saldo para reservar. kind: daily (esgotado/reserva atingida) ou minute (limite por minuto)."""

    def __init__(self, message: str, bucket: str, kind: str = "daily"):
        super().__init__(message)
        self.bucket = bucket
        self.kind = kind


def pacific_day(at: datetime | None = None) -> str:
    return (at or datetime.now(PACIFIC)).astimezone(PACIFIC).strftime("%Y-%m-%d")


def mode() -> str:
    m = load_settings()["youtube"].get("quota_accounting_mode", "separate_buckets")
    return m if m in ENDPOINTS else "separate_buckets"


def _bucket_limits(bucket: str) -> tuple[int, int]:
    yt = load_settings()["youtube"]
    if bucket == "legacy":
        return int(yt.get("daily_quota", 10_000)), int(yt.get("quota_reserve", 500))
    cfg = {**DEFAULT_BUCKETS[bucket], **((yt.get("buckets") or {}).get(bucket) or {})}
    return int(cfg["daily_limit"]), int(cfg["reserve"])


def _row_id(bucket: str, day: str) -> str:
    return f"{PROVIDER}:{bucket}:{day}"


def _ensure(conn, bucket: str, day: str) -> None:
    conn.execute(text("INSERT OR IGNORE INTO quotausage (id, provider, bucket, day, unit, used, attempts, uncertain, "
                      "exhausted, updated_at) VALUES (:id, :p, :b, :d, :u, 0, 0, 0, 0, CURRENT_TIMESTAMP)"),
                 {"id": _row_id(bucket, day), "p": PROVIDER, "b": bucket, "d": day, "u": BUCKET_UNIT[bucket]})


@dataclass
class Reservation:
    bucket: str
    day: str
    units: int
    released: bool = False

    def release(self) -> None:
        """Devolve a reserva: só quando o pedido com certeza NÃO foi enviado."""
        if self.released:
            return
        self.released = True
        with engine.begin() as conn:
            conn.execute(text("UPDATE quotausage SET used = MAX(0, used - :n), attempts = MAX(0, attempts - 1), "
                              "updated_at = CURRENT_TIMESTAMP WHERE id = :id"),
                         {"n": self.units, "id": _row_id(self.bucket, self.day)})

    def uncertain(self) -> None:
        """O pedido saiu e não houve resposta: continua contado, e o caso fica registrado."""
        with engine.begin() as conn:
            conn.execute(text("UPDATE quotausage SET uncertain = uncertain + 1 WHERE id = :id"),
                         {"id": _row_id(self.bucket, self.day)})


def reserve(endpoint: str, day: str | None = None) -> Reservation:
    """Reserva o custo de UM pedido ao endpoint, ou lança QuotaBlocked sem tocar no saldo."""
    bucket, cost = ENDPOINTS[mode()][endpoint]
    day = day or pacific_day()
    daily, reserve_units = _bucket_limits(bucket)
    cap = max(0, daily - reserve_units)
    with engine.begin() as conn:
        _ensure(conn, bucket, day)
        res = conn.execute(text(
            "UPDATE quotausage SET used = used + :n, attempts = attempts + 1, updated_at = CURRENT_TIMESTAMP "
            "WHERE id = :id AND exhausted = 0 AND used + :n <= :cap "
            "AND (blocked_until IS NULL OR blocked_until <= :now)"),
            {"n": cost, "id": _row_id(bucket, day), "cap": cap, "now": time.time()})
        if res.rowcount == 1:
            return Reservation(bucket, day, cost)
        row = conn.execute(text("SELECT blocked_until FROM quotausage WHERE id = :id"),
                           {"id": _row_id(bucket, day)}).first()
    if row and row[0] and row[0] > time.time():
        raise QuotaBlocked(f"Limite por minuto do YouTube ({bucket}): aguarde", bucket, "minute")
    raise QuotaBlocked(f"Cota do YouTube de hoje esgotada no bucket '{bucket}' (reserva de segurança atingida)",
                       bucket, "daily")


def bucket_for(endpoint: str) -> str:
    return ENDPOINTS[mode()][endpoint][0]


def mark_exhausted(day: str | None = None, bucket: str | None = None) -> None:
    """O Google recusou por limite diário: o bucket fica esgotado até a virada, mesmo que o contador discorde."""
    day = day or pacific_day()
    bucket = bucket or bucket_for("search")
    daily, _ = _bucket_limits(bucket)
    with engine.begin() as conn:
        _ensure(conn, bucket, day)
        conn.execute(text("UPDATE quotausage SET exhausted = 1, used = MAX(used, :daily), block_reason = 'daily', "
                          "updated_at = CURRENT_TIMESTAMP WHERE id = :id"),
                     {"daily": daily, "id": _row_id(bucket, day)})


def block_minute(bucket: str, seconds: float = MINUTE_BLOCK_SECONDS, day: str | None = None) -> None:
    day = day or pacific_day()
    with engine.begin() as conn:
        _ensure(conn, bucket, day)
        conn.execute(text("UPDATE quotausage SET blocked_until = :t, block_reason = 'minute', "
                          "updated_at = CURRENT_TIMESTAMP WHERE id = :id"),
                     {"t": time.time() + seconds, "id": _row_id(bucket, day)})


def _bucket_status(bucket: str, day: str) -> dict:
    daily, reserve_units = _bucket_limits(bucket)
    with engine.begin() as conn:
        row = conn.execute(text("SELECT used, attempts, uncertain, exhausted, blocked_until, origin FROM quotausage "
                                "WHERE id = :id"), {"id": _row_id(bucket, day)}).first()
    used, attempts, uncertain, exhausted, blocked_until, origin = row or (0, 0, 0, False, None, None)
    minute_blocked = bool(blocked_until and blocked_until > time.time())
    available = 0 if exhausted or minute_blocked else max(0, daily - reserve_units - used)
    return {"bucket": bucket, "unit": BUCKET_UNIT[bucket], "used": used, "attempts": attempts,
            "uncertain": uncertain, "exhausted": bool(exhausted), "minute_blocked": minute_blocked,
            "daily_limit": daily, "reserve": reserve_units, "available": available, "origin": origin}


def status(day: str | None = None) -> dict:
    """Situação do dia. Mantém os campos antigos (used, daily_quota, available, searches_left) para a tela."""
    day = day or pacific_day()
    m = mode()
    buckets = {b: _bucket_status(b, day) for b in dict.fromkeys(b for b, _ in ENDPOINTS[m].values())}
    s_bucket, s_cost = ENDPOINTS[m]["search"]
    v_bucket, v_cost = ENDPOINTS[m]["videos"]
    if m == "legacy_units":
        b = buckets["legacy"]
        searches_left = b["available"] // (s_cost + v_cost)
    else:
        searches_left = min(buckets[s_bucket]["available"] // s_cost, buckets[v_bucket]["available"] // v_cost)
    main = buckets[s_bucket]
    return {"day": day, "mode": m, "buckets": buckets, "searches_left": searches_left,
            "used": main["used"], "exhausted": main["exhausted"], "daily_quota": main["daily_limit"],
            "reserve": main["reserve"], "available": main["available"], "unit": main["unit"]}


def available(day: str | None = None) -> int:
    return status(day)["available"]


def can_search(day: str | None = None) -> bool:
    """Previsão (sem reservar): cabe ao menos uma busca + os detalhes dela hoje?"""
    return status(day)["searches_left"] >= 1


def search_call_units() -> int:
    """Custo de uma busca completa (search + videos) no regime atual, na unidade do bucket de busca."""
    m = mode()
    return ENDPOINTS[m]["search"][1] + (ENDPOINTS[m]["videos"][1] if m == "legacy_units" else 0)


def spend(units: int, day: str | None = None, bucket: str | None = None) -> None:
    """Registra gasto sem teto (uso administrativo/testes). O caminho normal é reserve()."""
    day = day or pacific_day()
    bucket = bucket or bucket_for("search")
    with engine.begin() as conn:
        _ensure(conn, bucket, day)
        conn.execute(text("UPDATE quotausage SET used = used + :n, updated_at = CURRENT_TIMESTAMP WHERE id = :id"),
                     {"n": units, "id": _row_id(bucket, day)})
