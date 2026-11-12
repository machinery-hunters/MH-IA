"""
Límite de consultas por persona: ventana deslizante en memoria.

La API corre en un solo proceso (un uvicorn sin --workers), así que basta con memoria: un
reinicio vacía los contadores, lo cual es aceptable para un freno de abuso (no es una cuota
contable). Se cuentan los intentos admitidos, no los rechazados: quien insiste no alarga su
propia espera.
"""

from __future__ import annotations

import math
import threading
import time
from collections import deque
from collections.abc import Callable


class SlidingWindowLimiter:
    def __init__(
        self, limit: int, window_s: float, *, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self.limit = limit
        self.window_s = window_s
        self._clock = clock
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def acquire(self, key: str) -> int:
        """0 si se admite (y se cuenta); si no, segundos a esperar (>= 1)."""
        if self.limit <= 0:
            return 0
        now = self._clock()
        with self._lock:
            hits = self._hits.setdefault(key, deque())
            while hits and hits[0] <= now - self.window_s:
                hits.popleft()
            if len(hits) >= self.limit:
                return max(1, math.ceil(hits[0] + self.window_s - now))
            hits.append(now)
            self._prune(now)
            return 0

    def _prune(self, now: float) -> None:
        # Sin esto, cada persona que alguna vez consultó dejaría una entrada para siempre.
        if len(self._hits) <= 1024:
            return
        stale = [k for k, h in self._hits.items() if not h or h[-1] <= now - self.window_s]
        for k in stale:
            del self._hits[k]
