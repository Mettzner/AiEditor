"""Fase E: Batch persistente, retomada por integridade e invalidação só do que depende da mudança; leitura de dados
legados depois das migrações."""
from __future__ import annotations

import json
import threading
from datetime import timedelta
from types import SimpleNamespace

import pytest
from pydantic import BaseModel
from sqlalchemy import create_engine, text
from sqlmodel import delete, select

from app import artifacts
from app.db import session_scope
from app.models import BatchJob, LlmCache, Preset, Production, ProductionConfig, now
from app.providers.llm import anthropic as A
from app.providers.llm.base import BatchPending


class Out(BaseModel):
    answer: str


def _msg(text_: str, in_t=100, out_t=20):
    return SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(type="text", text=text_)],
                           usage=SimpleNamespace(input_tokens=in_t, output_tokens=out_t, cache_creation_input_tokens=0,
                                                 cache_read_input_tokens=0), model="claude-sonnet-5-5")


class Batches:
    def __init__(self):
        self.created, self.cancelled, self.status, self.custom = 0, [], "in_progress", None
        self.lock = threading.Lock()

    def create(self, **kw):
        with self.lock:
            self.created += 1
        self.custom = kw["requests"][0]["custom_id"]
        return SimpleNamespace(id=f"batch-{self.created}")

    def retrieve(self, _id):
        return SimpleNamespace(id=_id, processing_status=self.status)

    def results(self, _id):
        return [SimpleNamespace(custom_id=self.custom, result=SimpleNamespace(type="succeeded",
                                                                              message=_msg('{"answer":"ok"}')))]

    def cancel(self, _id):
        self.cancelled.append(_id)


@pytest.fixture
def batches(monkeypatch):
    with session_scope() as s:
        s.exec(delete(BatchJob))
        s.exec(delete(LlmCache))
        s.commit()
    b = Batches()
    client = SimpleNamespace(messages=SimpleNamespace(batches=b, create=lambda **kw: _msg('{"answer":"sync"}')),
                             beta=SimpleNamespace(messages=SimpleNamespace(create=lambda **kw: _msg('{"answer":"sync"}'))))
    monkeypatch.setattr(A.AnthropicLLM, "_client", lambda self: client)
    return b


def call(user="pergunta"):
    return A.AnthropicLLM().structured(model="claude-sonnet-5-5", system="S", user=user, schema=Out, task="plan",
                                       batch=True)


def test_reinicio_durante_lote_recupera_o_id_sem_duplicar(batches):
    with pytest.raises(BatchPending) as first:
        call()
    assert batches.created == 1 and first.value.batch_id == "batch-1"
    # "reinício do worker": nova instância, mesmo pedido → confere o lote existente, não cria outro
    with pytest.raises(BatchPending):
        call()
    assert batches.created == 1
    batches.status = "ended"
    parsed, usage = call()
    assert parsed.answer == "ok" and usage.batch and batches.created == 1
    assert usage.cost == pytest.approx((100 * 2 + 20 * 10) / 1e6 / 2)  # desconto só nesta chamada de lote
    again, usage2 = call()  # depois de consumido: cache local, custo zero (não cobra em dobro)
    assert again.answer == "ok" and usage2.local_cache and usage2.cost == 0
    with session_scope() as s:
        job = s.exec(select(BatchJob)).one()
        assert job.status == "ended" and job.batch_id == "batch-1" and job.polls >= 2


def test_prazo_vencido_cancela_e_segue_sem_desconto(batches):
    with pytest.raises(BatchPending):
        call("prazo")
    with session_scope() as s:
        job = s.exec(select(BatchJob)).one()
        job.deadline_at = now() - timedelta(minutes=1)
        s.add(job)
        s.commit()
    parsed, usage = call("prazo")
    assert parsed.answer == "sync" and not usage.batch and batches.cancelled == ["batch-1"]


def test_producao_espera_lote_fora_do_worker_e_volta_na_hora(monkeypatch):
    from app.worker import runner
    from app.worker.__main__ import _wake_waiting

    with session_scope() as s:
        cfg = ProductionConfig(**Preset().model_dump(), title="t", channel_name="c").model_dump()
        p = Production(title="lote", script="x", config=cfg, status="running")
        s.add(p)
        s.commit()
        s.refresh(p)
        pid = p.id

    def pending(ctx):
        raise BatchPending("lote enviado", now() + timedelta(minutes=5), "batch-x")

    monkeypatch.setattr(runner, "STEPS", [("plan", 100, "Planejamento", pending)])
    monkeypatch.setattr(runner, "applicable_steps", lambda cfg: ["plan"])
    runner.run_production(pid, threading.Semaphore(1))
    with session_scope() as s:
        p = s.get(Production, pid)
        assert p.status == "waiting_provider" and p.resume_at is not None
        p.resume_at = now() - timedelta(seconds=1)
        s.add(p)
        s.commit()
    _wake_waiting()
    with session_scope() as s:
        assert s.get(Production, pid).status == "queued"  # volta para a fila; o worker pega na vez dela


def test_cancelar_producao_aguardando_cancela_o_lote(monkeypatch, batches):
    from fastapi.testclient import TestClient

    from app.main import app

    with session_scope() as s:
        p = Production(title="c", script="x", config={}, status="waiting_provider")
        s.add(p)
        s.commit()
        s.refresh(p)
        s.add(BatchJob(production_id=p.id, request_hash="h-cancel", batch_id="batch-z", status="submitted"))
        s.commit()
        pid = p.id
    with TestClient(app) as c:
        assert c.post(f"/api/productions/{pid}/cancel").status_code == 200
    assert batches.cancelled == ["batch-z"]
    with session_scope() as s:
        assert s.exec(select(BatchJob).where(BatchJob.batch_id == "batch-z")).one().status == "cancelled"


# ---------------------------------------------------------------- invalidação por dependência (E4)
def _job(tmp_path):
    (tmp_path / "audio").mkdir()
    (tmp_path / "audio" / "narration.wav").write_bytes(b"RIFF" * 10)
    (tmp_path / "output").mkdir()
    (tmp_path / "output" / "final.mp4").write_bytes(b"mp4")
    for name in ("transcript.json", "plan.json", "context_bible.json", "selection.json", "timeline.json"):
        (tmp_path / name).write_text(json.dumps({"v": name}), encoding="utf-8")
    return tmp_path


def test_mudanca_invalida_so_as_dependencias(tmp_path):
    job = _job(tmp_path)
    cfg = ProductionConfig(**Preset().model_dump(), title="t", channel_name="c").model_dump()
    settings = {"render": {"crf": 20}, "selection": {"min_score": 6.5}, "transcription": {"model": "small"},
                "llm": {"plan": {"model": "m"}, "bible": {"model": "m"}}, "youtube": {}, "archives": {},
                "vision": {}}
    for step in ("audio", "transcribe", "plan", "select", "direct", "render"):
        artifacts.record(step, job, "roteiro", cfg, settings)
    ok = lambda step, c=cfg, st=settings, sc="roteiro": artifacts.still_valid(step, job, sc, c, st)[0]  # noqa: E731
    assert all(ok(s) for s in ("audio", "transcribe", "plan", "select", "direct", "render"))
    # config de render muda: só o render
    st2 = {**settings, "render": {"crf": 18}}
    assert not ok("render", st=st2) and ok("plan", st=st2) and ok("audio", st=st2)
    # duração média das cenas muda: planejamento (e depois o que depende do plano), áudio intacto
    cfg2 = {**cfg, "avg_scene_seconds": 4.0}
    assert not ok("plan", c=cfg2) and ok("audio", c=cfg2) and ok("transcribe", c=cfg2)
    # roteiro muda (TTS): áudio e transcrição
    assert not ok("audio", sc="outro roteiro") and not ok("transcribe", sc="outro roteiro")
    # plano reescrito: seleção e direção invalidam; render só se a timeline mudar
    (job / "plan.json").write_text('{"v": "novo"}', encoding="utf-8")
    assert not ok("select") and not ok("direct") and ok("render")
    # saída sumiu: a etapa refaz
    (job / "timeline.json").unlink()
    assert not ok("direct")


def test_producao_antiga_sem_registro_continua_retomando(tmp_path):
    assert artifacts.still_valid("plan", tmp_path, "x", {}, {})[0]


def test_cena_alterada_perde_so_a_propria_escolha(tmp_path, monkeypatch):
    from tests.test_selector import make_ctx

    from app.pipeline import select as sel_mod

    ctx = make_ctx()
    scenes = [{"id": f"s00{i}", "start": i * 5.0, "end": i * 5.0 + 5, "text": f"cena {i}", "subject": "road",
               "queries": ["road"], "source": "stock"} for i in (1, 2)]
    ctx.write_json("plan.json", {"scenes": scenes})
    sel = sel_mod.Selector(ctx)
    sel.selection = {"s001": {"source": "stock", "asset": "a1"}, "s002": {"source": "stock", "asset": "a2"}}
    sel.save()
    scenes[1]["text"] = "cena 2 reescrita"
    ctx.write_json("plan.json", {"scenes": scenes})
    again = sel_mod.Selector(ctx)
    assert set(again.selection) == {"s001"} and again.invalidated_scenes == ["s002"]


def test_render_refaz_so_a_cena_alterada(tmp_path):
    from app.pipeline.render import compose

    class Ctx:
        dir = tmp_path

    rdir = tmp_path / "render" / "scenes"
    rdir.mkdir(parents=True)
    (tmp_path / "assets").mkdir()
    for sid in ("s1", "s2", "s3"):
        (tmp_path / "assets" / f"{sid}.mp4").write_bytes(sid.encode())
        (rdir / f"{sid}.mp4").write_bytes(b"x")
    scenes = [{"id": sid, "start": i * 5.0, "end": i * 5.0 + 5, "asset": f"assets/{sid}.mp4", "in": 0.0,
               "motion": None, "transition_in": {"type": "crossfade" if sid == "s3" else "cut", "duration": 0.5}}
              for i, sid in enumerate(("s1", "s2", "s3"))]
    xin = {"s3": "s2"}
    assert compose.invalidate_changed_scenes(Ctx(), scenes, rdir, xin) == []  # 1ª vez: só grava as impressões
    (tmp_path / "assets" / "s2.mp4").write_bytes(b"outro asset")
    assert compose.invalidate_changed_scenes(Ctx(), scenes, rdir, xin) == ["s2"]
    assert (rdir / "s1.mp4").exists() and not (rdir / "s2.mp4").exists()
    assert not (rdir / "s3.mp4").exists()  # a transição de entrada da s3 usava a s2


# ---------------------------------------------------------------- dados legados (16)
def test_presets_producoes_e_banco_legados_continuam_legiveis(tmp_path):
    from app.migrations import MIGRATIONS, migrate

    old_preset = {"language": "pt", "real_pct": 80, "youtube_pct": 30, "avg_scene_seconds": 5}
    assert Preset.model_validate(old_preset).youtube_pct == 30
    old_cfg = {**old_preset, "title": "t", "channel_name": "c"}
    assert ProductionConfig.model_validate(old_cfg).lang == "pt"
    eng = create_engine(f"sqlite:///{tmp_path / 'v1.db'}")
    with eng.begin() as conn:
        conn.execute(text("CREATE TABLE schema_version (version INTEGER NOT NULL, name TEXT, applied_at TEXT)"))
        conn.execute(text("INSERT INTO schema_version (version, name) VALUES (1, 'baseline')"))
        conn.execute(text("CREATE TABLE production (id INTEGER PRIMARY KEY, title VARCHAR, status VARCHAR)"))
        conn.execute(text("INSERT INTO production (title, status) VALUES ('antiga', 'done')"))
        conn.execute(text("CREATE TABLE usedasset (id INTEGER PRIMARY KEY, external_id VARCHAR, segment_start FLOAT)"))
        conn.execute(text("INSERT INTO usedasset (external_id, segment_start) VALUES ('x', 1.5)"))
        conn.execute(text("CREATE TABLE providerprice (id INTEGER PRIMARY KEY, provider VARCHAR, unit VARCHAR, "
                          "price FLOAT, note VARCHAR)"))
        conn.execute(text("INSERT INTO providerprice (provider, unit, price, note) VALUES "
                          "('darkvi', 'per_image', 0, 'incluso no plano')"))
    assert migrate(eng) == MIGRATIONS[-1][0]
    with eng.begin() as conn:
        assert conn.execute(text("SELECT title, resume_at FROM production")).one() == ("antiga", None)
        assert conn.execute(text("SELECT external_id, segment_start, segment_end FROM usedasset")).one() == \
            ("x", 1.5, None)
        assert conn.execute(text("SELECT currency, regime, as_of FROM providerprice")).one() == ("USD", "plan", None)
