#!/bin/sh
set -eu
PREFIX="${PREFIX:-$HOME/.local}"
APP="$PREFIX/share/gesit"
"$PREFIX/bin/gesit" --quit >/dev/null 2>&1 || true
PYTHONPATH="$APP" python3 -m gesit --uninstall-host || true
rm -rf "$APP" "$PREFIX/bin/gesit" "$PREFIX/share/applications/gesit.desktop" "$HOME/.config/autostart/gesit.desktop"
rm -f "$PREFIX/share/icons/hicolor/scalable/apps/gesit.svg"
for s in 16 24 32 48 64 128 256 512; do rm -f "$PREFIX/share/icons/hicolor/${s}x${s}/apps/gesit.png"; done
echo "Gesit removed (your downloads, ~/.config/gesit and the download list in ~/.local/share/gesit were kept)."
