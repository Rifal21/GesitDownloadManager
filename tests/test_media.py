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


@pytest.fixture()
def hls_server(tmp_path):
    root = tmp_path / "www"
    root.mkdir()
    subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=duration=6:size=160x120:rate=15",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=6", "-c:v", "libx264", "-c:a", "aac",
         "-f", "hls", "-hls_time", "2", "-hls_playlist_type", "vod", str(root / "index.m3u8")],
        check=True, timeout=60,
    )
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(root))
    handler.log_message = lambda *a, **k: None
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()


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
