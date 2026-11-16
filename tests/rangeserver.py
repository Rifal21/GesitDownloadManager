"""Tiny threaded HTTP server with Range support, for engine tests."""

from __future__ import annotations

import hashlib
import os
import re
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class State:
    data: bytes = b""
    ranges: bool = True
    drop_after: int | None = None  # drop connection after N bytes (first N requests)
    drop_budget: int = 0
    delay_per_chunk: float = 0.0
    disposition: str | None = None
    require_cookie: str | None = None
    etag: str = '"v1"'
    requests: list[str] = []
    redirect_to_other_host = False


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):  # noqa: D401
        pass

    def _common(self, send_body: bool):
        st = State
        if self.path.startswith("/redirect"):
            self.send_response(302)
            self.send_header("Location", "/file.bin")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if st.require_cookie and st.require_cookie not in (self.headers.get("Cookie") or ""):
            self.send_response(403)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if self.path.startswith("/missing"):
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        data = st.data
        total = len(data)
        rng = self.headers.get("Range")
        st.requests.append(rng or "-")
        start, end, status = 0, total - 1, 200
        if rng and st.ranges:
            m = re.match(r"bytes=(\d+)-(\d*)", rng)
            if m:
                start = int(m.group(1))
                end = int(m.group(2)) if m.group(2) else total - 1
                end = min(end, total - 1)
                status = 206
        length = end - start + 1
        self.send_response(status)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("ETag", st.etag)
        self.send_header("Content-Length", str(length))
        if st.ranges:
            self.send_header("Accept-Ranges", "bytes")
        if status == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{total}")
        if st.disposition:
            self.send_header("Content-Disposition", st.disposition)
        self.end_headers()
        if not send_body:
            return
        sent = 0
        drop = st.drop_after if (st.drop_budget > 0 and length > 1) else None
        if drop is not None:
            st.drop_budget -= 1
        try:
            while sent < length:
                n = min(32768, length - sent)
                if drop is not None and sent + n > drop:
                    self.wfile.write(data[start + sent : start + drop])
                    self.wfile.flush()
                    self.connection.shutdown(socket.SHUT_RDWR)
                    self.close_connection = True
                    return
                self.wfile.write(data[start + sent : start + sent + n])
                sent += n
                if st.delay_per_chunk:
                    time.sleep(st.delay_per_chunk)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_GET(self):
        self._common(True)

    def do_HEAD(self):
        self._common(False)


class RangeServer:
    def __init__(self):
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    def start(self):
        self.thread.start()
        return self

    def stop(self):
        self.httpd.shutdown()
        self.httpd.server_close()

    def url(self, path="/file.bin"):
        return f"http://127.0.0.1:{self.port}{path}"


def reset(size: int = 5 * 1024 * 1024) -> bytes:
    State.data = os.urandom(size)
    State.ranges = True
    State.drop_after = None
    State.drop_budget = 0
    State.delay_per_chunk = 0.0
    State.disposition = None
    State.require_cookie = None
    State.etag = '"v1"'
    State.requests = []
    return State.data


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()
