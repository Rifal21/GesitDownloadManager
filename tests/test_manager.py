import asyncio
import hashlib
import os
import struct
import subprocess
import sys
import threading
import time

import pytest

from gesit.core import ipc
from gesit.core.config import Config
from gesit.core.db import Database
from gesit.core.manager import Manager
from gesit.core.models import COMPLETE, ERROR, QUEUED, STOPPED

from . import rangeserver as rs


@pytest.fixture()
def server():
    s = rs.RangeServer().start()
    yield s
    s.stop()


@pytest.fixture()
def mgr(tmp_path):
    cfg = Config(download_root=str(tmp_path / "dl"), proxy_mode="none")
    m = Manager(cfg, Database(str(tmp_path / "db.sqlite")))
    m.start()
    yield m
    m.shutdown()


def wait_for(cond, timeout=20):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.05)
    return False


def test_add_and_complete_goes_to_category(mgr, server):
    data = rs.reset(1024 * 1024)
    it = mgr.add(server.url("/movie.mp4"))
    assert wait_for(lambda: mgr.get(it.id).status == COMPLETE), mgr.get(it.id).error
    done = mgr.get(it.id)
    assert done.category == "Video"
    assert done.save_dir.endswith("Video")
    assert hashlib.sha256(open(done.path, "rb").read()).hexdigest() == rs.sha(data)
    assert done.percent == 100


def test_download_later_waits_for_queue(mgr, server):
    rs.reset(200 * 1024)
    a = mgr.add(server.url("/a.zip"), start=False)
    b = mgr.add(server.url("/b.zip"), start=False)
    assert mgr.get(a.id).status == QUEUED
    time.sleep(0.5)
    assert mgr.get(a.id).status == QUEUED
    mgr.start_queue()
    assert wait_for(lambda: mgr.get(a.id).status == COMPLETE and mgr.get(b.id).status == COMPLETE)
    assert not mgr.queue_running


def test_queue_respects_max_active(mgr, server):
    rs.reset(2 * 1024 * 1024)
    rs.State.delay_per_chunk = 0.03
    mgr.cfg.connections = 1
    mgr.cfg.max_active = 2
    ids = [mgr.add(server.url(f"/f{i}.bin"), start=False).id for i in range(4)]
    mgr.start_queue()
    time.sleep(0.5)
    active = [i for i in ids if mgr.get(i).status in ("connecting", "downloading")]
    assert len(active) == 2
    rs.State.delay_per_chunk = 0
    assert wait_for(lambda: all(mgr.get(i).status == COMPLETE for i in ids), 40)


def test_stop_then_resume(mgr, server):
    data = rs.reset(6 * 1024 * 1024)
    rs.State.delay_per_chunk = 0.02
    mgr.cfg.connections = 2
    it = mgr.add(server.url("/big.iso"))
    assert wait_for(lambda: mgr.get(it.id).downloaded > 500_000)
    mgr.stop_item(it.id)
    assert wait_for(lambda: mgr.get(it.id).status == STOPPED)
    part = mgr.get(it.id)
    assert 0 < part.downloaded < len(data)
    rs.State.delay_per_chunk = 0
    mgr.start_item(it.id)
    assert wait_for(lambda: mgr.get(it.id).status == COMPLETE)
    assert hashlib.sha256(open(mgr.get(it.id).path, "rb").read()).hexdigest() == rs.sha(data)


def test_error_status_and_persistence(tmp_path, server):
    rs.reset(100)
    cfg = Config(download_root=str(tmp_path / "dl"), proxy_mode="none", retries=0)
    db = Database(str(tmp_path / "db.sqlite"))
    m = Manager(cfg, db)
    m.start()
    it = m.add(server.url("/missing"))
    assert wait_for(lambda: m.get(it.id).status == ERROR)
    m.shutdown()
    m2 = Manager(cfg, Database(str(tmp_path / "db.sqlite")))
    assert m2.get(it.id).status == ERROR
    assert "404" in m2.get(it.id).error


def test_remove_and_remove_completed(mgr, server):
    rs.reset(50 * 1024)
    it = mgr.add(server.url("/x.pdf"))
    assert wait_for(lambda: mgr.get(it.id).status == COMPLETE)
    path = mgr.get(it.id).path
    mgr.remove_completed()
    assert mgr.get(it.id) is None
    assert os.path.exists(path)  # file kept
    it2 = mgr.add(server.url("/y.pdf"))
    assert wait_for(lambda: mgr.get(it2.id).status == COMPLETE)
    p2 = mgr.get(it2.id).path
    mgr.remove([it2.id], delete_files=True)
    assert not os.path.exists(p2)


@pytest.mark.skipif(sys.platform == "win32", reason="Unix sockets")
def test_ipc_roundtrip(tmp_path):
    sock = str(tmp_path / "s.sock")

    async def handler(msg):
        return {"ok": True, "echo": msg}

    async def go():
        srv = ipc.IPCServer(handler, sock)
        await srv.start()
        r = await asyncio.get_running_loop().run_in_executor(None, lambda: ipc.send({"cmd": "ping"}, sock))
        await srv.stop()
        return r

    r = asyncio.run(go())
    assert r == {"ok": True, "echo": {"cmd": "ping"}}
    assert oct(os.stat(tmp_path).st_mode & 0o777)  # dir exists


@pytest.mark.skipif(sys.platform == "win32", reason="Unix sockets")
def test_native_host_bridge(tmp_path):
    sock_dir = tmp_path / "run"
    sock_dir.mkdir()
    (sock_dir / "gesit").mkdir(mode=0o700)
    sock = str(sock_dir / "gesit" / "gesit.sock")
    ready = threading.Event()

    def serve():
        async def handler(msg):
            return {"ok": True, "got": msg["cmd"]}

        async def main():
            srv = ipc.IPCServer(handler, sock)
            await srv.start()
            ready.set()
            await asyncio.sleep(4)

        asyncio.run(main())

    t = threading.Thread(target=serve, daemon=True)
    t.start()
    ready.wait(3)
    env = dict(os.environ, XDG_RUNTIME_DIR=str(sock_dir))
    p = subprocess.Popen([sys.executable, "-m", "gesit.native_host"], stdin=subprocess.PIPE,
                         stdout=subprocess.PIPE, env=env)
    body = b'{"cmd":"ping"}'
    p.stdin.write(struct.pack("=I", len(body)) + body)
    p.stdin.flush()
    (n,) = struct.unpack("=I", p.stdout.read(4))
    out = p.stdout.read(n)
    p.stdin.close()
    p.wait(5)
    assert b'"got": "ping"' in out


def test_extension_logic_with_mocked_browser():
    import shutil

    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    here = os.path.dirname(__file__)
    r = subprocess.run([node, os.path.join(here, "ext_harness.mjs")], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stdout + r.stderr


def test_corrupt_database_is_set_aside_not_fatal(tmp_path):
    path = tmp_path / "downloads.db"
    path.write_bytes(b"SQLite format 3\x00 this is not really a database")
    db = Database(str(path))
    assert db.recovered_from and os.path.exists(db.recovered_from)
    assert db.load_all() == []
    it = Manager(Config(download_root=str(tmp_path / "dl"), proxy_mode="none"), db)
    assert it.snapshot() == []
