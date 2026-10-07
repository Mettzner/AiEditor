"""Artefatos versionados e invalidação por dependência (Fase E4).

Cada etapa concluída grava em artifacts.json: versão do formato, hashes das ENTRADAS (roteiro, áudio, config
relevante, prompt, modelo, saídas das etapas anteriores) e as saídas esperadas. Na retomada, o runner só pula uma
etapa "concluída" se as saídas existem/abrem e as entradas não mudaram; senão ela e as seguintes rodam de novo.
Etapas independentes da mudança são preservadas (mudar a config de render não refaz áudio nem seleção).

Produções antigas (sem registro) continuam retomando como antes.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .fsutil import atomic_write_text

FORMAT_VERSION = 1
BIG_FILE = 50 * 1024 * 1024  # acima disto: tamanho + data de modificação (hash de 1 GB a cada retomada não compensa)

OUTPUTS: dict[str, list[str]] = {
    "audio": ["audio/narration.wav"],
    "transcribe": ["transcript.json"],
    "plan": ["plan.json", "context_bible.json"],
    "select": ["selection.json"],
    "generate": ["selection.json"],
    "direct": ["timeline.json"],
    "render": ["output/final.mp4"],
    "upload": [],
}
# campos da config de cada etapa (mudar outro campo não invalida a etapa)
CONFIG_FIELDS: dict[str, tuple[str, ...]] = {
    "audio": ("audio_mode", "tts_voice", "video_language", "language"),
    "plan": ("avg_scene_seconds", "media_style", "video_language", "language", "direction", "real_pct",
             "youtube_pct", "ai_media", "llm_economy"),
    "select": ("real_pct", "media_style", "selection_mode", "ai_media", "reference_key"),
    "generate": ("ai_media", "media_style", "reference_key"),
    "direct": ("direction", "subtitles", "subtitle_style", "music", "font", "color_primary", "color_accent",
               "period_grade", "context_cards", "sfx", "film_look", "video_language", "language"),
}
SETTINGS_FIELDS: dict[str, tuple[tuple[str, ...], ...]] = {
    "transcribe": (("transcription", "model"),),
    "plan": (("llm", "plan"), ("llm", "bible")),
    "select": (("selection",), ("youtube", "ingest_mode"), ("archives",), ("vision", "provider")),
    "render": (("render",),),
}


def fingerprint(path: Path) -> str | None:
    if not path.exists():
        return None
    st = path.stat()
    if st.st_size > BIG_FILE:
        return f"size:{st.st_size}:mtime:{st.st_mtime_ns}"
    h = hashlib.sha256()
    with path.open("rb") as f:
        while block := f.read(1 << 20):
            h.update(block)
    return h.hexdigest()


def _hash(data: Any) -> str:
    return hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()


def _dig(d: dict, path: tuple[str, ...]) -> Any:
    cur: Any = d
    for k in path:
        cur = cur.get(k) if isinstance(cur, dict) else None
    return cur


def step_deps(step: str, job: Path, script: str, config: dict, settings: dict) -> dict:
    """Hashes das entradas da etapa, a partir do estado atual da produção."""
    from .providers.llm.anthropic import PROMPT_VERSION

    deps: dict[str, Any] = {"format": FORMAT_VERSION}
    if fields := CONFIG_FIELDS.get(step):
        deps["config"] = _hash({k: config.get(k) for k in fields})
    if paths := SETTINGS_FIELDS.get(step):
        deps["settings"] = _hash([_dig(settings, p) for p in paths])
    if step == "audio":
        deps["script"] = _hash(script) if config.get("audio_mode") == "tts" else None
        uploaded = sorted((job / "input").glob("narration.*")) if (job / "input").exists() else []
        deps["input_audio"] = fingerprint(uploaded[0]) if uploaded else None
    elif step == "transcribe":
        deps["audio"] = fingerprint(job / "audio" / "narration.wav")
        deps["script"] = _hash(script)
    elif step == "plan":
        from .pipeline.visual import PLAN_SYSTEM

        deps["transcript"] = fingerprint(job / "transcript.json")
        deps["prompt"] = _hash([PROMPT_VERSION, PLAN_SYSTEM])
    elif step in ("select", "generate"):
        deps["plan"] = fingerprint(job / "plan.json")
    elif step == "direct":
        deps["plan"] = fingerprint(job / "plan.json")
        deps["selection"] = fingerprint(job / "selection.json")
        deps["transcript"] = fingerprint(job / "transcript.json")
    elif step == "render":
        deps["timeline"] = fingerprint(job / "timeline.json")
    elif step == "upload":
        deps["final"] = fingerprint(job / "output" / "final.mp4")
    return deps


def _registry(job: Path) -> dict:
    p = job / "artifacts.json"
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except ValueError:
        return {}


def record(step: str, job: Path, script: str, config: dict, settings: dict) -> None:
    reg = _registry(job)
    reg[step] = {"version": FORMAT_VERSION, "deps": step_deps(step, job, script, config, settings),
                 "outputs": OUTPUTS.get(step, [])}
    atomic_write_text(job / "artifacts.json", json.dumps(reg, indent=1, ensure_ascii=False))


def _output_ok(path: Path) -> bool:
    if not path.exists() or path.stat().st_size == 0:
        return False
    if path.suffix == ".json":
        try:
            json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            return False
    return True


def still_valid(step: str, job: Path, script: str, config: dict, settings: dict) -> tuple[bool, str]:
    """(válida?, motivo). Sem registro (produção antiga): válida, como antes."""
    rec = _registry(job).get(step)
    if not rec:
        return True, "sem registro (produção antiga)"
    missing = [o for o in rec.get("outputs", []) if not _output_ok(job / o)]
    if missing:
        return False, f"saída ausente ou corrompida: {', '.join(missing)}"
    now = step_deps(step, job, script, config, settings)
    changed = [k for k in now if rec["deps"].get(k) != now[k]]
    if changed:
        return False, f"entradas mudaram: {', '.join(changed)}"
    return True, "ok"


# ---------------------------------------------------------------- invalidação por cena
SCENE_FP_FIELDS = ("text", "start", "end", "subject", "queries", "visual_intent", "must_show", "must_avoid",
                   "context_id", "visual_role", "required_identity", "source", "documentary_query", "data_points",
                   "data_source", "must_not_imply", "entity_ids", "entity_rev")


def scene_fingerprint(scene: dict) -> str:
    return _hash({k: scene.get(k) for k in SCENE_FP_FIELDS})[:16]
