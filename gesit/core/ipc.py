"""IPC between the GUI process, the CLI and the native-messaging host.

Protocol: one JSON object per line, one request -> one response.

Transports
  * Linux/macOS: a Unix socket in a private directory (permissions are the access control).
  * Windows (or GESIT_IPC=tcp for tests): TCP on 127.0.0.1 with a random port. The port and a random token are
    written to ipc.json in the per-user data folder; every request must carry the token. A web page can reach
    127.0.0.1 but cannot read that file, so it cannot talk to Gesit.
"""

from __future__ import annotations

import asyncio
import hmac
import json
import os
import secrets
import socket
import sys
from typing import Awaitable, Callable

from . import paths


def use_tcp() -> bool:
    return sys.platform == "win32" or os.environ.get("GESIT_IPC") == "tcp"


def ipc_file() -> str:
    """Where a TCP-mode server publishes {port, token}. Keep in sync with native_host._ipc_file."""
    return os.environ.get("GESIT_IPC_FILE") or os.path.join(paths.data_dir(), "ipc.json")


def socket_path() -> str:
    """$XDG_RUNTIME_DIR/gesit/gesit.sock (a private dir, so Flatpak browsers can be granted only this dir).

    Falls back to /tmp/gesit-<uid>/ when that path is too long for AF_UNIX."""
    run = os.environ.get("XDG_RUNTIME_DIR")
    if run and len(os.path.join(run, "gesit", "gesit.sock").encode()) <= 100:
        base = os.path.join(run, "gesit")
    else:
        base = f"/tmp/gesit-{os.getuid()}"
    os.makedirs(base, mode=0o700, exist_ok=True)
    st = os.lstat(base)
    if st.st_uid != os.getuid() or st.st_mode & 0o077:  # someone else pre-created it
        raise PermissionError(f"{base} is not private to this user")
    return os.path.join(base, "gesit.sock")


Handler = Callable[[dict], Awaitable[dict]]


class IPCServer:
    def __init__(self, handler: Handler, path: str | None = None):
        self.handler = handler
        self.tcp = use_tcp()
        self.path = path or (ipc_file() if self.tcp else socket_path())
        self.token = secrets.token_hex(16)
        self.port = 0
        self._server: asyncio.AbstractServer | None = None

    async def start(self) -> None:
        if self.tcp:
            self._server = await asyncio.start_server(self._client, "127.0.0.1", 0)
            self.port = self._server.sockets[0].getsockname()[1]
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_BINARY", 0), 0o600)
            with os.fdopen(fd, "w") as f:
                json.dump({"port": self.port, "token": self.token, "pid": os.getpid()}, f)
            return
        if os.path.exists(self.path):
            os.unlink(self.path)  # stale socket: the single-instance check already ran
        self._server = await asyncio.start_unix_server(self._client, path=self.path)
        os.chmod(self.path, 0o600)

    async def stop(self) -> None:
        if self._server:
            self._server.close()
        try:
            if self.tcp:  # only remove our own file: a newer instance may have replaced it
                with open(self.path) as f:
                    if json.load(f).get("token") != self.token:
                        return
            os.unlink(self.path)
        except (OSError, ValueError):
            pass

    async def _client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            line = await asyncio.wait_for(reader.readline(), 10)
            if not line:
                return
            try:
                msg = json.loads(line)
                if not isinstance(msg, dict):
                    raise ValueError("expected an object")
                if self.tcp:
                    given = str(msg.pop("token", ""))
                    if not hmac.compare_digest(given.encode(), self.token.encode()):
                        raise PermissionError("unauthorized")
                resp = await self.handler(msg)
            except Exception as e:  # noqa: BLE001 - always answer the client
                resp = {"ok": False, "error": f"{type(e).__name__}: {e}"}
            writer.write(json.dumps(resp).encode() + b"\n")
            await writer.drain()
        except (asyncio.TimeoutError, ConnectionError):
            pass
        finally:
            writer.close()


def _exchange(s: socket.socket, msg: dict) -> dict:
    s.sendall(json.dumps(msg).encode() + b"\n")
    buf = b""
    while not buf.endswith(b"\n"):
        chunk = s.recv(65536)
        if not chunk:
            break
        buf += chunk
    return json.loads(buf or b"{}")


def send(msg: dict, path: str | None = None, timeout: float = 5.0) -> dict:
    """Blocking client used by the CLI and the native host. `path` overrides the socket / ipc.json location."""
    if use_tcp():
        with open(path or ipc_file()) as f:  # FileNotFoundError is an OSError: callers treat it as "not running"
            info = json.load(f)
        s = socket.create_connection(("127.0.0.1", int(info["port"])), timeout=timeout)
        try:
            return _exchange(s, {**msg, "token": info["token"]})
        finally:
            s.close()
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect(path or socket_path())
        return _exchange(s, msg)
    finally:
        s.close()


def is_running(path: str | None = None) -> bool:
    try:
        return bool(send({"cmd": "ping"}, path, timeout=1.5).get("ok"))
    except (OSError, ValueError, KeyError):
        return False
