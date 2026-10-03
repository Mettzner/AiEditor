"""Etapa 5: geração por IA → assets/ (§8).

Imagens: Darkvi (rate limiter global de 5/min). As cenas planejadas como IA começam a ser geradas logo
depois do planejamento, em paralelo com a busca nos bancos (select.run); esta etapa só cuida do que sobrar
(cenas migradas para IA). Vídeo de IA (fal.ai) entra na Fase 3; até lá, cenas pedidas como vídeo viram
imagem com movimento. Se a IA falhar, a cena tenta os bancos.
"""
from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Callable

from ..db import session_scope
from ..models import Channel
from ..providers.darkvi import images as darkvi_images
from ..providers.darkvi.client import DarkviError
from ..worker.context import JobContext
from .imagegen import generate_validated

_ref_lock = threading.Lock()


def _reference_key(ctx: JobContext) -> str | None:
    cfg = ctx.config
    with _ref_lock:
        if cfg.reference_key or not cfg.reference_image:
            return cfg.reference_key
        path = Path(cfg.reference_image)
        if not path.exists():
            return None
        try:
            key = darkvi_images.upload_reference(path)
        except darkvi_images.ReferenceNotAllowed:
            ctx.issue("REFERENCE_NOT_ALLOWED", "O plano da Darkvi não aceita imagem de referência; gerando sem ela")
            cfg.reference_image = None
            return None
        except DarkviError as e:
            ctx.issue("AI_GEN_FAILED", "Falha ao enviar a imagem de referência; gerando sem ela", detail=str(e))
            cfg.reference_image = None
            return None
        cfg.reference_key = key
        if ctx.channel_id:  # salva a key no preset para reuso
            with session_scope() as s:
                ch = s.get(Channel, ctx.channel_id)
                if ch:
                    ch.preset = {**ch.preset, "reference_key": key}
                    s.add(ch)
                    s.commit()
        return key


def generate_scenes(ctx: JobContext, sel, scenes: list[dict],
                    on_done: Callable[[dict, dict], None] | None = None) -> dict[str, dict]:
    """Gera (e valida) as imagens das cenas; se a IA falhar, a cena vai para bancos/fotos via o seletor."""
    from .select import NoCandidate

    if not scenes:
        return {}
    ref = _reference_key(ctx)
    quota_hit = threading.Event()
    style = ctx.config.visual_style
    results: dict[str, dict] = {}
    stats_all: list[dict] = []
    timer = getattr(sel, "timer", None)

    def to_stock(scene: dict, reason: str) -> dict:
        try:
            entry = sel.select_scene(scene, allow_ai=False)
            ctx.issue("SCENE_MIGRATED", "Geração por IA indisponível; cena foi para os bancos", scene=scene["id"],
                      detail=reason)
            return entry
        except NoCandidate as e:
            ctx.issue("AI_GEN_FAILED", "IA e bancos falharam; a cena vai repetir a anterior", scene=scene["id"],
                      detail=f"{reason} | {e}")
            return {"source": "missing", "reason": reason}

    def work(scene: dict) -> dict:
        if quota_hit.is_set():
            return to_stock(scene, "cota diária da Darkvi esgotada")
        dest = ctx.path("assets", f"{scene['id']}.png")
        stats: dict = {"_timer": timer, "_scene": scene["id"]} if timer else {}
        stats_all.append(stats)
        try:
            if timer:
                with timer.track(scene["id"], "geração de imagem (Darkvi)"):
                    result = generate_validated(scene, sel.brief, style, dest, sel.cfg, stats, reference_key=ref,
                                                media_style=ctx.config.media_style,
                                                period_look=ctx.config.period_look)
            else:
                result = generate_validated(scene, sel.brief, style, dest, sel.cfg, stats, reference_key=ref,
                                            media_style=ctx.config.media_style,
                                            period_look=ctx.config.period_look)
        except darkvi_images.QuotaExhausted as e:
            if not quota_hit.is_set():
                quota_hit.set()
                ctx.issue("IMAGE_QUOTA_EXHAUSTED", "Saldo diário de imagens da Darkvi acabou; as cenas restantes "
                          "vão para os bancos (sem provedor alternativo configurado)", detail=str(e.body))
            return to_stock(scene, "cota diária da Darkvi esgotada")
        except DarkviError as e:
            ctx.issue("AI_GEN_FAILED", "Geração da imagem falhou; cena foi para os bancos", scene=scene["id"],
                      detail=str(e))
            return to_stock(scene, str(e))
        except Exception as e:  # noqa: BLE001 — uma imagem com problema nunca derruba a etapa
            ctx.issue("AI_GEN_FAILED", "Erro inesperado na imagem de IA; cena foi para os bancos", scene=scene["id"],
                      detail=f"{type(e).__name__}: {e}"[:500])
            return to_stock(scene, f"{type(e).__name__}: {e}")
        if stats.get("vision_quota"):
            sel.note_vision_quota(stats["vision_quota"])
        if not result.ok:
            ctx.issue("AI_IMAGE_REJECTED", "A imagem gerada reprovou 2x na validação; cena foi para os bancos",
                      scene=scene["id"], detail=f"visto: {result.seen} | {result.reason}")
            return to_stock(scene, "imagem gerada reprovada na validação")
        return {"source": "ai_image", "source_used": "ai_image", "is_image": True, "provider": "darkvi",
                "asset": f"assets/{dest.name}", "prompt": result.prompt, "score": result.score, "seen": result.seen,
                "attempts": result.attempts, "vision_calls": stats.get("vision_calls", 0), "method": "ai_image",
                "style": result.style}

    # a Darkvi aceita 5 gerações/min (token bucket global); 3 em paralelo mantém a fila andando
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = {pool.submit(work, s): s for s in scenes}
        for fut in as_completed(futures):
            scene = futures[fut]
            entry = fut.result()
            results[scene["id"]] = entry
            if on_done:
                on_done(scene, entry)
    cost = sum(s.get("vision_cost", 0.0) for s in stats_all)
    calls = sum(s.get("vision_calls", 0) for s in stats_all)
    if calls or cost:
        ctx.record_llm({"task": "visão (Gemini) - imagens geradas", "model": sel.cfg["gemini_model"], "calls": calls,
                        "cost": round(cost, 6)}, step="generate")
    return results


def run(ctx: JobContext) -> str:
    from .select import Selector

    plan = ctx.read_json("plan.json")
    sel = Selector(ctx)
    scenes = [s for s in plan["scenes"]
              if (s["source"] == "ai" and s["id"] not in sel.selection)
              or sel.selection.get(s["id"], {}).get("source") == "migrate_ai"]
    if not scenes:
        return str(ctx.dir / "selection.json")
    wanted_video = [s for s in scenes if ctx.config.ai_media == "video"
                    or (ctx.config.ai_media == "both" and s.get("ai_kind") == "video")]
    if wanted_video:
        ctx.issue("SCENE_MIGRATED", f"{len(wanted_video)} cena(s) pediam vídeo de IA; geradas como imagem com "
                  "movimento (vídeo de IA entra na Fase 3)")
    done = [0]

    def on_done(scene: dict, entry: dict) -> None:
        ctx.check_cancel()
        sel.selection[scene["id"]] = entry
        sel.save()
        done[0] += 1
        ctx.progress(done[0] / len(scenes), f"Gerando imagens {done[0]}/{len(scenes)}")

    generate_scenes(ctx, sel, scenes, on_done)
    return str(ctx.dir / "selection.json")
