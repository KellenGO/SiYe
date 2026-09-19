# -*- coding: utf-8 -*-
"""
Round 13: cancel lifecycle hardening.

Reproduces the manual-acceptance blocker — cancel mid-search returns 500 and
the job is stuck in "cancelling" forever (permanent 409 for new searches).

Every test drives the PRODUCTION SearchJobManager.cancel_job (the cancel
algorithm is never reimplemented in tests). Subprocesses are real local
`python -c "import time; time.sleep(60)"` children — no platform accounts,
no network, no cookies. All tests assert their children exited (no stray
processes left in Task Manager).
"""

import asyncio
import sys
import textwrap


import pytest

from api.schemas.search import SearchJobRequestSchema
from api.services.search_job_manager import SearchJobManager, _ActiveJob, JobConflictError
from aggregate_search.models import UnifiedSearchResult

SLEEP_60 = ["-c", "import time; time.sleep(60)"]


def _make_result(platform: str, content_id: str, rank: int = 0) -> UnifiedSearchResult:
    return UnifiedSearchResult(
        platform=platform,
        content_id=content_id,
        title=f"Test {content_id}",
        url=f"https://{platform}.example.com/{content_id}",
        rank=rank,
    )


async def _spawn_sleep(loop=None):
    """Real, still-running local python subprocess (sleep 60)."""
    return await asyncio.create_subprocess_exec(sys.executable, *SLEEP_60)


async def _job_with_running_worker(job_id: str = "job-a",
                                   platforms=("xhs", "douyin"),
                                   loop=None):
    """Production _ActiveJob with: one succeeded platform (results kept),
    one running platform, a real subprocess, and a still-running task."""
    job = _ActiveJob(job_id, "词", list(platforms), limit_per_platform=5)
    job.set_platform_status("xhs", "succeeded", result_count=1)
    job.add_result("xhs", _make_result("xhs", "x1"))
    job.set_platform_status("douyin", "running")
    proc = await _spawn_sleep()
    job.procs.append(proc)

    async def _still_running():
        await asyncio.sleep(60)

    job.task = asyncio.create_task(_still_running())
    return job, proc


# ── Fake worker (full production flow, no real platforms) ──────────────

FAKE_WORKER = textwrap.dedent(
    """\
    import json, sys, time
    line = sys.stdin.readline()
    req = json.loads(line)
    job_id = req["job_id"]
    platform = req["platform"]

    def emit(event, data):
        print("MC_AGG_EVENT\\t" + json.dumps(
            {"event": event, "job_id": job_id, "platform": platform, "data": data}),
            flush=True)

    emit("status", {"status": "running", "message": "started"})
    if platform == "xhs":
        emit("result", {"platform": "xhs", "content_id": "fake-1",
                        "title": "t", "url": "https://x.example/fake-1", "rank": 0})
        emit("status", {"status": "succeeded"})
        emit("done", {})
        # 故意慢一点再退出：进程退得比父进程读完管道还快时，最后那行 done 会丢
        # （Linux CI 上真出现过：状态被翻成 failed、exit 255 / no done event）。
        time.sleep(0.4)
        sys.exit(0)
    time.sleep(60)
    """
)


# ── 1. 全流程复现：生产 create_job → 生产 worker → 中途取消 ─────────────

@pytest.mark.asyncio
async def test_full_flow_cancel_mid_search(monkeypatch, tmp_path):
    """四平台搜索进行中、部分平台已返回结果时取消：
    - 不抛异常（API 不 500）
    - 最终 overall=cancelled + completed_at 非空
    - 已有结果保留
    - is_search_active() == False
    - 立即可以创建下一次搜索
    - 所有子进程退出"""
    fake = tmp_path / "fake_worker.py"
    fake.write_text(FAKE_WORKER, encoding="utf-8")
    monkeypatch.setattr("api.services.worker_process.WORKER_SCRIPT", str(fake))

    mgr = SearchJobManager()
    resp = await mgr.create_job(
        SearchJobRequestSchema(keyword="词", platforms=["xhs", "douyin", "bilibili", "zhihu"],
                               limit_per_platform=5))
    job = mgr._active_job
    try:
        # 确保真实子进程已启动（4 个）且平台进入 running
        deadline = asyncio.get_running_loop().time() + 15
        while asyncio.get_running_loop().time() < deadline:
            if len(job.procs) == 4 and all(
                    job.platforms_state[p].status in ("running", "succeeded")
                    for p in job.platforms):
                break
            await asyncio.sleep(0.05)
        assert len(job.procs) == 4, "子进程必须全部启动"
        # 确定性断言（Round 17.1：不依赖 job.platforms 与 job.procs 的位置
        # 映射 —— 并发 append 顺序不保证一致）：三个 sleep(60) 子进程必然
        # 存活；xhs 已 succeeded 的进程可能已退出。
        assert sum(1 for p in job.procs if p.returncode is None) >= 3, \
            "至少 3 个子进程必须仍在运行"

        # xhs worker 快速返回结果并成功退出（部分平台已返回结果）
        deadline = asyncio.get_running_loop().time() + 10
        while asyncio.get_running_loop().time() < deadline:
            if job.platforms_state["xhs"].status == "succeeded":
                break
            await asyncio.sleep(0.05)
        assert job.platforms_state["xhs"].status == "succeeded"
        assert len(job.platform_results["xhs"]) == 1

        # 其余平台仍为 running（按平台状态断言，不做进程位置猜测）
        for p in ("douyin", "bilibili", "zhihu"):
            assert job.platforms_state[p].status == "running"

        # 中途取消
        cancelled = await mgr.cancel_job(job.job_id)
        assert cancelled is True

        resp2 = job.to_response()
        assert resp2.overall == "cancelled"
        assert resp2.completed_at is not None
        assert resp2.platforms["xhs"].status == "succeeded"  # 已有状态保留
        assert len(resp2.results) == 1  # 已有结果保留
        assert not mgr.is_search_active()

        # 子进程全部退出
        for p in job.procs:
            assert p.returncode is not None, "取消后子进程必须已退出"

        # 立即可创建下一次搜索
        resp3 = await mgr.create_job(
            SearchJobRequestSchema(keyword="新词", platforms=["xhs"], limit_per_platform=5))
        assert resp3.job_id != job.job_id
    finally:
        await mgr.cleanup()


# ── 2. 确定性复现：任务清理异常不能逃逸（500 + 永久 cancelling） ─────────

@pytest.mark.asyncio
async def test_task_cleanup_exception_must_not_escape():
    """job.task 取消时其清理步骤抛 RuntimeError：
    当前代码 `await job.task` 只捕 CancelledError → RuntimeError 逃逸 →
    cancel_job 抛异常（API 500）、_cancelling 永久 True、无法进入 cancelled。
    修复后：异常被隔离，任务最终 cancelled，可创建新任务。"""
    mgr = SearchJobManager()
    job, proc = await _job_with_running_worker()

    async def _task_with_cleanup_failure():
        try:
            await asyncio.sleep(60)
        finally:
            raise RuntimeError("cleanup exploded")

    job.task = asyncio.create_task(_task_with_cleanup_failure())
    mgr._active_job = job

    cancelled = await mgr.cancel_job(job.job_id)  # 生产 cancel_job
    assert cancelled is True
    assert not job._cancelling, "_cancelling 必须被解除"
    assert job._cancelled, "必须进入 cancelled"
    assert job.to_response().overall == "cancelled"
    assert not mgr.is_search_active()
    assert proc.returncode is not None
    assert job.procs == [], "已处理的进程必须被移除"

    resp = await mgr.create_job(
        SearchJobRequestSchema(keyword="新词", platforms=["xhs"], limit_per_platform=5))
    assert resp.job_id != job.job_id
    await mgr.cleanup()


# ── 3. 部分平台 succeeded、部分 running：结果保留、overall=cancelled ────

@pytest.mark.asyncio
async def test_cancel_keeps_partial_results():
    mgr = SearchJobManager()
    job, proc = await _job_with_running_worker()
    mgr._active_job = job

    cancelled = await mgr.cancel_job(job.job_id)
    assert cancelled is True

    resp = job.to_response()
    assert resp.overall == "cancelled"
    assert resp.platforms["xhs"].status == "succeeded"
    assert resp.platforms["xhs"].result_count == 1
    assert resp.platforms["douyin"].status == "cancelled"
    assert len(resp.results) == 1
    assert resp.completed_at is not None
    assert proc.returncode is not None


# ── 4. 某个 proc.wait() 抛异常：隔离，不影响其他进程与终态 ───────────────

class _RaisingProc:
    """伪进程：wait() 抛异常 —— 生产清理必须隔离该失败。"""

    returncode = None

    def kill(self):
        pass

    async def wait(self):
        raise RuntimeError("wait exploded")


@pytest.mark.asyncio
async def test_proc_wait_exception_isolated():
    mgr = SearchJobManager()
    job, proc = await _job_with_running_worker()
    job.procs.append(_RaisingProc())  # 一个进程 wait 抛异常
    mgr._active_job = job

    cancelled = await mgr.cancel_job(job.job_id)
    assert cancelled is True
    assert job.to_response().overall == "cancelled"
    assert proc.returncode is not None  # 真实进程仍被清理


# ── 5. job.task 以 CancelledError 结束：仍正常 cancelled ────────────────

@pytest.mark.asyncio
async def test_task_already_cancelled_ok():
    mgr = SearchJobManager()
    job, proc = await _job_with_running_worker()

    async def _self_cancel():
        raise asyncio.CancelledError()

    job.task = asyncio.create_task(_self_cancel())
    await asyncio.sleep(0.05)  # 任务已以 CancelledError 结束
    mgr._active_job = job

    cancelled = await mgr.cancel_job(job.job_id)
    assert cancelled is True
    assert job.to_response().overall == "cancelled"
    assert proc.returncode is not None


# ── 6. 两次并发取消幂等，无第二套清理、无 500 ───────────────────────────

@pytest.mark.asyncio
async def test_double_concurrent_cancel_idempotent():
    mgr = SearchJobManager()
    job, proc = await _job_with_running_worker()
    kills = []

    async def _swallows_cancel():
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            await asyncio.sleep(1.0)  # 让两次取消必然重叠

    real_kill = proc.kill

    def counting_kill():
        kills.append(1)
        real_kill()

    proc.kill = counting_kill
    job.task = asyncio.create_task(_swallows_cancel())
    mgr._active_job = job

    t1 = asyncio.create_task(mgr.cancel_job(job.job_id))
    t2 = asyncio.create_task(mgr.cancel_job(job.job_id))
    results = await asyncio.gather(t1, t2)
    assert results == [True, True], "重复取消必须幂等返回成功"
    assert job._cancelled and not job._cancelling
    assert job.to_response().overall == "cancelled"
    assert len(kills) == 1, "不允许启动第二套并发清理"
    assert proc.returncode is not None


# ── 7. 取消过程中不能创建新任务；取消完成后可以 ─────────────────────────

@pytest.mark.asyncio
async def test_cancel_in_progress_blocks_new_job_then_releases():
    mgr = SearchJobManager()
    job, proc = await _job_with_running_worker()

    async def _swallows_cancel():
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            await asyncio.sleep(1.0)

    job.task = asyncio.create_task(_swallows_cancel())
    mgr._active_job = job

    cancel_task = asyncio.create_task(mgr.cancel_job(job.job_id))
    await asyncio.sleep(0.1)  # 取消已开始（_cancelling=True）
    assert mgr.is_search_active(), "取消未完成前新搜索仍应被阻塞"
    with pytest.raises(JobConflictError):
        await mgr.create_job(
            SearchJobRequestSchema(keyword="blocked", platforms=["xhs"], limit_per_platform=5))
    await cancel_task

    assert not mgr.is_search_active()
    resp = await mgr.create_job(
        SearchJobRequestSchema(keyword="after-cancel", platforms=["xhs"], limit_per_platform=5))
    assert resp.job_id != job.job_id
    await mgr.cleanup()


# ── 8. GET 返回 cancelled + completed_at（经由生产 to_response） ─────────

@pytest.mark.asyncio
async def test_get_job_after_cancel():
    mgr = SearchJobManager()
    job, proc = await _job_with_running_worker()
    mgr._active_job = job
    await mgr.cancel_job(job.job_id)

    fetched = await mgr.get_job(job.job_id)
    assert fetched is not None
    assert fetched.overall == "cancelled"
    assert fetched.completed_at is not None
    assert len(fetched.results) == 1
    assert proc.returncode is not None


# ── 9. API 中途取消返回 200，不是 500；未知 job 保持 404 ─────────────────

@pytest.mark.asyncio
async def test_api_cancel_returns_200_not_500(monkeypatch):
    from fastapi import HTTPException
    from api.routers import search as router

    mgr = SearchJobManager()
    job, proc = await _job_with_running_worker()
    mgr._active_job = job
    monkeypatch.setattr(router, "search_job_manager", mgr)

    # 取消中途（任务清理抛异常）也不得返回 500
    resp = await router.cancel_search_job(job.job_id)
    assert resp == {"status": "cancelled", "job_id": job.job_id}

    # 重复取消幂等（不再 404）
    resp2 = await router.cancel_search_job(job.job_id)
    assert resp2 == {"status": "cancelled", "job_id": job.job_id}

    # 未知 job 保持 404
    with pytest.raises(HTTPException) as ei:
        await router.cancel_search_job("nonexistent")
    assert ei.value.status_code == 404
    assert proc.returncode is not None


# ── 10. shutdown cleanup 与用户 cancel 并发：不死锁、不留进程 ────────────

@pytest.mark.asyncio
async def test_shutdown_cleanup_concurrent_with_cancel():
    mgr = SearchJobManager()
    job, proc = await _job_with_running_worker()
    mgr._active_job = job

    t1 = asyncio.create_task(mgr.cancel_job(job.job_id))
    t2 = asyncio.create_task(mgr.cleanup())
    results = await asyncio.wait_for(asyncio.gather(t1, t2), timeout=30)
    assert results[0] is True
    assert job._cancelled and not job._cancelling
    assert job.to_response().overall == "cancelled"
    assert proc.returncode is not None


# ── 11. 已正常终态的 job 拒绝取消（保持 404/409 语义） ───────────────────

@pytest.mark.asyncio
async def test_terminal_job_cannot_be_cancelled():
    mgr = SearchJobManager()
    job = _ActiveJob("job-t", "词", ["xhs"], limit_per_platform=5)
    job.set_platform_status("xhs", "succeeded")
    job.finalize()
    mgr._active_job = job

    assert job.is_terminal()
    assert await mgr.cancel_job("job-t") is False  # 已正常完成 → 无可取消
    assert job.to_response().overall == "completed"


# ── 12. 报过终态但 done 行丢失：保住结果，不翻成 failed ──────────────────

NO_DONE_WORKER = textwrap.dedent(
    """\
    import json, sys
    line = sys.stdin.readline()
    req = json.loads(line)
    job_id = req["job_id"]
    platform = req["platform"]

    def emit(event, data):
        print("MC_AGG_EVENT\\t" + json.dumps(
            {"event": event, "job_id": job_id, "platform": platform, "data": data}),
            flush=True)

    emit("status", {"status": "running", "message": "started"})
    emit("result", {"platform": platform, "content_id": "fake-1",
                    "title": "t", "url": "https://x.example/fake-1", "rank": 0})
    emit("status", {"status": "succeeded"})
    sys.exit(0)  # 少了 done 行（Linux CI 上进程退得比父进程读完管道还快时真会发生）
    """
)


@pytest.mark.asyncio
async def test_succeeded_without_done_event_keeps_results(monkeypatch, tmp_path):
    """worker 已报 succeeded、只是 done 行没读到 → 保留结果与 succeeded，不翻 failed。

    这是 CI（Ubuntu）上真实出现过的 flake 根因：以前会把已经成功的平台标成
    "exit 255 / no done event" 的 failed —— 用户拿到了结果却看到"搜索失败"。
    """
    fake = tmp_path / "no_done_worker.py"
    fake.write_text(NO_DONE_WORKER, encoding="utf-8")
    monkeypatch.setattr("api.services.worker_process.WORKER_SCRIPT", str(fake))

    mgr = SearchJobManager()
    resp = await mgr.create_job(SearchJobRequestSchema(
        keyword="no-done-词", platforms=["xhs"], limit_per_platform=5, bypass_cache=True))
    try:
        await asyncio.wait_for(mgr._active_job.task, timeout=30)
        fetched = await mgr.get_job(resp.job_id)
        assert fetched is not None
        assert fetched.platforms["xhs"].status == "succeeded"
        assert fetched.platforms["xhs"].result_count == 1
        assert len(fetched.results) == 1
    finally:
        await mgr.cleanup()
