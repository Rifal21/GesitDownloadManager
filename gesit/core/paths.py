"""Per-user directories, one place so a Windows/macOS port only touches this file."""

from __future__ import annotations

import os
import sys

APP_DIR = "gesit"


def _home() -> str:
    return os.path.expanduser("~")


def config_dir() -> str:
    if sys.platform == "win32":
        return os.path.join(os.environ.get("APPDATA", os.path.join(_home(), "AppData", "Roaming")), "Gesit")
    if sys.platform == "darwin":
        return os.path.join(_home(), "Library", "Application Support", "Gesit")
    return os.path.join(os.environ.get("XDG_CONFIG_HOME", os.path.join(_home(), ".config")), APP_DIR)


def data_dir() -> str:
    if sys.platform == "win32":
        return os.path.join(os.environ.get("LOCALAPPDATA", os.path.join(_home(), "AppData", "Local")), "Gesit")
    if sys.platform == "darwin":
        return config_dir()
    return os.path.join(os.environ.get("XDG_DATA_HOME", os.path.join(_home(), ".local", "share")), APP_DIR)


def default_download_root() -> str:
    d = os.environ.get("XDG_DOWNLOAD_DIR") or os.path.join(_home(), "Downloads")
    return os.path.join(d, "Gesit")


def legacy_dirs() -> tuple[str, str]:
    """(config, data) directories of the pre-rename 'LIDM' builds, Linux only."""
    return (
        os.path.join(os.environ.get("XDG_CONFIG_HOME", os.path.join(_home(), ".config")), "lidm"),
        os.path.join(os.environ.get("XDG_DATA_HOME", os.path.join(_home(), ".local", "share")), "lidm"),
    )
