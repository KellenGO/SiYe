# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

"""In-memory search job manager with proper subprocess lifecycle."""

from __future__ import annotations

import asyncio
import logging
import os
import time
import uuid
from datetime import datetime, timezone
from collections import deque
from typing import Any, Dict, List, Optional

from aggregate_search.models import (
    UnifiedSearchResult, interleave_results, make_dedup_key,
    is_valid_platform,
)
from aggregate_search.hydration import hydration_candidates, metric_candidates
from aggregate_search.protocol import parse_event_line, WorkerRequest
from ..schemas.search import (
    SearchJobResponse, SearchJobRequestSchema, PlatformStatusInfo,
    PlatformTimingInfo,
)
from .accounts import (
    get_account_generation,
    get_session_snapshot,
    mark_login_required_from_search,
    record_search_outcome,
    evidence_token, record_usage,
    search_login_block,
)
from . import result_cache
from .result_hydration import ResultHydrator
from .search_metrics import PlatformCooldowns, SearchMetrics
from .search_exploration import Exploration
from .worker_process import (
    cancel_task_quietly,
    drain_stderr_to_eof,
    drain_stderr_to_logger,
    spawn_worker,
    terminate_worker,
)
from aggregate_search.pagination import PageState

WORKER_TIMEOUT_SECONDS = 100
GRACE_PERIOD_SECONDS = 5.0
#: worker 日志尾部保留行数（平台 0 结果/失败时打出来排障）。
WORKER_LOG_TAIL_LINES = 30
# Bound on waiting for an already in-flight cancel to finish (idempotent
# repeat cancel). The cancel cleanup itself is bounded by GRACE_PERIOD.
CANCEL_WAIT_TIMEOUT = 30.0

# Production defaults to resident worker supervisors; one-shot mode remains for
# 手工调试（tests/conftest.py 会把默认置为 oneshot，新增 supervisor 测试
# 单独开启）。用环境变量也可覆盖：MC_SEARCH_WORKER_MODE=oneshot。
SEARCH_WORKER_MODE = os.environ.get("MC_SEARCH_WORKER_MODE", "supervisor")

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


def _safe_error_summary(msg: str) -> str:
    return str(msg)[:200]


# ── Job Manager ─────────────────────────────────────────────────────────

class SearchJobManager:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._active_job: Optional[_ActiveJob] = None
        self._recent_job: Optional[_ActiveJob] = None
        # Resident platform workers start lazily and are reclaimed when idle.
        self.supervisor = PlatformWorkerSupervisor()
        self.cooldowns = PlatformCooldowns()
        self.metrics = SearchMetrics()

    def is_search_active(self) -> bool:
        job = self._active_job
        return job is not None and (not job.is_terminal()
                                   or (job.task is not None and not job.task.done()))

    async def stop_platform_worker(self, platform: str) -> None:
        """账号 sync/verify/delete 前停止对应平台 worker（避免 profile 锁）。"""
        if self._active_job and platform in self._active_job.platforms:
            await self._stop_hydration_task(self._active_job)
        await self.supervisor.stop_worker(platform, kill=True)

    async def create_job(self, req: SearchJobRequestSchema) -> SearchJobResponse:
        async with self._lock:
            if self.is_search_active():
                raise JobConflictError("A search job is already running.")

            platforms = req.platforms
            if not platforms:
                raise InvalidPlatformsError("At least one platform is required.")
            seen = set()
            for p in platforms:
                if p in seen:
                    raise InvalidPlatformsError(f"Duplicate platform: {p}")
                if not is_valid_platform(p):
                    raise InvalidPlatformsError(f"Invalid platform: {p}")
                seen.add(p)

            if self._active_job is not None:
                await self._stop_hydration_task(self._active_job)

            job_id = uuid.uuid4().hex[:12]
            job = _ActiveJob(
                job_id=job_id, keyword=req.keyword.strip(),
                platforms=platforms, limit_per_platform=req.limit_per_platform,
                platform_limits=req.platform_limits,
                bypass_cache=req.bypass_cache,
            )
            if req.continue_from:
                previous = self._active_job
                if (previous is None or previous.job_id != req.continue_from
                        or previous.keyword != job.keyword or previous.exploration is None):
                    raise InvalidPlatformsError("上一轮已失效，请从当前搜索继续或重新搜索")
                session = previous.exploration
                job.exploration = session
                job.continuation = True
                job.bypass_cache = True
                if req.replace_platforms:
                    # 单平台重搜（⟳）= 单平台版的"换一批"：
                    #   有内容且还有下一页 → 接着它的分页继续抓（带已见 id 去重），
                    #     拿到的是与上一轮**不重复**的新内容；
                    #   没搜过 / 一条都没有 → 从头搜（补一个平台的场景）；
                    #   有内容但已取尽 → 明确报错，绝不静默返回重复内容。
                    # 三种情况都不新增批次（"换一批"才是往后叠加）。
                    job.replaces_platforms = True
                    for p in platforms:
                        if session.fresh_search_needed(p):
                            session.reset_platform(job, p)
                            job.page_states[p] = PageState()
                            job.prior_ids[p] = []
                            job.platform_limits[p] = min(job.limit_for(p), session.MAX_RESULTS)
                            continue
                        if not session.more(p):
                            raise InvalidPlatformsError("该平台已经取完，重搜没有新内容")
                        job.page_states[p] = session.states[p].model_copy(deep=True)
                        job.prior_ids[p] = [r.content_id for r in session.results[p]]
                        job.platform_limits[p] = min(job.limit_for(p), session.remaining(p))
                else:
                    # 换批（换一批）：往后叠加新的批次，平台集合必须已经在会话里 ——
                    # "补一个没搜过的平台"走 replace_platforms，不要混进换批语义。
                    if any(p not in session.platforms for p in platforms):
                        raise InvalidPlatformsError("换批不能新增平台，请重新搜索")
                    if not any(session.more(p) for p in platforms):
                        raise InvalidPlatformsError("当前主题没有更多可获取内容，或已达到累计上限")
                    for p in platforms:
                        if session.generations[p] != job.account_generations[p]:
                            raise InvalidPlatformsError("账号状态已变化，请重新搜索后继续")
                        job.page_states[p] = session.states[p].model_copy(deep=True)
                        job.prior_ids[p] = [r.content_id for r in session.results[p]]
                        job.platform_limits[p] = min(job.limit_for(p), session.remaining(p))
            else:
                job.exploration = Exploration(job)
            self._active_job = job
            self._recent_job = job

        job.task = asyncio.create_task(self._run_job(job))
        await self.supervisor.start()  # 懒启动闲置回收（幂等）
        return job.to_response()

    async def get_job(self, job_id: str) -> Optional[SearchJobResponse]:
        if self._active_job and self._active_job.job_id == job_id:
            return self._active_job.to_response()
        if self._recent_job and self._recent_job.job_id == job_id:
            return self._recent_job.to_response()
        return None

    async def get_current(self) -> Optional[SearchJobResponse]:
        if self._active_job:
            return self._active_job.to_response()
        if self._recent_job:
            return self._recent_job.to_response()
        return None

    async def _run_job(self, job: "_ActiveJob") -> None:
        tasks = [asyncio.create_task(self._run_platform(job, p), name=f"w-{p}")
                 for p in job.platforms]
        await asyncio.gather(*tasks, return_exceptions=True)
        # The cancel path owns finalization while process cleanup is in flight.
        # Workers may finish naturally during that await; do not start hydration
        # or count an intermediate "cancelling" state as a completed search.
        if job._cancelling or job._cancelled:
            return
        job.finalize()
        # All workers have finished: complete successful platforms may be
        # reused even if another platform failed. Explicit refreshes write too.
        self._cache_job_results(job)
        self.metrics.record(job)
        if job.exploration:
            job.exploration.commit(job)

        # Hydration is intentionally detached from the original search task:
        # overall becomes terminal immediately, while GET /jobs keeps polling
        # only for the lightweight snippet updates.
        if (not job._cancelled and not job.hydration_cancel_event.is_set()
                and (hydration_candidates(job.response_results()) or metric_candidates(
                    [r for rows in job.platform_results.values() for r in rows]))):
            job.hydration_status = "running"
            job.hydration_task = asyncio.create_task(
                self._run_hydration(job), name=f"hydrate-{job.job_id}")

    def _cache_job_results(self, job: "_ActiveJob", *, hydration_update: bool = False) -> None:
        if job.continuation:
            return
        if job._compute_overall() not in ("completed", "partial"):
            return
        for platform in job.platforms:
            info = job.platforms_state.get(platform)
            if (info is None or info.status not in ("succeeded", "empty")
                    or info.cache_hit
                    or job.account_generations[platform] != get_account_generation(platform)):
                continue
            results = job.platform_results.get(platform, [])
            if hydration_update:
                result_cache.update(
                    job.keyword, platform, job.limit_for(platform), results,
                    owner=job.job_id,
                )
            else:
                result_cache.set(
                    job.keyword, platform, job.limit_for(platform), results,
                    owner=job.job_id, fetched_at=info.fetched_at,
                    started_at=job._start_ts,
                    pagination=job.page_states[platform].model_dump() if platform in job.page_checkpoints else None,
                )

    def _hydration_scope(self, job: "_ActiveJob") -> List[UnifiedSearchResult]:
        """详情补全的范围：**本任务跑过的平台**里、状态可补全、且不在冷却中的结果。

        换批/单平台重搜任务的 `response_results()` 是整轮全量视图（含会话里
        其它平台的结果），但本任务的 `platforms_state` 只有自己跑过的平台 ——
        不加过滤会直接 KeyError，让整个补全（含状态流转）失败。
        """
        return [
            result for result in job.response_results()
            if result.platform in job.platforms_state
            and job.platforms_state[result.platform].status in ("succeeded", "empty")
            and not self.cooldowns.remaining(result.platform)
        ]

    async def _run_hydration(self, job: "_ActiveJob") -> None:
        hydrator = ResultHydrator()
        metrics_results = []
        try:
            scoped = self._hydration_scope(job)
            metrics_results = metric_candidates(scoped)
            if metrics_results:
                await hydrator.hydrate_metrics(metrics_results, job.hydration_cancel_event, job.update_metrics)
            updates = await hydrator.hydrate(scoped, job.hydration_cancel_event)
            if not job.hydration_cancel_event.is_set():
                for update in updates:
                    job.update_snippet(update.result, update.snippet)
                self._cache_job_results(job, hydration_update=True)
        except asyncio.CancelledError:
            job.hydration_cancel_event.set()
        except Exception as exc:
            logger.warning("result hydration failed for %s: %s",
                           job.job_id, type(exc).__name__)
        finally:
            if metrics_results:
                await hydrator.close()
            job.hydration_status = "completed"

    async def _run_platform(self, job: "_ActiveJob", platform: str) -> None:
        """单平台执行：命中内存结果缓存则直接回放（不启动 worker）。

        缓存边界：只有平台终态 succeeded/empty 才写入；key 含
        账号代数（账号操作后自动失效）；用户主动"重新搜索"（bypass_cache）
        只跳过读取。未命中 → 走常驻/一次性 worker。
        """
        limit = job.limit_for(platform)
        if job.continuation and not job.exploration.more(platform):
            job.set_platform_status(platform, "empty")
            return
        if not job.bypass_cache:
            cached = result_cache.lookup(job.keyword, platform, limit)
            if cached is not None:
                for item in cached.results:
                    try:
                        job.add_result(platform, UnifiedSearchResult(**item))
                    except Exception:
                        pass
                job.set_platform_status(
                    platform,
                    "succeeded" if job.platform_results.get(platform) else "empty")
                job.platforms_state[platform].cache_hit = True
                job.platforms_state[platform].fetched_at = cached.fetched_at
                job.platforms_state[platform].cooldown_until = self.cooldowns.until(platform)
                if cached.pagination is not None:
                    job.page_states[platform] = PageState.model_validate(cached.pagination)
                    job.page_checkpoints.add(platform)
                return
        info = job.platforms_state[platform]
        # 登录预检（毫秒级、不启浏览器）：确定搜不了就直接回报 login_required，
        # 不再 spawn worker。否则浏览器路径要先启动浏览器 + 导航十几秒，才在
        # pong() 处失败 —— 这段时间对用户完全是浪费。
        # 放在缓存回放之后：已有缓存仍然可以离线回放，不需要登录。
        login_block = search_login_block(platform)
        if login_block:
            job.set_platform_status(platform, "login_required",
                                    error_summary=login_block)
            return
        if self.cooldowns.remaining(platform):
            job.set_platform_status(platform, "rate_limited", error_summary="平台冷却中，请倒计时结束后手动重试")
            info.cooldown_until = self.cooldowns.until(platform)
            info.cooldown_skipped = True
            return
        job.worker_platforms.add(platform)
        try:
            await self._run_worker(job, platform)
        finally:
            self.cooldowns.record(platform, info.status)
            info.cooldown_until = self.cooldowns.until(platform)
            self._log_worker_tail(job, platform, info.status)

    def _log_worker_tail(self, job: "_ActiveJob", platform: str, status: str) -> None:
        """平台 0 条结果或失败时，把 worker 日志尾部打到后端日志。

        worker 子进程的日志以前完全看不到（抖音"搜不出来"时无从下手）。
        "一条都没拿到"是所有异常里最需要证据的那种，所以这里兜住。
        内容已在 drain 时脱敏。
        """
        if status == "succeeded" and job.platform_results.get(platform):
            return
        worker = self.supervisor._workers.get(platform)
        if worker is None or not worker.log_tail:
            return
        logger.warning("[search] %s 终态 %s，worker 日志尾部（已脱敏）:", platform, status)
        for line in worker.log_tail:
            logger.warning("[worker:%s] %s", platform, line)

    def _build_request_json(self, job: "_ActiveJob", platform: str) -> bytes:
        request = WorkerRequest(
            job_id=job.job_id, mode="search", platform=platform,
            keyword=job.keyword, limit=job.limit_for(platform),
            # In-memory session snapshot (sent through stdin; without one the worker
            # 自动回退浏览器路径）；fast path 由 worker 安全回退兜底。
            session_snapshot=get_session_snapshot(platform),
            fast_path=True,
            bypass_cache=job.bypass_cache,
            pagination=job.page_states[platform].model_dump(),
            seen_ids=job.prior_ids[platform],
        )
        return request.model_dump_json().encode("utf-8") + b"\n"

    async def _run_worker(self, job: "_ActiveJob", platform: str) -> None:
        if SEARCH_WORKER_MODE == "supervisor":
            await self._run_worker_supervisor(job, platform)
        else:
            await self._run_worker_oneshot(job, platform)

    async def _run_worker_supervisor(self, job: "_ActiveJob", platform: str) -> None:
        """常驻 supervisor 模式：复用平台 worker 进程。"""
        job.set_platform_status(platform, "running", 0)
        done_received = False
        stdout_task = None
        try:
            request_json = self._build_request_json(job, platform)
            job.mark_spawn_start(platform)
            worker = await self.supervisor.submit(platform, request_json)
            job.mark_spawn_end(platform)  # 已有驻留进程时几乎为 0
            # Record whether this request reused an existing worker process.
            job.timings[platform].reused_worker = bool(worker.reused)
            proc = worker.proc
            job.procs.append(proc)

            stdout_task = asyncio.create_task(
                self._read_worker_output(job, platform, job.job_id, proc))

            done, pending = await asyncio.wait(
                [stdout_task], timeout=WORKER_TIMEOUT_SECONDS)

            if pending:
                # 超时：终止该平台 worker（下次搜索自动重建）。
                for t in pending:
                    t.cancel()
                await self._remove_proc(job, proc)
                await self.supervisor.stop_worker(platform, kill=True)
                job.set_platform_status(platform, "timed_out",
                                        error_summary="搜索超时，请稍后重试")
                return
            try:
                done_received = bool(stdout_task.result())
            except Exception:
                done_received = False

            # 请求处理已结束（成功/失败/空）：刷新空闲计时，防长任务误回收。
            await self.supervisor.touch(platform)

            # Deterministic retirement replaces the old time-based heuristic:
            # supervisor 通过 request_count 明确知道当前请求是不是该 worker
            # 的最后一个 —— 达到上限则确定性关闭 stdin、等待退出并移除旧
            # worker（等待是 event-driven，进程真正退出才返回）；未达上限
            # 的驻留 worker 不等待（它必然继续阻塞在 stdin 上）。
            if self.supervisor.is_at_max_requests(worker):
                await self.supervisor.retire_after_last_request(platform, worker)
            elif proc.returncode is None and not done_received:
                # 无 done 且进程可能已退出（崩溃/中途退出）：有界等待拿到
                # 真实退出码（进程已退出时 wait 立即返回）。
                try:
                    await asyncio.wait_for(proc.wait(),
                                           timeout=GRACE_PERIOD_SECONDS)
                except asyncio.TimeoutError:
                    pass

            # Keep the same terminal semantics as the one-shot path:
            #   done + exit0（驻留进程保留/优雅退役）        → 成功/空
            #   done + nonzero（done 后崩溃）                → failed
            #   无 done + exit0 / 无 done + nonzero（中途退出）→ failed
            if proc.returncode is not None:
                await self._remove_proc(job, proc)
                current = job.platforms_state.get(platform)
                if current and current.status in ("cancelled", "timed_out"):
                    return
                # 同上：报过终态（succeeded/empty）只是没读到 done 行的，保住结果不翻失败。
                # 只有"读到 done 但退出码非 0"或"没报终态又没 done"才算 failed。
                reported_ok = current is not None and current.status in ("succeeded", "empty")
                if (not done_received and not reported_ok) or (done_received and proc.returncode != 0):
                    logger.warning(
                        "[search] %s process ended unexpectedly: exit=%s done=%s",
                        platform, proc.returncode, done_received,
                    )
                    job.set_platform_status(
                        platform, "failed",
                        error_summary="搜索未正常完成，请重试")
                    return

            current = job.platforms_state.get(platform)
            if current and current.status == "timed_out":
                return
            if current and current.status in ("failed", "login_required",
                                              "rate_limited", "cancelled"):
                return
            if done_received:
                if current and current.status == "running":
                    job.set_platform_status(
                        platform,
                        "succeeded" if job.platform_results.get(platform) else "empty")
            elif not (current and current.status in ("succeeded", "empty")):
                # 同前：报过终态的保住，只有"什么都没报又没 done"才算 failed。
                job.set_platform_status(platform, "failed",
                                        error_summary="搜索未正常完成，请重试")
        except Exception as e:
            logger.warning("[search] %s supervisor failed: %s", platform, type(e).__name__)
            job.set_platform_status(platform, "failed",
                                    error_summary="搜索未正常完成，请重试")
        finally:
            if stdout_task is not None:
                if not stdout_task.done():
                    stdout_task.cancel()
                await asyncio.gather(stdout_task, return_exceptions=True)

    async def _remove_proc(self, job: "_ActiveJob", proc) -> None:
        try:
            job.procs.remove(proc)
        except ValueError:
            pass

    async def _run_worker_oneshot(self, job: "_ActiveJob", platform: str) -> None:
        job.set_platform_status(platform, "running", 0)
        done_received = False
        proc = None
        stdout_task = None
        stderr_task = None

        try:
            job.mark_spawn_start(platform)
            proc = await spawn_worker()
            job.mark_spawn_end(platform)
            job.procs.append(proc)

            request_json = self._build_request_json(job, platform)
            try:
                if proc.stdin:
                    proc.stdin.write(request_json)
                    await proc.stdin.drain()
                    proc.stdin.close()
            except Exception:
                # stdin write failed — process may still be alive, must clean up
                job.set_platform_status(platform, "failed", error_summary="stdin write failed")
                return

            stdout_task = asyncio.create_task(
                self._read_worker_output(job, platform, job.job_id, proc))
            stderr_task = asyncio.create_task(
                self._read_worker_stderr(proc))

            done, pending = await asyncio.wait(
                [stdout_task, stderr_task], timeout=WORKER_TIMEOUT_SECONDS)

            if pending:
                for t in pending:
                    t.cancel()
                await self._terminate_process(proc, platform, job)
            elif stdout_task in done:
                try:
                    done_received = stdout_task.result()
                except Exception:
                    pass

            # Drain remaining stdout
            try:
                while True:
                    raw = await asyncio.wait_for(proc.stdout.readline(), timeout=0.5)
                    if not raw:
                        break
            except (asyncio.TimeoutError, Exception):
                pass

            # 等待退出。取消清理可能正并发 wait 同一进程 —— terminate_worker
            # 内部吞掉这类异常，绝不能让它逃逸进 job.task（否则 cancel_job
            # 的 await 会收到非 CancelledError 异常，见 Round 13）。
            await terminate_worker(proc, grace=GRACE_PERIOD_SECONDS + 2)

            exit_code = proc.returncode if proc.returncode is not None else -1

            current = job.platforms_state.get(platform)
            # timed_out is final — don't override
            if current and current.status == "timed_out":
                return
            # A real error was already received from the worker — don't
            # override it with a "no done event" heuristic.
            if current and current.status in ("failed", "login_required",
                                              "rate_limited", "cancelled"):
                return

            if done_received and exit_code != 0:
                logger.warning("[search] %s process exited with code %s", platform, exit_code)
                job.set_platform_status(platform, "failed",
                                       error_summary="搜索未正常完成，请重试")
            elif not done_received:
                # worker 已经明确报过终态（succeeded/empty）、只是最后那行 done 没读到
                # （进程退得太快、管道先关）—— 保留用户已经拿到的结果，绝不翻成 failed。
                if not (current and current.status in ("succeeded", "empty")):
                    logger.warning("[search] %s process ended without completion event: exit=%s", platform, exit_code)
                    job.set_platform_status(platform, "failed",
                                           error_summary="搜索未正常完成，请重试")
            elif current and current.status == "running":
                job.set_platform_status(
                    platform,
                    "succeeded" if job.platform_results.get(platform) else "empty")

        except Exception as e:
            logger.warning("[search] %s process failed: %s", platform, type(e).__name__)
            job.set_platform_status(platform, "failed", error_summary="搜索未正常完成，请重试")
        finally:
            # Cancel reader tasks if still running
            for t in (stdout_task, stderr_task):
                if t and not t.done():
                    t.cancel()
            if stdout_task or stderr_task:
                await asyncio.gather(
                    *(t for t in (stdout_task, stderr_task) if t),
                    return_exceptions=True)
            # Ensure process is dead
            if proc is not None and proc.returncode is None:
                try:
                    proc.terminate()
                except Exception:
                    pass
                try:
                    await asyncio.wait_for(proc.wait(), timeout=GRACE_PERIOD_SECONDS)
                except asyncio.TimeoutError:
                    try:
                        proc.kill()
                        await proc.wait()
                    except Exception:
                        pass
                except Exception:
                    # Round 13: 与取消清理并发 wait 同一进程 —— 不得逃逸。
                    # 注意：任务被取消时此处 await 会抛 CancelledError
                    # （BaseException），仍正常向上传播保持取消语义。
                    pass

    async def _read_worker_output(
        self, job: "_ActiveJob", platform: str, expected_job_id: str,
        proc: asyncio.subprocess.Process,
    ) -> bool:
        if not proc.stdout:
            return False
        done_received = False
        while True:
            raw = await proc.stdout.readline()
            if not raw:
                break
            try:
                line_str = raw.decode("utf-8", errors="replace").strip()
            except Exception:
                continue
            if not line_str:
                continue
            event = parse_event_line(line_str)
            if event is None:
                continue
            if event.job_id != expected_job_id or event.platform != platform:
                continue

            if event.event == "status":
                sd = event.data or {}
                # error_summary 要透传给前端（例如抖音"已登录但 0 条，疑似风控"）。
                # 只接受字符串，且只作为安全摘要使用。
                summary = sd.get("error_summary")
                job.set_platform_status(
                    platform, sd.get("status", "running"),
                    error_summary=summary if isinstance(summary, str) and summary else None)
            elif event.event == "result":
                rd = event.data
                if isinstance(rd, dict) and rd.get("platform") == platform:
                    try:
                        job.add_result(platform, UnifiedSearchResult(**rd))
                    except Exception:
                        pass
            elif event.event == "error":
                ed = event.data or {}
                error_type = ed.get("type", "failed")
                job.set_platform_status(
                    platform, error_type,
                    error_summary=_safe_error_summary(ed.get("message", "")))
                # worker 明确 login_required → 反向降级 accounts
                # 服务中的账号状态（accounts 是账号状态的唯一事实来源）。
                # 账号同步失败绝不能让搜索任务崩溃：try/except 隔离，日志
                # 只记录平台与异常类型，不记录 worker 原始 body。
                # rate_limited / failed / timed_out 绝不修改账号状态。
                if error_type == "login_required" and record_usage(platform, "search", error_type, job.evidence_tokens[platform]):
                    try:
                        mark_login_required_from_search(platform)
                    except Exception as exc:
                        logger.warning(
                            "login_required account-state sync failed for "
                            "platform %s: %s", platform, type(exc).__name__)
            elif event.event == "metrics":
                job.apply_metrics(platform, event.data or {})
            elif event.event == "done":
                done_received = True
                break
        return done_received

    async def _read_worker_stderr(self, proc: asyncio.subprocess.Process) -> None:
        """排空 stderr 防管道写满。

        历史实现会把过滤后的行攒进一个 tail 列表，但**既不返回也不落日志**，
        纯属白算 —— 现在只做必要的排空。
        """
        await drain_stderr_to_eof(proc)

    async def _terminate_process(
        self, proc: asyncio.subprocess.Process, platform: str, job: "_ActiveJob",
    ) -> None:
        # 并发清理异常不得逃逸（Round 13）。
        await terminate_worker(proc, grace=GRACE_PERIOD_SECONDS)
        # Only set timed_out if not already terminal
        cur = job.platforms_state.get(platform)
        if cur and cur.status not in ("succeeded", "empty", "login_required",
                                       "rate_limited", "failed", "cancelled"):
            job.set_platform_status(platform, "timed_out")

    async def cancel_job(self, job_id: str) -> bool:
        """Cancel the active job.

        Contract (Round 13):
        - Returns True when the job is being / has been cancelled by this
          or a concurrent cancel (idempotent — a repeat cancel waits for the
          in-flight cleanup instead of returning 404/500 or starting a
          second cleanup).
        - Returns False only for an unknown job or a job already in a NORMAL
          terminal state (completed/partial/failed — nothing to cancel).
        - Never raises for cleanup problems: every step is bounded and
          isolated, and an outer finally guarantees the job reaches a
          queryable ``cancelled`` terminal state (``_cancelling=False``,
          ``_cancelled=True``, pending/running platforms -> cancelled,
          succeeded/empty platforms and results preserved, ``finalize()``,
          handled procs removed from ``job.procs``).
        """
        job = self._active_job
        if job is None or job.job_id != job_id:
            return False
        if job.hydration_task is not None and not job.hydration_task.done():
            await self._stop_hydration_task(job)
            return True
        if job.is_terminal():
            # 已取消的 job 重复取消 → 幂等成功；正常终态 → 无可取消。
            return job._cancelled
        # 已有取消在途：等待同一取消操作完成（不启动第二套清理）。
        if job._cancelling:
            await self._await_cancel_done(job)
            return True
        async with job.cancel_lock:
            if job.is_terminal():
                return job._cancelled
            if job._cancelling:
                await self._await_cancel_done(job)
                return True

            job._cancelling = True
            try:
                for p in job.platforms:
                    if job.platforms_state[p].status in ("pending", "running"):
                        job.set_platform_status(p, "cancelled", error_summary="已取消")
                await self._cleanup_job_processes(job)
                await self._stop_job_task(job)
            finally:
                # 最外层 finally：无论哪一步失败，任务都必须进入
                # 可查询的 cancelled 终态，绝不永久卡在 cancelling。
                job._cancelling = False
                job._cancelled = True
                for p in job.platforms:
                    if job.platforms_state[p].status in ("pending", "running"):
                        job.set_platform_status(p, "cancelled", error_summary="已取消")
                job.finalize()
                self.metrics.record(job)
                if job.exploration:
                    job.exploration.commit(job)
                job.cancel_done.set()
        return True

    async def _await_cancel_done(self, job: "_ActiveJob") -> None:
        """等待在途取消结束（有界；清理本身有界，超时仅防御）。"""
        try:
            await asyncio.wait_for(job.cancel_done.wait(), timeout=CANCEL_WAIT_TIMEOUT)
        except asyncio.TimeoutError:
            logger.warning("cancel: waited for in-flight cancel of %s "
                           "but it did not finish within %ss", job.job_id,
                           CANCEL_WAIT_TIMEOUT)

    async def _cleanup_job_processes(self, job: "_ActiveJob") -> None:
        """Kill + bounded-wait every live subprocess.

        - 每个进程 kill/terminate + wait 带超时；
        - 单个进程的失败被隔离（记录异常类型），不影响其他进程清理；
        - 已处理的进程从 job.procs 移除。
        本方法绝不抛异常。
        """
        procs = list(job.procs)
        for proc in procs:
            try:
                if proc.returncode is None:
                    proc.kill()
            except Exception as e:
                logger.warning("cancel: kill proc %s failed: %s",
                               getattr(proc, "pid", "?"), type(e).__name__)
        for proc in procs:
            try:
                await asyncio.wait_for(proc.wait(), timeout=GRACE_PERIOD_SECONDS)
            except asyncio.TimeoutError:
                try:
                    proc.kill()
                    await asyncio.wait_for(proc.wait(),
                                           timeout=GRACE_PERIOD_SECONDS)
                except Exception as e:
                    logger.warning("cancel: proc %s did not exit after kill: %s",
                                   getattr(proc, "pid", "?"), type(e).__name__)
            except Exception as e:
                logger.warning("cancel: wait proc %s failed: %s",
                               getattr(proc, "pid", "?"), type(e).__name__)
        for proc in procs:
            try:
                job.procs.remove(proc)
            except ValueError:
                pass

    async def _stop_job_task(self, job: "_ActiveJob") -> None:
        """取消并等待 job.task；任何异常（CancelledError / RuntimeError /
        ExceptionGroup / 超时）都不得冒泡到调用方。"""
        task = job.task
        if task is None or task.done():
            return
        task.cancel()
        try:
            await asyncio.wait_for(task, timeout=GRACE_PERIOD_SECONDS + 2)
        except (asyncio.TimeoutError, asyncio.CancelledError, Exception) as e:
            logger.warning("cancel: job task %s cleanup: %s",
                           job.job_id, type(e).__name__)

    async def cleanup(self) -> None:
        """Shutdown cleanup：与 cancel_job 使用同一组有界、防逃逸的
        清理原语（并发调用不会死锁、不留下进程）。Round 16: 同时停止全部
        常驻平台 worker（含 reader task / HTTP client / 浏览器子进程）。"""
        job = self._active_job
        if job is not None:
            # Mark all running platforms as cancelled
            for p in job.platforms:
                if job.platforms_state[p].status in ("pending", "running"):
                    job.set_platform_status(p, "cancelled", error_summary="服务已停止")
            await self._cleanup_job_processes(job)
            await self._stop_job_task(job)
            await self._stop_hydration_task(job)
            job._cancelled = True
            job.finalize()
            job.cancel_done.set()
        await self.supervisor.stop_all()
        # Round 16：shutdown 清空内存结果缓存。
        result_cache.clear()

    async def _stop_hydration_task(self, job: "_ActiveJob") -> None:
        # Set this even before the detached task starts, closing the terminal
        # status -> worker cleanup -> hydration scheduling race.
        job.hydration_cancel_event.set()
        async with job.hydration_stop_lock:
            task = job.hydration_task
            if task is None or task.done():
                return
            task.cancel()
            try:
                await asyncio.wait_for(task, timeout=GRACE_PERIOD_SECONDS)
            except (asyncio.TimeoutError, asyncio.CancelledError, Exception):
                pass
            finally:
                job.hydration_status = "completed"


# ── Active Job ──────────────────────────────────────────────────────────

class _ActiveJob:
    def __init__(self, job_id: str, keyword: str, platforms: List[str],
                 limit_per_platform: int,
                 platform_limits: Optional[Dict[str, Any]] = None,
                 bypass_cache: bool = False) -> None:
        self.job_id = job_id
        self.keyword = keyword
        self.platforms = platforms
        self.account_generations = {p: get_account_generation(p) for p in platforms}
        self.evidence_tokens = {p: evidence_token(p) for p in platforms}
        self.limit_per_platform = limit_per_platform
        # 用户主动"重新搜索"时绕过结果缓存。
        self.bypass_cache = bypass_cache
        # 按平台有效数量。platform_limits 已由 schema 校验（1–20
        # 严格整数）；只保留本次 platforms 中的平台，缺失平台回退统一值。
        # 每个平台拿到自己的标量，绝不共享最后一个数字。
        if platform_limits is None:
            platform_limits = {}
        self.platform_limits: Dict[str, int] = {
            p: int(platform_limits.get(p, limit_per_platform)) for p in platforms
        }
        self.created_at = datetime.now(timezone.utc).isoformat()
        self.completed_at: Optional[str] = None
        self.platforms_state: Dict[str, PlatformStatusInfo] = {
            p: PlatformStatusInfo(status="pending") for p in platforms}
        self.platform_results: Dict[str, List[UnifiedSearchResult]] = {
            p: [] for p in platforms}
        self._seen_keys: set = set()
        self.procs: List[asyncio.subprocess.Process] = []
        self.task: Optional[asyncio.Task] = None
        self._cancelling: bool = False
        self._cancelled: bool = False
        # ── 耗时指标（perf_counter 单调时钟；只记录数字，不含敏感信息）──
        self._start_ts = time.perf_counter()
        self._spawn_start: Dict[str, float] = {}
        self._first_result_ts: Dict[str, float] = {}
        self._platform_total_ts: Dict[str, float] = {}
        self.timings: Dict[str, PlatformTimingInfo] = {
            p: PlatformTimingInfo() for p in platforms}
        self.total_ms: Optional[int] = None
        # ── Per-job cancel coordination () ─────────────────────
        # cancel_lock: 同一 job 只允许一套并发清理。
        # cancel_done: 清理完成信号（重复取消等待它即可，幂等）。
        self.cancel_lock = asyncio.Lock()
        self.cancel_done = asyncio.Event()
        self.hydration_status = "not_started"
        self.hydration_task: Optional[asyncio.Task] = None
        self.hydration_cancel_event = asyncio.Event()
        self.hydration_stop_lock = asyncio.Lock()
        self.worker_platforms: set[str] = set()
        self.statistics_recorded = False
        self.exploration = None
        self.exploration_info = None
        self.continuation = False
        # 单平台重搜：结果替换当前批次里该平台的内容，而不是新增一批。
        self.replaces_platforms = False
        self.page_states = {p: PageState() for p in platforms}
        self.page_checkpoints = set()
        self.prior_ids = {p: [] for p in platforms}
        self._final_results: Optional[List[UnifiedSearchResult]] = None

    def _ms_since(self, start_ts: float) -> int:
        return int((time.perf_counter() - start_ts) * 1000)

    _METRIC_NUMERIC_FIELDS = (
        "worker_ready_ms", "browser_launch_ms", "navigation_ms",
        "preflight_ms", "search_api_ms",
    )

    def apply_metrics(self, platform: str, metrics: Dict) -> None:
        """把 worker 上报的内部指标合并进 timings（只接受白名单数值/枚举）。"""
        info = self.timings.get(platform)
        if info is None or not isinstance(metrics, dict):
            return
        if isinstance(metrics.get("pagination"), dict):
            try:
                state = PageState.model_validate(metrics["pagination"])
                if all(r.platform == platform for r in state.pending):
                    self.page_states[platform] = state
                    self.page_checkpoints.add(platform)
            except ValueError:
                pass
        for key in ("page_requests", "duplicate_count"):
            value = metrics.get(key)
            if isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 1000:
                setattr(info, key, value)
        for key in self._METRIC_NUMERIC_FIELDS:
            value = metrics.get(key)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                setattr(info, key, int(value))
        if metrics.get("fast_path_used") is True or metrics.get("fast_path_used") is False:
            info.fast_path_used = bool(metrics["fast_path_used"])
        provider_used = metrics.get("provider_used")
        if isinstance(provider_used, str) and provider_used in {
                "session_api", "light_api", "browser", "page_api", "public_search"}:
            info.provider_used = provider_used
        attempt_count = metrics.get("provider_attempt_count")
        if isinstance(attempt_count, int) and not isinstance(attempt_count, bool):
            info.provider_attempt_count = max(0, min(attempt_count, 10))
        attempts = metrics.get("provider_attempts")
        allowed_providers = {
            "session_api", "light_api", "browser", "page_api", "public_search"
        }
        if isinstance(attempts, list) and all(
                isinstance(item, str) and item in allowed_providers
                for item in attempts[:10]):
            info.provider_attempts = list(attempts[:10])
        if metrics.get("fallback_active") is True or metrics.get("fallback_active") is False:
            info.fallback_active = bool(metrics["fallback_active"])
        reason = metrics.get("fallback_reason")
        if isinstance(reason, str) and reason:
            info.fallback_reason = reason[:50]

    def mark_spawn_start(self, platform: str) -> None:
        self._spawn_start[platform] = time.perf_counter()

    def mark_spawn_end(self, platform: str) -> None:
        start = self._spawn_start.pop(platform, None)
        if start is not None:
            self.timings[platform].spawn_ms = self._ms_since(start)

    def mark_first_result(self, platform: str) -> None:
        if platform not in self._first_result_ts:
            self._first_result_ts[platform] = time.perf_counter()
            self.timings[platform].first_result_ms = self._ms_since(self._start_ts)

    def mark_platform_total(self, platform: str) -> None:
        if platform not in self._platform_total_ts:
            self._platform_total_ts[platform] = time.perf_counter()
            self.timings[platform].total_ms = self._ms_since(self._start_ts)

    def limit_for(self, platform: str) -> int:
        """该平台本次搜索的有效结果数量（platform_limits 优先，缺失回退统一值）。"""
        return self.platform_limits.get(platform, self.limit_per_platform)

    def set_platform_status(self, platform: str, status: str, result_count: int = 0,
                            error_summary: Optional[str] = None) -> None:
        info = self.platforms_state.get(platform)
        if info is None:
            return
        # timed_out is terminal and must not be downgraded
        if info.status == "timed_out" and status not in ("timed_out",):
            return
        terminal = {"succeeded", "empty", "login_required",
                    "rate_limited", "timed_out", "failed", "cancelled"}
        # Killing workers during cancellation may produce a nonzero exit after
        # a successful status. Preserve outcomes already received by the user.
        if (self._cancelling or self._cancelled) and info.status in terminal:
            return
        if info.status in terminal and status not in terminal:
            return
        info.status = status
        if status in terminal:
            self.mark_platform_total(platform)
        if status in ("succeeded", "empty") and info.fetched_at is None:
            info.fetched_at = datetime.now(timezone.utc).isoformat()
        if result_count:
            info.result_count = max(info.result_count, result_count)
        if error_summary:
            info.error_summary = error_summary

    def add_result(self, platform: str, result: UnifiedSearchResult) -> None:
        if result.content_id in self.prior_ids.get(platform, []):
            return
        key = make_dedup_key(platform, result.content_id)
        if key in self._seen_keys:
            return
        self._seen_keys.add(key)
        lst = self.platform_results.setdefault(platform, [])
        if len(lst) < self.limit_for(platform):
            lst.append(result)
            if len(lst) == 1:
                self.mark_first_result(platform)
            info = self.platforms_state.get(platform)
            if info:
                info.result_count = len(lst)

    def is_terminal(self) -> bool:
        return self._compute_overall() in (
            "completed", "partial", "failed", "cancelled")

    def finalize(self) -> None:
        self.completed_at = datetime.now(timezone.utc).isoformat()
        self.total_ms = self._ms_since(self._start_ts)
        # 平台结果按原始相关性（rank=source index）稳定重排。
        # 渐进展示期间保持到达顺序（首条尽早可见）；终态统一恢复源顺序。
        # 非 xhs 平台的 rank 即到达顺序，排序为空操作。stable sort 保证
        # 同 rank（理论不发生）不改变相对顺序。
        for p in self.platform_results:
            self.platform_results[p] = sorted(
                self.platform_results[p], key=lambda r: r.rank)
        for p, results in self.platform_results.items():
            info = self.platforms_state.get(p)
            if info and info.status in ("running", "pending"):
                info.status = "succeeded" if results else "empty"
                info.result_count = len(results)
                self.mark_platform_total(p)
                if info.status == "empty":
                    # 空结果不留原因就没法区分"真没结果"和"被风控"（worker 日志
                    # 不打在后端控制台）。记一条安全摘要：平台 / 关键词 / 耗时数值。
                    timing = self.timings.get(p)
                    logger.warning(
                        "[search] %s returned 0 results (keyword=%r, worker_ready_ms=%s, "
                        "navigation_ms=%s, search_api_ms=%s, total_ms=%s) — "
                        "可能是平台返回空列表或被风控，需结合 worker 日志确认",
                        p, self.keyword,
                        getattr(timing, "worker_ready_ms", None),
                        getattr(timing, "navigation_ms", None),
                        getattr(timing, "search_api_ms", None),
                        self.total_ms,
                    )
            if info:
                if info.status in ("succeeded", "empty") and info.fetched_at is None:
                    info.fetched_at = self.completed_at
                # Platform Doctor consumes safe local metadata only; this does
                # not alter the search result or worker decision path.
                if not info.cache_hit and record_usage(p, "search", info.status, self.evidence_tokens[p]):
                    record_search_outcome(p, info.status, self.timings.get(p))
        self._final_results = interleave_results(
            self.platform_results, platform_order=self.platforms)

    def response_results(self) -> List[UnifiedSearchResult]:
        if self._final_results is not None:
            return self._final_results
        if self.continuation and self.exploration:
            return self.exploration.preview(self)
        return interleave_results(self.platform_results, platform_order=self.platforms)

    def update_snippet(self, result: UnifiedSearchResult, snippet: str) -> None:
        """Update text in place without rebuilding/deduplicating the order."""
        result.snippet = snippet
        copies = list(self._final_results or []) + self.platform_results.get(result.platform, [])
        if self.exploration:
            copies += self.exploration.results.get(result.platform, [])
            for batch in self.exploration.batches:
                copies += batch["results"]
        for representative in copies:
            if representative.platform == result.platform and representative.content_id == result.content_id:
                representative.snippet = snippet
            for source in representative.grouped_sources or []:
                if source.platform == result.platform and source.content_id == result.content_id:
                    source.snippet = snippet

    def update_metrics(self, result: UnifiedSearchResult) -> None:
        """Keep platform tabs, grouped cards and previous exploration batches aligned."""
        copies = list(self._final_results or []) + self.platform_results.get(result.platform, [])
        if self.exploration:
            copies += self.exploration.results.get(result.platform, [])
            for batch in self.exploration.batches:
                copies += batch["results"]
        for representative in copies:
            if representative.platform == result.platform and representative.content_id == result.content_id:
                representative.metrics = dict(result.metrics)
                representative.metrics_status = result.metrics_status
                representative.metrics_updated_at = result.metrics_updated_at
                representative.metrics_approximate = list(result.metrics_approximate)
            for source in representative.grouped_sources or []:
                if source.platform == result.platform and source.content_id == result.content_id:
                    source.metrics = dict(result.metrics)

    def _compute_overall(self, statuses: Optional[List[str]] = None) -> str:
        if self._cancelled:
            return "cancelled"
        if self._cancelling:
            return "cancelling"
        if statuses is None:
            statuses = [info.status for info in self.platforms_state.values()]
        terminal = {"succeeded", "empty", "login_required",
                    "rate_limited", "timed_out", "failed", "cancelled"}
        if not all(s in terminal for s in statuses):
            return "running"
        successes = sum(1 for s in statuses if s in {"succeeded", "empty"})
        failures = len(statuses) - successes
        if failures == 0:
            return "completed"
        if successes > 0:
            return "partial"
        return "failed"

    def response_statuses(self) -> Dict[str, PlatformStatusInfo]:
        """这次响应该带上哪些平台的状态。

        换批（含"单独获取某个平台"）只跑其中一部分平台，但用户看到的是整轮结果：
        把会话里其它平台的既有状态一起带上，平台状态条与各平台条数才不会突然只剩一个。
        本次真正在跑的平台用实时状态覆盖。
        """
        statuses: Dict[str, PlatformStatusInfo] = {}
        if self.continuation and self.exploration is not None:
            statuses.update(self.exploration.platforms_state)
        statuses.update(self.platforms_state)
        return statuses

    def to_response(self) -> SearchJobResponse:
        all_results = self.response_results()
        statuses = self.response_statuses()
        pdict: Dict[str, PlatformStatusInfo] = {}
        for p, info in statuses.items():
            info = self.platforms_state.get(p, info)
            pdict[p] = PlatformStatusInfo(
                status=info.status, result_count=info.result_count,
                error_summary=info.error_summary,
                cache_hit=info.cache_hit, fetched_at=info.fetched_at,
                cooldown_until=info.cooldown_until, cooldown_skipped=info.cooldown_skipped,
                timings=self.timings.get(p))
        # Round 16: 平台的终态 status 事件会让 _compute_overall() 先于
        # finalize() 变成 terminal —— 此时 job 级 total_ms 尚未写入。这里
        # 在已终态时实时计算，避免响应出现"overall=completed 但 total_ms
        # 为 None"的竞态窗口（finalize 仍会写入最终值，二者一致）。
        # 换批时按"整轮"的平台状态算，避免只跑了 1 个平台就报 completed。
        overall = self._compute_overall([info.status for info in statuses.values()])
        job_total = self.total_ms
        if job_total is None and overall in (
                "completed", "partial", "failed", "cancelled"):
            job_total = self._ms_since(self._start_ts)
        return SearchJobResponse(
            job_id=self.job_id, overall=overall,
            keyword=self.keyword, created_at=self.created_at,
            completed_at=self.completed_at, total_ms=job_total,
            platforms=pdict, results=all_results,
            hydration_status=self.hydration_status, exploration=self.exploration_info)


class JobConflictError(Exception):
    pass


class InvalidPlatformsError(Exception):
    pass


search_job_manager = SearchJobManager()
