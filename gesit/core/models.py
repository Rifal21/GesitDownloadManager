"""Download item model shared by manager, DB and GUI."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field

# item states
STOPPED, QUEUED, CONNECTING, DOWNLOADING, COMPLETE, ERROR = (
    "stopped", "queued", "connecting", "downloading", "complete", "error",
)
ACTIVE = (CONNECTING, DOWNLOADING)


@dataclass
class Item:
    id: int = 0
    url: str = ""
    kind: str = "http"  # http | hls | ytdlp
    filename: str = ""
    save_dir: str = ""
    category: str = "General"
    referer: str = ""
    page_url: str = ""
    cookies: str = ""
    user_agent: str = ""
    headers: dict = field(default_factory=dict)
    auth_user: str = ""
    auth_pass: str = ""
    description: str = ""
    total: int = -1
    downloaded: int = 0
    status: str = STOPPED
    error: str = ""
    in_queue: bool = False
    resumable: bool = True
    added: float = field(default_factory=time.time)
    last_try: float = 0.0
    speed_limit_kbps: int = 0
    # runtime only (not persisted)
    speed: float = 0.0
    eta: float | None = None
    connections: list = field(default_factory=list)  # [(downloaded, remaining, state)]
    segments: list = field(default_factory=list)  # [(start, pos, end)] for the progress bar

    @property
    def path(self) -> str:
        import os

        return os.path.join(self.save_dir, self.filename) if self.filename else ""

    @property
    def percent(self) -> float:
        if self.status == COMPLETE:
            return 100.0
        if self.total and self.total > 0:
            return min(100.0, self.downloaded * 100.0 / self.total)
        return 0.0

    def headers_json(self) -> str:
        return json.dumps(self.headers or {})

    def snapshot(self) -> "Item":
        import copy

        return copy.copy(self)
