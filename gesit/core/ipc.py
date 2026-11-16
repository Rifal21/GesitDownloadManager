"""Unix-socket IPC between the GUI process, the CLI and the native-messaging host.

Protocol: one JSON object per line, one request -> one response.
"""

from __future__ import annotations

import asyncio
import json
import os
import socket
from typing import Awaitable, Callable


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
        self.path = path or socket_path()
        self._server: asyncio.AbstractServer | None = None

    async def start(self) -> None:
        if os.path.exists(self.path):
            os.unlink(self.path)  # stale socket: the single-instance check already ran
        self._server = await asyncio.start_unix_server(self._client, path=self.path)
        os.chmod(self.path, 0o600)

    async def stop(self) -> None:
        if self._server:
            self._server.close()
        try:
            os.unlink(self.path)
        except OSError:
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
                resp = await self.handler(msg)
            except Exception as e:  # noqa: BLE001 - always answer the client
                resp = {"ok": False, "error": f"{type(e).__name__}: {e}"}
            writer.write(json.dumps(resp).encode() + b"\n")
            await writer.drain()
        except (asyncio.TimeoutError, ConnectionError):
            pass
        finally:
            writer.close()


def send(msg: dict, path: str | None = None, timeout: float = 5.0) -> dict:
    """Blocking client used by the CLI and the native host."""
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect(path or socket_path())
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


def is_running(path: str | None = None) -> bool:
    try:
        return bool(send({"cmd": "ping"}, path, timeout=1.5).get("ok"))
    except (OSError, ValueError):
        return False
