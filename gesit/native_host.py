"""Native Messaging host: bridges the browser extension (stdio) to the Gesit socket.

Deliberately dependency-free and light: it is spawned by the browser for every
message batch. If Gesit is not running it starts it in the background.
"""

import json
import os
import socket
import struct
import subprocess
import sys
import time


IN_FLATPAK = os.path.exists("/.flatpak-info")


def _socket_path():
    # keep in sync with gesit.core.ipc.socket_path (this module stays import-light)
    run = os.environ.get("XDG_RUNTIME_DIR")
    if run and len(os.path.join(run, "gesit", "gesit.sock").encode()) <= 100:
        return os.path.join(run, "gesit", "gesit.sock")
    return os.path.join(f"/tmp/gesit-{os.getuid()}", "gesit.sock")


def _read_message():
    raw = sys.stdin.buffer.read(4)
    if len(raw) < 4:
        return None
    (length,) = struct.unpack("=I", raw)
    return json.loads(sys.stdin.buffer.read(length).decode("utf-8"))


def _write_message(obj):
    data = json.dumps(obj).encode("utf-8")
    sys.stdout.buffer.write(struct.pack("=I", len(data)) + data)
    sys.stdout.buffer.flush()


def _send(msg, timeout=8.0):
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect(_socket_path())
        s.sendall(json.dumps(msg).encode() + b"\n")
        buf = b""
        while not buf.endswith(b"\n"):
            chunk = s.recv(65536)
            if not chunk:
                break
            buf += chunk
        return json.loads(buf or b"{}")
    finally:
        s.close()


def _launch_app():
    import shutil

    exe = os.environ.get("GESIT_BIN") or shutil.which("gesit")
    cmd = [exe] if exe else [sys.executable, "-m", "gesit"]
    subprocess.Popen(
        cmd + ["--background"],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


LAUNCHING_COMMANDS = ("add", "add_many", "show")


def _forward(msg):
    try:
        return _send(msg)
    except OSError:
        pass
    if msg.get("cmd") not in LAUNCHING_COMMANDS:
        # status checks must not start the app every time the browser opens
        return {"ok": False, "error": "Gesit is not running", "running": False}
    if IN_FLATPAK:
        # a sandboxed browser cannot start host programs; the user must start Gesit (or autostart it)
        return {"ok": False, "error": "Gesit is not running. Start it first (gesit --install-host --autostart keeps it running)."}
    _launch_app()
    deadline = time.time() + 10
    while time.time() < deadline:
        time.sleep(0.3)
        try:
            return _send(msg)
        except OSError:
            continue
    return {"ok": False, "error": "Gesit is not running and could not be started"}


def main():
    while True:
        msg = _read_message()
        if msg is None:
            return
        try:
            _write_message(_forward(msg))
        except Exception as e:  # noqa: BLE001
            _write_message({"ok": False, "error": str(e)})


if __name__ == "__main__":
    main()
