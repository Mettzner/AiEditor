"""Uso por segmento (Fase D4): (asset, início, fim) em vez de "vídeo já usado".

Vídeos longos podem dar trechos distintos a cenas diferentes, sem sobreposição e com intervalo mínimo entre eles,
até `max_segments_per_video`; cada canal/autor entra no máximo `max_clips_per_channel` vezes no vídeo. Clipes
curtos e imagens continuam de uso único. Vídeos únicos e segmentos são contados separadamente.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field

LONG_FACTOR = 4.0  # vídeo "longo": pelo menos 4× a duração da cena


@dataclass
class SegmentLedger:
    max_per_video: int = 2
    max_per_channel: int = 4
    min_gap: float = 30.0
    claims: dict[str, list[tuple[float, float]]] = field(default_factory=dict)
    channels: dict[str, int] = field(default_factory=dict)
    lock: threading.Lock = field(default_factory=threading.Lock)

    @classmethod
    def from_cfg(cls, cfg: dict) -> "SegmentLedger":
        return cls(int(cfg.get("max_segments_per_video", 2)), int(cfg.get("max_clips_per_channel", 4)),
                   float(cfg.get("segment_min_gap_seconds", 30)))

    @staticmethod
    def is_long(duration: float | None, scene_dur: float) -> bool:
        return bool(duration) and duration >= max(scene_dur, 1.0) * LONG_FACTOR

    def _limit(self, long: bool) -> int:
        return self.max_per_video if long else 1

    def _fits(self, key: str, start: float, end: float, long: bool, channel: str) -> bool:
        taken = self.claims.get(key, [])
        if len(taken) >= self._limit(long):
            return False
        if channel and self.channels.get(channel, 0) >= self.max_per_channel:
            return False
        return all(end + self.min_gap <= a or start >= b + self.min_gap for a, b in taken)

    def can_claim(self, key: str, start: float, end: float, long: bool, channel: str = "") -> bool:
        with self.lock:
            return self._fits(key, start, end, long, channel)

    def claim(self, key: str, start: float, end: float, long: bool, channel: str = "") -> bool:
        with self.lock:
            if not self._fits(key, start, end, long, channel):
                return False
            self.claims.setdefault(key, []).append((start, end))
            if channel:
                self.channels[channel] = self.channels.get(channel, 0) + 1
            return True

    def release(self, key: str, start: float, end: float, channel: str = "") -> None:
        with self.lock:
            taken = self.claims.get(key, [])
            if (start, end) in taken:
                taken.remove((start, end))
                if channel and self.channels.get(channel):
                    self.channels[channel] -= 1
            if not taken:
                self.claims.pop(key, None)

    def full(self, key: str, long: bool) -> bool:
        with self.lock:
            return len(self.claims.get(key, [])) >= self._limit(long)

    def stats(self) -> dict:
        with self.lock:
            return {"unique_assets": len(self.claims), "segments": sum(len(v) for v in self.claims.values()),
                    "reused_assets": sum(1 for v in self.claims.values() if len(v) > 1)}


def planned_interval(duration: float, pos: float, scene_dur: float, is_image: bool) -> tuple[float, float]:
    """Mesmo cálculo do download: centraliza a cena no melhor frame apontado pela IA."""
    if is_image or not duration or duration <= scene_dur:
        return 0.0, round(scene_dur, 2)
    start = round(min(max(0.0, duration * pos - scene_dur / 2), duration - scene_dur - 0.05), 2)
    return start, round(start + scene_dur, 2)
