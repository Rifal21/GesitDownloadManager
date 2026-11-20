import functools
import http.server
import os
import shutil
import subprocess
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
