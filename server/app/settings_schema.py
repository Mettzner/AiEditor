"""Validação tipada das configurações gravadas pela tela (PUT /api/settings) e dos preços.

Só os campos que o pipeline lê como número/enum são checados (o resto passa como está, para não quebrar
configurações antigas nem campos novos da tela). Erro → HTTP 422 com a lista de problemas.
"""
from __future__ import annotations

import math
from typing import Any, Callable

Check = Callable[[Any], str | None]


def _num(lo: float | None = None, hi: float | None = None, integer: bool = False) -> Check:
    def check(v: Any) -> str | None:
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
            return "deve ser um número"
        if integer and int(v) != v:
            return "deve ser inteiro"
        if lo is not None and v < lo:
            return f"deve ser ≥ {lo}"
        if hi is not None and v > hi:
            return f"deve ser ≤ {hi}"
        return None
    return check


def _enum(*values: str) -> Check:
    return lambda v: None if v in values else f"deve ser um de: {', '.join(values)}"


def _bool(v: Any) -> str | None:
    return None if isinstance(v, bool) else "deve ser verdadeiro/falso"


RULES: dict[tuple[str, ...], Check] = {
    ("youtube", "enabled"): _bool,
    ("youtube", "first"): _bool,
    ("youtube", "quota_accounting_mode"): _enum("separate_buckets", "legacy_units"),
    ("youtube", "daily_quota"): _num(0, 10_000_000, True),
    ("youtube", "quota_reserve"): _num(0, 10_000_000, True),
    ("youtube", "max_duration"): _num(10, 86_400),
    ("youtube", "buckets", "search", "daily_limit"): _num(0, 10_000_000, True),
    ("youtube", "buckets", "search", "reserve"): _num(0, 10_000_000, True),
    ("youtube", "buckets", "default", "daily_limit"): _num(0, 10_000_000, True),
    ("youtube", "buckets", "default", "reserve"): _num(0, 10_000_000, True),
    ("selection", "youtube_results_per_query"): _num(1, 50, True),
    ("selection", "results_per_query"): _num(1, 80, True),
    ("selection", "stock_queries_per_scene"): _num(1, 10, True),
    ("selection", "youtube_queries_per_scene"): _num(1, 5, True),
    ("selection", "max_youtube_pages_per_group"): _num(1, 10, True),
    ("selection", "prerank_keep"): _num(1, 20, True),
    ("selection", "frames_per_candidate"): _num(1, 6, True),
    ("selection", "min_score"): _num(0, 10),
    ("selection", "accept_score"): _num(0, 10),
    ("selection", "text_min_score"): _num(0, 10),
    ("selection", "parallel_scenes"): _num(1, 16, True),
    ("selection", "parallel_downloads"): _num(1, 16, True),
    ("selection", "search_error_ttl_seconds"): _num(0, 86_400),
    ("selection", "validation_policy"): _enum("strict_for_exact_identity", "lenient"),
    ("selection", "verify_final_segment"): _bool,
    ("selection", "allow_youtube_fallback_from_stock"): _bool,
    ("budget", "max_usd_per_production"): _num(0, 10_000),
    ("upload", "max_mb"): _num(1, 20_000, True),
    ("upload", "max_minutes"): _num(1, 24 * 60),
    ("worker", "max_parallel_productions"): _num(1, 8, True),
    ("render", "crf"): _num(0, 51, True),
    ("render", "parallel_scenes"): _num(1, 16, True),
    ("render", "mode"): _enum("quality", "fast"),
    ("vision", "provider"): _enum("auto", "gemini", "openai", "claude"),
}


def _get(d: dict, path: tuple[str, ...]) -> tuple[bool, Any]:
    cur: Any = d
    for k in path:
        if not isinstance(cur, dict) or k not in cur:
            return False, None
        cur = cur[k]
    return True, cur


def validate_settings(settings: Any) -> list[str]:
    if not isinstance(settings, dict):
        return ["settings deve ser um objeto"]
    errors = []
    for path, check in RULES.items():
        present, value = _get(settings, path)
        if present and (msg := check(value)):
            errors.append(f"{'.'.join(path)} {msg}")
    ttl = (settings.get("selection") or {}).get("search_cache_ttl_hours") if isinstance(settings.get("selection"), dict) else None
    if ttl is not None:
        if not isinstance(ttl, dict):
            errors.append("selection.search_cache_ttl_hours deve ser um objeto {provedor: horas}")
        else:
            for k, v in ttl.items():
                lo = 24 if k == "pixabay" else 0  # o Pixabay exige cache de pelo menos 24 h
                if msg := _num(lo, 24 * 365)(v):
                    errors.append(f"selection.search_cache_ttl_hours.{k} {msg}")
    return errors


def validate_price(price: Any, currency: Any = "USD", as_of: Any = None) -> list[str]:
    errors = []
    if msg := _num(0, 1_000_000)(price):
        errors.append(f"price {msg}")
    if not (isinstance(currency, str) and len(currency) == 3 and currency.isalpha()):
        errors.append("currency deve ter 3 letras (ex.: USD)")
    if as_of is not None and not (isinstance(as_of, str) and len(as_of) == 10 and as_of[4] == "-" and as_of[7] == "-"):
        errors.append("as_of deve ser uma data AAAA-MM-DD")
    return errors
