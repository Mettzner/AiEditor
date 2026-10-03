"""Cronometragem por etapa e por cena (AJUSTE_ESTILO_CONTEXTUAL.md §10.1) → timing_report.json."""
from __future__ import annotations

import threading
import time
from contextlib import contextmanager


class Timings:
    """Acumula segundos por (cena, etapa). Seguro para cenas em paralelo."""

    def __init__(self) -> None:
        self.data: dict[str, dict[str, float]] = {}
        self.counts: dict[str, dict[str, int]] = {}
        self.lock = threading.Lock()
        self.started = time.perf_counter()

    @contextmanager
    def track(self, scene: str, stage: str):
        t = time.perf_counter()
        try:
            yield
        finally:
            self.add(scene, stage, time.perf_counter() - t)

    def add(self, scene: str, stage: str, seconds: float) -> None:
        with self.lock:
            d = self.data.setdefault(scene, {})
            d[stage] = d.get(stage, 0.0) + seconds
            c = self.counts.setdefault(scene, {})
            c[stage] = c.get(stage, 0) + 1

    def report(self, wall_seconds: float | None = None) -> dict:
        wall = wall_seconds if wall_seconds is not None else time.perf_counter() - self.started
        per_stage: dict[str, float] = {}
        for stages in self.data.values():
            for k, v in stages.items():
                per_stage[k] = per_stage.get(k, 0.0) + v
        scenes = [s for s in self.data if s != "_global"]
        slowest = sorted(per_stage.items(), key=lambda kv: kv[1], reverse=True)[:3]
        return {
            "wall_seconds": round(wall, 2),
            "scenes": len(scenes),
            "avg_per_scene_seconds": round(sum(sum(self.data[s].values()) for s in scenes) / max(1, len(scenes)), 2),
            "stage_totals_seconds": {k: round(v, 2) for k, v in sorted(per_stage.items(), key=lambda kv: -kv[1])},
            "slowest_stages": [{"stage": k, "seconds": round(v, 2)} for k, v in slowest],
            "per_scene": {s: {k: round(v, 2) for k, v in d.items()} for s, d in sorted(self.data.items())},
            "calls": self.counts,
            "note": "tempos por etapa somam o trabalho das cenas em paralelo; wall_seconds é o tempo real",
        }
