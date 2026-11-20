"""Internet speed test: latency, download and upload against Cloudflare's public test servers.

Runs only when the user asks for it. It downloads and uploads throw-away random data; nothing about the
user or their downloads is sent. The base URL can be replaced with GESIT_SPEEDTEST_URL (used by the tests).
"""

from __future__ import annotations

import asyncio
import os
import statistics
import time
from dataclasses import dataclass
from typing import Callable

import httpx

from .. import __version__

BASE_URL = "https://speed.cloudflare.com"
DOWN_BYTES = 25_000_000      # per request; the server refuses 100 MB and above
UP_BYTES = 8_000_000
WARMUP_S = 1.0               # TCP slow start would drag the average down
CHUNK = 64 * 1024

Progress = Callable[[str, float | None, float], None]  # phase, current Mbit/s (None for ping), 0..1


class SpeedTestError(Exception):
    pass


class Cancelled(Exception):
    pass


@dataclass
class Result:
    ping_ms: float
    jitter_ms: float
    download_bps: float
    upload_bps: float | None   # None when the upload step could not run
    note: str = ""             # why the upload is missing, if it is

    @property
    def download_mbps(self) -> float:
        return self.download_bps / 1e6

    @property
    def upload_mbps(self) -> float | None:
        return None if self.upload_bps is None else self.upload_bps / 1e6


def format_rate(bps: float | None) -> str:
    """'93.4 Mbit/s (11.7 MB/s)': line speed as ISPs quote it, plus what a download manager shows."""
    if bps is None:
        return "-"
    mbit = bps / 1e6
    if mbit >= 1000:
        return f"{mbit / 1000:.2f} Gbit/s ({bps / 8e6:.0f} MB/s)"
    return f"{mbit:.1f} Mbit/s ({bps / 8e6:.1f} MB/s)"


class SpeedTest:
    def __init__(self, proxy: str | None = None, base: str | None = None, on_progress: Progress | None = None,
                 down_seconds: float = 8.0, up_seconds: float = 6.0, streams: int = 4, pings: int = 8):
        self.proxy = proxy
        self.base = (base or os.environ.get("GESIT_SPEEDTEST_URL") or BASE_URL).rstrip("/")
        self.on_progress = on_progress or (lambda *_: None)
        self.down_seconds, self.up_seconds, self.streams, self.pings = down_seconds, up_seconds, streams, pings
        self._loop: asyncio.AbstractEventLoop | None = None
        self._task: asyncio.Task | None = None
        self._cancelled = False

    # ------------------------------------------------------------------ public
    def cancel(self) -> None:
        """Thread-safe: stops a running test; run() then raises Cancelled."""
        self._cancelled = True
        loop, task = self._loop, self._task
        if loop and task:
            loop.call_soon_threadsafe(task.cancel)

    def run(self) -> Result:
        """Blocking. Call from a worker thread. Raises SpeedTestError or Cancelled."""
        loop = asyncio.new_event_loop()
        self._loop = loop
        try:
            self._task = loop.create_task(self._run())
            if self._cancelled:
                self._task.cancel()
            try:
                return loop.run_until_complete(self._task)
            except asyncio.CancelledError:
                raise Cancelled() from None
        finally:
            self._task = None
            loop.run_until_complete(loop.shutdown_asyncgens())
            loop.close()

    # ---------------------------------------------------------------- internals
    def _client(self) -> httpx.AsyncClient:
        kwargs: dict = dict(
            timeout=httpx.Timeout(15.0, connect=10.0), follow_redirects=True,
            headers={"User-Agent": f"Gesit/{__version__}"},
            limits=httpx.Limits(max_connections=self.streams + 2, max_keepalive_connections=self.streams + 2),
        )
        if self.proxy:
            kwargs["proxy"] = self.proxy
        return httpx.AsyncClient(**kwargs)

    async def _run(self) -> Result:
        async with self._client() as client:
            try:
                ping, jitter = await self._latency(client)
                down = await self._download(client)
                up, note = None, ""
                try:
                    up = await self._upload(client)
                except SpeedTestError as e:  # a missing upload must not throw the download result away
                    note = str(e)
            except httpx.HTTPError as e:
                raise SpeedTestError(f"Could not reach the speed test server: {e}") from e
        self.on_progress("done", None, 1.0)
        return Result(ping, jitter, down, up, note)

    async def _latency(self, client: httpx.AsyncClient) -> tuple[float, float]:
        url = f"{self.base}/__down?bytes=0"
        samples: list[float] = []
        for i in range(self.pings + 1):
            t0 = time.perf_counter()
            r = await client.get(url)
            dt = (time.perf_counter() - t0) * 1000
            if r.status_code != 200:
                raise SpeedTestError(f"Speed test server answered HTTP {r.status_code}")
            if i:  # the first request pays for DNS and the TLS handshake
                samples.append(dt)
                self.on_progress("ping", None, i / self.pings)
        jitter = statistics.fmean(abs(a - b) for a, b in zip(samples, samples[1:])) if len(samples) > 1 else 0.0
        return statistics.median(samples), jitter

    async def _measure(self, phase: str, seconds: float, worker) -> float:
        """Run `streams` workers for `seconds`; return bits/s measured after the warm-up."""
        counter = [0]
        start = time.perf_counter()
        deadline = start + seconds
        warm = {"t": None, "bytes": 0}
        tasks = [asyncio.create_task(worker(counter, deadline)) for _ in range(self.streams)]
        window: list[tuple[float, int]] = []
        try:
            while time.perf_counter() < deadline:
                await asyncio.sleep(0.2)
                now = time.perf_counter()
                window.append((now, counter[0]))
                window = [w for w in window if now - w[0] <= 1.0]
                if warm["t"] is None and now - start >= WARMUP_S:
                    warm["t"], warm["bytes"] = now, counter[0]
                if len(window) > 1:
                    (t0, b0), (t1, b1) = window[0], window[-1]
                    self.on_progress(phase, (b1 - b0) * 8 / (t1 - t0) / 1e6, min(1.0, (now - start) / seconds))
            end = time.perf_counter()
        finally:
            for t in tasks:
                t.cancel()
            results = await asyncio.gather(*tasks, return_exceptions=True)
        errors = [r for r in results if isinstance(r, Exception) and not isinstance(r, asyncio.CancelledError)]
        if errors and len(errors) == len(results):
            raise errors[0]  # every stream failed (refused, unreachable): report why, not an empty measurement
        t_from, b_from = (warm["t"], warm["bytes"]) if warm["t"] is not None else (start, 0)
        span = end - t_from
        if span <= 0 or counter[0] <= b_from:
            raise SpeedTestError("No data was transferred during the test")
        return (counter[0] - b_from) * 8 / span

    async def _download(self, client: httpx.AsyncClient) -> float:
        url = f"{self.base}/__down?bytes={DOWN_BYTES}"

        async def worker(counter: list[int], deadline: float) -> None:
            while time.perf_counter() < deadline:
                async with client.stream("GET", url) as r:
                    if r.status_code != 200:
                        raise SpeedTestError(f"Speed test server answered HTTP {r.status_code}")
                    async for chunk in r.aiter_raw(CHUNK):
                        counter[0] += len(chunk)
                        if time.perf_counter() >= deadline:
                            return

        return await self._measure("download", self.down_seconds, worker)

    async def _upload(self, client: httpx.AsyncClient) -> float:
        url = f"{self.base}/__up"
        block = os.urandom(CHUNK)

        async def worker(counter: list[int], deadline: float) -> None:
            while time.perf_counter() < deadline:
                async def body():
                    sent = 0
                    while sent < UP_BYTES:
                        n = min(CHUNK, UP_BYTES - sent)
                        sent += n
                        counter[0] += n
                        yield block[:n]

                r = await client.post(url, content=body(), headers={
                    "Content-Type": "application/octet-stream", "Content-Length": str(UP_BYTES)})
                if r.status_code != 200:
                    raise SpeedTestError(f"Upload step refused by the server (HTTP {r.status_code})")

        try:
            return await self._measure("upload", self.up_seconds, worker)
        except httpx.HTTPError as e:
            raise SpeedTestError(f"Upload step failed: {e}") from e
