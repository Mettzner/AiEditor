"""Etapa 1: áudio (TTS Darkvi ou upload) → audio/narration.wav 48 kHz normalizado (§6.1)."""
from __future__ import annotations

from ..providers.darkvi import tts
from ..providers.darkvi.client import DarkviError
from ..worker.context import JobContext, StepError
from .render import ffmpeg
from .srt import merge_srts


def _tts_cache_paths(text: str, voice: str):
    import hashlib

    from ..config import CACHE_DIR

    key = hashlib.sha256(f"{voice}|{text}".encode()).hexdigest()
    d = CACHE_DIR / "tts"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{key}.mp3", d / f"{key}.srt"


def _from_tts_cache(text: str, voice: str, mp3, srt) -> bool:
    """Mesmo texto + mesma voz = mesma narração: reaproveita sem gastar saldo da Darkvi."""
    import shutil

    c_mp3, c_srt = _tts_cache_paths(text, voice)
    if not c_mp3.exists():
        return False
    shutil.copy(c_mp3, mp3)
    if c_srt.exists():
        shutil.copy(c_srt, srt)
    return True


def _to_tts_cache(text: str, voice: str, mp3, srt) -> None:
    import shutil

    c_mp3, c_srt = _tts_cache_paths(text, voice)
    if mp3.exists():
        shutil.copy(mp3, c_mp3)
    if srt.exists():
        shutil.copy(srt, c_srt)


def _normalize(src, dest) -> None:
    ffmpeg.run(["-i", str(src), "-af", "loudnorm=I=-14:TP=-1.5:LRA=11", "-ar", "48000", "-ac", "2",
                "-c:a", "pcm_s16le", str(dest)])


def run(ctx: JobContext) -> str:
    out = ctx.path("audio", "narration.wav")
    uploads = sorted((ctx.dir / "input").glob("narration.*"))
    if uploads:
        ctx.progress(0.2, "Convertendo áudio enviado")
        _normalize(uploads[0], out)
        return str(out)

    voice = ctx.config.tts_voice
    if not voice:
        raise StepError("AUDIO_TTS_FAILED", "Nenhuma voz TTS definida no preset do canal")
    chunks = tts.split_text(ctx.script)
    dcfg = ctx.settings["darkvi"]
    mp3s, srts = [], []
    try:
        for i, chunk in enumerate(chunks):
            mp3 = ctx.path("audio", f"tts_{i:02d}.mp3")
            srt = ctx.path("audio", f"tts_{i:02d}.srt")
            if mp3.exists():
                pass  # já baixado nesta produção (retry)
            elif _from_tts_cache(chunk, voice, mp3, srt):
                ctx.progress((i + 1) / len(chunks) * 0.85, "Narração reaproveitada do cache (sem custo)")
            else:
                def on_poll(status: str, i=i) -> None:
                    ctx.check_cancel()
                    ctx.progress((i + 0.5) / len(chunks) * 0.85, f"Gerando narração ({status or 'na fila'})")

                tts.synthesize(chunk, voice, ctx.config.title, mp3, srt, poll_seconds=dcfg["tts_poll_seconds"],
                               timeout_seconds=dcfg["tts_timeout_seconds"], on_poll=on_poll,
                               state_file=ctx.path("audio", f"tts_{i:02d}.json"))
                _to_tts_cache(chunk, voice, mp3, srt)
            mp3s.append(mp3)
            srts.append(srt)
    except DarkviError as e:
        raise StepError("AUDIO_TTS_FAILED", str(e), repr(e.body)) from e

    ctx.progress(0.9, "Normalizando narração")
    if len(mp3s) == 1:
        joined = mp3s[0]
    else:
        lst = ctx.path("audio", "concat.txt")
        lst.write_text("".join(f"file '{p.name}'\n" for p in mp3s), encoding="utf-8")
        joined = ctx.path("audio", "tts_joined.mp3")
        ffmpeg.run(["-f", "concat", "-safe", "0", "-i", lst.name, "-c", "copy", joined.name], cwd=lst.parent)
    _normalize(joined, out)

    existing = [s for s in srts if s.exists()]
    if existing:
        offsets, acc = [], 0.0
        for mp3 in mp3s:
            offsets.append(acc)
            acc += ffmpeg.duration(mp3)
        merge_srts([(s, offsets[srts.index(s)]) for s in existing], ctx.path("audio", "darkvi.srt"))
    return str(out)
