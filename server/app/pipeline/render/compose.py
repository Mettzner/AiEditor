"""Etapa 7: render da timeline → output/final.mp4 (§10).

1. Normaliza cada cena em paralelo (corte, 1920×1080, 30 fps). Imagens ganham Ken Burns (fotos de arquivo,
   retratos e panorâmicas entram inteiras numa moldura sobre o próprio fundo desfocado); vídeos podem ganhar
   um push-in lento. A cena que antecede uma transição ganha uma cauda extra do tamanho dela.
2. Cenas com transição de entrada (dissolve, fade pelo preto, flash branco...) recebem, nos primeiros
   quadros, a mistura com a cauda da anterior. Assim a passada final não precisa de xfade (que, junto com
   overlay, trava o agendador do FFmpeg).
3. Overlays animados (destaques, títulos, cards, citações, light leaks) são desenhados quadro a quadro
   (anim.py), codificados com transparência e aplicados dentro da cena em que caem, com o filtro `movie`.
   Uma imagem em loop como segunda entrada da passada final trava o agendador do FFmpeg 9.
4. Efeitos sonoros são pré-mixados numa única faixa (sfx.wav), posicionados no ponto exato de cada deixa.
5. Passada final única: concat (com outpoint cortando as caudas) + textura de filme + legendas + narração +
   música com ducking + efeitos sonoros.
"""
from __future__ import annotations

import hashlib
import json

import shutil
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import numpy as np

from ...directions import get_direction
from ...worker.context import Cancelled, JobContext, StepError
from ..context import GRADE_FILTERS
from . import anim, ffmpeg
from .fonts import find_font
from .subtitles import write_ass

FPS = 30
W, H = 1920, 1080


def _frames(seconds: float) -> int:
    return max(1, int(round(seconds * FPS)))


def _intermediate_codec(ctx: JobContext) -> list[str]:
    # sem B-frames: o outpoint do concat corta exatamente no quadro
    r = ctx.settings["render"]
    return ["-c:v", "libx264", "-preset", "veryfast", "-crf", str(r["intermediate_crf"]), "-bf", "0",
            "-g", str(FPS), "-pix_fmt", "yuv420p", "-r", str(FPS), "-an"]


def _zoompan(motion: dict, frames: int, d: int) -> str:
    """zoompan de (zoom, centro x, centro y) inicial → final, com easing suave nas pontas."""
    (z0, x0, y0), (z1, x1, y1) = motion["from"], motion["to"]
    lin = f"(on/{max(1, frames - 1)})"
    p = f"(0.5-0.5*cos(PI*{lin}))"
    z = f"{z0}+({z1 - z0})*{p}"
    x = f"max(0,min(iw-iw/zoom,({x0}+({x1 - x0})*{p})*iw-iw/zoom/2))"
    y = f"max(0,min(ih-ih/zoom,({y0}+({y1 - y0})*{p})*ih-ih/zoom/2))"
    return f"zoompan=z='{z}':x='{x}':y='{y}':d={d}:s={W}x{H}:fps={FPS}"


# Foto inteira numa moldura clara, levemente girada, com sombra, sobre a própria imagem desfocada (em 4K)
PHOTO_FRAME = (
    "[0:v]format=rgba,split[a][b];"
    "[a]scale=3840:2160:force_original_aspect_ratio=increase,crop=3840:2160,boxblur=40:3,"
    "eq=brightness=-0.10:saturation=0.7[bg];"
    "[b]scale=3100:1760:force_original_aspect_ratio=decrease,pad=iw+56:ih+56:28:28:color=0xF1ECE2FF,"
    "rotate=-0.012:c=none:ow=rotw(-0.012):oh=roth(-0.012),split[p][s];"
    "[s]colorchannelmixer=rr=0:gg=0:bb=0:aa=0.55,"
    "boxblur=luma_radius=26:luma_power=2:alpha_radius=26:alpha_power=2[sh];"
    "[bg][sh]overlay=(W-w)/2+24:(H-h)/2+32[bs];"
    "[bs][p]overlay=(W-w)/2:(H-h)/2,format=yuv420p,"
)


def _normalize_scene(ctx: JobContext, scene: dict, frames: int, out: Path) -> None:
    asset = ctx.dir / scene["asset"]
    # gradação do bloco de contexto (CONTEXTO_PROFUNDO_DO_ROTEIRO.md §8), antes da conversão final de cor
    grade = GRADE_FILTERS.get(scene.get("grade") or "")
    graded = f"{grade}," if grade else ""
    motion = scene.get("motion") or {}
    if motion.get("type") == "kenburns":
        zp = _zoompan(motion, frames, frames)
        if scene.get("frame") == "photo":
            args = ["-i", str(asset), "-filter_complex", f"{PHOTO_FRAME}{zp},setsar=1,{graded}format=yuv420p[v]",
                    "-map", "[v]"]
        else:
            vf = (f"scale=3840:2160:force_original_aspect_ratio=increase,crop=3840:2160,"
                  f"{zp},setsar=1,{graded}format=yuv420p")
            args = ["-i", str(asset), "-vf", vf]
    elif motion.get("type") == "push":
        # vídeo: push-in ou deslize lento; 1,5× de resolução basta para o movimento sair sem tremor
        vf = (f"scale=2880:1620:force_original_aspect_ratio=increase,crop=2880:1620,fps={FPS},"
              f"tpad=stop_mode=clone:stop_duration={frames / FPS:.3f},{_zoompan(motion, frames, 1)},setsar=1,"
              f"{graded}format=yuv420p")
        args = ["-ss", f"{scene.get('in', 0):.3f}", "-i", str(asset), "-vf", vf]
    else:
        vf = (f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},fps={FPS},setsar=1,"
              f"{graded}format=yuv420p,tpad=stop_mode=clone:stop_duration={frames / FPS:.3f}")
        args = ["-ss", f"{scene.get('in', 0):.3f}", "-i", str(asset), "-vf", vf]
    tmp = out.with_suffix(".tmp.mp4")
    ffmpeg.run(args + ["-frames:v", str(frames)] + _intermediate_codec(ctx) + [str(tmp)],
               cancel=ctx.is_cancelled)
    tmp.replace(out)


# nomes da timeline → transições do filtro xfade ("crossfade" é o nome antigo do fade simples)
XFADE = {"crossfade": "fade"}


def _crossfade_in(ctx: JobContext, prev_file: Path, prev_frames: int, raw: Path, frames: int, duration: float,
                  out: Path, transition: str = "fade") -> None:
    """Mistura a cauda da cena anterior (quadros após prev_frames) com o início desta cena."""
    f = (f"[0:v]trim=start_frame={prev_frames},setpts=PTS-STARTPTS[a];"
         f"[1:v]setpts=PTS-STARTPTS[b];"
         f"[a][b]xfade=transition={XFADE.get(transition, transition)}:duration={duration:.3f}:offset=0,"
         f"format=yuv420p[v]")
    tmp = out.with_suffix(".tmp.mp4")
    ffmpeg.run(["-i", str(prev_file), "-i", str(raw), "-filter_complex", f, "-map", "[v]", "-frames:v", str(frames)]
               + _intermediate_codec(ctx) + [str(tmp)], cancel=ctx.is_cancelled)
    tmp.replace(out)


def _final_encoder(ctx: JobContext, use_amf: bool) -> list[str]:
    r = ctx.settings["render"]
    if use_amf:
        br = r["amf_bitrate"]
        return ["-c:v", "h264_amf", "-quality", "quality", "-rc", "vbr_peak", "-b:v", br,
                "-maxrate", f"{int(br.rstrip('M')) * 4 // 3}M", "-pix_fmt", "yuv420p"]
    return ["-c:v", "libx264", "-preset", r["x264_preset"], "-crf", str(r["crf"]), "-pix_fmt", "yuv420p",
            "-profile:v", "high"]


def run(ctx: JobContext) -> str:
    timeline = ctx.read_json("timeline.json")
    if not ffmpeg.available():
        raise StepError("RENDER_FAILED", "FFmpeg não encontrado: instale a build completa e reinicie o worker")
    ctx.progress(0.0, "Aguardando render de outra produção")
    with ctx.render_lock:
        try:
            return _render(ctx, timeline)
        except StepError as e:
            if ctx.is_cancelled():  # o ffmpeg foi encerrado pelo cancelamento, não falhou
                raise Cancelled() from e
            raise


def _render(ctx: JobContext, tl: dict) -> str:
    rdir = ctx.path("render", "scenes", "x").parent
    scenes = tl["scenes"]
    n = len(scenes)
    main = {s["id"]: _frames(s["end"] - s["start"]) for s in scenes}
    tail = {a["id"]: _frames(b["transition_in"]["duration"])
            for a, b in zip(scenes, scenes[1:]) if b["transition_in"]["type"] != "cut"}
    total = {sid: main[sid] + tail.get(sid, 0) for sid in main}
    xin = {b["id"]: a["id"] for a, b in zip(scenes, scenes[1:]) if b["transition_in"]["type"] != "cut"}

    def final_path(sid: str) -> Path:
        return rdir / f"{sid}.mp4"

    def raw_path(sid: str) -> Path:
        return rdir / (f"{sid}.raw.mp4" if sid in xin else f"{sid}.mp4")

    # 0) intermediário de cena que mudou (asset, trecho, movimento, transição) é refeito; o resto é aproveitado
    invalidate_changed_scenes(ctx, scenes, rdir, xin)
    # 1) normalização paralela
    todo = [s for s in scenes if not final_path(s["id"]).exists() and not raw_path(s["id"]).exists()]
    done = n - len(todo)
    workers = int(ctx.settings["render"]["parallel_scenes"])
    try:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futs = [pool.submit(_normalize_scene, ctx, s, total[s["id"]], raw_path(s["id"])) for s in todo]
            for fut in as_completed(futs):
                fut.result()
                ctx.check_cancel()
                done += 1
                ctx.progress(0.5 * done / n, f"Preparando cenas {done}/{n}")
        # 2) transições, em ordem (uma cena pode ter transição de entrada e de saída)
        for s in scenes:
            sid = s["id"]
            if sid in xin and not final_path(sid).exists():
                prev = xin[sid]
                _crossfade_in(ctx, final_path(prev), main[prev], raw_path(sid), total[sid],
                              s["transition_in"]["duration"], final_path(sid), s["transition_in"]["type"])
    except ffmpeg.FFmpegError as e:
        raise StepError("RENDER_FAILED", f"Falha ao preparar cena: {e}", e.log) from e

    # 3) overlays dentro da cena (a direção já limita cada overlay a uma cena)
    style = tl["style"]
    direction = get_direction(tl["direction"])
    animated = getattr(direction.overlays, "ANIMATED", {}) if direction.overlays else {}
    static = getattr(direction.overlays, "TEMPLATES", {}) if direction.overlays else {}
    overlays = tl.get("overlays", [])
    by_scene: dict[str, list[tuple[dict, Path]]] = {}
    try:
        for i, o in enumerate(overlays):
            host = next((s for s in scenes if s["start"] <= o["start"] < s["end"]), None)
            if not host:
                continue
            if o["type"] in animated:
                src = ctx.path("render", "overlays", f"o{i:03d}.mkv")
                if not src.exists():
                    ctx.progress(0.5 + 0.02 * i / len(overlays), f"Animando textos {i + 1}/{len(overlays)}")
                    anim.encode(animated[o["type"]](o, style), o["end"] - o["start"], src)
            elif o["type"] in static:  # direções com templates estáticos (PNG)
                src = ctx.path("render", "overlays", f"o{i:03d}.png")
                if not src.exists():
                    static[o["type"]](o["text"], style, src)
            else:
                continue
            by_scene.setdefault(host["id"], []).append((o, src))
    except ffmpeg.FFmpegError as e:
        raise StepError("RENDER_FAILED", f"Falha ao animar overlay: {e}", e.log) from e
    scene_file: dict[str, str] = {s["id"]: f"scenes/{s['id']}.mp4" for s in scenes}
    try:
        for s in scenes:
            items = by_scene.get(s["id"])
            if not items:
                continue
            baked = rdir / f"{s['id']}.ov.mp4"
            if not baked.exists():
                ctx.progress(0.52, f"Aplicando overlays ({s['id']})")
                _bake_overlays(ctx, final_path(s["id"]), s["start"], items, total[s["id"]], baked)
            scene_file[s["id"]] = f"scenes/{baked.name}"
    except ffmpeg.FFmpegError as e:
        raise StepError("RENDER_FAILED", f"Falha ao aplicar overlay: {e}", e.log) from e

    lines = []
    for s in scenes:
        lines.append(f"file '{scene_file[s['id']]}'")
        if s["id"] in tail:
            lines.append(f"outpoint {main[s['id']] / FPS:.6f}")
    concat = ctx.path("render", "scenes.txt")
    concat.write_text("\n".join(lines) + "\n", encoding="utf-8")
    ctx.progress(0.55, "Compondo vídeo final")

    # 4) legendas
    fonts_dir = ctx.path("render", "fonts", "x").parent
    if tl.get("subtitles"):
        transcript = ctx.read_json("transcript.json")
        write_ass(transcript["words"], style["subtitle"], ctx.path("subs.ass"), mute=tl["subtitles"].get("mute"))
        try:
            shutil.copy(find_font(style["subtitle"]["font"]), fonts_dir)
        except (FileNotFoundError, OSError):
            pass

    out = ctx.path("output", "final.mp4")
    # render rápido por GPU só se o AMF funcionar neste PC (no app instalado, nem todo amigo tem placa AMD)
    use_amf = ctx.settings["render"]["mode"] == "fast" and ffmpeg.amf_available()
    try:
        _final_pass(ctx, tl, concat, out, use_amf)
    except ffmpeg.FFmpegError as e:
        if not use_amf:
            raise StepError("RENDER_FAILED", f"Falha na composição final: {e}", e.log) from e
        ctx.issue("AMF_FALLBACK", "Encode por GPU (AMF) falhou; renderizando com libx264", detail=e.log[-2000:])
        try:
            _final_pass(ctx, tl, concat, out, False)
        except ffmpeg.FFmpegError as e2:
            raise StepError("RENDER_FAILED", f"Falha na composição final: {e2}", e2.log) from e2
    return str(out)


def scene_render_fp(ctx: JobContext, scene: dict) -> str:
    from ...artifacts import fingerprint

    asset = ctx.dir / scene["asset"] if scene.get("asset") else None
    data = {k: v for k, v in scene.items() if k not in ("start", "end")}
    data["duration"] = round(scene["end"] - scene["start"], 3)
    data["asset_fp"] = fingerprint(asset) if asset else None
    return hashlib.sha256(json.dumps(data, sort_keys=True, default=str).encode()).hexdigest()[:20]


def invalidate_changed_scenes(ctx: JobContext, scenes: list[dict], rdir: Path, xin: dict[str, str]) -> list[str]:
    """Apaga os intermediários das cenas cuja impressão digital mudou (e a transição da cena seguinte, que usa a
    anterior). Sem impressão gravada (render antigo), o intermediário existente é aproveitado como antes."""
    changed = []
    nxt = {prev: sid for sid, prev in xin.items()}
    for s in scenes:
        sid = s["id"]
        fp_file = rdir / f"{sid}.fp"
        fp = scene_render_fp(ctx, s)
        old = fp_file.read_text(encoding="utf-8").strip() if fp_file.exists() else None
        if old is not None and old != fp:
            changed.append(sid)
            for name in (f"{sid}.mp4", f"{sid}.raw.mp4", f"{sid}.ov.mp4"):
                (rdir / name).unlink(missing_ok=True)
            if sid in nxt:  # a transição de entrada da próxima cena foi feita com esta
                (rdir / f"{nxt[sid]}.mp4").unlink(missing_ok=True)
                (rdir / f"{nxt[sid]}.ov.mp4").unlink(missing_ok=True)
        fp_file.write_text(fp, encoding="utf-8")
    return changed


def _bake_overlays(ctx: JobContext, scene_mp4: Path, scene_start: float, items: list[tuple[dict, Path]],
                   frames: int, out: Path) -> None:
    """Sobrepõe os overlays à cena: vídeos animados com alfa (.mkv) ou PNGs com fade. `movie` lê o arquivo dentro
    do grafo, sem segunda entrada. Citações desfocam e escurecem a cena por trás enquanto duram."""
    f, cur = [], "0:v"
    for k, (o, path) in enumerate(items):
        dur = o["end"] - o["start"]
        rel = max(0.0, o["start"] - scene_start)
        src = path.relative_to(ctx.dir).as_posix()
        if o.get("backdrop") == "blur":
            f.append(f"[{cur}]split[bm{k}][bb{k}]")
            f.append(f"[bb{k}]boxblur=22:2,eq=brightness=-0.06,format=yuva420p,"
                     f"fade=t=in:st={rel:.3f}:d=0.35:alpha=1,fade=t=out:st={rel + dur - 0.4:.3f}:d=0.4:alpha=1[bf{k}]")
            f.append(f"[bm{k}][bf{k}]overlay=0:0[bk{k}]")
            cur = f"bk{k}"
        if path.suffix == ".mkv":
            f.append(f"movie={src},setpts=PTS-STARTPTS+{rel:.3f}/TB[ov{k}]")
        else:
            fd = min(0.3, dur / 3)
            f.append(f"movie={src},loop=loop=-1:size=1,fps={FPS},trim=duration={dur:.3f},format=rgba,"
                     f"fade=t=in:st=0:d={fd:.2f}:alpha=1,fade=t=out:st={dur - fd:.3f}:d={fd:.2f}:alpha=1,"
                     f"setpts=PTS-STARTPTS+{rel:.3f}/TB[ov{k}]")
        f.append(f"[{cur}][ov{k}]overlay=0:0:eof_action=pass:repeatlast=0[v{k}]")
        cur = f"v{k}"
    f.append(f"[{cur}]format=yuv420p[v]")
    tmp = out.with_suffix(".tmp.mp4")
    ffmpeg.run(["-i", scene_mp4.relative_to(ctx.dir).as_posix(), "-filter_complex", ";".join(f), "-map", "[v]",
                "-frames:v", str(frames)] + _intermediate_codec(ctx) + [tmp.relative_to(ctx.dir).as_posix()],
               cwd=ctx.dir, cancel=ctx.is_cancelled)
    tmp.replace(out)


SFX_RATE = 48000


def _load_sfx(path: str, cache: dict[str, np.ndarray]) -> np.ndarray:
    if path not in cache:
        data = np.frombuffer(ffmpeg.decode_mono_f32(Path(path), SFX_RATE), dtype=np.float32).copy()
        peak = float(np.max(np.abs(data))) if data.size else 0.0
        cache[path] = data / peak * 0.9 if peak > 0 else data  # todo som no mesmo nível antes do ganho da deixa
    return cache[path]


def mix_sfx(events: list[dict], duration: float, dest: Path, load=_load_sfx) -> Path | None:
    """Pré-mixa os efeitos numa faixa mono: cada som entra no ponto da deixa conforme o alinhamento — "start"
    (começa ali), "end" (termina ali: risers) ou "peak" (o pico de energia cai ali: whooshes de transição)."""
    if not events:
        return None
    n = int((duration + 1) * SFX_RATE)
    bed = np.zeros(n, np.float32)
    cache: dict[str, np.ndarray] = {}
    for ev in events:
        snd = load(ev["file"], cache)
        if not snd.size:
            continue
        if ev["category"] == "riser" and snd.size > 3 * SFX_RATE:  # risers longos: só os 3 s finais
            snd = snd[-3 * SFX_RATE:].copy()
            ramp = int(0.3 * SFX_RATE)
            snd[:ramp] *= np.linspace(0, 1, ramp, dtype=np.float32)
        if ev.get("align") == "end":
            offset = snd.size
        elif ev.get("align") == "peak":
            win = int(0.02 * SFX_RATE)
            rough = int(np.argmax(np.convolve(np.abs(snd), np.ones(win, np.float32), "same")))  # região de energia
            lo = max(0, rough - win)
            offset = lo + int(np.argmax(np.abs(snd[lo:rough + win])))  # pico exato dentro dela
        else:
            offset = 0
        i = int(round(ev["start"] * SFX_RATE)) - offset
        a, b = max(0, i), min(n, i + snd.size)
        if b > a:
            bed[a:b] += snd[a - i:b - i] * (10 ** (ev.get("gain_db", -20) / 20))
    pcm = (np.clip(bed, -1, 1) * 32767).astype("<i2")
    import wave

    with wave.open(str(dest), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SFX_RATE)
        w.writeframes(pcm.tobytes())
    return dest


def _final_pass(ctx: JobContext, tl: dict, concat: Path, out: Path, use_amf: bool) -> None:
    duration = tl["duration"]
    inputs: list[str] = []
    count = 0

    def add_input(*args: str) -> int:
        nonlocal count
        inputs.extend(args)
        count += 1
        return count - 1

    video_idx = add_input("-f", "concat", "-safe", "0", "-i", str(concat.relative_to(ctx.dir)))
    narr_idx = add_input("-i", tl["audio"]["narration"])
    music = tl["audio"].get("music")
    music_idx = add_input("-stream_loop", "-1", "-i", music["file"]) if music else None
    bed = mix_sfx(tl["audio"].get("sfx") or [], duration, ctx.path("render", "sfx.wav"))
    sfx_idx = add_input("-i", str(bed.relative_to(ctx.dir))) if bed else None

    f: list[str] = [f"[{video_idx}:v]settb=AVTB,fps={FPS},format=yuv420p[v0]"]
    cur = "v0"
    look = tl.get("look")
    if look:  # textura de filme antes das legendas (legenda fica limpa): vinheta + grão só na luminância
        chain = [f"vignette=angle={look['vignette']}"] if look.get("vignette") else []
        chain += [f"noise=c0s={int(look['grain'])}:c0f=t+u"] if look.get("grain") else []
        if chain:
            f.append(f"[{cur}]{','.join(chain)}[vl]")
            cur = "vl"
    if tl.get("subtitles"):
        f.append(f"[{cur}]ass=subs.ass:fontsdir=render/fonts[vs]")
        cur = "vs"
    f.append(f"[{cur}]null[vout]")

    if music_idx is not None:
        vol = music["volume_db"]
        fade_out_start = max(0.0, duration - 3)
        f.append(f"[{narr_idx}:a]aformat=sample_rates=48000:channel_layouts=stereo,asplit=2[n1][n2]")
        f.append(f"[{music_idx}:a]aformat=sample_rates=48000:channel_layouts=stereo,atrim=0:{duration:.3f},"
                 f"volume={vol}dB,afade=t=in:d=2,afade=t=out:st={fade_out_start:.3f}:d=3[m]")
        if music.get("ducking"):
            f.append("[m][n2]sidechaincompress=threshold=0.05:ratio=6:attack=30:release=500[md]")
        else:
            f.append("[n2]anullsink;[m]anull[md]")
        f.append("[n1][md]amix=inputs=2:duration=first:normalize=0[amain]")
    else:
        f.append(f"[{narr_idx}:a]aformat=sample_rates=48000:channel_layouts=stereo[amain]")
    if sfx_idx is not None:
        f.append(f"[{sfx_idx}:a]aformat=sample_rates=48000:channel_layouts=stereo[fx]")
        f.append("[amain][fx]amix=inputs=2:duration=first:normalize=0,alimiter=limit=0.97:level=0[aout]")
    else:
        f.append("[amain]anull[aout]")

    script = ctx.path("render", "filter.txt")
    script.write_text(";\n".join(f), encoding="utf-8")
    tmp = out.with_suffix(".tmp.mp4")
    args = inputs + ["-/filter_complex", str(script.relative_to(ctx.dir)), "-map", "[vout]", "-map", "[aout]",
                     "-t", f"{duration:.3f}", *_final_encoder(ctx, use_amf), "-r", str(FPS),
                     "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-movflags", "+faststart",
                     str(tmp.relative_to(ctx.dir))]
    ffmpeg.run(args, cwd=ctx.dir, total_seconds=duration, cancel=ctx.is_cancelled,
               on_progress=lambda frac: ctx.progress(0.55 + 0.45 * frac, f"Renderizando {frac:.0%}"))
    tmp.replace(out)
