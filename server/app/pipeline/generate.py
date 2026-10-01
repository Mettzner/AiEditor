"""Etapa 5: geração por IA → assets/ (§8).

Imagens: Darkvi (rate limiter global de 5/min). Vídeo de IA (fal.ai) entra na Fase 3; até lá,
cenas pedidas como vídeo viram imagem com movimento. Se a IA falhar, a cena tenta os bancos.
"""
from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from ..db import session_scope
from ..models import Channel
from ..providers.darkvi import images as darkvi_images
from ..providers.darkvi.client import DarkviError
from ..worker.context import JobContext
from .select import NoCandidate, Selector


def _reference_key(ctx: JobContext) -> str | None:
    cfg = ctx.config
    if cfg.reference_key or not cfg.reference_image:
        return cfg.reference_key
    path = Path(cfg.reference_image)
    if not path.exists():
        return None
    try:
        key = darkvi_images.upload_reference(path)
    except darkvi_images.ReferenceNotAllowed:
        ctx.issue("REFERENCE_NOT_ALLOWED", "O plano da Darkvi não aceita imagem de referência; gerando sem ela")
        return None
    except DarkviError as e:
        ctx.issue("AI_GEN_FAILED", "Falha ao enviar a imagem de referência; gerando sem ela", detail=str(e))
        return None
    if ctx.channel_id:  # salva a key no preset para reuso
        with session_scope() as s:
            ch = s.get(Channel, ctx.channel_id)
            if ch:
                ch.preset = {**ch.preset, "reference_key": key}
                s.add(ch)
                s.commit()
    return key


def run(ctx: JobContext) -> str:
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

    ref = _reference_key(ctx)
    quota_hit = threading.Event()
    style = ctx.config.visual_style
    done = 0

    def to_stock(scene: dict, reason: str) -> dict:
        try:
            entry = sel.select_scene(scene)
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
        prompt = darkvi_images.build_prompt(scene["visual_intent"], style)
        dest = ctx.path("assets", f"{scene['id']}.png")
        try:
            darkvi_images.generate(prompt, dest, reference_key=ref)
        except darkvi_images.QuotaExhausted as e:
            if not quota_hit.is_set():
                quota_hit.set()
                ctx.issue("IMAGE_QUOTA_EXHAUSTED", "Saldo diário de imagens da Darkvi acabou; as cenas restantes "
                          "vão para os bancos (sem provedor alternativo configurado)", detail=str(e.body))
            return to_stock(scene, "cota diária da Darkvi esgotada")
        except DarkviError as e:
            return to_stock(scene, str(e))
        return {"source": "ai_image", "provider": "darkvi", "asset": f"assets/{scene['id']}.png", "prompt": prompt}

    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = {pool.submit(work, s): s for s in scenes}
        for fut in as_completed(futures):
            ctx.check_cancel()
            scene = futures[fut]
            sel.selection[scene["id"]] = fut.result()
            sel.save()
            done += 1
            ctx.progress(done / len(scenes), f"Gerando imagens {done}/{len(scenes)}")
    return str(ctx.dir / "selection.json")
