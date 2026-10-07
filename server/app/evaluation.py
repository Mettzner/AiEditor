"""Benchmark de interpretação e pesquisa (seção 11 do plano de melhorias).

Uso (offline, padrão, sem rede e sem custo):
    python -m app.evaluation                      # todos os casos de server/eval/cases
    python -m app.evaluation --out resultado.json

Avaliação real (opt-in, com orçamento; NÃO roda por padrão):
    set AIEDITOR_LIVE=1
    python -m app.evaluation --live --budget 1.00

Offline, cada caso traz a resposta do modelo gravada ("replay": Bíblia e cenas) e candidatos de busca anotados.
O benchmark roda o pós-processamento real do app (normalização da Bíblia, validação das cenas, divisão por
contexto, reconciliação, plano de busca, ranking com identidade e diversidade) e compara com as anotações.
Isso mede a LÓGICA determinística; não prova a qualidade de interpretação de um modelo real. No modo --live a
Bíblia e o planejamento vêm do Claude configurado, sob o teto informado.

Métricas: acerto de papel visual, identidade exigida, tempo narrado, "não sugerir" de negações e entidades;
entidades e contradições da Bíblia; divisão por contexto; precisão@1 do ranking (identidade e relevância),
diversidade da amostra para a visão, cobertura do YouTube; chamadas previstas; custo por minuto (faixa da
estimativa); falhas e retrabalho (cenas com pendências).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import date
from pathlib import Path

CASES_DIR = Path(__file__).resolve().parents[1] / "eval" / "cases"

DRAFT_DEFAULTS = dict(literal=True, subject="subject", subject_category="other", must_show=[], setting="",
                      action="", shot="medium", mood="", must_avoid=[], style_allowance="real_only",
                      allowed_styles=["real_footage"], style_reason="real", visual_intent="", queries=[],
                      kind="concreto", energy="media", affinity={"stock": 0.6, "youtube": 0.5, "ai": 0.3},
                      ai_kind="image", chapter_break=False, chapter_title=None, highlight=None, emphasis="none",
                      quote=None, overlay_language="en", context_id="", era_markers_to_show=[], archival_query="",
                      timeless_alternative="", timeless_query="", meaning="", entities=[])
BLOCK_DEFAULTS = dict(era_confidence="explicit", era_evidence="", place_confidence="explicit", climate="",
                      landscape="", society="", clothing="", architecture="", interiors="", lighting="", transport="",
                      objects=[], era_markers_to_show=[], anachronisms=[], search_vocabulary=[], palette="", mood="",
                      card_text="")


def load_cases(folder: Path = CASES_DIR) -> list[dict]:
    cases = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(folder.glob("*.json"))]
    return [_expand(c) for c in cases]


def _expand(case: dict) -> dict:
    """Casos gerados (documentário longo): unidades e replay montados por código, sem arquivo gigante."""
    gen = case.get("generate")
    if not gen:
        return case
    n, per_block = int(gen["units"]), int(gen["units_per_block"])
    units = [gen["unit_template"].format(i=i) for i in range(n)]
    blocks = [{"id": f"ctx{b + 1}", "first_unit": b * per_block, "last_unit": min(n, (b + 1) * per_block) - 1,
               "setting_type": "historical", "era": str(1800 + 40 * b), "era_label": f"{1800 + 40 * b}s",
               "place": "rural England", "footage_feasibility": "pre_film"} for b in range(-(-n // per_block))]
    scenes = [{"first_unit": i, "last_unit": min(n - 1, i + gen["units_per_scene"] - 1), "subject": "farmer",
               "visual_role": "contextual_illustration"} for i in range(0, n, gen["units_per_scene"])]
    case = dict(case)
    case["units"] = units
    case["replay"] = {"bible": {"topic": case["id"], "contexts": blocks, "entities": [], "claims": []},
                      "plan": scenes}
    return case


def _units(case: dict) -> list[dict]:
    return [{"i": i, "start": i * 3.0, "end": i * 3.0 + 2.8, "text": t} for i, t in enumerate(case["units"])]


def _draft(d: dict):
    from .pipeline.plan import SceneDraft

    return SceneDraft(**{**DRAFT_DEFAULTS, **d})


def _bible_raw(raw: dict) -> dict:
    return {**raw, "contexts": [{**BLOCK_DEFAULTS, **b} for b in raw.get("contexts") or []]}


def interpret(case: dict, live: bool = False, budget: float = 0.0) -> tuple[dict, list[dict], int]:
    """(bíblia final, cenas finais, chamadas de LLM) pelo pipeline do app; replay offline ou Claude no --live."""
    from .pipeline.context import block_by_id, block_for_unit, finish_bible, scene_context
    from .pipeline.plan import WINDOW_UNITS, PlanWindow, _validate
    from .pipeline.semantics import check_scene, script_evidence

    units = _units(case)
    windows = [units[i:i + WINDOW_UNITS] for i in range(0, len(units), WINDOW_UNITS)]
    if live:
        bible_raw, drafts = _live_llm(case, units, windows, budget)
    else:
        bible_raw, drafts = _bible_raw(case["replay"]["bible"]), [_draft(d) for d in case["replay"]["plan"]]
    bible = finish_bible(bible_raw, len(units), units)
    scenes = []
    for win in windows:
        mine = [d for d in drafts if win[0]["i"] <= d.first_unit <= win[-1]["i"]]
        if not mine:
            continue
        fixed = _validate(PlanWindow(scenes=mine, music_mood=None), win[0]["i"], win[-1]["i"], "real_preferred",
                          bible).scenes
        for d in fixed:
            sc = {"id": f"s{len(scenes) + 1:03d}", **d.model_dump(), "units": [d.first_unit, d.last_unit],
                  "text": " ".join(u["text"] for u in units[d.first_unit:d.last_unit + 1])}
            block = block_by_id(bible, d.context_id) or block_for_unit(bible, d.first_unit)
            sc["context"] = scene_context(block)
            check_scene(sc, sc["text"], bible)
            sc["script_evidence"] = script_evidence(sc, units)
            scenes.append(sc)
    return bible, scenes, 1 + len(windows)


def _live_llm(case: dict, units: list[dict], windows: list[list[dict]], budget: float):
    """Bíblia e cenas pelo Claude configurado, sob o teto (Ledger de uma produção temporária)."""
    from .budget import Ledger, paid_llm
    from .db import init_db, session_scope
    from .models import Production
    from .pipeline.context import ContextBible
    from .pipeline.plan import PLAN_MAX_TOKENS, PlanWindow
    from .pipeline.visual import PLAN_SYSTEM, brief_section
    from .providers.llm.base import call_llm, estimate_cost

    init_db()
    with session_scope() as s:
        p = Production(title=f"eval:{case['id']}", script="\n".join(case["units"]), config={}, status="done")
        s.add(p)
        s.commit()
        s.refresh(p)
        pid = p.id
    Ledger.for_production(pid, limit=budget)

    class Ctx:
        production_id = pid

        def record_llm(self, usage, step=None):
            with session_scope() as s:
                prod = s.get(Production, pid)
                prod.cost_actual = (prod.cost_actual or 0) + (usage.cost or 0)
                s.add(prod)
                s.commit()

    ctx = Ctx()
    listing = "\n".join(f"[{u['i']}|{u['start']:.1f}-{u['end']:.1f}] {u['text']}" for u in units)
    context = f"PRODUCTION SETTINGS\n- Video language: {case.get('language', 'en')}\n\nSCRIPT UNITS\n{listing}"
    user = f"Write ONLY the CONTEXT BIBLE for the whole script (no scenes yet). Cover all units 0 to {len(units) - 1}."
    bible, _ = paid_llm(ctx, "bible", lambda: call_llm("bible", system=PLAN_SYSTEM, context=context, user=user,
                                                       schema=ContextBible, max_tokens=16000),
                        estimate_cost("bible", system=PLAN_SYSTEM, context=context, user=user, max_tokens=16000))
    raw = bible.model_dump()
    drafts = []
    for win in windows:
        brief = brief_section(raw)
        u = f"Plan units {win[0]['i']} to {win[-1]['i']}."
        out, _ = paid_llm(ctx, "plan", lambda: call_llm("plan", system=PLAN_SYSTEM, context=[context, brief], user=u,
                                                        schema=PlanWindow, max_tokens=PLAN_MAX_TOKENS),
                          estimate_cost("plan", system=PLAN_SYSTEM, context=[context, brief], user=u,
                                        max_tokens=PLAN_MAX_TOKENS))
        drafts += out.scenes
    return raw, drafts


def _scene_for(scenes: list[dict], unit: int) -> dict | None:
    return next((s for s in scenes if s["units"][0] <= unit <= s["units"][1]), None)


def score_case(case: dict, bible: dict, scenes: list[dict]) -> dict:
    from .pipeline.semantics import norm, reconcile

    ann = case.get("annotations") or {}
    checks: dict[str, list[bool]] = {k: [] for k in ("visual_role", "required_identity", "narrative_time",
                                                     "must_not_imply", "entities")}
    names = {e["id"]: e for e in bible.get("entities") or []}
    for exp in ann.get("scenes") or []:
        sc = _scene_for(scenes, exp["units"][0])
        if sc is None:
            for k in checks:
                if k in exp:
                    checks[k].append(False)
            continue
        if "visual_role" in exp:
            checks["visual_role"].append(sc.get("visual_role") == exp["visual_role"])
        if "required_identity" in exp:
            checks["required_identity"].append(sc.get("required_identity") == exp["required_identity"])
        if "narrative_time" in exp:
            checks["narrative_time"].append((sc.get("context") or {}).get("narrative_time") == exp["narrative_time"])
        if "must_not_imply" in exp:
            got = {norm(x) for x in sc.get("must_not_imply") or []}
            checks["must_not_imply"].append(all(norm(x) in got for x in exp["must_not_imply"]))
        if "entities" in exp:
            got = {norm(names[i]["name"]) for i in sc.get("entity_ids") or [] if i in names}
            checks["entities"].append(all(norm(x) in got for x in exp["entities"]))
    ent_ok = []
    for exp in ann.get("entities") or []:
        e = next((x for x in bible.get("entities") or [] if norm(x["name"]) == norm(exp["name"])), None)
        ent_ok.append(bool(e) and (not exp.get("scientific_name")
                                   or norm(e.get("scientific_name", "")) == norm(exp["scientific_name"])))
    problems = reconcile(scenes, bible)
    contradictions = sum(1 for p in problems if p["code"] == "SCRIPT_CONTRADICTION")
    crossing = 0
    bounds = [b["units"][0] for b in bible.get("context_timeline") or []][1:]
    for s in scenes:
        if any(s["units"][0] < b <= s["units"][1] for b in bounds) and not s.get("comparison"):
            crossing += 1
    return {
        "checks": {k: {"ok": sum(v), "total": len(v)} for k, v in checks.items() if v},
        "bible_entities": {"ok": sum(ent_ok), "total": len(ent_ok)},
        "contradictions": {"found": contradictions, "expected": ann.get("contradictions", 0)},
        "context_blocks": {"found": len(bible.get("context_timeline") or []), "expected": ann.get("context_blocks")},
        "scenes_crossing_contexts": crossing,
        "rework_scenes": sum(1 for s in scenes if s.get("unresolved")),
        "scenes": len(scenes),
    }


def score_search(case: dict, bible: dict, scenes: list[dict]) -> dict | None:
    """Ranking com identidade e diversidade sobre candidatos anotados (sem rede)."""
    from .pipeline.select.plan_search import documentary_query, identity_terms
    from .pipeline.select.prerank import rank
    from .pipeline.validation import needs_exact
    from .providers.stock.base import Candidate

    pools = case.get("candidates") or {}
    if not pools:
        return None
    p_at_1, diversity, yt_cover = [], [], []
    for unit_key, items in pools.items():
        sc = _scene_for(scenes, int(unit_key))
        if sc is None:
            continue
        cands = [Candidate(provider=x.get("provider", "pexels"), external_id=str(i), title=x["title"],
                           duration=x.get("duration", 60), width=1920, height=1080, page_url="", thumbnail=None,
                           author=x.get("channel", ""), channel_id=x.get("channel", ""),
                           description=x.get("description", "")) for i, x in enumerate(items)]
        meta = {str(i): x for i, x in enumerate(items)}
        doc = documentary_query(sc, bible, sc.get("must_avoid"))  # como no seletor: a documental vem primeiro
        queries = [q for q in [doc, *(sc.get("queries") or [])] if q] or [sc.get("subject") or ""]
        ranked = rank(cands, queries, sc.get("visual_intent") or "",
                      5, keep=5, subject=sc.get("subject") or "", identity=identity_terms(sc, bible),
                      exact=needs_exact(sc) or sc.get("required_identity") == "species", per_channel=2)
        top = meta[ranked[0].external_id] if ranked else {}
        p_at_1.append(bool(top.get("relevant")) and top.get("identity", True))
        diversity.append(len({c.channel_id for c in ranked}) / max(1, len(ranked)))
        yt_cover.append(any(meta[c.external_id].get("relevant") and c.provider == "youtube" for c in ranked))
    n = max(1, len(p_at_1))
    return {"precision_at_1": round(sum(p_at_1) / n, 3), "sample_diversity": round(sum(diversity) / n, 3),
            "youtube_coverage": round(sum(yt_cover) / n, 3), "scenes": len(p_at_1)}


def cost_per_minute(case: dict) -> dict:
    from .estimate import estimate
    from .models import Preset, ProductionConfig

    cfg = ProductionConfig(**Preset(real_pct=80).model_dump(), title=case["id"], channel_name="eval",
                           video_language=case.get("language", "en"))
    seconds = len(case["units"]) * 3.0
    est = estimate(cfg, " ".join(case["units"]), audio_seconds=seconds)
    minutes = max(seconds / 60, 1e-6)
    return {"low": round(est["cost"]["range"]["low"] / minutes, 4), "high": round(est["cost"]["range"]["high"] / minutes, 4),
            "currency": est["cost"]["currency"], "prices_as_of": est["cost"]["prices_as_of"]}


def run(cases: list[dict], live: bool = False, budget: float = 0.0) -> dict:
    results, failures = [], []
    for case in cases:
        t = time.perf_counter()
        try:
            bible, scenes, calls = interpret(case, live, budget)
            results.append({"id": case["id"], "language": case.get("language"), "kind": case.get("kind"),
                            "llm_calls": calls, "interpretation": score_case(case, bible, scenes),
                            "search": score_search(case, bible, scenes), "cost_per_minute": cost_per_minute(case),
                            "seconds": round(time.perf_counter() - t, 3)})
        except Exception as e:  # noqa: BLE001 — falha conta no relatório, não derruba o benchmark
            failures.append({"id": case["id"], "error": f"{type(e).__name__}: {e}"[:300]})
    totals: dict[str, dict] = {}
    for r in results:
        for k, v in r["interpretation"]["checks"].items():
            t = totals.setdefault(k, {"ok": 0, "total": 0})
            t["ok"] += v["ok"]
            t["total"] += v["total"]
    return {"date": date.today().isoformat(), "mode": "live" if live else "offline (replay + fixtures)",
            "warning": None if live else "Fixtures sintéticas testam a lógica do app; não medem a qualidade de "
                                         "interpretação de um modelo real.",
            "cases": results, "failures": failures,
            "totals": {k: {**v, "rate": round(v["ok"] / v["total"], 3) if v["total"] else None}
                       for k, v in totals.items()}}


def to_markdown(report: dict) -> str:
    lines = [f"# Benchmark AiEditor — {report['date']} ({report['mode']})", ""]
    if report.get("warning"):
        lines += [f"> {report['warning']}", ""]
    lines += ["| caso | idioma | cenas | chamadas LLM | papel | identidade | tempo | não sugerir | entidades | "
              "p@1 | diversidade | YouTube | retrabalho | US$/min |", "|" + "---|" * 14]
    for r in report["cases"]:
        c = r["interpretation"]["checks"]

        def f(k):
            return f"{c[k]['ok']}/{c[k]['total']}" if k in c else "–"

        s = r["search"] or {}
        lines.append(f"| {r['id']} | {r['language']} | {r['interpretation']['scenes']} | {r['llm_calls']} | "
                     f"{f('visual_role')} | {f('required_identity')} | {f('narrative_time')} | {f('must_not_imply')} | "
                     f"{f('entities')} | {s.get('precision_at_1', '–')} | {s.get('sample_diversity', '–')} | "
                     f"{s.get('youtube_coverage', '–')} | {r['interpretation']['rework_scenes']} | "
                     f"{r['cost_per_minute']['low']}–{r['cost_per_minute']['high']} |")
    if report["failures"]:
        lines += ["", "Falhas:"] + [f"- {x['id']}: {x['error']}" for x in report["failures"]]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Benchmark de interpretação e pesquisa do AiEditor")
    ap.add_argument("--cases", type=Path, default=CASES_DIR)
    ap.add_argument("--out", type=Path, default=None, help="grava o relatório JSON (e .md ao lado)")
    ap.add_argument("--live", action="store_true", help="usa o Claude de verdade (gasta; exige AIEDITOR_LIVE=1)")
    ap.add_argument("--budget", type=float, default=0.0, help="teto em US$ para o modo --live")
    args = ap.parse_args(argv)
    if args.live and (os.environ.get("AIEDITOR_LIVE") != "1" or args.budget <= 0):
        print("Modo --live exige AIEDITOR_LIVE=1 e --budget > 0 (nada foi executado).", file=sys.stderr)
        return 2
    if not args.live:
        os.environ.setdefault("AIEDITOR_DATA", str(Path(os.environ.get("TEMP", "/tmp")) / "aieditor_eval"))
    from .db import init_db

    init_db()
    report = run(load_cases(args.cases), args.live, args.budget)
    md = to_markdown(report)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(report, indent=1, ensure_ascii=False), encoding="utf-8")
        args.out.with_suffix(".md").write_text(md, encoding="utf-8")
    print(md)
    return 1 if report["failures"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
