"""Etapa 4: busca e seleção das cenas reais → assets/ + selection.json + timing_report.json.

Regras: MELHORIA_SELECAO_DE_CENAS.md (funil econômico, cota do YouTube), MELHORIA_PRECISAO_VISUAL.md
(assunto primeiro, validação que descreve antes de julgar) e AJUSTE_ESTILO_CONTEXTUAL.md (estilo guiado pelo
contexto, franquias sempre bloqueadas, velocidade).

Três fases, para paralelizar sem perder a garantia de não repetir clipes:
  A) avaliar: cenas em paralelo (6 no modo rápido); cada uma percorre a cadeia de fontes e guarda seus melhores
     colocados já pontuados (nenhum download);
  B) resolver (local, sem chamadas): clipe repetido fica com a cena de maior nota e a outra usa o 2º colocado;
     bônus de +0,5 para manter blocos de estilo coerentes entre cenas vizinhas;
  C) baixar os vencedores em paralelo (4 por vez), caindo para o próximo colocado se o download falhar.
As imagens das cenas planejadas como IA (Darkvi, 5/min) são geradas em segundo plano desde o início.

Cadeia de fontes por cena (imagem gerada só quando o tipo de mídia de IA permite):
    planejada YouTube: YouTube → bancos de vídeo → imagem gerada (Darkvi) → fotos de banco
    planejada bancos:  bancos de vídeo → imagem gerada (Darkvi) → fotos de banco
Cena de contexto histórico (CONTEXTO_PROFUNDO_DO_ROTEIRO.md §3): a ordem segue a viabilidade da época, por exemplo
    antes de ~1890:  reconstituição (bancos) → acervos históricos → plano atemporal → imagem de época → fotos
    1890–2000:       arquivo (YouTube/acervos) → reconstituição → plano atemporal → imagem de época → fotos
e a IA de visão zera qualquer candidato com anacronismo visível.
"""
from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field

from ...config import get_secret, load_settings
from ...db import session_scope
from ...models import UsedAsset
from ...providers.darkvi import images as darkvi_images
from ...providers.darkvi.client import DarkviError
from ...providers.http import ProviderError, download
from ...providers.llm import gemini
from ...providers.llm.base import call_llm
from ...providers.stock import enabled_providers
from ...providers.stock.archives import ARCHIVES
from ...providers.youtube import client as youtube
from ...providers.youtube import quota as yt_quota
from ...worker.context import JobContext, StepError
from ..context import HISTORICAL, STRATEGY_LABEL, is_historical, scene_anachronisms, strategy_order
from ..plan import load_bible
from ..timing import Timings
from ..visual import (REWRITE_SYSTEM, QueryRewriteBatch, compact_json, image_type_for, sanitize_queries, scene_style,
                      style_of_realism, video_type_for)
from .funnel import Choice, SceneContext, choose
from .prerank import rank, technical_filter
from .search import cached_search, search_all

log = logging.getLogger("aieditor.select")

SOURCE_LABEL = {"youtube": "YouTube", "stock": "bancos de vídeo", "stock_photo": "fotos de banco",
                "ai_image": "imagem gerada", "archive": "acervos históricos", "timeless": "planos atemporais"}
# etapa da cadeia → estratégia de época registrada no relatório visual (§10.7)
STEP_STRATEGY = {"stock": "reconstituição", "archive": "arquivo", "timeless": "atemporal", "ai_image": "IA",
                 "stock_photo": "foto de banco"}
STYLE_REJECT_REASONS = ("estilo", "franquia", "interface/marca")
CONTINUITY_BONUS = 0.5


class NoCandidate(Exception):
    pass


@dataclass
class Option:
    candidate: object
    score: float
    pos: float
    realism: str | None
    source: str
    method: str
    seen: str = ""
    era: dict = field(default_factory=dict)


@dataclass
class Decision:
    """Resultado da fase A para uma cena: opções pontuadas (sem download) ou um asset pronto (imagem gerada)."""

    scene: dict
    planned: str
    options: list[Option] = field(default_factory=list)
    entry: dict | None = None
    pick: int = 0
    kind: str = "ok"  # ok | low_score | generic
    stats: dict = field(default_factory=dict)
    queries: list[str] = field(default_factory=list)
    must_avoid: list[str] = field(default_factory=list)
    previous: str = ""
    allow_ai: bool = True
    tried_sources: set[str] = field(default_factory=set)
    pending_rewrite: dict | None = None

    @property
    def best(self) -> Option | None:
        return self.options[self.pick] if self.options and self.pick < len(self.options) else None


def _mode_cfg(selection: dict, mode: str) -> dict:
    return {**selection, **(selection.get("modes", {}).get(mode) or {})}


class Selector:
    def __init__(self, ctx: JobContext):
        self.ctx = ctx
        self.cfg = _mode_cfg(ctx.settings["selection"], getattr(ctx.config, "selection_mode", "fast"))
        self.providers = enabled_providers()
        self.lock = threading.Lock()
        self.selection: dict[str, dict] = (ctx.read_json("selection.json")
                                           if (ctx.dir / "selection.json").exists() else {})
        self.used = {f"{v['provider']}:{v['external_id']}" for v in self.selection.values() if v.get("external_id")}
        self.youtube_on = bool(ctx.settings["youtube"].get("enabled", True) and get_secret("youtube"))
        self.brief: dict = load_bible(ctx.dir)  # Bíblia de Contexto (superconjunto do antigo brief)
        cfg = ctx.config
        self.media_style = getattr(cfg, "media_style", "real_preferred")
        self.period_look = getattr(cfg, "period_look", "cinematic")
        archives = ctx.settings.get("archives") or {}
        self.archives_on = bool(archives.get("enabled", True))
        self.cfg["archive_min_photo_height"] = archives.get("min_photo_height", 500)
        self.cfg["archive_min_video_height"] = archives.get("min_video_height", 240)
        self.ai_image_on = bool(get_secret("darkvi")) and cfg.real_pct < 100 and cfg.ai_media != "video"
        self.franchises = load_settings()["selection"].get("blocked_franchises", [])
        self.reactive_quota_reported = False
        self.vision_quota_reported = False
        self.darkvi_quota_hit = False
        self.timer = Timings()
        self.totals: dict = {"searches": 0, "vision_calls": 0, "vision_failed": 0, "vision_cost": 0.0}

    def save(self) -> None:
        with self.lock:
            self.ctx.write_json("selection.json", self.selection)

    def note_vision_quota(self, msg: str) -> None:
        with self.lock:
            first = not self.vision_quota_reported
            self.vision_quota_reported = True
        if first:
            self.ctx.issue("VISION_QUOTA", "Cota do Gemini esgotada: as cenas seguintes são escolhidas pelo ranking "
                           "de texto, sem espera. Ative o faturamento no Google AI Studio para mais avaliações por "
                           "dia.", detail=msg)

    # ---- contexto da cena ------------------------------------------------------------------------
    def _scene_ctx(self, scene: dict, previous: str, must_avoid: list[str]) -> SceneContext:
        allowance, allowed = scene_style(scene, self.media_style)
        return SceneContext(
            intent=scene.get("visual_intent", ""), text=scene["text"], style=self.ctx.config.visual_style,
            previous=previous, subject=scene.get("subject", ""), must_show=scene.get("must_show") or [],
            must_avoid=list(dict.fromkeys(must_avoid + scene_anachronisms(scene)
                                          + list(self.brief.get("global_avoid") or []))),
            topic=self.brief.get("topic", ""), visual_world=self.brief.get("visual_world", ""),
            allowance=allowance, allowed_styles=allowed, style_reason=scene.get("style_reason", ""),
            context=scene.get("context") or {})

    @staticmethod
    def _timeless_view(scene: dict) -> dict:
        """Plano atemporal (§3.1): o assunto vira o plano seguro (mãos, vela, natureza), sem must_show de época."""
        return {**scene, "subject": scene.get("timeless_query") or scene.get("subject", ""),
                "visual_intent": scene.get("timeless_alternative") or scene.get("visual_intent", ""),
                "must_show": [], "queries": [scene["timeless_query"]] if scene.get("timeless_query") else []}

    def _queries_for(self, step: str, scene: dict, queries: list[str]) -> list[str]:
        """Queries de cada etapa. Cena histórica: YouTube usa a de reconstituição (antes de ~1890) ou a de arquivo
        (depois), conforme a viabilidade da época; acervos usam a de arquivo; atemporal, a do plano seguro."""
        if not is_historical(scene):
            return queries
        feas = scene["context"].get("footage_feasibility")
        archival = [q for q in [scene.get("archival_query")] if q]
        if step == "youtube":
            return (archival or queries) if feas in ("early_film", "historical_modern") else queries
        if step == "archive":
            return archival + queries[:1]
        if step == "timeless":
            return [scene["timeless_query"]] if scene.get("timeless_query") else []
        return queries

    # ---- busca por fonte ---------------------------------------------------------------------
    def _search(self, source: str, scene: dict, queries: list[str], stats: dict) -> list:
        lang = self.ctx.config.search_language
        per_page = int(self.cfg["results_per_query"])
        _, allowed = scene_style(scene, self.media_style)
        if source == "youtube":
            out = []
            for q in queries[: int(self.cfg["youtube_queries_per_scene"])]:
                out += cached_search("youtube", "video", q, per_page, lang,
                                     lambda q=q: youtube.search(q, per_page=per_page, lang=lang), stats)
            return out
        if source == "archive":
            feas = (scene.get("context") or {}).get("footage_feasibility")
            out, errors = [], []
            for q in queries[:2]:
                try:
                    out += cached_search("archives", "photo", q, per_page, "en",
                                         lambda q=q: ARCHIVES.search_photos(q, per_page=per_page), stats)
                    if feas in ("early_film", "historical_modern"):  # há filme de arquivo a partir de ~1890
                        out += cached_search("archives", "video", q, min(per_page, 6), "en",
                                             lambda q=q: ARCHIVES.search(q, per_page=6), stats)
                except ProviderError as e:
                    errors.append(str(e))
            if errors and not out:
                log.warning("cena %s (acervos): %s", scene["id"], errors[:2])
            return list({c.key: c for c in out}.values())
        photos = source == "stock_photo"
        if source == "timeless":
            allowed = ["real_footage"]
        media_type = image_type_for(allowed) if photos else video_type_for(allowed)
        cands, errors = search_all(self.providers, queries[: int(self.cfg["stock_queries_per_scene"])], per_page,
                                   lang, photos=photos, stats=stats, media_type=media_type)
        if errors and not cands:
            log.warning("cena %s (%s): %s", scene["id"], source, errors[:3])
        return cands

    def _try_source(self, source: str, scene: dict, previous: str, stats: dict, queries: list[str],
                    must_avoid: list[str]) -> Choice | None:
        sid = scene["id"]
        dur = scene["end"] - scene["start"]
        queries = self._queries_for(source, scene, queries)
        if source == "timeless":
            scene = self._timeless_view(scene)
        if not queries:
            return None
        with self.timer.track(sid, f"busca ({SOURCE_LABEL[source]})"):
            cands = self._search(source, scene, queries, stats)
        with self.lock:
            used = set(self.used)
        allowance, allowed = scene_style(scene, self.media_style)
        avoid = list(dict.fromkeys(must_avoid + scene_anachronisms(scene)))
        with self.timer.track(sid, "filtro + ranking de texto"):
            passed, rejected = technical_filter(cands, dur, used, self.cfg, youtube.max_duration(), avoid,
                                                self.brief.get("global_avoid") or [], allowance, allowed,
                                                self.franchises)
        stats["style_rejected"] = stats.get("style_rejected", 0) + sum(rejected.get(r, 0)
                                                                     for r in STYLE_REJECT_REASONS)
        if not passed:
            self.ctx.issue("SCENE_NO_CANDIDATES",
                           f"Nenhum candidato de {SOURCE_LABEL[source]} passou no filtro técnico",
                           scene=sid, detail=f"{len(cands)} encontrados; reprovados: {rejected}")
            return None
        vocab = (scene.get("context") or {}).get("search_vocabulary") or []
        ranked = rank(passed, queries + ([" ".join(vocab)] if vocab else []), scene.get("visual_intent", ""), dur,
                      int(self.cfg["prerank_keep"]), scene.get("subject", ""), scene.get("must_show") or [])
        stats["_timer"], stats["_scene"] = self.timer, sid
        choice = choose(ranked, self._scene_ctx(scene, previous, must_avoid), self.cfg, stats)
        stats["style_rejected"] += choice.style_rejected
        if stats.get("vision_quota"):
            self.note_vision_quota(stats["vision_quota"])
        return choice

    def rewrite_batch(self, items: list[tuple[dict, list[str], list[str], list[str]]],
                      ) -> dict[str, tuple[list[str], list[str]]]:
        """Reescreve as queries de VÁRIAS cenas numa única chamada barata (Haiku).

        items: (cena, queries usadas, must_avoid, o que foi visto nos melhores reprovados).
        Devolve {scene_id: (novas queries, itens extras para o must_avoid)}.
        """
        if not items:
            return {}
        payload = []
        for scene, queries, must_avoid, seen in items:
            _, allowed = scene_style(scene, self.media_style)
            payload.append({k: v for k, v in {
                "scene_id": scene["id"], "narration": scene["text"], "subject": scene.get("subject"),
                "literal": scene.get("literal"), "must_show": scene.get("must_show"),
                "visual_intent": scene.get("visual_intent"), "allowed_styles": allowed,
                "must_avoid": must_avoid, "queries_used": queries, "seen_in_rejected": seen,
            }.items() if v not in (None, [], "")})
        try:
            with self.timer.track("_global", "reescrita de queries (lote)"):
                out, usage = call_llm("rewrite", system=REWRITE_SYSTEM, user=compact_json(payload),
                                      schema=QueryRewriteBatch, max_tokens=150 + 120 * len(items))
            self.ctx.record_llm(usage, step="select")
        except Exception as e:  # noqa: BLE001
            log.warning("reescrita de queries em lote falhou: %s", e)
            return {}
        by_id = {it.scene_id: it for it in out.items}
        result = {}
        for scene, queries, must_avoid, _ in items:
            it = by_id.get(scene["id"])
            if not it:
                continue
            _, allowed = scene_style(scene, self.media_style)
            extra = [x for x in it.extra_must_avoid if x and x not in must_avoid][:5]
            new = sanitize_queries(scene.get("subject", ""), it.queries, must_avoid + extra, allowed)
            if new:
                result[scene["id"]] = (new, extra)
        return result

    # ---- imagem gerada ---------------------------------------------------------------------------
    def _ai_image(self, scene: dict, stats: dict) -> dict | None:
        from ..imagegen import generate_validated

        if self.darkvi_quota_hit:
            return None
        dest = self.ctx.path("assets", f"{scene['id']}.png")
        try:
            with self.timer.track(scene["id"], "geração de imagem (Darkvi)"):
                result = generate_validated(scene, self.brief, self.ctx.config.visual_style, dest, self.cfg, stats,
                                            reference_key=self.ctx.config.reference_key, media_style=self.media_style,
                                            period_look=self.period_look)
        except darkvi_images.QuotaExhausted:
            with self.lock:
                first = not self.darkvi_quota_hit
                self.darkvi_quota_hit = True
            if first:
                self.ctx.issue("IMAGE_QUOTA_EXHAUSTED", "Saldo diário de imagens da Darkvi acabou; as cenas seguem "
                               "para as fotos de banco")
            return None
        except DarkviError as e:
            log.warning("cena %s: geração Darkvi falhou: %s", scene["id"], e)
            self.ctx.issue("AI_GEN_FAILED", "Geração da imagem falhou; seguindo para a próxima opção",
                           scene=scene["id"], detail=str(e))
            return None
        except Exception as e:  # noqa: BLE001 — uma imagem com problema nunca derruba a seleção
            log.warning("cena %s: erro inesperado na imagem de IA: %s", scene["id"], e)
            self.ctx.issue("AI_GEN_FAILED", "Erro inesperado na imagem de IA; seguindo para a próxima opção",
                           scene=scene["id"], detail=f"{type(e).__name__}: {e}"[:500])
            return None
        if stats.get("vision_quota"):
            self.note_vision_quota(stats["vision_quota"])
        if not result.ok:
            self.ctx.issue("AI_IMAGE_REJECTED", "A imagem gerada reprovou 2x na validação; seguindo para a próxima "
                           "opção", scene=scene["id"], detail=f"visto: {result.seen} | {result.reason}")
            return None
        return {"source": "ai_image", "source_used": "ai_image", "is_image": True, "provider": "darkvi",
                "external_id": None, "asset": f"assets/{dest.name}", "prompt": result.prompt,
                "score": result.score, "seen": result.seen, "style": result.style,
                "attempts": result.attempts, "in": 0.0, "in_point": 0.0, "out_point": None, "method": "ai_image"}

    # ---- fase A: decidir sem baixar -------------------------------------------------------------
    def _chain(self, planned: str, allow_ai: bool, scene: dict | None = None) -> list[str]:
        feas = ((scene or {}).get("context") or {}).get("footage_feasibility")
        if feas in HISTORICAL:
            return self._period_chain(planned, allow_ai, scene or {}, feas)
        chain = ["youtube", "stock"] if planned == "youtube" else ["stock"]
        if self.ai_image_on and allow_ai:
            chain.append("ai_image")
        return chain + ["stock_photo"]

    def _period_chain(self, planned: str, allow_ai: bool, scene: dict, feas: str) -> list[str]:
        """Cadeia da cena histórica na ordem de estratégias da época (§3); fotos de banco só no fim."""
        steps: list[str] = []
        for strategy in scene.get("strategy_order") or strategy_order(feas, self.media_style):
            if strategy == "period_reenactment":
                steps += (["youtube"] if planned == "youtube" and feas in ("pre_photo", "pre_film") else []) + ["stock"]
            elif strategy in ("archival_art", "archival_film"):
                steps += (["youtube"] if planned == "youtube" and strategy == "archival_film" else [])
                steps += ["archive"] if self.archives_on else []
            elif strategy == "timeless" and scene.get("timeless_query"):
                steps.append("timeless")
            elif strategy == "ai_period" and self.ai_image_on and allow_ai:
                steps.append("ai_image")
        return list(dict.fromkeys(steps + ["stock_photo"]))

    def decide(self, scene: dict, previous: str = "", allow_ai: bool = True,
               skip: set[str] | None = None, rewrite: str = "inline", queries: list[str] | None = None,
               must_avoid: list[str] | None = None) -> Decision:
        """rewrite: "inline" (reescreve na hora, 1 chamada só desta cena), "defer" (para e devolve o pedido de
        reescrita para o lote da etapa) ou "none"."""
        planned = "youtube" if scene.get("source") == "youtube" else "stock"
        d = Decision(scene=scene, planned=planned, queries=list(queries or scene.get("queries") or []),
                     must_avoid=list(must_avoid if must_avoid is not None else scene.get("must_avoid") or []),
                     previous=previous, allow_ai=allow_ai)
        stats = d.stats
        vision = gemini.available()
        min_score = float(self.cfg["min_score"])
        rewrite_below = float(self.cfg.get("rewrite_below", min_score))
        reserve: list[Option] = []
        rewritten = rewrite == "none"
        for source in self._chain(planned, allow_ai, scene):
            if skip and source in skip:
                continue
            d.tried_sources.add(source)
            if source == "ai_image":
                d.entry = self._ai_image(scene, stats)
                if d.entry:
                    return d
                continue
            if source == "youtube":
                if not self.youtube_on:
                    continue
                if not yt_quota.can_search():
                    self.ctx.issue("YOUTUBE_QUOTA_FALLBACK", "Sem cota do YouTube hoje; cena foi para os bancos",
                                   scene=scene["id"])
                    continue
            elif source == "archive":
                if not self.archives_on:
                    continue
            elif not self.providers:
                continue
            try:
                choice = self._try_source(source, scene, previous, stats, d.queries, d.must_avoid)
                # reescrever só faz sentido para as buscas do assunto (acervos e atemporal têm queries próprias)
                if (vision and choice and choice.method.startswith("vision") and choice.score < rewrite_below
                        and not rewritten and source in ("youtube", "stock", "stock_photo")):
                    rewritten = True
                    seen = [choice.reason] + choice.rejected_seen
                    if rewrite == "defer":  # a etapa junta todas as cenas numa chamada só
                        d.pending_rewrite = {"source": source, "seen": seen[:3]}
                        return d
                    fix = self.rewrite_batch([(scene, d.queries, d.must_avoid, seen[:3])]).get(scene["id"])
                    if fix:
                        d.queries, extra = fix
                        d.must_avoid += extra
                        self.ctx.issue("QUERY_REWRITTEN", f"Queries reescritas: {d.queries}", scene=scene["id"],
                                       detail=f"visto: {seen[:3]} | novos itens a evitar: {extra}")
                        again = self._try_source(source, scene, previous, stats, d.queries, d.must_avoid)
                        if again and again.score > choice.score:
                            choice = again
            except youtube.QuotaExhausted as e:
                if e.reactive:
                    with self.lock:
                        first = not self.reactive_quota_reported
                        self.reactive_quota_reported = True
                    if first:
                        self.ctx.issue("PROVIDER_QUOTA", "O YouTube recusou por cota durante a busca; as próximas "
                                       "cenas vão direto para os bancos", scene=scene["id"])
                self.ctx.issue("YOUTUBE_QUOTA_FALLBACK", "Sem cota do YouTube; cena foi para os bancos",
                               scene=scene["id"])
                continue
            except ProviderError as e:
                log.warning("cena %s: %s falhou: %s", scene["id"], source, e)
                continue
            if choice is None:
                continue
            options = [Option(choice.candidate, choice.score, choice.best_pos, choice.realism, source, choice.method,
                              choice.reason, choice.era)]
            options += [Option(c, s, p, r, source, choice.method, era=(x[0] if x else {}))
                        for c, s, p, r, *x in choice.alternatives]
            # sem visão a nota é só do texto: não é comparável ao limiar da visão, mas um texto que mal cita o
            # assunto (nota < text_min_score) fica na reserva e a cadeia segue (ex.: imagem gerada). Na produção 5,
            # sem cota do Gemini, entraram "duffel bag" e "cachoeira" com nota 0,75–1,2 em cenas de 1887.
            text_based = not vision or choice.method == "text_fallback"
            if (text_based and choice.score >= float(self.cfg.get("text_min_score", 3.0))) or \
                    (not text_based and choice.score >= min_score):
                d.options = options + sorted(reserve, key=lambda o: o.score, reverse=True)
                return d
            reserve += [o for o in options if o.score > 0]  # nota 0 = estilo incompatível/franquia: nunca
        if reserve:
            d.options, d.kind = sorted(reserve, key=lambda o: o.score, reverse=True), "low_score"
            return d
        if vision and is_historical(scene):
            # cena de época: a foto genérica não passa pela IA de visão e poderia ser moderna (§9: nunca filmagem
            # moderna); sem opção validada, a cena estende a anterior ou vai para a IA
            d.options, d.kind = [], "generic"
            return d
        d.options, d.kind = self._generic_options(scene, stats, d.must_avoid), "generic"
        return d

    def _generic_options(self, scene: dict, stats: dict, must_avoid: list[str]) -> list[Option]:
        """Último recurso: foto genérica do assunto (query = só o subject), sem a IA de visão."""
        subject = scene.get("subject")
        if not subject or not self.providers:
            return []
        allowance, allowed = scene_style(scene, self.media_style)
        vocab = (scene.get("context") or {}).get("search_vocabulary") or []
        if is_historical(scene) and vocab:
            subject = f"{vocab[0]} {subject}"
        must_avoid = list(dict.fromkeys(must_avoid + scene_anachronisms(scene)))
        with self.timer.track(scene["id"], "busca (foto genérica)"):
            cands, _ = search_all(self.providers, [subject], int(self.cfg["results_per_query"]),
                                  self.ctx.config.search_language, photos=True, stats=stats,
                                  media_type=image_type_for(allowed))
        with self.lock:
            used = set(self.used)
        passed, _ = technical_filter(cands, 0, used, self.cfg, must_avoid=must_avoid,
                                     global_avoid=self.brief.get("global_avoid") or [], allowance=allowance,
                                     allowed_styles=allowed, franchises=self.franchises)
        ranked = rank(passed, [subject], scene.get("visual_intent", ""), 0, 3, subject)
        return [Option(c, c.score, 0.5, None, "stock_photo", "generic", "foto genérica do assunto") for c in ranked]

    # ---- fase B: resolver repetições e continuidade (local) ------------------------------------
    def resolve(self, decisions: list[Decision]) -> None:
        claimed: dict[str, str] = {k: "já usado" for k in self.used}
        for d in sorted((d for d in decisions if d.options), key=lambda d: d.options[0].score, reverse=True):
            d.pick = next((i for i, o in enumerate(d.options) if o.candidate.key not in claimed), len(d.options))
            if d.best:
                claimed[d.best.candidate.key] = d.scene["id"]
        # continuidade de estilo: vizinhos com o mesmo estilo puxam a cena para esse estilo (+0,5)
        order = sorted(decisions, key=lambda d: d.scene["start"])

        def style(d: Decision | None) -> str | None:
            if d is None:
                return None
            if d.entry:
                return "real" if d.entry.get("style", "real_footage") == "real_footage" else d.entry.get("style")
            return style_of_realism(d.best.realism) if d.best else None

        for i, d in enumerate(order):
            if not d.best or d.best.realism is None:
                continue
            prev_s = style(order[i - 1]) if i > 0 else None
            next_s = style(order[i + 1]) if i + 1 < len(order) else None
            if not prev_s or prev_s != next_s or style_of_realism(d.best.realism) == prev_s:
                continue
            for j, o in enumerate(d.options):
                if (style_of_realism(o.realism) == prev_s and o.candidate.key not in claimed
                        and min(10.0, o.score + CONTINUITY_BONUS) > d.best.score):
                    claimed.pop(d.best.candidate.key, None)
                    d.pick = j
                    claimed[o.candidate.key] = d.scene["id"]
                    d.stats["continuity"] = prev_s
                    break

    # ---- fase C: baixar ------------------------------------------------------------------------
    def finish(self, d: Decision) -> dict:
        """Baixa a opção escolhida (ou a seguinte, se falhar) e monta a entrada do selection.json."""
        scene, stats = d.scene, d.stats
        if d.entry is None:
            entry = None
            errors = []
            order = list(range(d.pick, len(d.options))) + list(range(0, d.pick))
            tries = 0
            for i in order:
                o = d.options[i]
                if tries >= 3:
                    break
                try:
                    with self.timer.track(scene["id"], "download do vencedor"):
                        entry = self._download(scene, o)
                    d.pick = i
                    break
                except NoCandidate:
                    continue
                except Exception as e:  # noqa: BLE001
                    tries += 1
                    log.warning("cena %s: download de %s falhou: %s", scene["id"], o.candidate.key, e)
                    errors.append(f"{o.candidate.key}: {str(e)[:200]}")
            if errors:
                self.ctx.issue("SCENE_NO_CANDIDATES", "Download de asset falhou; usado o próximo colocado",
                               scene=scene["id"], detail=" | ".join(errors))
            if entry is None:
                # o download de todas as opções falhou (ex.: vídeos do YouTube sem formato horizontal):
                # retoma a cadeia a partir das fontes ainda não tentadas
                skip = d.tried_sources | {o.source for o in d.options}
                if d.kind != "generic" and set(self._chain(d.planned, d.allow_ai, scene)) - skip:
                    retry = self.decide(scene, d.previous, d.allow_ai, skip=skip)
                    retry.stats = {**stats, **{k: v for k, v in retry.stats.items() if k not in stats}}
                    self.resolve([retry])
                    return self.finish(retry)
                self._accumulate(stats)
                raise NoCandidate("nenhuma fonte teve candidato aprovado")
            o = d.best
            entry.update(method=o.method, reason=o.seen, realism=o.realism,
                         seen=o.seen if o.method.startswith("vision") else None, step=o.source)
            if o.era:
                entry.update(era_consistent=o.era.get("era_consistent"),
                             anachronisms_seen=o.era.get("anachronisms_seen") or [],
                             is_timeless=o.era.get("is_timeless"))
            if is_historical(scene):
                feas = scene["context"].get("footage_feasibility")
                label = STEP_STRATEGY.get(o.source, o.source)
                if o.source == "youtube":
                    label = "arquivo" if feas in ("early_film", "historical_modern") else "reconstituição"
                entry["strategy"] = label
            if d.kind == "low_score":
                self.ctx.issue("SCENE_LOW_SCORE", f"Melhor candidato ficou com nota {o.score:.1f} (limiar "
                               f"{float(self.cfg['min_score']):.1f})", scene=scene["id"], detail=f"visto: {o.seen}")
            elif d.kind == "generic":
                entry["method"] = "generic"
                self.ctx.issue("SCENE_GENERIC_FALLBACK", f"Usada uma foto genérica de '{scene.get('subject')}' por "
                               "falta de opção", scene=scene["id"])
        else:
            entry = d.entry
        if stats.get("style_rejected"):
            self.ctx.issue("STYLE_REJECTED", f"{stats['style_rejected']} candidato(s) descartado(s) por estilo "
                           "incompatível com a cena ou por franquia", scene=scene["id"])
        self._accumulate(stats)
        allowance, allowed = scene_style(scene, self.media_style)
        if entry.get("source") == "ai_image" and is_historical(scene):
            entry["strategy"] = STRATEGY_LABEL["ai_period"]
        entry.update(planned_source=d.planned, vision_calls=stats.get("vision_calls", 0),
                     searches=stats.get("searches", 0), queries_used=d.queries,
                     must_avoid_used=list(dict.fromkeys(d.must_avoid + scene_anachronisms(scene))),
                     context_id=scene.get("context_id"),
                     style_allowance=allowance, allowed_styles=allowed, style_reason=scene.get("style_reason"),
                     continuity=stats.get("continuity"))
        if entry["source_used"] != d.planned:
            self.ctx.issue("SCENE_MIGRATED", f"Cena planejada para {SOURCE_LABEL[d.planned]} usou "
                           f"{SOURCE_LABEL[entry['source_used']]} ({d.planned} → {entry['source_used']})",
                           scene=scene["id"])
        log.info("cena %s: %s via %s, nota %s, %d chamada(s) de visão, %d busca(s)", scene["id"],
                 entry["source_used"], entry.get("method"), entry.get("score"), entry["vision_calls"],
                 entry["searches"])
        return entry

    def select_scene(self, scene: dict, previous: str = "", allow_ai: bool = True) -> dict:
        """Uma cena de ponta a ponta (decidir + baixar), usada no fallback da etapa de IA."""
        d = self.decide(scene, previous, allow_ai)
        self.resolve([d])
        return self.finish(d)

    def _accumulate(self, stats: dict) -> None:
        """Soma os números da cena nos totais da produção (uma vez por cena)."""
        with self.lock:
            if stats.get("_accounted"):
                return
            stats["_accounted"] = True
            for k in ("searches", "vision_calls", "vision_failed"):
                self.totals[k] += stats.get(k, 0)
            self.totals["vision_cost"] += stats.get("vision_cost", 0.0)
            if stats.get("vision_error"):
                self.totals["vision_error"] = stats["vision_error"]

    def _download(self, scene: dict, o: Option) -> dict:
        c = o.candidate
        with self.lock:
            if c.key in self.used:
                raise NoCandidate(f"{c.key} já foi usado neste vídeo")
            self.used.add(c.key)
        sid = scene["id"]
        dur = scene["end"] - scene["start"]
        in_point = 0.0
        if not c.is_image and c.duration > dur:
            # centraliza a duração da cena no melhor frame apontado pela IA
            in_point = round(min(max(0.0, c.duration * o.pos - dur / 2), c.duration - dur - 0.05), 2)
        out_point = round(in_point + dur, 2) if not c.is_image else None
        try:
            if c.provider == "youtube":
                dest = self.ctx.path("assets", f"{sid}.mp4")
                youtube.download_segment(c.external_id, in_point, in_point + dur + 0.5, dest)
                asset_in, resolution = 0.0, [c.width, c.height]
            elif c.is_image:
                rend = c.best_rendition()
                assert rend
                ext = ".png" if rend.url.lower().split("?")[0].endswith(".png") else ".jpg"
                dest = self.ctx.path("assets", f"{sid}{ext}")
                download(rend.url, dest)
                asset_in, resolution = 0.0, [rend.width, rend.height]
            else:
                rend = c.best_rendition()
                assert rend
                dest = self.ctx.path("assets", f"{sid}.mp4")
                download(rend.url, dest)
                asset_in, resolution = in_point, [rend.width, rend.height]
        except Exception:
            with self.lock:
                self.used.discard(c.key)
            raise
        with session_scope() as s:
            s.add(UsedAsset(channel_id=self.ctx.channel_id, production_id=self.ctx.production_id,
                            source=c.provider, external_id=c.external_id, segment_start=in_point))
            s.commit()
        return {
            "source": c.source, "source_used": c.source, "is_image": c.is_image,
            "provider": c.provider, "external_id": c.external_id, "page_url": c.page_url, "title": c.title,
            "author": c.author, "author_url": c.author_url, "license": c.license, "channel": c.author,
            "query": c.query, "score": o.score, "asset": f"assets/{dest.name}",
            "in": asset_in, "in_point": in_point, "out_point": out_point,
            "clip_duration": c.duration or None, "resolution": resolution,
        }


def run(ctx: JobContext) -> str:
    from ..generate import generate_scenes

    started = time.perf_counter()
    plan = ctx.read_json("plan.json")
    sel = Selector(ctx)
    real = [s for s in plan["scenes"] if s["source"] in ("stock", "youtube")]
    todo = [s for s in real if s["id"] not in sel.selection]
    if real and not sel.providers and not sel.youtube_on:
        raise StepError("PROVIDER_QUOTA", "Nenhuma fonte de vídeo ativa: configure Pexels, Pixabay ou YouTube")
    if not gemini.available():
        st = gemini.quota_status()
        log.info("Gemini indisponível (%s): seleção só pelo ranking de texto", st.get("reason") or "sem chave")
        if st["blocked"]:
            sel.note_vision_quota(st["reason"])

    # imagens de IA começam já, em segundo plano (fila da Darkvi, 5/min), enquanto os bancos são avaliados
    ai_scenes = [s for s in plan["scenes"] if s["source"] == "ai" and s["id"] not in sel.selection]
    background = ThreadPoolExecutor(max_workers=1)

    def store_ai(scene: dict, entry: dict) -> None:
        with sel.lock:
            sel.selection[scene["id"]] = entry
        sel.save()

    ai_future = background.submit(generate_scenes, ctx, sel, ai_scenes, store_ai) if ai_scenes else None

    previous = {b["id"]: a.get("visual_intent", "") for a, b in zip(plan["scenes"], plan["scenes"][1:])}
    ai_possible = bool(get_secret("darkvi")) and ctx.config.real_pct < 100
    total = max(1, len(todo))

    # A) avaliar em paralelo
    decisions: list[Decision] = []
    done = 0
    with ThreadPoolExecutor(max_workers=int(sel.cfg["parallel_scenes"])) as pool:
        futures = {pool.submit(sel.decide, s, previous.get(s["id"], ""), True, None, "defer"): s for s in todo}
        for fut in as_completed(futures):
            ctx.check_cancel()
            decisions.append(fut.result())
            done += 1
            ctx.progress(0.5 * done / total, f"Avaliando cenas {done}/{len(todo)}")

    # A2) reescrita de queries: todas as cenas reprovadas numa única chamada, depois retomam a cadeia
    pending = [d for d in decisions if d.pending_rewrite]
    if pending:
        ctx.progress(0.52, f"Reescrevendo buscas de {len(pending)} cena(s) (1 chamada)")
        fixes = sel.rewrite_batch([(d.scene, d.queries, d.must_avoid, d.pending_rewrite["seen"]) for d in pending])

        def resume(d: Decision) -> Decision:
            fix = fixes.get(d.scene["id"])
            queries, must_avoid = (fix[0], d.must_avoid + fix[1]) if fix else (d.queries, d.must_avoid)
            if fix:
                ctx.issue("QUERY_REWRITTEN", f"Queries reescritas: {queries}", scene=d.scene["id"],
                          detail=f"visto: {d.pending_rewrite['seen']} | novos itens a evitar: {fix[1]}")
            skip = d.tried_sources - {d.pending_rewrite["source"]}
            again = sel.decide(d.scene, d.previous, d.allow_ai, skip, "none", queries, must_avoid)
            again.stats = {**d.stats, **{k: v for k, v in again.stats.items() if k not in ("_accounted",)},
                           "searches": d.stats.get("searches", 0) + again.stats.get("searches", 0),
                           "vision_calls": d.stats.get("vision_calls", 0) + again.stats.get("vision_calls", 0)}
            again.tried_sources |= d.tried_sources
            return again

        with ThreadPoolExecutor(max_workers=int(sel.cfg["parallel_scenes"])) as pool:
            redone = {d.scene["id"]: r for d, r in zip(pending, pool.map(resume, pending))}
        decisions = [redone.get(d.scene["id"], d) for d in decisions]
        ctx.progress(0.6, "Avaliação concluída")
    t_decide = time.perf_counter() - started

    # B) repetições e continuidade (local)
    sel.resolve(decisions)

    # C) baixar em paralelo
    failures = 0
    done = 0
    with ThreadPoolExecutor(max_workers=int(sel.cfg.get("parallel_downloads", 4))) as pool:
        futures = {pool.submit(sel.finish, d): d for d in decisions}
        for fut in as_completed(futures):
            d = futures[fut]
            ctx.check_cancel()
            try:
                entry = fut.result()
            except NoCandidate as e:
                failures += 1
                if ai_possible and not sel.ai_image_on:
                    entry = {"source": "migrate_ai", "reason": str(e)}
                    ctx.issue("SCENE_MIGRATED", "Nenhum clipe nem foto serviu; a cena vai para geração por IA",
                              scene=d.scene["id"], detail=str(e))
                else:
                    entry = {"source": "missing", "reason": str(e)}
                    ctx.issue("SCENE_LOW_SCORE", "Nenhum asset adequado encontrado; a cena vai estender a anterior",
                              scene=d.scene["id"], detail=str(e))
            with sel.lock:
                sel.selection[d.scene["id"]] = entry
            sel.save()
            done += 1
            ctx.progress(0.6 + 0.35 * done / total, f"Baixando cenas {done}/{len(todo)}")
    t_select = time.perf_counter() - started

    if ai_future:
        ctx.progress(0.96, f"Aguardando imagens de IA ({len(ai_scenes)})")
        try:
            ai_future.result()
        except Exception as e:  # noqa: BLE001 — as cenas sem imagem ficam para a etapa de IA tentar de novo
            log.warning("geração de IA em segundo plano falhou: %s", e)
            ctx.issue("AI_GEN_FAILED", "A geração de imagens em segundo plano falhou; a etapa de IA tenta de novo",
                      detail=f"{type(e).__name__}: {e}"[:500])
    background.shutdown()

    t = sel.totals
    if t["vision_calls"] or t["vision_cost"]:
        ctx.record_llm({"task": "visão (Gemini)", "model": sel.cfg["gemini_model"], "calls": t["vision_calls"],
                        "cost": round(t["vision_cost"], 6)}, step="select")
    if t["vision_failed"]:
        ctx.issue("VISION_FAILED", f"A IA de visão falhou em {t['vision_failed']} avaliação(ões); essas cenas "
                  "foram escolhidas pelo ranking de texto", detail=t.get("vision_error"))
    report = sel.timer.report(time.perf_counter() - started)
    report.update(mode=ctx.config.selection_mode, real_scenes=len(todo), ai_scenes=len(ai_scenes),
                  decide_seconds=round(t_decide, 2), select_seconds_without_ai=round(t_select, 2),
                  vision_calls=t["vision_calls"], searches=t["searches"])
    ctx.write_json("timing_report.json", report)
    log.info("seleção (%s): %.1fs no total, %.1fs para avaliar %d cena(s) reais, %d chamada(s) de visão, "
             "%d busca(s); mais lentas: %s", report["mode"], report["wall_seconds"], t_decide, len(todo),
             t["vision_calls"], t["searches"], report["slowest_stages"])
    if real and failures == len(real):
        raise StepError("PROVIDER_QUOTA", "Nenhuma cena encontrou asset: verifique as chaves e cotas das fontes")
    sel.save()
    return str(ctx.dir / "selection.json")
