"""SQLite persistence for the download list."""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time

from .models import ACTIVE, STOPPED, Item

_COLS = [
    "url", "kind", "filename", "save_dir", "category", "referer", "page_url", "cookies",
    "user_agent", "headers", "auth_user", "auth_pass", "description", "total", "downloaded",
    "status", "error", "in_queue", "resumable", "added", "last_try", "speed_limit_kbps",
]

SCHEMA = f"""
CREATE TABLE IF NOT EXISTS downloads (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    url TEXT NOT NULL, kind TEXT DEFAULT 'http', filename TEXT, save_dir TEXT, category TEXT,
    referer TEXT, page_url TEXT, cookies TEXT, user_agent TEXT, headers TEXT,
    auth_user TEXT, auth_pass TEXT, description TEXT,
    total INTEGER, downloaded INTEGER, status TEXT, error TEXT,
    in_queue INTEGER, resumable INTEGER, added REAL, last_try REAL, speed_limit_kbps INTEGER
);
"""


class Database:
    def __init__(self, path: str):
        if path != ":memory:":
            os.makedirs(os.path.dirname(path), exist_ok=True)
        self._lock = threading.Lock()
        self.recovered_from: str | None = None
        try:
            self._open(path)
        except sqlite3.DatabaseError:
            # corrupt / not a database: keep it for inspection and start a fresh list instead of refusing to start
            if path == ":memory:":
                raise
            try:
                self._db.close()  # Windows cannot rename a file that is still open
            except (AttributeError, sqlite3.Error):
                pass
            self.recovered_from = f"{path}.corrupt-{int(time.time())}"
            os.replace(path, self.recovered_from)
            self._open(path)
        if path != ":memory:":
            os.chmod(path, 0o600)  # rows hold cookies and passwords

    def _open(self, path: str) -> None:
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.executescript(SCHEMA)

    def _values(self, it: Item) -> list:
        vals = []
        for c in _COLS:
            v = getattr(it, c)
            if c == "headers":
                v = json.dumps(v or {})
            elif c in ("in_queue", "resumable"):
                v = int(bool(v))
            vals.append(v)
        return vals

    def insert(self, it: Item) -> int:
        with self._lock:
            cur = self._db.execute(
                f"INSERT INTO downloads ({','.join(_COLS)}) VALUES ({','.join('?' * len(_COLS))})",
                self._values(it),
            )
            self._db.commit()
            return int(cur.lastrowid)

    def update(self, it: Item) -> None:
        with self._lock:
            sets = ",".join(f"{c}=?" for c in _COLS)
            self._db.execute(f"UPDATE downloads SET {sets} WHERE id=?", [*self._values(it), it.id])
            self._db.commit()

    def delete(self, ids: list[int]) -> None:
        with self._lock:
            self._db.executemany("DELETE FROM downloads WHERE id=?", [(i,) for i in ids])
            self._db.commit()

    def load_all(self) -> list[Item]:
        with self._lock:
            rows = self._db.execute("SELECT * FROM downloads ORDER BY id").fetchall()
        items = []
        for r in rows:
            it = Item(id=r["id"])
            for c in _COLS:
                v = r[c]
                if c == "headers":
                    v = json.loads(v or "{}")
                elif c in ("in_queue", "resumable"):
                    v = bool(v)
                elif v is None and c in ("referer", "page_url", "cookies", "user_agent", "auth_user",
                                         "auth_pass", "description", "error", "filename", "save_dir"):
                    v = ""
                setattr(it, c, v)
            if it.status in ACTIVE:  # app died mid-download
                it.status = STOPPED
            items.append(it)
        return items

    def close(self) -> None:
        with self._lock:
            self._db.close()
