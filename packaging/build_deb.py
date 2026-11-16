#!/usr/bin/env python3
"""Build gesit_<ver>_all.deb without dpkg (pure Python: ar + tar.xz).

usage: build_deb.py OUTDIR      (run `python extension/build.py` first)
env:   GESIT_MAINTAINER="Name <mail>"  GESIT_HOMEPAGE=https://...
"""
import gzip
import hashlib
import io
import os
import shutil
import sys
import tarfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from gesit import __version__, hostinstall  # noqa: E402

OUT = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "dist")
MAINTAINER = os.environ.get("GESIT_MAINTAINER", "Rifal21 <giantjou77@gmail.com>")
HOMEPAGE = os.environ.get("GESIT_HOMEPAGE", "https://github.com/Rifal21/GesitDownloadManager")
PYDIR = "usr/lib/python3/dist-packages"

files: dict[str, tuple[bytes, int]] = {}  # path -> (content, mode)


def add(path: str, data: bytes, mode: int = 0o644) -> None:
    files[path.lstrip("/")] = (data, mode)


def add_file(path: str, src: str, mode: int = 0o644) -> None:
    with open(src, "rb") as f:
        add(path, f.read(), mode)


# application package (pure Python)
for dp, dns, fns in os.walk(os.path.join(ROOT, "gesit")):
    dns[:] = [d for d in dns if d != "__pycache__"]
    for fn in fns:
        if fn.endswith(".py"):
            full = os.path.join(dp, fn)
            add_file(os.path.join(PYDIR, os.path.relpath(full, ROOT)), full)
add("usr/bin/gesit", b"#!/usr/bin/python3\nfrom gesit.gui.app import main_cli\nmain_cli()\n", 0o755)
add("usr/bin/gesit-native-host", b"#!/usr/bin/python3\nfrom gesit.native_host import main\nmain()\n", 0o755)

# desktop integration
add_file("usr/share/applications/gesit.desktop", os.path.join(ROOT, "packaging/gesit.desktop"))
add_file("usr/share/icons/hicolor/scalable/apps/gesit.svg", os.path.join(ROOT, "packaging/gesit.svg"))
for s in (16, 24, 32, 48, 64, 128, 256, 512):
    add_file(f"usr/share/icons/hicolor/{s}x{s}/apps/gesit.png",
             os.path.join(ROOT, f"packaging/icons/hicolor/{s}x{s}/apps/gesit.png"))

# browser extensions + system-wide native messaging manifests
ext = os.path.join(ROOT, "extension", "dist")
if not os.path.isdir(os.path.join(ext, "chrome")):
    sys.exit("run `python extension/build.py` first")
for kind in ("chrome", "firefox"):
    for dp, _d, fns in os.walk(os.path.join(ext, kind)):
        for fn in fns:
            full = os.path.join(dp, fn)
            add_file(os.path.join("usr/share/gesit/extension", kind, os.path.relpath(full, os.path.join(ext, kind))), full)
add_file("usr/share/gesit/extension/gesit-firefox.xpi", os.path.join(ext, "gesit-firefox.xpi"))
for rel, content in hostinstall.system_manifests().items():
    add(rel, content.encode())

# docs (Debian policy wants a copyright file and a gzipped changelog)
add_file("usr/share/doc/gesit/copyright", os.path.join(ROOT, "LICENSE"))
changelog = (f"gesit ({__version__}) stable; urgency=low\n\n  * Release {__version__}.\n\n"
             f" -- {MAINTAINER}  {time.strftime('%a, %d %b %Y %H:%M:%S +0000', time.gmtime())}\n").encode()
add("usr/share/doc/gesit/changelog.Debian.gz", gzip.compress(changelog, mtime=0))

installed_kb = sum(len(d) for d, _ in files.values()) // 1024 + 1
control = f"""Package: gesit
Version: {__version__}
Architecture: all
Maintainer: {MAINTAINER}
Installed-Size: {installed_kb}
Depends: python3 (>= 3.11), python3-pyside6.qtcore, python3-pyside6.qtgui, python3-pyside6.qtwidgets, python3-pyside6.qtsvg, python3-httpx
Recommends: ffmpeg, yt-dlp
Section: net
Priority: optional
Homepage: {HOMEPAGE}
Description: Fast multi-connection download manager with browser integration
 Gesit splits every download over many connections (dynamic segmentation),
 resumes interrupted downloads, has a queue and scheduler, and takes over
 downloads and videos from Chrome, Chromium, Brave, Edge, Vivaldi and Firefox
 through a browser extension. Video sites are handled with yt-dlp and ffmpeg.
"""


def tar_bytes(entries: dict[str, tuple[bytes, int]], compress: str = "xz") -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode=f"w:{compress}", format=tarfile.GNU_FORMAT) as tar:
        dirs = sorted({os.path.dirname(p) for p in entries if os.path.dirname(p)} |
                      {d for p in entries for d in _parents(p)})
        for d in dirs:
            ti = tarfile.TarInfo("./" + d)
            ti.type, ti.mode, ti.uid, ti.gid, ti.uname, ti.gname, ti.mtime = tarfile.DIRTYPE, 0o755, 0, 0, "root", "root", 0
            tar.addfile(ti)
        for path in sorted(entries):
            data, mode = entries[path]
            ti = tarfile.TarInfo("./" + path)
            ti.size, ti.mode, ti.uid, ti.gid, ti.uname, ti.gname, ti.mtime = len(data), mode, 0, 0, "root", "root", 0
            tar.addfile(ti, io.BytesIO(data))
    return buf.getvalue()


def _parents(path: str):
    parts = os.path.dirname(path).split("/")
    return ["/".join(parts[: i + 1]) for i in range(len(parts)) if parts[0]]


md5 = "".join(f"{hashlib.md5(d).hexdigest()}  {p}\n" for p, (d, _m) in sorted(files.items()))
control_tar = tar_bytes({"control": (control.encode(), 0o644), "md5sums": (md5.encode(), 0o644)})
data_tar = tar_bytes(files)


def ar_member(name: str, data: bytes) -> bytes:
    header = f"{name + '/':<16}{int(time.time()):<12}{0:<6}{0:<6}{'100644':<8}{len(data):<10}`\n"
    assert len(header) == 60
    return header.encode() + data + (b"\n" if len(data) % 2 else b"")


os.makedirs(OUT, exist_ok=True)
deb = os.path.join(OUT, f"gesit_{__version__}_all.deb")
with open(deb, "wb") as f:
    f.write(b"!<arch>\n")
    f.write(ar_member("debian-binary", b"2.0\n"))
    f.write(ar_member("control.tar.xz", control_tar))
    f.write(ar_member("data.tar.xz", data_tar))
print(deb, f"{os.path.getsize(deb) // 1024} KiB,", len(files), "files")
