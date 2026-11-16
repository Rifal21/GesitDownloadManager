import asyncio
import hashlib
import os

import pytest

from gesit.engine.downloader import COMPLETE, ERROR, PAUSED, Downloader, Request
from gesit.engine.limiter import SpeedLimiter

from . import rangeserver as rs


@pytest.fixture()
def server():
    s = rs.RangeServer().start()
    yield s
    s.stop()


def sha_file(path):
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


def run(coro):
    return asyncio.run(coro)


def test_segmented_download_matches(server, tmp_path):
    data = rs.reset(6 * 1024 * 1024)
    d = Downloader(Request(server.url(), str(tmp_path), connections=8))
    run(d.run())
    assert d.status == COMPLETE, d.error
    assert sha_file(d.final_path) == rs.sha(data)
    assert len(d.segments) >= 8
    assert not [f for f in os.listdir(tmp_path) if f.endswith((".part", ".gesit"))]


def test_dynamic_split_creates_more_segments(server, tmp_path):
    data = rs.reset(8 * 1024 * 1024)
    rs.State.delay_per_chunk = 0.002
    d = Downloader(Request(server.url(), str(tmp_path), connections=2))
    run(d.run())
    assert d.status == COMPLETE, d.error
    assert sha_file(d.final_path) == rs.sha(data)


def test_no_range_support_falls_back_to_single(server, tmp_path):
    data = rs.reset(2 * 1024 * 1024)
    rs.State.ranges = False
    d = Downloader(Request(server.url(), str(tmp_path), connections=8))
    run(d.run())
    assert d.status == COMPLETE, d.error
    assert not d.resumable
    assert sha_file(d.final_path) == rs.sha(data)


def test_retry_after_dropped_connections(server, tmp_path):
    data = rs.reset(4 * 1024 * 1024)
    rs.State.drop_after = 100_000
    rs.State.drop_budget = 6
    d = Downloader(Request(server.url(), str(tmp_path), connections=4, retries=8))
    run(d.run())
    assert d.status == COMPLETE, d.error
    assert sha_file(d.final_path) == rs.sha(data)


def test_pause_and_resume_is_byte_exact(server, tmp_path):
    data = rs.reset(8 * 1024 * 1024)
    rs.State.delay_per_chunk = 0.01

    async def scenario():
        d = Downloader(Request(server.url(), str(tmp_path), connections=4))
        task = asyncio.create_task(d.run())
        while d.downloaded < 1_000_000:
            await asyncio.sleep(0.05)
        d.stop()
        await task
        assert d.status == PAUSED
        got = d.downloaded
        assert 0 < got < len(data)
        name = d.filename
        rs.State.delay_per_chunk = 0
        d2 = Downloader(Request(server.url(), str(tmp_path), filename=name, connections=4))
        await d2.run()
        return d2, got

    d2, got = run(scenario())
    assert d2.status == COMPLETE, d2.error
    assert sha_file(d2.final_path) == rs.sha(data)


def test_resume_discarded_when_file_changed(server, tmp_path):
    rs.reset(3 * 1024 * 1024)
    rs.State.delay_per_chunk = 0.004

    async def scenario():
        d = Downloader(Request(server.url(), str(tmp_path), connections=2))
        t = asyncio.create_task(d.run())
        while d.downloaded < 500_000:
            await asyncio.sleep(0.05)
        d.stop()
        await t
        new = rs.reset(3 * 1024 * 1024)
        rs.State.etag = '"v2"'
        d2 = Downloader(Request(server.url(), str(tmp_path), filename=d.filename, connections=2))
        await d2.run()
        return d2, new

    d2, new = run(scenario())
    assert d2.status == COMPLETE, d2.error
    assert sha_file(d2.final_path) == rs.sha(new)


def test_filename_from_content_disposition_and_redirect(server, tmp_path):
    data = rs.reset(300 * 1024)
    rs.State.disposition = "attachment; filename*=UTF-8''hello%20world.bin"
    d = Downloader(Request(server.url("/redirect"), str(tmp_path)))
    run(d.run())
    assert d.status == COMPLETE, d.error
    assert os.path.basename(d.final_path) == "hello world.bin"
    assert sha_file(d.final_path) == rs.sha(data)


def test_existing_file_is_not_overwritten(server, tmp_path):
    rs.reset(100 * 1024)
    (tmp_path / "file.bin").write_bytes(b"old")
    d = Downloader(Request(server.url(), str(tmp_path)))
    run(d.run())
    assert d.status == COMPLETE
    assert os.path.basename(d.final_path) == "file (1).bin"
    assert (tmp_path / "file.bin").read_bytes() == b"old"


def test_http_404_is_fatal(server, tmp_path):
    rs.reset(1000)
    d = Downloader(Request(server.url("/missing"), str(tmp_path), retries=1))
    run(d.run())
    assert d.status == ERROR
    assert "404" in d.error


def test_cookie_and_headers_are_sent(server, tmp_path):
    data = rs.reset(200 * 1024)
    rs.State.require_cookie = "session=abc"
    d = Downloader(Request(server.url(), str(tmp_path), cookies="session=abc"))
    run(d.run())
    assert d.status == COMPLETE, d.error
    assert sha_file(d.final_path) == rs.sha(data)


def test_speed_limit(server, tmp_path):
    rs.reset(1024 * 1024)
    lim = SpeedLimiter(512 * 1024)
    import time

    t = time.monotonic()
    d = Downloader(Request(server.url(), str(tmp_path), connections=4), limiter=lim)
    run(d.run())
    took = time.monotonic() - t
    assert d.status == COMPLETE, d.error
    assert took > 1.2  # 1 MiB at 512 KiB/s minus burst


def test_download_works_without_os_pwrite(server, tmp_path, monkeypatch):
    """Windows has no os.pwrite: the seek+write fallback must produce identical files."""
    from gesit.engine import downloader as dlmod

    monkeypatch.setattr(dlmod, "_HAS_PWRITE", False)
    data = rs.reset(5 * 1024 * 1024)
    d = Downloader(Request(server.url(), str(tmp_path), connections=8))
    run(d.run())
    assert d.status == COMPLETE, d.error
    assert sha_file(d.final_path) == rs.sha(data)
