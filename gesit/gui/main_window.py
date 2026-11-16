"""Main window: toolbar, category tree and download list."""

from __future__ import annotations

import datetime

from PySide6.QtCore import QAbstractTableModel, QMimeDatabase, QModelIndex, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QBrush, QColor, QGuiApplication, QIcon, QKeySequence
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QHeaderView, QMainWindow, QMenu, QMessageBox, QSplitter,
    QStatusBar, QTableView, QToolBar, QToolButton, QTreeWidget, QTreeWidgetItem,
)

from ..core import util
from ..core.config import CATEGORIES
from ..core.models import ACTIVE, COMPLETE, CONNECTING, DOWNLOADING, ERROR, QUEUED, STOPPED, Item
from . import icons

COLS = ["File name", "Q", "Size", "Status", "Time left", "Transfer rate", "Last try date", "Description"]
_mime = QMimeDatabase()
_icon_cache: dict[str, QIcon] = {}


def file_icon(name: str) -> QIcon:
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    if ext not in _icon_cache:
        mt = _mime.mimeTypeForFile(name, QMimeDatabase.MatchMode.MatchExtension)
        ic = QIcon.fromTheme(mt.iconName(), QIcon.fromTheme(mt.genericIconName(), icons.icon("open")))
        _icon_cache[ext] = ic
    return _icon_cache[ext]


def status_text(it: Item) -> str:
    if it.status == COMPLETE:
        return "Complete"
    if it.status == ERROR:
        return "Error"
    if it.status == CONNECTING:
        return "Connecting..."
    if it.status == DOWNLOADING:
        return f"{it.percent:.2f}%" if it.total and it.total > 0 else util.human_size(it.downloaded)
    if it.status == QUEUED:
        return "Queued"
    return f"{it.percent:.2f}%" if it.downloaded and it.total and it.total > 0 else ""


class DownloadModel(QAbstractTableModel):
    def __init__(self):
        super().__init__()
        self.rows: list[Item] = []

    def rowCount(self, parent=QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(COLS)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):  # noqa: N802
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return COLS[section]
        return None

    def data(self, index: QModelIndex, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        it, c = self.rows[index.row()], index.column()
        if role == Qt.ItemDataRole.DisplayRole:
            if c == 0:
                return it.filename or util.filename_from_url(it.url) or it.url
            if c == 1:
                return "●" if it.in_queue and it.status != COMPLETE else ""
            if c == 2:
                return util.human_size(it.total) if it.total and it.total > 0 else ("" if it.status in (STOPPED, QUEUED) else "Unknown")
            if c == 3:
                return status_text(it)
            if c == 4:
                return util.human_time(it.eta) if it.status in ACTIVE else ""
            if c == 5:
                return util.human_speed(it.speed) if it.status in ACTIVE else ""
            if c == 6:
                return datetime.datetime.fromtimestamp(it.last_try).strftime("%b %d %H:%M:%S %Y") if it.last_try else ""
            if c == 7:
                return it.description
        elif role == Qt.ItemDataRole.DecorationRole and c == 0:
            return file_icon(it.filename or util.filename_from_url(it.url))
        elif role == Qt.ItemDataRole.TextAlignmentRole:
            if c in (1, 2, 3, 4, 5):
                return int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter) if c != 1 else int(Qt.AlignmentFlag.AlignCenter)
        elif role == Qt.ItemDataRole.ToolTipRole:
            if c == 3 and it.status == ERROR:
                return it.error
            if c == 0:
                return f"{it.url}\n{it.path}"
        elif role == Qt.ItemDataRole.ForegroundRole and c == 3 and it.status == ERROR:
            return QBrush(QColor("#c0392b"))
        elif role == Qt.ItemDataRole.UserRole:
            return it.id
        return None

    def set_rows(self, rows: list[Item]) -> bool:
        """Returns True when the row set changed (selection must be restored)."""
        same = [r.id for r in rows] == [r.id for r in self.rows]
        if same:
            self.rows = rows
            if rows:
                self.dataChanged.emit(self.index(0, 0), self.index(len(rows) - 1, len(COLS) - 1))
            return False
        self.beginResetModel()
        self.rows = rows
        self.endResetModel()
        return True


SORT_KEYS = {
    0: lambda i: (i.filename or i.url).lower(),
    1: lambda i: i.in_queue,
    2: lambda i: i.total,
    3: lambda i: i.percent,
    4: lambda i: i.eta if i.eta is not None else 1e18,
    5: lambda i: i.speed,
    6: lambda i: i.last_try,
    7: lambda i: i.description.lower(),
}


class MainWindow(QMainWindow):
    closing_to_tray = Signal()

    def __init__(self, app):
        super().__init__()
        self.app = app
        self.mgr = app.manager
        self.model = DownloadModel()
        self.filter = ("all", None)
        self.sort_col, self.sort_order = -1, Qt.SortOrder.AscendingOrder
        self.setWindowTitle("Gesit")
        self.setWindowIcon(icons.icon("app"))
        self.resize(1100, 640)
        self.setAcceptDrops(True)
        self._build_actions()
        self._build_menus()
        self._build_toolbar()
        self._build_body()
        self.setStatusBar(QStatusBar())
        self.timer = QTimer(self)
        self.timer.setInterval(250)
        self.timer.timeout.connect(self.refresh)
        self.timer.start()
        self.refresh()
        self._selection_changed()

    # ------------------------------------------------------------------ build
    def _act(self, text, icon=None, slot=None, shortcut=None, tip=None) -> QAction:
        a = QAction(icons.icon(icon) if icon else QIcon(), text, self)
        if slot:
            a.triggered.connect(lambda _c=False, s=slot: s())
        if shortcut:
            a.setShortcut(QKeySequence(shortcut))
        if tip:
            a.setStatusTip(tip)
        return a

    def _build_actions(self) -> None:
        a = self.app
        self.a_add = self._act("Add URL", "add", a.add_url_dialog, "Ctrl+N", "Add a new download")
        self.a_resume = self._act("Resume", "resume", self.resume_selected, None, "Resume the selected downloads")
        self.a_stop = self._act("Stop", "stop", self.stop_selected, None, "Stop the selected downloads")
        self.a_stopall = self._act("Stop All", "stopall", self.mgr.stop_all, None, "Stop all downloads")
        self.a_delete = self._act("Delete", "delete", self.delete_selected, "Delete", "Delete the selected downloads")
        self.a_deletec = self._act("Delete C.", "deletec", self.delete_completed, None, "Delete all completed downloads from the list")
        self.a_options = self._act("Options", "options", a.options_dialog, "Ctrl+O")
        self.a_sched = self._act("Scheduler", "scheduler", a.scheduler_dialog)
        self.a_startq = self._act("Start Queue", "startq", self.mgr.start_queue, None, "Start the main download queue")
        self.a_stopq = self._act("Stop Queue", "stopq", self.mgr.stop_queue, None, "Stop the main download queue")
        self.a_links = self._act("Download all links...", "grabber", a.links_from_clipboard_dialog, None,
                                 "Pick links from the clipboard text / a page and download them")
        self.a_open = self._act("Open", None, self.open_selected)
        self.a_folder = self._act("Open folder", None, self.open_folder_selected)
        self.a_redl = self._act("Redownload", None, self.redownload_selected)
        self.a_props = self._act("Properties", None, self.properties_selected)
        self.a_copy = self._act("Copy address", None, self.copy_address)
        self.a_toq = self._act("Add to queue", None, lambda: self.queue_selected(True))
        self.a_fromq = self._act("Remove from queue", None, lambda: self.queue_selected(False))
        self.a_progress = self._act("Show progress", None, self.show_progress_selected)
        self.a_quit = self._act("Exit", None, a.quit, "Ctrl+Q")
        self.a_integration = self._act("Set up browser integration...", None, a.setup_integration, None,
                                       "Connect Chrome, Brave, Edge, Vivaldi and Firefox to Gesit")
        self.a_about = self._act("About Gesit", None, a.about_dialog)

    def _build_menus(self) -> None:
        mb = self.menuBar()
        m = mb.addMenu("&Tasks")
        m.addAction(self.a_add)
        m.addAction(self.a_links)
        m.addSeparator()
        m.addAction(self.a_options)
        m.addAction(self.a_sched)
        m.addAction(self.a_integration)
        m.addSeparator()
        m.addAction(self.a_quit)
        m = mb.addMenu("&File")
        for x in (self.a_open, self.a_folder, self.a_props, self.a_copy):
            m.addAction(x)
        m.addSeparator()
        m.addAction(self.a_delete)
        m.addAction(self.a_deletec)
        m = mb.addMenu("&Downloads")
        for x in (self.a_resume, self.a_stop, self.a_stopall, self.a_redl, self.a_progress):
            m.addAction(x)
        m.addSeparator()
        for x in (self.a_startq, self.a_stopq, self.a_toq, self.a_fromq):
            m.addAction(x)
        m.addSeparator()
        sub = m.addMenu("Speed limiter (global)")
        for label, kbps in (("Unlimited", 0), ("64 KB/s", 64), ("256 KB/s", 256), ("1 MB/s", 1024),
                            ("5 MB/s", 5120), ("10 MB/s", 10240)):
            act = QAction(label, self)
            act.triggered.connect(lambda _c=False, k=kbps: self.app.set_global_limit(k))
            sub.addAction(act)
        m = mb.addMenu("&View")
        self.a_tree = QAction("Categories panel", self, checkable=True, checked=True)
        self.a_tree.toggled.connect(lambda v: self.tree.setVisible(v))
        m.addAction(self.a_tree)
        m = mb.addMenu("&Help")
        m.addAction(self.a_about)

    def _build_toolbar(self) -> None:
        tb = QToolBar("Main")
        tb.setMovable(False)
        tb.setIconSize(QSize(32, 32))
        tb.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
        self.addToolBar(tb)
        for act in (self.a_add, self.a_resume, self.a_stop, self.a_stopall, self.a_delete, self.a_deletec,
                    None, self.a_options, self.a_sched, None, self.a_startq, self.a_stopq, None, self.a_links):
            if act is None:
                tb.addSeparator()
            else:
                tb.addAction(act)
        for btn in tb.findChildren(QToolButton):
            btn.setMinimumWidth(64)

    def _build_body(self) -> None:
        split = QSplitter()
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setIconSize(QSize(18, 18))
        self.tree.setMinimumWidth(170)
        self.tree.setMaximumWidth(280)
        root_all = self._tree_item(self.tree, "All Downloads", "all", ("all", None))
        for cat in CATEGORIES:
            self._tree_item(root_all, cat, "folder", ("cat", cat))
        self._tree_item(self.tree, "Unfinished", "unfinished", ("unfinished", None))
        self._tree_item(self.tree, "Finished", "finished", ("finished", None))
        queues = self._tree_item(self.tree, "Queues", "queue", ("queues", None))
        self._tree_item(queues, "Main download queue", "queue", ("queue", None))
        self.tree.expandAll()
        self.tree.setCurrentItem(self.tree.topLevelItem(0))
        self.tree.currentItemChanged.connect(self._tree_changed)
        split.addWidget(self.tree)

        self.view = QTableView()
        self.view.setModel(self.model)
        self.view.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.view.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.view.setShowGrid(False)
        self.view.setAlternatingRowColors(True)
        self.view.verticalHeader().setVisible(False)
        self.view.verticalHeader().setDefaultSectionSize(24)
        self.view.setIconSize(QSize(18, 18))
        self.view.setWordWrap(False)
        self.view.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.view.customContextMenuRequested.connect(self._context_menu)
        self.view.doubleClicked.connect(self._double_click)
        hh = self.view.horizontalHeader()
        hh.setSectionsClickable(True)
        hh.setSortIndicatorShown(True)
        hh.setSortIndicator(-1, Qt.SortOrder.AscendingOrder)
        hh.setStretchLastSection(True)
        hh.sectionClicked.connect(self._header_clicked)
        for i, w in enumerate((330, 28, 90, 90, 100, 110, 160, 200)):
            self.view.setColumnWidth(i, w)
        self.view.selectionModel().selectionChanged.connect(lambda *_: self._selection_changed())
        split.addWidget(self.view)
        split.setStretchFactor(1, 1)
        split.setSizes([200, 900])
        self.setCentralWidget(split)

    @staticmethod
    def _tree_item(parent, text, icon, data) -> QTreeWidgetItem:
        it = QTreeWidgetItem(parent, [text])
        it.setIcon(0, icons.icon(icon))
        it.setData(0, Qt.ItemDataRole.UserRole, data)
        return it

    # ------------------------------------------------------------- list state
    def _tree_changed(self, cur: QTreeWidgetItem | None, _prev) -> None:
        if cur:
            self.filter = cur.data(0, Qt.ItemDataRole.UserRole)
            self.refresh()

    def _header_clicked(self, col: int) -> None:
        if col == self.sort_col:
            self.sort_order = (
                Qt.SortOrder.DescendingOrder if self.sort_order == Qt.SortOrder.AscendingOrder else Qt.SortOrder.AscendingOrder
            )
        else:
            self.sort_col, self.sort_order = col, Qt.SortOrder.AscendingOrder
        self.view.horizontalHeader().setSortIndicator(self.sort_col, self.sort_order)
        self.refresh()

    def _matches(self, it: Item) -> bool:
        kind, val = self.filter
        if kind == "cat":
            return it.category == val
        if kind == "unfinished":
            return it.status != COMPLETE
        if kind == "finished":
            return it.status == COMPLETE
        if kind in ("queue", "queues"):
            return it.in_queue and it.status != COMPLETE
        return True

    def selected_ids(self) -> list[int]:
        return [self.model.rows[i.row()].id for i in self.view.selectionModel().selectedRows()]

    def selected_items(self) -> list[Item]:
        ids = set(self.selected_ids())
        return [r for r in self.model.rows if r.id in ids]

    def refresh(self) -> None:
        items = self.mgr.snapshot()
        rows = [i for i in items if self._matches(i)]
        if self.sort_col >= 0:
            rows.sort(key=SORT_KEYS[self.sort_col], reverse=self.sort_order == Qt.SortOrder.DescendingOrder)
        sel = set(self.selected_ids())
        if self.model.set_rows(rows) and sel:
            sm = self.view.selectionModel()
            for r, it in enumerate(rows):
                if it.id in sel:
                    sm.select(self.model.index(r, 0), sm.SelectionFlag.Select | sm.SelectionFlag.Rows)
        total_speed = sum(i.speed for i in items if i.status in ACTIVE)
        active = sum(1 for i in items if i.status in ACTIVE)
        self.statusBar().showMessage(
            f"{len(items)} downloads   |   {active} active   |   {util.human_speed(total_speed) or '0 B/sec'}"
            + ("   |   Queue running" if self.mgr.queue_running else "")
        )
        self.app.update_progress_dialogs({i.id: i for i in items})
        self._selection_changed()

    def _selection_changed(self) -> None:
        sel = self.selected_items()
        any_sel = bool(sel)
        self.a_resume.setEnabled(any(i.status in (STOPPED, ERROR, QUEUED) for i in sel))
        self.a_stop.setEnabled(any(i.status in (*ACTIVE, QUEUED) for i in sel))
        self.a_delete.setEnabled(any_sel)
        self.a_open.setEnabled(len(sel) == 1 and sel[0].status == COMPLETE)
        self.a_folder.setEnabled(len(sel) == 1)
        self.a_redl.setEnabled(any_sel)
        self.a_props.setEnabled(len(sel) == 1)
        self.a_copy.setEnabled(any_sel)
        self.a_toq.setEnabled(any(i.status != COMPLETE and not i.in_queue for i in sel))
        self.a_fromq.setEnabled(any(i.in_queue for i in sel))
        self.a_progress.setEnabled(len(sel) == 1 and sel[0].status != COMPLETE)

    # ---------------------------------------------------------------- actions
    def resume_selected(self) -> None:
        for it in self.selected_items():
            if it.status in (STOPPED, ERROR, QUEUED):
                self.mgr.start_item(it.id)
                if self.app.cfg.show_progress_dialog:
                    self.app.show_progress(it.id)

    def stop_selected(self) -> None:
        for i in self.selected_items():
            self.mgr.stop_item(i.id)

    def delete_selected(self) -> None:
        sel = self.selected_items()
        if not sel:
            return
        box = QMessageBox(QMessageBox.Icon.Question, "Delete",
                          f"Do you want to delete {len(sel)} selected download(s) from the list?", parent=self)
        box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        cb = QCheckBox("Also delete the downloaded file(s) from disk")
        box.setCheckBox(cb)
        if box.exec() == QMessageBox.StandardButton.Yes:
            self.mgr.remove([i.id for i in sel], cb.isChecked())

    def delete_completed(self) -> None:
        if any(i.status == COMPLETE for i in self.mgr.snapshot()):
            if QMessageBox.question(self, "Delete completed", "Remove all completed downloads from the list?\n"
                                    "(the files stay on disk)") == QMessageBox.StandardButton.Yes:
                self.mgr.remove_completed()

    def open_selected(self) -> None:
        from .widgets import open_path

        for it in self.selected_items():
            if it.status == COMPLETE:
                open_path(it.path)

    def open_folder_selected(self) -> None:
        from .widgets import open_folder

        for it in self.selected_items()[:1]:
            open_folder(it.path if it.status == COMPLETE else it.save_dir)

    def redownload_selected(self) -> None:
        for it in self.selected_items():
            self.mgr.redownload(it.id)
            if self.app.cfg.show_progress_dialog:
                self.app.show_progress(it.id)

    def properties_selected(self) -> None:
        from .dialogs import PropertiesDialog

        sel = self.selected_items()
        if sel:
            PropertiesDialog(sel[0], self).exec()

    def copy_address(self) -> None:
        QGuiApplication.clipboard().setText("\n".join(i.url for i in self.selected_items()))

    def queue_selected(self, on: bool) -> None:
        self.mgr.add_to_queue(self.selected_ids(), on)

    def show_progress_selected(self) -> None:
        for it in self.selected_items()[:1]:
            self.app.show_progress(it.id)

    def _double_click(self, idx: QModelIndex) -> None:
        it = self.model.rows[idx.row()]
        if it.status == COMPLETE:
            self.open_selected()
        else:
            if it.status in (STOPPED, ERROR, QUEUED):
                self.mgr.start_item(it.id)
            self.app.show_progress(it.id)

    def _context_menu(self, pos) -> None:
        sel = self.selected_items()
        if not sel:
            return
        m = QMenu(self)
        for a in (self.a_open, self.a_folder, None, self.a_resume, self.a_stop, self.a_progress, self.a_redl,
                  None, self.a_toq, self.a_fromq, None, self.a_copy, self.a_props, None, self.a_delete):
            if a is None:
                m.addSeparator()
            elif a.isEnabled():
                m.addAction(a)
        m.exec(self.view.viewport().mapToGlobal(pos))

    # ------------------------------------------------------- window behaviour
    def closeEvent(self, ev) -> None:  # noqa: N802
        if self.app.quitting or not self.app.cfg.close_to_tray:
            ev.accept()
            self.app.quit()
            return
        # keep running even without a system tray (GNOME): a sandboxed browser cannot restart Gesit
        ev.ignore()
        self.hide()
        if not self.app.tray_available:
            self.app.explain_hidden_once()

    def dragEnterEvent(self, ev) -> None:  # noqa: N802
        if ev.mimeData().hasText() or ev.mimeData().hasUrls():
            ev.acceptProposedAction()

    def dropEvent(self, ev) -> None:  # noqa: N802
        md = ev.mimeData()
        urls = [u.toString() for u in md.urls() if u.scheme() in ("http", "https", "ftp")]
        if not urls and md.hasText():
            urls = [t.strip() for t in md.text().split() if t.lower().startswith(("http://", "https://"))]
        if len(urls) == 1:
            self.app.add_url_dialog(urls[0])
        elif urls:
            self.app.links_dialog(urls)
        ev.acceptProposedAction()
