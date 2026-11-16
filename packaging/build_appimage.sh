#!/usr/bin/env bash
# Build Gesit-<ver>-x86_64.AppImage: relocatable CPython (via uv) + PySide6 + httpx + Gesit.
# The result needs no system Python and runs on distros with glibc >= 2.28.
#
# usage: build_appimage.sh [OUTDIR]
#
# THIS SCRIPT DOWNLOADS (on first run), so read it before running:
#   1. a standalone CPython build         -> `uv python install` (astral-sh/python-build-standalone on GitHub)
#   2. PySide6-Essentials and httpx wheels -> PyPI, via `uv pip install`
#   3. appimagetool (ELF, runs on this machine) -> github.com/AppImage/appimagetool, cached in build/tools/
# Set APPIMAGETOOL=/path/to/appimagetool to use a copy you already trust instead of downloading it.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="${1:-$ROOT/dist}"
PYVER="${GESIT_APPIMAGE_PYTHON:-3.12}"
QTVER="${GESIT_PYSIDE_VERSION:-6.12.0}"
VER="$(sed -n 's/^__version__ = "\(.*\)"/\1/p' "$ROOT/gesit/__init__.py")"
WORK="$ROOT/build/appimage"
APPDIR="$WORK/AppDir"
TOOLS="$ROOT/build/tools"
rm -rf "$WORK"
mkdir -p "$APPDIR/usr/share/gesit" "$TOOLS" "$OUT"

echo "==> relocatable Python $PYVER"
uv python install "$PYVER" >/dev/null
PYBIN="$(uv python find --managed-python "$PYVER")"
PYROOT="$(dirname "$(dirname "$(readlink -f "$PYBIN")")")"
cp -a "$PYROOT" "$APPDIR/usr/python"
find "$APPDIR/usr/python" -name EXTERNALLY-MANAGED -delete
rm -rf "$APPDIR"/usr/python/lib/python*/test "$APPDIR"/usr/python/lib/python*/idlelib \
       "$APPDIR"/usr/python/lib/python*/tkinter "$APPDIR"/usr/python/lib/python*/ensurepip \
       "$APPDIR"/usr/python/lib/python*/turtledemo "$APPDIR/usr/python/share" "$APPDIR/usr/python/include"

echo "==> dependencies (PySide6-Essentials $QTVER, httpx)"
SITE="$APPDIR/usr/site-packages"
uv pip install --quiet --python "$APPDIR/usr/python/bin/python3" --target "$SITE" \
    "PySide6-Essentials==$QTVER" httpx

echo "==> trimming Qt modules Gesit does not use"
PS="$SITE/PySide6"
for m in Qml Quick QuickControls2 QuickTemplates2 QuickWidgets QuickShapes QuickLayouts QuickTest Pdf PdfWidgets \
         Designer UiTools Sql Test Help Concurrent PrintSupport OpenGLWidgets Multimedia MultimediaWidgets \
         WebChannel WebEngineCore Nfc Bluetooth Sensors Location Positioning RemoteObjects Scxml StateMachine \
         TextToSpeech 3DCore Charts DataVisualization Graphs HttpServer SpatialAudio SerialPort SerialBus; do
    rm -f "$PS"/Qt${m}.abi3.so "$PS"/Qt${m}.pyi "$PS"/Qt/lib/libQt6${m}*.so* 2>/dev/null || true
done
rm -rf "$PS/Qt/qml" "$PS/Qt/translations" "$PS/Qt/metatypes" "$PS/Qt/libexec" "$PS/examples" "$PS/include" \
       "$PS/glue" "$PS/typesystems" "$PS/scripts" "$PS/support" "$PS/QtAsyncio"
rm -f "$PS"/designer "$PS"/uic "$PS"/rcc "$PS"/lupdate "$PS"/assistant "$PS"/linguist "$PS"/qmllint \
      "$PS"/qmlformat "$PS"/qmlls "$PS"/svgtoqml 2>/dev/null || true
rm -rf "$PS/Qt/plugins/designer" "$PS/Qt/plugins/sqldrivers" "$PS/Qt/plugins/qmltooling" \
       "$PS/Qt/plugins/networkinformation" "$PS/Qt/plugins/multimedia" "$PS/Qt/plugins/tls" \
       "$PS/Qt/plugins/position" "$PS/Qt/plugins/sensors"
find "$SITE" -name "__pycache__" -type d -prune -exec rm -rf {} + 2>/dev/null || true
find "$SITE" -name "*.pyi" -delete 2>/dev/null || true

echo "==> Gesit itself, browser extensions, desktop files"
cp -r "$ROOT/gesit" "$SITE/gesit"
find "$SITE/gesit" -name "__pycache__" -type d -prune -exec rm -rf {} +
python3 "$ROOT/extension/build.py" >/dev/null
mkdir -p "$APPDIR/usr/share/gesit/extension" "$APPDIR/usr/share/applications" \
         "$APPDIR/usr/share/icons/hicolor/scalable/apps"
cp -r "$ROOT/extension/dist/chrome" "$ROOT/extension/dist/firefox" "$APPDIR/usr/share/gesit/extension/"
cp "$ROOT/packaging/gesit.desktop" "$APPDIR/gesit.desktop"
cp "$ROOT/packaging/gesit.desktop" "$APPDIR/usr/share/applications/gesit.desktop"
cp -r "$ROOT/packaging/icons/hicolor/." "$APPDIR/usr/share/icons/hicolor/"
cp "$ROOT/packaging/gesit.svg" "$APPDIR/usr/share/icons/hicolor/scalable/apps/gesit.svg"
cp "$ROOT/packaging/icons/hicolor/256x256/apps/gesit.png" "$APPDIR/gesit.png"
ln -sf gesit.png "$APPDIR/.DirIcon"
cp "$ROOT/LICENSE" "$APPDIR/usr/share/gesit/LICENSE"

cat > "$APPDIR/AppRun" <<'RUN'
#!/bin/sh
HERE="$(dirname "$(readlink -f "$0")")"
export APPDIR="$HERE"
export PYTHONHOME="$HERE/usr/python"
export PYTHONPATH="$HERE/usr/site-packages"
export PYTHONNOUSERSITE=1
export PYTHONDONTWRITEBYTECODE=1
export GESIT_EXT_DIR="$HERE/usr/share/gesit/extension"
exec "$HERE/usr/python/bin/python3" -m gesit "$@"
RUN
chmod +x "$APPDIR/AppRun"

echo "==> appimagetool"
TOOL="${APPIMAGETOOL:-$TOOLS/appimagetool-x86_64.AppImage}"
if [ ! -x "$TOOL" ]; then
    curl -fsSL -o "$TOOL" \
        "https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-x86_64.AppImage"
    chmod +x "$TOOL"
fi
FINAL="$OUT/Gesit-$VER-x86_64.AppImage"
ARCH=x86_64 "$TOOL" --appimage-extract-and-run "$APPDIR" "$FINAL" >"$WORK/appimagetool.log" 2>&1 \
    || { tail -20 "$WORK/appimagetool.log"; exit 1; }
chmod +x "$FINAL"
du -h "$FINAL"
