"""Relatório de direção (direcao.json): como o vídeo foi montado, cena a cena.

Junta plan.json, selection.json, timeline.json e transcript.json num documento legível, para
acompanhar e ajustar a direção. Vai para o Drive junto com o vídeo e aparece no card da produção.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..config import load_settings
from .allocate import composition, targets


def _load(job: Path, name: str) -> Any:
    p = job / name
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def _pct(part: float, total: float) -> float:
    return round(100 * part / total, 1) if total else 0.0


def build_report(job: Path, production: dict, config, issues: list[dict]) -> dict | None:
    plan = _load(job, "plan.json")
    if not plan:
        return None
    selection = _load(job, "selection.json") or {}
    timeline = _load(job, "timeline.json") or {}
    transcript = _load(job, "transcript.json") or {}
    tl_scenes = {s["id"]: s for s in timeline.get("scenes", [])}
    issues_by_scene: dict[str, list[str]] = {}
    for i in issues:
        if i.get("scene"):
            issues_by_scene.setdefault(i["scene"], []).append(f"{i['code']}: {i['message']}")

    scenes = []
    for s in plan["scenes"]:
        sel = selection.get(s["id"], {})
        rendered = tl_scenes.get(s["id"])
        final = sel.get("source", s["source"])
        asset = {k: sel[k] for k in ("source_used", "is_image", "provider", "title", "query", "score", "method", "seen",
                                     "realism", "strategy", "era_consistent", "anachronisms_seen", "is_timeless",
                                     "queries_used", "must_avoid_used", "attempts",
                                     "reason", "vision_calls", "searches", "in_point", "out_point", "clip_duration",
                                     "resolution", "author", "license", "page_url", "prompt", "segment_check")
                 if sel.get(k) is not None}
        asset["validation"] = scene_validation(sel, s, config_min_score(job))
        scenes.append({
            "id": s["id"],
            "tempo": f"{s['start']:.2f}–{s['end']:.2f}",
            "duracao": round(s["end"] - s["start"], 2),
            "texto": s["text"],
            "contexto": _scene_context_summary(s),
            "plano": {
                "subject": s.get("subject"),
                "style_allowance": s.get("style_allowance"),
                "allowed_styles": s.get("allowed_styles"),
                "style_reason": s.get("style_reason"),
                "literal": s.get("literal"),
                "must_show": s.get("must_show"),
                "must_avoid": s.get("must_avoid"),
                "visual_intent": s.get("visual_intent"),
                "kind": s.get("kind"),
                "energy": s.get("energy"),
                "affinity": s.get("affinity"),
                "queries": s.get("queries"),
                "queries_by_strategy": s.get("queries_by_strategy"),
                "strategy_order": s.get("strategy_order"),
                "era_markers_to_show": s.get("era_markers_to_show"),
                "timeless_alternative": s.get("timeless_alternative") or None,
                "anachronisms": s.get("anachronisms"),
                "ai_kind": s.get("ai_kind"),
                "chapter_break": s.get("chapter_break"),
                "chapter_title": s.get("chapter_title"),
                "highlight": s.get("highlight"),
            },
            "fonte_alocada": s["source"],
            "fonte_final": final,
            "migrou": not (final == s["source"] or (s["source"] == "ai" and final.startswith("ai"))),
            "asset": asset,
            "render": ({"transition_in": rendered["transition_in"], "motion": rendered["motion"], "in": rendered["in"]}
                       if rendered else "absorvida pela cena vizinha (sem asset)"),
            "problemas": issues_by_scene.get(s["id"], []),
        })

    for s in plan["scenes"]:
        s["final_source"] = selection.get(s["id"], {}).get("source", s["source"])
    valid = [s for s in plan["scenes"] if s["final_source"] not in ("missing", "migrate_ai")]
    final_sec = composition(valid)
    total = sum(final_sec.values())
    goal = targets(total, config)
    durations = [s["end"] - s["start"] for s in plan["scenes"]]

    return {
        "producao": {k: production.get(k) for k in ("id", "title", "channel_name", "created_at", "drive_url")},
        "config": {
            "direcao": config.direction, "idioma_video": config.lang, "idioma_roteiro": config.language,
            "estilo_de_midia": getattr(config, "media_style", None),
            "modo_de_selecao": getattr(config, "selection_mode", None),
            "idioma_buscas": "en",
            "estilo_visual": (plan.get("visual_style") or config.visual_style),
            "representacao_de_epoca": plan.get("period_look") or config.period_look,
            "real_pct": config.real_pct, "youtube_pct": config.youtube_pct,
            "ai_media": config.ai_media, "duracao_media_cena": config.avg_scene_seconds,
            "legendas": config.subtitles, "musica": config.music.enabled,
        },
        "resumo": {
            "duracao_total": round(total, 2),
            "cenas": len(plan["scenes"]),
            "duracao_cena": {"media": round(sum(durations) / len(durations), 2) if durations else 0,
                             "min": round(min(durations), 2) if durations else 0,
                             "max": round(max(durations), 2) if durations else 0},
            "composicao_meta_pct": {k: _pct(v, total) for k, v in goal.items()},
            "composicao_final_pct": {k: _pct(v, total) for k, v in final_sec.items()},
            # youtube_pct é a fatia do MATERIAL REAL (não do vídeo inteiro): meta efetiva × alcançado
            "youtube_no_material_real": {
                "meta_pct": _pct(goal.get("youtube", 0), goal.get("youtube", 0) + goal.get("stock", 0)),
                "alcancado_pct": _pct(final_sec.get("youtube", 0), final_sec.get("youtube", 0)
                                      + final_sec.get("stock", 0)),
                "regra": "YouTube primeiro enquanto houver cota" if (load_settings()["youtube"].get("first", True))
                else f"{config.youtube_pct}% do material real"},
            "validacao": _validation_counts(selection, plan["scenes"], job),
            "chamadas_visao": sum(int(v.get("vision_calls") or 0) for v in selection.values()),
            "buscas": sum(int(v.get("searches") or 0) for v in selection.values()),
            "crossfades": sum(1 for s in timeline.get("scenes", []) if s["transition_in"]["type"] == "crossfade"),
            "overlays": len(timeline.get("overlays", [])),
            "clima_musical": plan.get("music_mood"),
            "musica": (timeline.get("audio") or {}).get("music"),
            "transcricao": {"fonte": transcript.get("source"), "divergencia": transcript.get("divergence"),
                            "idioma_detectado": transcript.get("detected_language")},
        },
        "cenas": scenes,
        "visual": build_visual_report(job),
        "tempos": _load(job, "timing_report.json"),
        "biblia_de_contexto": _load(job, "context_bible.json") or _load(job, "video_brief.json"),
        "overlays": timeline.get("overlays", []),
        "problemas": [{k: i.get(k) for k in ("code", "severity", "scene", "message")} for i in issues],
    }


def _validation_counts(selection: dict, scenes: list[dict], job: Path) -> dict:
    counts: dict[str, int] = {}
    for s in scenes:
        st = scene_validation(selection.get(s["id"], {}), s, config_min_score(job))["status"]
        counts[st] = counts.get(st, 0) + 1
    return counts


def config_min_score(job: Path) -> float:
    from ..config import load_settings

    return float(load_settings()["selection"].get("min_score", 6.5))


def scene_validation(entry: dict, scene: dict, min_score: float) -> dict:
    """Estado salvo na seleção; produções antigas (sem o campo) são classificadas na hora, com a mesma regra."""
    from ..config import load_settings
    from .validation import classify, policy

    if entry.get("validation"):
        return entry["validation"]
    return classify(entry, scene, min_score, policy(load_settings()))


def _scene_context_summary(s: dict) -> dict | None:
    c = s.get("context") or {}
    if not c:
        return None
    return {"id": c.get("id"), "tipo": c.get("setting_type"), "epoca": c.get("era"), "rotulo": c.get("era_label"),
            "lugar": c.get("place"), "viabilidade": c.get("footage_feasibility")}


def _strategy(s: dict, e: dict) -> str | None:
    if e.get("strategy"):
        return e["strategy"]
    if (s.get("context") or {}).get("setting_type") != "historical" or not e.get("asset"):
        return None
    return "IA" if str(e.get("source", "")).startswith("ai") else None


def write_report(ctx) -> Path | None:
    """Gera output/direcao.json com os problemas registrados até agora."""
    from sqlmodel import select

    from ..db import session_scope
    from ..models import Issue, Production

    with session_scope() as s:
        p = s.get(Production, ctx.production_id)
        production = {"id": p.id, "title": p.title, "channel_name": ctx.config.channel_name,
                      "created_at": str(p.created_at), "drive_url": p.drive_url} if p else {}
        issues = [i.model_dump() for i in s.exec(select(Issue).where(Issue.production_id == ctx.production_id))]
    report = build_report(ctx.dir, production, ctx.config, issues)
    if report is None:
        return None
    return ctx.write_json("output/direcao.json", report)


def build_visual_report(job: Path) -> list[dict]:
    """visual_report.json: uma linha por cena com o assunto esperado e o que a IA viu (§9)."""
    plan = _load(job, "plan.json")
    if not plan:
        return []
    selection = _load(job, "selection.json") or {}
    rows = []
    for s in plan["scenes"]:
        e = selection.get(s["id"], {})
        score = e.get("score")
        rows.append({
            "cena": s["id"], "narracao": s["text"][:120], "assunto_esperado": s.get("subject"),
            "visto": e.get("seen") or ("(sem validação visual)" if e.get("asset") else "(sem asset)"),
            "fonte": e.get("source_used") or e.get("source"), "provedor": e.get("provider"),
            "metodo": e.get("method"), "nota": score,
            "estilo": e.get("realism") or (e.get("style") if e.get("seen") else None),  # só o que a visão confirmou
            "estilos_aceitos": e.get("allowed_styles") or s.get("allowed_styles"),
            "style_reason": s.get("style_reason"),
            "contexto": (f"{s['context'].get('id')}: {s['context'].get('era')} · {s['context'].get('place')}"
                         if s.get("context") else None),
            "era_consistent": e.get("era_consistent"),
            "anacronismos_vistos": e.get("anachronisms_seen") or None,
            "estrategia": _strategy(s, e),
            "validacao": (v := scene_validation(e, s, config_min_score(job)))["status"],
            "revisar": v["status"] != "validated" or e.get("era_consistent") is False
            or bool(e.get("anachronisms_seen")),
        })
    return rows


def write_visual_report(ctx) -> Path:
    return ctx.write_json("output/visual_report.json", build_visual_report(ctx.dir))
