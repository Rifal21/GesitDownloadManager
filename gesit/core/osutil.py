"""Small OS-specific helpers (Linux and Windows) so the rest of the code stays portable."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys

WINDOWS = sys.platform == "win32"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
# CREATE_NO_WINDOW: no console flashes up when a GUI program starts ffmpeg / yt-dlp
NO_WINDOW = 0x08000000 if WINDOWS else 0


def _app_dir() -> str | None:
    """Folder of a PyInstaller build (where gesit.exe and tools/ live), else None."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return None


def tool_path(name: str) -> str | None:
    """Find ffmpeg / yt-dlp: bundled copy first (installer ships them), then PATH."""
    exe = name + (".exe" if WINDOWS else "")
    dirs = [os.environ.get("GESIT_TOOLS_DIR")]
    app = _app_dir()
    if app:
        dirs += [os.path.join(app, "tools"), app]
    for d in dirs:
        if d and os.path.isfile(os.path.join(d, exe)):
            return os.path.join(d, exe)
    found = shutil.which(name)
    if found:
        return found
    local = os.path.expanduser(os.path.join("~", ".local", "bin", name))  # pip --user installs
    return local if not WINDOWS and os.path.isfile(local) else None


def power_action(action: str, delay_s: int = 0) -> None:
    """action: 'poweroff' or 'suspend'. delay_s is honoured on Windows (`shutdown /a` can still cancel it)."""
    if WINDOWS:
        cmd = ["shutdown", "/s", "/t", str(delay_s)] if action == "poweroff" else \
            ["rundll32.exe", "powrprof.dll,SetSuspendState", "0,1,0"]
    else:
        cmd = ["systemctl", action]
    subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=NO_WINDOW)


def reveal_command(path: str) -> list[str] | None:
    """Command that opens the file manager with `path` selected (Windows Explorer); None where unsupported."""
    return ["explorer", f"/select,{os.path.normpath(path)}"] if WINDOWS and os.path.isfile(path) else None


def autostart_command() -> str:
    """The command line autostart should run."""
    appimage = os.environ.get("APPIMAGE")
    if appimage:
        return f'"{appimage}"'
    app = _app_dir()
    if app and WINDOWS:
        return f'"{os.path.join(app, "gesit.exe")}"'
    exe = shutil.which("gesit")
    return exe if exe else f'"{sys.executable}" -m gesit'


def enable_autostart() -> str:
    cmd = autostart_command() + " --background"
    if WINDOWS:
        import winreg

        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            winreg.SetValueEx(k, "Gesit", 0, winreg.REG_SZ, cmd)
        return "Autostart enabled: Gesit starts hidden when you sign in."
    d = os.path.join(os.path.expanduser("~/.config"), "autostart")
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "gesit.desktop"), "w") as f:
        f.write(f"[Desktop Entry]\nType=Application\nName=Gesit\nExec={cmd}\nIcon=gesit\n"
                "X-GNOME-Autostart-enabled=true\n")
    return "Autostart enabled: Gesit starts hidden at login."


def disable_autostart() -> None:
    if WINDOWS:
        import winreg

        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
                winreg.DeleteValue(k, "Gesit")
        except OSError:
            pass
        return
    try:
        os.remove(os.path.join(os.path.expanduser("~/.config"), "autostart", "gesit.desktop"))
    except OSError:
        pass
