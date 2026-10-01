"""Etapa 4: busca e seleção das cenas reais → assets/ + selection.json (§7).

Fase 1: filtro técnico + pré-ranking textual. YouTube ainda não tem adapter: cenas alocadas a ele
migram para os bancos (SCENE_MIGRATED).
"""
from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

from ...config import get_secret
from ...db import session_scope
from ...models import UsedAsset
from ...providers.http import download
from ...providers.stock import enabled_providers
from ...worker.context import JobContext, StepError
from .prerank import rank, technical_filter
from .search import search_all


class NoCandidate(Exception):
    pass


class Selector:
    def __init__(self, ctx: JobContext):
        self.ctx = ctx
        self.providers = enabled_providers()
        self.per_page = int(ctx.settings["selection"]["per_page"])
        self.lock = threading.Lock()
        self.selection: dict[str, dict] = (ctx.read_json("selection.json")
                                           if (ctx.dir / "selection.json").exists() else {})
        self.used = {f"{v['provider']}:{v['external_id']}" for v in self.selection.values() if v.get("external_id")}

    def save(self) -> None:
        with self.lock:
            self.ctx.write_json("selection.json", self.selection)

    def select_scene(self, scene: dict) -> dict:
        """Escolhe e baixa um clipe de banco para a cena. Lança NoCandidate se nada servir."""
        if not self.providers:
            raise NoCandidate("nenhum banco de vídeo ativo com chave configurada")
        dur = scene["end"] - scene["start"]
        cands, errors = search_all(self.providers, scene["queries"], self.per_page, self.ctx.config.search_language)
        pool = []
        with self.lock:
            used = set(self.used)
        # relaxa os critérios em degraus: 1080p com duração → 720p com duração → 720p qualquer duração
        for min_h, need_dur in ((1080, True), (720, True), (720, False)):
            pool = technical_filter(cands, dur, used, min_height=min_h, require_duration=need_dur)
            if pool:
                break
        if not pool:
            raise NoCandidate(f"{len(cands)} candidatos, nenhum passou no filtro técnico; erros: {errors[:3]}")
        for best in rank(pool, scene["queries"], dur):
            with self.lock:
                if best.key in self.used:
                    continue
                self.used.add(best.key)
            rend = best.best_rendition()
            assert rend
            dest = self.ctx.path("assets", f"{scene['id']}.mp4")
            download(rend.url, dest)
            clip_in = round(max(0.0, (best.duration - dur) * 0.25), 2) if best.duration > dur else 0.0
            entry = {"source": "stock", "provider": best.provider, "external_id": best.external_id,
                     "page_url": best.page_url, "title": best.title, "author": best.author,
                     "author_url": best.author_url, "query": best.query, "score": best.score,
                     "asset": f"assets/{scene['id']}.mp4", "in": clip_in, "clip_duration": best.duration,
                     "resolution": [rend.width, rend.height]}
            with session_scope() as s:
                s.add(UsedAsset(channel_id=self.ctx.channel_id, production_id=self.ctx.production_id,
                                source=best.provider, external_id=best.external_id))
                s.commit()
            return entry
        raise NoCandidate("todos os candidatos já foram usados neste vídeo")


def run(ctx: JobContext) -> str:
    plan = ctx.read_json("plan.json")
    sel = Selector(ctx)
    real = [s for s in plan["scenes"] if s["source"] in ("stock", "youtube")]
    youtube = [s for s in real if s["source"] == "youtube"]
    if youtube:
        ctx.issue("SCENE_MIGRATED", f"{len(youtube)} cena(s) do YouTube foram para os bancos: o adapter do "
                  "YouTube entra na Fase 3")
    todo = [s for s in real if s["id"] not in sel.selection]
    if real and not sel.providers:
        raise StepError("PROVIDER_QUOTA", "Nenhum banco de vídeo ativo: configure a chave do Pexels ou do Pixabay")

    ai_possible = bool(get_secret("darkvi")) and ctx.config.real_pct < 100
    done = len(real) - len(todo)
    failures = 0
    with ThreadPoolExecutor(max_workers=int(ctx.settings["selection"]["parallel_scenes"])) as pool:
        futures = {pool.submit(sel.select_scene, s): s for s in todo}
        for fut in as_completed(futures):
            scene = futures[fut]
            ctx.check_cancel()
            try:
                sel.selection[scene["id"]] = fut.result()
            except NoCandidate as e:
                failures += 1
                if ai_possible:
                    sel.selection[scene["id"]] = {"source": "migrate_ai", "reason": str(e)}
                    ctx.issue("SCENE_MIGRATED", "Nenhum clipe serviu; a cena vai para geração por IA",
                              scene=scene["id"], detail=str(e))
                else:
                    sel.selection[scene["id"]] = {"source": "missing", "reason": str(e)}
                    ctx.issue("SCENE_LOW_SCORE", "Nenhum clipe encontrado; a cena vai repetir a anterior",
                              scene=scene["id"], detail=str(e))
            sel.save()
            done += 1
            ctx.progress(done / max(1, len(real)), f"Selecionando cenas {done}/{len(real)}")

    if real and failures == len(real):
        raise StepError("PROVIDER_QUOTA", "Nenhuma cena encontrou clipe: verifique as chaves e cotas dos bancos")
    sel.save()
    return str(ctx.dir / "selection.json")
