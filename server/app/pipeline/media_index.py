"""Índice temporal de arquivos de vídeo AUTORIZADOS e conferência do trecho final (Fases D2/D3).

- index_file: duração/resolução reais (ffprobe), cortes de cena (filtro scene do FFmpeg) ou amostragem espaçada
  quando não há cortes, e um frame por segmento com o timestamp MEDIDO (não estimado por porcentagem).
- refine_around: aprofunda só a região promissora (frames mais densos perto de um instante).
- frames_at / extract_frame: frames em instantes exatos de um arquivo local.
- cut_segment: recorta [início, fim] do arquivo local (sem áudio) e confere com o ffprobe que o resultado tem a
  duração pedida.
- probe_video: duração, largura e altura reais (para tratar falha com alternativa).
Nenhuma rede: tudo sobre arquivos que já estão no computador. Miniaturas hq1/hq2/hq3 do YouTube servem só para
descobrir candidatos; o corte usa sempre o arquivo e os timestamps medidos aqui.
"""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
from pathlib import Path

from ..config import CACHE_DIR
from .render import ffmpeg

SCENE_THRESHOLD = 0.35
COARSE_STEP = 20.0  # amostragem espaçada (s) quando não há cortes detectáveis
MAX_SEGMENTS = 120


def probe_video(path: Path) -> dict:
    """{duration, width, height, has_video}. Lança ffmpeg.FFmpegError se o arquivo não abre."""
    info = ffmpeg.probe(path)
    video = next((s for s in info.get("streams", []) if s.get("codec_type") == "video"), None)
    duration = float(info.get("format", {}).get("duration") or (video or {}).get("duration") or 0)
    return {"duration": duration, "width": int((video or {}).get("width") or 0),
            "height": int((video or {}).get("height") or 0), "has_video": video is not None}


def file_hash(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        while block := f.read(chunk):
            h.update(block)
    return h.hexdigest()


def detect_cuts(path: Path, threshold: float = SCENE_THRESHOLD) -> list[float]:
    """Instantes (s) de troca de plano segundo o filtro `scene` do FFmpeg (showinfo → pts_time)."""
    cmd = [ffmpeg.ffmpeg_bin(), "-hide_banner", "-nostdin", "-i", str(path), "-an",
           "-vf", f"select='gt(scene,{threshold})',showinfo", "-f", "null", "-"]
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                          creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return sorted({round(float(m), 3) for m in re.findall(r"pts_time:([0-9.]+)", proc.stderr)})


def extract_frame(path: Path, t: float, dest: Path, width: int = 320) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    ffmpeg.run(["-ss", f"{max(0.0, t):.3f}", "-i", str(path), "-frames:v", "1", "-vf", f"scale={width}:-2",
                "-q:v", "4", str(dest)])
    if not dest.exists() or dest.stat().st_size == 0:
        raise ffmpeg.FFmpegError(f"frame em {t:.2f}s não foi extraído de {Path(path).name}")
    return dest


def _frames_dir(path: Path) -> Path:
    return CACHE_DIR / "index" / hashlib.sha1(str(Path(path).resolve()).encode()).hexdigest()[:16]


def frames_at(path: Path, times: list[float], tag: str = "f") -> list[tuple[float, Path]]:
    out = []
    folder = _frames_dir(path)
    for t in times:
        dest = folder / f"{tag}_{t:09.3f}.jpg"
        if not dest.exists():
            extract_frame(path, t, dest)
        out.append((t, dest))
    return out


def index_file(path: Path, coarse_step: float = COARSE_STEP) -> dict:
    """{duration, width, height, sha256, segments: [{start, end, frame_t, frame}]} — gravado ao lado dos frames
    e reaproveitado enquanto o arquivo (hash) não mudar."""
    path = Path(path)
    digest = file_hash(path)
    cache = _frames_dir(path) / "index.json"
    if cache.exists():
        data = json.loads(cache.read_text(encoding="utf-8"))
        if data.get("sha256") == digest:
            return data
    meta = probe_video(path)
    if not meta["has_video"] or meta["duration"] <= 0:
        raise ffmpeg.FFmpegError(f"{path.name} não tem vídeo legível")
    dur = meta["duration"]
    cuts = [c for c in detect_cuts(path) if 0.5 < c < dur - 0.5]
    bounds = [0.0, *cuts, dur] if cuts else [0.0, *[i * coarse_step for i in range(1, int(dur // coarse_step) + 1)
                                                    if i * coarse_step < dur - 0.5], dur]
    segments = []
    for a, b in list(zip(bounds, bounds[1:]))[:MAX_SEGMENTS]:
        mid = round(a + (b - a) / 2, 3)
        (t, frame), = frames_at(path, [mid], "seg")
        segments.append({"start": round(a, 3), "end": round(b, 3), "frame_t": t, "frame": str(frame)})
    data = {**meta, "sha256": digest, "method": "scene_cuts" if cuts else f"every_{coarse_step:g}s",
            "segments": segments}
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(data, indent=1), encoding="utf-8")
    return data


def refine_around(path: Path, t: float, span: float = 10.0, step: float = 2.0, duration: float | None = None
                  ) -> list[tuple[float, Path]]:
    """Frames mais densos só perto do instante promissor (amostragem em duas etapas)."""
    dur = duration if duration is not None else probe_video(path)["duration"]
    times = [round(x, 3) for x in _frange(max(0.0, t - span), min(dur - 0.05, t + span), step)]
    return frames_at(path, times, "ref")


def _frange(a: float, b: float, step: float) -> list[float]:
    out, x = [], a
    while x <= b + 1e-9:
        out.append(x)
        x += step
    return out


def segment_times(start: float, end: float, n: int = 3) -> list[float]:
    """Instantes para conferir o trecho USADO: início, meio e fim (com uma margem dentro do intervalo)."""
    span = max(0.0, end - start)
    if n <= 1 or span <= 0:
        return [round(start + span / 2, 3)]
    margin = min(0.25, span * 0.1)
    a, b = start + margin, end - margin
    return [round(a + (b - a) * i / (n - 1), 3) for i in range(n)]


def cut_segment(src: Path, start: float, end: float, dest: Path) -> dict:
    """Recorta [start, end] (sem áudio) e devolve o probe do resultado; confere a duração obtida."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    ffmpeg.run(["-ss", f"{start:.3f}", "-i", str(src), "-t", f"{end - start:.3f}", "-an", "-c:v", "libx264",
                "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p", str(dest)])
    meta = probe_video(dest)
    if abs(meta["duration"] - (end - start)) > 0.5:
        raise ffmpeg.FFmpegError(f"trecho recortado com {meta['duration']:.2f}s, pedido {end - start:.2f}s")
    return meta
