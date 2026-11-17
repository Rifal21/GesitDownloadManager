#!/usr/bin/env python3
"""Regenerate every raster/vector icon from gesit/gui/icons.py (needs PySide6)."""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, root)

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import QGuiApplication  # noqa: E402

app = QGuiApplication([])
from gesit.gui import icons  # noqa: E402


def png(size: int, path: str) -> None:
    pm = icons.pixmap("app", size)
    pm.setDevicePixelRatio(1)
    pm = pm.scaled(size, size, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    assert pm.save(path), path


svg = icons.raw_svg("app").replace('viewBox="0 0 32 32"', 'viewBox="0 0 32 32" width="256" height="256"')
with open(os.path.join(root, "packaging", "gesit.svg"), "w") as f:
    f.write(svg)
for s in (16, 32, 48, 128):  # browser extension
    png(s, os.path.join(root, "extension", "shared", "icons", f"icon{s}.png"))
for s in (16, 24, 32, 48, 64, 128, 256, 512):  # freedesktop hicolor theme
    png(s, os.path.join(root, "packaging", "icons", "hicolor", f"{s}x{s}", "apps", "gesit.png"))


def write_ico(path: str, sizes=(16, 24, 32, 48, 64, 128, 256)) -> None:
    import struct

    blobs = []
    for sz in sizes:
        with open(os.path.join(root, "packaging", "icons", "hicolor", f"{sz}x{sz}", "apps", "gesit.png"), "rb") as f:
            blobs.append((sz, f.read()))
    out = struct.pack("<HHH", 0, 1, len(blobs))
    offset = 6 + 16 * len(blobs)
    for sz, data in blobs:
        out += struct.pack("<BBBBHHII", sz % 256, sz % 256, 0, 0, 1, 32, len(data), offset)  # 256 is stored as 0
        offset += len(data)
    out += b"".join(d for _s, d in blobs)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(out)


write_ico(os.path.join(root, "packaging", "windows", "gesit.ico"))
print("icons written")
