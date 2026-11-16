import os
import subprocess
import sys
import textwrap


def test_closing_window_without_tray_keeps_gesit_running(tmp_path):
    """GNOME has no tray: the X button must hide Gesit, not quit it (browser integration depends on it)."""
    code = textwrap.dedent(
        """
        import os, sys
        sys.path.insert(0, os.environ["REPO"])
        from PySide6.QtWidgets import QApplication, QSystemTrayIcon
        from PySide6.QtCore import QTimer
        from gesit.gui.app import GesitApp
        qapp = QApplication([]); qapp.setQuitOnLastWindowClosed(False)
        assert not QSystemTrayIcon.isSystemTrayAvailable()
        app = GesitApp(qapp)
        app.cfg.hidden_hint_shown = True          # no modal box in the test
        assert app.window.isVisible()
        app.window.close()
        assert not app.window.isVisible() and not app.quitting, "closing the window must not quit"
        app.show_main()
        assert app.window.isVisible()
        app.cfg.close_to_tray = False
        app.window.close()
        assert app.quitting, "with close_to_tray off, closing quits"
        print("LIFECYCLE OK")
        """
    )
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen", REPO=os.getcwd(),
               XDG_CONFIG_HOME=str(tmp_path / "c"), XDG_DATA_HOME=str(tmp_path / "d"),
               XDG_DOWNLOAD_DIR=str(tmp_path / "dl"), XDG_RUNTIME_DIR=str(tmp_path / "r"))
    os.makedirs(env["XDG_RUNTIME_DIR"], mode=0o700)
    r = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, timeout=60)
    assert "LIFECYCLE OK" in r.stdout, r.stdout + r.stderr[-800:]
