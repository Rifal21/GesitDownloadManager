"""Bandwidth limiter shared by all workers of a download (and globally)."""

from __future__ import annotations

import asyncio
import time


class SpeedLimiter:
    """Virtual-clock limiter: each acquire() reserves n/rate seconds of the pipe."""

    BURST = 0.25  # seconds of traffic allowed to go out ahead of the clock

    def __init__(self, rate: int = 0):
        self._rate = max(0, int(rate))
        self._next = 0.0

    @property
    def rate(self) -> int:
        return self._rate

    def set_rate(self, rate: int) -> None:
        self._rate = max(0, int(rate))
        self._next = 0.0

    async def acquire(self, n: int) -> None:
        if self._rate <= 0:
            return
        now = time.monotonic()
        self._next = max(self._next, now) + n / self._rate
        wait = self._next - now - self.BURST
        if wait > 0:
            await asyncio.sleep(wait)
