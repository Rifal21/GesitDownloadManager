#!/usr/bin/env python3
"""Render the store listing images offscreen: promo tile + an app screenshot with sample downloads."""
import os
import sys
import tempfile
import time

root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
tmp = tempfile.mkdtemp(prefix="gesit-assets-")
for k in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_RUNTIME_DIR", "XDG_DOWNLOAD_DIR"):
    os.environ[k] = os.path.join(tmp, k)
    os.makedirs(os.environ[k], mode=0o700)
os.environ["QT_QPA_PLATFORM"] = "offscreen"
sys.path.insert(0, root)

from PySide6.QtCore import QRectF, Qt  # noqa: E402
from PySide6.QtGui import QColor, QFont, QImage, QLinearGradient, QPainter  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from gesit.core.config import DB_PATH  # noqa: E402
from gesit.core.db import Database  # noqa: E402
from gesit.core.models import COMPLETE, STOPPED, Item  # noqa: E402
from gesit.gui import icons  # noqa: E402
from gesit.gui.app import GesitApp  # noqa: E402

out = os.path.join(root, "extension", "store-assets")
qapp = QApplication([])

# ---- promo tile 440x280
img = QImage(440, 280, QImage.Format.Format_RGB32)
p = QPainter(img)
g = QLinearGradient(0, 0, 440, 280)
g.setColorAt(0, QColor("#ffb02e"))
g.setColorAt(1, QColor("#e8470f"))
p.fillRect(0, 0, 440, 280, g)
p.drawPixmap(34, 78, icons.pixmap("app", 124))
p.setPen(QColor("white"))
f = QFont("Sans", 34)
f.setBold(True)
p.setFont(f)
p.drawText(QRectF(176, 82, 250, 60), Qt.AlignmentFlag.AlignVCenter, "Gesit")
f2 = QFont("Sans", 12)
p.setFont(f2)
p.drawText(QRectF(178, 140, 250, 70), Qt.TextFlag.TextWordWrap, "Fast multi-connection downloads,\nstraight from your browser")
p.end()
img.save(os.path.join(out, "promo-tile-440x280.png"))

# ---- app screenshot with believable sample data
db = Database(DB_PATH)
now = time.time()
samples = [
    ("ubuntu-24.04.2-desktop-amd64.iso", "Compressed", 6_200_000_000, 4_960_000_000, STOPPED, "https://releases.ubuntu.com/"),
    ("lecture-14-distributed-systems.mp4", "Video", 1_480_000_000, 1_480_000_000, COMPLETE, "https://example.edu/"),
    ("annual-report-2025.pdf", "Documents", 18_400_000, 18_400_000, COMPLETE, "https://example.org/"),
    ("blender-4.3.2-linux-x64.tar.xz", "Compressed", 340_000_000, 238_000_000, STOPPED, "https://download.blender.org/"),
    ("lofi-beats-collection.flac", "Music", 612_000_000, 612_000_000, COMPLETE, "https://example.net/"),
    ("firefox-esr-140.0.deb", "Programs", 71_000_000, 71_000_000, COMPLETE, "https://example.org/"),
    ("dataset-images-part3.zip", "Compressed", 2_900_000_000, 1_160_000_000, STOPPED, "https://example.com/"),
]
for i, (name, cat, total, done, status, url) in enumerate(samples):
    it = Item(url=url + name, filename=name, category=cat, save_dir=f"/home/user/Downloads/Gesit/{cat}", total=total,
              downloaded=done, status=status, last_try=now - 3600 * (i + 1), resumable=True)
    db.insert(it)
db.close()

app = GesitApp(qapp)
app.cfg.integration_prompted = True
app.window.resize(1280, 800)
app.window.show()
for _ in range(12):
    qapp.processEvents()
    time.sleep(0.05)
app.window.grab().save(os.path.join(out, "screenshot-app-1280x800.png"))
app.quitting = True
app.window.hide()
os._exit(0)
