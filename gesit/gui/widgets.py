"""Reusable widgets and helpers."""

from __future__ import annotations

import os
import shutil
import subprocess
from concurrent.futures import Future
from typing import Callable

from ..core import osutil
from PySide6.QtCore import QRectF, QSize, Qt, QTimer, QUrl
from PySide6.QtGui import QColor, QDesktopServices, QFont, QLinearGradient, QPainter, QPen
from PySide6.QtWidgets import QWidget


class SegmentBar(QWidget):
    """Segmented progress bar: one block per connection segment."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(22)
        self._total = 0
        self._segments: list[tuple[int, int, int]] = []
        self._downloaded = 0
        self._complete = False

    def sizeHint(self) -> QSize:
        return QSize(300, 24)

    def set_data(self, total: int, segments: list, downloaded: int, complete: bool = False) -> None:
        self._total, self._segments = total if total and total > 0 else 0, list(segments)
        self._downloaded, self._complete = downloaded, complete
        self.update()

    def paintEvent(self, _ev) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        pal = self.palette()
        r = QRectF(0.5, 0.5, self.width() - 1, self.height() - 1)
        p.fillRect(r, pal.base())
        p.setPen(QPen(pal.mid().color()))
        p.drawRect(r)
        inner = r.adjusted(2, 2, -2, -2)
        blue_top, blue_bot = QColor("#5aa0f2"), QColor("#2a63c4")
        if self._complete:
            blue_top, blue_bot = QColor("#5fcf86"), QColor("#2e9e4f")
        grad = QLinearGradient(inner.topLeft(), inner.bottomLeft())
        grad.setColorAt(0, blue_top)
        grad.setColorAt(1, blue_bot)
        w = inner.width()
        if self._complete:
            p.fillRect(inner, grad)
        elif self._total and self._segments:
            for start, pos, _end in self._segments:
                if pos <= start:
                    continue
                x0 = inner.left() + w * start / self._total
                x1 = inner.left() + w * pos / self._total
                p.fillRect(QRectF(x0, inner.top(), max(1.0, x1 - x0), inner.height()), grad)
                p.setPen(QPen(QColor(255, 255, 255, 140)))
                p.drawLine(int(x0), int(inner.top()), int(x0), int(inner.bottom()))
        elif self._total:
            p.fillRect(QRectF(inner.left(), inner.top(), w * self._downloaded / self._total, inner.height()), grad)
        pct = 100.0 if self._complete else (self._downloaded * 100.0 / self._total if self._total else 0)
        f = QFont(self.font())
        f.setBold(True)
        p.setFont(f)
        text = f"{pct:.1f}%" if self._total or self._complete else ""
        rect = self.rect()
        p.setPen(QColor(255, 255, 255, 230))  # halo keeps the text legible over blue and white alike
        for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
            p.drawText(rect.translated(dx, dy), Qt.AlignmentFlag.AlignCenter, text)
        p.setPen(QColor("#10243f"))
        p.drawText(rect, Qt.AlignmentFlag.AlignCenter, text)


def watch_future(fut: Future, cb: Callable, parent) -> None:
    """Call cb(result, error) on the Qt thread when `fut` completes."""
    timer = QTimer(parent)
    timer.setInterval(80)

    def poll():
        if not fut.done():
            return
        timer.stop()
        timer.deleteLater()
        try:
            cb(fut.result(), None)
        except Exception as e:  # noqa: BLE001
            cb(None, e)

    timer.timeout.connect(poll)
    timer.start()


def open_path(path: str) -> None:
    QDesktopServices.openUrl(QUrl.fromLocalFile(path))


def open_folder(path: str) -> None:
    """Open the containing folder, selecting the file when the file manager supports it."""
    cmd = osutil.reveal_command(path)
    if cmd:  # Windows Explorer
        subprocess.Popen(cmd)
        return
    if os.path.isfile(path) and shutil.which("dbus-send"):
        try:
            subprocess.run(
                ["dbus-send", "--session", "--dest=org.freedesktop.FileManager1", "--type=method_call",
                 "/org/freedesktop/FileManager1", "org.freedesktop.FileManager1.ShowItems",
                 f"array:string:{QUrl.fromLocalFile(path).toString()}", "string:"],
                timeout=2, check=True, capture_output=True,
            )
            return
        except (subprocess.SubprocessError, OSError):
            pass
    open_path(os.path.dirname(path) if os.path.isfile(path) else path)
