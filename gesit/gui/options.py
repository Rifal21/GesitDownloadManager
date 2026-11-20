"""Options dialog (General, Save To, Connection, Proxy, Site Logins) and Scheduler."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QGroupBox,
    QHBoxLayout, QHeaderView, QLabel, QLineEdit, QPlainTextEdit, QPushButton, QRadioButton, QSpinBox,
    QTableWidget, QTableWidgetItem, QTabWidget, QTimeEdit, QVBoxLayout, QWidget,
)
from PySide6.QtCore import QTime

from ..core.config import CATEGORIES, Config
from . import icons


def _dir_row(edit: QLineEdit, parent) -> QHBoxLayout:
    row = QHBoxLayout()
    row.addWidget(edit)
    b = QPushButton("Browse...")

    def pick():
        d = QFileDialog.getExistingDirectory(parent, "Select folder", edit.text())
        if d:
            edit.setText(d)

    b.clicked.connect(pick)
    row.addWidget(b)
    return row


class OptionsDialog(QDialog):
    def __init__(self, cfg: Config, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self.setWindowTitle("Options")
        self.setWindowIcon(icons.icon("options"))
        self.setMinimumSize(620, 520)
        lay = QVBoxLayout(self)
        tabs = QTabWidget()
        lay.addWidget(tabs)
        tabs.addTab(self._general(), "General")
        tabs.addTab(self._save_to(), "Save To")
        tabs.addTab(self._connection(), "Connection")
        tabs.addTab(self._proxy(), "Proxy")
        tabs.addTab(self._logins(), "Site Logins")
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self._ok)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    # ------------------------------------------------------------------ tabs
    def _general(self) -> QWidget:
        c, w = self.cfg, QWidget()
        lay = QVBoxLayout(w)
        box = QGroupBox("Browser integration")
        bl = QVBoxLayout(box)
        self.capture = QCheckBox("Capture downloads from browsers (needs the Gesit browser extension)")
        self.capture.setChecked(c.capture_enabled)
        self.show_info = QCheckBox("Show the Download File Info dialog for captured downloads")
        self.show_info.setChecked(c.show_dialog_on_capture)
        bl.addWidget(self.capture)
        bl.addWidget(self.show_info)
        bl.addWidget(QLabel("Capture downloads of these file types (extensions, space separated):"))
        self.exts = QPlainTextEdit(" ".join(c.capture_extensions))
        self.exts.setFixedHeight(80)
        bl.addWidget(self.exts)
        form = QFormLayout()
        self.min_size = QSpinBox()
        self.min_size.setRange(0, 10_000_000)
        self.min_size.setSuffix(" KB")
        self.min_size.setValue(c.capture_min_size_kb)
        form.addRow("Only capture files larger than", self.min_size)
        self.exclude = QLineEdit(" ".join(c.exclude_hosts))
        self.exclude.setPlaceholderText("example.com intranet.local")
        form.addRow("Don't capture from sites", self.exclude)
        bl.addLayout(form)
        lay.addWidget(box)
        box2 = QGroupBox("Behaviour")
        b2 = QVBoxLayout(box2)
        self.cb = {}
        for key, text in (
            ("start_minimized", "Start hidden in the background"),
            ("close_to_tray", "Closing the window keeps Gesit running in the background (browser integration needs it)"),
            ("show_progress_dialog", "Show the progress dialog when a download starts"),
            ("show_complete_dialog", "Show the download complete dialog"),
            ("notify_on_complete", "Desktop notification when a download finishes"),
            ("check_updates", "Check for new Gesit versions (asks github.com at most once a day)"),
        ):
            cb = QCheckBox(text)
            cb.setChecked(getattr(c, key))
            self.cb[key] = cb
            b2.addWidget(cb)
        lay.addWidget(box2)
        lay.addStretch()
        return w

    def _save_to(self) -> QWidget:
        c, w = self.cfg, QWidget()
        lay = QVBoxLayout(w)
        lay.addWidget(QLabel("Default download folder (each category gets its own subfolder):"))
        self.root = QLineEdit(c.download_root)
        lay.addLayout(_dir_row(self.root, self))
        lay.addWidget(QLabel("Folder per category (leave empty for the default subfolder):"))
        form = QFormLayout()
        self.cat_dirs: dict[str, QLineEdit] = {}
        for cat in CATEGORIES:
            e = QLineEdit(c.category_dirs.get(cat, ""))
            e.setPlaceholderText(c.category_dir(cat) if cat not in c.category_dirs else "")
            self.cat_dirs[cat] = e
            form.addRow(cat, _dir_row(e, self))
        lay.addLayout(form)
        lay.addStretch()
        return w

    def _connection(self) -> QWidget:
        c, w = self.cfg, QWidget()
        form = QFormLayout(w)
        self.conns = QSpinBox()
        self.conns.setRange(1, 32)
        self.conns.setValue(c.connections)
        form.addRow("Default max. connections per download", self.conns)
        self.max_active = QSpinBox()
        self.max_active.setRange(1, 32)
        self.max_active.setValue(c.max_active)
        form.addRow("Max. simultaneous downloads in the queue", self.max_active)
        self.retries = QSpinBox()
        self.retries.setRange(0, 100)
        self.retries.setValue(c.retries)
        form.addRow("Retries per connection", self.retries)
        self.timeout = QSpinBox()
        self.timeout.setRange(5, 300)
        self.timeout.setSuffix(" s")
        self.timeout.setValue(c.timeout)
        form.addRow("Connection timeout", self.timeout)
        self.speed = QSpinBox()
        self.speed.setRange(0, 10_000_000)
        self.speed.setSpecialValueText("Unlimited")
        self.speed.setSuffix(" KB/s")
        self.speed.setValue(c.speed_limit_kbps)
        form.addRow("Global speed limit", self.speed)
        self.ua = QLineEdit(c.user_agent)
        self.ua.setPlaceholderText("Default (Chrome on Linux)")
        form.addRow("User agent", self.ua)
        self.tls = QCheckBox("Verify HTTPS certificates")
        self.tls.setChecked(c.verify_tls)
        form.addRow("", self.tls)
        return w

    def _proxy(self) -> QWidget:
        c, w = self.cfg, QWidget()
        lay = QVBoxLayout(w)
        self.p_none, self.p_sys, self.p_man = (
            QRadioButton("No proxy"), QRadioButton("Use system proxy (http_proxy / https_proxy variables)"),
            QRadioButton("Manual proxy"),
        )
        {"none": self.p_none, "system": self.p_sys, "manual": self.p_man}.get(c.proxy_mode, self.p_sys).setChecked(True)
        for r in (self.p_none, self.p_sys, self.p_man):
            lay.addWidget(r)
        form = QFormLayout()
        self.p_type = QComboBox()
        self.p_type.addItems(["http", "socks5"])
        self.p_type.setCurrentText(c.proxy_type)
        self.p_host, self.p_port = QLineEdit(c.proxy_host), QSpinBox()
        self.p_port.setRange(0, 65535)
        self.p_port.setValue(c.proxy_port)
        self.p_user, self.p_pass = QLineEdit(c.proxy_user), QLineEdit(c.proxy_pass)
        self.p_pass.setEchoMode(QLineEdit.EchoMode.Password)
        for k, wd in (("Type", self.p_type), ("Address", self.p_host), ("Port", self.p_port),
                      ("Login", self.p_user), ("Password", self.p_pass)):
            form.addRow(k, wd)
        self.p_box = QWidget()
        self.p_box.setLayout(form)
        self.p_box.setEnabled(self.p_man.isChecked())
        self.p_man.toggled.connect(self.p_box.setEnabled)
        lay.addWidget(self.p_box)
        lay.addWidget(QLabel("SOCKS5 needs the python-socksio package."))
        lay.addStretch()
        return w

    def _logins(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.addWidget(QLabel("Credentials used automatically for these sites (HTTP basic authentication):"))
        self.logins = QTableWidget(0, 3)
        self.logins.setHorizontalHeaderLabels(["Site", "Login", "Password"])
        self.logins.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        for e in self.cfg.site_logins:
            self._add_login_row(e.get("host", ""), e.get("user", ""), e.get("password", ""))
        lay.addWidget(self.logins)
        row = QHBoxLayout()
        add, rem = QPushButton("Add"), QPushButton("Remove")
        add.clicked.connect(lambda: self._add_login_row())
        rem.clicked.connect(lambda: self.logins.removeRow(self.logins.currentRow()))
        row.addWidget(add)
        row.addWidget(rem)
        row.addStretch()
        lay.addLayout(row)
        return w

    def _add_login_row(self, host="", user="", pw="") -> None:
        r = self.logins.rowCount()
        self.logins.insertRow(r)
        self.logins.setItem(r, 0, QTableWidgetItem(host))
        self.logins.setItem(r, 1, QTableWidgetItem(user))
        pwi = QTableWidgetItem("*" * len(pw) if pw else "")
        pwi.setData(Qt.ItemDataRole.UserRole, pw)
        self.logins.setItem(r, 2, pwi)

    # ------------------------------------------------------------------ save
    def _ok(self) -> None:
        c = self.cfg
        c.capture_enabled = self.capture.isChecked()
        c.show_dialog_on_capture = self.show_info.isChecked()
        c.capture_extensions = sorted({e.strip().lstrip(".").lower() for e in self.exts.toPlainText().split() if e.strip()})
        c.capture_min_size_kb = self.min_size.value()
        c.exclude_hosts = [h.lower() for h in self.exclude.text().split() if h]
        for k, cb in self.cb.items():
            setattr(c, k, cb.isChecked())
        c.download_root = self.root.text().strip() or c.download_root
        c.category_dirs = {k: e.text().strip() for k, e in self.cat_dirs.items() if e.text().strip()}
        c.connections, c.max_active = self.conns.value(), self.max_active.value()
        c.retries, c.timeout = self.retries.value(), self.timeout.value()
        c.speed_limit_kbps, c.user_agent, c.verify_tls = self.speed.value(), self.ua.text().strip(), self.tls.isChecked()
        c.proxy_mode = "manual" if self.p_man.isChecked() else "none" if self.p_none.isChecked() else "system"
        c.proxy_type, c.proxy_host, c.proxy_port = self.p_type.currentText(), self.p_host.text().strip(), self.p_port.value()
        c.proxy_user, c.proxy_pass = self.p_user.text(), self.p_pass.text()
        logins = []
        for r in range(self.logins.rowCount()):
            host = (self.logins.item(r, 0).text() if self.logins.item(r, 0) else "").strip()
            if not host:
                continue
            pwi = self.logins.item(r, 2)
            real = pwi.data(Qt.ItemDataRole.UserRole) if pwi else ""
            shown = pwi.text() if pwi else ""
            pw = real if (real is not None and shown == "*" * len(real or "")) else shown
            logins.append({"host": host.lower(), "user": self.logins.item(r, 1).text(), "password": pw})
        c.site_logins = logins
        self.accept()


class SchedulerDialog(QDialog):
    DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

    def __init__(self, cfg: Config, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self.start_now = False
        self.setWindowTitle("Scheduler")
        self.setWindowIcon(icons.icon("scheduler"))
        self.setMinimumWidth(460)
        lay = QVBoxLayout(self)
        self.enable = QCheckBox("Enable scheduler for the main download queue")
        self.enable.setChecked(cfg.sched_enabled)
        lay.addWidget(self.enable)

        box = QGroupBox("Start / stop")
        form = QFormLayout(box)
        self.start = QTimeEdit(QTime.fromString(cfg.sched_start, "HH:mm"))
        self.start.setDisplayFormat("HH:mm")
        form.addRow("Start download at", self.start)
        self.stop_en = QCheckBox("Stop download at")
        self.stop_en.setChecked(cfg.sched_stop_enabled)
        self.stop = QTimeEdit(QTime.fromString(cfg.sched_stop, "HH:mm"))
        self.stop.setDisplayFormat("HH:mm")
        self.stop.setEnabled(cfg.sched_stop_enabled)
        self.stop_en.toggled.connect(self.stop.setEnabled)
        form.addRow(self.stop_en, self.stop)
        days = QHBoxLayout()
        self.days = []
        for i, d in enumerate(self.DAYS):
            cb = QCheckBox(d)
            cb.setChecked(i in cfg.sched_days)
            self.days.append(cb)
            days.addWidget(cb)
        form.addRow("Days", days)
        lay.addWidget(box)

        box2 = QGroupBox("When the queue finishes")
        f2 = QFormLayout(box2)
        self.finish = QComboBox()
        self.finish.addItem("Do nothing", "none")
        self.finish.addItem("Exit Gesit", "exit")
        self.finish.addItem("Suspend the computer", "suspend")
        self.finish.addItem("Turn off the computer", "poweroff")
        self.finish.setCurrentIndex(max(0, self.finish.findData(cfg.sched_on_finish)))
        f2.addRow(self.finish)
        lay.addWidget(box2)

        row = QHBoxLayout()
        now = QPushButton("Start now")
        now.clicked.connect(self._now)
        row.addWidget(now)
        row.addStretch()
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self._ok)
        bb.rejected.connect(self.reject)
        row.addWidget(bb)
        lay.addLayout(row)

    def _apply(self) -> None:
        c = self.cfg
        c.sched_enabled = self.enable.isChecked()
        c.sched_start = self.start.time().toString("HH:mm")
        c.sched_stop_enabled = self.stop_en.isChecked()
        c.sched_stop = self.stop.time().toString("HH:mm")
        c.sched_days = [i for i, cb in enumerate(self.days) if cb.isChecked()]
        c.sched_on_finish = self.finish.currentData()

    def _ok(self) -> None:
        self._apply()
        self.accept()

    def _now(self) -> None:
        self._apply()
        self.start_now = True
        self.accept()
