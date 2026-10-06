"""OTIMIZACAO_CUSTO_CLAUDE.md: medição, cache de prompt, cache local, lote e parâmetros por modelo (sem rede)."""
from __future__ import annotations

import json
import re
from types import SimpleNamespace

from app.pipeline import plan as plan_mod
from app.pipeline import select as sel_mod
from app.pipeline.visual import PLAN_SYSTEM
from app.providers.llm import anthropic as A
from app.providers.llm.base import LLMUsage
from pydantic import BaseModel

from .test_selector import env, issues, make_ctx, row, scene  # noqa: F401


class Out(BaseModel):
    answer: str


def fake_response(parsed, in_t=1000, out_t=50, cw=0, cr=0, stop="end_turn", text=None):
    if text is None:
        text = parsed.model_dump_json() if parsed is not None else ""
    return SimpleNamespace(parsed_output=parsed, stop_reason=stop, stop_details=None,
                           content=[SimpleNamespace(type="text", text=text)],
                           usage=SimpleNamespace(input_tokens=in_t, output_tokens=out_t,
                                                 cache_creation_input_tokens=cw, cache_read_input_tokens=cr))


# ---------------------------------------------------------------- §3 estrutura do prompt e breakpoints
def test_parametros_com_breakpoints_de_cache():
    p = A.AnthropicLLM._params("claude-sonnet-5-5", "SYSTEM", "pedido", "CONTEXTO", "low", "adaptive", 4000, "5m")
    assert p["system"][0]["cache_control"] == {"type": "ephemeral"}
    ctx_block, user_block = p["messages"][0]["content"]
    assert ctx_block["text"] == "CONTEXTO" and ctx_block["cache_control"] == {"type": "ephemeral"}
    assert "cache_control" not in user_block  # a parte variável fica depois dos breakpoints
    assert p["thinking"] == {"type": "adaptive"} and p["output_config"] == {"effort": "low"}


def test_parametros_por_modelo():
    haiku = A.AnthropicLLM._params("claude-haiku-4-5", "S", "u", None, "low", "off", 300, "5m")
    assert "output_config" not in haiku and "thinking" not in haiku  # Haiku 4.5 não aceita effort
    sonnet_off = A.AnthropicLLM._params("claude-sonnet-5-5", "S", "u", None, "low", "off", 300, "5m")
    assert sonnet_off["thinking"] == {"type": "between_tools"}  # "disabled" dá 400 no Sonnet 5.5
    ttl = A.AnthropicLLM._params("claude-sonnet-5-5", "S", "u", "C", None, "adaptive", 300, "1h")
    assert ttl["system"][0]["cache_control"] == {"type": "ephemeral", "ttl": "1h"}


def test_sistema_do_planejamento_sem_dados_variaveis():
    assert not re.search(r"\{[a-z_]+\}", PLAN_SYSTEM)  # nada de template: mesmo texto em toda produção
    for volatile in ("2026", "production id", "Title:"):
        assert volatile not in PLAN_SYSTEM


def test_contexto_sem_timestamps_por_palavra_e_estavel(env):
    ctx = make_ctx()
    units = [{"i": 0, "start": 0.0, "end": 4.2, "text": "Frozen fruit keeps nutrients."},
             {"i": 1, "start": 4.2, "end": 9.0, "text": "Freeze it fast."}]
    c1, c2 = plan_mod.plan_context(ctx, units), plan_mod.plan_context(ctx, units)
    assert c1 == c2  # byte a byte
    assert "[0|0.0-4.2] Frozen fruit keeps nutrients." in c1
    assert '"words"' not in c1 and "start" not in c1


# ---------------------------------------------------------------- §1 medição e custo
def test_custo_com_cache_e_lote():
    usage = A._usage_from(fake_response(None, 100, 1000, 2000, 0), "claude-sonnet-5-5", "plan", 1.0)
    assert round(usage.cost, 6) == round((100 * 2 + 1000 * 10 + 2000 * 2.5) / 1e6, 6)
    cached = A._usage_from(fake_response(None, 100, 1000, 0, 2000), "claude-sonnet-5-5", "plan", 1.0)
    assert cached.cost < usage.cost  # leitura de cache custa 10% da entrada no Sonnet 5.5
    batch = A._usage_from(fake_response(None, 100, 1000, 0, 0), "claude-sonnet-5-5", "plan", 1.0, batch=True)
    assert round(batch.cost, 6) == round((100 * 2 + 1000 * 10) / 1e6 / 2, 6)


def test_llm_usage_json_por_producao(env):
    ctx = make_ctx()
    ctx.record_llm(LLMUsage(input_tokens=500, output_tokens=200, cost=0.003, model="claude-sonnet-5-5", task="plan"))
    ctx.record_llm(LLMUsage(input_tokens=50, output_tokens=20, cost=0.0002, model="claude-haiku-4-5",
                            task="rewrite"))
    ctx.record_llm({"task": "visão (Gemini)", "cost": 0.001, "calls": 8})
    data = ctx.read_json("llm_usage.json")
    assert data["summary"]["calls"] == 3 and data["summary"]["top_task"] == "plan"
    assert abs(data["summary"]["cost"] - 0.0042) < 1e-9


# ---------------------------------------------------------------- §7 não pagar duas vezes
def test_cache_local_de_respostas(env, monkeypatch):
    calls = []

    class FakeMessages:
        def create(self, **kw):
            calls.append(kw)
            return fake_response(Out(answer="ok"))

    fake_client = SimpleNamespace(messages=FakeMessages(), beta=SimpleNamespace(messages=FakeMessages()))
    monkeypatch.setattr(A.AnthropicLLM, "_client", lambda self: fake_client)
    llm = A.AnthropicLLM()
    kw = dict(model="claude-haiku-4-5", system="S", user="mesma entrada única 123", schema=Out, task="rewrite",
              thinking="off")
    first, u1 = llm.structured(**kw)
    again, u2 = llm.structured(**kw)
    assert first.answer == again.answer == "ok" and len(calls) == 1
    assert not u1.local_cache and u2.local_cache and u2.cost == 0



# ---------------------------------------------------------------- tentativas cobradas que falham
def _client_returning(resp, calls):
    class FakeMessages:
        def create(self, **kw):
            calls.append(kw)
            return resp

    return SimpleNamespace(messages=FakeMessages(), beta=SimpleNamespace(messages=FakeMessages()))


def test_resposta_cortada_leva_o_uso_cobrado_no_erro(env, monkeypatch):
    import pytest

    calls = []
    cut = fake_response(None, in_t=2000, out_t=16000, stop="max_tokens", text='{"answer":"cort')
    monkeypatch.setattr(A.AnthropicLLM, "_client", lambda self: _client_returning(cut, calls))
    with pytest.raises(A.LLMCallFailed) as err:
        A.AnthropicLLM().structured(model="claude-sonnet-5-5", system="S", user="corta 1", schema=Out, task="plan")
    assert err.value.truncated and err.value.usage.output_tokens == 16000
    assert round(err.value.usage.cost, 6) == round((2000 * 2 + 16000 * 10) / 1e6, 6)
    assert calls[0]["output_config"]["format"]["type"] == "json_schema"  # saída estruturada sem o parse do SDK


def test_json_invalido_tambem_leva_o_uso(env, monkeypatch):
    import pytest

    bad = fake_response(None, in_t=100, out_t=10, text="não é json")
    monkeypatch.setattr(A.AnthropicLLM, "_client", lambda self: _client_returning(bad, []))
    with pytest.raises(A.LLMCallFailed) as err:
        A.AnthropicLLM().structured(model="claude-haiku-4-5", system="S", user="json 1", schema=Out, task="rewrite",
                                    thinking="off")
    assert not err.value.truncated and err.value.usage.input_tokens == 100


def test_lote_cortado_nao_vira_erro_de_json_sem_custo(env, monkeypatch):
    import pytest

    cut = fake_response(None, in_t=50, out_t=16000, stop="max_tokens", text='{"answer":"cort')

    class FakeBatches:
        def create(self, **kw):
            return SimpleNamespace(id="b1", processing_status="ended")

        def retrieve(self, _id):
            return SimpleNamespace(id="b1", processing_status="ended")

        def results(self, _id):
            return [SimpleNamespace(result=SimpleNamespace(type="succeeded", message=cut))]

    client = SimpleNamespace(messages=SimpleNamespace(batches=FakeBatches()))
    monkeypatch.setattr(A.AnthropicLLM, "_client", lambda self: client)
    with pytest.raises(A.LLMCallFailed) as err:
        A.AnthropicLLM().structured(model="claude-sonnet-5-5", system="S", user="lote 1", schema=Out, task="plan",
                                    batch=True)
    assert err.value.truncated and err.value.usage.batch
    assert round(err.value.usage.cost, 6) == round((50 * 2 + 16000 * 10) / 1e6 / 2, 6)


def test_reescrita_de_varias_cenas_numa_unica_chamada(env, monkeypatch):
    def vision(n, prompt, call):
        good = "chest freezer" in prompt
        return [row(1, 8.5 if good else 3.0, subject=good)] + [row(i, 2.0) for i in range(2, n + 1)]

    env["vision"].mode = vision
    ctx = make_ctx()
    scenes = [{**scene(f"s{i:03d}", q=f"topic{i} road"), "start": i * 5.0, "end": i * 5.0 + 5.0,
               "subject": f"topic{i} road", "visual_intent": f"intent {i}", "must_avoid": ["freezer"]}
              for i in range(1, 5)]
    ctx.write_json("plan.json", {"scenes": scenes})
    sel_mod.run(ctx)
    assert env["llm"].calls == ["QueryRewriteBatch"]  # 4 cenas reprovadas → 1 chamada
    assert len(json.loads(env["llm"].users[0])) == 4
    assert issues(ctx).count("QUERY_REWRITTEN") == 4
    sel = ctx.read_json("selection.json")
    assert all(v["score"] >= 6.5 for v in sel.values())


# ---------------------------------------------------------------- outras APIs: TTS da Darkvi
def test_narracao_reaproveitada_do_cache(env, monkeypatch, tmp_path):
    from app.pipeline import audio

    mp3, srt = tmp_path / "a.mp3", tmp_path / "a.srt"
    mp3.write_bytes(b"mp3")
    srt.write_text("1\n00:00:00,000 --> 00:00:01,000\nhi\n", encoding="utf-8")
    audio._to_tts_cache("mesmo texto", "voz-1", mp3, srt)
    out_mp3, out_srt = tmp_path / "b.mp3", tmp_path / "b.srt"
    assert audio._from_tts_cache("mesmo texto", "voz-1", out_mp3, out_srt) and out_mp3.read_bytes() == b"mp3"
    assert not audio._from_tts_cache("mesmo texto", "outra voz", tmp_path / "c.mp3", tmp_path / "c.srt")
