"""Teto de gasto por produção: o Claude na visão para antes de estourar o orçamento, e cada chamada paga entra
no custo da produção (antes só o total do Gemini era registrado, no fim da etapa)."""
from types import SimpleNamespace

import pytest

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
