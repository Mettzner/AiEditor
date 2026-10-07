"""Critérios de aceite de MELHORIA_SELECAO_DE_CENAS.md (§7), com provedores e IA simulados."""
from __future__ import annotations

import threading
from datetime import datetime, timedelta

import httpx
import pytest
from sqlmodel import delete, select

from app.db import init_db, session_scope
from app.models import (Channel, Issue, Preset, Production, ProductionConfig, SearchCache, UsedAsset, VisionCache,
                        YtQuota)
from app.pipeline import select as sel_mod
from app.pipeline.select import funnel
from app.providers.llm import gemini
from app.pipeline.visual import VisionRow, VisionSheet
from app.providers.stock.base import Candidate, Rendition
from app.providers.youtube import client as yt
from app.providers.youtube import quota
from app.worker.context import JobContext

init_db()


# ---------------------------------------------------------------- fakes
class FakeStock:
    """Banco simulado: 10 vídeos por query (3 queries → 30 candidatos por cena) e fotos opcionais."""

    def __init__(self, pid: str, videos: int = 10, photos: int = 5):
        self.id, self.videos, self.photos = pid, videos, photos
        self.calls = 0

    def _cand(self, i: int, query: str, image: bool) -> Candidate:
        return Candidate(
            provider=self.id, external_id=f"{'photo-' if image else ''}{query[:6]}-{i}", title=f"{query} night road",
            duration=0.0 if image else 30.0, width=1920, height=1080 if not image else 1280,
            page_url=f"https://x/{i}", thumbnail=None,
            renditions=[Rendition(f"https://cdn/{self.id}/{i}.{'jpg' if image else 'mp4'}", 1920, 1080)],
            query=query, author="Autor", is_image=image,
            preview_frames=[f"https://f/{i}/{k}.jpg" for k in range(1 if image else 15)])

    def search(self, query, per_page=15, lang="en", **kw):
        self.calls += 1
        return [self._cand(i, query, False) for i in range(self.videos)]

    def search_photos(self, query, per_page=15, lang="en", **kw):
        self.calls += 1
        return [self._cand(i, query, True) for i in range(self.photos)]


def row(i: int, value: float, realism: str = "real_footage", subject: bool = True, forbidden=()) -> VisionRow:
    # context_match = quality = value → nota calculada = value
    return VisionRow(row=i, seen=f"candidato {i}", realism=realism, subject_visible=subject,
                     forbidden_present=list(forbidden), context_match=value, quality=value, best_frame=1)


class FakeVision:
    """Avaliações determinísticas: 'clear' dá um vencedor claro; 'tie' força o desempate."""

    def __init__(self, mode: str = "clear"):
        self.mode, self.calls = mode, 0

    def __call__(self, image, prompt, model, schema=None):
        self.calls += 1
        n = int(prompt.split("contact sheet with ")[1].split(" numbered")[0])
        if callable(self.mode):
            rows = self.mode(n, prompt, self.calls)
        elif self.mode == "clear":
            rows = [row(1, 9.0)] + [row(i, 5.0) for i in range(2, n + 1)]
        elif self.mode == "low":
            rows = [row(i, 4.0) for i in range(1, n + 1)]
        elif self.mode == "non_real":
            rows = [row(i, 9.5, realism="video_game") for i in range(1, n + 1)]
        else:
            rows = [row(1, 7.0), row(2, 6.9)] + [row(i, 5.0) for i in range(3, n + 1)]
        return VisionSheet(candidates=rows), 0.0


class FakeLLM:
    """LLM simulado para a reescrita de queries e a correção de overlays."""

    def __init__(self):
        self.calls = []
        self.users = []

    def __call__(self, task, *, system, user, schema, context=None, max_tokens=8000, batch=False):
        """Mesma assinatura de providers.llm.base.call_llm."""
        import json

        from app.providers.llm.base import LLMUsage
        self.calls.append(schema.__name__)
        self.users.append(user)
        if schema.__name__ == "QueryRewriteBatch":
            ids = [item["scene_id"] for item in json.loads(user)]
            return schema(items=[{"scene_id": i, "queries": ["frozen strawberries macro", "frozen strawberries bowl",
                                                              "strawberries frost"],
                                  "extra_must_avoid": ["chest freezer"]} for i in ids]), LLMUsage(task=task, cost=0.001)
        if schema.__name__ == "OverlayFixBatch":
            idx = [t["index"] for t in json.loads(user)["texts"]]
            return schema(items=[{"index": i, "text": "12 Protein-Rich Plants"} for i in idx]), LLMUsage(task=task)
        raise AssertionError(schema.__name__)


@pytest.fixture
def env(monkeypatch):
    with session_scope() as s:
        for model in (SearchCache, VisionCache, YtQuota, Issue, UsedAsset):
            s.exec(delete(model))
        s.commit()
    stock = [FakeStock("pexels"), FakeStock("pixabay")]
    vision = FakeVision()
    monkeypatch.setattr(sel_mod, "enabled_providers", lambda: stock)
    monkeypatch.setattr(sel_mod, "download", lambda url, dest, headers=None: dest.write_bytes(b"x") or dest)
    monkeypatch.setattr(sel_mod, "get_secret", lambda p: "k")
    monkeypatch.setattr(funnel, "build_sheet", lambda rows: b"jpeg")
    monkeypatch.setattr(gemini, "available", lambda: True)
    monkeypatch.setattr(gemini, "rate_sheet", vision)
    monkeypatch.setattr(yt, "_key", lambda: "k")
    llm = FakeLLM()
    monkeypatch.setattr(sel_mod, "call_llm", llm)
    return {"stock": stock, "vision": vision, "monkeypatch": monkeypatch, "llm": llm}


def make_ctx(real_pct: int = 100, mode: str = "fast") -> JobContext:
    with session_scope() as s:
        ch = Channel(name="t", preset=Preset().model_dump())
        s.add(ch)
        s.commit()
        s.refresh(ch)
        cfg = ProductionConfig(**Preset(real_pct=real_pct, selection_mode=mode).model_dump(), title="t",
                               channel_name="t")
        p = Production(channel_id=ch.id, title="t", script="x", config=cfg.model_dump(), status="running")
        s.add(p)
        s.commit()
        s.refresh(p)
    ctx = JobContext(p, {"select": 100}, threading.Semaphore(1))
    ctx.begin_step("select", "teste")
    return ctx


def scene(sid: str = "s001", source: str = "stock", q: str = "dark country road") -> dict:
    return {"id": sid, "start": 0.0, "end": 5.0, "text": "a figure crossed the road", "source": source,
            "visual_intent": "figura atravessando estrada rural à noite", "subject": "dark country road",
            "queries": [q, f"{q} fog", f"{q} headlights"]}


def issues(ctx) -> list[str]:
    with session_scope() as s:
        return [i.code for i in s.exec(select(Issue).where(Issue.production_id == ctx.production_id))]


# ---------------------------------------------------------------- critérios
def test_1_custo_no_maximo_2_chamadas_de_visao(env):  # noqa: D103
    ctx = make_ctx()
    entry = sel_mod.Selector(ctx).select_scene(scene())
    assert entry["vision_calls"] == 1 and entry["method"] == "vision"
    env["vision"].mode = "tie"  # modo rápido: sem desempate, continua 1 chamada
    entry = sel_mod.Selector(ctx).select_scene(scene("s002", q="foggy forest path"))
    assert entry["vision_calls"] == 1 and entry["method"] == "vision"
    # modo preciso: desempate por frames → 2 chamadas
    entry = sel_mod.Selector(make_ctx(mode="precise")).select_scene(scene("s003", q="old stone bridge"))
    assert entry["vision_calls"] == 2 and entry["method"] == "vision_tiebreak"


def test_2_cache_zero_buscas_e_zero_visao_na_segunda_vez(env):
    first = sel_mod.Selector(make_ctx()).select_scene(scene())
    assert first["searches"] > 0 and first["vision_calls"] == 1
    calls_before = sum(p.calls for p in env["stock"])
    again = sel_mod.Selector(make_ctx()).select_scene(scene())  # outra produção, mesma cena
    assert again["searches"] == 0 and again["vision_calls"] == 0
    assert sum(p.calls for p in env["stock"]) == calls_before
    assert again["external_id"] == first["external_id"]


def test_3_cota_preventiva_nao_chama_youtube(env):
    with session_scope() as s:
        s.add(YtQuota(day=quota.pacific_day(), used=9450))
        s.commit()
    hits = []
    env["monkeypatch"].setattr(yt, "request", lambda *a, **k: hits.append(a) or pytest.fail("chamou o YouTube"))
    ctx = make_ctx()
    entry = sel_mod.Selector(ctx).select_scene(scene(source="youtube"))
    assert not hits and entry["source_used"] == "stock"
    assert "YOUTUBE_QUOTA_FALLBACK" in issues(ctx)


def test_4_cota_reativa_403_e_cenas_seguintes_nao_chamam(env):
    hits = []

    def fake_request(method, url, **kw):
        hits.append(url)
        body = {"error": {"errors": [{"reason": "quotaExceeded"}]}}
        return httpx.Response(403, json=body, request=httpx.Request(method, url))

    env["monkeypatch"].setattr(yt, "request", fake_request)
    ctx = make_ctx()
    selector = sel_mod.Selector(ctx)
    entry = selector.select_scene(scene("s001", source="youtube"))
    assert entry["source_used"] == "stock" and len(hits) == 1
    codes = issues(ctx)
    assert "PROVIDER_QUOTA" in codes and "YOUTUBE_QUOTA_FALLBACK" in codes
    selector.select_scene(scene("s002", source="youtube", q="old farmhouse window"))
    assert len(hits) == 1  # não tentou de novo no mesmo dia
    assert quota.status()["exhausted"]


def test_5_fotos_como_ultimo_recurso_com_movimento(env, tmp_path):
    for p in env["stock"]:
        p.videos = 0
    ctx = make_ctx()
    entry = sel_mod.Selector(ctx).select_scene(scene())
    assert entry["is_image"] is True and entry["source_used"] == "stock_photo"
    assert entry["asset"].endswith(".jpg")
    # o render aplica Ken Burns em qualquer imagem
    from app.pipeline import direct
    plan = {"music_mood": None, "scenes": [{**scene(), "chapter_break": False, "highlight": None}]}
    ctx.write_json("plan.json", plan)
    ctx.write_json("selection.json", {"s001": entry})
    (ctx.dir / entry["asset"]).write_bytes(b"x")
    ctx.config.music.enabled = False
    direct.run(ctx)
    tl = ctx.read_json("timeline.json")
    assert tl["scenes"][0]["motion"]["type"] == "kenburns"


def test_6_cota_zera_a_meia_noite_do_pacifico(env):
    today = datetime.now(quota.PACIFIC)
    quota.mark_exhausted(quota.pacific_day(today))
    assert not quota.can_search(quota.pacific_day(today))
    tomorrow = (today + timedelta(days=1)).replace(hour=0, minute=1)
    assert quota.can_search(quota.pacific_day(tomorrow))
    # 23:59 em Los Angeles ainda é "hoje" mesmo já sendo outro dia em UTC
    late = today.replace(hour=23, minute=59)
    assert quota.pacific_day(late.astimezone(quota.ZoneInfo("UTC"))) == quota.pacific_day(today)


def test_7_sem_repeticao_no_mesmo_video(env):
    ctx = make_ctx()
    selector = sel_mod.Selector(ctx)
    a = selector.select_scene(scene("s001"))
    b = selector.select_scene(scene("s002"))  # mesmas queries → mesmos candidatos
    assert a["external_id"] != b["external_id"]


def test_8_passo4_mostra_cenas_do_youtube_que_cabem(env):
    from app.estimate import estimate
    with session_scope() as s:
        s.add(YtQuota(day=quota.pacific_day(), used=9000))  # sobram 500 → 4 buscas
        s.commit()
    cfg = ProductionConfig(**Preset(real_pct=100, youtube_pct=50, avg_scene_seconds=6).model_dump(),
                           title="t", channel_name="t")
    q = estimate(cfg, "word " * 1500)["quotas"]
    # YouTube primeiro: as 100 cenas reais tentam o YouTube; só 4 cabem na cota que sobrou
    assert q["youtube_scenes"] == 100 and q["youtube_scenes_fit"] == 4 and q["youtube_available"] == 500


def test_visao_falhando_nao_derruba(env):
    def boom(*a, **k):
        raise RuntimeError("gemini fora")

    env["monkeypatch"].setattr(gemini, "rate_sheet", boom)
    entry = sel_mod.Selector(make_ctx()).select_scene(scene())
    assert entry["method"] == "text_fallback"


def test_sem_chave_gemini_usa_so_texto(env):
    env["monkeypatch"].setattr(gemini, "available", lambda: False)
    entry = sel_mod.Selector(make_ctx()).select_scene(scene())
    assert entry["method"] == "text" and entry["vision_calls"] == 0


def test_nota_baixa_usa_reserva_e_registra(env):
    env["vision"].mode = "low"
    ctx = make_ctx()
    entry = sel_mod.Selector(ctx).select_scene(scene())
    assert entry["score"] == 4.0 and "SCENE_LOW_SCORE" in issues(ctx)
    assert "QUERY_REWRITTEN" in issues(ctx)  # tentou reescrever antes de aceitar a reserva


def test_download_falhando_cai_para_proxima_fonte(env):
    def fake_search(query, per_page=15, lang="en"):
        return [Candidate(provider="youtube", external_id="yt1", title=f"{query} archive", duration=120.0,
                          width=1280, height=720, page_url="https://youtu.be/yt1", thumbnail=None, query=query,
                          preview_frames=["a", "b", "c"], frame_positions=[0.25, 0.5, 0.75])]

    def broken_segment(*a, **k):
        raise RuntimeError("yt-dlp falhou")

    env["monkeypatch"].setattr(yt, "search", fake_search)
    env["monkeypatch"].setattr(yt, "download_segment", broken_segment)
    ctx = make_ctx()
    entry = sel_mod.Selector(ctx).select_scene(scene(source="youtube"))
    assert entry["source_used"] == "stock"
    # YouTube primeiro: cair nos bancos é o caminho esperado, não uma migração
    assert "SCENE_NO_CANDIDATES" in issues(ctx) and "SCENE_MIGRATED" not in issues(ctx)


def test_download_tenta_proximo_colocado_antes_de_trocar_de_fonte(env):
    def fake_search(query, per_page=15, lang="en"):
        return [Candidate(provider="youtube", external_id=f"yt{i}", title=f"{query} archive", duration=120.0,
                          width=1280, height=720, page_url=f"https://youtu.be/yt{i}", thumbnail=None, query=query,
                          preview_frames=["a", "b", "c"], frame_positions=[0.25, 0.5, 0.75]) for i in range(4)]

    tried = []

    def segment(video_id, start, end, dest):
        tried.append(video_id)
        if len(tried) == 1:
            raise RuntimeError("Requested format is not available")  # vencedor quadrado
        dest.write_bytes(b"x")
        return dest

    env["monkeypatch"].setattr(yt, "search", fake_search)
    env["monkeypatch"].setattr(yt, "download_segment", segment)
    entry = sel_mod.Selector(make_ctx()).select_scene(scene(source="youtube"))
    assert entry["source_used"] == "youtube" and len(tried) == 2 and entry["external_id"] == tried[1]
