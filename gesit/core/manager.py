"""Download manager: owns the asyncio loop, queue, persistence and events.

The manager runs its own event loop in a background thread. Every public
method is thread-safe and may be called from the Qt main thread or from the
loop thread itself (IPC handlers).
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import datetime
import os
import subprocess
import threading
import time
from typing import Any, Callable

from ..engine import downloader as dl
from ..engine.limiter import SpeedLimiter
from ..engine.media import HLSJob, YtdlpJob
from . import util
from .config import Config, category_for
from .db import Database
from .models import ACTIVE, COMPLETE, CONNECTING, DOWNLOADING, ERROR, QUEUED, STOPPED, Item

Listener = Callable[[str, Item | None], None]


class Manager:
    def __init__(self, cfg: Config, db: Database):
        self.cfg = cfg
        self.db = db
        self.items: dict[int, Item] = {}
        self._jobs: dict[int, Any] = {}
        self._tasks: dict[int, asyncio.Task] = {}
        self._listeners: list[Listener] = []
        self._lock = threading.RLock()
        self.queue_running = False
        self._sched_started = False
        self._last_sched_minute = ""
        self.global_limiter = SpeedLimiter(cfg.speed_limit_kbps * 1024)
        self.loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run_loop, name="gesit-manager", daemon=True)
        for it in db.load_all():
            self.items[it.id] = it

    # ------------------------------------------------------------- lifecycle
    def _run_loop(self) -> None:
        asyncio.set_event_loop(self.loop)
        self.loop.create_task(self._housekeeping())
        self.loop.run_forever()

    def start(self) -> None:
        self._thread.start()

    def shutdown(self) -> None:
        async def _shutdown() -> None:
            for j in list(self._jobs.values()):
                j.stop()
            tasks = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)  # lets subprocess jobs be reaped

        try:
            asyncio.run_coroutine_threadsafe(_shutdown(), self.loop).result(timeout=8)
        except Exception:  # noqa: BLE001 - still persist state below
            pass
        self.loop.call_soon_threadsafe(self.loop.stop)  # only after the result was delivered
        self._thread.join(timeout=2)
        with self._lock:
            for it in self.items.values():
                if it.status in ACTIVE:
                    it.status = STOPPED
                self.db.update(it)

    def add_listener(self, fn: Listener) -> None:
        self._listeners.append(fn)

    def _emit(self, kind: str, item: Item | None = None) -> None:
        snap = item.snapshot() if item else None
        for fn in list(self._listeners):
            try:
                fn(kind, snap)
            except Exception:  # noqa: BLE001 - a bad listener must not break downloads
                pass

    def _call(self, fn: Callable, *args, **kwargs):
        """Run fn on the manager loop and return its result (sync)."""
        if threading.current_thread() is self._thread:
            return fn(*args, **kwargs)
        fut: concurrent.futures.Future = concurrent.futures.Future()

        def runner():
            try:
                fut.set_result(fn(*args, **kwargs))
            except BaseException as e:  # noqa: BLE001
                fut.set_exception(e)

        self.loop.call_soon_threadsafe(runner)
        return fut.result(timeout=15)

    # --------------------------------------------------------------- snapshot
    def snapshot(self) -> list[Item]:
        with self._lock:
            return [it.snapshot() for it in self.items.values()]

    def get(self, item_id: int) -> Item | None:
        with self._lock:
            it = self.items.get(item_id)
            return it.snapshot() if it else None

    # -------------------------------------------------------------------- add
    def add(
        self,
        url: str,
        *,
        filename: str = "",
        save_dir: str = "",
        category: str = "",
        referer: str = "",
        page_url: str = "",
        cookies: str = "",
        user_agent: str = "",
        headers: dict | None = None,
        auth_user: str = "",
        auth_pass: str = "",
        description: str = "",
        kind: str = "",
        start: bool = True,
        total: int = -1,
    ) -> Item:
        return self._call(
            self._add, url, filename, save_dir, category, referer, page_url, cookies, user_agent,
            headers or {}, auth_user, auth_pass, description, kind, start, total,
        )

    def _add(self, url, filename, save_dir, category, referer, page_url, cookies, user_agent,
             headers, auth_user, auth_pass, description, kind, start, total) -> Item:
        url = url.strip()
        if "://" not in url:
            url = "http://" + url
        if not kind:
            path = url.split("?", 1)[0].lower()
            kind = "hls" if path.endswith(".m3u8") else "http"
        guess_name = filename or util.filename_from_url(url)
        if kind == "hls" and not filename:
            guess_name = os.path.splitext(guess_name)[0] + ".mp4"
        cat = category or ("Video" if kind in ("hls", "ytdlp") else category_for(guess_name))
        it = Item(
            url=url, kind=kind, filename=filename, category=cat,
            save_dir=save_dir or self.cfg.category_dir(cat),
            referer=referer, page_url=page_url, cookies=cookies, user_agent=user_agent,
            headers=headers, auth_user=auth_user, auth_pass=auth_pass, description=description,
            total=total, in_queue=not start, status=STOPPED if start else QUEUED,
        )
        it._auto_dir = not save_dir  # type: ignore[attr-defined]
        it._auto_cat = not category  # type: ignore[attr-defined]
        if not it.auth_user:
            login = self.cfg.login_for(util.host_of(url))
            if login:
                it.auth_user, it.auth_pass = login
        it.id = self.db.insert(it)
        with self._lock:
            self.items[it.id] = it
        self._emit("added", it)
        if start:
            self._start(it)
        else:
            self._schedule()
        return it.snapshot()

    # ------------------------------------------------------------------ probe
    def probe(self, url: str, *, referer="", cookies="", user_agent="", headers=None,
              auth: tuple[str, str] | None = None) -> concurrent.futures.Future:
        """Async-probe a URL (for the Download File Info dialog)."""
        req = self._request(Item(url=url, referer=referer, cookies=cookies, user_agent=user_agent,
                                 headers=headers or {}, auth_user=(auth or ("", ""))[0],
                                 auth_pass=(auth or ("", ""))[1]), "")

        async def go():
            async with dl.make_client(req, 2) as client:
                p = await dl.probe(client, req)
            return {
                "filename": p.filename, "size": p.total if p.total is not None else -1,
                "resumable": p.resumable, "type": p.content_type or "",
                "category": category_for(p.filename), "url": p.final_url,
            }

        return asyncio.run_coroutine_threadsafe(go(), self.loop)

    # ---------------------------------------------------------------- control
    def start_item(self, item_id: int) -> None:
        self._call(self._start_id, item_id)

    def _start_id(self, item_id: int) -> None:
        it = self.items.get(item_id)
        if it:
            self._start(it)

    def stop_item(self, item_id: int) -> None:
        self._call(self._stop, item_id)

    def stop_all(self) -> None:
        self._call(self._stop_all)

    def _stop_all(self) -> None:
        self.queue_running = False
        for i in list(self.items):
            self._stop(i)

    def _stop(self, item_id: int) -> None:
        it = self.items.get(item_id)
        if not it:
            return
        job = self._jobs.get(item_id)
        if job and it.status in ACTIVE:
            job.stop()
        elif it.status == QUEUED:
            it.status, it.in_queue = STOPPED, False
            self.db.update(it)
            self._emit("changed", it)

    def remove(self, ids: list[int], delete_files: bool = False) -> None:
        self._call(self._remove, ids, delete_files)

    def _remove(self, ids: list[int], delete_files: bool) -> None:
        for i in ids:
            it = self.items.get(i)
            if not it:
                continue
            job = self._jobs.get(i)
            if job:
                job.stop()
                task = self._tasks.get(i)
                if task:
                    task.cancel()
            self._delete_partials(it)
            if delete_files and it.filename:
                self._rm(it.path)
            with self._lock:
                self.items.pop(i, None)
            self._jobs.pop(i, None)
            self._tasks.pop(i, None)
        self.db.delete(ids)
        self._emit("removed", None)
        self._schedule()

    def remove_completed(self) -> None:
        self._call(lambda: self._remove([i for i, it in self.items.items() if it.status == COMPLETE], False))

    def redownload(self, item_id: int) -> None:
        self._call(self._redownload, item_id)

    def _redownload(self, item_id: int) -> None:
        it = self.items.get(item_id)
        if not it or it.status in ACTIVE:
            return
        self._delete_partials(it)
        if it.status == COMPLETE:
            self._rm(it.path)
        it.downloaded, it.status, it.error = 0, STOPPED, ""
        self._start(it)

    def update_item(self, item_id: int, **fields) -> None:
        self._call(self._update_item, item_id, fields)

    def _update_item(self, item_id: int, fields: dict) -> None:
        it = self.items.get(item_id)
        if not it:
            return
        for k, v in fields.items():
            setattr(it, k, v)
        job = self._jobs.get(item_id)
        if job and "speed_limit_kbps" in fields and hasattr(job, "limiter"):
            job.limiter.set_rate(it.speed_limit_kbps * 1024)
        self.db.update(it)
        self._emit("changed", it)

    def apply_config(self) -> None:
        self.global_limiter.set_rate(self.cfg.speed_limit_kbps * 1024)
        self._schedule()

    # ------------------------------------------------------------------ queue
    def start_queue(self) -> None:
        self._call(self._start_queue, False)

    def _start_queue(self, from_scheduler: bool) -> None:
        self.queue_running = True
        self._sched_started = from_scheduler
        for it in self.items.values():
            if it.status in (STOPPED, ERROR) and it.in_queue:
                it.status = QUEUED
        self._schedule()
        self._emit("queue", None)

    def stop_queue(self) -> None:
        self._call(self._stop_queue)

    def _stop_queue(self) -> None:
        self.queue_running = False
        for i, it in list(self.items.items()):
            if it.in_queue and it.status in ACTIVE:
                self._stop(i)
        self._emit("queue", None)

    def add_to_queue(self, ids: list[int], on: bool = True) -> None:
        def f():
            for i in ids:
                it = self.items.get(i)
                if it and it.status != COMPLETE:
                    it.in_queue = on
                    if on and it.status in (STOPPED, ERROR):
                        it.status = QUEUED
                    elif not on and it.status == QUEUED:
                        it.status = STOPPED
                    self.db.update(it)
                    self._emit("changed", it)
            self._schedule()

        self._call(f)

    def _schedule(self) -> None:
        if not self.queue_running:
            return
        active = sum(1 for it in self.items.values() if it.in_queue and it.status in ACTIVE)
        for it in sorted(self.items.values(), key=lambda x: x.id):
            if active >= max(1, self.cfg.max_active):
                break
            if it.in_queue and it.status == QUEUED:
                self._start(it)
                active += 1
        pending = [it for it in self.items.values() if it.in_queue and it.status in (QUEUED, *ACTIVE)]
        if not pending:
            self.queue_running = False
            self._emit("queue", None)
            self._emit("queue_finished", None)
            if self._sched_started:
                self._sched_started = False
                self._finish_action()

    def _finish_action(self) -> None:
        act = self.cfg.sched_on_finish
        if act == "exit":
            self._emit("request_exit", None)
        elif act in ("suspend", "poweroff"):
            subprocess.Popen(["systemctl", act], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    # ------------------------------------------------------------------- jobs
    def _request(self, it: Item, save_dir: str | None = None) -> dl.Request:
        cfg = self.cfg
        return dl.Request(
            url=it.url,
            save_dir=save_dir if save_dir is not None else it.save_dir,
            filename=it.filename or None,
            headers=dict(it.headers or {}),
            cookies=it.cookies or None,
            referer=it.referer or None,
            user_agent=it.user_agent or cfg.user_agent or None,
            auth=(it.auth_user, it.auth_pass) if it.auth_user else None,
            proxy=cfg.proxy_url(),
            connections=cfg.connections,
            retries=cfg.retries,
            timeout=float(cfg.timeout),
            verify_tls=cfg.verify_tls,
        )

    def _start(self, it: Item) -> None:
        if it.status in ACTIVE or it.status == COMPLETE or it.id in self._tasks:
            return
        req = self._request(it)
        limiter = SpeedLimiter(it.speed_limit_kbps * 1024)

        def on_change(it=it):
            self._sync(it)

        def on_probe(info: dl.Probe, r: dl.Request, it=it):
            # choose the category folder now that the real file name is known
            if getattr(it, "_auto_cat", False):
                it.category = category_for(r.filename or info.filename)
            if getattr(it, "_auto_dir", False):
                r.save_dir = it.save_dir = self.cfg.category_dir(it.category)
            if info.total is not None:
                it.total = info.total

        if it.kind == "hls":
            job = HLSJob(req, limiter, self.global_limiter, on_change)
        elif it.kind == "ytdlp":
            job = YtdlpJob(req, limiter, self.global_limiter, on_change)
        else:
            job = dl.Downloader(req, limiter, self.global_limiter, on_change, on_probe)
        self._jobs[it.id] = job
        it.status, it.error, it.last_try = CONNECTING, "", time.time()
        self._emit("changed", it)
        self._tasks[it.id] = self.loop.create_task(self._run_job(it, job))

    async def _run_job(self, it: Item, job) -> None:
        try:
            await job.run()
        except asyncio.CancelledError:
            pass
        finally:
            self._sync(it)
            self._jobs.pop(it.id, None)
            self._tasks.pop(it.id, None)
        if it.id not in self.items:
            return
        it.speed, it.eta, it.connections = 0.0, None, []
        if job.status == dl.COMPLETE:
            it.status, it.error = COMPLETE, ""
            it.in_queue = False
            it.filename = os.path.basename(job.final_path or it.filename)
            it.save_dir = os.path.dirname(job.final_path) if job.final_path else it.save_dir
            if it.total and it.total > 0:
                it.downloaded = it.total
        elif job.status == dl.ERROR:
            it.status, it.error = ERROR, job.error or "Unknown error"
        else:
            it.status = STOPPED
        self.db.update(it)
        self._emit("complete" if it.status == COMPLETE else "error" if it.status == ERROR else "changed", it)
        self._schedule()

    def _sync(self, it: Item) -> None:
        job = self._jobs.get(it.id)
        if not job:
            return
        with self._lock:
            if job.filename:
                it.filename = job.filename
            if job.total is not None:
                it.total = job.total
            it.downloaded = job.downloaded
            it.resumable = bool(getattr(job, "resumable", False))
            it.speed = job.speed
            it.eta = job.eta
            it.connections = job.connection_info()
            it.segments = [(s.start, s.pos, s.end) for s in job.segments] if job.segments else []
            if job.status == dl.DOWNLOADING and it.status == CONNECTING:
                it.status = DOWNLOADING

    # ------------------------------------------------------------ housekeeping
    async def _housekeeping(self) -> None:
        tick = 0
        while True:
            await asyncio.sleep(1.0)
            tick += 1
            if tick % 5 == 0:
                for it in list(self.items.values()):
                    if it.status in ACTIVE:
                        self.db.update(it)
            self._scheduler_tick()

    def _scheduler_tick(self) -> None:
        cfg = self.cfg
        if not cfg.sched_enabled:
            return
        now = datetime.datetime.now()
        stamp = now.strftime("%Y-%m-%d %H:%M")
        if stamp == self._last_sched_minute or now.weekday() not in cfg.sched_days:
            return
        hhmm = now.strftime("%H:%M")
        if hhmm == cfg.sched_start:
            self._last_sched_minute = stamp
            self._start_queue(True)
        elif cfg.sched_stop_enabled and hhmm == cfg.sched_stop:
            self._last_sched_minute = stamp
            self._sched_started = False
            self._stop_queue()

    # ------------------------------------------------------------------ files
    @staticmethod
    def _rm(path: str) -> None:
        try:
            if path and os.path.isfile(path):
                os.remove(path)
        except OSError:
            pass

    def _delete_partials(self, it: Item) -> None:
        if it.filename and it.save_dir:
            base = os.path.join(it.save_dir, it.filename)
            for suffix in (".part", ".gesit", ".gesit.tmp"):
                self._rm(base + suffix)

    def speed_total(self) -> float:
        with self._lock:
            return sum(it.speed for it in self.items.values() if it.status in ACTIVE)
