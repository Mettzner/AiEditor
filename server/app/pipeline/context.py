"""Bíblia de Contexto (CONTEXTO_PROFUNDO_DO_ROTEIRO.md): quando, onde, com quem e em que ambiente cada trecho
do roteiro acontece.

Gerada uma vez por produção, na mesma chamada que planeja a primeira janela de cenas (o prefixo em cache é o
mesmo, então não há chamada extra). Toda cena herda o bloco de contexto em que está; buscas, prompts de imagem,
validação visual e render derivam desse contexto.
"""
from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel

SettingType = Literal["historical", "contemporary", "timeless"]
Feasibility = Literal["pre_photo", "pre_film", "early_film", "historical_modern", "contemporary", "timeless"]
Confidence = Literal["explicit", "inferred", "none"]

HISTORICAL = ("pre_photo", "pre_film", "early_film", "historical_modern")
DEFAULT_GLOBAL_AVOID = ["text on screen", "watermarks", "logos", "existing characters or franchises"]
# o que nunca pode aparecer numa imagem gerada de época, além dos anacronismos do bloco (§7)
MODERN_GENERIC = ["modern clothing", "modern buildings", "electric lights", "cars", "plastic"]


class ContextBlock(BaseModel):
    id: str
    first_unit: int
    last_unit: int
    setting_type: SettingType
    era: str
    era_label: str
    era_confidence: Confidence
    era_evidence: str
    place: str
    place_confidence: Confidence
    climate: str
    landscape: str
    society: str
    clothing: str
    architecture: str
    interiors: str
    lighting: str
    transport: str
    objects: list[str]
    era_markers_to_show: list[str]
    anachronisms: list[str]
    search_vocabulary: list[str]
    palette: str
    mood: str
    footage_feasibility: Feasibility
    card_text: str


class BibleEntity(BaseModel):
    name: str
    look: str
    contexts: list[str]


class ContextBible(BaseModel):
    topic: str
    audience: str
    tone: str
    region_culture: str
    visual_world: str
    contexts: list[ContextBlock]
    recurring_entities: list[BibleEntity]
    recurring_places: list[BibleEntity]
    global_avoid: list[str]


BIBLE_RULES = """CONTEXT BIBLE, written once from the WHOLE script, before any scene, when the request asks for it. It says WHEN,
WHERE, WITH WHOM and IN WHAT ENVIRONMENT each part of the script happens. Every image must be coherent with it.
- topic (a few words), audience, tone (two or three adjectives), visual_world (kinds of places, objects and people
  that fit), region_culture (one line: place, culture and era).
- contexts: split the script into context blocks by unit ranges (first_unit, last_unit): contiguous, in order,
  covering ALL units. Start a new block only when the era or the place really changes (1850 farmers → present-day
  scientists). Usually one block, rarely more than four. ids "ctx1", "ctx2"...
  For each block:
  * setting_type: "historical" (the narration is about the past, even in the present tense: "In 1850, families
    eat..." is historical), "contemporary" (today) or "timeless" (no sign of any era).
  * era: the most specific time ("1850", "1920s", "late 18th century", "present day", "timeless"); era_label: a
    descriptive label ("mid-19th century", "Victorian era", "contemporary"); era_confidence: "explicit" (stated in
    the script), "inferred" (deduced from clues: oil lamps, carriages, telegrams, a war) or "none"; era_evidence:
    the clue from the script, or "".
  * When in doubt, do NOT modernize: signs of the past without a year → infer the period (oil lamps, walking miles
    to the market → 19th century). No sign at all → "timeless", never "present day" by default.
  * place: specific ("rural Ireland", "Victorian England, urban working class", "rural Jamaica"); place_confidence;
    climate; landscape (relief and vegetation).
  * society: social class, occupations and population as the script implies.
  * Material culture OF THAT ERA AND PLACE: clothing (including hair), architecture, interiors, lighting (the light
    sources), transport, objects (tools, food, remedies, technology).
  * era_markers_to_show: 2 to 4 visible elements that prove the era ("thatched roofs", "period wool clothing",
    "horse-drawn cart"); [] for contemporary and timeless blocks.
  * anachronisms: 6 to 12 short items that did NOT exist there or would clash ("cars", "power lines", "electric
    lights", "plastic", "asphalt", "phones", "wristwatches", "modern clothing", "sneakers", "zippers"). Contemporary
    block: things that would look like another era or another place (can be short). Timeless block: clearly modern
    items ("phones", "cars", "screens", "plastic packaging") and clearly antique costume.
  * search_vocabulary: 3 to 6 words or short phrases that stock-footage sites understand for this era and place.
    Sites do not understand years: "victorian", "19th century", "period costume", "reenactment", "old west",
    "pioneer", "colonial era", "medieval", "1920s", "vintage", "archival footage", "caribbean village", "tropical".
    [] for timeless blocks.
  * palette: colors and light coherent with era and tone; mood: the emotional atmosphere of the block.
  * footage_feasibility: "pre_photo" (before ~1840), "pre_film" (~1840-1890), "early_film" (~1890-1940),
    "historical_modern" (~1940-2000), "contemporary", "timeless".
  * card_text: a short place-and-date card IN THE VIDEO LANGUAGE ("Rural Ireland, 1850" in English, "Irlanda
    rural, 1850" in Portuguese); "" for contemporary or timeless blocks without a meaningful place.
- recurring_entities: people or things that appear in several scenes, each with a concrete look OF ITS ERA (age,
  appearance, clothing) and the ids of the blocks where they appear. [] if none.
- recurring_places: places that repeat, each with a fixed description and block ids. [] if none.
- global_avoid: text on screen, watermarks, logos, existing characters or franchises, plus likely confusions.
- The script is the source of truth: infer only what is coherent with it and never contradict it. Be specific
  ("Victorian England, urban working class" beats "19th century", which beats "old"). Represent people as the script
  and the historical and geographic context indicate, with respect, never as caricature or stereotype."""


# ---------------------------------------------------------------- época → viabilidade (§3)

_CENTURY = re.compile(r"\b(\d{1,2})(?:st|nd|rd|th)\s+century\b")
_YEAR = re.compile(r"\b(1\d{3}|20\d{2})(s?)\b")
_ANCIENT = ("medieval", "middle ages", "ancient", "roman", "renaissance", "viking", "bc", "b.c.", "antiquity",
            "bronze age", "iron age", "stone age", "prehistoric", "colonial era", "feudal")


def era_year(era: str) -> int | None:
    """Ano aproximado da época ("1850" → 1850; "1920s" → 1925; "mid-19th century" → 1850)."""
    text = (era or "").lower()
    m = _YEAR.search(text)
    if m:
        return int(m.group(1)) + (5 if m.group(2) else 0)
    m = _CENTURY.search(text)
    if m:
        base = (int(m.group(1)) - 1) * 100
        if "early" in text:
            return base + 15
        if "late" in text:
            return base + 85
        return base + 50
    if any(w in text for w in _ANCIENT):
        return 1000
    return None


def feasibility_for(setting_type: str, era: str, era_label: str, fallback: str) -> str:
    """Classe de viabilidade coerente com a tabela do §3; o ano explícito vence a resposta do LLM."""
    if setting_type == "contemporary":
        return "contemporary"
    if setting_type == "timeless":
        return "timeless"
    year = era_year(era) or era_year(era_label)
    if year is None:
        return fallback if fallback in HISTORICAL else "pre_film"
    if year < 1840:
        return "pre_photo"
    if year < 1890:
        return "pre_film"
    if year < 1940:
        return "early_film"
    if year < 2000:
        return "historical_modern"
    return "contemporary"


def decade_of(era: str, era_label: str = "") -> str:
    year = era_year(era) or era_year(era_label)
    return f"{year // 10 * 10}s" if year and year >= 1840 else (era or era_label)


# ---------------------------------------------------------------- normalização

def neutral_bible(title: str, visual_style: str, n_units: int) -> dict:
    """Usada quando o LLM falha: um único bloco atemporal (na dúvida, não modernizar)."""
    block = ContextBlock(
        id="ctx1", first_unit=0, last_unit=max(0, n_units - 1), setting_type="timeless", era="timeless",
        era_label="timeless", era_confidence="none", era_evidence="", place="everyday neutral setting",
        place_confidence="none", climate="", landscape="", society="", clothing="", architecture="", interiors="",
        lighting="natural light", transport="", objects=[], era_markers_to_show=[],
        anachronisms=["phones", "screens", "cars", "plastic packaging"], search_vocabulary=[], palette="", mood="",
        footage_feasibility="timeless", card_text="")
    bible = ContextBible(topic=title, audience="general audience", tone="informative",
                         region_culture="everyday neutral setting", visual_world=visual_style or "real life",
                         contexts=[block], recurring_entities=[], recurring_places=[], global_avoid=[])
    return finish_bible(bible.model_dump(), n_units)


def _blocks(bible: dict) -> list[dict]:
    """Blocos em lista, aceitando o formato salvo (context_timeline + contexts por id) ou o do LLM (lista)."""
    ctxs = bible.get("contexts")
    if isinstance(ctxs, list):
        return [dict(c) for c in ctxs]
    if isinstance(ctxs, dict):
        out = []
        for t in bible.get("context_timeline") or []:
            c = dict(ctxs.get(t["id"]) or {})
            c.update(id=t["id"], first_unit=t["units"][0], last_unit=t["units"][1],
                     setting_type=t.get("setting_type", c.get("setting_type", "timeless")))
            out.append(c)
        return out
    return []


def finish_bible(raw: dict, n_units: int, units: list[dict] | None = None) -> dict:
    """Normaliza a bíblia: blocos contíguos cobrindo todas as unidades, viabilidade coerente com a época,
    global_avoid padrão. Devolve o formato salvo em context_bible.json (linha do tempo + contextos por id)."""
    blocks = sorted(_blocks(raw), key=lambda c: c.get("first_unit", 0))
    last = max(0, n_units - 1)
    if not blocks:
        return neutral_bible(raw.get("topic", ""), raw.get("visual_world", ""), n_units)
    fixed, expected = [], 0
    for b in blocks:
        if b.get("last_unit", 0) < expected and fixed:
            continue
        b["first_unit"] = expected
        b["last_unit"] = min(max(int(b.get("last_unit", expected)), expected), last)
        fixed.append(b)
        expected = b["last_unit"] + 1
        if expected > last:
            break
    fixed[-1]["last_unit"] = last
    seen_ids: set[str] = set()
    for n, b in enumerate(fixed, start=1):
        if not b.get("id") or b["id"] in seen_ids:
            b["id"] = f"ctx{n}"
        seen_ids.add(b["id"])
        b["footage_feasibility"] = feasibility_for(b.get("setting_type", "timeless"), b.get("era", ""),
                                                   b.get("era_label", ""), b.get("footage_feasibility", ""))
        if b["footage_feasibility"] == "contemporary" and b.get("setting_type") == "historical":
            b["setting_type"] = "contemporary"
        if b.get("setting_type") != "historical":
            b["era_markers_to_show"] = []
        b["search_vocabulary"] = [v for v in b.get("search_vocabulary") or [] if not re.fullmatch(r"\d{4}", v.strip())]
    timeline = []
    for b in fixed:
        item = {"id": b["id"], "units": [b["first_unit"], b["last_unit"]], "era": b.get("era"),
                "era_label": b.get("era_label"), "place": b.get("place"), "setting_type": b.get("setting_type")}
        if units:
            item["time"] = f"{units[b['first_unit']]['start']:.1f}-{units[b['last_unit']]['end']:.1f}s"
        timeline.append(item)
    contexts = {b["id"]: {k: v for k, v in b.items() if k not in ("id", "first_unit", "last_unit")} for b in fixed}
    out = {k: raw.get(k, "") for k in ("topic", "audience", "tone", "region_culture", "visual_world")}
    out.update(context_timeline=timeline, contexts=contexts,
               recurring_entities=list(raw.get("recurring_entities") or []),
               recurring_places=list(raw.get("recurring_places") or []),
               global_avoid=list(dict.fromkeys(DEFAULT_GLOBAL_AVOID + list(raw.get("global_avoid") or []))))
    return out


def blocks(bible: dict) -> list[dict]:
    """Blocos do formato salvo, com id e faixa de unidades."""
    return _blocks(bible)


def block_for_unit(bible: dict, unit: int) -> dict | None:
    for b in _blocks(bible):
        if b["first_unit"] <= unit <= b["last_unit"]:
            return b
    bl = _blocks(bible)
    return bl[-1] if bl else None


def block_by_id(bible: dict, ctx_id: str | None) -> dict | None:
    return next((b for b in _blocks(bible) if b["id"] == ctx_id), None)


# campos do contexto copiados para cada cena do plan.json (o resto do pipeline lê só a cena)
SCENE_CONTEXT_FIELDS = ("setting_type", "era", "era_label", "place", "climate", "landscape", "society", "clothing",
                        "architecture", "interiors", "lighting", "transport", "objects", "era_markers_to_show",
                        "anachronisms", "search_vocabulary", "palette", "footage_feasibility", "card_text")


def scene_context(block: dict | None) -> dict:
    if not block:
        return {}
    return {"id": block["id"], **{k: block.get(k) for k in SCENE_CONTEXT_FIELDS}}


def is_historical(scene: dict) -> bool:
    return (scene.get("context") or {}).get("footage_feasibility") in HISTORICAL


def scene_anachronisms(scene: dict) -> list[str]:
    return list((scene.get("context") or {}).get("anachronisms") or [])


# ---------------------------------------------------------------- estratégias por época (§3)

STRATEGIES = {
    "pre_photo": ["period_reenactment", "archival_art", "timeless", "ai_period"],
    "pre_film": ["period_reenactment", "archival_art", "timeless", "ai_period"],
    "early_film": ["archival_film", "period_reenactment", "timeless", "ai_period"],
    "historical_modern": ["archival_film", "period_reenactment", "timeless", "ai_period"],
}
STRATEGY_LABEL = {"period_reenactment": "reconstituição", "archival_art": "arquivo", "archival_film": "arquivo",
                  "timeless": "atemporal", "ai_period": "IA"}


def strategy_order(feasibility: str | None, media_style: str = "real_preferred") -> list[str]:
    """Ordem de estratégias da cena histórica. "Só real" sem foto possível (pre_photo) não usa arte de época."""
    order = list(STRATEGIES.get(feasibility or "", []))
    if media_style == "real_only" and feasibility == "pre_photo":
        order = [s for s in order if s != "archival_art"]
    return order


def period_allowed_styles(feasibility: str | None, allowance: str, allowed: list[str],
                          media_style: str) -> tuple[str, list[str]]:
    """§4: antes de ~1890 a arte da época (pintura, gravura, ilustração) entra como permitida, salvo "Só real"."""
    if media_style == "real_only" or feasibility not in ("pre_photo", "pre_film"):
        return allowance, allowed
    out = list(dict.fromkeys(list(allowed) + ["painting_historical", "illustration"]))
    if "real_footage" not in out:
        out.insert(0, "real_footage")
    return ("real_preferred" if allowance == "real_only" else allowance), out


# ---------------------------------------------------------------- gradação por bloco (§8)

def grade_for(feasibility: str | None, period_look: str = "cinematic") -> str | None:
    if feasibility in ("pre_photo", "pre_film"):
        return "sepia" if period_look == "archival" and feasibility == "pre_film" else "period_warm"
    if feasibility == "early_film":
        return "bw" if period_look == "archival" else "early_film"
    if feasibility == "historical_modern":
        return "vintage"
    return None


GRADE_FILTERS = {
    "period_warm": "eq=saturation=0.78:contrast=1.04:gamma=0.98,colorbalance=rs=0.05:gs=0.02:bs=-0.05:rm=0.04:bm=-0.03",
    "sepia": "colorchannelmixer=.393:.769:.189:0:.349:.686:.168:0:.272:.534:.131,eq=contrast=1.05",
    "early_film": "eq=saturation=0.6:contrast=1.05,colorbalance=rs=0.04:bs=-0.04",
    "bw": "hue=s=0,eq=contrast=1.08,noise=alls=7:allf=t",
    "vintage": "eq=saturation=0.88,colorbalance=rs=0.03:bs=-0.03",
}
