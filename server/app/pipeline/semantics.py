"""Interpretação semântica tipada do roteiro (Fase B): entidades, afirmações, papel visual e reconciliação.

Nada aqui chama IA. A Bíblia de Contexto (1 chamada por vídeo) devolve entidades e afirmações; cada janela do
planejamento devolve, por cena, o papel visual, a identidade exigida e as referências (entity_ids, claim_ids,
trecho de evidência). Este módulo normaliza e VALIDA essas saídas de forma determinística:
- IDs estáveis (ent01, clm01) e fusão de entidades duplicadas por nome/alias;
- o trecho de evidência precisa existir na narração da cena (senão vira pendência, não é inventado);
- afirmação negada vira "não sugerir" na cena; metáfora nunca é tratada como evidência exata;
- nome popular ambíguo sem nome científico fica pendente quando a cena exige a espécie;
- gráfico só com dados E fonte (nunca gerado por IA de imagem);
- reconciliação final: contradições entre afirmações, entidades citadas que não existem, ambiguidades importantes.
Uma afirmação do roteiro é uma ALEGAÇÃO (needs_source), nunca um fato verificado.

Correções estruturais ficam registradas em `unresolved`/notas: o significado não é reparado em silêncio.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Literal

from pydantic import BaseModel

VisualRole = Literal["exact_evidence", "contextual_illustration", "explanation", "metaphor", "reconstruction",
                     "comparison", "data_explanation"]
RequiredIdentity = Literal["none", "generic", "category", "species", "exact_person", "exact_event", "exact_place",
                           "exact_object"]
InterpConfidence = Literal["explicit", "inferred", "uncertain"]
NarrativeTime = Literal["past", "present", "future", "timeless", "mixed"]
Modality = Literal["asserted", "negated", "possible", "hypothetical", "reported", "question"]
EntityKind = Literal["person", "animal", "plant", "object", "place", "event", "organization", "concept", "other"]

VISUAL_ROLES = VisualRole.__args__  # type: ignore[attr-defined]
EXACT_ROLES = {"exact_evidence"}


class ScriptEntity(BaseModel):
    id: str = ""
    name: str
    kind: EntityKind = "other"
    aliases: list[str] = []
    scientific_name: str = ""
    real_identifiable: bool = False  # pessoa, lugar ou evento real e identificável
    ambiguous_name: bool = False  # nome popular que serve a mais de uma espécie/coisa
    explicit_attributes: list[str] = []  # ditos no roteiro
    inferred_attributes: list[str] = []  # deduzidos (artísticos, nunca documentais)
    evidence_units: list[int] = []


class ScriptClaim(BaseModel):
    id: str = ""
    units: list[int] = []
    quote: str = ""
    subject: str = ""
    relation: str = ""
    object: str = ""
    modality: Modality = "asserted"
    confidence: InterpConfidence = "explicit"
    needs_source: bool = True


class Ambiguity(BaseModel):
    units: list[int] = []
    question: str
    importance: Literal["low", "high"] = "low"


class DataPoint(BaseModel):
    label: str
    value: float
    unit: str = ""


BIBLE_SEMANTICS_RULES = """- entities: EVERY person, animal, plant, object, place, event or organization the script refers to (not only the
  recurring ones), resolving pronouns and indirect references ("she", "the plant", "that night") to the same
  entity. name: as written in the script; aliases: other names in the script and common regional names;
  scientific_name: only when the script states it or the identity is certain from the script, else "";
  real_identifiable: true for real, identifiable people, places and events; ambiguous_name: true when the common
  name fits several species or things and the script does not disambiguate; explicit_attributes: only what the
  script says; inferred_attributes: what you deduce (artistic, never documentary); evidence_units: unit numbers.
- claims: the factual statements of the script, each with units, quote (exact words from those units), subject,
  relation, object, modality ("asserted", "negated" for "is not/never", "possible", "hypothetical", "reported",
  "question"), confidence and needs_source (true for any fact a viewer could check). A claim is what the script
  SAYS, not a verified truth. Metaphors are not claims.
- ambiguities: questions the script leaves open that change what should be shown (which species, which person,
  which year), with importance "high" when a wrong guess would show the wrong thing. [] if none.
- contexts[].narrative_time: "past", "present", "future", "timeless" or "mixed", as NARRATED (an event of 2005
  told today is "past", even though modern footage can show it). Never infer an era from a single object."""

PLAN_SEMANTICS_RULES = """INTERPRETATION FIELDS (every scene)
- entity_ids: ids of the bible entities the scene is about (resolve pronouns: "she" → the right entity id).
- claim_ids: ids of the bible claims stated in this scene's units, else [].
- evidence_quote: the exact words of THIS scene's narration that justify the image (copy, do not paraphrase).
- visual_role: "exact_evidence" (must show the exact real event, person or object), "contextual_illustration"
  (a generic image of the kind of thing), "explanation" (shows how something works), "metaphor" (figurative
  sentence), "reconstruction" (reenactment of something not filmed), "comparison" (two contexts on purpose),
  "data_explanation" (numbers that a chart explains better).
- required_identity: "none", "generic", "category", "species" (a specific plant/animal species), "exact_person",
  "exact_event", "exact_place" or "exact_object". A metaphor never requires an exact identity.
- interpretation_confidence: "explicit", "inferred" or "uncertain"; unresolved: short notes on what is unclear.
- must_not_imply: what the image must NOT suggest (negated facts: "it is not poisonous" → "poisonous warning";
  a different species; a different era). [] if nothing.
- documentary_query: 2 to 7 English words for YouTube and archives that KEEP the exact identity: proper names,
  scientific names, years and places are allowed here ("Moringa oleifera leaves harvest", "Chernobyl 1986
  evacuation footage"). "" when the scene is generic.
- comparison: true only when the scene deliberately joins two contexts (then explain it in unresolved).
- data_points and data_source: only when the narration gives numbers AND their source; a chart is drawn by the
  editor (never by an image model). Otherwise [] and ""."""


def norm(text: str) -> str:
    text = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode().lower()
    return " ".join(re.findall(r"[a-z0-9]+", text))


def _names(e: dict) -> set[str]:
    return {norm(n) for n in [e.get("name", ""), *(e.get("aliases") or []), e.get("scientific_name", "")] if norm(n)}


def finish_entities(raw: list[dict], n_units: int) -> list[dict]:
    """IDs estáveis (ordem da 1ª evidência, depois nome) e fusão de duplicadas por nome/alias em comum."""
    merged: list[dict] = []
    for e in (dict(x) for x in raw if isinstance(x, dict) and (x.get("name") or "").strip()):
        e = ScriptEntity.model_validate({**e, "id": ""}).model_dump()
        e["evidence_units"] = sorted({u for u in e["evidence_units"] if isinstance(u, int) and 0 <= u < n_units})
        twin = next((m for m in merged if _names(m) & _names(e) and m["kind"] == e["kind"]), None)
        if twin is None:
            merged.append(e)
            continue
        twin["aliases"] = sorted({*twin["aliases"], *e["aliases"], e["name"]} - {twin["name"]})
        twin["scientific_name"] = twin["scientific_name"] or e["scientific_name"]
        for k in ("explicit_attributes", "inferred_attributes"):
            twin[k] = list(dict.fromkeys(twin[k] + e[k]))
        twin["evidence_units"] = sorted(set(twin["evidence_units"]) | set(e["evidence_units"]))
        twin["real_identifiable"] = twin["real_identifiable"] or e["real_identifiable"]
        twin["ambiguous_name"] = (twin["ambiguous_name"] or e["ambiguous_name"]) and not twin["scientific_name"]
    merged.sort(key=lambda e: (e["evidence_units"][0] if e["evidence_units"] else n_units, norm(e["name"])))
    for i, e in enumerate(merged, start=1):
        e["id"] = f"ent{i:02d}"
    return merged


def entity_index(entities: list[dict]) -> dict[str, str]:
    """nome/alias normalizado → id."""
    out: dict[str, str] = {}
    for e in entities:
        for n in _names(e):
            out.setdefault(n, e["id"])
    return out


def finish_claims(raw: list[dict], entities: list[dict], n_units: int) -> list[dict]:
    idx = entity_index(entities)
    out = []
    for c in (x for x in raw if isinstance(x, dict)):
        c = ScriptClaim.model_validate({**c, "id": ""}).model_dump()
        c["units"] = sorted({u for u in c["units"] if isinstance(u, int) and 0 <= u < n_units})
        if not (c["relation"] or c["object"] or c["quote"]):
            continue
        c["subject_entity"] = idx.get(norm(c["subject"]), "")
        c["object_entity"] = idx.get(norm(c["object"]), "")
        out.append(c)
    out.sort(key=lambda c: (c["units"][0] if c["units"] else n_units, c["quote"]))
    for i, c in enumerate(out, start=1):
        c["id"] = f"clm{i:02d}"
    return out


def link_recurring(recurring: list[dict], entities: list[dict]) -> list[dict]:
    """Aparência fixa das entidades recorrentes ganha o id da entidade e a base da aparência: dita no roteiro ou
    inferida (artística, nunca atributo documental de uma pessoa real)."""
    idx = entity_index(entities)
    by_id = {e["id"]: e for e in entities}
    out = []
    for r in recurring or []:
        r = dict(r)
        eid = idx.get(norm(r.get("name", "")), "")
        r["entity_id"] = eid
        r["look_basis"] = "script" if eid and by_id[eid]["explicit_attributes"] else "inferred_artistic"
        out.append(r)
    return out


def narrative_time_for(block: dict) -> str:
    given = block.get("narrative_time")
    if given in NarrativeTime.__args__:  # type: ignore[attr-defined]
        return given
    return {"historical": "past", "contemporary": "present", "timeless": "timeless"}.get(
        block.get("setting_type", ""), "timeless")


# ---------------------------------------------------------------- validação da cena

def _contains(text: str, quote: str) -> bool:
    q = norm(quote)
    return bool(q) and q in norm(text)


def check_scene(scene: dict, text: str, bible: dict) -> list[str]:
    """Valida e completa os campos de interpretação de UMA cena (dict do SceneDraft), no lugar.

    Devolve as notas do que foi corrigido estruturalmente (também anexadas a scene["unresolved"])."""
    notes: list[str] = []
    entities = bible.get("entities") or []
    by_id = {e["id"]: e for e in entities}
    idx = entity_index(entities)
    claims = {c["id"]: c for c in bible.get("claims") or []}
    scene.setdefault("unresolved", [])
    ids = []
    for ref in scene.get("entity_ids") or []:
        eid = ref if ref in by_id else idx.get(norm(ref), "")
        if eid:
            ids.append(eid)
        else:
            notes.append(f"entidade desconhecida ignorada: {ref}")
    for name in scene.get("entities") or []:  # nomes das recorrentes (campo antigo)
        if (eid := idx.get(norm(name))) and eid not in ids:
            ids.append(eid)
    scene["entity_ids"] = list(dict.fromkeys(ids))
    known_claims = [c for c in scene.get("claim_ids") or [] if c in claims]
    if len(known_claims) != len(scene.get("claim_ids") or []):
        notes.append("afirmação desconhecida ignorada")
    scene["claim_ids"] = known_claims
    quote = (scene.get("evidence_quote") or "").strip()
    if quote and not _contains(text, quote):
        notes.append("trecho de evidência não está na narração da cena")
        scene["evidence_quote"] = ""
    role = scene.get("visual_role") or "contextual_illustration"
    if role not in VISUAL_ROLES:
        role = "contextual_illustration"
    identity = scene.get("required_identity") or "generic"
    if scene.get("literal") is False and role in EXACT_ROLES:
        notes.append("frase figurada marcada como evidência exata: tratada como metáfora")
        role = "metaphor"
    if role == "metaphor" and identity not in ("none", "generic", "category"):
        notes.append("metáfora não exige identidade exata")
        identity = "generic"
    scene["visual_role"], scene["required_identity"] = role, identity
    # afirmação negada: o objeto negado não pode ser sugerido pela imagem
    avoid = list(scene.get("must_not_imply") or [])
    for cid in known_claims:
        c = claims[cid]
        if c.get("modality") == "negated" and c.get("object") and norm(c["object"]) not in map(norm, avoid):
            avoid.append(c["object"])
    scene["must_not_imply"] = avoid
    if identity == "species":
        for eid in scene["entity_ids"]:
            e = by_id[eid]
            if e["kind"] in ("plant", "animal") and e.get("ambiguous_name") and not e.get("scientific_name"):
                notes.append(f"nome popular ambíguo sem nome científico: {e['name']}")
    if scene.get("data_points") and not (scene.get("data_source") or "").strip():
        notes.append("dados sem fonte: o gráfico não será desenhado")
        scene["data_points"] = []
    if scene.get("data_points") and role != "data_explanation":
        scene["visual_role"] = "data_explanation"
    scene["unresolved"] = list(dict.fromkeys(list(scene["unresolved"]) + notes))
    return notes


def needs_review(scene: dict) -> bool:
    return bool(scene.get("unresolved")) or scene.get("interpretation_confidence") == "uncertain"


def script_evidence(scene: dict, units: list[dict]) -> list[dict]:
    """[{unit, quote}] das unidades da cena onde o trecho de evidência aparece (ou a 1ª unidade, sem trecho)."""
    first, last = scene["units"]
    quote = scene.get("evidence_quote") or ""
    hits = [{"unit": u["i"], "quote": quote} for u in units[first:last + 1] if quote and _contains(u["text"], quote)]
    if hits:
        return hits
    joined = " ".join(u["text"] for u in units[first:last + 1])
    if quote and _contains(joined, quote):  # trecho que atravessa unidades
        return [{"unit": first, "quote": quote}]
    return []


# ---------------------------------------------------------------- reconciliação final

def reconcile(scenes: list[dict], bible: dict) -> list[dict]:
    """Problemas globais depois de todas as janelas: [{code, message, scene?}]. Não altera nada."""
    out: list[dict] = []
    claims = bible.get("claims") or []
    seen: dict[tuple, dict] = {}
    for c in claims:
        key = (c.get("subject_entity") or norm(c.get("subject", "")), norm(c.get("relation", "")),
               c.get("object_entity") or norm(c.get("object", "")))
        other = seen.get(key)
        if other and {other.get("modality"), c.get("modality")} == {"asserted", "negated"}:
            out.append({"code": "SCRIPT_CONTRADICTION", "message": f"O roteiro afirma e nega a mesma coisa: "
                        f"\"{other.get('quote')}\" × \"{c.get('quote')}\""})
        seen.setdefault(key, c)
    known = {e["id"] for e in bible.get("entities") or []}
    for s in scenes:
        missing = [e for e in s.get("entity_ids") or [] if e not in known]
        if missing:
            out.append({"code": "INTERPRETATION_REVIEW", "scene": s["id"],
                        "message": f"Cena cita entidades inexistentes: {missing}"})
        if needs_review(s):
            out.append({"code": "INTERPRETATION_REVIEW", "scene": s["id"],
                        "message": "Interpretação a revisar: " + "; ".join(s.get("unresolved") or ["incerta"])})
    for a in bible.get("ambiguities") or []:
        if a.get("importance") == "high":
            units = a.get("units") or []
            scene = next((s["id"] for s in scenes if units and s["units"][0] <= units[0] <= s["units"][1]), None)
            out.append({"code": "INTERPRETATION_REVIEW", "scene": scene,
                        "message": f"Ambiguidade importante do roteiro: {a.get('question')}"})
    # cronologia: o mesmo bloco não pode reaparecer depois de outro sem ser declarado (comparação/flashback)
    order = [s.get("context_id") for s in scenes if s.get("context_id")]
    for i in range(2, len(order)):
        if order[i] != order[i - 1] and order[i] in order[: i - 1] and not scenes[i].get("comparison"):
            sid = [s for s in scenes if s.get("context_id")][i]["id"]
            out.append({"code": "INTERPRETATION_REVIEW", "scene": sid,
                        "message": f"Volta ao contexto {order[i]} sem declarar comparação ou flashback"})
    return out
