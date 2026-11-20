import functools
import http.server
import os
import shutil
import subprocess
import sys
import threading
import time

import pytest

from gesit.core.config import Config
from gesit.core.db import Database
from gesit.core.manager import Manager
from gesit.core.models import COMPLETE, ERROR

pytestmark = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")


def _make_hls(root, audio: str | None, duration: int = 6) -> None:
    src = ["-f", "lavfi", "-i", f"testsrc=duration={duration}:size=160x120:rate=15"]
    if audio:
        src += ["-f", "lavfi", "-i", f"sine=frequency=440:duration={duration}"]
    codec = ["-c:a", audio] if audio else ["-an"]
    subprocess.run(
        ["ffmpeg", "-loglevel", "error", *src, "-c:v", "libx264", *codec,
         "-f", "hls", "-hls_time", "2", "-hls_playlist_type", "vod", str(root / "index.m3u8")],
        check=True, timeout=60,
    )


@pytest.fixture()
def hls_server(tmp_path, request):
    root = tmp_path / "www"
    root.mkdir()
    _make_hls(root, getattr(request, "param", "aac"))
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(root))
    handler.log_message = lambda *a, **k: None
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()


# audio that is not AAC (MP3, AC3) used to make ffmpeg fail because of a forced aac_adtstoasc filter
@pytest.mark.parametrize("hls_server", ["aac", "libmp3lame", "ac3", None], indirect=True,
                         ids=["aac", "mp3", "ac3", "no-audio"])
def test_hls_download_produces_playable_mp4(hls_server, tmp_path):
    cfg = Config(download_root=str(tmp_path / "dl"), proxy_mode="none")
    m = Manager(cfg, Database(str(tmp_path / "db.sqlite")))
    m.start()
    try:
        it = m.add(hls_server + "/index.m3u8", filename="clip.mp4")
        end = time.time() + 40
        while time.time() < end and m.get(it.id).status not in (COMPLETE, ERROR):
            time.sleep(0.2)
        got = m.get(it.id)
        assert got.status == COMPLETE, got.error
        assert got.category == "Video" and got.kind == "hls"
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", got.path],
            capture_output=True, text=True,
        )
        assert 5 < float(probe.stdout.strip()) < 7
        assert not [f for f in os.listdir(got.save_dir) if f.endswith(".part")]
    finally:
        m.shutdown()


def test_hls_missing_playlist_reports_error(hls_server, tmp_path):
    cfg = Config(download_root=str(tmp_path / "dl"), proxy_mode="none")
    m = Manager(cfg, Database(str(tmp_path / "db.sqlite")))
    m.start()
    try:
        it = m.add(hls_server + "/nope.m3u8")
        end = time.time() + 20
        while time.time() < end and m.get(it.id).status != ERROR:
            time.sleep(0.2)
        assert m.get(it.id).status == ERROR
        assert "404" in m.get(it.id).error
    finally:
        m.shutdown()


@pytest.mark.skipif(not shutil.which("yt-dlp") and not os.path.exists(os.path.expanduser("~/.local/bin/yt-dlp")),
                    reason="yt-dlp not installed")
def test_ytdlp_job_downloads_direct_video(tmp_path):
    root = tmp_path / "www"
    root.mkdir()
    subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=duration=2:size=160x120:rate=15",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", str(root / "demo.mp4")], check=True, timeout=60,
    )
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(root))
    handler.log_message = lambda *a, **k: None
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    cfg = Config(download_root=str(tmp_path / "dl"), proxy_mode="none")
    m = Manager(cfg, Database(str(tmp_path / "db.sqlite")))
    m.start()
    try:
        it = m.add(f"http://127.0.0.1:{httpd.server_address[1]}/demo.mp4", kind="ytdlp")
        end = time.time() + 60
        while time.time() < end and m.get(it.id).status not in (COMPLETE, ERROR):
            time.sleep(0.3)
        got = m.get(it.id)
        assert got.status == COMPLETE, got.error
        assert os.path.getsize(got.path) > 1000
        assert got.category == "Video"
    finally:
        m.shutdown()
        httpd.shutdown()


HAVE_YTDLP = bool(shutil.which("yt-dlp") or os.path.exists(os.path.expanduser("~/.local/bin/yt-dlp")))


def test_mp4_args_depend_on_ffmpeg():
    from gesit.engine.media import _mp4_args
    with_ff = _mp4_args(True)
    assert "--merge-output-format" in with_ff and with_ff[with_ff.index("--merge-output-format") + 1] == "mp4"
    assert "--remux-video" in with_ff
    # H.264 before resolution, separate streams before the (often 403 / low-res) merged format
    assert with_ff[with_ff.index("-S") + 1].startswith("vcodec:h264")
    assert with_ff[with_ff.index("-f") + 1].startswith("bv+ba")
    # nothing can be merged without ffmpeg: ask for one finished mp4 file
    assert _mp4_args(False) == ["-f", "b[ext=mp4]/b"]


@pytest.mark.skipif(not HAVE_YTDLP, reason="yt-dlp not installed")
def test_ytdlp_job_turns_webm_into_mp4(tmp_path):
    root = tmp_path / "www"
    root.mkdir()
    made = subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=duration=2:size=160x120:rate=15",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=2", "-c:v", "libvpx-vp9", "-c:a", "libopus",
         str(root / "demo.webm")], timeout=60,
    )
    if made.returncode != 0:
        pytest.skip("ffmpeg has no VP9/Opus encoder")
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(root))
    handler.log_message = lambda *a, **k: None
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    cfg = Config(download_root=str(tmp_path / "dl"), proxy_mode="none")
    m = Manager(cfg, Database(str(tmp_path / "db.sqlite")))
    m.start()
    try:
        it = m.add(f"http://127.0.0.1:{httpd.server_address[1]}/demo.webm", kind="ytdlp")
        end = time.time() + 60
        while time.time() < end and m.get(it.id).status not in (COMPLETE, ERROR):
            time.sleep(0.3)
        got = m.get(it.id)
        assert got.status == COMPLETE, got.error
        assert got.path.endswith(".mp4"), got.path
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=format_name", "-of", "csv=p=0", got.path],
            capture_output=True, text=True,
        )
        assert "mp4" in probe.stdout
    finally:
        m.shutdown()
        httpd.shutdown()


# --------------------------------------------------------------- yt-dlp cookies and retries
@pytest.fixture()
def fake_ytdlp(tmp_path, monkeypatch):
    """A stand-in yt-dlp that logs how it was called and fails on demand (FAKE_FAIL=cookies|plain|both)."""
    if sys.platform == "win32":
        pytest.skip("shebang script")
    tools = tmp_path / "tools"
    tools.mkdir()
    log = tmp_path / "calls.log"
    exe = tools / "yt-dlp"
    exe.write_text(f"""#!/usr/bin/env python3
import os, sys
args = sys.argv[1:]
if args == ["--version"]:
    print(os.environ.get("FAKE_VERSION", "2099.01.01"))
    sys.exit(0)
with_cookies = "--cookies" in args
if with_cookies:
    jar = open(args[args.index("--cookies") + 1]).read()
    assert jar.startswith("# Netscape HTTP Cookie File"), jar
open({str(log)!r}, "a").write("cookies" if with_cookies else "plain")
open({str(log)!r}, "a").write("\\n")
fail = os.environ.get("FAKE_FAIL", "")
if fail == "both" or (fail == "cookies" and with_cookies) or (fail == "plain" and not with_cookies):
    sys.stderr.write("WARNING: noise\\nERROR: " + ("session rotated" if with_cookies else "Sign in to confirm") + "\\n")
    sys.exit(1)
out = args[args.index("-o") + 1].replace("%(title).150B.%(ext)s", "video.mp4")
print("GESITN video.mp4")
if os.environ.get("FAKE_PROGRESS"):
    print("GESITS 1500")
    if "--progress" in args:  # like the real thing: --print makes it quiet, --progress brings the lines back
        for done in (100, 500, 1000):                      # video stream
            print(f"GESITP {{done}} 1000 NA {{done * 10}}.5", flush=True)
        for done in (250, 500):                            # audio stream: the counter starts again
            print(f"GESITP {{done}} 500 NA 900.25", flush=True)
open(out, "wb").write(b"x" * 2048)
print("GESITF " + out)
""")
    exe.chmod(0o755)
    monkeypatch.setenv("GESIT_TOOLS_DIR", str(tools))
    monkeypatch.setenv("TMPDIR", str(tmp_path))  # cookie files must not be left behind here
    monkeypatch.setattr("gesit.engine.media.tempfile.tempdir", str(tmp_path))
    return log


def _run_ytdlp(tmp_path, url, cookies="SID=abc; HSID=def"):
    import asyncio
    from gesit.engine.downloader import Request
    from gesit.engine.media import YtdlpJob

    job = YtdlpJob(Request(url=url, save_dir=str(tmp_path / "out"), cookies=cookies))
    asyncio.run(job.run())
    return job


def _calls(log):
    return log.read_text().split() if log.exists() else []


def _no_cookie_files(tmp_path):
    return [f for f in os.listdir(tmp_path) if f.startswith("gesit-cookies-")] == []


def test_ytdlp_other_sites_use_cookies_first(fake_ytdlp, tmp_path):
    job = _run_ytdlp(tmp_path, "https://vimeo.com/123")
    assert job.status == COMPLETE and _calls(fake_ytdlp) == ["cookies"]
    assert _no_cookie_files(tmp_path)


def test_ytdlp_other_sites_retry_without_cookies(fake_ytdlp, tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_FAIL", "cookies")
    job = _run_ytdlp(tmp_path, "https://vimeo.com/123")
    assert job.status == COMPLETE and _calls(fake_ytdlp) == ["cookies", "plain"]
    assert _no_cookie_files(tmp_path)


def test_ytdlp_youtube_tries_without_cookies_first(fake_ytdlp, tmp_path):
    """A rotated YouTube login made yt-dlp stop with 'The page needs to be reloaded' on public videos."""
    job = _run_ytdlp(tmp_path, "https://www.youtube.com/shorts/abc")
    assert job.status == COMPLETE and _calls(fake_ytdlp) == ["plain"]


def test_ytdlp_youtube_falls_back_to_cookies_for_restricted_videos(fake_ytdlp, tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_FAIL", "plain")
    job = _run_ytdlp(tmp_path, "https://youtu.be/abc")
    assert job.status == COMPLETE and _calls(fake_ytdlp) == ["plain", "cookies"]
    assert _no_cookie_files(tmp_path)


def test_ytdlp_without_cookies_runs_once(fake_ytdlp, tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_FAIL", "both")
    job = _run_ytdlp(tmp_path, "https://vimeo.com/123", cookies="")
    assert job.status == ERROR and _calls(fake_ytdlp) == ["plain"]
    assert job.error == "yt-dlp failed: ERROR: Sign in to confirm"


def test_ytdlp_both_attempts_failing_reports_the_sites_own_answer(fake_ytdlp, tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_FAIL", "both")
    for url in ("https://www.youtube.com/watch?v=abc", "https://vimeo.com/123"):
        job = _run_ytdlp(tmp_path, url)
        assert job.status == ERROR
        assert job.error.startswith("yt-dlp failed: ERROR: Sign in to confirm"), job.error
        assert "also failed with your browser cookies" in job.error
        assert "session rotated" not in job.error  # warnings and the cookie run's error stay out
    assert _no_cookie_files(tmp_path)


def test_cookie_file_is_scoped_to_the_site_and_private(tmp_path):
    from gesit.engine.media import _write_cookie_file

    path = _write_cookie_file("SID=abc; bad name=1; =x; novalue; PREF=a=b", "https://www.youtube.com/watch?v=1")
    try:
        assert oct(os.stat(path).st_mode & 0o777) == oct(0o600) or sys.platform == "win32"
        rows = [ln.split("\t") for ln in open(path).read().splitlines()[1:]]
        assert [(r[0], r[3], r[5], r[6]) for r in rows] == [
            (".youtube.com", "TRUE", "SID", "abc"), (".youtube.com", "TRUE", "PREF", "a=b")]
    finally:
        os.remove(path)
    assert _write_cookie_file("", "https://example.com/") is None
    assert _write_cookie_file("novalue", "https://example.com/") is None
    http_path = _write_cookie_file("a=1", "http://download1.example.com/f")
    try:
        assert open(http_path).read().splitlines()[1].startswith(".download1.example.com\tTRUE\t/\tFALSE\t")
    finally:
        os.remove(http_path)


def test_failure_with_an_old_ytdlp_says_to_update_it(fake_ytdlp, tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_FAIL", "both")
    monkeypatch.setenv("FAKE_VERSION", "2020.01.15")
    job = _run_ytdlp(tmp_path, "https://www.youtube.com/watch?v=abc")
    assert job.status == ERROR
    assert "ERROR: Sign in to confirm" in job.error
    assert "Your yt-dlp (2020.01.15) is" in job.error and "days old" in job.error and "Update it" in job.error


def test_failure_with_a_fresh_ytdlp_gives_no_update_advice(fake_ytdlp, tmp_path, monkeypatch):
    import datetime
    monkeypatch.setenv("FAKE_FAIL", "both")
    monkeypatch.setenv("FAKE_VERSION", (datetime.date.today() - datetime.timedelta(days=3)).strftime("%Y.%m.%d"))
    job = _run_ytdlp(tmp_path, "https://vimeo.com/123")
    assert job.status == ERROR and "Update it" not in job.error


def test_success_never_mentions_updates(fake_ytdlp, tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_VERSION", "2020.01.15")
    job = _run_ytdlp(tmp_path, "https://vimeo.com/123")
    assert job.status == COMPLETE and not job.error


@pytest.mark.parametrize("text,days", [("2026.07.04", 139), ("2026.07.04.123456", 139), ("nightly", None), ("", None),
                                       ("2026.13.40", None)])
def test_version_age(text, days):
    import datetime
    from gesit.engine.media import _version_age_days
    assert _version_age_days(text, today=datetime.date(2026, 11, 20)) == days


def test_user_tools_folder_is_searched_before_path(tmp_path, monkeypatch):
    from gesit.core import osutil

    if sys.platform == "win32":
        pytest.skip("POSIX executable names")
    monkeypatch.delenv("GESIT_TOOLS_DIR", raising=False)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    tools = tmp_path / "data" / "gesit" / "tools"
    tools.mkdir(parents=True)
    (tools / "yt-dlp").write_text("#!/bin/sh\n")
    monkeypatch.setenv("PATH", str(tmp_path / "nothing"))
    assert osutil.tool_path("yt-dlp") == str(tools / "yt-dlp")
    assert osutil.user_tools_dir() == str(tools)


def test_ytdlp_progress_covers_video_and_audio_as_one_download(fake_ytdlp, tmp_path, monkeypatch):
    """Without --progress yt-dlp printed no progress at all (bar stuck, 'Receiving data...' then suddenly complete);
    video and audio are separate streams and the byte counter restarts for the second one."""
    import asyncio
    from gesit.engine.downloader import Request
    from gesit.engine.media import YtdlpJob

    monkeypatch.setenv("FAKE_PROGRESS", "1")
    job = YtdlpJob(Request(url="https://vimeo.com/1", save_dir=str(tmp_path / "out")))
    seen = []
    job.on_change = lambda: seen.append((job.status, job.downloaded, job.total, job.speed))
    asyncio.run(job.run())
    assert job.status == COMPLETE
    live = [(d, t, sp) for st, d, t, sp in seen if st == "downloading" and d]
    assert len(live) == 5, live
    downs = [d for d, _, _ in live]
    assert downs == sorted(downs), f"progress went backwards: {downs}"
    assert downs == [100, 500, 1000, 1250, 1500]          # audio continues after the video, not from zero
    assert all(t == 1500 for _, t, _ in live)              # one total for the whole download
    assert live[0][2] == 1000.5 and live[3][2] == 900.25   # speed is passed through
    assert max(downs) <= 1500


@pytest.mark.skipif(not HAVE_YTDLP, reason="yt-dlp not installed")
def test_real_ytdlp_reports_progress_while_downloading(tmp_path):
    """The real yt-dlp: a file that takes a moment to arrive must give several progress steps."""
    import asyncio
    from gesit.engine.downloader import Request
    from gesit.engine.media import YtdlpJob

    root = tmp_path / "www"
    root.mkdir()
    subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc2=duration=6:size=640x360:rate=25",
         "-c:v", "libx264", "-b:v", "3M", "-pix_fmt", "yuv420p", str(root / "big.mp4")], check=True, timeout=90,
    )

    class Slow(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *a, **k):
            super().__init__(*a, directory=str(root), **k)

        def copyfile(self, src, dst):
            while chunk := src.read(32768):
                dst.write(chunk)
                time.sleep(0.01)

        def log_message(self, *a):
            pass

    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Slow)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        job = YtdlpJob(Request(url=f"http://127.0.0.1:{httpd.server_address[1]}/big.mp4", save_dir=str(tmp_path / "out")))
        seen = []
        job.on_change = lambda: seen.append((job.status, job.downloaded, job.total))
        asyncio.run(job.run())
    finally:
        httpd.shutdown()
    assert job.status == COMPLETE, job.error
    steps = [(d, t) for st, d, t in seen if st == "downloading" and d and t and d < t]
    assert len(steps) >= 3, f"no live progress: {seen[:6]}"
