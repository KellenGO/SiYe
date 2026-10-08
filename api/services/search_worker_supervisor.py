# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
# This file is part of MediaCrawler project.
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

"""Lazy resident platform workers and their process lifecycle."""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from typing import Dict, Optional

from .worker_process import cancel_task_quietly, drain_stderr_to_logger, spawn_worker, terminate_worker

GRACE_PERIOD_SECONDS = 5.0
WORKER_LOG_TAIL_LINES = 30
logger = logging.getLogger(__name__)


# ── Resident platform worker supervisor ─────────────────────────────────

class PlatformWorkerSupervisor:
    """懒启动、可回收的平台 worker supervisor。

    - 每个平台最多一个 worker 子进程；
    - 第一次搜索才启动；worker 以 NDJSON 循环读取多个请求（--resident）；
    - 同一 worker 串行处理任务，四个平台仍可并行（各自独立进程）；
    - 空闲 IDLE_TIMEOUT_SECONDS 后优雅退出（关闭 stdin）；
    - 处理 MAX_REQUESTS_PER_WORKER 次后优雅重启（worker 自身退出）；
    - worker crash 后自动重建；cancel/timeout 可直接终止对应 worker；
    - 浏览器 context 默认仍按请求创建并关闭（不常驻四台 Edge）。
    """

    IDLE_TIMEOUT_SECONDS = 300.0
    MAX_REQUESTS_PER_WORKER = 20
    _REAP_INTERVAL_SECONDS = 30.0

    def __init__(self) -> None:
        self._workers: Dict[str, "_ResidentWorker"] = {}
        self._lock = asyncio.Lock()
        self._reaper_task: Optional[asyncio.Task] = None

    async def start(self) -> None:
        if self._reaper_task is None or self._reaper_task.done():
            self._reaper_task = asyncio.create_task(self._idle_reaper())

    async def _spawn(self, platform: str) -> "_ResidentWorker":
        # The supervisor is the single source of truth for the max-request
        # 把上限写进子进程 env，worker 与 supervisor 绝不各自维护不一致的上限。
        proc = await spawn_worker(
            "--resident",
            env_extra={"MC_WORKER_MAX_REQUESTS": str(self.MAX_REQUESTS_PER_WORKER)},
        )
        worker = _ResidentWorker(platform=platform, proc=proc)
        worker.stderr_task = asyncio.create_task(
            self._drain_stderr(platform, proc, worker))
        return worker

    async def submit(self, platform: str, line: bytes) -> "_ResidentWorker":
        """取（或懒启动）平台 worker 并写入一个请求行。

        若 worker 恰在写入时退出（如 max-requests 优雅重启的窗口期，
        returncode 尚未置位），丢弃旧进程并重建一次再写。
        worker.reused 标记本次是否复用了既有进程（供 timing 表达）。
        """
        async with self._lock:
            worker = self._workers.get(platform)
            reused = worker is not None and worker.proc.returncode is None
            if worker is None or worker.proc.returncode is not None:
                if worker is not None:
                    self._workers.pop(platform, None)
                worker = await self._spawn(platform)
                self._workers[platform] = worker
                reused = False
            worker.last_used_at = time.monotonic()
            worker.request_count += 1
            worker.busy = True
            worker.reused = reused
            try:
                if worker.proc.stdin:
                    worker.proc.stdin.write(line)
                    await worker.proc.stdin.drain()
            except Exception:
                # 进程在"查表→写入"之间退出：丢弃并重建一次。
                self._workers.pop(platform, None)
                try:
                    if worker.proc.returncode is None:
                        worker.proc.kill()
                except Exception:
                    pass
                worker = await self._spawn(platform)
                self._workers[platform] = worker
                worker.last_used_at = time.monotonic()
                worker.request_count += 1
                worker.busy = True
                worker.reused = False
                if worker.proc.stdin:
                    worker.proc.stdin.write(line)
                    await worker.proc.stdin.drain()
            return worker

    async def touch(self, platform: str) -> None:
        """请求处理完成后刷新空闲计时起点（避免长任务被误回收）。

        last_used_at 只在 submit 时更新：若不在此处刷新，空闲回收器会把
        "仍在处理请求"的 worker 误判为空闲（长搜索期间 now-last_used_at
        持续增长）。任务完成（或终态）后调用一次即可；同时清除 busy 标记。
        """
        async with self._lock:
            worker = self._workers.get(platform)
            if worker is not None:
                worker.busy = False
                worker.last_used_at = time.monotonic()

    def is_at_max_requests(self, worker: "_ResidentWorker") -> bool:
        """该 worker 是否已达到 max-request 上限（当前请求即最后一个）。

        The supervisor is the single source of truth for the limit (_spawn
        子进程 env），因此这里用 supervisor 自己的计数判断，绝不靠等待/
        轮询进程退出。
        """
        return worker.request_count >= self.MAX_REQUESTS_PER_WORKER

    async def retire_after_last_request(
        self, platform: str, worker: "_ResidentWorker",
    ) -> None:
        """最后一个请求完成后的确定性退役。

        1. 先从注册表移除 —— 此后该平台的任何新请求必然新建 worker，
           绝不写入正在退出的旧 stdin；
        2. 关闭 stdin（确定性优雅信号，worker 本就将在处理后退出）；
        3. 有界等待进程退出（event-driven：wait 只在进程真正退出时返回，
           不是时间启发式；超时只是防御性兜底）；
        4. 取消并回收 stderr drain task。

        调用方保证该 worker 确实达到上限（is_at_max_requests）。
        """
        async with self._lock:
            if self._workers.get(platform) is worker:
                self._workers.pop(platform, None)
        await terminate_worker(worker.proc, grace=GRACE_PERIOD_SECONDS, graceful=True)
        await cancel_task_quietly(worker.stderr_task)

    async def stop_worker(self, platform: str, kill: bool = True) -> None:
        """终止平台 worker（cancel/timeout/账号操作前/shutdown）。

        kill=True：直接 kill（worker 可能在浏览器操作中）。
        kill=False：关闭 stdin 让其优雅退出（空闲回收）。
        """
        async with self._lock:
            worker = self._workers.pop(platform, None)
        if worker is None:
            return
        await terminate_worker(
            worker.proc, grace=GRACE_PERIOD_SECONDS, graceful=not kill)
        await cancel_task_quietly(worker.stderr_task)

    async def stop_all(self) -> None:
        for platform in list(self._workers.keys()):
            await self.stop_worker(platform, kill=True)
        if self._reaper_task and not self._reaper_task.done():
            self._reaper_task.cancel()
            try:
                await self._reaper_task
            except (asyncio.CancelledError, Exception):
                pass
            self._reaper_task = None

    async def _idle_reaper(self) -> None:
        try:
            while True:
                await asyncio.sleep(self._REAP_INTERVAL_SECONDS)
                now = time.monotonic()
                for platform, worker in list(self._workers.items()):
                    # 只回收"空闲且无请求在途"的 worker：busy 的 worker 由
                    # 请求级超时/取消终止，绝不因 idle 判定被误杀。
                    if worker.proc.returncode is None and not worker.busy and \
                            now - worker.last_used_at > self.IDLE_TIMEOUT_SECONDS:
                        # 空闲：优雅退出（关 stdin），不 kill。
                        await self.stop_worker(platform, kill=False)
        except asyncio.CancelledError:
            pass

    async def _drain_stderr(self, platform: str, proc, worker: "_ResidentWorker" = None) -> None:
        """常驻排空 worker stderr（防管道填满导致子进程卡死），并留住尾部日志。

        排空是必须的；留住尾部则用于排障：worker 子进程的日志以前**完全看不到**
        （抖音"搜不出来"时无从下手），现在平台 0 结果/失败时会把尾部打到后端日志。
        落日志前统一脱敏（见 worker_process.sanitize_worker_log_lines）。
        """
        def emit(line: str) -> None:
            logger.debug("[worker:%s] %s", platform, line)
            if worker is not None:
                worker.log_tail.append(line)

        await drain_stderr_to_logger(proc, emit)


class _ResidentWorker:
    __slots__ = ("platform", "proc", "stderr_task", "last_used_at",
                 "request_count", "busy", "reused", "log_tail")

    def __init__(self, platform: str, proc):
        self.platform = platform
        self.proc = proc
        self.stderr_task: Optional[asyncio.Task] = None
        self.last_used_at: float = time.monotonic()
        self.request_count: int = 0
        self.busy: bool = False
        # Record whether this request reused an existing worker.
        self.reused: bool = False
        # worker 日志尾部（已脱敏）：平台 0 结果/失败时打出来排障。
        self.log_tail: deque = deque(maxlen=WORKER_LOG_TAIL_LINES)


