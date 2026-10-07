"""Teto de gasto por produção: o Claude na visão para antes de estourar o orçamento, e cada chamada paga entra
no custo da produção (antes só o total do Gemini era registrado, no fim da etapa)."""
from types import SimpleNamespace

import pytest
from sqlmodel import select

from app import budget as budget_mod
from app.budget import VisionBudget
from app.providers.llm import vision


def test_libera_ate_o_teto_e_depois_para(monkeypatch):
    spent = {"v": 0.0}
    monkeypatch.setattr(budget_mod, "production_cost", lambda _pid: spent["v"])
    b = VisionBudget(1, limit=1.0, reserve=0.15)
    allowed = 0
    while b.allow_paid():
        spent["v"] += 0.1  # o registro da chamada soma no custo da produção
        b.settle(0.1)
        allowed += 1
    assert allowed == 8  # com 0,80 gasto: 0,80 + 0,10 da próxima + 0,15 de reserva > 1,00
    assert not b.allow_paid()  # uma vez esgotado, não volta


def test_teto_zero_nao_limita():
    assert all(VisionBudget(1, limit=0).allow_paid() for _ in range(50))


def test_visao_sem_orcamento_nao_chama_o_claude(monkeypatch):
    monkeypatch.setattr(vision, "gemini_usable", lambda: False)
    monkeypatch.setattr(vision, "claude_available", lambda: True)
    called = []
    monkeypatch.setattr("app.providers.llm.base.call_llm", lambda *a, **k: called.append(1))
    exhausted = SimpleNamespace(allow_paid=lambda: False, settle=lambda c: None)
    with pytest.raises(vision.VisionBudgetExceeded):
        vision.rate_sheet(b"jpg", "prompt", "gemini", schema=object, budget=exhausted)
    assert not called


def test_chamada_do_claude_e_registrada_no_custo(monkeypatch):
    monkeypatch.setattr(vision, "gemini_usable", lambda: False)
    monkeypatch.setattr(vision, "claude_available", lambda: True)
    usage = SimpleNamespace(cost=0.006)
    monkeypatch.setattr("app.providers.llm.base.call_llm", lambda *a, **k: ("nota", usage))
    recorded, settled = [], []
    b = SimpleNamespace(allow_paid=lambda: True, settle=settled.append)
    parsed, cost, provider = vision.rate_sheet(b"jpg", "prompt", "gemini", schema=object, budget=b,
                                               record=recorded.append)
    assert (parsed, cost, provider) == ("nota", 0.006, "claude")
    assert recorded == [usage] and settled == [0.006]


def test_busca_gravada_ao_mesmo_tempo_por_duas_cenas_nao_derruba(monkeypatch):
    from sqlalchemy.exc import IntegrityError

    from app.pipeline.select import search

    class Session:
        def get(self, *a):
            return None

        def add(self, row):
            pass

        def commit(self):
            raise IntegrityError("INSERT", {}, Exception("UNIQUE constraint failed: searchcache.key"))

        def rollback(self):
            self.rolled = True

    from contextlib import contextmanager

    s = Session()
    monkeypatch.setattr(search, "session_scope", contextmanager(lambda: (yield s)))
    assert search.cached_search("pixabay", "video", "q", 10, "en", lambda: []) == []
    assert s.rolled


def _chain(monkeypatch, gemini_ok=False, openai_ok=True):
    from app.providers.llm import gemini, openai_vision

    monkeypatch.setattr(vision, "_mode", lambda: "auto")
    monkeypatch.setattr(vision, "gemini_usable", lambda: gemini_ok)
    monkeypatch.setattr(vision, "openai_usable", lambda: openai_ok)
    monkeypatch.setattr(vision, "claude_available", lambda: True)
    return gemini, openai_vision


def test_sem_gemini_a_openai_avalia_antes_do_claude(monkeypatch):
    _, openai_vision = _chain(monkeypatch)
    monkeypatch.setattr(openai_vision, "rate_sheet", lambda *a: ("nota", SimpleNamespace(cost=0.002)))
    monkeypatch.setattr("app.providers.llm.base.call_llm", lambda *a, **k: pytest.fail("Claude não devia ser chamado"))
    recorded = []
    assert vision.rate_sheet(b"jpg", "p", "g", schema=object, record=recorded.append)[2] == "openai"
    assert recorded[0].cost == 0.002


def test_openai_sem_credito_passa_para_o_claude(monkeypatch):
    _, openai_vision = _chain(monkeypatch)

    def broke(*a):
        raise openai_vision.OpenAIUnavailable("Conta da OpenAI sem crédito")

    monkeypatch.setattr(openai_vision, "rate_sheet", broke)
    monkeypatch.setattr("app.providers.llm.base.call_llm", lambda *a, **k: ("nota", SimpleNamespace(cost=0.006)))
    assert vision.rate_sheet(b"jpg", "p", "g", schema=object)[2] == "claude"


def test_teto_vale_para_openai_e_claude_juntos(monkeypatch):
    _, openai_vision = _chain(monkeypatch)
    monkeypatch.setattr(openai_vision, "rate_sheet", lambda *a: pytest.fail("teto estourado não chama a OpenAI"))
    exhausted = SimpleNamespace(allow_paid=lambda: False, settle=lambda c: None)
    with pytest.raises(vision.VisionBudgetExceeded):
        vision.rate_sheet(b"jpg", "p", "g", schema=object, budget=exhausted)


def test_resposta_da_openai_vira_avaliacao_e_custo(monkeypatch):
    from pydantic import BaseModel

    from app.providers.llm import openai_vision

    class Nota(BaseModel):
        score: float

    sent = {}

    def fake_request(method, url, **kw):
        sent.update(kw["json"])
        return SimpleNamespace(status_code=200, text="", json=lambda: {
            "choices": [{"message": {"content": '{"score": 8.5}'}}],
            "usage": {"prompt_tokens": 2000, "completion_tokens": 500}})

    monkeypatch.setattr(openai_vision, "request", fake_request)
    monkeypatch.setattr(openai_vision, "get_secret", lambda _p: "sk-teste")
    parsed, usage = openai_vision.rate_sheet(b"jpg", "avalie", Nota, "sistema")
    assert parsed.score == 8.5
    assert sent["model"] == "gpt-4.1-mini"
    assert sent["messages"][1]["content"][1]["image_url"]["url"].startswith("data:image/jpeg;base64,")
    assert abs(usage.cost - (2000 * 0.40 + 500 * 1.60) / 1e6) < 1e-9


# ---------------------------------------------------------------- Ledger: reserva antes, reconciliação depois (A6)
def _production(cost: float = 0.0) -> int:
    from app.db import session_scope
    from app.models import Production

    with session_scope() as s:
        p = Production(title="t", script="x", config={}, status="running", cost_actual=cost)
        s.add(p)
        s.commit()
        s.refresh(p)
        return p.id


class _Ctx:
    def __init__(self, pid):
        self.production_id = pid
        self.recorded = []

    def record_llm(self, usage, step=None):
        from app.db import session_scope
        from app.models import Production

        self.recorded.append(usage)
        with session_scope() as s:
            p = s.get(Production, self.production_id)
            p.cost_actual += usage.cost
            s.add(p)
            s.commit()


def test_teto_impede_despesa_sem_chamar():
    from app.budget import BudgetExceeded, Ledger, paid_llm

    pid = _production(cost=0.95)
    Ledger.for_production(pid, limit=1.0)
    ctx, called = _Ctx(pid), []
    with pytest.raises(BudgetExceeded):
        paid_llm(ctx, "plan", lambda: called.append(1), estimate=0.10)
    assert not called and not ctx.recorded


def test_reserva_aberta_conta_no_teto_e_reconcilia_com_o_real():
    from app.budget import BudgetExceeded, Ledger, ledger_summary
    from app.db import session_scope
    from app.models import CostEntry

    pid = _production()
    led = Ledger.for_production(pid, limit=1.0)
    first = led.reserve("bible", 0.7)
    with pytest.raises(BudgetExceeded):  # 0,7 reservado + 0,4 > 1,0 mesmo sem custo registrado ainda
        led.reserve("plan", 0.4)
    first.settle(0.05)  # o real foi bem menor: libera a diferença
    second = led.reserve("plan", 0.4)
    second.settle(None)  # não aconteceu
    with session_scope() as s:
        rows = {r.id: r for r in s.exec(select(CostEntry).where(CostEntry.production_id == pid))}
    assert rows[first.entry_id].status == "settled" and rows[first.entry_id].actual_usd == 0.05
    assert rows[second.entry_id].status == "released"
    assert ledger_summary(pid)["bible"]["settled"] == 1


def test_falha_cobrada_entra_no_custo_e_fecha_a_reserva():
    from app.budget import Ledger, paid_llm
    from app.providers.llm.base import LLMUsage

    pid = _production()
    Ledger.for_production(pid, limit=5.0)
    ctx = _Ctx(pid)

    class Cut(Exception):
        usage = LLMUsage(cost=0.02, model="claude-sonnet-5-5")

    def boom():
        raise Cut()

    with pytest.raises(Cut):
        paid_llm(ctx, "plan", boom, estimate=0.3)
    assert [u.cost for u in ctx.recorded] == [0.02]
    assert not Ledger.for_production(pid).open


def test_reservas_orfas_sao_liberadas_na_retomada():
    from app.budget import Ledger, release_stale

    pid = _production()
    Ledger.for_production(pid, limit=1.0).reserve("bible", 0.9)  # processo "morreu" com a reserva aberta
    assert release_stale(pid) == 1
    assert Ledger.for_production(pid, limit=1.0).reserve("bible", 0.9)


def test_preco_desconhecido_fica_explicito():
    from app.providers.llm.anthropic import canonical_model, price_info

    prices, known = price_info("claude-modelo-novo-9")
    assert not known and prices == max(price_info(m)[0] for m in ("claude-opus-5-5", "claude-sonnet-5-5"))
    assert price_info("claude-haiku-4-5")[1]
    assert canonical_model("claude-haiku-4-5-20251001", "claude-haiku-4-5") == "claude-haiku-4-5"


def test_estimativa_tem_faixa_tarefas_e_hipoteses():
    from app.estimate import estimate
    from app.models import Preset, ProductionConfig

    cfg = ProductionConfig(**Preset(real_pct=80).model_dump(), title="t", channel_name="c")
    cost = estimate(cfg, "palavra " * 3000)["cost"]
    assert cost["range"]["low"] <= cost["total"] <= cost["range"]["high"]
    assert {"bible", "plan", "rewrite", "overlay"} <= set(cost["by_task"])
    assert cost["by_task"]["plan"]["calls"][0] >= 4  # 45 unidades por janela
    assert cost["assumptions"] and cost["currency"] == "USD" and cost["prices_as_of"]
