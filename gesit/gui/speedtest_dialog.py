"""Network speed test window (Tasks > Network speed test)."""

from __future__ import annotations

import threading
import time

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import (
    QDialog, QGridLayout, QHBoxLayout, QLabel, QProgressBar, QPushButton, QVBoxLayout,
)

from ..core import speedtest
from ..core.config import Config
from ..core.speedtest import Cancelled, Result, SpeedTest, SpeedTestError, format_rate
from . import icons

# share of the progress bar each phase takes
_WEIGHT = {"ping": (0.0, 0.1), "download": (0.1, 0.6), "upload": (0.6, 1.0)}


class _Signals(QObject):
    progress = Signal(str, object, float)   # phase, Mbit/s or None, fraction of the phase
    finished = Signal(object, str)          # Result | None, error text ("" when cancelled or ok)


class SpeedTestDialog(QDialog):
    def __init__(self, cfg: Config, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self.setWindowTitle("Network speed test")
        self.setWindowIcon(icons.icon("app"))
        self.setMinimumWidth(520)
        self._test: SpeedTest | None = None
        self._thread: threading.Thread | None = None
        self._alive = True
        self.sig = _Signals(self)
        self.sig.progress.connect(self._on_progress)
        self.sig.finished.connect(self._on_finished)

        lay = QVBoxLayout(self)
        grid = QGridLayout()
        self.down = self._stat(grid, 0, "Download")
        self.up = self._stat(grid, 1, "Upload")
        self.ping = self._stat(grid, 2, "Ping")
        lay.addLayout(grid)
        self.bar = QProgressBar()
        self.bar.setRange(0, 1000)
        self.bar.setTextVisible(False)
        lay.addWidget(self.bar)
        self.status = QLabel()
        self.status.setWordWrap(True)
        lay.addWidget(self.status)
        self.last = QLabel()
        self.last.setWordWrap(True)
        self.last.setEnabled(False)
        lay.addWidget(self.last)
        note = QLabel(
            f"The test uses your connection at full speed for about 15 seconds, with servers of Cloudflare "
            f"({speedtest.BASE_URL.split('//')[1]}). It only runs when you press Start."
        )
        note.setWordWrap(True)
        note.setEnabled(False)
        lay.addWidget(note)
        row = QHBoxLayout()
        row.addStretch()
        self.start = QPushButton("Start")
        self.start.setDefault(True)
        self.start.clicked.connect(self._toggle)
        close = QPushButton("Close")
        close.clicked.connect(self.close)
        row.addWidget(self.start)
        row.addWidget(close)
        lay.addLayout(row)
        self._show_last()
        self.status.setText("Press Start to measure your internet speed.")

    # ---------------------------------------------------------------- layout
    def _stat(self, grid: QGridLayout, col: int, title: str) -> QLabel:
        head = QLabel(title)
        head.setAlignment(Qt.AlignmentFlag.AlignCenter)
        head.setEnabled(False)
        value = QLabel("-")
        value.setAlignment(Qt.AlignmentFlag.AlignCenter)
        f = value.font()
        f.setPointSize(f.pointSize() + 8)
        f.setBold(True)
        value.setFont(f)
        sub = QLabel("")
        sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        sub.setEnabled(False)
        grid.addWidget(head, 0, col)
        grid.addWidget(value, 1, col)
        grid.addWidget(sub, 2, col)
        value.sub = sub  # type: ignore[attr-defined]
        return value

    def _set(self, label: QLabel, big: str, small: str = "") -> None:
        label.setText(big)
        label.sub.setText(small)  # type: ignore[attr-defined]

    def _show_last(self) -> None:
        d = self.cfg.last_speedtest or {}
        if not d.get("ts"):
            self.last.hide()
            return
        when = time.strftime("%d %b %Y %H:%M", time.localtime(d["ts"]))
        up = format_rate(d["up"] * 1e6) if d.get("up") else "-"
        self.last.setText(
            f"Last test, {when}: download {format_rate(d['down'] * 1e6)}, upload {up}, ping {d['ping']:.0f} ms"
        )
        self.last.show()

    # ---------------------------------------------------------------- running
    def _toggle(self) -> None:
        if self._test:
            self._test.cancel()
            self.start.setEnabled(False)
            self.status.setText("Stopping...")
        else:
            self._begin()

    def _begin(self) -> None:
        for lab in (self.down, self.up, self.ping):
            self._set(lab, "-")
        self.bar.setValue(0)
        self.start.setText("Stop")
        self.status.setText("Testing latency...")
        test = SpeedTest(proxy=self.cfg.proxy_url(), on_progress=self._progress_from_thread)
        self._test = test

        def work() -> None:
            res, err = None, ""
            try:
                res = test.run()
            except Cancelled:
                pass
            except SpeedTestError as e:
                err = str(e)
            except Exception as e:  # noqa: BLE001 - the worker must never die silently
                err = f"{type(e).__name__}: {e}"
            self._emit(self.sig.finished, res, err)

        self._thread = threading.Thread(target=work, name="gesit-speedtest", daemon=True)
        self._thread.start()

    def _emit(self, signal, *args) -> None:
        if self._alive:
            try:
                signal.emit(*args)
            except RuntimeError:  # the dialog was destroyed while the worker was finishing
                pass

    def _progress_from_thread(self, phase: str, mbps: float | None, frac: float) -> None:
        self._emit(self.sig.progress, phase, mbps, frac)

    def _on_progress(self, phase: str, mbps, frac: float) -> None:
        if phase in _WEIGHT:
            lo, hi = _WEIGHT[phase]
            self.bar.setValue(int((lo + (hi - lo) * frac) * 1000))
        if phase == "download" and mbps is not None:
            self._set(self.down, f"{mbps:.1f}", "Mbit/s")
            self.status.setText("Testing download speed...")
        elif phase == "upload" and mbps is not None:
            self._set(self.up, f"{mbps:.1f}", "Mbit/s")
            self.status.setText("Testing upload speed...")
        elif phase == "ping":
            self.status.setText("Testing latency...")

    def _on_finished(self, res: Result | None, err: str) -> None:
        self._test = None
        self.start.setEnabled(True)
        self.start.setText("Start")
        if res is None:
            self.bar.setValue(0)
            self.status.setText(err or "Stopped.")
            return
        self.bar.setValue(1000)
        self._set(self.down, f"{res.download_mbps:.1f}", f"Mbit/s  ({res.download_bps / 8e6:.1f} MB/s)")
        if res.upload_bps is not None:
            self._set(self.up, f"{res.upload_mbps:.1f}", f"Mbit/s  ({res.upload_bps / 8e6:.1f} MB/s)")
        else:
            self._set(self.up, "-", "not measured")
        self._set(self.ping, f"{res.ping_ms:.0f}", f"ms  (jitter {res.jitter_ms:.1f} ms)")
        self.status.setText("Done." if not res.note else f"Done. {res.note}")
        self.cfg.last_speedtest = {
            "ts": time.time(), "down": res.download_mbps, "up": res.upload_mbps, "ping": res.ping_ms,
        }
        self.cfg.save()
        self.last.hide()  # the numbers above are the newest ones; the line is for the next time the window opens

    # ---------------------------------------------------------------- closing
    def done(self, r: int) -> None:
        self._alive = False
        if self._test:
            self._test.cancel()
            if self._thread:
                self._thread.join(3)
        super().done(r)
