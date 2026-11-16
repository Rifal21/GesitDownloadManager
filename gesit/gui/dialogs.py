"""Add URL, Download File Info, Download Complete, Properties, About."""

from __future__ import annotations

import datetime
import os

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QVBoxLayout, QWidget,
)

from .. import __version__
from ..core import util
from ..core.config import CATEGORIES, Config, category_for
from ..core.models import Item
from . import icons
from .widgets import open_folder, open_path, watch_future


class AddURLDialog(QDialog):
    """'Add URL' - OK continues to the Download File Info dialog."""

    def __init__(self, parent=None, prefill: str = ""):
        super().__init__(parent)
        self.setWindowTitle("Enter new address to download")
        self.setWindowIcon(icons.icon("add"))
        self.setMinimumWidth(520)
        self.action: str | None = None
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("Address:"))
        self.url = QLineEdit(prefill)
        self.url.setPlaceholderText("https://example.com/file.zip")
        self.url.setClearButtonEnabled(True)
        lay.addWidget(self.url)
        if not prefill:
            clip = QGuiApplication.clipboard().text().strip()
            if clip.lower().startswith(("http://", "https://", "ftp://")) and "\n" not in clip:
                self.url.setText(clip)
                self.url.selectAll()
        self.auth = QCheckBox("Use authorization")
        lay.addWidget(self.auth)
        form = QFormLayout()
        self.user, self.pw = QLineEdit(), QLineEdit()
        self.pw.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow("Login:", self.user)
        form.addRow("Password:", self.pw)
        self.auth_box = QWidget()
        self.auth_box.setLayout(form)
        self.auth_box.setVisible(False)
        self.auth.toggled.connect(self.auth_box.setVisible)
        lay.addWidget(self.auth_box)
        btns = QHBoxLayout()
        btns.addStretch()
        ok, later, cancel = QPushButton("OK"), QPushButton("Download Later"), QPushButton("Cancel")
        ok.setDefault(True)
        ok.clicked.connect(lambda: self._done("ok"))
        later.clicked.connect(lambda: self._done("later"))
        cancel.clicked.connect(self.reject)
        for b in (ok, later, cancel):
            btns.addWidget(b)
        lay.addLayout(btns)

    def _done(self, action: str) -> None:
        if not self.url.text().strip():
            self.url.setFocus()
            return
        self.action = action
        self.accept()

    def values(self) -> dict:
        d = {"url": self.url.text().strip()}
        if self.auth.isChecked():
            d["auth_user"], d["auth_pass"] = self.user.text(), self.pw.text()
        return d


class FileInfoDialog(QDialog):
    """'Download File Info' - shown for every captured / added download."""

    def __init__(self, manager, cfg: Config, info: dict, parent=None):
        super().__init__(parent)
        self.manager, self.cfg, self.info = manager, cfg, info
        self.action: str | None = None
        self._name_edited = False
        self._cat_edited = False
        self.setWindowTitle("Download File Info")
        self.setWindowIcon(icons.icon("resume"))
        self.setMinimumWidth(560)
        lay = QVBoxLayout(self)

        self.url = QLineEdit(info["url"])
        self.url.setReadOnly(True)
        form = QFormLayout()
        form.addRow("URL", self.url)
        self.category = QComboBox()
        self.category.addItems(CATEGORIES)
        form.addRow("Category", self.category)

        self.name = QLineEdit(info.get("filename", ""))
        self.name.setPlaceholderText("Detecting file name...")
        self.name.textEdited.connect(lambda _t: setattr(self, "_name_edited", True))
        self.dir = QLineEdit()
        browse = QPushButton("...")
        browse.setFixedWidth(34)
        browse.clicked.connect(self._browse)
        row = QHBoxLayout()
        row.addWidget(self.dir)
        row.addWidget(browse)
        form.addRow("Save As", row)
        form.addRow("File name", self.name)
        self.desc = QLineEdit()
        form.addRow("Description", self.desc)
        lay.addLayout(form)

        self.size_label = QLabel("File size: detecting...")
        self.resume_label = QLabel("")
        lay.addWidget(self.size_label)
        lay.addWidget(self.resume_label)

        btns = QHBoxLayout()
        btns.addStretch()
        start, later, cancel = QPushButton("Start Download"), QPushButton("Download Later"), QPushButton("Cancel")
        start.setDefault(True)
        start.clicked.connect(lambda: self._done("start"))
        later.clicked.connect(lambda: self._done("later"))
        cancel.clicked.connect(self.reject)
        for b in (start, later, cancel):
            btns.addWidget(b)
        lay.addLayout(btns)

        self.kind = info.get("kind", "")
        streaming = self.kind in ("ytdlp", "hls")
        cat = "Video" if streaming else (
            info.get("category") or category_for(info.get("filename", "") or util.filename_from_url(info["url"]))
        )
        self._set_category(cat)
        self.category.activated.connect(self._on_category)
        if streaming:
            # a page / playlist URL is not a file: skip the HTTP probe, the downloader names the file itself
            how = "yt-dlp" if self.kind == "ytdlp" else "ffmpeg"
            self.name.setPlaceholderText(f"Taken from the video title ({how})")
            self.size_label.setText(f"Video download via {how}: size and name are determined while downloading")
            self.resume_label.setText("Resume capability: No")
        else:
            self._probe()

    # --------------------------------------------------------------- helpers
    def _set_category(self, cat: str) -> None:
        self.category.setCurrentText(cat)
        self.dir.setText(self.info.get("save_dir") or self.cfg.category_dir(cat))

    def _on_category(self, _i: int) -> None:
        self._cat_edited = True
        self.dir.setText(self.cfg.category_dir(self.category.currentText()))

    def _browse(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "Save to folder", self.dir.text())
        if d:
            self.dir.setText(d)

    def _probe(self) -> None:
        i = self.info
        fut = self.manager.probe(
            i["url"], referer=i.get("referer", ""), cookies=i.get("cookies", ""),
            user_agent=i.get("user_agent", ""), headers=i.get("headers"),
            auth=(i.get("auth_user", ""), i.get("auth_pass", "")) if i.get("auth_user") else None,
        )
        watch_future(fut, self._probed, self)

    def _probed(self, res: dict | None, err: Exception | None) -> None:
        if err or not res:
            self.size_label.setText(f"File size: unknown  ({err})" if err else "File size: unknown")
            return
        if not self._name_edited and not self.name.text():
            self.name.setText(res["filename"])
        if not self._cat_edited and not self.info.get("category"):
            self._set_category(res["category"])
        size = res["size"]
        self.size_label.setText(f"File size: {util.human_size(size) if size >= 0 else 'Unknown'}")
        self.resume_label.setText("Resume capability: " + ("Yes" if res["resumable"] else "No"))

    def _done(self, action: str) -> None:
        self.action = action
        self.accept()

    def values(self) -> dict:
        v = dict(self.info)
        v.update(
            filename=self.name.text().strip(), save_dir=self.dir.text().strip(),
            category=self.category.currentText(), description=self.desc.text().strip(),
        )
        return v


class CompleteDialog(QDialog):
    def __init__(self, item: Item, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Download complete")
        self.setWindowIcon(icons.icon("finished"))
        self.setMinimumWidth(480)
        lay = QVBoxLayout(self)
        head = QHBoxLayout()
        pic = QLabel()
        pic.setPixmap(icons.pixmap("finished", 40))
        head.addWidget(pic)
        t = QLabel(f"<b>Download complete</b><br>{util.human_size(item.total if item.total > 0 else item.downloaded)} "
                   f"downloaded")
        head.addWidget(t, 1)
        lay.addLayout(head)
        form = QFormLayout()
        for k, v in (("File", item.filename), ("Saved to", item.save_dir), ("Address", item.url)):
            lab = QLabel(v)
            lab.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            lab.setWordWrap(True)
            form.addRow(k + ":", lab)
        lay.addLayout(form)
        self.dont_show = QCheckBox("Don't show this dialog again")
        lay.addWidget(self.dont_show)
        row = QHBoxLayout()
        row.addStretch()
        path = item.path
        b_open, b_folder, b_close = QPushButton("Open"), QPushButton("Open folder"), QPushButton("Close")
        b_open.clicked.connect(lambda: open_path(path))
        b_folder.clicked.connect(lambda: open_folder(path))
        b_close.clicked.connect(self.accept)
        b_open.setDefault(True)
        for b in (b_open, b_folder, b_close):
            row.addWidget(b)
        lay.addLayout(row)


class PropertiesDialog(QDialog):
    def __init__(self, item: Item, parent=None):
        super().__init__(parent)
        self.setWindowTitle("File properties")
        self.setMinimumWidth(560)
        lay = QVBoxLayout(self)
        form = QFormLayout()
        fmt = lambda ts: datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S") if ts else "-"  # noqa: E731
        rows = [
            ("File name", item.filename or "-"), ("Saved to", item.save_dir), ("Address", item.url),
            ("Referer", item.referer or "-"), ("Parent page", item.page_url or "-"),
            ("Category", item.category), ("Size", util.human_size(item.total) if item.total > 0 else "Unknown"),
            ("Downloaded", util.human_size(item.downloaded)), ("Status", item.status.title()),
            ("Resume capability", "Yes" if item.resumable else "No"), ("Type", item.kind.upper()),
            ("Added", fmt(item.added)), ("Last try", fmt(item.last_try)),
            ("Description", item.description or "-"), ("Error", item.error or "-"),
        ]
        for k, v in rows:
            lab = QLabel(str(v))
            lab.setWordWrap(True)
            lab.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            form.addRow(k + ":", lab)
        lay.addLayout(form)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)


class AboutDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("About Gesit")
        lay = QVBoxLayout(self)
        pic = QLabel()
        pic.setPixmap(icons.pixmap("app", 72))
        pic.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(pic)
        t = QLabel(
            f"<h2 align=center>Gesit {__version__}</h2>"
            "<p align=center>Fast multi-connection download manager<br>"
            "Multi-connection downloads with dynamic segmentation, resume, queue,<br>"
            "scheduler and browser integration.</p>"
            "<p align=center>Open source (MIT). Independent project.</p>"
        )
        t.setTextFormat(Qt.TextFormat.RichText)
        lay.addWidget(t)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)


class LinksDialog(QDialog):
    """Pick several links to download at once (poor man's Site Grabber)."""

    def __init__(self, urls: list[str], parent=None):
        super().__init__(parent)
        from PySide6.QtWidgets import QHeaderView, QTableWidget, QTableWidgetItem

        self.action: str | None = None
        self.urls = urls
        self.setWindowTitle("Download all links")
        self.setWindowIcon(icons.icon("grabber"))
        self.resize(760, 480)
        lay = QVBoxLayout(self)
        top = QHBoxLayout()
        top.addWidget(QLabel("Show:"))
        self.filter = QComboBox()
        self.filter.addItems(["All links", *CATEGORIES])
        self.filter.currentTextChanged.connect(self._apply_filter)
        top.addWidget(self.filter)
        top.addWidget(QLabel("Name contains:"))
        self.text = QLineEdit()
        self.text.textChanged.connect(self._apply_filter)
        top.addWidget(self.text, 1)
        lay.addLayout(top)
        self.table = QTableWidget(len(urls), 3)
        self.table.setHorizontalHeaderLabels(["File", "Category", "Address"])
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        for r, u in enumerate(urls):
            name = util.filename_from_url(u) or u
            cell = QTableWidgetItem(name)
            cell.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable)
            cell.setCheckState(Qt.CheckState.Checked)
            self.table.setItem(r, 0, cell)
            self.table.setItem(r, 1, QTableWidgetItem(category_for(name)))
            self.table.setItem(r, 2, QTableWidgetItem(u))
        self.table.setColumnWidth(0, 260)
        lay.addWidget(self.table)
        row = QHBoxLayout()
        sel_all, sel_none = QPushButton("Select all"), QPushButton("Select none")
        sel_all.clicked.connect(lambda: self._check_visible(True))
        sel_none.clicked.connect(lambda: self._check_visible(False))
        row.addWidget(sel_all)
        row.addWidget(sel_none)
        row.addStretch()
        now, later, cancel = QPushButton("Download Now"), QPushButton("Download Later"), QPushButton("Cancel")
        now.setDefault(True)
        now.clicked.connect(lambda: self._done("start"))
        later.clicked.connect(lambda: self._done("later"))
        cancel.clicked.connect(self.reject)
        for b in (now, later, cancel):
            row.addWidget(b)
        lay.addLayout(row)

    def _apply_filter(self) -> None:
        cat, txt = self.filter.currentText(), self.text.text().lower()
        for r in range(self.table.rowCount()):
            ok = (cat == "All links" or self.table.item(r, 1).text() == cat) and txt in self.table.item(r, 2).text().lower()
            self.table.setRowHidden(r, not ok)

    def _check_visible(self, on: bool) -> None:
        for r in range(self.table.rowCount()):
            if not self.table.isRowHidden(r):
                self.table.item(r, 0).setCheckState(Qt.CheckState.Checked if on else Qt.CheckState.Unchecked)

    def _done(self, action: str) -> None:
        self.action = action
        self.accept()

    def selected(self) -> list[str]:
        return [
            self.urls[r] for r in range(self.table.rowCount())
            if not self.table.isRowHidden(r) and self.table.item(r, 0).checkState() == Qt.CheckState.Checked
        ]
