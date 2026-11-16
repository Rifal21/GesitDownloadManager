#!/usr/bin/env python3
"""Assemble the browser extensions.

  python extension/build.py          dist/chrome (MV3, pinned dev key), dist/firefox (MV2), dist/gesit-firefox.xpi
  python extension/build.py --store  also dist/store/*.zip ready to upload to the Chrome Web Store / AMO
"""

import json
import shutil
import sys
import zipfile
from pathlib import Path

root = Path(__file__).resolve().parent
dist = root / "dist"
key = (root / "chrome_key.txt").read_text().split()[0]
store = "--store" in sys.argv


def zip_dir(src: Path, out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted(src.rglob("*")):
            if f.is_file():
                z.write(f, f.relative_to(src))


if dist.exists():
    shutil.rmtree(dist)
for target in ("chrome", "firefox"):
    out = dist / target
    shutil.copytree(root / "shared", out)
    manifest = (root / target / "manifest.json").read_text().replace("__KEY__", key)
    json.loads(manifest)  # fail early on a broken manifest
    (out / "manifest.json").write_text(manifest)

zip_dir(dist / "firefox", dist / "gesit-firefox.xpi")
built = [dist / "chrome", dist / "firefox", dist / "gesit-firefox.xpi"]

if store:
    # The Chrome Web Store assigns its own id: the pinned dev `key` must not be in the uploaded manifest.
    web = dist / "store-chrome"
    shutil.copytree(dist / "chrome", web)
    m = json.loads((web / "manifest.json").read_text())
    m.pop("key", None)
    (web / "manifest.json").write_text(json.dumps(m, indent=2) + "\n")
    zip_dir(web, dist / "store" / "gesit-chrome-webstore.zip")
    shutil.rmtree(web)
    shutil.copy(dist / "gesit-firefox.xpi", dist / "store" / "gesit-firefox-amo.zip")
    built += [dist / "store" / "gesit-chrome-webstore.zip", dist / "store" / "gesit-firefox-amo.zip"]

print("built:", ", ".join(str(p.relative_to(root)) for p in built))
