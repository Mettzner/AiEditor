"""Imagem gerada (Darkvi) com prompt de estilo adequado à cena e validação visual.

Padrão fotorrealista; numa cena stylized_ok o bloco de estilo muda (3D científico, pintura de época,
ilustração, cartoon genérico). Cena de contexto histórico: prompt de época (CONTEXTO_PROFUNDO_DO_ROTEIRO.md §7),
no estilo de representação do preset (reconstituição cinematográfica ou aparência de arquivo).
Se a imagem reprovar na validação, é regerada 1 vez com o motivo incorporado ao prompt; anacronismos vistos
entram no Avoid. Se reprovar de novo, o chamador registra AI_IMAGE_REJECTED e segue a cadeia.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..providers.darkvi import images as darkvi_images
from ..providers.llm import gemini, vision
from .select.funnel import SceneContext, rate_local_image
from .context import scene_anachronisms
from .visual import build_image_prompt, scene_style, wanted_style


@dataclass
class GeneratedImage:
    ok: bool
    path: Path | None
    prompt: str
    score: float | None  # None quando não houve validação (sem Gemini)
    seen: str
    attempts: int
    reason: str = ""
    style: str = "real_footage"


def scene_context(scene: dict, brief: dict | None, style: str, previous: str = "",
                  media_style: str = "real_preferred") -> SceneContext:
    brief = brief or {}
    allowance, allowed = scene_style(scene, media_style)
    return SceneContext(
        intent=scene.get("visual_intent", ""), text=scene.get("text", ""), style=style, previous=previous,
        subject=scene.get("subject", ""), must_show=scene.get("must_show") or [],
        must_avoid=list(scene.get("must_avoid") or []) + scene_anachronisms(scene) + list(brief.get("global_avoid") or []),
        topic=brief.get("topic", ""), visual_world=brief.get("visual_world", ""), allowance=allowance,
        allowed_styles=allowed, style_reason=scene.get("style_reason", ""), context=scene.get("context") or {},
        meaning=scene.get("meaning", ""), beat=scene.get("beat", ""))


def generate_validated(scene: dict, brief: dict | None, style: str, dest: Path, cfg: dict, stats: dict,
                       reference_key: str | None = None, media_style: str = "real_preferred",
                       period_look: str = "cinematic") -> GeneratedImage:
    """Gera e valida. Exceções da Darkvi (cota, erro) sobem para o chamador decidir o fallback."""
    ctx = scene_context(scene, brief, style, media_style=media_style)
    look = wanted_style(scene, media_style)
    feedback = None
    extra_avoid: list[str] = []
    prompt = ""
    last_seen, last_score = "", None
    for attempt in (1, 2):
        prompt = build_image_prompt(scene, brief, feedback, look, period_look=period_look, extra_avoid=extra_avoid)
        darkvi_images.generate(prompt, dest, reference_key=reference_key)
        if not vision.available():
            return GeneratedImage(True, dest, prompt, None, "", attempt, style=look)
        try:
            note = rate_local_image(dest, ctx, cfg["gemini_model"], stats)  # identidade = bytes da imagem
        except Exception as e:  # noqa: BLE001 — sem validação possível (cota, rede), aceita a imagem
            if isinstance(e, gemini.GeminiQuotaExhausted):
                stats["vision_quota"] = str(e)
            else:
                stats["vision_failed"] = stats.get("vision_failed", 0) + 1
                stats["vision_error"] = str(e)[:300]
            return GeneratedImage(True, dest, prompt, None, "", attempt, style=look)
        last_seen, last_score = note.get("seen", ""), note["score"]
        if last_score >= cfg["min_score"]:
            return GeneratedImage(True, dest, prompt, last_score, last_seen, attempt, style=look)
        problems = []
        if note.get("brand_or_franchise"):
            problems.append("remove any existing character, brand or franchise")
        if note.get("realism") == "ai_looking":
            problems.append("fix the AI defects (hands, faces, plastic look)")
        elif look == "real_footage" and note.get("realism") != "real_footage":
            problems.append(f"it looked like {note.get('realism')}, it must look like a real photograph")
        if not note.get("subject_visible"):
            problems.append(f"the subject '{scene.get('subject')}' was not clearly visible")
        if note.get("forbidden_present"):
            problems.append(f"remove {', '.join(note['forbidden_present'])}")
        if note.get("anachronisms_seen"):  # §7: regerar 1 vez com o item visto no Avoid
            extra_avoid = list(dict.fromkeys(extra_avoid + [a for a in note["anachronisms_seen"] if a]))
            problems.append(f"remove the out-of-period {', '.join(note['anachronisms_seen'])}")
        elif note.get("era_consistent") is False and (scene.get("context") or {}).get("setting_type") == "historical":
            problems.append("clothing, buildings and objects must match the time period exactly")
        if note.get("place_consistent") is False:
            problems.append(f"vegetation, architecture and climate must match {(scene.get('context') or {}).get('place')}")
        feedback = f"previous image showed: {last_seen}; " + "; ".join(problems or ["make it match the narration"])
    dest.unlink(missing_ok=True)
    return GeneratedImage(False, None, prompt, last_score, last_seen, 2, feedback or "", style=look)
