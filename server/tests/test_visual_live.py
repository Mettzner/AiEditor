"""Testes com IA real (MELHORIA_PRECISAO_VISUAL.md §12). Gastam alguns centavos, por isso só rodam com
AIEDITOR_LIVE=1:

    $env:AIEDITOR_LIVE="1"; .venv\\Scripts\\python -m pytest tests -m live -q
"""
from __future__ import annotations

import io
import json
import os
import tempfile
from pathlib import Path

import pytest
from PIL import Image, ImageDraw, ImageFilter

from app.config import get_secret, load_settings
from app.pipeline.plan import PlanWindow, _validate
from app.pipeline.select.funnel import SceneContext, rate_local_image
from app.pipeline.visual import DEFAULT_GLOBAL_AVOID, PLAN_SYSTEM

pytestmark = pytest.mark.live
LIVE = os.environ.get("AIEDITOR_LIVE") == "1"
needs_claude = pytest.mark.skipif(not (LIVE and get_secret("anthropic")), reason="AIEDITOR_LIVE=1 + chave Anthropic")
needs_gemini = pytest.mark.skipif(not (LIVE and get_secret("gemini")), reason="AIEDITOR_LIVE=1 + chave Gemini")

BRIEF = {"topic": "natural remedies and everyday nutrition", "audience": "adults interested in natural health",
         "tone": "warm, informative", "region_culture": "everyday homes and kitchens",
         "visual_world": "real kitchens, markets, gardens, ordinary people",
         "recurring_entities": [], "global_avoid": DEFAULT_GLOBAL_AVOID}


def plan_units(sentences: list[str], media_style: str = "real_preferred") -> list[dict]:
    """Mesma estrutura de produção: sistema fixo + contexto (configurações + unidades) + pedido com o brief."""
    from app.lang import name as lang_name
    from app.pipeline.visual import MEDIA_STYLE_RULES, NUMBER_HINTS, brief_section
    from app.providers.llm.base import call_llm

    listing = "\n".join(f"[{i}|{i * 5:.1f}-{i * 5 + 5:.1f}] {t}" for i, t in enumerate(sentences))
    context = ("PRODUCTION SETTINGS\n- Title: test\n- Target average scene duration: 5.0s, varying ±30%.\n"
               f"- Video language: {lang_name('en')} (en). Number format: {NUMBER_HINTS['en'].strip()}\n"
               f"- Channel media style: {MEDIA_STYLE_RULES[media_style]}\n- Channel visual style: (none)\n"
               f"- Direction:\n\nSCRIPT UNITS\n{listing}")
    user = f"{brief_section(BRIEF)}\nPlan units 0 to {len(sentences) - 1} (window 1 of 1)."
    parsed, _ = call_llm("plan", system=PLAN_SYSTEM, context=context, user=user, schema=PlanWindow,
                         max_tokens=16000)
    return [sc.model_dump() for sc in _validate(parsed, 0, len(sentences) - 1, media_style).scenes]


def plan_one(sentence: str, media_style: str = "real_preferred") -> dict:
    return plan_units([sentence], media_style)[0]


def words(scene: dict) -> str:
    return " ".join([scene["subject"]] + scene["queries"]).lower()


@needs_claude
@pytest.mark.parametrize("sentence,check", [
    ("Frozen fruit keeps most of its nutrients.",
     lambda s: any(w in s["subject"].lower() for w in ("fruit", "berr", "strawberr")) and
     any("freezer" in a.lower() for a in s["must_avoid"]) and not any("freezer" in q for q in s["queries"])),
    ("He froze in fear when he heard the noise.",
     lambda s: s["literal"] is False and any(w in s["subject"].lower() for w in ("man", "person", "scared",
                                                                                 "frightened", "afraid")) and
     any("ice" in a.lower() for a in s["must_avoid"])),
    ("The root of the problem is what we eat.",
     lambda s: s["literal"] is False and "root" not in s["subject"].lower() and
     any(w in s["subject"].lower() for w in ("food", "meal", "plate", "eating", "junk", "burger"))),
    ("It was a real battle against the flu.",
     lambda s: any(w in s["subject"].lower() for w in ("sick", "ill", "flu", "patient", "person", "woman", "man")) and
     any(w in " ".join(s["must_avoid"]).lower() for w in ("soldier", "war", "battle"))),
    ("This tea is a game changer.",
     lambda s: "tea" in s["subject"].lower() and not any("game" in q.split() for q in s["queries"])),
    ("Cold remedies our grandmothers used.",
     lambda s: any(w in s["subject"].lower() for w in ("remed", "tea", "honey", "herb", "grandmother", "soup")) and
     any("snow" in a.lower() for a in s["must_avoid"])),
    ("Your energy levels will improve.",
     lambda s: s["subject_category"] in ("person", "activity") and
     not any(w in words(s) for w in ("energy", "levels", "improve", "health"))),
])
def test_planejamento_etapa_b(sentence, check):
    scene = plan_one(sentence)
    assert check(scene), json.dumps({k: scene[k] for k in ("literal", "subject", "subject_category", "must_avoid",
                                                           "queries")}, ensure_ascii=False)
    assert all(scene["subject"].split()[-1].lower() in q or q.startswith(scene["subject"].lower())
               for q in scene["queries"])


# ---------------------------------------------------------------- Etapa E com imagens fixas
def _gameplay() -> Path:
    img = Image.new("RGB", (1280, 720), (40, 120, 60))
    d = ImageDraw.Draw(img)
    for x in range(0, 1280, 64):
        d.polygon([(x, 500), (x + 32, 380), (x + 64, 500)], fill=(30, 90, 40))
    d.rectangle((20, 20, 320, 50), fill=(60, 0, 0))
    d.rectangle((20, 20, 260, 50), fill=(220, 30, 30))
    d.text((30, 26), "HP 82/100", fill="white")
    d.rectangle((1080, 20, 1260, 200), fill=(0, 0, 0), outline="white", width=3)
    d.text((1100, 210), "MINIMAP", fill="white")
    d.text((560, 680), "[E] PICK UP STRAWBERRY  x3", fill="white")
    d.ellipse((600, 420, 680, 500), fill=(230, 20, 40))
    return _save(img, "gameplay.png")


def _render3d() -> Path:
    img = Image.new("RGB", (1280, 720), (235, 235, 240))
    for k in range(60, 0, -1):  # esfera com degradê liso, cara de render
        c = 255 - k * 3
        ImageDraw.Draw(img).ellipse((640 - k * 4, 360 - k * 4, 640 + k * 3, 360 + k * 3), fill=(c, 20, 40))
    return _save(img.filter(ImageFilter.GaussianBlur(1)), "render3d.png")


def _save(img: Image.Image, name: str) -> Path:
    p = Path(tempfile.mkdtemp()) / name
    img.save(p)
    return p


def _pexels_photo(query: str) -> Path:
    from app.providers.http import download
    from app.providers.stock.pexels import Pexels

    c = Pexels().search_photos(query, per_page=3)[0]
    p = Path(tempfile.mkdtemp()) / "photo.jpg"
    download(c.thumbnail or c.renditions[0].url, p)
    return p


FRUIT = SceneContext(intent="close-up of frozen strawberries with frost in a bowl", text="Frozen fruit keeps most "
                     "of its nutrients.", style="", previous="", subject="frozen strawberries",
                     must_show=["strawberries", "frost"], must_avoid=["freezer", "refrigerator"] + DEFAULT_GLOBAL_AVOID,
                     topic="frozen fruit benefits", visual_world="real kitchens")


def rate(path: Path) -> dict:
    from app.providers.llm.gemini import GeminiQuotaExhausted

    try:
        return rate_local_image(path, FRUIT, load_settings()["selection"]["gemini_model"], {}, key_hint=str(path))
    except GeminiQuotaExhausted as e:
        pytest.skip(f"cota do Gemini esgotada hoje: {e}")


@needs_gemini
def test_visao_gameplay_e_render_valem_zero():
    for p in (_gameplay(), _render3d()):
        r = rate(p)
        assert r["realism"] != "real_footage" and r["score"] == 0, r


@needs_gemini
@pytest.mark.skipif(not get_secret("pexels"), reason="precisa da chave do Pexels para baixar as fotos")
def test_visao_freezer_reprova_e_morango_aprova():
    freezer = rate(_pexels_photo("chest freezer appliance"))
    assert freezer["score"] <= 3, freezer
    berries = rate(_pexels_photo("frozen strawberries"))
    assert berries["score"] >= 7, berries


# ---------------------------------------------------------------- AJUSTE_ESTILO_CONTEXTUAL.md §9
@needs_claude
@pytest.mark.parametrize("sentence,media_style,check", [
    ("Frozen fruit keeps most of its nutrients.", "real_preferred",
     lambda s: s["style_allowance"] == "real_only" and s["allowed_styles"] == ["real_footage"]),
    ("Inside your cells, vitamin C neutralizes free radicals.", "real_preferred",
     lambda s: s["style_allowance"] == "stylized_ok" and {"cgi_3d", "animation"} & set(s["allowed_styles"])),
    ("In 1720, sailors used lime juice to fight scurvy.", "real_preferred",
     lambda s: s["style_allowance"] in ("real_preferred", "stylized_ok") and
     "painting_historical" in s["allowed_styles"]),
    ("Locals say a creature walks these woods at night.", "real_preferred",
     lambda s: "illustration" in s["allowed_styles"] or s["style_allowance"] != "real_only"),
    ("Kids spend hours playing video games.", "real_preferred",
     lambda s: "real_footage" in s["allowed_styles"] and s["subject_category"] in ("person", "activity")),
    ("This tea is a game changer.", "real_preferred",
     lambda s: s["style_allowance"] == "real_only" and not any("game" in q.split() for q in s["queries"])),
    ("Inside your cells, vitamin C neutralizes free radicals.", "real_only",
     lambda s: s["style_allowance"] == "real_only" and s["allowed_styles"] == ["real_footage"]),
])
def test_estilo_contextual(sentence, media_style, check):
    scene = plan_one(sentence, media_style)
    assert check(scene), json.dumps({k: scene[k] for k in ("subject", "style_allowance", "allowed_styles",
                                                           "style_reason", "queries")}, ensure_ascii=False)


# ---------------------------------------------------------------- CONTEXTO_PROFUNDO_DO_ROTEIRO.md §9
def plan_with_bible(sentences: list[str], media_style: str = "real_preferred") -> tuple[dict, list[dict]]:
    """Mesma chamada da produção na 1ª janela: Bíblia de Contexto + cenas."""
    from app.lang import name as lang_name
    from app.pipeline.context import finish_bible
    from app.pipeline.context import ContextBible
    from app.pipeline.plan import BIBLE_REQUEST, PlanWindow
    from app.pipeline.visual import MEDIA_STYLE_RULES, NUMBER_HINTS, brief_section
    from app.providers.llm.base import call_llm

    units = [{"i": i, "start": i * 5.0, "end": i * 5.0 + 5.0, "text": t} for i, t in enumerate(sentences)]
    listing = "\n".join(f"[{u['i']}|{u['start']:.1f}-{u['end']:.1f}] {u['text']}" for u in units)
    context = ("PRODUCTION SETTINGS\n- Title: test\n- Target average scene duration: 5.0s, varying ±30%.\n"
               f"- Video language: {lang_name('en')} (en). Number format: {NUMBER_HINTS['en'].strip()}\n"
               f"- Channel media style: {MEDIA_STYLE_RULES[media_style]}\n- Channel visual style: (none)\n"
               f"- Direction:\n\nSCRIPT UNITS\n{listing}")
    raw, _ = call_llm("plan", system=PLAN_SYSTEM, context=context, schema=ContextBible, max_tokens=8000,
                      user=BIBLE_REQUEST.format(first=0, last=len(units) - 1))
    bible = finish_bible(raw.model_dump(), len(units), units)
    user = f"{brief_section(bible)}\nPlan units 0 to {len(sentences) - 1} (window 1 of 1)."
    parsed, _ = call_llm("plan", system=PLAN_SYSTEM, context=context, user=user, schema=PlanWindow,
                         max_tokens=16000)
    window = _validate(parsed, 0, len(units) - 1, media_style, bible)
    return bible, [sc.model_dump() for sc in window.scenes]


def _blocks(bible: dict) -> list[dict]:
    from app.pipeline.context import blocks

    return blocks(bible)


@needs_claude
def test_contexto_1850_irlanda_rural():
    bible, scenes = plan_with_bible(["In 1850, Irish farmers watched their potato crops rot in the fields."])
    b = _blocks(bible)[0]
    assert b["setting_type"] == "historical" and "1850" in b["era"] and b["era_confidence"] == "explicit", b
    assert "ireland" in b["place"].lower() and b["footage_feasibility"] == "pre_film"
    assert any(w in " ".join(b["anachronisms"]).lower() for w in ("tractor", "car", "power line", "modern"))
    qs = " ".join(scenes[0]["queries"]).lower()
    assert "1850" not in qs and any(v.split()[0].lower() in qs for v in b["search_vocabulary"]), scenes[0]["queries"]
    assert scenes[0]["timeless_query"] and scenes[0]["archival_query"]


@needs_claude
def test_epoca_inferida_sem_ano():
    bible, _ = plan_with_bible(["Back then, people lit their homes with oil lamps and walked miles to the market."])
    from app.pipeline.context import era_year

    b = _blocks(bible)[0]
    assert b["setting_type"] == "historical" and b["era_confidence"] == "inferred", b
    year = era_year(b["era"]) or era_year(b["era_label"])
    assert year and 1700 <= year < 1940, b


@needs_claude
def test_dois_blocos_1920_e_hoje():
    bible, scenes = plan_with_bible([
        "In the 1920s, doctors prescribed this tonic to tired patients.",
        "Families kept a bottle of it in every kitchen cabinet.",
        "Today, scientists in modern laboratories confirm it works.",
        "Their studies show real benefits for energy and sleep."])
    b = _blocks(bible)
    assert len(b) == 2, b
    assert b[0]["footage_feasibility"] == "early_film" and b[1]["footage_feasibility"] == "contemporary"
    assert scenes[-1]["context_id"] == b[1]["id"]


@needs_claude
def test_sem_epoca_e_atemporal():
    bible, _ = plan_with_bible(["Grandma would gather the leaves before sunrise."])
    b = _blocks(bible)[0]
    assert b["setting_type"] == "timeless" and b["footage_feasibility"] == "timeless", b


@needs_claude
def test_lugar_caribenho():
    bible, scenes = plan_with_bible(["On a small farm in rural Jamaica, the old remedy was passed down."])
    b = _blocks(bible)[0]
    world = " ".join([b["place"], b["landscape"], b["climate"]]).lower()
    assert "jamaica" in world and any(w in world for w in ("tropical", "caribbean", "humid"))
    assert "snow" not in world and "europe" not in world.replace("european-style", "")


@needs_gemini
@pytest.mark.skipif(not get_secret("pexels"), reason="precisa da chave do Pexels para baixar a foto")
def test_foto_moderna_reprovada_em_cena_de_1850():
    from app.pipeline.select.funnel import SceneContext as SC
    from app.providers.llm.gemini import GeminiQuotaExhausted

    ctx = SC(intent="irish farmers in a potato field", text="In 1850, Irish farmers watched their crops rot.",
             style="", previous="", subject="farmer in a field", must_show=[], must_avoid=["tractor"],
             topic="potato famine", visual_world="rural Ireland 1850", allowance="real_preferred",
             allowed_styles=["real_footage", "painting_historical"],
             context={"setting_type": "historical", "era": "1850", "era_label": "mid-19th century",
                      "place": "rural Ireland", "clothing": "wool coats, shawls",
                      "architecture": "stone cottages with thatched roofs", "lighting": "daylight",
                      "transport": "horse-drawn carts",
                      "anachronisms": ["tractors", "cars", "power lines", "modern clothing", "asphalt"]})
    try:
        r = rate_local_image(_pexels_photo("farmer tractor field"), ctx, load_settings()["selection"]["gemini_model"],
                             {}, key_hint="tractor-1850")
    except GeminiQuotaExhausted as e:
        pytest.skip(f"cota do Gemini esgotada hoje: {e}")
    assert r["score"] == 0 and r["anachronisms_seen"], r


@needs_claude
def test_bloco_de_cenas_celulares_no_mesmo_estilo():
    sentences = [
        "Every morning you eat breakfast without thinking about it.",
        "But inside your body, something remarkable starts.",
        "Vitamin C enters the bloodstream and travels to your cells.",
        "Inside each cell, it finds unstable molecules called free radicals.",
        "It gives them an electron and neutralizes them.",
        "The cell membrane stays protected and keeps working.",
        "Back in your kitchen, a simple orange made this possible.",
    ]
    scenes = plan_units(sentences)
    inner = [sc for sc in scenes if sc["style_allowance"] == "stylized_ok"]
    assert inner, scenes
    first_styles = {next(s for s in sc["allowed_styles"] if s != "real_footage") for sc in inner}
    assert len(first_styles) == 1, [(sc["text"], sc["allowed_styles"]) for sc in scenes]
    assert scenes[0]["style_allowance"] == "real_only"
