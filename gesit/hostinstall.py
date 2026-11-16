"""Browser integration setup: Native Messaging manifests, the host launcher and the extension files."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys

from .core import paths

HOST_NAME = "app.gesit.host"
FIREFOX_EXTENSION_ID = "gesit@gesit.app"
# Extension id derived from the `key` pinned in extension/chrome/manifest.json (unpacked / sideloaded builds)
DEV_EXTENSION_ID = "npmaegfkbmjnpndhkdekimmlbilkefjo"
# Fill in the id Chrome Web Store assigns after the first upload, then ship a new release.
STORE_EXTENSION_IDS: list[str] = []

HOME = os.path.expanduser("~")
CHROMIUM_DIRS = {
    "Google Chrome": ".config/google-chrome",
    "Chromium": ".config/chromium",
    "Brave": ".config/BraveSoftware/Brave-Browser",
    "Vivaldi": ".config/vivaldi",
    "Microsoft Edge": ".config/microsoft-edge",
    "Opera": ".config/opera",
}
FIREFOX_DIRS = {
    "Firefox": ".mozilla/native-messaging-hosts",
    "LibreWolf": ".librewolf/native-messaging-hosts",
}
# Flatpak browsers: app id -> (name, manifest dir relative to ~/.var/app/<id>, is_firefox)
FLATPAK_BROWSERS = {
    "com.brave.Browser": ("Brave (Flatpak)", "config/BraveSoftware/Brave-Browser/NativeMessagingHosts", False),
    "com.google.Chrome": ("Chrome (Flatpak)", "config/google-chrome/NativeMessagingHosts", False),
    "org.chromium.Chromium": ("Chromium (Flatpak)", "config/chromium/NativeMessagingHosts", False),
    "com.microsoft.Edge": ("Edge (Flatpak)", "config/microsoft-edge/NativeMessagingHosts", False),
    "com.vivaldi.Vivaldi": ("Vivaldi (Flatpak)", "config/vivaldi/NativeMessagingHosts", False),
    "org.mozilla.firefox": ("Firefox (Flatpak)", ".mozilla/native-messaging-hosts", True),
}
SANDBOX_GRANT = "xdg-run/gesit"  # the only thing a sandboxed browser gets: Gesit's socket directory
SYSTEM_MANIFESTS = [
    "/etc/opt/chrome/native-messaging-hosts", "/etc/chromium/native-messaging-hosts",
    "/etc/opt/edge/native-messaging-hosts", "/etc/opt/vivaldi/native-messaging-hosts",
    "/etc/brave/native-messaging-hosts", "/usr/lib/mozilla/native-messaging-hosts",
]


def extension_ids(extra: list[str] | None = None) -> list[str]:
    env = [i.strip() for i in os.environ.get("GESIT_EXTENSION_IDS", "").split(",") if i.strip()]
    return list(dict.fromkeys([DEV_EXTENSION_ID, *STORE_EXTENSION_IDS, *env, *(extra or [])]))


def _manifest(path: str, firefox: bool, extra_ids: list[str] | None = None) -> dict:
    m = {"name": HOST_NAME, "description": "Gesit native messaging host", "path": path, "type": "stdio"}
    if firefox:
        m["allowed_extensions"] = [FIREFOX_EXTENSION_ID]
    else:
        m["allowed_origins"] = [f"chrome-extension://{i}/" for i in extension_ids(extra_ids)]
    return m


def _write_manifest(path: str, host: str, firefox: bool, extra_ids: list[str] | None) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(_manifest(host, firefox, extra_ids), f, indent=2)


def _targets() -> list[tuple[str, str, bool, str]]:
    """(browser, manifest path, is_firefox, browser profile root that must exist)."""
    out = []
    for name, d in CHROMIUM_DIRS.items():
        root = os.path.join(HOME, d)
        out.append((name, os.path.join(root, "NativeMessagingHosts", HOST_NAME + ".json"), False, root))
    for name, d in FIREFOX_DIRS.items():
        out.append((name, os.path.join(HOME, d, HOST_NAME + ".json"), True, os.path.join(HOME, d.split("/")[0])))
    return out


# ------------------------------------------------------------------ the host launcher
def write_standalone_host(path: str, app_bin: str | None = None) -> str:
    """Self-contained copy of native_host.py (stdlib only) that needs nothing but a system python3.

    Used for AppImage (the app is a squashfs mount) and Flatpak browsers (the sandbox cannot see our install).
    `app_bin` is the executable the host starts when Gesit is not running yet."""
    src = os.path.join(os.path.dirname(os.path.abspath(__file__)), "native_host.py")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(src) as f, open(path, "w") as out:
        out.write("#!/usr/bin/python3\n")
        if app_bin:
            out.write(f"import os as _os\n_os.environ.setdefault('GESIT_BIN', {app_bin!r})\n")
        out.write(f.read())
    os.chmod(path, 0o755)
    return path


def make_wrapper(path: str) -> str:
    """Launcher script for a source / venv checkout: runs the host with the interpreter used right now."""
    pkg_parent = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(
            "#!/bin/sh\n"
            f'export PYTHONPATH="{pkg_parent}${{PYTHONPATH:+:$PYTHONPATH}}"\n'
            f'exec "{sys.executable}" -m gesit.native_host "$@"\n'
        )
    os.chmod(path, 0o755)
    return path


def default_host_launcher() -> str:
    """Pick the right launcher for how Gesit is installed."""
    appimage = os.environ.get("APPIMAGE")
    if appimage:
        return write_standalone_host(os.path.join(paths.data_dir(), "gesit-native-host"), appimage)
    system = shutil.which("gesit-native-host")
    if system and os.path.dirname(system) in ("/usr/bin", "/usr/local/bin"):
        return system
    return make_wrapper(os.path.join(paths.data_dir(), "gesit-native-host"))


# --------------------------------------------------------------------- extension files
def _find_extension_dir() -> str | None:
    cands = [os.environ.get("GESIT_EXT_DIR"), os.path.join(sys.prefix, "share/gesit/extension"),
             os.path.join(paths.data_dir(), "extension"), "/usr/share/gesit/extension"]
    for c in cands:
        if c and os.path.isdir(os.path.join(c, "chrome")):
            return c
    return None


def install_extension_files(dest_root: str | None = None) -> str | None:
    """Copy the unpacked extensions somewhere permanent (an AppImage mount disappears on exit)."""
    src = _find_extension_dir()
    if not src:
        return None
    dest = dest_root or os.path.join(paths.data_dir(), "extension")
    if os.path.abspath(src) == os.path.abspath(dest):
        return dest
    for kind in ("chrome", "firefox"):
        if os.path.isdir(os.path.join(src, kind)):
            shutil.copytree(os.path.join(src, kind), os.path.join(dest, kind), dirs_exist_ok=True)
    return dest


# ----------------------------------------------------------------------------- install
def install(wrapper: str, extra_ids: list[str] | None = None) -> list[str]:
    """Write a manifest for every browser that is installed (profile root exists)."""
    done = []
    for name, path, firefox, root in _targets():
        if os.path.isdir(root):
            _write_manifest(path, wrapper, firefox, extra_ids)
            done.append(name)
    return done


def install_flatpak(extra_ids: list[str] | None = None) -> list[tuple[str, str | None]]:
    """Host + extension inside each installed Flatpak browser, plus a grant for Gesit's socket directory only.

    A sandboxed browser cannot read ~/.local/share, but it always sees its own data dir.
    Returns (browser name, extension folder)."""
    done = []
    ext_src = _find_extension_dir()
    for app_id, (name, rel, firefox) in FLATPAK_BROWSERS.items():
        app_dir = os.path.join(HOME, ".var", "app", app_id)
        if not os.path.isdir(app_dir):
            continue
        host = write_standalone_host(os.path.join(app_dir, "data", "gesit", "gesit-native-host"),
                                     os.environ.get("APPIMAGE") or shutil.which("gesit"))
        _write_manifest(os.path.join(app_dir, rel, HOST_NAME + ".json"), host, firefox, extra_ids)
        if shutil.which("flatpak"):
            subprocess.run(["flatpak", "override", "--user", f"--filesystem={SANDBOX_GRANT}", app_id],
                           check=False, capture_output=True)
        ext_dst = None
        if ext_src:
            kind = "firefox" if firefox else "chrome"
            ext_dst = os.path.join(app_dir, "data", "gesit", "extension", kind)
            shutil.copytree(os.path.join(ext_src, kind), ext_dst, dirs_exist_ok=True)
        done.append((name, ext_dst))
    run = os.environ.get("XDG_RUNTIME_DIR")
    if done and run:
        os.makedirs(os.path.join(run, "gesit"), mode=0o700, exist_ok=True)  # must exist when the browser starts
    return done


def uninstall() -> list[str]:
    removed = []
    for name, path, _fx, _root in _targets():
        if os.path.exists(path):
            os.remove(path)
            removed.append(name)
    return removed


def uninstall_flatpak() -> list[str]:
    removed = []
    for app_id, (name, rel, _fx) in FLATPAK_BROWSERS.items():
        app_dir = os.path.join(HOME, ".var", "app", app_id)
        manifest = os.path.join(app_dir, rel, HOST_NAME + ".json")
        if os.path.exists(manifest):
            os.remove(manifest)
            shutil.rmtree(os.path.join(app_dir, "data", "gesit"), ignore_errors=True)
            if shutil.which("flatpak"):
                subprocess.run(["flatpak", "override", "--user", f"--nofilesystem={SANDBOX_GRANT}", app_id],
                               check=False, capture_output=True)
            removed.append(name)
    return removed


def setup(extra_ids: list[str] | None = None) -> dict:
    """Everything needed for browser integration. Returns a summary for the CLI / the GUI."""
    wrapper = default_host_launcher()
    browsers = install(wrapper, extra_ids)
    flat = install_flatpak(extra_ids)
    ext_dir = install_extension_files()
    return {"host": wrapper, "browsers": browsers, "flatpak": flat, "extension_dir": ext_dir}


def is_integrated() -> bool:
    """True if any user-level or system-wide manifest exists (used to decide whether to offer setup)."""
    if any(os.path.exists(p) for _n, p, _f, _r in _targets()):
        return True
    if any(os.path.exists(os.path.join(HOME, ".var", "app", a, rel, HOST_NAME + ".json"))
           for a, (_n, rel, _f) in FLATPAK_BROWSERS.items()):
        return True
    return any(os.path.exists(os.path.join(d, HOST_NAME + ".json")) for d in SYSTEM_MANIFESTS)


def refresh_appimage_host() -> None:
    """If the AppImage was moved/updated, repoint an already-installed host launcher at the new path."""
    appimage = os.environ.get("APPIMAGE")
    host = os.path.join(paths.data_dir(), "gesit-native-host")
    if not appimage or not os.path.exists(host):
        return
    try:
        with open(host) as f:
            if repr(appimage) in f.read(2048):
                return
    except OSError:
        return
    setup()


def system_manifests(host_path: str = "/usr/bin/gesit-native-host") -> dict[str, str]:
    """{path relative to /: manifest JSON} for packages that install system-wide (deb, Arch)."""
    out = {}
    for d in SYSTEM_MANIFESTS:
        firefox = "mozilla" in d
        out[os.path.join(d, HOST_NAME + ".json").lstrip("/")] = json.dumps(_manifest(host_path, firefox), indent=2) + "\n"
    return out
