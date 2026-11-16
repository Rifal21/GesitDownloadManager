"""Multi-connection HTTP download engine with dynamic segmentation and resume.

Design notes
------------
* The file is split into segments, each owned by one worker (one TCP connection).
* When a worker finishes, it steals half of the largest remaining segment
  (the owner notices the shrunken ``end`` on its next chunk), so all connections
  stay busy until the very end. This is dynamic segmentation, the technique
  download accelerators use.
* Segment progress is persisted to ``<file>.gesit`` so a stopped download can be
  resumed byte-exact as long as the server still reports the same validators.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from dataclasses import dataclass, field
from typing import Callable

import httpx

from ..core import util
from .limiter import SpeedLimiter

_HAS_PWRITE = hasattr(os, "pwrite")  # missing on Windows


def _pwrite(fd: int, data: bytes, offset: int) -> None:
    """Write at an absolute offset. The fallback (seek + write) is safe because all workers run on one
    event-loop thread and never await between the seek and the write."""
    if _HAS_PWRITE:
        os.pwrite(fd, data, offset)
    else:
        os.lseek(fd, offset, os.SEEK_SET)
        os.write(fd, data)


MIN_SEGMENT = 256 * 1024
CHUNK = 64 * 1024
DEFAULT_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/130.0 Safari/537.36"
)

IDLE, CONNECTING, DOWNLOADING, PAUSED, COMPLETE, ERROR = (
    "idle", "connecting", "downloading", "paused", "complete", "error",
)


class DownloadError(Exception):
    def __init__(self, message: str, fatal: bool = True):
        super().__init__(message)
        self.fatal = fatal


class RangeIgnored(Exception):
    pass


@dataclass
class Segment:
    start: int
    pos: int
    end: int  # exclusive
    active: bool = False
    retries: int = 0

    @property
    def remaining(self) -> int:
        return max(0, self.end - self.pos)

    @property
    def done(self) -> bool:
        return self.pos >= self.end


@dataclass
class Request:
    url: str
    save_dir: str
    filename: str | None = None
    headers: dict[str, str] = field(default_factory=dict)
    cookies: str | None = None
    referer: str | None = None
    user_agent: str | None = None
    auth: tuple[str, str] | None = None
    proxy: str | None = None
    connections: int = 8
    retries: int = 5
    timeout: float = 30.0
    verify_tls: bool = True
    overwrite: bool = False


@dataclass
class Probe:
    final_url: str
    total: int | None
    resumable: bool
    filename: str
    etag: str | None
    last_modified: str | None
    content_type: str | None


def build_headers(req: Request) -> dict[str, str]:
    h = {
        "User-Agent": req.user_agent or DEFAULT_UA,
        "Accept": "*/*",
        "Accept-Encoding": "identity",
    }
    if req.referer:
        h["Referer"] = req.referer
    if req.cookies:
        h["Cookie"] = req.cookies
    for k, v in (req.headers or {}).items():
        if k.lower() in ("range", "host", "content-length", "accept-encoding"):
            continue
        h[k] = v
    return h


def _strip_credentials_hook(origin_host: str):
    async def hook(request: httpx.Request) -> None:
        if request.url.host != origin_host:
            request.headers.pop("cookie", None)
            request.headers.pop("authorization", None)

    return hook


def make_client(req: Request, max_conn: int) -> httpx.AsyncClient:
    kwargs = dict(
        event_hooks={"request": [_strip_credentials_hook(util.host_of(req.url))]},
        follow_redirects=True,
        timeout=httpx.Timeout(req.timeout, connect=req.timeout),
        limits=httpx.Limits(max_connections=max_conn + 2, max_keepalive_connections=0),
        verify=req.verify_tls,
        auth=httpx.BasicAuth(*req.auth) if req.auth else None,
    )
    if req.proxy:
        kwargs["proxy"] = req.proxy
    return httpx.AsyncClient(**kwargs)


def _probe_from_response(r: httpx.Response, req: Request) -> Probe:
    total = None
    resumable = False
    cr = r.headers.get("content-range")
    if r.status_code == 206 and cr and "/" in cr:
        tail = cr.rsplit("/", 1)[1].strip()
        if tail.isdigit():
            total = int(tail)
            resumable = True
    elif r.status_code == 416 and cr and "/" in cr:
        tail = cr.rsplit("/", 1)[1].strip()
        if tail.isdigit():
            total = int(tail)
    else:
        cl = r.headers.get("content-length")
        if cl and cl.isdigit() and "content-encoding" not in r.headers:
            total = int(cl)
        resumable = False

    name = req.filename or util.parse_content_disposition(r.headers.get("content-disposition"))
    if not name:
        name = util.filename_from_url(str(r.url)) or util.filename_from_url(req.url)
    name = util.ensure_extension(util.sanitize_filename(name), r.headers.get("content-type"))
    return Probe(
        final_url=str(r.url),
        total=total,
        resumable=resumable and total is not None,
        filename=name,
        etag=r.headers.get("etag"),
        last_modified=r.headers.get("last-modified"),
        content_type=r.headers.get("content-type"),
    )


async def probe(client: httpx.AsyncClient, req: Request) -> Probe:
    headers = build_headers(req)
    last_exc: Exception | None = None
    # 1) ranged GET: one request tells us size, range support, name, validators
    try:
        async with client.stream("GET", req.url, headers={**headers, "Range": "bytes=0-0"}) as r:
            if r.status_code in (200, 206, 416):
                return _probe_from_response(r, req)
            if r.status_code in (401, 407):
                raise DownloadError(f"HTTP {r.status_code}: authorization required")
            last_exc = DownloadError(f"HTTP {r.status_code} {r.reason_phrase}")
    except httpx.HTTPError as e:
        last_exc = e
    # 2) HEAD fallback for servers that dislike ranged GET
    try:
        r = await client.head(req.url, headers=headers)
        if r.status_code < 400:
            return _probe_from_response(r, req)
        last_exc = DownloadError(f"HTTP {r.status_code} {r.reason_phrase}")
    except httpx.HTTPError as e:
        last_exc = last_exc or e
    if isinstance(last_exc, DownloadError):
        raise last_exc
    raise DownloadError(f"Cannot connect: {last_exc}", fatal=False)


class Downloader:
    """One download job. Create, ``await run()``, call ``stop()`` from anywhere."""

    def __init__(
        self,
        req: Request,
        limiter: SpeedLimiter | None = None,
        global_limiter: SpeedLimiter | None = None,
        on_change: Callable[[], None] | None = None,
        on_probe: Callable[["Probe", Request], None] | None = None,
    ):
        self.req = req
        self.on_probe = on_probe
        self.limiter = limiter or SpeedLimiter(0)
        self.global_limiter = global_limiter or SpeedLimiter(0)
        self.on_change = on_change
        self.status = IDLE
        self.error: str | None = None
        self.total: int | None = None
        self.downloaded = 0
        self.speed = 0.0
        self.resumable = False
        self.segments: list[Segment] = []
        self.filename = req.filename or ""
        self.final_path: str | None = None
        self.max_connections = max(1, min(32, req.connections))
        self.probe_info: Probe | None = None

        self._main: asyncio.Task | None = None
        self._workers: set[asyncio.Task] = set()
        self._stopping = False
        self._fd: int | None = None
        self._samples: list[tuple[float, int]] = []
        self._client: httpx.AsyncClient | None = None
        self._fatal: DownloadError | None = None

    # ------------------------------------------------------------------ paths
    @property
    def part_path(self) -> str:
        return os.path.join(self.req.save_dir, self.filename + ".part")

    @property
    def state_path(self) -> str:
        return os.path.join(self.req.save_dir, self.filename + ".gesit")

    # --------------------------------------------------------------- lifecycle
    def stop(self) -> None:
        """Pause: cancel workers, keep partial data and state for resume."""
        self._stopping = True
        if self._main:
            self._main.cancel()  # cancelling gather() also cancels its workers

    def _set_status(self, status: str) -> None:
        self.status = status
        if self.on_change:
            self.on_change()

    async def run(self) -> None:
        self._stopping = False
        self._fatal = None
        self.error = None
        self._set_status(CONNECTING)
        self._main = asyncio.current_task()
        try:
            os.makedirs(self.req.save_dir, exist_ok=True)
            self._client = make_client(self.req, self.max_connections)
            info = await self._probe_with_retry()
            self.probe_info = info
            if self.on_probe:
                self.on_probe(info, self.req)  # lets the manager pick the category folder
                os.makedirs(self.req.save_dir, exist_ok=True)
            self.total = info.total
            self.resumable = info.resumable
            if not self.filename:
                self.filename = self._pick_filename(info.filename)
            legacy = os.path.join(self.req.save_dir, self.filename + ".lidm")  # partial from the pre-rename app
            if os.path.exists(legacy) and not os.path.exists(self.state_path):
                os.replace(legacy, self.state_path)
            self._load_or_init_segments(info)
            self._open_file()
            self._set_status(DOWNLOADING)
            ticker = asyncio.create_task(self._ticker())
            try:
                if self.resumable and len(self.segments) >= 1:
                    await self._run_segmented()
                else:
                    await self._run_single()
            finally:
                ticker.cancel()
            if self._fatal:
                raise self._fatal
            self._finish()
        except asyncio.CancelledError:
            if self._stopping:
                self.speed = 0.0
                self._save_state()
                self._set_status(PAUSED)
            else:
                raise
        except DownloadError as e:
            self.error = str(e)
            self.speed = 0.0
            self._save_state()
            self._set_status(ERROR)
        except Exception as e:  # noqa: BLE001 - surface anything to the UI
            self.error = f"{type(e).__name__}: {e}"
            self.speed = 0.0
            self._save_state()
            self._set_status(ERROR)
        finally:
            self._close_file()
            if self._client:
                await self._client.aclose()
                self._client = None
            self._main = None

    def _pick_filename(self, name: str) -> str:
        """Auto-named downloads must not clobber another file or partial download."""
        if self._has_resume_state(name):
            return name
        stem, ext = os.path.splitext(name)
        cand, i = name, 0
        d = self.req.save_dir
        while (os.path.exists(os.path.join(d, cand)) and not self.req.overwrite) or os.path.exists(
            os.path.join(d, cand + ".part")
        ):
            i += 1
            cand = f"{stem} ({i}){ext}"
        return cand

    # ------------------------------------------------------------------ probe
    async def _probe_with_retry(self) -> Probe:
        delay = 1.0
        for attempt in range(self.req.retries + 1):
            try:
                return await probe(self._client, self.req)  # type: ignore[arg-type]
            except DownloadError as e:
                if e.fatal or attempt >= self.req.retries:
                    raise
            await asyncio.sleep(delay)
            delay = min(delay * 2, 15)
        raise DownloadError("Cannot connect")

    # ---------------------------------------------------------------- segments
    def _has_resume_state(self, name: str) -> bool:
        d = self.req.save_dir
        has_state = os.path.exists(os.path.join(d, name + ".gesit")) or os.path.exists(os.path.join(d, name + ".lidm"))
        return has_state and os.path.exists(os.path.join(d, name + ".part"))

    def _load_or_init_segments(self, info: Probe) -> None:
        self.segments = []
        self.downloaded = 0
        if os.path.exists(self.state_path) and os.path.exists(self.part_path):
            try:
                with open(self.state_path) as f:
                    st = json.load(f)
                same = (
                    st.get("total") == info.total
                    and st.get("etag") == info.etag
                    and st.get("last_modified") == info.last_modified
                )
                if same and info.resumable:
                    self.segments = [Segment(s, p, e) for s, p, e in st["segments"]]
                    self.downloaded = sum(s.pos - s.start for s in self.segments)
                    return
            except (OSError, ValueError, KeyError):
                pass
            for p in (self.state_path, self.part_path):
                try:
                    os.remove(p)
                except OSError:
                    pass
        if info.resumable and info.total:
            n = max(1, min(self.max_connections, info.total // MIN_SEGMENT))
            size = info.total // n
            for i in range(n):
                start = i * size
                end = info.total if i == n - 1 else (i + 1) * size
                self.segments.append(Segment(start, start, end))
        else:
            self.segments = [Segment(0, 0, info.total if info.total is not None else 1 << 62)]

    def _save_state(self) -> None:
        if not self.filename or not self.probe_info or not self.resumable:
            return
        st = {
            "url": self.req.url,
            "total": self.total,
            "etag": self.probe_info.etag,
            "last_modified": self.probe_info.last_modified,
            "segments": [[s.start, s.pos, s.end] for s in self.segments],
        }
        tmp = self.state_path + ".tmp"
        try:
            with open(tmp, "w") as f:
                json.dump(st, f)
            os.replace(tmp, self.state_path)
        except OSError:
            pass

    def _open_file(self) -> None:
        flags = os.O_RDWR | os.O_CREAT
        self._fd = os.open(self.part_path, flags, 0o644)
        if self.total and os.fstat(self._fd).st_size != self.total:
            try:
                os.posix_fallocate(self._fd, 0, self.total)
            except (OSError, AttributeError):
                os.ftruncate(self._fd, self.total)

    def _close_file(self) -> None:
        if self._fd is not None:
            try:
                os.close(self._fd)
            except OSError:
                pass
            self._fd = None

    def _next_segment(self) -> Segment | None:
        for s in self.segments:
            if not s.active and not s.done:
                s.active = True
                return s
        # steal: split the largest remaining active segment in two
        best = None
        for s in self.segments:
            if s.active and s.remaining > 2 * MIN_SEGMENT and (best is None or s.remaining > best.remaining):
                best = s
        if best is None:
            return None
        mid = best.pos + best.remaining // 2
        new = Segment(mid, mid, best.end, active=True)
        best.end = mid
        self.segments.append(new)
        return new

    async def _run_segmented(self) -> None:
        count = min(self.max_connections, max(1, self._unfinished_count()))
        self._workers = {asyncio.create_task(self._worker()) for _ in range(count)}
        try:
            results = await asyncio.gather(*self._workers, return_exceptions=True)
        finally:
            for w in self._workers:
                w.cancel()
        if not self._fatal:
            for r in results:
                if isinstance(r, Exception):
                    self._fatal = DownloadError(f"{type(r).__name__}: {r}")
                    break

    def _unfinished_count(self) -> int:
        return sum(1 for s in self.segments if not s.done)

    async def _worker(self) -> None:
        while not self._stopping and not self._fatal:
            seg = self._next_segment()
            if seg is None:
                return
            try:
                await self._download_segment(seg)
            finally:
                seg.active = False

    async def _download_segment(self, seg: Segment) -> None:
        delay = 1.0
        while not seg.done and not self._stopping and not self._fatal:
            before = seg.pos
            try:
                await self._fetch_range(seg)
            except asyncio.CancelledError:
                raise
            except RangeIgnored:
                self._fatal = DownloadError("Server stopped honouring Range requests")
                self.stop_workers_soft()
                return
            except DownloadError as e:
                if e.fatal:
                    self._fatal = e
                    self.stop_workers_soft()
                    return
                seg.retries += 1
            except (httpx.HTTPError, OSError) as e:
                seg.retries += 1
                if seg.retries > self.req.retries:
                    self._fatal = DownloadError(f"Connection failed: {e}")
                    self.stop_workers_soft()
                    return
            else:
                if seg.done:
                    return
                # stream ended early without error: treat as retryable
                seg.retries += 1
            if seg.pos > before:
                seg.retries = 0
                delay = 1.0
            if seg.retries > self.req.retries:
                self._fatal = DownloadError("Too many retries (connection keeps dropping)")
                self.stop_workers_soft()
                return
            await asyncio.sleep(delay)
            delay = min(delay * 2, 15)

    def stop_workers_soft(self) -> None:
        """Cancel the other workers after a fatal error (state stays resumable)."""
        cur = asyncio.current_task()
        for t in self._workers:
            if t is not cur:
                t.cancel()

    async def _fetch_range(self, seg: Segment) -> None:
        headers = build_headers(self.req)
        headers["Range"] = f"bytes={seg.pos}-{seg.end - 1}"
        async with self._client.stream("GET", self.probe_info.final_url, headers=headers) as r:  # type: ignore[union-attr]
            if r.status_code == 200:
                raise RangeIgnored()
            if r.status_code in (403, 404, 410):
                raise DownloadError(f"HTTP {r.status_code} {r.reason_phrase} (link may have expired)")
            if r.status_code in (401, 407):
                raise DownloadError(f"HTTP {r.status_code}: authorization required")
            if r.status_code >= 500 or r.status_code == 429:
                raise DownloadError(f"HTTP {r.status_code}", fatal=False)
            if r.status_code != 206:
                raise DownloadError(f"Unexpected HTTP {r.status_code}")
            cr = r.headers.get("content-range", "")
            if cr.startswith("bytes ") and int(cr[6:].split("-")[0]) != seg.pos:
                raise DownloadError("Server returned the wrong byte range", fatal=False)
            async for chunk in r.aiter_raw(CHUNK):
                await self.limiter.acquire(len(chunk))
                await self.global_limiter.acquire(len(chunk))
                n = min(len(chunk), seg.end - seg.pos)
                if n <= 0:
                    break
                _pwrite(self._fd, chunk if n == len(chunk) else chunk[:n], seg.pos)  # type: ignore[arg-type]
                seg.pos += n
                self.downloaded += n
                if seg.pos >= seg.end:
                    break

    # ------------------------------------------------------- single connection
    async def _run_single(self) -> None:
        seg = self.segments[0]
        seg.start = seg.pos = 0
        self.downloaded = 0
        self.segments = [seg]
        seg.active = True
        delay = 1.0
        attempts = 0
        while True:
            seg.pos = 0
            self.downloaded = 0
            try:
                async with self._client.stream(  # type: ignore[union-attr]
                    "GET", self.probe_info.final_url, headers=build_headers(self.req)  # type: ignore[union-attr]
                ) as r:
                    if r.status_code in (401, 407):
                        raise DownloadError(f"HTTP {r.status_code}: authorization required")
                    if r.status_code >= 400:
                        raise DownloadError(
                            f"HTTP {r.status_code} {r.reason_phrase}", fatal=r.status_code < 500 and r.status_code != 429
                        )
                    cl = r.headers.get("content-length")
                    if cl and cl.isdigit() and "content-encoding" not in r.headers:
                        self.total = int(cl)
                        seg.end = self.total
                    async for chunk in r.aiter_raw(CHUNK):
                        await self.limiter.acquire(len(chunk))
                        await self.global_limiter.acquire(len(chunk))
                        _pwrite(self._fd, chunk, seg.pos)  # type: ignore[arg-type]
                        seg.pos += len(chunk)
                        self.downloaded += len(chunk)
                if self.total is None:
                    self.total = self.downloaded
                    seg.end = self.downloaded
                return
            except asyncio.CancelledError:
                raise
            except DownloadError as e:
                if e.fatal:
                    raise
                attempts += 1
            except (httpx.HTTPError, OSError):
                attempts += 1
            if attempts > self.req.retries:
                raise DownloadError("Connection failed too many times")
            await asyncio.sleep(delay)
            delay = min(delay * 2, 15)

    # --------------------------------------------------------------- finishing
    def _finish(self) -> None:
        if self.total is not None and self.resumable and self.downloaded < self.total:
            raise DownloadError("Download ended before all data was received")
        if self.total is not None and not self.resumable and self.downloaded != self.total:
            raise DownloadError("Download ended before all data was received")
        self._close_file()
        dest = os.path.join(self.req.save_dir, self.filename)
        if os.path.exists(dest) and not self.req.overwrite:
            dest = util.unique_path(dest)
        os.replace(self.part_path, dest)
        try:
            os.remove(self.state_path)
        except OSError:
            pass
        self.final_path = dest
        self.filename = os.path.basename(dest)
        self.speed = 0.0
        self._set_status(COMPLETE)

    # ------------------------------------------------------------- bookkeeping
    async def _ticker(self) -> None:
        last_save = 0.0
        while True:
            await asyncio.sleep(0.5)
            now = time.monotonic()
            self._samples.append((now, self.downloaded))
            while len(self._samples) > 1 and now - self._samples[0][0] > 4.0:
                self._samples.pop(0)
            if len(self._samples) >= 2:
                dt = self._samples[-1][0] - self._samples[0][0]
                db = self._samples[-1][1] - self._samples[0][1]
                self.speed = db / dt if dt > 0 else 0.0
            if now - last_save >= 1.0:
                self._save_state()
                last_save = now
            if self.on_change:
                self.on_change()

    @property
    def eta(self) -> float | None:
        if not self.total or self.speed <= 0:
            return None
        return max(0.0, (self.total - self.downloaded) / self.speed)

    def connection_info(self) -> list[tuple[int, int, str]]:
        """(downloaded, remaining, state) per segment for the progress dialog."""
        out = []
        for s in self.segments:
            state = "Receiving data..." if s.active and not s.done else ("Complete" if s.done else "Idle")
            out.append((s.pos - s.start, s.remaining, state))
        return out
