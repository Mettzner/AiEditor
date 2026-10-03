"""Etapa 6: direção → timeline.json (§9.3). O renderer só executa este JSON."""
from __future__ import annotations

from ..directions import get_direction
from ..providers.music import library
from ..worker.context import JobContext
from .allocate import YOUTUBE_IMPLEMENTED, composition, targets
from .context import grade_for
from .report import write_report, write_visual_report


def fix_overlay_language(ctx: JobContext, overlays: list[dict], scenes: list[dict]) -> list[dict]:
    """Nenhum texto na tela em idioma diferente do vídeo (§11.4): todos os textos errados são corrigidos numa
    única chamada barata (Haiku); o que continuar errado é descartado."""
    from ..lang import is_mismatch, name
    from ..providers.llm.base import call_llm
    from .visual import OVERLAY_FIX_SYSTEM, OverlayFixBatch, compact_json

    lang = ctx.config.lang
    for o in overlays:
        o["language"] = lang
    wrong = [i for i, o in enumerate(overlays) if is_mismatch(o["text"], lang)]
    if not wrong:
        return overlays
    payload = {"target_language": f"{name(lang)} ({lang})", "texts": [
        {"index": i, "text": overlays[i]["text"], "type": overlays[i]["type"],
         "narration": next((s["text"] for s in scenes if s["start"] <= overlays[i]["start"] < s["end"]), "")}
        for i in wrong]}
    fixed: dict[int, str] = {}
    err = None
    try:
        out, usage = call_llm("overlay", system=OVERLAY_FIX_SYSTEM, user=compact_json(payload), schema=OverlayFixBatch,
                              max_tokens=60 + 40 * len(wrong))
        ctx.record_llm(usage, step="direct")
        fixed = {it.index: it.text.strip()[:60] for it in out.items}
    except Exception as e:  # noqa: BLE001
        err = repr(e)
    kept = []
    for i, o in enumerate(overlays):
        if i not in wrong:
            kept.append(o)
            continue
        new = fixed.get(i)
        if new and not is_mismatch(new, lang):
            ctx.issue("OVERLAY_LANGUAGE_FIXED", f"Overlay \"{o['text']}\" corrigido para \"{new}\" ({lang})")
            kept.append({**o, "text": new, "original_text": o["text"]})
        else:
            ctx.issue("OVERLAY_LANGUAGE_FIXED", f"Overlay \"{o['text']}\" removido: não foi possível deixá-lo em "
                      f"{lang}", severity="warning", detail=err)
    return kept


FPS = 30

# Padrões de Ken Burns: (zoom, centro x, centro y) inicial → final
KENBURNS = [
    ([1.0, 0.5, 0.5], [1.12, 0.5, 0.5]),
    ([1.12, 0.5, 0.5], [1.0, 0.5, 0.5]),
    ([1.08, 0.42, 0.5], [1.08, 0.58, 0.5]),
    ([1.08, 0.58, 0.5], [1.08, 0.42, 0.5]),
    ([1.0, 0.5, 0.55], [1.12, 0.45, 0.45]),
]


def _q(t: float) -> float:
    """Quantiza no frame, para que cenas consecutivas não acumulem desvio."""
    return round(round(t * FPS) / FPS, 4)


def run(ctx: JobContext) -> str:
    plan = ctx.read_json("plan.json")
    selection = ctx.read_json("selection.json") if (ctx.dir / "selection.json").exists() else {}
    direction = get_direction(ctx.config.direction)
    params = direction.params

    # Cenas sem asset são absorvidas pela vizinha (a anterior se estende).
    merged: list[dict] = []
    for s in plan["scenes"]:
        entry = selection.get(s["id"], {})
        has_asset = entry.get("asset") and (ctx.dir / entry["asset"]).exists()
        if not has_asset:
            if merged:
                merged[-1]["end"] = s["end"]
                continue
            s = {**s, "_pending_merge": True}
        merged.append({**s, "entry": entry})
    while merged and merged[0].get("_pending_merge"):  # primeira cena sem asset: a seguinte começa em 0
        if len(merged) == 1:
            raise RuntimeError("Nenhuma cena tem asset para renderizar")
        merged[1]["start"] = merged[0]["start"]
        merged.pop(0)

    scenes, overlays = [], []
    last_overlay = -1e9
    min_gap = params.get("overlay_min_gap_seconds", 20)
    xfade = params.get("crossfade_seconds", 0.3)
    cfg = ctx.config
    prev_ctx: str | None = None
    for n, s in enumerate(merged):
        e = s["entry"]
        start, end = _q(s["start"]), _q(s["end"])
        is_image = bool(e.get("is_image")) or e["source"].startswith("ai_image")
        context = s.get("context") or {}
        scene = {
            "id": s["id"], "start": start, "end": end, "source": e["source"], "asset": e["asset"],
            "in": e.get("in", 0.0), "motion": None,
            "transition_in": {"type": "crossfade", "duration": xfade} if s.get("chapter_break") and n > 0
            else {"type": "cut"},
        }
        if is_image:
            a, b = KENBURNS[n % len(KENBURNS)]
            scene["motion"] = {"type": "kenburns", "from": a, "to": b}
        # gradação uniforme por bloco histórico: costura reconstituição, pintura e IA numa mesma atmosfera (§8)
        grade = grade_for(context.get("footage_feasibility"), cfg.period_look) if cfg.period_grade else None
        if grade:
            scene["grade"] = grade
        scenes.append(scene)

        chapter = bool(s.get("chapter_break") and s.get("chapter_title") and n > 0)
        if chapter:
            dur = params.get("chapter_title_seconds", 3.0)
            overlays.append({"type": "chapter", "start": _q(start + xfade), "end": _q(min(end, start + xfade + dur)),
                             "text": s["chapter_title"], "style": f"{direction.id}.chapter"})
            last_overlay = start
        # card de lugar e data ao entrar num bloco de contexto (no 1º bloco, só se for histórico)
        ctx_id = context.get("id")
        entering = ctx_id and ctx_id != prev_ctx and (n > 0 or context.get("setting_type") == "historical")
        prev_ctx = ctx_id or prev_ctx
        if cfg.context_cards and entering and (context.get("card_text") or "").strip():
            c_start = _q(start + (xfade + params.get("chapter_title_seconds", 3.0) + 0.2 if chapter else 0.5))
            c_end = _q(min(end - 0.1, c_start + params.get("context_card_seconds", 3.5)))
            if c_end - c_start >= 1.5:
                overlays.append({"type": "place_card", "start": c_start, "end": c_end,
                                 "text": context["card_text"].strip(), "style": f"{direction.id}.place_card"})
                last_overlay = start
                continue
        if chapter:
            continue
        if s.get("highlight") and start - last_overlay >= min_gap:
            dur = params.get("overlay_duration_seconds", 3.5)
            o_start = _q(start + 0.4)
            overlays.append({"type": "highlight", "start": o_start, "end": _q(min(end - 0.1, o_start + dur)),
                             "text": s["highlight"], "style": f"{direction.id}.highlight"})
            last_overlay = start

    overlays = fix_overlay_language(ctx, overlays, merged)

    # Desvio de composição (tolerância de 5 p.p. por fonte)
    for s in plan["scenes"]:
        s["final_source"] = selection.get(s["id"], {}).get("source", s["source"])
    final = composition([s for s in plan["scenes"] if s["final_source"] not in ("missing", "migrate_ai")])
    total = sum(final.values()) or 1
    goal = targets(total, ctx.config)
    if not YOUTUBE_IMPLEMENTED:  # já avisado como SCENE_MIGRATED; compara só real × IA
        goal["stock"] += goal.pop("youtube")
        final["stock"] += final.pop("youtube")
    deviations = [f"{k}: meta {goal[k] / total:.0%}, final {final[k] / total:.0%}"
                  for k in goal if abs(goal[k] - final[k]) / total > 0.05]
    if deviations:
        ctx.issue("COMPOSITION_DEVIATION", "Composição final desviou da meta", detail="; ".join(deviations))

    music = None
    if ctx.config.music.enabled:
        track = library.pick(plan.get("music_mood") or ctx.config.music.mood, ctx.channel_id)
        if track:
            music = {"file": str(track), "volume_db": ctx.config.music.volume_db, "ducking": True}
        else:
            ctx.issue("MUSIC_FAILED", "Biblioteca de músicas vazia; o vídeo segue sem música "
                      "(geração via ElevenLabs entra na Fase 4)")

    timeline = {
        "version": 1, "resolution": [1920, 1080], "fps": FPS,
        "duration": scenes[-1]["end"],
        "audio": {"narration": "audio/narration.wav", "music": music},
        "scenes": scenes,
        "overlays": overlays,
        "subtitles": {"file": "subs.ass"} if ctx.config.subtitles else None,
        "style": {"font": ctx.config.font, "color_primary": ctx.config.color_primary,
                  "color_accent": ctx.config.color_accent, "subtitle": ctx.config.subtitle_style.model_dump()},
        "direction": direction.id,
    }
    out = ctx.write_json("timeline.json", timeline)
    write_report(ctx)
    write_visual_report(ctx)
    return str(out)
