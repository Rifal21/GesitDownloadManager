"""Application controller: wires manager, IPC, tray, windows and dialogs."""

from __future__ import annotations

import asyncio
import os
import re
import shutil
import subprocess
import sys
import threading
import time

from PySide6.QtCore import QObject, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QAction, QDesktopServices, QGuiApplication
from PySide6.QtWidgets import QApplication, QMenu, QMessageBox, QSystemTrayIcon

from .. import __version__
from ..core import ipc, osutil, updates, util
from ..core.config import CONFIG_PATH, DB_PATH, Config, migrate_legacy
from ..core.db import Database
from ..core.manager import Manager
from ..core.models import COMPLETE, Item
from . import icons
from .dialogs import AboutDialog, AddURLDialog, CompleteDialog, FileInfoDialog, LinksDialog
from .main_window import MainWindow
from .options import OptionsDialog, SchedulerDialog
from .progress import ProgressDialog

URL_RE = re.compile(r"https?://[^\s\"'<>]+", re.I)


class Bridge(QObject):
    """Thread-safe hop from the manager / IPC thread to the Qt main thread."""

    event = Signal(str, object)
    capture = Signal(dict)
    capture_many = Signal(list, dict)
    show_window = Signal()
    quit_requested = Signal()
    update_result = Signal(object, str, bool)  # Update | None, error text, user asked for it


class GesitApp:
    def __init__(self, qapp: QApplication, background: bool = False):
        self.qapp = qapp
        migrate_legacy()  # settings + download list from the pre-rename 'lidm' install
        self.cfg = Config.load()
        self.db = Database(DB_PATH)
        self.manager = Manager(self.cfg, self.db)
        self.quitting = False
        self.progress: dict[int, ProgressDialog] = {}
        self.post: dict[int, dict] = {}
        self._dialogs: set = set()
        self.bridge = Bridge()
        self.bridge.event.connect(self.on_event)
        self.bridge.capture.connect(self.on_capture)
        self.bridge.capture_many.connect(self.on_capture_many)
        self.bridge.show_window.connect(self.show_main)
        self.bridge.quit_requested.connect(self.quit)
        self.bridge.update_result.connect(self._on_update_result)
        self._pending_update: updates.Update | None = None
        self._checking_updates = False
        self.manager.add_listener(lambda k, it: self.bridge.event.emit(k, it))
        self.manager.start()

        self.window = MainWindow(self)
        self.tray_available = QSystemTrayIcon.isSystemTrayAvailable()
        self.tray = self._make_tray() if self.tray_available else None

        self.ipc = ipc.IPCServer(self._ipc_handler)
        asyncio.run_coroutine_threadsafe(self.ipc.start(), self.manager.loop).result(5)

        if not (background or self.cfg.start_minimized):
            self.window.show()
            QTimer.singleShot(1200, self._offer_integration)
        QTimer.singleShot(15000, self.check_updates_auto)
        self._update_timer = QTimer(self.window)  # long-running background instances check again later
        self._update_timer.timeout.connect(self.check_updates_auto)
        self._update_timer.start(6 * 3600 * 1000)
        try:
            from ..hostinstall import refresh_appimage_host

            refresh_appimage_host()
        except OSError:
            pass

    # ----------------------------------------------------------------- tray
    def _make_tray(self) -> QSystemTrayIcon:
        t = QSystemTrayIcon(icons.icon("app"), self.window)
        t.setToolTip("Gesit")
        m = QMenu()
        for text, fn in (
            ("Show Gesit", self.show_main), ("Add URL...", self.add_url_dialog), (None, None),
            ("Start Queue", self.manager.start_queue), ("Stop All", self.manager.stop_all), (None, None),
            ("Options", self.options_dialog), (None, None), ("Exit", self.quit),
        ):
            if text is None:
                m.addSeparator()
            else:
                a = QAction(text, m)
                a.triggered.connect(lambda _c=False, f=fn: f())
                m.addAction(a)
        t.setContextMenu(m)
        t.messageClicked.connect(lambda: self._pending_update and self.show_main())
        t.activated.connect(
            lambda reason: self.toggle_main() if reason == QSystemTrayIcon.ActivationReason.Trigger else None
        )
        self._tray_menu = m
        t.show()
        return t

    def notify(self, title: str, msg: str) -> None:
        if self.tray:
            self.tray.showMessage(title, msg, icons.icon("app"), 5000)

    # --------------------------------------------------------------- windows
    def show_main(self) -> None:
        self.window.show()
        self.window.setWindowState(self.window.windowState() & ~Qt.WindowState.WindowMinimized)
        self.window.raise_()
        self.window.activateWindow()
        if self._pending_update:
            QTimer.singleShot(400, self._offer_update)

    # ------------------------------------------------------------------ updates
    def check_updates_auto(self) -> None:
        """Quiet check on start and every few hours; hits GitHub at most once a day."""
        if self.quitting or not self.cfg.check_updates:
            return
        if time.time() - self.cfg.last_update_check < 22 * 3600:
            return
        self.check_updates(manual=False)

    def check_updates(self, manual: bool = True) -> None:
        if self._checking_updates:
            return
        self._checking_updates = True
        proxy = self.cfg.proxy_url()

        def work() -> None:
            upd, err = None, ""
            try:
                upd = updates.check(__version__, proxy=proxy)
            except updates.UpdateError as e:
                err = str(e)
            except Exception as e:  # noqa: BLE001 - never let the checker take the app down
                err = f"{type(e).__name__}: {e}"
            self.bridge.update_result.emit(upd, err, manual)

        threading.Thread(target=work, name="gesit-update-check", daemon=True).start()

    def _on_update_result(self, upd, err: str, manual: bool) -> None:
        self._checking_updates = False
        if self.quitting:
            return
        if not err:
            self.cfg.last_update_check = time.time()
            self.cfg.save()
        if manual:
            if err:
                QMessageBox.warning(self.window, "Check for updates", err)
            elif upd:
                self._pending_update = upd
                self._offer_update()
            else:
                QMessageBox.information(self.window, "Check for updates",
                                        f"Gesit {__version__} is the latest version.")
            return
        if err or not upd or upd.version == self.cfg.update_notified:
            return
        self._pending_update = upd
        if self.window.isVisible():
            self._offer_update()
        elif self.tray:
            self.notify("Gesit update available", f"Gesit {upd.version} is out (you have {__version__}). Click to see it.")
        # no tray and hidden window: the offer appears the next time the window is shown

    def _offer_update(self) -> None:
        upd, self._pending_update = self._pending_update, None
        if not upd:
            return
        self.cfg.update_notified = upd.version
        self.cfg.save()
        box = QMessageBox(self.window)
        box.setIcon(QMessageBox.Icon.Information)
        box.setWindowTitle("Update available")
        box.setText(f"Gesit {upd.version} is available. You have {__version__}.")
        info = ("If you installed Gesit from the AUR or a .deb, update it with your package manager.\n"
                "On Windows and for the AppImage, download the new version from the release page.")
        box.setInformativeText(info)
        if upd.notes:
            box.setDetailedText(upd.notes)
        box.setTextFormat(Qt.TextFormat.PlainText)
        open_btn = box.addButton("Open release page", QMessageBox.ButtonRole.AcceptRole)
        box.addButton("Later", QMessageBox.ButtonRole.RejectRole)
        box.finished.connect(lambda _r, b=box, o=open_btn, u=upd:
                             QDesktopServices.openUrl(QUrl(u.url)) if b.clickedButton() is o else None)
        self._keep(box)
        box.show()

    # --------------------------------------------------------- browser integration
    def _offer_integration(self) -> None:
        from ..hostinstall import is_integrated

        if self.cfg.integration_prompted or is_integrated():
            return
        self.cfg.integration_prompted = True
        self.cfg.save()
        ask = QMessageBox.question(
            self.window, "Browser integration",
            "Set up browser integration now?\n\nThis lets Gesit take over downloads and videos from Chrome, "
            "Chromium, Brave, Edge, Vivaldi and Firefox. You can do it later from Tasks > Set up browser integration.",
        )
        if ask == QMessageBox.StandardButton.Yes:
            self.setup_integration()

    def setup_integration(self) -> None:
        from .. import hostinstall

        try:
            res = hostinstall.setup()
        except OSError as e:
            QMessageBox.warning(self.window, "Browser integration", f"Setup failed: {e}")
            return
        text = describe_setup(res)
        if not (res["browsers"] or res["flatpak"]):
            text += "\n\nNo supported browser profile was found. Start your browser once, then try again."
        else:
            text += ("\n\nLast step: in your browser open the extensions page, enable Developer mode, choose "
                     "'Load unpacked' and select the folder above. (This step disappears once the extension is "
                     "published in the browser store.)")
        box = QMessageBox(QMessageBox.Icon.Information, "Browser integration", text,
                          QMessageBox.StandardButton.Ok, self.window)
        box.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        box.exec()

    def explain_hidden_once(self) -> None:
        """Without a tray icon the user needs to know where Gesit went and how to reach it again."""
        if self.cfg.hidden_hint_shown:
            return
        self.cfg.hidden_hint_shown = True
        self.cfg.save()
        box = QMessageBox(
            QMessageBox.Icon.Information, "Gesit keeps running",
            "Gesit is still running in the background so your browser can send downloads to it.\n\n"
            "• To show the window again, open Gesit from the app menu (or run: gesit).\n"
            "• To quit completely, use Tasks > Exit.\n\n"
            "Tip: install gnome-shell-extension-appindicator to get a tray icon on GNOME.",
            QMessageBox.StandardButton.Ok, None,
        )
        box.setWindowIcon(icons.icon("app"))
        QTimer.singleShot(0, box.show)
        self._keep(box)

    def toggle_main(self) -> None:
        if self.window.isVisible() and not self.window.isMinimized():
            self.window.hide()
        else:
            self.show_main()

    def _keep(self, dlg) -> None:
        self._dialogs.add(dlg)
        dlg.finished.connect(lambda _r, d=dlg: self._dialogs.discard(d))

    def about_dialog(self) -> None:
        AboutDialog(self.window).exec()

    def options_dialog(self) -> None:
        dlg = OptionsDialog(self.cfg, self.window)
        if dlg.exec():
            self.cfg.save()
            self.manager.apply_config()

    def scheduler_dialog(self) -> None:
        dlg = SchedulerDialog(self.cfg, self.window)
        if dlg.exec():
            self.cfg.save()
            self.manager.apply_config()
            if dlg.start_now:
                self.manager.start_queue()

    def set_global_limit(self, kbps: int) -> None:
        self.cfg.speed_limit_kbps = kbps
        self.cfg.save()
        self.manager.apply_config()

    # ------------------------------------------------------------ add dialogs
    def add_url_dialog(self, prefill: str = "") -> None:
        dlg = AddURLDialog(self.window, prefill)
        if not dlg.exec():
            return
        v = dlg.values()
        v["_from_add_dialog"] = True
        if dlg.action == "later":
            self._add_item(v, start=False)
        else:
            self.open_file_info(v)

    def open_file_info(self, info: dict) -> None:
        dlg = FileInfoDialog(self.manager, self.cfg, info, None)
        dlg.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self._keep(dlg)

        def done(result: int, d=dlg):
            if result and d.action:
                self._add_item(d.values(), start=d.action == "start")

        dlg.finished.connect(done)
        dlg.show()
        dlg.raise_()
        dlg.activateWindow()

    def _add_item(self, v: dict, start: bool) -> Item:
        item = self.manager.add(
            v["url"], filename=v.get("filename", ""), save_dir=v.get("save_dir", ""),
            category=v.get("category", ""), referer=v.get("referer", ""), page_url=v.get("page_url", ""),
            cookies=v.get("cookies", ""), user_agent=v.get("user_agent", ""), headers=v.get("headers"),
            auth_user=v.get("auth_user", ""), auth_pass=v.get("auth_pass", ""),
            description=v.get("description", ""), kind=v.get("kind", ""), start=start,
            total=int(v.get("filesize") or -1),
        )
        if start and self.cfg.show_progress_dialog:
            self.show_progress(item.id)
        return item

    def links_from_clipboard_dialog(self) -> None:
        urls = list(dict.fromkeys(URL_RE.findall(QGuiApplication.clipboard().text())))
        if not urls:
            QMessageBox.information(
                self.window, "Download all links",
                "No links found in the clipboard.\nCopy some text containing links, then try again.\n"
                "(In the browser, right-click a page > \"Download all links with Gesit\".)",
            )
            return
        self.links_dialog(urls)

    def links_dialog(self, urls: list[str], extra: dict | None = None) -> None:
        dlg = LinksDialog(urls, self.window if self.window.isVisible() else None)
        dlg.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self._keep(dlg)

        def done(result: int, d=dlg):
            if not (result and d.action):
                return
            for u in d.selected():
                self.manager.add(u, start=False, **(extra or {}))
            if d.action == "start":
                self.manager.start_queue()

        dlg.finished.connect(done)
        dlg.show()
        dlg.raise_()

    # ----------------------------------------------------------------- events
    def on_capture(self, info: dict) -> None:
        self.open_file_info(info)

    def on_capture_many(self, urls: list, extra: dict) -> None:
        self.links_dialog(urls, extra)

    def show_progress(self, item_id: int) -> None:
        if item_id in self.progress:
            d = self.progress[item_id]
            d.show()
            d.raise_()
            return
        it = self.manager.get(item_id)
        if not it or it.status == COMPLETE:
            return
        post = self.post.setdefault(item_id, {"dialog": self.cfg.show_complete_dialog})
        dlg = ProgressDialog(self.manager, it, post, None)
        dlg.closed.connect(lambda i: self.progress.pop(i, None))
        self.progress[item_id] = dlg
        dlg.show()

    def update_progress_dialogs(self, items: dict[int, Item]) -> None:
        for i, dlg in list(self.progress.items()):
            it = items.get(i)
            if it is None:
                dlg.close()
            else:
                dlg.refresh(it)

    def on_event(self, kind: str, item: Item | None) -> None:
        if kind in ("added", "removed", "changed", "queue"):
            return  # the 250 ms refresh timer picks these up
        if kind == "request_exit":
            self.quit()
        elif kind == "complete" and item:
            dlg = self.progress.pop(item.id, None)
            if dlg:
                dlg.close()
            post = self.post.pop(item.id, {})
            if post.get("open"):
                from .widgets import open_path

                open_path(item.path)
            if post.get("dialog", self.cfg.show_complete_dialog):
                cd = CompleteDialog(item, None)
                cd.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
                self._keep(cd)
                cd.finished.connect(lambda _r, d=cd: self._complete_closed(d))
                cd.show()
            elif self.cfg.notify_on_complete:
                self.notify("Download complete", item.filename)
            if post.get("exit"):
                self.quit()
            elif post.get("poweroff"):
                self._confirm_poweroff()
        elif kind == "error" and item:
            if item.id in self.progress:
                return  # the dialog shows the error itself
            self.notify("Download failed", f"{item.filename or item.url}\n{item.error}")

    def _complete_closed(self, dlg: CompleteDialog) -> None:
        if dlg.dont_show.isChecked():
            self.cfg.show_complete_dialog = False
            self.cfg.save()

    def _confirm_poweroff(self) -> None:
        box = QMessageBox(QMessageBox.Icon.Warning, "Gesit", "The computer will turn off in 30 seconds.",
                          QMessageBox.StandardButton.Cancel, None)
        box.setWindowIcon(icons.icon("app"))
        QTimer.singleShot(30000, lambda: (box.isVisible() and (box.accept(), osutil.power_action("poweroff"))))
        box.show()

    # -------------------------------------------------------------------- IPC
    async def _ipc_handler(self, msg: dict) -> dict:
        cmd = msg.get("cmd")
        cfg = self.cfg
        if cmd == "ping":
            return {"ok": True, "version": __version__}
        if cmd == "show":
            self.bridge.show_window.emit()
            return {"ok": True}
        if cmd == "quit":
            self.bridge.quit_requested.emit()
            return {"ok": True}
        if cmd == "get_config":
            return {
                "ok": True, "version": __version__, "enabled": cfg.capture_enabled,
                "extensions": cfg.capture_extensions, "min_size_kb": cfg.capture_min_size_kb,
                "exclude_hosts": cfg.exclude_hosts,
            }
        if cmd == "add":
            info = self._clean(msg)
            if not info:
                return {"ok": False, "error": "invalid url"}
            if msg.get("dialog", True) and cfg.show_dialog_on_capture:
                self.bridge.capture.emit(info)
                return {"ok": True, "dialog": True}
            item = self.manager.add(
                info["url"], filename=info.get("filename", ""), referer=info.get("referer", ""),
                page_url=info.get("page_url", ""), cookies=info.get("cookies", ""),
                user_agent=info.get("user_agent", ""), headers=info.get("headers"),
                kind=info.get("kind", ""), total=int(info.get("filesize") or -1),
            )
            self.bridge.event.emit("changed", None)
            return {"ok": True, "id": item.id}
        if cmd == "add_many":
            extra = {k: v for k, v in self._clean({**msg, "url": "http://x"}).items()
                     if k in ("referer", "cookies", "user_agent", "page_url")}
            urls = [u for u in (msg.get("urls") or []) if isinstance(u, str) and u.lower().startswith(("http://", "https://"))]
            if not urls:
                return {"ok": False, "error": "no links"}
            self.bridge.capture_many.emit(urls, extra)
            return {"ok": True, "count": len(urls)}
        return {"ok": False, "error": f"unknown command: {cmd}"}

    @staticmethod
    def _clean(msg: dict) -> dict | None:
        """Validate and sanitise a request that came from a browser."""
        url = msg.get("url")
        if not isinstance(url, str) or not url.lower().startswith(("http://", "https://")):
            return None
        out: dict = {"url": url}
        for k in ("referer", "cookies", "user_agent", "page_url", "kind"):
            if isinstance(msg.get(k), str):
                out[k] = msg[k]
        if out.get("kind") not in (None, "http", "hls", "ytdlp"):
            out.pop("kind")
        if isinstance(msg.get("filename"), str) and msg["filename"].strip():
            out["filename"] = util.sanitize_filename(msg["filename"])
        if isinstance(msg.get("filesize"), (int, float)) and msg["filesize"] > 0:
            out["filesize"] = int(msg["filesize"])
        if isinstance(msg.get("headers"), dict):
            out["headers"] = {str(k): str(v) for k, v in msg["headers"].items()}
        return out

    # ------------------------------------------------------------------- quit
    def quit(self) -> None:
        if self.quitting:
            return
        self.quitting = True
        try:
            asyncio.run_coroutine_threadsafe(self.ipc.stop(), self.manager.loop).result(2)
        except Exception:  # noqa: BLE001
            pass
        self.cfg.save()
        self.manager.shutdown()
        if self.tray:
            self.tray.hide()
        self.qapp.quit()


def describe_setup(res: dict) -> str:
    names = res["browsers"] + [n for n, _ in res["flatpak"]]
    lines = ["Browser integration set up for: " + (", ".join(names) or "no supported browser found"),
             "Host launcher: " + res["host"]]
    for name, ext in res["flatpak"]:
        lines.append(f"  {name}: the sandbox may only access Gesit's socket dir (xdg-run/gesit).")
        if ext:
            lines.append(f"    load the extension from THIS folder (visible inside the sandbox): {ext}")
    if res["flatpak"]:
        lines.append("Fully restart Flatpak browsers (flatpak kill <app-id>) and keep Gesit running: "
                     "a sandboxed browser cannot start it.")
    if res["extension_dir"]:
        lines.append("Extension folders (Chrome/Brave/Edge: chrome, Firefox: firefox): " + res["extension_dir"])
    return "\n".join(lines)


def enable_autostart() -> str:
    return osutil.enable_autostart()


CLI_FLAGS = ("--version", "--install-host", "--uninstall-host", "--quit", "--enable-autostart", "--disable-autostart")


def _attach_console() -> None:
    """A windowed Windows build has no stdout; reuse the parent terminal so CLI flags can print."""
    if sys.platform != "win32" or sys.stdout is not None:
        return
    try:
        import ctypes

        if ctypes.windll.kernel32.AttachConsole(-1):  # ATTACH_PARENT_PROCESS
            sys.stdout = open("CONOUT$", "w", encoding="utf-8")
            sys.stderr = sys.stdout
    except (OSError, AttributeError):
        pass


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if any(a in CLI_FLAGS for a in argv):
        _attach_console()
    if "--enable-autostart" in argv:
        print(osutil.enable_autostart())
        return 0
    if "--disable-autostart" in argv:
        osutil.disable_autostart()
        print("Autostart disabled.")
        return 0
    if "--version" in argv:
        print(f"Gesit {__version__}")
        return 0
    if "--install-host" in argv or "--uninstall-host" in argv:
        from .. import hostinstall

        if "--uninstall-host" in argv:
            gone = hostinstall.uninstall() + hostinstall.uninstall_flatpak()
            print("Removed native host for:", ", ".join(gone) or "nothing")
            return 0
        extra = [argv[i + 1] for i, a in enumerate(argv) if a == "--extension-id" and i + 1 < len(argv)]
        res = hostinstall.setup(extra)
        print(describe_setup(res))
        if "--autostart" in argv:
            print(enable_autostart())
        return 0
    background = "--background" in argv
    urls = [a for a in argv if a.lower().startswith(("http://", "https://"))]

    if "--quit" in argv:
        try:
            ipc.send({"cmd": "quit"})
        except OSError:
            print("Gesit is not running")
        return 0

    if ipc.is_running():  # single instance: hand over to the running one
        try:
            for u in urls:
                ipc.send({"cmd": "add", "url": u, "dialog": True})
            if not urls and not background:
                ipc.send({"cmd": "show"})
        except OSError:
            pass
        return 0

    QGuiApplication.setDesktopFileName("gesit")
    qapp = QApplication(sys.argv[:1])
    qapp.setApplicationName("Gesit")
    qapp.setQuitOnLastWindowClosed(False)
    qapp.setWindowIcon(icons.icon("app"))
    if qapp.style().objectName().lower() == "windows":
        qapp.setStyle("Fusion")
    app = GesitApp(qapp, background)
    for u in urls:
        app.on_capture({"url": u})
    return qapp.exec()


def main_cli() -> None:
    sys.exit(main())
