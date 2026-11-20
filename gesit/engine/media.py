"""Jobs for streaming media: HLS playlists (ffmpeg) and site videos (yt-dlp).

They expose the same surface as ``Downloader`` so the manager can treat all
jobs alike: ``run()``, ``stop()``, status/total/downloaded/speed/filename...
"""

from __future__ import annotations

import asyncio
import datetime
import os
import re
import shutil
import tempfile
import time
import urllib.parse
from typing import Callable

import httpx

from ..core import osutil, util
from .downloader import (
    COMPLETE, CONNECTING, DOWNLOADING, ERROR, IDLE, PAUSED,
    DownloadError, Request, build_headers, make_client,
)
from .limiter import SpeedLimiter


class _ProcessJob:
    resumable = False

    def __init__(self, req: Request, limiter: SpeedLimiter | None = None,
                 global_limiter: SpeedLimiter | None = None,
                 on_change: Callable[[], None] | None = None, on_probe=None):
        self.req = req
        self.on_change = on_change
        self.status = IDLE
        self.error: str | None = None
        self.total: int | None = None
        self.downloaded = 0
        self.speed = 0.0
        self.segments: list = []
        self.filename = req.filename or ""
        self.final_path: str | None = None
        self.probe_info = None
        self._proc: asyncio.subprocess.Process | None = None
        self._stopping = False
        self._last = (time.monotonic(), 0)

    def _set(self, st: str) -> None:
        self.status = st
        if self.on_change:
            self.on_change()

    def _update_progress(self, downloaded: int, total: int | None = None, speed: float | None = None) -> None:
        now = time.monotonic()
        self.downloaded = downloaded
        if total:
            self.total = total
        if speed is None:
            t0, b0 = self._last
            if now - t0 >= 1.0:
                self.speed = max(0.0, (downloaded - b0) / (now - t0))
                self._last = (now, downloaded)
        else:
            self.speed = speed
        if self.on_change:
            self.on_change()

    def stop(self) -> None:
        self._stopping = True
        if self._proc and self._proc.returncode is None:
            try:
                self._proc.terminate()
            except ProcessLookupError:
                pass

    @property
    def eta(self) -> float | None:
        if not self.total or self.speed <= 0:
            return None
        return max(0.0, (self.total - self.downloaded) / self.speed)

    def connection_info(self):
        return []

    async def _reap(self) -> None:
        """Make sure the child process is gone before the event loop can close."""
        proc = self._proc
        if proc is None or proc.returncode is not None:
            return
        try:
            proc.terminate()
            await asyncio.wait_for(proc.wait(), 3)
        except (asyncio.TimeoutError, ProcessLookupError):
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            await proc.wait()

    async def _spawn(self, *argv: str) -> asyncio.subprocess.Process:
        kwargs = {"creationflags": osutil.NO_WINDOW} if osutil.WINDOWS else {}
        return await asyncio.create_subprocess_exec(
            *argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, **kwargs
        )


# ------------------------------------------------------------------------ HLS
def _parse_master(text: str, base: str) -> str | None:
    """Return the URL of the best variant if `text` is a master playlist."""
    best, best_bw, lines = None, -1, text.splitlines()
    for i, line in enumerate(lines):
        if line.startswith("#EXT-X-STREAM-INF"):
            m = re.search(r"BANDWIDTH=(\d+)", line)
            bw = int(m.group(1)) if m else 0
            for nxt in lines[i + 1 :]:
                if nxt and not nxt.startswith("#"):
                    if bw > best_bw:
                        best, best_bw = urllib.parse.urljoin(base, nxt.strip()), bw
                    break
    return best


def _playlist_duration(text: str) -> float:
    return sum(float(m) for m in re.findall(r"#EXTINF:([\d.]+)", text))


class HLSJob(_ProcessJob):
    async def run(self) -> None:
        self._stopping = False
        self._set(CONNECTING)
        out_dir = self.req.save_dir
        try:
            ffmpeg = osutil.tool_path("ffmpeg")
            if not ffmpeg:
                raise DownloadError("ffmpeg is not installed (Linux: your package manager; Windows: reinstall Gesit with the tools option)")
            os.makedirs(out_dir, exist_ok=True)
            url, duration = await self._resolve_playlist()
            name = self.filename or util.sanitize_filename(
                os.path.splitext(util.filename_from_url(self.req.url))[0] or "video"
            )
            if not os.path.splitext(name)[1] or os.path.splitext(name)[1].lower() == ".m3u8":
                name = os.path.splitext(name)[0] + ".mp4"
            self.filename = os.path.basename(util.unique_path(os.path.join(out_dir, name)))
            part = os.path.join(out_dir, self.filename + ".part")
            hdrs = "".join(f"{k}: {v}\r\n" for k, v in build_headers(self.req).items() if k != "Accept-Encoding")
            # no explicit "-bsf:a aac_adtstoasc": the mp4 muxer adds it for AAC by itself, and forcing it
            # makes ffmpeg fail ("Invalid argument") on streams whose audio is MP3 or AC3
            self._proc = await self._spawn(
                ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-nostats", "-progress", "pipe:1",
                "-headers", hdrs, "-i", url, "-c", "copy", "-f", "mp4", part,
            )
            self._set(DOWNLOADING)
            err_task = asyncio.create_task(self._proc.stderr.read())
            async for raw in self._proc.stdout:
                line = raw.decode("utf-8", "ignore").strip()
                if line.startswith("total_size="):
                    size = int(line.split("=")[1] or 0)
                    self._cur_size = size
                elif line.startswith("out_time_us=") or line.startswith("out_time_ms="):
                    v = line.split("=")[1]
                    if v.lstrip("-").isdigit() and int(v) > 0 and duration:
                        frac = min(0.999, int(v) / 1e6 / duration)
                        size = getattr(self, "_cur_size", 0)
                        self._update_progress(size, int(size / frac) if size else None)
            rc = await self._proc.wait()
            err = (await err_task).decode("utf-8", "ignore").strip()
            if self._stopping:
                self._cleanup(part)
                self.speed = 0.0
                self._set(PAUSED)
                return
            if rc != 0:
                self._cleanup(part)
                raise DownloadError(f"ffmpeg failed: {err.splitlines()[-1] if err else rc}")
            dest = os.path.join(out_dir, self.filename)
            os.replace(part, dest)
            self.final_path = dest
            self.total = self.downloaded = os.path.getsize(dest)
            self.speed = 0.0
            self._set(COMPLETE)
        except asyncio.CancelledError:
            self.stop()
            await self._reap()
            raise
        except DownloadError as e:
            self.error, self.speed = str(e), 0.0
            self._set(ERROR)
        except Exception as e:  # noqa: BLE001
            self.error, self.speed = f"{type(e).__name__}: {e}", 0.0
            self._set(ERROR)

    @staticmethod
    def _cleanup(part: str) -> None:
        try:
            os.remove(part)
        except OSError:
            pass

    async def _resolve_playlist(self) -> tuple[str, float]:
        async with make_client(self.req, 2) as client:
            url = self.req.url
            for _ in range(3):
                try:
                    r = await client.get(url, headers=build_headers(self.req))
                except httpx.HTTPError as e:
                    raise DownloadError(f"Cannot fetch playlist: {e}") from e
                if r.status_code >= 400:
                    raise DownloadError(f"HTTP {r.status_code} while fetching playlist")
                variant = _parse_master(r.text, str(r.url))
                if not variant:
                    return str(r.url), _playlist_duration(r.text)
                url = variant
        return url, 0.0


# --------------------------------------------------------------------- yt-dlp
# line markers printed by yt-dlp (see the --print / --progress-template arguments below)
_TAG_NAME, _TAG_FILE, _TAG_PROGRESS, _TAG_SIZE = "GESITN ", "GESITF ", "GESITP ", "GESITS "


def _mp4_args(have_ffmpeg: bool) -> list[str]:
    """Format selection so the result is an .mp4 whenever possible.

    Without arguments yt-dlp picks VP9 + Opus on YouTube and writes .webm, and sorting by container
    alone picks AV1 (mp4, but many players and devices cannot play it). So: H.264 first, then the best
    resolution available in H.264 (1080p on YouTube), AAC audio, and ffmpeg merges or remuxes whatever
    is left into an mp4 container. Sites without H.264 still give their best resolution, as mp4.
    Without ffmpeg nothing can be merged, so ask for a single ready-made mp4 file instead.
    """
    if not have_ffmpeg:
        return ["-f", "b[ext=mp4]/b"]
    # "bv+ba/b": separate video + audio streams first. The pre-merged formats (YouTube itag 18) are
    # low resolution and YouTube answers 403 for them without a PO token.
    return ["-f", "bv+ba/b", "-S", "vcodec:h264,res,acodec:m4a", "--merge-output-format", "mp4",
            "--remux-video", "mp4"]


STALE_DAYS = 45


def _version_age_days(version: str, today: datetime.date | None = None) -> int | None:
    """yt-dlp versions are dates ('2026.07.04'); None when the text is not one."""
    m = re.match(r"^(\d{4})\.(\d{2})\.(\d{2})", version.strip())
    if not m:
        return None
    try:
        released = datetime.date(int(m[1]), int(m[2]), int(m[3]))
    except ValueError:
        return None
    return ((today or datetime.date.today()) - released).days


def _is_youtube(url: str) -> bool:
    host = (urllib.parse.urlsplit(url).hostname or "").lower()
    return any(host == d or host.endswith("." + d) for d in ("youtube.com", "youtu.be", "youtube-nocookie.com"))


def _write_cookie_file(cookies: str, url: str) -> str | None:
    """Turn the browser's "a=1; b=2" Cookie header into a Netscape cookie file for yt-dlp.

    yt-dlp wants real cookies in its jar: logged-in YouTube cookies passed as a plain header make it
    fail with "The page needs to be reloaded", and newer versions flag the header as deprecated.
    The cookies are scoped to the host they came from, never wider.
    """
    host = (urllib.parse.urlsplit(url).hostname or "").lower()
    if not host or not cookies:
        return None
    domain = "." + (host[4:] if host.startswith("www.") else host)
    secure = "TRUE" if url.lower().startswith("https:") else "FALSE"
    expiry = int(time.time()) + 86400
    lines = ["# Netscape HTTP Cookie File"]
    for pair in cookies.split(";"):
        name, sep, value = pair.strip().partition("=")
        if not sep or not name or re.search(r"[\s\x00-\x1f]", name) or re.search(r"[\x00-\x1f]", value):
            continue
        lines.append(f"{domain}\tTRUE\t/\t{secure}\t{expiry}\t{name}\t{value}")
    if len(lines) == 1:
        return None
    fd, path = tempfile.mkstemp(prefix="gesit-cookies-", suffix=".txt")
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:  # mkstemp creates it 0600
        f.write("\n".join(lines) + "\n")
    return path


class YtdlpJob(_ProcessJob):
    async def run(self) -> None:
        self._stopping = False
        self._set(CONNECTING)
        exe = osutil.tool_path("yt-dlp")
        cookie_file = None
        try:
            if not exe:
                raise DownloadError("yt-dlp is not installed (Linux: your package manager; Windows: reinstall Gesit with the tools option)")
            os.makedirs(self.req.save_dir, exist_ok=True)
            cookie_file = _write_cookie_file(self.req.cookies, self.req.url)
            try:
                final = await self._run_attempts(exe, cookie_file)
            except DownloadError as e:
                hint = await self._stale_hint(exe)
                raise DownloadError(f"{e}. {hint}" if hint else str(e)) from e
            if final is None:  # stopped by the user
                self.speed = 0.0
                self._set(PAUSED)
                return
            self.final_path = final
            self.filename = os.path.basename(final)
            self.req.save_dir = os.path.dirname(final)
            self.total = self.downloaded = os.path.getsize(final)
            self.speed = 0.0
            self._set(COMPLETE)
        except asyncio.CancelledError:
            self.stop()
            await self._reap()
            raise
        except DownloadError as e:
            self.error, self.speed = str(e), 0.0
            self._set(ERROR)
        except Exception as e:  # noqa: BLE001
            self.error, self.speed = f"{type(e).__name__}: {e}", 0.0
            self._set(ERROR)
        finally:
            if cookie_file:
                try:
                    os.remove(cookie_file)
                except OSError:
                    pass

    async def _stale_hint(self, exe: str) -> str:
        """Old yt-dlp is the usual reason YouTube answers 403 / 'page needs to be reloaded': say so."""
        try:
            proc = await asyncio.create_subprocess_exec(
                exe, "--version", stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
                **({"creationflags": osutil.NO_WINDOW} if osutil.WINDOWS else {}),
            )
            out, _ = await asyncio.wait_for(proc.communicate(), 10)
        except (OSError, asyncio.TimeoutError):
            return ""
        version = out.decode("utf-8", "ignore").strip()
        age = _version_age_days(version)
        if age is None or age < STALE_DAYS:
            return ""
        how = ("run 'yt-dlp -U' in the tools folder next to gesit.exe" if osutil.WINDOWS else
               f"pip install -U yt-dlp, your package manager, or put the latest yt-dlp in {osutil.user_tools_dir()}")
        return f"Your yt-dlp ({version}) is {age} days old and sites keep changing, which is the usual cause. Update it: {how}"

    async def _run_attempts(self, exe: str, cookie_file: str | None) -> str | None:
        """Cookies are a help, not a requirement, so a failed login must not sink a public video.

        Most sites: with the browser's cookies first (many downloads need the login), then without.
        YouTube: without first. Public videos work that way, whereas a logged-in session that YouTube
        has rotated makes yt-dlp stop with "The page needs to be reloaded"; the cookies are only used
        when the video turns out to need them (age limit, private, members).
        """
        order = [cookie_file, None] if cookie_file else [None]
        if cookie_file and _is_youtube(self.req.url):
            order.reverse()
        failures: list[tuple[bool, DownloadError]] = []  # (used cookies, error)
        for cf in order:
            try:
                return await self._attempt(exe, cf)
            except DownloadError as e:
                failures.append((cf is not None, e))
                if self._stopping:
                    raise
                if len(failures) < len(order):
                    self.downloaded, self.total, self.speed = 0, None, 0.0
                    self._set(CONNECTING)
        if len(failures) == 1:
            raise failures[0][1]
        # report the answer the site gave without our login: it says what is really wrong with the video
        plain = next(e for used, e in failures if not used)
        raise DownloadError(f"{plain} (also failed with your browser cookies)")

    async def _attempt(self, exe: str, cookie_file: str | None) -> str | None:
        """One yt-dlp run. Returns the final path, None when stopped, raises DownloadError on failure."""
        argv = [
            # --progress: --print puts yt-dlp in quiet mode, and quiet mode switches the progress lines off
            exe, "--no-playlist", "--newline", "--no-colors", "--no-simulate", "--progress",
            "-o", os.path.join(self.req.save_dir, "%(title).150B.%(ext)s"),
            "--progress-template",
            f"download:{_TAG_PROGRESS}%(progress.downloaded_bytes)s %(progress.total_bytes)s "
            "%(progress.total_bytes_estimate)s %(progress.speed)s",
            "--print", f"before_dl:{_TAG_NAME}%(title).150B.%(ext)s",
            # whole size of the download: for video + audio yt-dlp reports the sum in filesize_approx
            "--print", f"before_dl:{_TAG_SIZE}%(filesize,filesize_approx)s",
            "--print", f"after_move:{_TAG_FILE}%(filepath)s",
        ]
        ffmpeg = osutil.tool_path("ffmpeg")
        argv += _mp4_args(bool(ffmpeg))
        if ffmpeg:  # yt-dlp needs ffmpeg to merge video and audio streams
            argv += ["--ffmpeg-location", os.path.dirname(ffmpeg)]
        if self.req.proxy:
            argv += ["--proxy", self.req.proxy]
        if self.req.referer:
            argv += ["--referer", self.req.referer]
        if self.req.user_agent:
            argv += ["--user-agent", self.req.user_agent]
        if cookie_file:
            argv += ["--cookies", cookie_file]
        argv.append(self.req.url)
        self._proc = await self._spawn(*argv)
        err_task = asyncio.create_task(self._proc.stderr.read())
        self._set(DOWNLOADING)
        final = None
        expected: int | None = None   # size of everything yt-dlp is going to fetch (video + audio)
        base = 0                      # bytes of streams that are already finished
        last_done, last_tot = 0, None
        async for raw in self._proc.stdout:
            line = raw.decode("utf-8", "ignore").rstrip()
            if line.startswith(_TAG_NAME):
                self.filename = util.sanitize_filename(line[len(_TAG_NAME):])
            elif line.startswith(_TAG_SIZE):
                v = line[len(_TAG_SIZE):].strip()
                expected = int(float(v)) if re.fullmatch(r"\d+(\.\d+)?", v) else None
            elif line.startswith(_TAG_FILE):
                final = line[len(_TAG_FILE):]
            elif line.startswith(_TAG_PROGRESS):
                parts = line.split()[1:]
                nums = [float(p) if re.fullmatch(r"[\d.]+", p) else None for p in parts] + [None] * 4
                done, tot, est, spd = nums[:4]
                done, stream_tot = int(done or 0), int(tot or est or 0) or None
                if done < last_done and stream_tot != last_tot:   # video finished, the audio stream began
                    base += last_done
                last_done, last_tot = done, stream_tot
                total = max(expected or 0, base + (stream_tot or 0)) or None
                self._update_progress(base + done, total, spd or 0.0)
        rc = await self._proc.wait()
        err = (await err_task).decode("utf-8", "ignore").strip()
        if self._stopping:
            return None
        if rc != 0 or not final:
            last = [ln for ln in err.splitlines() if ln.strip() and not ln.startswith("WARNING")]
            raise DownloadError(f"yt-dlp failed: {last[-1] if last else rc}")
        return final
