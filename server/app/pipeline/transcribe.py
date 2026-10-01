"""Etapa 2: transcrição (faster-whisper) e alinhamento ao roteiro → transcript.json (§6.2).

O roteiro é a fonte da grafia; a transcrição dá o tempo de cada palavra.
"""
from __future__ import annotations

import difflib
import re
import threading
import unicodedata

from ..worker.context import JobContext
from . import srt as srt_mod
from .render import ffmpeg

_model_lock = threading.Lock()
_models: dict[tuple[str, str], object] = {}


def norm(word: str) -> str:
    w = unicodedata.normalize("NFKD", word.lower())
    return re.sub(r"[^\w]", "", "".join(c for c in w if not unicodedata.combining(c)))


def script_words(script: str) -> list[str]:
    return [w for w in script.split() if norm(w)]


def _whisper(ctx: JobContext, wav) -> tuple[list[dict], float]:
    import numpy as np
    from faster_whisper import WhisperModel

    cfg = ctx.settings["transcription"]
    key = (cfg["model"], cfg["compute_type"])
    with _model_lock:
        if key not in _models:
            ctx.progress(0.05, f"Carregando modelo whisper '{cfg['model']}'")
            _models[key] = WhisperModel(cfg["model"], device="cpu", compute_type=cfg["compute_type"])
        model = _models[key]
    audio = np.frombuffer(ffmpeg.decode_mono_f32(wav), dtype=np.float32)
    segments, info = model.transcribe(audio, language=ctx.config.language or None, word_timestamps=True,  # type: ignore[attr-defined]
                                      vad_filter=True, beam_size=5)
    words = []
    for seg in segments:
        ctx.check_cancel()
        ctx.progress(0.1 + 0.8 * min(1.0, seg.end / max(info.duration, 1)), "Transcrevendo")
        for w in seg.words or []:
            if norm(w.word):
                words.append({"text": w.word.strip(), "start": float(w.start), "end": float(w.end)})
    return words, float(info.duration)


def _from_srt(path) -> list[dict]:
    """Fallback: distribui as palavras de cada legenda proporcionalmente ao tamanho."""
    words = []
    for cue in srt_mod.parse(path):
        toks = [t for t in cue["text"].split() if norm(t)]
        total = sum(len(t) for t in toks) or 1
        t = cue["start"]
        for tok in toks:
            dur = (cue["end"] - cue["start"]) * len(tok) / total
            words.append({"text": tok, "start": t, "end": t + dur})
            t += dur
    return words


def align(script: str, heard: list[dict], audio_duration: float) -> tuple[list[dict], float]:
    """Mapeia cada palavra do roteiro para um tempo; palavras não reconhecidas são interpoladas."""
    sw = script_words(script)
    a = [norm(w) for w in sw]
    b = [norm(w["text"]) for w in heard]
    times: list[tuple[float, float] | None] = [None] * len(sw)
    matched = 0
    for block in difflib.SequenceMatcher(None, a, b, autojunk=False).get_matching_blocks():
        for k in range(block.size):
            h = heard[block.b + k]
            times[block.a + k] = (h["start"], h["end"])
            matched += 1
    # interpola lacunas entre âncoras
    i = 0
    n = len(sw)
    while i < n:
        if times[i] is not None:
            i += 1
            continue
        j = i
        while j < n and times[j] is None:
            j += 1
        left = times[i - 1][1] if i > 0 and times[i - 1] else 0.0  # type: ignore[index]
        right = times[j][0] if j < n and times[j] else audio_duration  # type: ignore[index]
        chars = [max(1, len(a[k])) for k in range(i, j)]
        span = max(0.0, right - left)
        t = left
        for k, c in zip(range(i, j), chars):
            d = span * c / sum(chars)
            times[k] = (t, t + d)
            t += d
        i = j
    words = [{"text": w, "start": round(t[0], 3), "end": round(t[1], 3)} for w, t in zip(sw, times)]  # type: ignore[index]
    divergence = 1 - matched / max(1, len(sw))
    return words, divergence


def run(ctx: JobContext) -> str:
    wav = ctx.path("audio", "narration.wav")
    audio_duration = ffmpeg.duration(wav)
    darkvi_srt = ctx.dir / "audio" / "darkvi.srt"
    source = "whisper"
    try:
        heard, _ = _whisper(ctx, wav)
    except Exception as e:  # noqa: BLE001
        if not darkvi_srt.exists():
            raise
        ctx.issue("TRANSCRIPT_FALLBACK", "Whisper falhou; usando o SRT da Darkvi", detail=repr(e))
        heard, source = _from_srt(darkvi_srt), "darkvi_srt"

    words, divergence = align(ctx.script, heard, audio_duration)
    limit = ctx.settings["transcription"]["divergence_warn"]
    if divergence > limit:
        ctx.issue("TRANSCRIPT_DIVERGENCE",
                  f"{divergence:.0%} das palavras do roteiro não foram reconhecidas no áudio (limite {limit:.0%})")

    cross = None
    if source == "whisper" and darkvi_srt.exists():
        cues = srt_mod.parse(darkvi_srt)
        if cues:  # validação cruzada simples: fim da fala nas duas fontes
            cross = round(abs(cues[-1]["end"] - (heard[-1]["end"] if heard else 0)), 2)

    out = ctx.write_json("transcript.json", {
        "source": source, "audio_duration": audio_duration, "divergence": round(divergence, 4),
        "srt_end_delta": cross, "words": words, "heard": heard,
    })
    return str(out)
