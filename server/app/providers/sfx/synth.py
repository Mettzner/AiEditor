"""Efeitos sonoros sintetizados (numpy): o fallback quando não há biblioteca nem chave do Freesound.

Ruído filtrado com faixa móvel (whoosh, swoosh, riser), seno grave com queda de frequência (impact) e
estalos curtos (click). Cada categoria tem algumas variações por semente para não repetir o mesmo som.
"""
from __future__ import annotations

import wave
from pathlib import Path

import numpy as np

SR = 48000
CATEGORIES = ("whoosh", "swoosh_soft", "impact", "riser", "click")


def _band_sweep(noise: np.ndarray, centers: np.ndarray, bw_ratio: float) -> np.ndarray:
    """Passa-faixa variando no tempo (STFT com janela de Hann e máscara gaussiana em torno do centro)."""
    win, hop = 2048, 512
    window = np.hanning(win).astype(np.float32)
    freqs = np.fft.rfftfreq(win, 1 / SR)
    n = len(noise)
    padded = np.concatenate([noise, np.zeros(win, np.float32)])
    out = np.zeros(n + win, np.float32)
    for start in range(0, n, hop):
        c = centers[min(start + win // 2, n - 1)]
        mask = np.exp(-0.5 * ((freqs - c) / max(40.0, c * bw_ratio)) ** 2)
        spec = np.fft.rfft(padded[start:start + win] * window) * mask
        out[start:start + win] += np.fft.irfft(spec).astype(np.float32) * window
    return out[:n]


def _log_curve(points: list[tuple[float, float]], n: int) -> np.ndarray:
    """Curva de frequência interpolada em escala log entre (fração do tempo, Hz)."""
    x = np.linspace(0, 1, n)
    xs, fs = zip(*points)
    return np.exp(np.interp(x, xs, np.log(fs))).astype(np.float32)


def _normalize(x: np.ndarray, peak: float = 0.9) -> np.ndarray:
    m = float(np.max(np.abs(x))) or 1.0
    return (x / m * peak).astype(np.float32)


def _fades(x: np.ndarray, fade_in: float = 0.004, fade_out: float = 0.02) -> np.ndarray:
    a, b = int(fade_in * SR), int(fade_out * SR)
    if a:
        x[:a] *= np.linspace(0, 1, a)
    if b:
        x[-b:] *= np.linspace(1, 0, b)
    return x


def whoosh(rng: np.random.Generator) -> np.ndarray:
    dur = 1.0 + rng.uniform(-0.15, 0.25)
    n = int(dur * SR)
    peak = rng.uniform(0.5, 0.62)
    lo, hi = rng.uniform(250, 450), rng.uniform(1800, 3200)
    centers = _log_curve([(0, lo), (peak, hi), (1, lo * 1.2)], n)
    body = _band_sweep(rng.standard_normal(n).astype(np.float32), centers, 0.55)
    p = np.linspace(0, 1, n)
    env = np.where(p < peak, (p / peak) ** 2.2, np.exp(-(p - peak) / (1 - peak) * 4))
    return _normalize(_fades(body * env))


def swoosh_soft(rng: np.random.Generator) -> np.ndarray:
    dur = 0.45 + rng.uniform(-0.05, 0.12)
    n = int(dur * SR)
    centers = _log_curve([(0, rng.uniform(1200, 1800)), (1, rng.uniform(4000, 6000))], n)
    body = _band_sweep(rng.standard_normal(n).astype(np.float32), centers, 0.45)
    env = np.sin(np.pi * np.linspace(0, 1, n) ** 0.8) ** 2
    return _normalize(_fades(body * env))


def impact(rng: np.random.Generator) -> np.ndarray:
    dur = 2.4 + rng.uniform(-0.2, 0.4)
    n = int(dur * SR)
    t = np.arange(n) / SR
    f0, f1 = rng.uniform(95, 125), rng.uniform(34, 42)
    freq = f1 + (f0 - f1) * np.exp(-t * 9)
    phase = 2 * np.pi * np.cumsum(freq) / SR
    boom = np.sin(phase) * np.exp(-t * rng.uniform(1.8, 2.6))
    hit = _band_sweep(rng.standard_normal(n).astype(np.float32), np.full(n, 350, np.float32), 1.2)
    hit *= np.exp(-t * 28)
    x = np.tanh((boom * 1.0 + _normalize(hit, 0.6)) * 1.6)
    return _normalize(_fades(x, 0.001, 0.2))


def riser(rng: np.random.Generator) -> np.ndarray:
    dur = 2.4 + rng.uniform(-0.2, 0.5)
    n = int(dur * SR)
    t = np.arange(n) / SR
    centers = _log_curve([(0, rng.uniform(250, 400)), (1, rng.uniform(6000, 8000))], n)
    body = _band_sweep(rng.standard_normal(n).astype(np.float32), centers, 0.35)
    tone_f = np.interp(t, [0, dur], [rng.uniform(180, 240), rng.uniform(700, 900)])
    tone = np.sin(2 * np.pi * np.cumsum(tone_f) / SR) * 0.25
    env = (t / dur) ** 2.2
    return _normalize(_fades(_normalize(body) * env + tone * env, 0.05, 0.03))


def click(rng: np.random.Generator) -> np.ndarray:
    dur = 0.07
    n = int(dur * SR)
    t = np.arange(n) / SR
    burst = _band_sweep(rng.standard_normal(n).astype(np.float32), np.full(n, rng.uniform(2200, 3400), np.float32),
                        0.6) * np.exp(-t * rng.uniform(110, 150))
    ping = np.sin(2 * np.pi * rng.uniform(2800, 3600) * t) * np.exp(-t * 90) * 0.3
    return _normalize(_fades(_normalize(burst) + ping, 0.0005, 0.01))


GENERATORS = {"whoosh": whoosh, "swoosh_soft": swoosh_soft, "impact": impact, "riser": riser, "click": click}


def write_wav(samples: np.ndarray, dest: Path) -> Path:
    pcm = (np.clip(samples, -1, 1) * 32767).astype("<i2")
    with wave.open(str(dest), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())
    return dest


def generate(category: str, variant: int, dest: Path) -> Path:
    seed = 1000 + variant * 7919 + sum(map(ord, category))  # hash() de str muda a cada processo
    return write_wav(GENERATORS[category](np.random.default_rng(seed)), dest)
