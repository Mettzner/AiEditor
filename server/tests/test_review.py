"""Fase F: revisão integrada ao backend (leitura, trocas, trecho, busca, correções, render parcial) e contratos
frontend/backend; histórico paginado."""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

from app.config import job_dir
from app.db import session_scope
from app.models import Preset, Production, ProductionConfig, ProductionStep
from app.providers.stock.base import Candidate, Rendition

WEB = Path(__file__).resolve().parents[2] / "web"


def cand(i: int) -> dict:
    return Candidate(provider="pexels", external_id=f"alt{i}", title=f"farmer field {i}", duration=30, width=1920,
                     height=1080, page_url=f"https://pexels.com/v/{i}", thumbnail=None, author="Ana",
                     renditions=[Rendition(f"https://cdn/p/{i}.mp4", 1920, 1080)],
                     preview_frames=[f"https://f/{i}.jpg"]).to_dict()


@pytest.fixture
def prod(monkeypatch):
    from app.main import app
    from app.pipeline import select as sel_mod

    monkeypatch.setattr(sel_mod, "download", lambda url, dest, headers=None, **kw: dest.write_bytes(b"x") or dest)
    with session_scope() as s:
        cfg = ProductionConfig(**Preset().model_dump(), title="rev", channel_name="c").model_dump()
        p = Production(title="rev", script="x", config=cfg, status="done")
        s.add(p)
        s.commit()
        s.refresh(p)
        pid = p.id
        for step in ("audio", "transcribe", "plan", "select", "direct", "render", "upload"):
            s.add(ProductionStep(production_id=pid, step=step, status="done"))
        s.commit()
    job = job_dir(pid)
    (job / "assets").mkdir(parents=True, exist_ok=True)
    (job / "assets" / "s001.mp4").write_bytes(b"x")
    plan = {"scenes": [{"id": "s001", "start": 0.0, "end": 5.0, "text": "The farmer dug the field.",
                        "subject": "farmer", "queries": ["farmer field"], "source": "stock", "entity_ids": ["ent01"],
                        "claim_ids": ["clm01"], "visual_role": "contextual_illustration", "unresolved": []}]}
    selection = {"s001": {"source": "stock", "source_used": "stock", "provider": "pexels", "external_id": "orig",
                          "asset": "assets/s001.mp4", "method": "text", "score": 4.0, "in": 2.0, "in_point": 2.0,
                          "out_point": 7.0, "clip_duration": 30, "page_url": "https://pexels.com/v/orig",
                          "alternatives": [{"candidate": cand(1), "score": 8.2, "pos": 0.5, "method": "vision",
                                            "source": "stock", "reason": "farmer digging"}]}}
    bible = {"entities": [{"id": "ent01", "name": "farmer", "kind": "person", "aliases": []}],
             "claims": [{"id": "clm01", "quote": "dug the field", "needs_source": True}]}
    for name, data in (("plan.json", plan), ("selection.json", selection), ("context_bible.json", bible)):
        (job / name).write_text(json.dumps(data), encoding="utf-8")
    with TestClient(app) as c:
        yield c, pid, job


def steps(pid) -> dict:
    with session_scope() as s:
        return {r.step: r.status for r in s.exec(select(ProductionStep).where(ProductionStep.production_id == pid))}


def test_revisao_lista_cenas_com_estado_flags_e_metricas(prod):
    c, pid, _ = prod
    data = c.get(f"/api/productions/{pid}/review").json()
    s = data["scenes"][0]
    assert s["validation"]["status"] == "unvalidated" and "no_vision" in s["flags"]
    assert "claim_without_source" in s["flags"] and s["alternatives"][0]["score"] == 8.2
    assert data["metrics"]["by_source"]["stock"]["scenes"] == 1


def _ts_fields(interface: str) -> set[str]:
    src = (WEB / "lib" / "api.ts").read_text(encoding="utf-8")
    body = re.search(rf"export interface {interface} \{{(.*?)\n\}}", src, re.S).group(1)
    return set(re.findall(r"^\s{2}(\w+)\??:", body, re.M))


def test_contrato_frontend_backend_da_revisao(prod):
    c, pid, _ = prod
    data = c.get(f"/api/productions/{pid}/review").json()
    assert _ts_fields("ReviewScene") <= set(data["scenes"][0])
    assert _ts_fields("ReviewPayload") <= set(data)
    assert _ts_fields("ReviewAlternative") <= set(data["scenes"][0]["alternatives"][0])


def test_trocar_pela_alternativa_fixa_e_guarda_a_anterior(prod):
    c, pid, job = prod
    r = c.post(f"/api/productions/{pid}/scenes/s001/replace", json={"alternative": 0})
    assert r.status_code == 200, r.text
    e = json.loads((job / "selection.json").read_text(encoding="utf-8"))["s001"]
    assert e["external_id"] == "alt1" and e["pinned"] and e["validation"]["status"] == "validated"
    assert e["alternatives"][-1]["reason"] == "escolha anterior"
    assert c.post(f"/api/productions/{pid}/scenes/s001/replace", json={"alternative": 9}).status_code == 422


def test_trecho_editado_nao_vira_confirmado(prod):
    c, pid, job = prod
    assert c.post(f"/api/productions/{pid}/scenes/s001/interval", json={"in_point": 26}).status_code == 422
    r = c.post(f"/api/productions/{pid}/scenes/s001/interval", json={"in_point": 10})
    assert r.status_code == 200
    e = json.loads((job / "selection.json").read_text(encoding="utf-8"))["s001"]
    assert e["in"] == 10 and e["out_point"] == 15 and e["segment_check"]["status"] == "edited"


def test_buscar_mais_acrescenta_alternativas_sem_trocar(prod, monkeypatch):
    from app.pipeline import select as sel_mod
    from tests.test_selector import FakeStock

    monkeypatch.setattr(sel_mod, "enabled_providers", lambda: [FakeStock("pexels")])
    c, pid, job = prod
    r = c.post(f"/api/productions/{pid}/scenes/s001/search-more", json={"queries": ["farmer plowing field"]})
    assert r.status_code == 200 and r.json()["found"] > 0
    e = json.loads((job / "selection.json").read_text(encoding="utf-8"))["s001"]
    assert e["external_id"] == "orig" and len(e["alternatives"]) > 1


def test_corrigir_interpretacao_requeue_so_o_que_depende(prod):
    c, pid, job = prod
    r = c.patch(f"/api/productions/{pid}/scenes/s001/interpretation", json={"visual_role": "exact_evidence",
                                                                           "subject": "old farmer"})
    assert r.status_code == 200
    plan = json.loads((job / "plan.json").read_text(encoding="utf-8"))
    assert plan["scenes"][0]["visual_role"] == "exact_evidence" and plan["scenes"][0]["subject"] == "old farmer"
    st = steps(pid)
    assert st["audio"] == "done" and st["plan"] == "done" and st["select"] == "pending" and st["render"] == "pending"


def test_corrigir_entidade_muda_a_impressao_das_cenas(prod):
    from app.artifacts import scene_fingerprint

    c, pid, job = prod
    before = scene_fingerprint(json.loads((job / "plan.json").read_text(encoding="utf-8"))["scenes"][0])
    assert c.patch(f"/api/productions/{pid}/entities/ent01", json={"scientific_name": "Homo sapiens"}).json()["scenes"] == 1
    after = scene_fingerprint(json.loads((job / "plan.json").read_text(encoding="utf-8"))["scenes"][0])
    assert before != after


def test_idioma_e_render_parcial(prod):
    c, pid, _ = prod
    assert c.post(f"/api/productions/{pid}/language", json={"video_language": "xx"}).status_code == 422
    assert c.post(f"/api/productions/{pid}/rerender").status_code == 200
    st = steps(pid)
    assert st["direct"] == "pending" and st["render"] == "pending" and st["select"] == "done"
    assert c.post(f"/api/productions/{pid}/rerender").status_code == 409  # já na fila: não edita


def test_assets_so_dentro_da_pasta_da_producao(prod):
    c, pid, _ = prod
    assert c.get(f"/api/productions/{pid}/assets/assets/s001.mp4").status_code == 200
    assert c.get(f"/api/productions/{pid}/assets/../../aieditor.db").status_code in (404, 422)
    assert c.get(f"/api/productions/{pid}/assets/..%2f..%2faieditor.db").status_code == 404


def test_manifesto_e_creditos_exportaveis(prod):
    c, pid, _ = prod
    m = c.get(f"/api/productions/{pid}/manifest").json()
    assert m["assets"][0]["scene"] == "s001" and m["claims_pending_source"]
    assert "dug the field" in c.get(f"/api/productions/{pid}/credits").text


def test_historico_paginado_e_ids(prod):
    c, pid, _ = prod
    r = c.get("/api/productions?limit=1&offset=0")
    assert r.status_code == 200 and len(r.json()) == 1 and int(r.headers["X-Total-Count"]) >= 1
    assert pid in c.get("/api/productions/ids").json()
