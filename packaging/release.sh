#!/usr/bin/env bash
# Build every Linux release artifact into dist/ and write SHA256SUMS.
#
#   packaging/release.sh              source tarball, Arch package, .deb, browser-store zips
#   packaging/release.sh --appimage   also the AppImage (downloads CPython, wheels and appimagetool: see build_appimage.sh)
#
# env (optional overrides): GESIT_MAINTAINER="Name <mail>"  GESIT_HOMEPAGE=https://github.com/you/repo
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
VER="$(sed -n 's/^__version__ = "\(.*\)"/\1/p' gesit/__init__.py)"
DIST="$ROOT/dist"
rm -rf "$DIST"; mkdir -p "$DIST"

echo "==> tests"
"${PYTHON:-$ROOT/.venv/bin/python}" -m pytest tests -q

echo "==> source tarball (GitHub release asset, AUR source)"
tar --exclude-vcs --exclude='./.venv' --exclude='./build' --exclude='./dist' --exclude='__pycache__' \
    --exclude='.pytest_cache' --exclude='.impeccable' --exclude='./extension/dist' --exclude='*.pkg.tar.zst' \
    --exclude='./packaging/src' --exclude='./packaging/pkg' --exclude='*.egg-info' \
    --transform "s,^\./,gesit-$VER/," -czf "$DIST/gesit-$VER.tar.gz" .

echo "==> browser extension packages"
python3 extension/build.py --store
cp extension/dist/store/*.zip "$DIST/"
cp extension/dist/gesit-firefox.xpi "$DIST/gesit-$VER-firefox-unsigned.xpi"

echo "==> .deb"
"${PYTHON:-$ROOT/.venv/bin/python}" packaging/build_deb.py "$DIST"

echo "==> Arch package (built in a scratch copy so your tree stays clean)"
SCRATCH="$(mktemp -d)"
trap 'rm -rf "$SCRATCH"' EXIT
tar -xzf "$DIST/gesit-$VER.tar.gz" -C "$SCRATCH"
( cd "$SCRATCH/gesit-$VER/packaging" && makepkg -f --noconfirm >/dev/null && cp ./*.pkg.tar.zst "$DIST/" )

if [ "${1:-}" = "--appimage" ]; then
    echo "==> AppImage"
    "$ROOT/packaging/build_appimage.sh" "$DIST"
fi

( cd "$DIST" && sha256sum -- * > SHA256SUMS )
echo; ls -lh "$DIST"
