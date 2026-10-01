"""Token bucket global: 5 gerações/min compartilhadas por todas as produções (§8.1)."""
from __future__ import annotations

import threading
import time


class TokenBucket:
    def __init__(self, capacity: int, per_seconds: float):
        self.capacity = capacity
        self.rate = capacity / per_seconds
        self.tokens = float(capacity)
        self.updated = time.monotonic()
        self.lock = threading.Lock()

    def acquire(self) -> None:
        while True:
            with self.lock:
                t = time.monotonic()
                self.tokens = min(self.capacity, self.tokens + (t - self.updated) * self.rate)
                self.updated = t
                if self.tokens >= 1:
                    self.tokens -= 1
                    return
                wait = (1 - self.tokens) / self.rate
            time.sleep(wait)


IMAGE_BUCKET = TokenBucket(5, 60.0)
