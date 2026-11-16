#!/bin/sh
# Per-user install (no root): app, launcher, desktop entry, icons, browser integration.
#   GESIT_AUTOSTART=1 ./install.sh     also start Gesit hidden at login
set -eu
HERE="$(cd "$(dirname "$0")/.." && pwd)"
PREFIX="${PREFIX:-$HOME/.local}"
APP="$PREFIX/share/gesit"
PY="${GESIT_PYTHON:-python3}"

if ! "$PY" -c "import PySide6.QtWidgets, httpx" 2>/dev/null; then
    echo "Missing dependencies. On Arch run:  sudo pacman -S pyside6 python-httpx" >&2
    echo "(optional: ffmpeg yt-dlp python-socksio)" >&2
    exit 1
fi

# migrate from the pre-rename LIDM install (keeps your data)
if [ -d "$PREFIX/share/lidm" ] || [ -x "$PREFIX/bin/lidm" ]; then
    echo "Found an old LIDM install: removing it and keeping your data..."
    PREFIX="$PREFIX" sh "$HERE/packaging/remove-old-lidm.sh"
fi

mkdir -p "$APP" "$PREFIX/bin" "$PREFIX/share/applications"
rm -rf "$APP/gesit"
cp -r "$HERE/gesit" "$APP/gesit"
find "$APP/gesit" -name __pycache__ -type d -prune -exec rm -rf {} +

# build the browser extensions and copy them to a stable path (overwrite in place: a browser that has the
# folder loaded unpacked keeps working)
"$PY" "$HERE/extension/build.py" >/dev/null
mkdir -p "$APP/extension"
cp -r "$HERE/extension/dist/." "$APP/extension/"

cat > "$PREFIX/bin/gesit" <<EOS
#!/bin/sh
export PYTHONPATH="$APP\${PYTHONPATH:+:\$PYTHONPATH}"
exec "$(command -v "$PY")" -m gesit "\$@"
EOS
chmod +x "$PREFIX/bin/gesit"

for s in 16 24 32 48 64 128 256 512; do
    mkdir -p "$PREFIX/share/icons/hicolor/${s}x${s}/apps"
    cp "$HERE/packaging/icons/hicolor/${s}x${s}/apps/gesit.png" "$PREFIX/share/icons/hicolor/${s}x${s}/apps/gesit.png"
done
mkdir -p "$PREFIX/share/icons/hicolor/scalable/apps"
cp "$HERE/packaging/gesit.svg" "$PREFIX/share/icons/hicolor/scalable/apps/gesit.svg"
sed "s|^Exec=.*|Exec=$PREFIX/bin/gesit %U|" "$HERE/packaging/gesit.desktop" > "$PREFIX/share/applications/gesit.desktop"

# browser integration: host launcher + manifests (also Flatpak browsers) + extension folders
GESIT_EXT_DIR="$APP/extension" PYTHONPATH="$APP" "$PY" -m gesit --install-host ${GESIT_AUTOSTART:+--autostart}

command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database "$PREFIX/share/applications" || true
command -v gtk-update-icon-cache >/dev/null 2>&1 && gtk-update-icon-cache -q -t "$PREFIX/share/icons/hicolor" || true
cat <<EOM

Gesit installed in $PREFIX
  Run: gesit   (or open "Gesit" from the app menu; make sure $PREFIX/bin is in PATH)
EOM
