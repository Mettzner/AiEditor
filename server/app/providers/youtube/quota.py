"""Cota diária da YouTube Data API (zera à meia-noite do Pacífico).

Custos: search.list = 100 unidades, videos.list = 1. O gasto é registrado a cada chamada, mesmo
quando ela falha. As últimas `quota_reserve` unidades nunca são usadas.
"""
from __future__ import annotations

import threading
from datetime import datetime
from zoneinfo import ZoneInfo

from ...config import load_settings
from ...db import session_scope
from ...models import YtQuota, now

PACIFIC = ZoneInfo("America/Los_Angeles")
SEARCH_COST = 100
VIDEOS_COST = 1
_lock = threading.Lock()


def pacific_day(at: datetime | None = None) -> str:
    return (at or datetime.now(PACIFIC)).astimezone(PACIFIC).strftime("%Y-%m-%d")


def _limits() -> tuple[int, int]:
    yt = load_settings()["youtube"]
    return int(yt.get("daily_quota", 10_000)), int(yt.get("quota_reserve", 500))


def status(day: str | None = None) -> dict:
    day = day or pacific_day()
    daily, reserve = _limits()
    with session_scope() as s:
        row = s.get(YtQuota, day)
        used, exhausted = (row.used, row.exhausted) if row else (0, False)
    available = 0 if exhausted else max(0, daily - reserve - used)
    return {"day": day, "used": used, "exhausted": exhausted, "daily_quota": daily, "reserve": reserve,
            "available": available, "searches_left": available // (SEARCH_COST + VIDEOS_COST)}


def available(day: str | None = None) -> int:
    return status(day)["available"]


def can_search(day: str | None = None) -> bool:
    return available(day) >= SEARCH_COST + VIDEOS_COST


def spend(units: int, day: str | None = None) -> None:
    day = day or pacific_day()
    with _lock, session_scope() as s:
        row = s.get(YtQuota, day) or YtQuota(day=day)
        row.used += units
        row.updated_at = now()
        s.add(row)
        s.commit()


def mark_exhausted(day: str | None = None) -> None:
    """O Google recusou por cota: o dia fica esgotado mesmo que o contador local discorde."""
    day = day or pacific_day()
    daily, _ = _limits()
    with _lock, session_scope() as s:
        row = s.get(YtQuota, day) or YtQuota(day=day)
        row.used = max(row.used, daily)
        row.exhausted = True
        row.updated_at = now()
        s.add(row)
        s.commit()
