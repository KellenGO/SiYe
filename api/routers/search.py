# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

"""FastAPI router for aggregate search + login endpoints."""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import uuid
from datetime import datetime, timezone
from typing import Dict, Optional, Literal

from fastapi import APIRouter, HTTPException, Header, Request, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from ..schemas.search import SearchJobRequestSchema, SearchJobResponse
from ..schemas.favorites import FavoritesJobRequest, FavoritesJobResponse, MissingDecisions
from ..services.remote_favorites_store import get_remote_favorites_store
from ..services.favorites_job_manager import favorites_job_manager
from ..services.search_job_manager import (
    search_job_manager, JobConflictError, InvalidPlatformsError,
)
from ..services import accounts as accounts_service
from ..services.worker_process import (
    cancel_task_quietly, spawn_worker, terminate_worker,
)

search_router = APIRouter(prefix="/api/search", tags=["aggregate-search"])



# ── Operation coordinator（Phase 4.2：搜索/登录排他，账号操作共享 2 槽）──

_operation_coordinator = accounts_service.operation_coordinator

#: 未达并发上限、但该平台正在后台验证时的通用说明（三个端点各不相同的那句
#: 由各自传入 —— 同步要"再同步"、清除要"再清除"）。
_ACCOUNT_BUSY_MESSAGE = "已有两个账号操作正在进行，请稍后再试"


@contextlib.asynccontextmanager
async def _account_operation_gate(
    platform: str,
    kind: str,
    action: str,
    verify_busy_message: str,
    *,
    skip_release_if_verifying: bool = False,
):
    """账号操作（同步 / 验证 / 清除）的统一守卫。

    三个端点原先各抄了一遍同样的 12 行，还各抄了一遍"竞态复查"，改一处漏两处。
    这里把顺序固定下来（顺序本身是正确性的一部分）：

    1. 抢槽位：搜索排他 + 同平台串行 + 全局最多两个并发账号操作；
    2. **acquire 之后复查搜索** —— 搜索的排他租约创建后立即释放、运行期靠
       ``is_search_active()`` 判断，所以拿到槽位后仍可能已有搜索在跑；
    3. 停掉该平台常驻 worker，避免与 profile 锁冲突。

    未被拒时 yield ``None``；被拒时 yield 一个现成的 409 响应，调用方直接
    ``return`` 即可。
    """
    reason = await _operation_coordinator.acquire_account(platform, kind)
    if reason:
        if search_job_manager.is_search_active():
            yield _account_error(409, "search_in_progress",
                                 f"正在搜索，暂时不能{action}，请等待搜索完成", platform)
            return
        if reason == "platform":
            yield _account_error(409, "verification_in_progress",
                                 verify_busy_message, platform)
            return
        yield _account_error(409, "account_op_in_progress",
                             _ACCOUNT_BUSY_MESSAGE, platform)
        return

    if search_job_manager.is_search_active():
        await _operation_coordinator.release_account(platform)
        yield _account_error(409, "search_in_progress",
                             f"正在搜索，暂时不能{action}，请等待搜索完成", platform)
        return

    await search_job_manager.stop_platform_worker(platform)
    try:
        yield None
    finally:
        # 后台验证仍在跑时槽位不释放（由任务 done 回调释放）；
        # 任务已完成则这里释放。
        if not (skip_release_if_verifying
                and accounts_service.is_verify_active(platform)):
            await _operation_coordinator.release_account(platform)

# ── Login state ─────────────────────────────────────────────────────────

_login_jobs: Dict[str, dict] = {}
_login_procs: Dict[str, asyncio.subprocess.Process] = {}
_login_tasks: Dict[str, asyncio.Task] = {}
_MAX_LOGIN_JOBS = 20  # keep at most this many terminal login jobs

LOGIN_TOTAL_TIMEOUT = 620  # 10 min + margin
LOGIN_DRAIN_TIMEOUT = 5
LOGIN_KILL_TIMEOUT = 3


def _cleanup_old_login_jobs():
    terminal = [k for k, v in _login_jobs.items()
                if v.get("status") in ("succeeded", "failed", "timed_out")]
    excess = len(terminal) - _MAX_LOGIN_JOBS
    for k in terminal[:max(0, excess)]:
        _login_jobs.pop(k, None)
    # also clean up corresponding tasks
    for k in list(_login_tasks.keys()):
        if k not in _login_jobs:
            t = _login_tasks.pop(k, None)
            if t and not t.done():
                t.cancel()


async def _run_login_worker(platform: str, job_id: str):
    _login_jobs[job_id] = {
        "platform": platform, "status": "running", "message": "Starting...",
        "created_at": datetime.now(timezone.utc).isoformat(), "completed_at": None,
    }
    proc = None
    stdout_task = None
    stderr_task = None
    await accounts_service.begin_scan_login(platform)
    done_received = False
    error_received = False
    final_status = "failed"
    final_message = "Unknown error"

    try:
        proc = await spawn_worker()
        _login_procs[job_id] = proc

        request_json = json.dumps({
            "job_id": job_id, "mode": "login", "platform": platform,
            "keyword": "", "limit": 0,
        }) + "\n"
        if proc.stdin:
            proc.stdin.write(request_json.encode("utf-8"))
            await proc.stdin.drain()
            proc.stdin.close()

        from aggregate_search.protocol import parse_event_line

        async def _read_out():
            nonlocal done_received, error_received, final_status, final_message
            while True:
                raw = await proc.stdout.readline()
                if not raw:
                    break
                try:
                    line = raw.decode("utf-8", errors="replace").strip()
                except Exception:
                    continue
                if not line:
                    continue
                evt = parse_event_line(line)
                if evt is None:
                    continue
                # Identity validation
                if evt.job_id != job_id:
                    continue
                if evt.platform != platform:
                    continue
                if evt.event == "status":
                    d = evt.data or {}
                    s = d.get("status", final_status)
                    if s in ("running", "succeeded"):
                        final_status = s
                    final_message = d.get("message", final_message)
                elif evt.event == "error":
                    error_received = True
                    d = evt.data or {}
                    final_status = d.get("type", "failed")
                    final_message = d.get("message", final_message)
                elif evt.event == "done":
                    done_received = True
                    break

        async def _read_err():
            while True:
                raw = await proc.stderr.readline()
                if not raw:
                    break

        stdout_task = asyncio.create_task(_read_out())
        stderr_task = asyncio.create_task(_read_err())

        # Wait with total timeout
        try:
            await asyncio.wait_for(
                asyncio.gather(stdout_task, stderr_task),
                timeout=LOGIN_TOTAL_TIMEOUT)
        except asyncio.TimeoutError:
            final_status = "timed_out"
            final_message = "Login process timed out"
            await terminate_worker(proc, grace=LOGIN_KILL_TIMEOUT)

        # Drain remaining stdout/stderr to EOF
        try:
            while True:
                raw = await asyncio.wait_for(proc.stdout.readline(), timeout=LOGIN_DRAIN_TIMEOUT)
                if not raw:
                    break
        except (asyncio.TimeoutError, Exception):
            pass
        try:
            while True:
                raw = await asyncio.wait_for(proc.stderr.readline(), timeout=LOGIN_DRAIN_TIMEOUT)
                if not raw:
                    break
        except (asyncio.TimeoutError, Exception):
            pass

        await terminate_worker(proc, grace=LOGIN_DRAIN_TIMEOUT)

        rc = proc.returncode if proc.returncode is not None else -1

        # Final status determination
        if final_status == "timed_out":
            pass  # keep timed_out
        elif error_received:
            # Keep the real error emitted by the worker — never override
            # it with a "no done event" heuristic.
            pass
        elif not done_received:
            final_status = "failed"
            final_message = f"exit {rc}, no done event"
        elif rc != 0:
            final_status = "failed"
            final_message = f"exit {rc}"
        elif final_status == "succeeded" and done_received and rc == 0:
            final_status = "succeeded"
        else:
            final_status = "failed"
            final_message = final_message or "unknown state"

    except Exception as e:
        final_status = "failed"
        final_message = type(e).__name__
    finally:
        await cancel_task_quietly(stdout_task)
        await cancel_task_quietly(stderr_task)
        await terminate_worker(proc)
        _login_procs.pop(job_id, None)

    accounts_service.finish_scan_login(platform, final_status == "succeeded")
    _login_jobs[job_id].update(
        status=final_status, message=final_message,
        completed_at=datetime.now(timezone.utc).isoformat())
    _cleanup_old_login_jobs()


# ── Endpoints ───────────────────────────────────────────────────────────

class LoginRequest(BaseModel):
    platform: str = Field(..., description="Platform slug")


class LoginStatusResponse(BaseModel):
    job_id: str
    platform: str
    status: str
    message: str = ""
    created_at: str
    completed_at: Optional[str] = None


@search_router.post("/login")
async def start_login(req: LoginRequest):
    valid = {"xhs", "douyin", "bilibili", "zhihu"}
    if req.platform not in valid:
        raise HTTPException(status_code=422, detail=f"Invalid platform: {req.platform}")

    # Phase 4.2: 排他租约 —— 搜索/账号操作进行中时拒绝登录；租约在登录
    # 任务完成前一直持有（任务 done 回调释放）。
    if not await _operation_coordinator.acquire_exclusive("login"):
        if search_job_manager.is_search_active():
            raise HTTPException(status_code=409,
                                detail="A search job is running. Wait for it to complete before logging in.")
        for info in _login_jobs.values():
            if info["status"] in ("pending", "running"):
                raise HTTPException(status_code=409, detail="A login session is already active.")
        raise HTTPException(status_code=409,
                            detail="账号操作进行中，请等待完成后再登录。")

    # Check active login（租约内的二次检查，保持消息一致）
    try:
        for info in _login_jobs.values():
            if info["status"] in ("pending", "running"):
                raise HTTPException(status_code=409, detail="A login session is already active.")
        # Check active search
        if search_job_manager.is_search_active():
            raise HTTPException(status_code=409,
                                detail="A search job is running. Wait for it to complete before logging in.")

        job_id = uuid.uuid4().hex[:12]
        _login_jobs[job_id] = {
            "platform": req.platform, "status": "pending", "message": "Login queued...",
            "created_at": datetime.now(timezone.utc).isoformat(), "completed_at": None,
        }
        task = asyncio.create_task(_run_login_worker(req.platform, job_id))
        _login_tasks[job_id] = task
        task.add_done_callback(_release_login_lease)
    except Exception:
        # 任务未创建/创建失败：租约不会由 done 回调释放，必须在这里释放。
        await _operation_coordinator.release_exclusive("login")
        raise

    names = {"xhs": "小红书", "douyin": "抖音", "bilibili": "B站", "zhihu": "知乎"}
    return {
        "job_id": job_id, "platform": req.platform, "status": "pending",
        "message": f"正在启动 {names.get(req.platform, req.platform)} 登录窗口...",
    }


def _release_login_lease(_task: asyncio.Task) -> None:
    """登录任务结束 → 释放排他租约（done 回调不能 await，用 ensure_future）。"""
    asyncio.ensure_future(_operation_coordinator.release_exclusive("login"))


@search_router.get("/login/{job_id}", response_model=LoginStatusResponse)
async def get_login_status(job_id: str):
    info = _login_jobs.get(job_id)
    if info is None:
        raise HTTPException(status_code=404, detail="Login job not found.")
    return LoginStatusResponse(job_id=job_id, **info)


@search_router.post("/jobs", response_model=SearchJobResponse, status_code=201)
async def create_search_job(req: SearchJobRequestSchema):
    req.keyword = req.keyword.strip()
    if not req.keyword:
        raise HTTPException(status_code=422, detail="Keyword must not be empty.")

    # Phase 4.2: 排他租约（创建期）。登录/账号操作进行中时拒绝。
    if not await _operation_coordinator.acquire_exclusive("search"):
        for info in _login_jobs.values():
            if info["status"] in ("pending", "running"):
                raise HTTPException(status_code=409,
                                    detail="A login session is active. Wait for it to complete before searching.")
        raise HTTPException(status_code=409,
                            detail="账号操作进行中，请等待完成后再搜索。")

    try:
        # Check active login
        for info in _login_jobs.values():
            if info["status"] in ("pending", "running"):
                raise HTTPException(status_code=409,
                                    detail="A login session is active. Wait for it to complete before searching.")
        try:
            return await search_job_manager.create_job(req)
        except InvalidPlatformsError as e:
            raise HTTPException(status_code=422, detail=str(e))
        except JobConflictError:
            raise HTTPException(status_code=409, detail="A search job is already running.")
    finally:
        # 短租约：搜索任务在后台运行；运行期间的互斥由各端点的
        # is_search_active() 检查与 job 状态保证（Phase 4.2 语义一致）。
        await _operation_coordinator.release_exclusive("search")


@search_router.get("/jobs/current")
async def get_current_job():
    return await search_job_manager.get_current()


@search_router.get("/statistics")
async def get_search_statistics():
    return search_job_manager.metrics.snapshot(search_job_manager.cooldowns)


@search_router.post("/favorites/jobs", response_model=FavoritesJobResponse, status_code=201)
async def create_favorites_job(req: FavoritesJobRequest, summary: bool = False):
    if search_job_manager.is_search_active():
        raise HTTPException(status_code=409, detail="搜索进行中，请等待完成后再同步收藏夹。")
    if not await _operation_coordinator.acquire_exclusive("favorites"):
        raise HTTPException(status_code=409, detail="账号或收藏夹操作进行中，请稍后再试。")
    try:
        response = await favorites_job_manager.create(req, summary=True) if summary else await favorites_job_manager.create(req)
        task = favorites_job_manager.active_task()
        if task:
            task.add_done_callback(
                lambda _task: asyncio.ensure_future(
                    _operation_coordinator.release_exclusive("favorites")))
        else:
            await _operation_coordinator.release_exclusive("favorites")
        return response
    except RuntimeError:
        await _operation_coordinator.release_exclusive("favorites")
        raise HTTPException(status_code=409, detail="收藏夹同步正在进行中。")
    except Exception:
        await _operation_coordinator.release_exclusive("favorites")
        raise


@search_router.get("/favorites/archive")
async def get_favorites_archive(
    platform: Optional[Literal["bilibili", "xhs", "douyin", "zhihu"]] = None,
    state: Optional[Literal["present", "pending", "archived", "legacy"]] = None,
    account: Optional[str] = Query(default=None, max_length=128),
    offset: int = Query(default=0, ge=0), limit: int = Query(default=50, ge=1, le=100),
):
    return await asyncio.to_thread(get_remote_favorites_store().archive_page,
        platform=platform, state=state, account=account, offset=offset, limit=limit)


@search_router.post("/favorites/missing/resolve")
async def resolve_missing_favorites(request: MissingDecisions):
    return await asyncio.to_thread(get_remote_favorites_store().resolve_missing, request.decisions)


@search_router.post("/favorites/archive/purge-legacy")
async def purge_legacy_favorites_archive():
    """清掉「账号未确认」的历史归档。只动同步镜像，不碰本地收藏、备注与收藏夹。"""
    return await asyncio.to_thread(get_remote_favorites_store().purge_legacy_archive)


@search_router.get("/favorites/jobs/latest", response_model=FavoritesJobResponse)
async def get_latest_favorites_job(summary: bool = False):
    """Restore the last favourites result so entering the page needs no sync.

    Declared before ``/favorites/jobs/{job_id}`` so ``latest`` is not captured
    as a job id.
    """
    try:
        response = await favorites_job_manager.latest(summary=True) if summary else await favorites_job_manager.latest()
    except RuntimeError:
        raise HTTPException(status_code=503, detail="本机同步收藏读取失败，请检查磁盘与数据库") from None
    if response is None:
        raise HTTPException(status_code=404, detail="暂无可恢复的收藏夹结果。")
    return response


@search_router.post("/favorites/jobs/{job_id}/cancel", response_model=FavoritesJobResponse)
async def cancel_favorites_job(job_id: str, summary: bool = False):
    response = await favorites_job_manager.cancel(job_id, summary=True) if summary else await favorites_job_manager.cancel(job_id)
    if response is None:
        raise HTTPException(status_code=404, detail="收藏同步任务不存在")
    return response


@search_router.get("/favorites/jobs/{job_id}", response_model=FavoritesJobResponse)
async def get_favorites_job(job_id: str, summary: bool = False):
    response = await favorites_job_manager.get(job_id, summary=True) if summary else await favorites_job_manager.get(job_id)
    if response is None:
        raise HTTPException(status_code=404, detail="收藏夹同步任务不存在。")
    return response


@search_router.get("/jobs/{job_id}", response_model=SearchJobResponse)
async def get_search_job(job_id: str):
    resp = await search_job_manager.get_job(job_id)
    if resp is None:
        raise HTTPException(status_code=404, detail="Job not found.")
    return resp


@search_router.post("/jobs/{job_id}/cancel")
async def cancel_search_job(job_id: str):
    cancelled = await search_job_manager.cancel_job(job_id)
    if not cancelled:
        raise HTTPException(status_code=404, detail="Job not found or already completed.")
    return {"status": "cancelled", "job_id": job_id}


# ── Account management (browser-extension cookie sync) ──────────────────

class SyncTicketRequest(BaseModel):
    platform: str = Field(..., description="Platform slug")


class SyncCookiesRequest(BaseModel):
    cookies: list = Field(..., description="Chrome-v1 raw cookie list")
    cookie_format: str = Field(..., description="Must be 'chrome-v1'")
    extension_protocol_version: int = 0
    request_id: str = ""
    skipped_partitioned: int = 0
    browser_cookie_store_count: int = 0


def _account_error(status_code: int, safe_code: str, safe_message: str,
                   platform: Optional[str] = None,
                   diagnostics: Optional[dict] = None) -> JSONResponse:
    """Structured account error — the extension and web UI read these fields."""
    d = diagnostics or {}
    return JSONResponse(status_code=status_code, content={
        "success": False,
        "platform": platform,
        "verified": False,
        "safe_error_code": safe_code,
        "safe_message": safe_message,
        "sync_stage": d.get("sync_stage"),
        "received_cookie_count": d.get("received_cookie_count"),
        "accepted_cookie_count": d.get("accepted_cookie_count"),
        "skipped_cookie_count": d.get("skipped_cookie_count"),
        "rejected_cookie_count": d.get("rejected_cookie_count"),
        "required_cookie_present": d.get("required_cookie_present"),
        "login_marker_presence": d.get("login_marker_presence"),
        "browser_cookie_store_count": d.get("browser_cookie_store_count"),
    })


def _account_error_from_exc(exc: Exception, status_code: int,
                            platform: Optional[str] = None) -> JSONResponse:
    return _account_error(
        status_code,
        getattr(exc, "safe_code", "account_error"),
        str(exc) or "操作失败，请重试",
        platform=platform,
        diagnostics=getattr(exc, "diagnostics", None),
    )


@search_router.get("/accounts")
async def get_accounts():
    """Per-platform account state — never includes cookies/tokens/paths."""
    return {"accounts": accounts_service.get_accounts()}


@search_router.post("/accounts/sync-ticket")
async def create_sync_ticket(req: SyncTicketRequest):
    """Issue a one-time sync ticket (>=128-bit, 60s, memory-only, single-use)."""
    try:
        ticket = accounts_service.create_sync_ticket(req.platform)
    except accounts_service.PlatformError as e:
        raise HTTPException(status_code=422, detail=str(e))
    return {
        "ticket": ticket,
        "platform": req.platform,
        "expires_in": accounts_service.TICKET_TTL_SECONDS,
    }


@search_router.post("/accounts/{platform}/sync")
async def sync_account_cookies(
    platform: str,
    req: SyncCookiesRequest,
    request: Request,
    x_sync_ticket: Optional[str] = Header(default=None, alias="X-Sync-Ticket"),
):
    """Extension-only endpoint: import chrome-v1 cookies + bounded verify."""
    origin = request.headers.get("origin", "")
    if not origin.startswith("chrome-extension://"):
        return _account_error(403, "extension_origin_required",
                              "仅允许浏览器扩展调用同步接口", platform)
    if not x_sync_ticket:
        return _account_error(400, "sync_ticket_invalid",
                              "缺少一次性同步票据", platform)

    # Phase 4.2: 账号操作共享槽位（不同平台最多 2 并发，同平台串行）。
    # 检查在 ticket 消费之前，新 ticket 不被消费（可重试语义：等验证完成拿新票据再同步）。
    async with _account_operation_gate(
        platform, "sync", "同步账号",
        "该平台正在后台验证登录状态，请稍等验证完成后再同步",
        skip_release_if_verifying=True,
    ) as denied:
        if denied is not None:
            return denied
        try:
            await accounts_service.consume_sync_ticket(x_sync_ticket, platform)
        except accounts_service.TicketError as e:
            return _account_error(400, e.safe_code, str(e), platform)
        try:
            result = await accounts_service.sync_platform_cookies(
                platform, req.cookies, cookie_format=req.cookie_format,
                extension_protocol_version=req.extension_protocol_version,
                browser_cookie_store_count=req.browser_cookie_store_count)
        except accounts_service.CookieFormatInvalidError as e:
            return _account_error_from_exc(e, 400, platform)
        except accounts_service.CookieDomainRejectedError as e:
            return _account_error_from_exc(e, 400, platform)
        except accounts_service.ExtensionProtocolOutdatedError as e:
            return _account_error_from_exc(e, 422, platform)
        except accounts_service.SessionImportError as e:
            return _account_error_from_exc(e, 400, platform)
        except accounts_service.PlatformError as e:
            return _account_error_from_exc(e, 422, platform)

    # No background verify is spawned here: sync_platform_cookies' bounded
    # verify keeps running inside the accounts service (single task per
    # platform, cancelled on shutdown) — the router must NOT start a second
    # verify of the same profile.
    return result


@search_router.post("/accounts/{platform}/verify")
async def verify_account(platform: str, reuse_recent: bool = False):
    """Re-open the headless profile and verify the session via pong."""
    if reuse_recent:
        if platform in accounts_service.PLATFORM_PROFILE_DIRS and not accounts_service.profile_dir_for(platform).is_dir():
            return {"success": True, "platform": platform, "verified": False, "status": "disconnected", "skipped": True}
        cached = accounts_service.recent_verification(platform)
        if cached:
            return cached
    async with _account_operation_gate(
        platform, "verify", "验证账号",
        "该平台正在后台验证登录状态，请稍等验证完成后再试",
    ) as denied:
        if denied is not None:
            return denied
        try:
            return await accounts_service.verify_platform(platform)
        except accounts_service.PlatformError as e:
            return _account_error_from_exc(e, 422, platform)
        except accounts_service.SessionImportError as e:
            return _account_error_from_exc(e, 400, platform)


@search_router.delete("/accounts/{platform}/session")
async def delete_account_session(platform: str):
    """Delete the platform's profile (browser_data only, path-verified)."""
    async with _account_operation_gate(
        platform, "delete", "清除账号",
        "该平台正在后台验证登录状态，请稍等验证完成后再清除",
    ) as denied:
        if denied is not None:
            return denied
        try:
            return await accounts_service.delete_platform_session(platform)
        except accounts_service.PlatformError as e:
            return _account_error_from_exc(e, 422, platform)
        except accounts_service.SessionImportError as e:
            return _account_error_from_exc(e, 400, platform)


# ── Shutdown ────────────────────────────────────────────────────────────

async def _cleanup_login_on_shutdown():
    for job_id, proc in list(_login_procs.items()):
        try:
            if proc.returncode is None:
                proc.kill()
            try:
                await asyncio.wait_for(proc.wait(), timeout=3)
            except asyncio.TimeoutError:
                pass
        except Exception:
            pass
    _login_procs.clear()
    for job_id, task in list(_login_tasks.items()):
        if not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
    _login_tasks.clear()
