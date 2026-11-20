"""Persistent user settings (JSON in ~/.config/gesit)."""

from __future__ import annotations

import json
import os
import shutil
from dataclasses import asdict, dataclass, field, fields

from . import paths

CONFIG_DIR = paths.config_dir()
DATA_DIR = paths.data_dir()
CONFIG_PATH = os.path.join(CONFIG_DIR, "config.json")
DB_PATH = os.path.join(DATA_DIR, "downloads.db")


def default_root() -> str:
    return paths.default_download_root()


def migrate_legacy() -> list[str]:
    """Copy settings and the download list from a pre-rename 'lidm' install (never overwrites, never deletes)."""
    done: list[str] = []
    old_cfg, old_data = paths.legacy_dirs()
    old_json = os.path.join(old_cfg, "config.json")
    old_db = os.path.join(old_data, "downloads.db")
    for src, dst in ((old_json, CONFIG_PATH), (old_db, DB_PATH)):
        if os.path.exists(src) and not os.path.exists(dst):
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(src, dst)
            os.chmod(dst, 0o600)
            done.append(os.path.basename(dst))
    if "config.json" in done:
        # the old default folder was ~/Downloads/LIDM: new downloads follow the new brand (existing ones keep their paths)
        try:
            with open(CONFIG_PATH) as f:
                raw = json.load(f)
            if os.path.basename(raw.get("download_root", "")) == "LIDM":
                raw["download_root"] = default_root()
                with open(CONFIG_PATH, "w") as f:
                    json.dump(raw, f, indent=2)
        except (OSError, ValueError):
            pass
    return done


CATEGORY_EXTS: dict[str, list[str]] = {
    "Compressed": ["zip", "rar", "7z", "gz", "bz2", "xz", "zst", "tar", "tgz", "iso", "cab", "lz", "z"],
    "Documents": ["pdf", "doc", "docx", "xls", "xlsx", "ppt", "pptx", "odt", "ods", "odp", "txt", "epub", "mobi", "rtf", "csv"],
    "Music": ["mp3", "flac", "wav", "ogg", "opus", "m4a", "aac", "wma", "mid", "midi"],
    "Programs": ["exe", "msi", "deb", "rpm", "appimage", "run", "bin", "apk", "dmg", "pkg", "jar", "sh", "flatpakref"],
    "Video": ["mp4", "mkv", "avi", "mov", "wmv", "flv", "webm", "m4v", "mpg", "mpeg", "ts", "m3u8", "3gp"],
}
CATEGORIES = list(CATEGORY_EXTS) + ["General"]


def category_for(filename: str) -> str:
    ext = os.path.splitext(filename)[1].lstrip(".").lower()
    for cat, exts in CATEGORY_EXTS.items():
        if ext in exts:
            return cat
    return "General"


@dataclass
class Config:
    # --- general
    connections: int = 8
    max_active: int = 4  # simultaneous downloads in the queue
    retries: int = 5
    timeout: int = 30
    speed_limit_kbps: int = 0  # 0 = unlimited
    user_agent: str = ""
    verify_tls: bool = True
    # --- save to
    download_root: str = field(default_factory=default_root)
    category_dirs: dict[str, str] = field(default_factory=dict)  # overrides per category
    # --- browser capture
    capture_enabled: bool = True
    capture_extensions: list[str] = field(
        default_factory=lambda: sorted({e for exts in CATEGORY_EXTS.values() for e in exts if e not in ("ts", "bin", "sh", "txt", "csv", "z")})
    )
    capture_min_size_kb: int = 0
    exclude_hosts: list[str] = field(default_factory=list)
    show_dialog_on_capture: bool = True
    # --- proxy: mode none|system|manual ; type http|socks5
    proxy_mode: str = "system"
    proxy_type: str = "http"
    proxy_host: str = ""
    proxy_port: int = 0
    proxy_user: str = ""
    proxy_pass: str = ""
    # --- site logins [{"host":..,"user":..,"password":..}]
    site_logins: list[dict] = field(default_factory=list)
    # --- UI behaviour
    start_minimized: bool = False
    hidden_hint_shown: bool = False
    integration_prompted: bool = False
    close_to_tray: bool = True
    notify_on_complete: bool = True
    show_progress_dialog: bool = True
    show_complete_dialog: bool = True
    # --- updates
    check_updates: bool = True
    last_update_check: float = 0.0
    update_notified: str = ""  # newest version the user was already told about
    last_speedtest: dict = field(default_factory=dict)  # {"ts", "down", "up" (Mbit/s), "ping" (ms)}
    # --- scheduler
    sched_enabled: bool = False
    sched_start: str = "02:00"
    sched_stop_enabled: bool = False
    sched_stop: str = "07:00"
    sched_days: list[int] = field(default_factory=lambda: [0, 1, 2, 3, 4, 5, 6])  # Mon=0
    sched_on_finish: str = "none"  # none|exit|suspend|poweroff

    def category_dir(self, category: str) -> str:
        if category in self.category_dirs and self.category_dirs[category]:
            return self.category_dirs[category]
        return os.path.join(self.download_root, category)

    def proxy_url(self) -> str | None:
        if self.proxy_mode == "manual" and self.proxy_host:
            auth = ""
            if self.proxy_user:
                auth = f"{self.proxy_user}:{self.proxy_pass}@"
            scheme = "socks5" if self.proxy_type == "socks5" else "http"
            return f"{scheme}://{auth}{self.proxy_host}:{self.proxy_port}"
        if self.proxy_mode == "system":
            for k in ("https_proxy", "HTTPS_PROXY", "http_proxy", "HTTP_PROXY", "all_proxy", "ALL_PROXY"):
                if os.environ.get(k):
                    return os.environ[k]
        return None

    def login_for(self, host: str) -> tuple[str, str] | None:
        for e in self.site_logins:
            h = (e.get("host") or "").lower()
            if h and (host == h or host.endswith("." + h)):
                return e.get("user", ""), e.get("password", "")
        return None

    # ------------------------------------------------------------ persistence
    @classmethod
    def load(cls, path: str = CONFIG_PATH) -> "Config":
        cfg = cls()
        try:
            with open(path) as f:
                raw = json.load(f)
        except (OSError, ValueError):
            return cfg
        names = {f.name for f in fields(cls)}
        for k, v in raw.items():
            if k in names:
                setattr(cfg, k, v)
        return cfg

    def save(self, path: str = CONFIG_PATH) -> None:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(asdict(self), f, indent=2)
        os.chmod(tmp, 0o600)  # may contain proxy / site passwords
        os.replace(tmp, path)
