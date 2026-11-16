"""Small helpers shared by engine, manager and GUI."""

from __future__ import annotations

import mimetypes
import os
import re
import urllib.parse


def human_size(n: float | None) -> str:
    if n is None or n < 0:
        return "Unknown"
    units = ["Bytes", "KB", "MB", "GB", "TB"]
    v = float(n)
    for u in units:
        if v < 1024 or u == units[-1]:
            if u == "Bytes":
                return f"{int(v)} {u}"
            return f"{v:.2f} {u}" if v < 100 else f"{v:.1f} {u}"
        v /= 1024
    return f"{n} Bytes"


def human_speed(bps: float) -> str:
    if bps <= 0:
        return ""
    return human_size(bps).replace("Bytes", "B") + "/sec"


def human_time(seconds: float | None) -> str:
    if seconds is None or seconds < 0 or seconds == float("inf"):
        return ""
    s = int(seconds)
    if s >= 86400:
        return f"{s // 86400} day {s % 86400 // 3600} hour"
    if s >= 3600:
        return f"{s // 3600} hour {s % 3600 // 60} min"
    if s >= 60:
        return f"{s // 60} min {s % 60} sec"
    return f"{s} sec"


_BAD_CHARS = re.compile(r'[\x00-\x1f/\\:*?"<>|]')


def sanitize_filename(name: str, fallback: str = "download") -> str:
    name = _BAD_CHARS.sub("_", name).strip().strip(".")
    if not name:
        return fallback
    if len(name.encode("utf-8")) > 240:
        stem, ext = os.path.splitext(name)
        ext = ext[:20]
        stem = stem.encode("utf-8")[: 240 - len(ext.encode("utf-8"))].decode("utf-8", "ignore")
        name = stem + ext
    return name


def parse_content_disposition(value: str | None) -> str | None:
    """Extract a file name from a Content-Disposition header (RFC 6266 / 5987)."""
    if not value:
        return None
    m = re.search(r"filename\*\s*=\s*([^';]+)'[^']*'([^;]+)", value, re.I)
    if m:
        try:
            return urllib.parse.unquote(m.group(2).strip().strip('"'), encoding=m.group(1) or "utf-8")
        except LookupError:
            return urllib.parse.unquote(m.group(2).strip().strip('"'))
    m = re.search(r'filename\s*=\s*"((?:[^"\\]|\\.)*)"', value, re.I)
    if m:
        return m.group(1).replace('\\"', '"')
    m = re.search(r"filename\s*=\s*([^;]+)", value, re.I)
    if m:
        return m.group(1).strip()
    return None


def filename_from_url(url: str) -> str:
    path = urllib.parse.urlsplit(url).path
    name = urllib.parse.unquote(os.path.basename(path.rstrip("/")))
    return name


def ensure_extension(name: str, content_type: str | None) -> str:
    if os.path.splitext(name)[1] or not content_type:
        return name
    ctype = content_type.split(";")[0].strip().lower()
    if ctype in ("text/html", "application/octet-stream", ""):
        return name if ctype != "text/html" else name + ".html"
    ext = mimetypes.guess_extension(ctype)
    return name + ext if ext else name


def unique_path(path: str) -> str:
    """Return `path`, or `path (1)` style variant if it already exists."""
    if not os.path.exists(path):
        return path
    stem, ext = os.path.splitext(path)
    i = 1
    while os.path.exists(f"{stem} ({i}){ext}"):
        i += 1
    return f"{stem} ({i}){ext}"


def host_of(url: str) -> str:
    return (urllib.parse.urlsplit(url).hostname or "").lower()
