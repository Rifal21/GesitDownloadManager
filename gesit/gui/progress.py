"""Per-download progress dialog (status, speed limiter, completion actions)."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QFormLayout, QHBoxLayout, QHeaderView, QLabel, QPushButton, QSpinBox,
    QTableWidget, QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget,
)

from ..core import util
from ..core.models import COMPLETE, CONNECTING, DOWNLOADING, ERROR, QUEUED, STOPPED, Item
from . import icons
from .widgets import SegmentBar


class ProgressDialog(QDialog):
    closed = Signal(int)

    def __init__(self, manager, item: Item, post: dict, parent=None):
        super().__init__(parent)
        self.manager, self.item_id, self.post = manager, item.id, post
        self.setWindowIcon(icons.icon("resume"))
        self.setMinimumWidth(560)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        lay = QVBoxLayout(self)
        self.tabs = QTabWidget()
        lay.addWidget(self.tabs)

        # ---- tab 1: download status
        st = QWidget()
        sl = QVBoxLayout(st)
        self.url_label = QLabel(item.url)
        self.url_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.url_label.setWordWrap(True)
        sl.addWidget(self.url_label)
        self.status = QLabel()
        self.bar = SegmentBar()
        sl.addWidget(self.bar)
        form = QFormLayout()
        self.f_status, self.f_size, self.f_done = QLabel(), QLabel(), QLabel()
        self.f_rate, self.f_left, self.f_resume = QLabel(), QLabel(), QLabel()
        for k, w in (("Status", self.f_status), ("File size", self.f_size), ("Downloaded", self.f_done),
                     ("Transfer rate", self.f_rate), ("Time left", self.f_left), ("Resume capability", self.f_resume)):
            form.addRow(k, w)
        sl.addLayout(form)
        self.tabs.addTab(st, "Download status")

        # ---- tab 2: speed limiter
        sp = QWidget()
        spl = QVBoxLayout(sp)
        self.use_limit = QCheckBox("Use speed limiter")
        self.use_limit.setChecked(item.speed_limit_kbps > 0)
        row = QHBoxLayout()
        row.addWidget(QLabel("Maximum download speed:"))
        self.limit = QSpinBox()
        self.limit.setRange(1, 10_000_000)
        self.limit.setValue(item.speed_limit_kbps or 512)
        self.limit.setSuffix(" KBytes/sec")
        self.limit.setEnabled(self.use_limit.isChecked())
        row.addWidget(self.limit)
        row.addStretch()
        spl.addWidget(self.use_limit)
        spl.addLayout(row)
        spl.addStretch()
        self.use_limit.toggled.connect(self.limit.setEnabled)
        self.use_limit.toggled.connect(lambda _c: self._apply_limit())
        self.limit.editingFinished.connect(self._apply_limit)
        self.tabs.addTab(sp, "Speed Limiter")

        # ---- tab 3: options on completion
        oc = QWidget()
        ol = QVBoxLayout(oc)
        self.c_dialog = QCheckBox("Show download complete dialog")
        self.c_open = QCheckBox("Open file when done")
        self.c_exit = QCheckBox("Exit Gesit when done")
        self.c_off = QCheckBox("Turn off computer when done")
        self.c_dialog.setChecked(post.get("dialog", True))
        for k, cb in (("dialog", self.c_dialog), ("open", self.c_open), ("exit", self.c_exit), ("poweroff", self.c_off)):
            cb.setChecked(post.get(k, cb.isChecked()))
            cb.toggled.connect(lambda v, k=k: self.post.__setitem__(k, v))
            ol.addWidget(cb)
        ol.addStretch()
        self.tabs.addTab(oc, "Options on completion")

        # ---- details table
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["N.", "Downloaded", "Info"])
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.table.setMinimumHeight(140)
        self.table.setVisible(False)
        lay.addWidget(self.table)

        btns = QHBoxLayout()
        self.b_details = QPushButton("Show details >>")
        self.b_details.clicked.connect(self._toggle_details)
        self.b_pause = QPushButton("Pause")
        self.b_pause.clicked.connect(self._pause_resume)
        self.b_close = QPushButton("Close")
        self.b_close.clicked.connect(self.close)
        self.b_cancel = QPushButton("Cancel")
        self.b_cancel.clicked.connect(self._cancel)
        btns.addWidget(self.b_details)
        btns.addStretch()
        for b in (self.b_pause, self.b_cancel, self.b_close):
            btns.addWidget(b)
        lay.addLayout(btns)
        self.refresh(item)

    # ---------------------------------------------------------------- actions
    def _apply_limit(self) -> None:
        kbps = self.limit.value() if self.use_limit.isChecked() else 0
        self.manager.update_item(self.item_id, speed_limit_kbps=kbps)

    def _toggle_details(self) -> None:
        show = not self.table.isVisible()
        self.table.setVisible(show)
        self.b_details.setText("<< Hide details" if show else "Show details >>")
        self.adjustSize()

    def _pause_resume(self) -> None:
        it = self.manager.get(self.item_id)
        if not it:
            return
        if it.status in (CONNECTING, DOWNLOADING):
            self.manager.stop_item(self.item_id)
        else:
            self.manager.start_item(self.item_id)

    def _cancel(self) -> None:
        self.manager.stop_item(self.item_id)
        self.close()

    def closeEvent(self, ev) -> None:  # noqa: N802
        self.closed.emit(self.item_id)
        super().closeEvent(ev)

    # ---------------------------------------------------------------- refresh
    def refresh(self, it: Item) -> None:
        total = it.total if it.total and it.total > 0 else 0
        self.setWindowTitle(f"{it.percent:.0f}%  {it.filename or it.url}" if it.status != COMPLETE else f"Complete  {it.filename}")
        self.bar.set_data(total, it.segments, it.downloaded, it.status == COMPLETE)
        label = {
            CONNECTING: "Connecting...", DOWNLOADING: "Receiving data...", STOPPED: "Paused",
            QUEUED: "Queued", COMPLETE: "Download complete", ERROR: f"Error: {it.error}",
        }.get(it.status, it.status)
        self.f_status.setText(label)
        self.f_size.setText(util.human_size(it.total) if total else "Unknown")
        pct = f"  ({it.percent:.2f}%)" if total else ""
        self.f_done.setText(util.human_size(it.downloaded) + pct)
        self.f_rate.setText(util.human_speed(it.speed) or "-")
        self.f_left.setText(util.human_time(it.eta) or "-")
        self.f_resume.setText("Yes" if it.resumable else "No")
        active = it.status in (CONNECTING, DOWNLOADING)
        self.b_pause.setText("Pause" if active else "Resume")
        self.b_pause.setVisible(it.status != COMPLETE)
        self.b_cancel.setVisible(it.status != COMPLETE)
        if self.table.isVisible():
            conns = it.connections
            self.table.setRowCount(len(conns))
            for i, (done, remaining, state) in enumerate(conns):
                for c, text in enumerate((str(i + 1), util.human_size(done), state if remaining else "Complete")):
                    cell = self.table.item(i, c)
                    if cell is None:
                        cell = QTableWidgetItem()
                        self.table.setItem(i, c, cell)
                    cell.setText(text)
