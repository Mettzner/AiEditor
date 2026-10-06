"""Etapa 6: direção → timeline.json (§9.3). O renderer só executa este JSON.

O planejamento marca intenções por cena (capítulo, destaque, ênfase narrativa, citação); a direção traduz
cada intenção num recurso de edição com os parâmetros do manifest: transições, movimento de câmera, moldura
de foto, overlays animados, light leaks, textura de filme e efeitos sonoros.
"""
from __future__ import annotations

import difflib
import re

from ..directions import get_direction
from ..providers import sfx as sfx_library
from ..providers.music import library
from ..worker.context import JobContext
from .allocate import YOUTUBE_IMPLEMENTED, composition, targets
from .context import grade_for, video_look
from .plan import load_bible
from .report import write_report, write_visual_report


def fix_overlay_language(ctx: JobContext, overlays: list[dict], scenes: list[dict]) -> list[dict]:
    """Nenhum texto na tela em idioma diferente do vídeo (§11.4): todos os textos errados são corrigidos numa
    única chamada barata (Haiku); o que continuar errado é descartado."""
    from ..lang import is_mismatch, name
    from ..providers.llm.base import call_llm, failed_usage
    from .visual import OVERLAY_FIX_SYSTEM, OverlayFixBatch, compact_json

    lang = ctx.config.lang
    for o in overlays:
        o["language"] = lang
    wrong = [i for i, o in enumerate(overlays) if o.get("text") and is_mismatch(o["text"], lang)]
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
        if (paid := failed_usage(e)) is not None:
            ctx.record_llm(paid, step="direct")
    kept = []
    for i, o in enumerate(overlays):
        if i not in wrong:
            kept.append(o)
            continue
        new = fixed.get(i)
        if new and not is_mismatch(new, lang):
            ctx.issue("OVERLAY_LANGUAGE_FIXED", f"Overlay \"{o['text']}\" corrigido para \"{new}\" ({lang})")
            # citação traduzida não acompanha mais as palavras faladas: as palavras entram em ritmo uniforme
            kept.append({**o, "text": new, "original_text": o["text"], "word_times": None})
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
# Ken Burns pela ênfase da cena: tensão aproxima devagar, revelação aproxima mais, calma desliza de lado
KENBURNS_BY_EMPHASIS = {
    "tension": ([1.0, 0.5, 0.5], [1.14, 0.5, 0.47]),
    "reveal": ([1.0, 0.5, 0.5], [1.18, 0.5, 0.5]),
    "calm": ([1.06, 0.44, 0.5], [1.06, 0.56, 0.5]),
}
TOKEN = re.compile(r"[\wÀ-ÿ']+", re.UNICODE)


def _q(t: float) -> float:
    """Quantiza no frame, para que cenas consecutivas não acumulem desvio."""
    return round(round(t * FPS) / FPS, 4)


def _norm(word: str) -> str:
    return "".join(TOKEN.findall(word.lower()))


def quote_word_times(quote: str, words: list[dict], start: float, end: float) -> list[float] | None:
    """Instante em que cada palavra da citação é falada, procurando o trecho mais parecido da narração entre
    `start` e `end`. None se a citação não estiver na narração (o LLM parafraseou): ela é descartada."""
    q = [_norm(w) for w in quote.split()]
    q = [w for w in q if w]
    span = [w for w in words if w["start"] >= start - 0.3 and w["end"] <= end + 0.3]
    norm = [_norm(w["text"]) for w in span]
    if not q or len(span) < len(q):
        return None
    best, best_ratio = None, 0.0
    target = " ".join(q)
    for i in range(len(span) - len(q) + 1):
        ratio = difflib.SequenceMatcher(None, target, " ".join(norm[i:i + len(q)])).ratio()
        if ratio > best_ratio:
            best, best_ratio = i, ratio
    if best is None or best_ratio < 0.8:
        return None
    times = [span[best + k]["start"] for k in range(len(q))]
    # palavras do texto exibido que não são tokens (ex.: "—") herdam o tempo da anterior
    out, k = [], 0
    for w in quote.split():
        if _norm(w):
            out.append(times[min(k, len(times) - 1)])
            k += 1
        else:
            out.append(out[-1] if out else times[0])
    return out


def _image_aspect(path) -> float | None:
    try:
        from PIL import Image

        with Image.open(path) as im:
            return im.width / im.height
    except Exception:  # noqa: BLE001
        return None


def _transition(spec: dict | None, prev_dur: float, dur: float) -> dict:
    """Transição limitada a 40% das duas cenas; curta demais vira corte."""
    if not spec:
        return {"type": "cut"}
    d = min(float(spec["duration"]), 0.4 * prev_dur, 0.4 * dur)
    return {"type": spec["type"], "duration": round(d, 3)} if d >= 0.15 else {"type": "cut"}


def build_sfx(cues: list[tuple[str, float, str]], params: dict, sounds: list[dict] | None) -> list[dict]:
    """Escolhe um som por deixa e aplica o espaçamento mínimo (cliques e risers ficam de fora da regra)."""
    gap = params.get("sfx_min_gap_seconds", 1.2)
    gains = params.get("sfx_gain_db", {})
    out, last = [], -1e9
    for n, (cat, at, align) in enumerate(sorted(cues, key=lambda c: c[1])):
        spaced = cat not in ("click", "riser")
        if spaced and at - last < gap:
            continue
        if spaced:
            last = at
        snd = sfx_library.pick(cat, n, sounds)
        out.append({"category": cat, "start": round(at, 3), "align": align, "gain_db": gains.get(cat, -22),
                    "file": snd["file"], "source": snd["source"], "license": snd.get("license", ""),
                    "author": snd.get("author", ""), "page_url": snd.get("page_url", "")})
    return out


def run(ctx: JobContext) -> str:
    plan = ctx.read_json("plan.json")
    selection = ctx.read_json("selection.json") if (ctx.dir / "selection.json").exists() else {}
    words = ctx.read_json("transcript.json")["words"] if (ctx.dir / "transcript.json").exists() else []
    direction = get_direction(ctx.config.direction)
    params = direction.params
    cfg = ctx.config
    _, period_look = video_look(load_bible(ctx.dir), cfg)

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

    transitions = params.get("transitions") or {"chapter": {"type": "fade",
                                                            "duration": params.get("crossfade_seconds", 0.3)}}
    motion_cfg = params.get("video_motion") or {}
    scenes, overlays, mute = [], [], []
    cues: list[tuple[str, float, str]] = []  # (categoria, instante, alinhamento)
    last_overlay = last_reveal = last_calm = last_quote = -1e9
    min_gap = params.get("overlay_min_gap_seconds", 20)
    chapter_n = 0
    prev_ctx: str | None = None
    for n, s in enumerate(merged):
        e = s["entry"]
        start, end = _q(s["start"]), _q(s["end"])
        dur = end - start
        prev_dur = scenes[-1]["end"] - scenes[-1]["start"] if scenes else 0.0
        emphasis = s.get("emphasis") or "none"
        is_image = bool(e.get("is_image")) or e["source"].startswith("ai_image")
        context = s.get("context") or {}
        ctx_id = context.get("id")
        entering = bool(ctx_id and ctx_id != prev_ctx and (n > 0 or context.get("setting_type") == "historical"))
        prev_ctx = ctx_id or prev_ctx

        # transição de entrada: capítulo > troca de contexto > revelação > passagem calma; o resto é corte seco
        kind = None
        if n > 0:
            if s.get("chapter_break"):
                kind = "chapter"
            elif entering:
                kind = "context"
            elif emphasis == "reveal" and start - last_reveal >= params.get("reveal_min_gap_seconds", 30):
                kind = "reveal"
            elif (emphasis == "calm" and (merged[n - 1].get("emphasis") or "none") == "calm"
                  and start - last_calm >= params.get("calm_dissolve_min_gap_seconds", 15)):
                kind = "calm"
        trans = _transition(transitions.get(kind) if kind else None, prev_dur, dur)
        if trans["type"] == "cut":
            kind = None
        xfade = trans.get("duration", 0.0)
        if kind == "reveal":
            last_reveal = start
            cues += [("riser", start, "end"), ("impact", start, "start")]
        elif kind == "calm":
            last_calm = start
        elif kind == "context":
            cues.append(("whoosh", start, "peak"))

        scene = {"id": s["id"], "start": start, "end": end, "source": e["source"], "asset": e["asset"],
                 "in": e.get("in", 0.0), "motion": None, "transition_in": trans, "emphasis": emphasis}
        if is_image:
            a, b = KENBURNS_BY_EMPHASIS.get(emphasis) or KENBURNS[n % len(KENBURNS)]
            scene["motion"] = {"type": "kenburns", "from": a, "to": b}
            aspect = _image_aspect(ctx.dir / e["asset"])
            # foto de arquivo, retrato ou panorâmica: inteira numa moldura sobre um fundo desfocado, sem corte
            if params.get("photo_frame") and (e["source"] == "archive" or (aspect and not 1.45 <= aspect <= 2.1)):
                scene["frame"] = "photo"
        else:
            m = motion_cfg.get(emphasis) if emphasis in ("tension", "reveal") else None
            if not m and motion_cfg.get("long") and dur >= motion_cfg.get("long_scene_seconds", 8.0):
                m = motion_cfg["long"]
            if m:
                scene["motion"] = {"type": "push", "from": m["from"], "to": m["to"]}
        # gradação uniforme por bloco histórico: costura reconstituição, pintura e IA numa mesma atmosfera (§8)
        grade = grade_for(context.get("footage_feasibility"), period_look) if cfg.period_grade else None
        if grade:
            scene["grade"] = grade
        scenes.append(scene)

        if kind == "context" and params.get("light_leaks") and context.get("setting_type") == "historical":
            overlays.append({"type": "light_leak", "start": start, "end": _q(min(end - 0.1, start + 1.6)),
                             "text": "", "style": f"{direction.id}.light_leak"})

        chapter = bool(s.get("chapter_break") and s.get("chapter_title") and n > 0)
        if chapter:
            chapter_n += 1
            d = params.get("chapter_title_seconds", 3.0)
            overlays.append({"type": "chapter", "start": _q(start + xfade), "end": _q(min(end, start + xfade + d)),
                             "text": s["chapter_title"], "index": chapter_n, "style": f"{direction.id}.chapter"})
            last_overlay = start
        # card de lugar e data ao entrar num bloco de contexto (no 1º bloco, só se for histórico)
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
        # citação em tela cheia, palavra por palavra no ritmo da narração (as legendas somem enquanto ela dura)
        quote = (s.get("quote") or "").strip()
        if quote and dur >= 3.0 and start - last_quote >= params.get("quote_min_gap_seconds", 60):
            times = quote_word_times(quote, words, start, end) if words else None
            if times is not None:
                q_start = _q(max(start + 0.15, times[0] - 0.6))  # a tela escurece pouco antes da 1ª palavra
                q_end = _q(min(end - 0.1, max(times[-1] + 1.6, q_start + 3.0), q_start + params.get(
                    "quote_max_seconds", 9.0)))
                overlays.append({"type": "quote", "start": q_start, "end": q_end, "text": quote,
                                 "word_times": [round(max(0.0, t - q_start), 3) for t in times],
                                 "backdrop": "blur", "style": f"{direction.id}.quote"})
                mute.append([q_start, q_end])
                last_quote = last_overlay = start
                continue
            ctx.issue("QUOTE_DROPPED", f"Citação descartada (não está na narração): \"{quote[:80]}\"", scene=s["id"])
        if s.get("highlight") and start - last_overlay >= min_gap:
            d = params.get("overlay_duration_seconds", 3.5)
            o_start = _q(start + 0.4)
            overlays.append({"type": "highlight", "start": o_start, "end": _q(min(end - 0.1, o_start + d)),
                             "text": s["highlight"], "style": f"{direction.id}.highlight"})
            last_overlay = start

    overlays = fix_overlay_language(ctx, overlays, merged)

    # efeitos sonoros: as deixas das transições + as que cada animação declara
    sfx_events: list[dict] = []
    if getattr(cfg, "sfx", True):
        sfx_of = getattr(direction.overlays, "SFX", {}) if direction.overlays else {}
        for o in overlays:
            if o["type"] in sfx_of:
                cues += [(cat, o["start"] + rel, "start") for cat, rel in sfx_of[o["type"]](o)]
        if cues:
            sfx_library.ensure({c[0] for c in cues}, issue=lambda msg, detail: ctx.issue(
                "SFX_FALLBACK", msg, detail=detail, severity="warning"))
            sfx_events = build_sfx(cues, params, sfx_library.load())

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
        "version": 2, "resolution": [1920, 1080], "fps": FPS,
        "duration": scenes[-1]["end"],
        "audio": {"narration": "audio/narration.wav", "music": music, "sfx": sfx_events},
        "scenes": scenes,
        "overlays": overlays,
        "subtitles": {"file": "subs.ass", "mute": mute} if ctx.config.subtitles else None,
        "look": params.get("film_look") if getattr(cfg, "film_look", True) else None,
        "style": {"font": ctx.config.font, "color_primary": ctx.config.color_primary,
                  "color_accent": ctx.config.color_accent, "subtitle": ctx.config.subtitle_style.model_dump()},
        "direction": direction.id,
    }
    out = ctx.write_json("timeline.json", timeline)
    write_report(ctx)
    write_visual_report(ctx)
    return str(out)
