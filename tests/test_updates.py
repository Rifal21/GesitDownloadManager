import json
import os
import subprocess
import sys
import textwrap
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from gesit.core import updates


@pytest.mark.parametrize("latest,current,newer", [
    ("v1.2.0", "1.1.0", True),
    ("1.1.1", "1.1.0", True),
    ("v1.10.0", "1.9.9", True),      # numeric, not alphabetical
    ("1.1.0", "1.1.0", False),
    ("v1.0.9", "1.1.0", False),
    ("1.2", "1.2.0", False),         # missing parts count as zero
    ("v2", "1.9.9", True),
    ("1.2.0-rc1", "1.1.0", False),   # pre-release tags are never offered
    ("nightly", "1.1.0", False),
    ("", "1.1.0", False),
])
def test_is_newer(latest, current, newer):
    assert updates.is_newer(latest, current) is newer


class _Server:
    def __init__(self, status=200, body=None):
        outer = self
        self.hits = 0
        self.agents = []

        class H(BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                outer.hits += 1
                outer.agents.append(self.headers.get("User-Agent", ""))
                raw = body if isinstance(body, bytes) else json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def log_message(self, *a):
                pass

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}/latest"

    def close(self):
        self.httpd.shutdown()


@pytest.fixture()
def serve():
    servers = []

    def make(status=200, body=None):
        s = _Server(status, body)
        servers.append(s)
        return s

    yield make
    for s in servers:
        s.close()


def test_check_reports_newer_release(serve):
    s = serve(body={"tag_name": "v1.4.0", "body": "- faster\n- fixes", "draft": False, "prerelease": False})
    u = updates.check("1.1.0", url=s.url)
    assert u and u.version == "1.4.0" and "faster" in u.notes
    assert u.url == updates.RELEASE_PAGE + "v1.4.0"
    assert s.agents == ["Gesit/1.1.0"]


def test_check_up_to_date_draft_and_prerelease(serve):
    assert updates.check("1.4.0", url=serve(body={"tag_name": "v1.4.0"}).url) is None
    assert updates.check("1.1.0", url=serve(body={"tag_name": "v9.0.0", "draft": True}).url) is None
    assert updates.check("1.1.0", url=serve(body={"tag_name": "v9.0.0", "prerelease": True}).url) is None
    assert updates.check("1.1.0", url=serve(status=404, body={"message": "Not Found"}).url) is None


def test_release_page_comes_from_the_tag_not_from_the_response(serve):
    s = serve(body={"tag_name": "v2.0.0", "html_url": "https://evil.example/login"})
    assert updates.check("1.1.0", url=s.url).url == updates.RELEASE_PAGE + "v2.0.0"


def test_check_errors_are_reported_not_raised_as_crashes(serve):
    with pytest.raises(updates.UpdateError, match="HTTP 500"):
        updates.check("1.1.0", url=serve(status=500, body={}).url)
    with pytest.raises(updates.UpdateError, match="Unexpected"):
        updates.check("1.1.0", url=serve(body=b"<html>not json</html>").url)
    with pytest.raises(updates.UpdateError, match="Unexpected"):
        updates.check("1.1.0", url=serve(body={"no_tag": 1}).url)
    with pytest.raises(updates.UpdateError, match="Could not reach"):
        updates.check("1.1.0", url="http://127.0.0.1:9/latest", timeout=2)


def test_long_release_notes_are_shortened(serve):
    s = serve(body={"tag_name": "v1.5.0", "body": "x" * 5000})
    assert len(updates.check("1.1.0", url=s.url).notes) < 720


@pytest.mark.skipif(sys.platform == "win32", reason="XDG directories and Unix socket in the GUI harness")
def test_app_offers_update_once_and_manual_check_reports(tmp_path, serve):
    s = serve(body={"tag_name": "v9.9.9", "body": "notes", "draft": False, "prerelease": False})
    code = textwrap.dedent(
        """
        import os, sys, time
        sys.path.insert(0, os.environ["REPO"])
        from PySide6.QtWidgets import QApplication, QMessageBox
        from gesit.gui.app import GesitApp
        qapp = QApplication([]); qapp.setQuitOnLastWindowClosed(False)
        app = GesitApp(qapp)
        app.cfg.hidden_hint_shown = True
        app.cfg.integration_prompted = True

        def wait():
            end = time.time() + 15
            while app._checking_updates and time.time() < end:
                qapp.processEvents(); time.sleep(0.02)
            for _ in range(20):
                qapp.processEvents()
            assert not app._checking_updates, "check never finished"

        def boxes():
            return [w for w in qapp.topLevelWidgets() if isinstance(w, QMessageBox) and w.isVisible()]

        # automatic check, window visible: dialog + remembered, never shown twice for the same version
        app.check_updates_auto(); wait()
        assert app.cfg.update_notified == "9.9.9", app.cfg.update_notified
        assert app.cfg.last_update_check > 0
        bx = boxes(); assert len(bx) == 1 and "9.9.9" in bx[0].text(), [b.text() for b in bx]
        bx[0].close(); qapp.processEvents()

        # a second automatic check inside 22h does not even contact the server
        app.check_updates_auto(); wait()
        assert not boxes()

        # forced automatic check for the same version stays silent
        app.cfg.last_update_check = 0
        app.check_updates(manual=False); wait()
        assert not boxes() and app._pending_update is None

        # manual check always answers
        app.check_updates(manual=True); wait()
        bx = boxes(); assert len(bx) == 1 and "9.9.9" in bx[0].text()
        bx[0].close(); qapp.processEvents()

        # opt-out is respected
        app.cfg.check_updates = False; app.cfg.last_update_check = 0; app.cfg.update_notified = ""
        app.check_updates_auto(); wait()
        assert not boxes() and app._pending_update is None

        # hidden window and no tray: kept until the window is shown again
        app.cfg.check_updates = True
        app.window.hide()
        app.check_updates(manual=False); wait()
        assert app._pending_update and not boxes()
        app.show_main()
        end = time.time() + 5
        while not boxes() and time.time() < end:
            qapp.processEvents(); time.sleep(0.02)
        assert boxes() and app._pending_update is None
        print("UPDATES OK")
        app.quit()
        """
    )
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen", REPO=os.getcwd(), GESIT_UPDATE_URL=s.url,
               XDG_CONFIG_HOME=str(tmp_path / "c"), XDG_DATA_HOME=str(tmp_path / "d"),
               XDG_DOWNLOAD_DIR=str(tmp_path / "dl"), XDG_RUNTIME_DIR=str(tmp_path / "r"))
    os.makedirs(env["XDG_RUNTIME_DIR"], mode=0o700)
    r = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, timeout=90)
    assert "UPDATES OK" in r.stdout, r.stdout + r.stderr[-1500:]
    # contacted: first auto, forced auto, manual, hidden-window check. Not: the second auto (<22h), the opt-out
    assert s.hits == 4, s.hits
