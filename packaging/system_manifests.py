#!/usr/bin/env python3
"""Write the system-wide native messaging manifests under a destination root: system_manifests.py DESTDIR"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from gesit import hostinstall  # noqa: E402

dest = sys.argv[1]
for rel, content in hostinstall.system_manifests().items():
    path = os.path.join(dest, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(content)
    os.chmod(path, 0o644)
print(f"wrote {len(hostinstall.system_manifests())} manifests under {dest}")
