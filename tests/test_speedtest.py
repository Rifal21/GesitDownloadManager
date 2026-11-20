import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

import pytest

from gesit.core import speedtest
from gesit.core.speedtest import Cancelled, SpeedTest, SpeedTestError, format_rate


class FakeCloud:
    """Stand-in for speed.cloudflare.com: /__down?bytes=N and /__up, optionally rate limited per connection."""

    def __init__(self, down_status=200, up_status=200, rate_bps=None):
        outer = self
        self.rate_bps = rate_bps
        self.up_bytes = 0

        class H(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_GET(self):  # noqa: N802
                u = urlsplit(self.path)
                n = int(parse_qs(u.query).get("bytes", ["0"])[0])
                if down_status != 200:
                    self.send_response(down_status)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                self.send_response(200)
                self.send_header("Content-Length", str(n))
                self.end_headers()
                chunk = b"x" * 16384
                sent = 0
                try:
                    while sent < n:
                        part = chunk[: min(len(chunk), n - sent)]
                        self.wfile.write(part)
                        sent += len(part)
                        if outer.rate_bps:
                            time.sleep(len(part) * 8 / outer.rate_bps)
                except (BrokenPipeError, ConnectionResetError):
                    pass

            def do_POST(self):  # noqa: N802
                left = int(self.headers.get("Content-Length", "0"))
                while left > 0:
                    data = self.rfile.read(min(65536, left))
                    if not data:
                        break
                    left -= len(data)
                    outer.up_bytes += len(data)
                self.send_response(up_status)
                self.send_header("Content-Length", "0")
                self.end_headers()

            def log_message(self, *a):
                pass

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.httpd.daemon_threads = True
        self.httpd.handle_error = lambda *a: None  # cancelled streams reset the connection on purpose
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.httpd.server_address[1]}"

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture()
def cloud(monkeypatch):
    for k in ("http_proxy", "https_proxy", "all_proxy", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):
        monkeypatch.delenv(k, raising=False)
    made = []

    def make(**kw):
        c = FakeCloud(**kw)
        made.append(c)
        return c

    yield make
    for c in made:
        c.close()


def quick(base, **kw):
    kw = dict(down_seconds=2.0, up_seconds=2.0, streams=2, pings=3) | kw
    return SpeedTest(base=base, **kw)


def test_full_run_reports_every_phase_in_order(cloud):
    c = cloud()
    seen = []
    r = quick(c.base, on_progress=lambda ph, mbps, frac: seen.append((ph, mbps, frac))).run()
    phases = [p for p, _, _ in seen]
    assert phases[0] == "ping" and phases[-1] == "done"
    assert phases.index("download") < phases.index("upload") < phases.index("done")
    assert all(0 <= f <= 1 for _, _, f in seen)
    assert any(p == "download" and m and m > 0 for p, m, _ in seen), "live download speed was reported"
    assert 0 < r.ping_ms < 200 and r.jitter_ms >= 0
    assert r.download_bps > 1e6 and r.upload_bps and r.upload_bps > 1e6 and r.note == ""
    assert c.up_bytes > 0


def test_download_speed_is_measured_correctly(cloud):
    """2 connections x 4 Mbit/s each: the result must land near 8 Mbit/s, warm-up excluded."""
    c = cloud(rate_bps=4_000_000)
    r = quick(c.base, down_seconds=3.0, up_seconds=1.0).run()
    assert 5.5e6 < r.download_bps < 10.5e6, r.download_bps


def test_missing_upload_keeps_the_download_result(cloud):
    c = cloud(up_status=500)
    r = quick(c.base).run()
    assert r.download_bps > 1e6
    assert r.upload_bps is None and "HTTP 500" in r.note


def test_server_refusing_the_download_is_an_error(cloud):
    c = cloud(down_status=403)
    with pytest.raises(SpeedTestError, match="HTTP 403"):
        quick(c.base).run()


def test_unreachable_server_is_an_error_not_a_crash(monkeypatch):
    for k in ("http_proxy", "https_proxy", "all_proxy", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):
        monkeypatch.delenv(k, raising=False)
    with pytest.raises(SpeedTestError, match="Could not reach"):
        quick("http://127.0.0.1:9").run()


def test_cancel_stops_a_running_test_quickly(cloud):
    c = cloud(rate_bps=2_000_000)
    t = quick(c.base, down_seconds=30.0, up_seconds=30.0)
    out = {}

    def work():
        try:
            t.run()
        except Cancelled:
            out["cancelled"] = time.perf_counter()

    th = threading.Thread(target=work)
    th.start()
    time.sleep(1.5)
    asked = time.perf_counter()
    t.cancel()
    th.join(10)
    assert not th.is_alive(), "run() did not return after cancel()"
    assert out["cancelled"] - asked < 3


def test_cancel_before_start_is_honoured(cloud):
    c = cloud()
    t = quick(c.base)
    t.cancel()
    with pytest.raises(Cancelled):
        t.run()


def test_base_url_can_come_from_the_environment(cloud, monkeypatch):
    c = cloud()
    monkeypatch.setenv("GESIT_SPEEDTEST_URL", c.base + "/")
    assert SpeedTest().base == c.base
    assert SpeedTest(base="https://example.test").base == "https://example.test"
    monkeypatch.delenv("GESIT_SPEEDTEST_URL")
    assert SpeedTest().base == speedtest.BASE_URL


@pytest.mark.parametrize("bps,text", [
    (None, "-"),
    (93_400_000, "93.4 Mbit/s (11.7 MB/s)"),
    (500_000, "0.5 Mbit/s (0.1 MB/s)"),
    (1_250_000_000, "1.25 Gbit/s (156 MB/s)"),
])
def test_format_rate(bps, text):
    assert format_rate(bps) == text


# ------------------------------------------------------------------ CLI and window
import os
import subprocess
import sys
import textwrap


def _env(tmp_path, base):
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen", REPO=os.getcwd(), GESIT_SPEEDTEST_URL=base,
               XDG_CONFIG_HOME=str(tmp_path / "c"), XDG_DATA_HOME=str(tmp_path / "d"),
               XDG_DOWNLOAD_DIR=str(tmp_path / "dl"), XDG_RUNTIME_DIR=str(tmp_path / "r"), PYTHONPATH=os.getcwd())
    for k in ("http_proxy", "https_proxy", "all_proxy", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):
        env.pop(k, None)
    os.makedirs(env["XDG_RUNTIME_DIR"], mode=0o700, exist_ok=True)
    return env


@pytest.mark.skipif(sys.platform == "win32", reason="XDG directories in the harness")
def test_cli_speedtest_prints_the_result(cloud, tmp_path):
    c = cloud()
    r = subprocess.run([sys.executable, "-m", "gesit", "--speedtest"], env=_env(tmp_path, c.base),
                       capture_output=True, text=True, timeout=90)
    assert r.returncode == 0, r.stdout + r.stderr[-600:]
    out = r.stdout
    assert "Measuring latency" in out and "Measuring download" in out and "Measuring upload" in out
    assert "Ping" in out and "Download" in out and "Upload" in out and "bit/s" in out and "MB/s" in out


@pytest.mark.skipif(sys.platform == "win32", reason="XDG directories in the harness")
def test_cli_speedtest_failure_exits_with_an_error(tmp_path):
    r = subprocess.run([sys.executable, "-m", "gesit", "--speedtest"], env=_env(tmp_path, "http://127.0.0.1:9"),
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 1 and "Speed test failed" in r.stdout and "Traceback" not in r.stderr


@pytest.mark.skipif(sys.platform == "win32", reason="XDG directories in the harness")
def test_speedtest_window_runs_saves_the_result_and_can_be_stopped(cloud, tmp_path):
    c = cloud()
    slow = cloud(rate_bps=1_000_000)
    code = textwrap.dedent(
        """
        import os, sys, time
        sys.path.insert(0, os.environ["REPO"])
        from PySide6.QtWidgets import QApplication
        from gesit.gui.app import GesitApp
        from gesit.gui.speedtest_dialog import SpeedTestDialog
        qapp = QApplication([]); qapp.setQuitOnLastWindowClosed(False)
        app = GesitApp(qapp)
        app.cfg.hidden_hint_shown = app.cfg.integration_prompted = True

        def pump(cond, secs):
            end = time.time() + secs
            while not cond() and time.time() < end:
                qapp.processEvents(); time.sleep(0.02)
            assert cond(), "timed out"

        app.speedtest_dialog(); app.speedtest_dialog()          # second call re-uses the open window
        dlgs = [d for d in app._dialogs if isinstance(d, SpeedTestDialog)]
        assert len(dlgs) == 1
        d = dlgs[0]
        assert d.start.text() == "Start" and not app.cfg.last_speedtest

        d.start.click()
        assert d.start.text() == "Stop"
        pump(lambda: d._test is None, 90)
        assert d.start.text() == "Start" and d.status.text().startswith("Done"), d.status.text()
        assert d.bar.value() == 1000
        assert d.down.text() not in ("-", "") and d.up.text() not in ("-", "") and d.ping.text() != "-"
        saved = app.cfg.last_speedtest
        assert saved["down"] > 1 and saved["up"] > 1 and saved["ping"] > 0 and saved["ts"] > 0
        assert d.last.isHidden()                                 # just shown above, no need to repeat it
        again = SpeedTestDialog(app.cfg)                         # a later visit shows the previous result
        assert "Last test" in again.last.text() and not again.last.isHidden()
        again.close()

        # stop a running test: the worker returns, the button comes back, the old result stays
        os.environ["GESIT_SPEEDTEST_URL"] = os.environ["SLOW"]
        d.start.click()
        pump(lambda: d.down.text() != "-", 30)
        d.start.click()                                          # Stop
        pump(lambda: d._test is None, 10)
        assert d.start.text() == "Start" and d.status.text() == "Stopped."
        assert app.cfg.last_speedtest == saved

        # closing while running must not hang or crash
        d.start.click(); pump(lambda: d._test is not None and d.down.text() != "-", 30)
        t0 = time.time(); d.close(); assert time.time() - t0 < 5
        for _ in range(30):
            qapp.processEvents(); time.sleep(0.02)
        print("SPEEDTEST WINDOW OK")
        app.quit()
        """
    )
    env = _env(tmp_path, c.base)
    env["SLOW"] = slow.base
    r = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, timeout=180)
    assert "SPEEDTEST WINDOW OK" in r.stdout, r.stdout + r.stderr[-1800:]
