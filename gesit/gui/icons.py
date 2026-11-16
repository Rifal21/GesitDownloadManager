"""Self-drawn SVG icons (no third-party assets)."""

from __future__ import annotations

from PySide6.QtCore import QByteArray, QSize, Qt
from PySide6.QtGui import QIcon, QImage, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

_W = "#ffffff"


def _circle(color: str, inner: str) -> str:
    return f'<circle cx="16" cy="16" r="14" fill="{color}"/>{inner}'


_SVG: dict[str, str] = {
    "app": (
        '<defs><linearGradient id="g" x1="0.1" y1="0" x2="0.9" y2="1"><stop offset="0" stop-color="#ffb02e"/>'
        '<stop offset="1" stop-color="#e8470f"/></linearGradient></defs>'
        '<rect x="1" y="1" width="30" height="30" rx="8.5" fill="url(#g)"/>'
        # shaft drawn as three segments (dynamic segmentation), then the arrow head and the tray
        f'<path d="M16 5.5v1.4M16 11.3v1.4M16 17.1v0.01" stroke="{_W}" stroke-width="3" stroke-linecap="round"/>'
        f'<path d="M10.2 16.6l5.8 5.8 5.8-5.8" stroke="{_W}" stroke-width="3.2" fill="none" stroke-linecap="round" stroke-linejoin="round"/>'
        f'<path d="M8.5 26.4h15" stroke="{_W}" stroke-width="3.2" stroke-linecap="round"/>'
    ),
    "add": _circle("#2e9e4f", f'<path d="M16 9v14M9 16h14" stroke="{_W}" stroke-width="3.4" stroke-linecap="round"/>'),
    "resume": _circle(
        "#2476d2",
        f'<path d="M16 8v11M11 14.5l5 5.5 5-5.5" stroke="{_W}" stroke-width="3" fill="none" stroke-linecap="round" stroke-linejoin="round"/>'
        f'<path d="M10 24h12" stroke="{_W}" stroke-width="3" stroke-linecap="round"/>',
    ),
    "stop": _circle("#e08a1e", f'<rect x="10.5" y="10.5" width="11" height="11" rx="1.5" fill="{_W}"/>'),
    "stopall": _circle(
        "#cf3a2c",
        f'<rect x="8" y="8" width="9" height="9" rx="1.2" fill="{_W}"/><rect x="15" y="15" width="9" height="9" rx="1.2" fill="{_W}" stroke="#cf3a2c" stroke-width="1.5"/>',
    ),
    "delete": _circle("#cf3a2c", f'<path d="M10.5 10.5l11 11M21.5 10.5l-11 11" stroke="{_W}" stroke-width="3.2" stroke-linecap="round"/>'),
    "deletec": (
        '<path d="M8 9h16l-1.6 18a2 2 0 0 1-2 1.8h-8.8a2 2 0 0 1-2-1.8z" fill="#7a8591"/>'
        '<rect x="6" y="5.5" width="20" height="3.2" rx="1.4" fill="#5d6873"/><rect x="12.5" y="3" width="7" height="3.5" rx="1.2" fill="#5d6873"/>'
        f'<path d="M12 15l3 3.2 5.5-6" stroke="{_W}" stroke-width="2.6" fill="none" stroke-linecap="round" stroke-linejoin="round"/>'
    ),
    "options": (
        '<circle cx="16" cy="16" r="11" fill="none" stroke="#68758a" stroke-width="5" stroke-dasharray="4.3 4.3"/>'
        '<circle cx="16" cy="16" r="9" fill="#68758a"/><circle cx="16" cy="16" r="3.6" fill="#fff"/>'
    ),
    "scheduler": (
        '<circle cx="16" cy="16" r="13.5" fill="#fff" stroke="#3b6fb6" stroke-width="2.6"/>'
        '<path d="M16 8.5V16l5 3" stroke="#3b6fb6" stroke-width="2.6" fill="none" stroke-linecap="round" stroke-linejoin="round"/>'
    ),
    "startq": _circle("#2e9e4f", f'<path d="M12.5 9.5v13l11-6.5z" fill="{_W}" stroke="{_W}" stroke-width="1.5" stroke-linejoin="round"/>'),
    "stopq": _circle("#e08a1e", f'<rect x="10" y="9.5" width="4.2" height="13" rx="1.2" fill="{_W}"/><rect x="17.8" y="9.5" width="4.2" height="13" rx="1.2" fill="{_W}"/>'),
    "grabber": _circle(
        "#7b4fb8",
        f'<circle cx="16" cy="13" r="3.6" fill="{_W}"/>'
        f'<path d="M16 17v5M9 9l4 3M23 9l-4 3M8.5 20l5-3M23.5 20l-5-3" stroke="{_W}" stroke-width="2.2" stroke-linecap="round"/>',
    ),
    "folder": (
        '<path d="M3 8a2 2 0 0 1 2-2h7l3 3h12a2 2 0 0 1 2 2v13a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z" fill="#f2b84b"/>'
        '<path d="M3 13h26v12a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z" fill="#f7cd6e"/>'
    ),
    "all": (
        '<path d="M3 8a2 2 0 0 1 2-2h7l3 3h12a2 2 0 0 1 2 2v13a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z" fill="#4c8bd8"/>'
        '<path d="M3 13h26v12a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z" fill="#6ba3ea"/>'
        f'<path d="M16 15v7M12.8 19.2l3.2 3.2 3.2-3.2" stroke="{_W}" stroke-width="2.2" fill="none" stroke-linecap="round" stroke-linejoin="round"/>'
    ),
    "unfinished": _circle("#e08a1e", f'<path d="M16 9v7.5l4 2.5" stroke="{_W}" stroke-width="2.8" fill="none" stroke-linecap="round"/>'),
    "finished": _circle("#2e9e4f", f'<path d="M9.5 16.5l4.5 4.5 8.5-9.5" stroke="{_W}" stroke-width="3" fill="none" stroke-linecap="round" stroke-linejoin="round"/>'),
    "queue": (
        '<rect x="5" y="6" width="22" height="5" rx="2" fill="#4c8bd8"/><rect x="5" y="13.5" width="22" height="5" rx="2" fill="#6ba3ea"/>'
        '<rect x="5" y="21" width="22" height="5" rx="2" fill="#9cc3f3"/>'
    ),
    "open": (
        '<path d="M5 6h13l7 7v13a1.5 1.5 0 0 1-1.5 1.5h-18A1.5 1.5 0 0 1 4 26V7.5A1.5 1.5 0 0 1 5.5 6z" fill="#fff" stroke="#68758a" stroke-width="2"/>'
        '<path d="M18 6v7h7" fill="none" stroke="#68758a" stroke-width="2"/>'
    ),
    "link": (
        '<path d="M13 19l6-6M12 15l-2.5 2.5a4 4 0 0 0 5.7 5.7L18 20.5M20 17l2.5-2.5a4 4 0 0 0-5.7-5.7L14 11.5" '
        'stroke="#2476d2" stroke-width="3" fill="none" stroke-linecap="round"/>'
    ),
}


_cache: dict[tuple[str, int], QPixmap] = {}


def pixmap(name: str, size: int = 32) -> QPixmap:
    key = (name, size)
    if key in _cache:
        return _cache[key]
    svg = f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">{_SVG[name]}</svg>'
    renderer = QSvgRenderer(QByteArray(svg.encode()))
    scale = 2  # render at 2x for hi-dpi crispness
    img = QImage(size * scale, size * scale, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    renderer.render(p)
    p.end()
    pm = QPixmap.fromImage(img)
    pm.setDevicePixelRatio(scale)
    _cache[key] = pm
    return pm


def icon(name: str) -> QIcon:
    ic = QIcon()
    for s in (16, 24, 32, 48, 64, 128):
        ic.addPixmap(pixmap(name, s))
    return ic


def raw_svg(name: str) -> str:
    return f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">{_SVG[name]}</svg>'


def size(px: int) -> QSize:
    return QSize(px, px)
