"""Fase A3/A4: cache de visão pela requisição efetiva e pelos bytes; descrição observável reaproveitável."""
import threading
import time

import pytest
from PIL import Image
from sqlmodel import delete

from app.db import session_scope
from app.models import AssetDescription, VisionCache
from app.pipeline.select import funnel
from app.pipeline.visual import VisionRow, VisionSheet
from app.providers.llm import vision
from app.providers.stock.base import Candidate


class Counter:
    def __init__(self, delay: float = 0.0):
        self.calls = 0
        self.delay = delay
        self.lock = threading.Lock()

    def __call__(self, image, prompt, model, schema=None, budget=None, record=None):
        with self.lock:
            self.calls += 1
        time.sleep(self.delay)
        n = prompt.count("\n") and int(prompt.split("contact sheet with ")[1].split(" ")[0])
        rows = [VisionRow(row=i, seen="red wooden barn in a field", realism="real_footage", subject_visible=True,
                          forbidden_present=[], context_match=8, quality=8, best_frame=0) for i in range(1, n + 1)]
        return VisionSheet(candidates=rows), 0.0, "gemini"


@pytest.fixture
def rater(monkeypatch):
    with session_scope() as s:
        s.exec(delete(VisionCache))
        s.exec(delete(AssetDescription))
        s.commit()
    counter = Counter()
    monkeypatch.setattr(vision, "rate_sheet", counter)
    monkeypatch.setattr(funnel, "build_sheet", lambda rows: ("|".join(str(f) for r in rows for f in r)).encode())
    return counter


def ctx(**kw) -> funnel.SceneContext:
    base = dict(intent="red barn at dawn", text="The old barn stood alone.", style="", previous="",
                subject="red barn", must_show=["barn"], must_avoid=["tractor"])
    base.update(kw)
    return funnel.SceneContext(**base)


def cand(i: str) -> Candidate:
    return Candidate(provider="pexels", external_id=i, title="barn", duration=10, width=1920, height=1080,
                     page_url="", thumbnail=None, preview_frames=[f"https://img/{i}/1.jpg"], frame_positions=[0.5])


def rate(c: funnel.SceneContext, cands=None, stage="sheet"):
    cands = cands or [cand("a"), cand("b")]
    return funnel._rate(stage, [x.preview_frames for x in cands], cands, c, "gemini-x", {})


def test_mesma_requisicao_usa_cache(rater):
    rate(ctx())
    rate(ctx())
    assert rater.calls == 1


@pytest.mark.parametrize("change", [
    {"text": "The barn burned down."},           # narrativa
    {"must_show": ["barn", "smoke"]},            # exigência
    {"must_avoid": ["tractor", "people"]},       # restrição
    {"previous": "close-up of a hay bale"},      # cena anterior
    {"context": {"setting_type": "historical", "era": "1850", "era_label": "mid-19th century"}},
    {"allowed_styles": ["real_footage", "painting_historical"]},
    {"meaning": "loss and abandonment"},
])
def test_cache_invalida_por_narrativa_e_requisitos(rater, change):
    rate(ctx())
    rate(ctx(**change))
    assert rater.calls == 2


def test_cache_invalida_por_conteudo_dos_frames(rater, monkeypatch):
    rate(ctx())
    monkeypatch.setattr(funnel, "build_sheet", lambda rows: b"other-bytes")  # mesmos candidatos, outros pixels
    rate(ctx())
    assert rater.calls == 2


def test_imagem_gerada_com_mesmo_prompt_e_bytes_diferentes_nao_herda(rater, monkeypatch, tmp_path):
    monkeypatch.setattr(funnel, "build_sheet", lambda rows: b"".join(p.read_bytes() for r in rows for p in r))
    a, b = tmp_path / "s001.png", tmp_path / "s001_retry.png"
    Image.new("RGB", (8, 8), (200, 0, 0)).save(a)
    Image.new("RGB", (8, 8), (0, 0, 200)).save(b)
    funnel.rate_local_image(a, ctx(), "gemini-x", {}, key_hint="s001.png:1:same prompt")
    funnel.rate_local_image(b, ctx(), "gemini-x", {}, key_hint="s001.png:1:same prompt")  # mesmo nome/tentativa/prompt
    assert rater.calls == 2
    funnel.rate_local_image(a, ctx(), "gemini-x", {})  # mesmos bytes de novo: cache
    assert rater.calls == 2


def test_troca_de_modelo_invalida(rater, monkeypatch):
    rate(ctx())
    monkeypatch.setattr(vision, "signature", lambda model: "auto|gemini=outro")
    rate(ctx())
    assert rater.calls == 2


def test_descricao_reaproveitada_sem_herdar_adequacao(rater):
    rate(ctx())
    other = [cand("a"), cand("b")]
    assert funnel.observed_descriptions(other, 1) == 2
    assert other[0].observed == "red wooden barn in a field"
    # outra cena, mesmos candidatos: a descrição existe, mas a adequação é julgada de novo
    rate(ctx(text="A storm hit the coast.", subject="storm", must_show=["waves"]))
    assert rater.calls == 2
    with session_scope() as s:
        d = s.get(AssetDescription, funnel.description_ref(other[0], other[0].preview_frames))
        assert d and not hasattr(d, "context_match") and not hasattr(d, "subject_visible")


def test_descricao_entra_no_ranking_de_texto_sem_virar_aprovacao(rater):
    from app.pipeline.select.prerank import text_score

    c = cand("z")
    c.title = "clip 1234"
    before = text_score(c, ["red barn"], "red barn at dawn", 5, "red barn")
    c.observed = "red wooden barn in a field"
    assert text_score(c, ["red barn"], "red barn at dawn", 5, "red barn") > before


def test_avaliacoes_identicas_simultaneas_fazem_uma_chamada(monkeypatch):
    with session_scope() as s:
        s.exec(delete(VisionCache))
        s.commit()
    slow = Counter(delay=0.4)
    monkeypatch.setattr(vision, "rate_sheet", slow)
    monkeypatch.setattr(funnel, "build_sheet", lambda rows: b"same")
    out = []
    threads = [threading.Thread(target=lambda: out.append(rate(ctx(), [cand("q")]))) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert slow.calls == 1 and len(out) == 5 and all(o[0]["score"] > 0 for o in out)
