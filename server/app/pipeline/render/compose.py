"""Etapa 7: render da timeline → output/final.mp4 (§10).

1. Normaliza cada cena em paralelo (corte, 1920×1080, 30 fps, Ken Burns nas imagens). A cena que
   antecede um crossfade ganha uma cauda extra do tamanho da transição.
2. Cenas com crossfade de entrada recebem, nos primeiros quadros, a mistura com a cauda da anterior.
   Assim a passada final não precisa de xfade (que, junto com overlay, trava o agendador do FFmpeg).
3. Passada final única: concat (com outpoint cortando as caudas) + overlays + legendas + narração +
   música com ducking.
"""
from __future__ import annotations

import shutil
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from ...directions import get_direction
from ...worker.context import JobContext, StepError
from . import ffmpeg
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


def _normalize_scene(ctx: JobContext, scene: dict, frames: int, out: Path) -> None:
    asset = ctx.dir / scene["asset"]
    if scene.get("motion") and scene["motion"]["type"] == "kenburns":
        (z0, x0, y0), (z1, x1, y1) = scene["motion"]["from"], scene["motion"]["to"]
        p = f"(on/{max(1, frames - 1)})"
        z = f"{z0}+({z1 - z0})*{p}"
        x = f"max(0,min(iw-iw/zoom,({x0}+({x1 - x0})*{p})*iw-iw/zoom/2))"
        y = f"max(0,min(ih-ih/zoom,({y0}+({y1 - y0})*{p})*ih-ih/zoom/2))"
        vf = (f"scale=3840:2160:force_original_aspect_ratio=increase,crop=3840:2160,"
              f"zoompan=z='{z}':x='{x}':y='{y}':d={frames}:s={W}x{H}:fps={FPS},setsar=1,format=yuv420p")
        args = ["-i", str(asset), "-vf", vf]
    else:
        vf = (f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},fps={FPS},setsar=1,"
              f"format=yuv420p,tpad=stop_mode=clone:stop_duration={frames / FPS:.3f}")
        args = ["-ss", f"{scene.get('in', 0):.3f}", "-i", str(asset), "-vf", vf]
    tmp = out.with_suffix(".tmp.mp4")
    ffmpeg.run(args + ["-frames:v", str(frames)] + _intermediate_codec(ctx) + [str(tmp)])
    tmp.replace(out)


def _crossfade_in(ctx: JobContext, prev_file: Path, prev_frames: int, raw: Path, frames: int, duration: float,
                  out: Path) -> None:
    """Mistura a cauda da cena anterior (quadros após prev_frames) com o início desta cena."""
    f = (f"[0:v]trim=start_frame={prev_frames},setpts=PTS-STARTPTS[a];"
         f"[1:v]setpts=PTS-STARTPTS[b];"
         f"[a][b]xfade=transition=fade:duration={duration:.3f}:offset=0,format=yuv420p[v]")
    tmp = out.with_suffix(".tmp.mp4")
    ffmpeg.run(["-i", str(prev_file), "-i", str(raw), "-filter_complex", f, "-map", "[v]", "-frames:v", str(frames)]
               + _intermediate_codec(ctx) + [str(tmp)])
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
        return _render(ctx, timeline)


def _render(ctx: JobContext, tl: dict) -> str:
    rdir = ctx.path("render", "scenes", "x").parent
    scenes = tl["scenes"]
    n = len(scenes)
    main = {s["id"]: _frames(s["end"] - s["start"]) for s in scenes}
    tail = {a["id"]: _frames(b["transition_in"]["duration"])
            for a, b in zip(scenes, scenes[1:]) if b["transition_in"]["type"] == "crossfade"}
    total = {sid: main[sid] + tail.get(sid, 0) for sid in main}
    xin = {b["id"]: a["id"] for a, b in zip(scenes, scenes[1:]) if b["transition_in"]["type"] == "crossfade"}

    def final_path(sid: str) -> Path:
        return rdir / f"{sid}.mp4"

    def raw_path(sid: str) -> Path:
        return rdir / (f"{sid}.raw.mp4" if sid in xin else f"{sid}.mp4")

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
        # 2) crossfades, em ordem (uma cena pode ter crossfade de entrada e de saída)
        for s in scenes:
            sid = s["id"]
            if sid in xin and not final_path(sid).exists():
                prev = xin[sid]
                _crossfade_in(ctx, final_path(prev), main[prev], raw_path(sid), total[sid],
                              s["transition_in"]["duration"], final_path(sid))
    except ffmpeg.FFmpegError as e:
        raise StepError("RENDER_FAILED", f"Falha ao preparar cena: {e}", e.log) from e

    lines = []
    for s in scenes:
        lines.append(f"file 'scenes/{s['id']}.mp4'")
        if s["id"] in tail:
            lines.append(f"outpoint {main[s['id']] / FPS:.6f}")
    concat = ctx.path("render", "scenes.txt")
    concat.write_text("\n".join(lines) + "\n", encoding="utf-8")
    ctx.progress(0.55, "Compondo vídeo final")

    # 3) overlays e legendas
    style = tl["style"]
    direction = get_direction(tl["direction"])
    overlay_files = []
    for i, o in enumerate(tl.get("overlays", [])):
        tpl = getattr(direction.overlays, "TEMPLATES", {}).get(o["type"]) if direction.overlays else None
        if tpl:
            overlay_files.append((o, tpl(o["text"], style, ctx.path("render", "overlays", f"o{i:03d}.png"))))

    fonts_dir = ctx.path("render", "fonts", "x").parent
    if tl.get("subtitles"):
        transcript = ctx.read_json("transcript.json")
        write_ass(transcript["words"], style["subtitle"], ctx.path("subs.ass"))
        try:
            shutil.copy(find_font(style["subtitle"]["font"]), fonts_dir)
        except (FileNotFoundError, OSError):
            pass

    out = ctx.path("output", "final.mp4")
    use_amf = ctx.settings["render"]["mode"] == "fast"
    try:
        _final_pass(ctx, tl, concat, overlay_files, out, use_amf)
    except ffmpeg.FFmpegError as e:
        if not use_amf:
            raise StepError("RENDER_FAILED", f"Falha na composição final: {e}", e.log) from e
        ctx.issue("AMF_FALLBACK", "Encode por GPU (AMF) falhou; renderizando com libx264", detail=e.log[-2000:])
        try:
            _final_pass(ctx, tl, concat, overlay_files, out, False)
        except ffmpeg.FFmpegError as e2:
            raise StepError("RENDER_FAILED", f"Falha na composição final: {e2}", e2.log) from e2
    return str(out)


def _final_pass(ctx: JobContext, tl: dict, concat: Path, overlay_files, out: Path, use_amf: bool) -> None:
    duration = tl["duration"]
    inputs: list[str] = []
    count = 0

    def add_input(*args: str) -> int:
        nonlocal count
        inputs.extend(args)
        count += 1
        return count - 1

    video_idx = add_input("-f", "concat", "-safe", "0", "-i", str(concat.relative_to(ctx.dir)))
    ov_idx = [add_input("-loop", "1", "-framerate", str(FPS), "-t", f"{o['end'] - o['start']:.3f}",
                        "-i", str(png.relative_to(ctx.dir))) for o, png in overlay_files]
    narr_idx = add_input("-i", tl["audio"]["narration"])
    music = tl["audio"].get("music")
    music_idx = add_input("-stream_loop", "-1", "-i", music["file"]) if music else None

    f: list[str] = [f"[{video_idx}:v]settb=AVTB,fps={FPS},format=yuv420p[v0]"]
    cur = "v0"
    for i, (o, _) in enumerate(overlay_files):
        dur = o["end"] - o["start"]
        fd = min(0.3, dur / 3)
        f.append(f"[{ov_idx[i]}:v]format=rgba,fade=t=in:st=0:d={fd:.2f}:alpha=1,"
                 f"fade=t=out:st={dur - fd:.3f}:d={fd:.2f}:alpha=1,setpts=PTS+{o['start']:.3f}/TB[ov{i}]")
        f.append(f"[{cur}][ov{i}]overlay=0:0:eof_action=pass[o{i}]")
        cur = f"o{i}"
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
        f.append("[n1][md]amix=inputs=2:duration=first:normalize=0[aout]")
    else:
        f.append(f"[{narr_idx}:a]anull[aout]")

    script = ctx.path("render", "filter.txt")
    script.write_text(";\n".join(f), encoding="utf-8")
    tmp = out.with_suffix(".tmp.mp4")
    args = inputs + ["-/filter_complex", str(script.relative_to(ctx.dir)), "-map", "[vout]", "-map", "[aout]",
                     "-t", f"{duration:.3f}", *_final_encoder(ctx, use_amf), "-r", str(FPS),
                     "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-movflags", "+faststart",
                     str(tmp.relative_to(ctx.dir))]
    ffmpeg.run(args, cwd=ctx.dir, total_seconds=duration,
               on_progress=lambda frac: ctx.progress(0.55 + 0.45 * frac, f"Renderizando {frac:.0%}"))
    tmp.replace(out)
