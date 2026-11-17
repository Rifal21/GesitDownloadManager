"""The Windows IPC transport (TCP on 127.0.0.1 + token), forced on with GESIT_IPC=tcp so Linux can test it."""
import asyncio
import json
import os
import socket
import struct
import subprocess
import sys
import threading

import pytest

from gesit.core import ipc

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture()
def tcp_env(tmp_path, monkeypatch):
    f = str(tmp_path / "data" / "ipc.json")
    monkeypatch.setenv("GESIT_IPC", "tcp")
    monkeypatch.setenv("GESIT_IPC_FILE", f)
    return f


async def echo(msg):
    return {"ok": True, "echo": msg}


def test_roundtrip_with_token_and_cleanup(tcp_env):
    async def go():
        srv = ipc.IPCServer(echo)
        await srv.start()
        loop = asyncio.get_running_loop()
        info = json.load(open(tcp_env))
        assert info["port"] == srv.port and len(info["token"]) == 32
        r = await loop.run_in_executor(None, lambda: ipc.send({"cmd": "ping"}))
        running = await loop.run_in_executor(None, ipc.is_running)
        await srv.stop()
        return r, running

    r, running = asyncio.run(go())
    assert r == {"ok": True, "echo": {"cmd": "ping"}}, "the token must be stripped before the handler sees it"
    assert running
    assert not os.path.exists(tcp_env), "stop() removes the published port/token"
    assert not ipc.is_running()


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission bits")
def test_ipc_file_is_private(tcp_env):
    async def go():
        srv = ipc.IPCServer(echo)
        await srv.start()
        mode = os.stat(tcp_env).st_mode & 0o777
        await srv.stop()
        return mode

    assert asyncio.run(go()) == 0o600


def raw(port, payload: bytes) -> bytes:
    s = socket.create_connection(("127.0.0.1", port), timeout=5)
    s.sendall(payload)
    s.shutdown(socket.SHUT_WR)
    out = b""
    while chunk := s.recv(4096):
        out += chunk
    s.close()
    return out


def test_requests_without_or_with_wrong_token_are_rejected(tcp_env):
    async def go():
        srv = ipc.IPCServer(echo)
        await srv.start()
        loop = asyncio.get_running_loop()
        call = lambda payload: loop.run_in_executor(None, raw, srv.port, payload)  # noqa: E731
        no_token = await call(b'{"cmd":"add","url":"http://x"}\n')
        bad_token = await call(b'{"cmd":"add","token":"0000","url":"http://x"}\n')
        # what a web page can do: send an HTTP request to the port
        http = await call(b"POST / HTTP/1.1\r\nHost: 127.0.0.1\r\nContent-Type: text/plain\r\n\r\n"
                          b'{"cmd":"add","url":"http://evil"}\n')
        await srv.stop()
        return no_token, bad_token, http

    for out in asyncio.run(go()):
        reply = json.loads(out)
        assert reply["ok"] is False and "echo" not in reply


def test_native_host_speaks_tcp_and_reports_not_running(tcp_env, tmp_path):
    env = dict(os.environ, PYTHONPATH=REPO)

    def call(msg):
        p = subprocess.Popen([sys.executable, "-m", "gesit.native_host"], stdin=subprocess.PIPE,
                             stdout=subprocess.PIPE, env=env, cwd=REPO)
        body = json.dumps(msg).encode()
        p.stdin.write(struct.pack("=I", len(body)) + body)
        p.stdin.flush()
        (n,) = struct.unpack("=I", p.stdout.read(4))
        out = json.loads(p.stdout.read(n))
        p.stdin.close()
        p.wait(10)
        return out

    assert call({"cmd": "ping"}) == {"ok": False, "error": "Gesit is not running", "running": False}

    ready = threading.Event()

    def serve():
        async def main():
            srv = ipc.IPCServer(echo)
            await srv.start()
            ready.set()
            await asyncio.sleep(5)
            await srv.stop()

        asyncio.run(main())

    threading.Thread(target=serve, daemon=True).start()
    assert ready.wait(5)
    reply = call({"cmd": "get_config"})
    assert reply["ok"] is True and reply["echo"] == {"cmd": "get_config"}
