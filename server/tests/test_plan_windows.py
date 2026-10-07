"""Planejamento em janelas: resposta cortada por max_tokens não é repetida igual (seria cortada e cobrada de novo);
a janela é dividida ao meio. O custo das tentativas cortadas entra na produção."""
from __future__ import annotations

import re

from app.pipeline import context as C
from app.pipeline import plan as plan_mod
from app.pipeline.plan import PlanWindow
from app.providers.llm.anthropic import LLMCallFailed
from app.providers.llm.base import LLMUsage

from .test_context import IRELAND, draft, raw_bible
from .test_selector import make_ctx


def _transcript(ctx, n_units: int) -> None:
    words = [{"text": f"Word{i}.", "start": i * 1.0, "end": i * 1.0 + 0.6} for i in range(n_units)]
    ctx.write_json("transcript.json", {"words": words, "audio_duration": float(n_units)})


def _fake_llm(max_units: int, calls: list):
    """IA simulada: corta (max_tokens) quando o pedido tem mais de max_units unidades."""
    bible = raw_bible([{**IRELAND, "first_unit": 0, "last_unit": 10_000}])

    def fake(task, *, schema, user="", **kw):
        if schema is C.ContextBible:
            return C.ContextBible.model_validate(bible), LLMUsage(task="bible", cost=0.01)
        a, b = map(int, re.search(r"Plan units (\d+) to (\d+)", user).groups())
        calls.append((a, b))
        if b - a + 1 > max_units:
            err = LLMCallFailed("cortada", response=None, truncated=True)
            err.usage = LLMUsage(task="plan", output_tokens=16000, cost=0.16)
            raise err
        return PlanWindow(scenes=[draft(i, i) for i in range(a, b + 1)], music_mood=None), LLMUsage(
            task="plan", cost=0.02)

    return fake


def test_resposta_cortada_divide_a_janela_em_vez_de_repetir(monkeypatch):
    ctx = make_ctx()
    _transcript(ctx, 40)
    calls: list = []
    monkeypatch.setattr(plan_mod, "WINDOW_UNITS", 40)
    monkeypatch.setattr(plan_mod, "call_llm", _fake_llm(max_units=20, calls=calls))
    plan_mod.run(ctx)
    units = ctx.read_json("units.json")
    assert len(units) == 40
    assert calls == [(0, 39), (0, 19), (20, 39)]  # nada de repetir o pedido que cortou
    scenes = ctx.read_json("plan.json")["scenes"]
    assert all(s["style_reason"] != "fallback automático" for s in scenes)
    usage = ctx.read_json("llm_usage.json")
    plan_costs = sorted(c["cost"] for c in usage["calls"] if c["task"] == "plan")
    assert plan_costs == [0.02, 0.02, 0.16]  # a tentativa cortada também foi cobrada


def test_janela_pequena_que_ainda_corta_vai_para_o_agrupamento_automatico(monkeypatch):
    ctx = make_ctx()
    _transcript(ctx, 8)
    calls: list = []
    monkeypatch.setattr(plan_mod, "call_llm", _fake_llm(max_units=2, calls=calls))
    plan_mod.run(ctx)
    assert calls == [(0, 7)]  # pequena demais para dividir: uma tentativa só, sem retry idêntico
    scenes = ctx.read_json("plan.json")["scenes"]
    assert scenes and all(s["style_reason"] == "fallback automático" for s in scenes)
