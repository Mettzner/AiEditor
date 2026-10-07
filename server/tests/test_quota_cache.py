"""Fase A: cache antes da cota, cota do YouTube por bucket com reserva atômica e single-flight das buscas."""
import os
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest
from sqlalchemy import create_engine, text
from sqlmodel import delete

from app.config import update_settings
from app.db import session_scope
from app.models import CacheLease, QuotaUsage, SearchCache
from app.pipeline.select import search
from app.providers.http import ProviderError
from app.providers.stock.base import Candidate
from app.providers.youtube import client as yt
from app.providers.youtube import quota

SERVER = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def clean():
    with session_scope() as s:
        for model in (QuotaUsage, SearchCache, CacheLease):
            s.exec(delete(model))
        s.commit()
    update_settings({"youtube": {"quota_accounting_mode": "separate_buckets"}})
    yield
    update_settings({"youtube": {"quota_accounting_mode": "separate_buckets"}})


def cand(i: str) -> Candidate:
    return Candidate(provider="youtube", external_id=i, title=f"video {i}", duration=60, width=1280, height=720,
                     page_url="", thumbnail=None)


def resp(status: int, body: dict) -> httpx.Response:
    return httpx.Response(status, json=body, request=httpx.Request("GET", "https://x"))


def used(bucket: str) -> int:
    return quota.status()["buckets"][bucket]["used"] if bucket in quota.status()["buckets"] else 0


# ---------------------------------------------------------------- A1: cache antes da cota
def test_cota_zerada_com_cache_valido_nao_busca(monkeypatch):
    search.cached_search("youtube", "video", "old mill", 50, "en", lambda: [cand("a")])
    quota.mark_exhausted(bucket="search")
    monkeypatch.setattr(yt, "request", lambda *a, **k: pytest.fail("chamou o YouTube"))
    monkeypatch.setattr(yt, "_key", lambda: "k")
    stats: dict = {}
    out = search.cached_search("youtube", "video", "old mill", 50, "en",
                               lambda: yt.search("old mill", per_page=50), stats)
    assert [c.external_id for c in out] == ["a"]
    assert stats.get("cache_hit") == 1 and not stats.get("searches")


def test_cota_esgotada_sem_cache_registra_bloqueio(monkeypatch):
    quota.mark_exhausted(bucket="search")
    monkeypatch.setattr(yt, "_key", lambda: "k")
    monkeypatch.setattr(yt, "request", lambda *a, **k: pytest.fail("chamou o YouTube"))
    stats: dict = {}
    with pytest.raises(yt.QuotaExhausted):
        search.cached_search("youtube", "video", "new query", 50, "en", lambda: yt.search("new query"), stats)
    assert stats == {"cache_miss": 1, "quota_blocked": 1}
    with session_scope() as s:  # falta de cota não vira cache de erro
        assert s.get(SearchCache, search.cache_key("youtube", "video", "new query", 50, "en")) is None


# ---------------------------------------------------------------- A2: buckets, retries, paginação, dia
def test_buckets_separados(monkeypatch):
    monkeypatch.setattr(yt, "_key", lambda: "k")
    calls = []

    def fake(method, url, **kw):
        calls.append(url.rsplit("/", 1)[-1])
        if url.endswith("search"):
            return resp(200, {"items": [{"id": {"videoId": "v1"}}], "nextPageToken": "P2"})
        return resp(200, {"items": []})

    monkeypatch.setattr(yt, "request", fake)
    out, token = yt.search_page("bridge", 50)
    assert token == "P2" and calls == ["search", "videos"]
    st = quota.status()
    assert st["buckets"]["search"]["used"] == 1 and st["buckets"]["search"]["unit"] == "calls"
    assert st["buckets"]["default"]["used"] == 1 and st["buckets"]["default"]["unit"] == "units"
    yt.search_page("bridge", 50, page_token="P2")  # nova página = nova chamada contada
    assert used("search") == 2 and used("default") == 2
    # bucket de busca esgotado não impede detalhes (outro bucket)
    quota.mark_exhausted(bucket="search")
    with pytest.raises(quota.QuotaBlocked):
        quota.reserve("search")
    assert quota.reserve("videos").bucket == "default"


def test_retentativas_sao_contadas(monkeypatch):
    monkeypatch.setattr(yt, "_key", lambda: "k")
    monkeypatch.setattr(yt.time, "sleep", lambda s: None)
    seq = [resp(503, {}), resp(503, {}), resp(200, {"items": []})]
    monkeypatch.setattr(yt, "request", lambda *a, **k: seq.pop(0))
    assert yt.search_page("x")[0] == []
    st = quota.status()["buckets"]["search"]
    assert st["used"] == 3 and st["attempts"] == 3


def test_falha_de_rede_conta_como_incerta(monkeypatch):
    monkeypatch.setattr(yt, "_key", lambda: "k")
    monkeypatch.setattr(yt.time, "sleep", lambda s: None)

    def boom(*a, **k):
        raise ProviderError("timeout")

    monkeypatch.setattr(yt, "request", boom)
    with pytest.raises(ProviderError):
        yt.search_page("x")
    st = quota.status()["buckets"]["search"]
    assert st["used"] == 3 and st["uncertain"] == 3  # 1 + 2 retentativas, todas contadas por precaução


def test_sem_chave_nao_reserva(monkeypatch):
    monkeypatch.setattr(yt, "get_secret", lambda p: None)
    with pytest.raises(yt.CredentialError):
        yt.search_page("x")
    assert used("search") == 0


def test_limite_por_minuto_nao_esgota_o_dia(monkeypatch):
    monkeypatch.setattr(yt, "_key", lambda: "k")
    body = {"error": {"errors": [{"reason": "rateLimitExceeded"}]}}
    monkeypatch.setattr(yt, "request", lambda *a, **k: resp(403, body))
    with pytest.raises(yt.QuotaExhausted) as e:
        yt.search_page("x")
    assert e.value.kind == "minute" and e.value.reactive
    st = quota.status()["buckets"]["search"]
    assert st["minute_blocked"] and not st["exhausted"]
    with pytest.raises(quota.QuotaBlocked) as b:
        quota.reserve("search")
    assert b.value.kind == "minute"
    with session_scope() as s:  # passado o minuto, volta a reservar
        row = s.get(QuotaUsage, f"youtube:search:{quota.pacific_day()}")
        row.blocked_until = time.time() - 1
        s.add(row)
        s.commit()
    assert quota.reserve("search")


def test_limite_diario_esgota_so_o_bucket_da_chamada(monkeypatch):
    monkeypatch.setattr(yt, "_key", lambda: "k")
    body = {"error": {"errors": [{"reason": "quotaExceeded"}]}}
    monkeypatch.setattr(yt, "request", lambda *a, **k: resp(403, body))
    with pytest.raises(yt.QuotaExhausted) as e:
        yt.search_page("x")
    assert e.value.kind == "daily" and e.value.bucket == "search"
    st = quota.status()
    assert st["buckets"]["search"]["exhausted"] and not st["buckets"]["default"]["exhausted"]


def test_credencial_recusada_nao_e_cota(monkeypatch):
    monkeypatch.setattr(yt, "_key", lambda: "k")
    body = {"error": {"errors": [{"reason": "keyInvalid"}]}}
    monkeypatch.setattr(yt, "request", lambda *a, **k: resp(400, body))
    with pytest.raises(yt.CredentialError):
        yt.search_page("x")
    assert not quota.status()["buckets"]["search"]["exhausted"]


def test_virada_do_dia_no_pacifico():
    today = datetime.now(quota.PACIFIC)
    quota.mark_exhausted(quota.pacific_day(today), bucket="search")
    assert not quota.can_search(quota.pacific_day(today))
    tomorrow = (today + timedelta(days=1)).replace(hour=0, minute=1)
    assert quota.can_search(quota.pacific_day(tomorrow))
    assert quota.reserve("search", quota.pacific_day(tomorrow)).day == quota.pacific_day(tomorrow)


def test_regime_legado_continua_disponivel():
    update_settings({"youtube": {"quota_accounting_mode": "legacy_units", "daily_quota": 10_000,
                                 "quota_reserve": 500}})
    r = quota.reserve("search")
    assert r.bucket == "legacy" and r.units == 100
    st = quota.status()
    assert st["mode"] == "legacy_units" and st["used"] == 100 and st["searches_left"] == (9500 - 100) // 101


def test_reserva_atomica_entre_processos():
    """4 processos disputam o bucket de busca (teto 95): nenhuma reserva passa do teto."""
    script = (
        "import sys\n"
        "from app.db import init_db\n"
        "init_db()\n"
        "from app.providers.youtube import quota\n"
        "ok = 0\n"
        "for _ in range(40):\n"
        "    try:\n"
        "        quota.reserve('search'); ok += 1\n"
        "    except quota.QuotaBlocked:\n"
        "        pass\n"
        "print(ok)\n")
    env = {**os.environ, "PYTHONPATH": str(SERVER)}
    procs = [subprocess.Popen([sys.executable, "-c", script], cwd=SERVER, env=env, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, text=True) for _ in range(4)]
    results = []
    for p in procs:
        out, err = p.communicate(timeout=120)
        assert p.returncode == 0, err
        results.append(int(out.strip().splitlines()[-1]))
    assert sum(results) == 95
    st = quota.status()["buckets"]["search"]
    assert st["used"] == 95 and st["attempts"] == 95


# ---------------------------------------------------------------- A4: single-flight e cache de erro
def test_buscas_identicas_simultaneas_fazem_um_fetch():
    calls = []
    gate = threading.Event()

    def slow():
        calls.append(1)
        gate.wait(2)
        return [cand("z")]

    results = []
    threads = [threading.Thread(target=lambda: results.append(
        search.cached_search("pexels", "video", "same query", 15, "en", slow))) for _ in range(6)]
    for t in threads:
        t.start()
    time.sleep(0.3)
    gate.set()
    for t in threads:
        t.join()
    assert len(calls) == 1 and all(r[0].external_id == "z" for r in results) and len(results) == 6


def test_single_flight_entre_processos_espera_a_concessao():
    """Com a concessão de outro processo ativa, a busca espera e usa o resultado gravado por ele."""
    key = search.cache_key("pexels", "video", "shared", 15, "en")
    with session_scope() as s:
        s.add(CacheLease(key=key, owner="outro-processo", expires_at=time.time() + 30))
        s.commit()

    def other_process_finishes():
        time.sleep(0.5)
        search._store(key, "pexels", "shared", [cand("from-other")], "ok")
        with session_scope() as s:
            s.exec(delete(CacheLease))
            s.commit()

    threading.Thread(target=other_process_finishes).start()
    out = search.cached_search("pexels", "video", "shared", 15, "en", lambda: pytest.fail("buscou em dobro"))
    assert out[0].external_id == "from-other"


def test_vazio_real_e_cacheado_e_erro_tem_ttl_curto(monkeypatch):
    assert search.cached_search("pexels", "video", "nothing", 15, "en", lambda: []) == []
    stats: dict = {}
    assert search.cached_search("pexels", "video", "nothing", 15, "en", lambda: pytest.fail("x"), stats) == []
    assert stats["cache_empty"] == 1

    def fail():
        raise ProviderError("HTTP 503")

    with pytest.raises(ProviderError):
        search.cached_search("pexels", "video", "flaky", 15, "en", fail)
    with pytest.raises(search.CachedProviderError):  # dentro do TTL curto: não martela o provedor
        search.cached_search("pexels", "video", "flaky", 15, "en", lambda: pytest.fail("x"))
    key = search.cache_key("pexels", "video", "flaky", 15, "en")
    with session_scope() as s:
        row = s.get(SearchCache, key)
        assert row.status == "error" and (row.expires_at - row.created_at).total_seconds() <= 121
        row.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        s.add(row)
        s.commit()
    assert search.cached_search("pexels", "video", "flaky", 15, "en", lambda: [cand("ok")])[0].external_id == "ok"


def test_ttl_por_provedor_e_limpeza():
    update_settings({"selection": {"search_cache_ttl_hours": {"pexels": 1}}})
    try:
        search.cached_search("pexels", "video", "short ttl", 15, "en", lambda: [cand("a")])
        key = search.cache_key("pexels", "video", "short ttl", 15, "en")
        with session_scope() as s:
            row = s.get(SearchCache, key)
            assert (row.expires_at - row.created_at).total_seconds() == pytest.approx(3600, abs=2)
            row.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
            s.add(row)
            s.commit()
        assert search.purge_expired() >= 1
        with session_scope() as s:
            assert s.get(SearchCache, key) is None
    finally:
        update_settings({"selection": {"search_cache_ttl_hours": {"pexels": 168}}})


# ---------------------------------------------------------------- migração 002
def test_migracao_preserva_saldo_legado_sem_misturar_unidades(tmp_path):
    from app.migrations import MIGRATIONS, migrate

    eng = create_engine(f"sqlite:///{tmp_path / 'legacy.db'}")
    today = quota.pacific_day()
    with eng.begin() as conn:
        conn.execute(text("CREATE TABLE schema_version (version INTEGER NOT NULL, name TEXT, applied_at TEXT)"))
        conn.execute(text("INSERT INTO schema_version (version, name) VALUES (1, 'baseline')"))
        conn.execute(text("CREATE TABLE ytquota (day VARCHAR PRIMARY KEY, used INTEGER, exhausted BOOLEAN, "
                          "updated_at DATETIME)"))
        conn.execute(text("INSERT INTO ytquota VALUES ('2026-01-02', 9999, 1, NULL), (:d, 1010, 0, NULL)"),
                     {"d": today})
        conn.execute(text("CREATE TABLE searchcache (key VARCHAR PRIMARY KEY, provider VARCHAR, query VARCHAR, "
                          "results JSON, created_at DATETIME)"))
        conn.execute(text("INSERT INTO searchcache VALUES ('k1', 'pexels', 'q', '[]', CURRENT_TIMESTAMP)"))
        conn.execute(text("CREATE TABLE quotausage (id VARCHAR PRIMARY KEY, provider VARCHAR, bucket VARCHAR, "
                          "day VARCHAR, unit VARCHAR, used INTEGER, attempts INTEGER, uncertain INTEGER, "
                          "exhausted BOOLEAN, blocked_until FLOAT, block_reason VARCHAR, origin VARCHAR, "
                          "updated_at DATETIME)"))
    assert migrate(eng) == MIGRATIONS[-1][0]
    with eng.begin() as conn:
        rows = {r[0]: r[1:] for r in conn.execute(text("SELECT id, unit, used, origin FROM quotausage"))}
        assert conn.execute(text("SELECT status FROM searchcache")).scalar() == "empty"
        assert conn.execute(text("SELECT COUNT(*) FROM ytquota")).scalar() == 2  # histórico intacto
    assert rows["youtube:legacy:2026-01-02"] == ("units", 9999, "legacy_yt_quota")
    assert rows[f"youtube:legacy:{today}"] == ("units", 1010, "legacy_yt_quota")
    assert rows[f"youtube:search:{today}"] == ("calls", 10, "migrated_estimate")  # ceil(1010/101)
    assert rows[f"youtube:default:{today}"] == ("units", 10, "migrated_estimate")
    assert "youtube:search:2026-01-02" not in rows  # dias passados não ganham estimativa
