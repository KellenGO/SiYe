# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#
# 声明：本代码仅供学习和研究目的使用。使用者应遵守以下原则：
# 1. 不得用于任何商业用途。
# 2. 使用时应遵守目标平台的使用条款和robots.txt规则。
# 3. 不得进行大规模爬取或对平台造成运营干扰。
# 4. 应合理控制请求频率，避免给目标平台带来不必要的负担。
# 5. 不得用于任何非法或不当的用途。
#
# 详细许可条款请参阅项目根目录下的LICENSE文件。

"""
Round 16 常驻平台 worker supervisor 测试。

用 tests/fake_resident_worker.py（确定性假 worker）驱动真实的子进程
生命周期：PID 复用、done 后处理下一请求、max-requests 重启、crash 恢复、
idle 回收、cancel/timeout 终止、shutdown 清理、账号操作只停目标平台、
secret 不进 argv。

注意：本文件内的测试显式开启 supervisor 模式；tests/conftest.py 的
autouse fixture 会把默认模式置回 oneshot，二者互不干扰。
"""

import asyncio
import logging
import os
import sys
import time
from pathlib import Path


import pytest
import pytest_asyncio

import api.services.search_job_manager as sjm
import api.services.accounts as accounts
import api.services.worker_process as worker_process
from aggregate_search.models import WorkerRequest
from api.schemas.search import SearchJobRequestSchema

_FAKE_WORKER = str(Path(__file__).parent / "fake_resident_worker.py")


@pytest_asyncio.fixture
async def manager(monkeypatch):
    """Supervisor 模式 manager：指向假 worker，测试后清理全部驻留进程。"""
    monkeypatch.setattr(worker_process, "WORKER_SCRIPT", _FAKE_WORKER)
    monkeypatch.setattr(sjm, "SEARCH_WORKER_MODE", "supervisor")
    mgr = sjm.SearchJobManager()
    yield mgr
    await mgr.cleanup()


async def _run_to_completion(manager: sjm.SearchJobManager, req) -> sjm.SearchJobResponse:
    resp = await manager.create_job(req)
    job = manager._active_job
    if job is not None and job.task is not None:
        await asyncio.wait_for(job.task, timeout=15)
    fetched = await manager.get_job(resp.job_id)
    assert fetched is not None
    return fetched


def _worker_pid(manager: sjm.SearchJobManager, platform: str):
    worker = manager.supervisor._workers.get(platform)
    if worker is None or worker.proc.returncode is not None:
        return None
    return worker.proc.pid


async def _capture_running_pid(manager: sjm.SearchJobManager, platform: str,
                               label: str) -> int:
    """在 job 运行期间捕获 worker PID（观察运行中的进程，绝不等待其退出）。"""
    for _ in range(300):
        pid = _worker_pid(manager, platform)
        if pid is not None:
            return pid
        await asyncio.sleep(0.02)
    raise AssertionError(f"{label}: 未捕获到运行中的 worker PID")


def _req(keyword: str, platform: str = "xhs", limit: int = 3) -> SearchJobRequestSchema:
    return SearchJobRequestSchema(
        keyword=keyword, platforms=[platform], limit_per_platform=limit,
        bypass_cache=True)


class TestResidentSupervisor:
    @pytest.mark.asyncio
    async def test_pid_reused_across_jobs_and_done_then_next(self, manager):
        """第二次搜索复用同一 worker 进程；done 后可处理下一请求。"""
        first = await _run_to_completion(manager, _req("__result_2__"))
        assert first.platforms["xhs"].status == "succeeded"
        assert first.platforms["xhs"].result_count == 2
        pid1 = _worker_pid(manager, "xhs")
        assert pid1 is not None
        spawn1 = first.platforms["xhs"].timings.spawn_ms
        # worker 上报的内部指标必须合并进 job timings（worker→manager 闭环）。
        timings = first.platforms["xhs"].timings
        assert timings.worker_ready_ms == 5
        assert timings.search_api_ms == 7
        assert timings.fast_path_used is True

        second = await _run_to_completion(manager, _req("__result_1__"))
        assert second.platforms["xhs"].status == "succeeded"
        assert second.platforms["xhs"].result_count == 1
        pid2 = _worker_pid(manager, "xhs")
        assert pid2 == pid1
        # 已有驻留进程 → 第二次 spawn 开销不应更大。毫秒取整时两次都可能为 0。
        spawn2 = second.platforms["xhs"].timings.spawn_ms
        assert spawn2 <= spawn1

    @pytest.mark.asyncio
    async def test_max_requests_restart_spawns_new_pid(self, manager, monkeypatch):
        """MAX_REQUESTS_PER_WORKER=1：处理后确定性退役，下次搜索重建新进程。"""
        monkeypatch.setattr(sjm.PlatformWorkerSupervisor,
                            "MAX_REQUESTS_PER_WORKER", 1)
        resp1 = await manager.create_job(_req("__result_1__"))
        pid1 = await _capture_running_pid(manager, "xhs", "job1")
        job1 = manager._active_job
        await asyncio.wait_for(job1.task, timeout=15)
        done1 = await manager.get_job(resp1.job_id)
        assert done1.platforms["xhs"].status == "succeeded"
        # 退役是确定性的：worker 已从注册表移除且已退出。
        assert manager.supervisor._workers.get("xhs") is None

        resp2 = await manager.create_job(_req("__result_1__"))
        pid2 = await _capture_running_pid(manager, "xhs", "job2")
        job2 = manager._active_job
        await asyncio.wait_for(job2.task, timeout=15)
        done2 = await manager.get_job(resp2.job_id)
        assert done2.platforms["xhs"].status == "succeeded"
        assert done2.platforms["xhs"].result_count == 1
        assert pid2 != pid1, "max-requests 退役后必须创建新进程"

    @pytest.mark.asyncio
    async def test_immediate_next_request_after_max_requests(self, manager,
                                                             monkeypatch):
        """Round 16.2: MAX=1 时，第一条完成后立即发第二条（不等待旧 worker
        退出、不 sleep）—— 连续多轮，第二条全部成功且 PID 已更换。"""
        monkeypatch.setattr(sjm.PlatformWorkerSupervisor,
                            "MAX_REQUESTS_PER_WORKER", 1)
        for round_no in range(3):
            resp1 = await manager.create_job(_req("__result_1__"))
            pid1 = await _capture_running_pid(manager, "xhs",
                                              f"round{round_no}/job1")
            job1 = manager._active_job
            await asyncio.wait_for(job1.task, timeout=15)
            done1 = await manager.get_job(resp1.job_id)
            assert done1.platforms["xhs"].status == "succeeded"

            # 立即发第二个请求（旧 worker 已完成确定性退役，不等待其退出）。
            resp2 = await manager.create_job(_req("__result_1__"))
            pid2 = await _capture_running_pid(manager, "xhs",
                                              f"round{round_no}/job2")
            job2 = manager._active_job
            await asyncio.wait_for(job2.task, timeout=15)
            done2 = await manager.get_job(resp2.job_id)
            assert done2.platforms["xhs"].status == "succeeded", (
                f"round{round_no} 第二个请求必须成功（不得丢失请求）")
            assert done2.platforms["xhs"].result_count == 1
            assert pid2 != pid1, (
                f"round{round_no} 第二个请求必须由新 PID 处理")

    @pytest.mark.asyncio
    async def test_delayed_exit_after_done_not_lost(self, manager, monkeypatch):
        """Round 16.2: 退出耗时超过旧 50ms 启发式（0.3s）也不丢失下一请求。"""
        monkeypatch.setattr(sjm.PlatformWorkerSupervisor,
                            "MAX_REQUESTS_PER_WORKER", 1)
        resp1 = await manager.create_job(_req("__delayed_exit_0__"))
        pid1 = await _capture_running_pid(manager, "xhs", "job1")
        job1 = manager._active_job
        await asyncio.wait_for(job1.task, timeout=15)
        done1 = await manager.get_job(resp1.job_id)
        assert done1.platforms["xhs"].status == "succeeded"
        # worker 已确定性退役（移除 + 退出），绝不残留。
        assert manager.supervisor._workers.get("xhs") is None
        # 立即第二个请求：必须由新 worker 成功完成。
        resp2 = await manager.create_job(_req("__result_1__"))
        pid2 = await _capture_running_pid(manager, "xhs", "job2")
        job2 = manager._active_job
        await asyncio.wait_for(job2.task, timeout=15)
        done2 = await manager.get_job(resp2.job_id)
        assert done2.platforms["xhs"].status == "succeeded"
        assert done2.platforms["xhs"].result_count == 1
        assert pid2 != pid1, "0.3s 延迟退出后下一请求必须由新 PID 处理"

    @pytest.mark.asyncio
    async def test_crash_recovery_new_pid(self, manager):
        """worker 硬崩溃（无 done）→ 平台 failed；下次搜索自动重建新进程。"""
        first = await _run_to_completion(manager, _req("__crash_7__"))
        assert first.platforms["xhs"].status == "failed"
        assert first.platforms["xhs"].error_summary == "搜索未正常完成，请重试"
        # crash 后 submit 会丢弃旧 worker（returncode=7 已置位）。
        second = await _run_to_completion(manager, _req("__result_1__"))
        assert second.platforms["xhs"].status == "succeeded"
        assert second.platforms["xhs"].result_count == 1

    @pytest.mark.asyncio
    async def test_uncaught_exception_fails_fast_and_new_pid(self, manager):
        """Round 16.1: 未捕获异常 → worker 立即退出（不悬挂到
        WORKER_TIMEOUT），平台快速 failed；随后请求由新 PID 正常完成。"""
        start = time.monotonic()
        first = await _run_to_completion(manager, _req("__uncaught_boom__"))
        elapsed = time.monotonic() - start
        assert first.platforms["xhs"].status == "failed"
        assert elapsed < 30, (
            f"未捕获异常必须在短时间内失败（实际 {elapsed:.1f}s），"
            "不能等待 WORKER_TIMEOUT_SECONDS")
        assert first.platforms["xhs"].error_summary == "搜索未正常完成，请重试"

        second = await _run_to_completion(manager, _req("__result_1__"))
        assert second.platforms["xhs"].status == "succeeded"
        assert second.platforms["xhs"].result_count == 1

    @pytest.mark.asyncio
    async def test_done_nonzero_is_failed(self, manager, monkeypatch):
        """Round 16.1 严格语义: done + nonzero 退出 → failed（done 后崩溃）。

        Round 16.2: 以 MAX=1 退役场景确定性观察退出码（无时间启发式）。"""
        monkeypatch.setattr(sjm.PlatformWorkerSupervisor,
                            "MAX_REQUESTS_PER_WORKER", 1)
        first = await _run_to_completion(manager, _req("__exit_3__"))
        assert first.platforms["xhs"].status == "failed"
        assert first.platforms["xhs"].error_summary == "搜索未正常完成，请重试"

    @pytest.mark.asyncio
    async def test_no_done_exit0_is_failed(self, manager):
        """Round 16.1 严格语义: 无 done + exit0 → failed。"""
        first = await _run_to_completion(manager, _req("__no_done_exit0__"))
        assert first.platforms["xhs"].status == "failed"
        assert first.platforms["xhs"].error_summary == "搜索未正常完成，请重试"

    @pytest.mark.asyncio
    async def test_reused_worker_flag(self, manager):
        """Round 16.1: 第二次搜索明确标记 reused_worker=true，spawn≈0。"""
        first = await _run_to_completion(manager, _req("__result_1__"))
        assert first.platforms["xhs"].timings.reused_worker is False
        second = await _run_to_completion(manager, _req("__result_1__"))
        assert second.platforms["xhs"].timings.reused_worker is True
        assert second.platforms["xhs"].timings.spawn_ms <= \
            first.platforms["xhs"].timings.spawn_ms

    @pytest.mark.asyncio
    async def test_idle_reaper_graceful_exit(self, manager, monkeypatch):
        """空闲超时 → 关闭 stdin 优雅退出，worker 从注册表移除。"""
        monkeypatch.setattr(sjm.PlatformWorkerSupervisor,
                            "IDLE_TIMEOUT_SECONDS", 0.2)
        monkeypatch.setattr(sjm.PlatformWorkerSupervisor,
                            "_REAP_INTERVAL_SECONDS", 0.05)
        resp = await _run_to_completion(manager, _req("__result_1__"))
        assert resp.platforms["xhs"].status == "succeeded"
        assert _worker_pid(manager, "xhs") is not None
        await asyncio.sleep(1.0)
        assert manager.supervisor._workers.get("xhs") is None

    @pytest.mark.asyncio
    async def test_cancel_kills_worker_and_next_job_recovers(self, manager):
        """cancel 终止对应 worker；之后搜索重建并正常完成。"""
        resp = await manager.create_job(_req("__slow_5__"))
        for _ in range(100):
            if _worker_pid(manager, "xhs") is not None:
                break
            await asyncio.sleep(0.05)
        assert _worker_pid(manager, "xhs") is not None
        assert await manager.cancel_job(resp.job_id) is True

        cancelled = await manager.get_job(resp.job_id)
        assert cancelled.overall == "cancelled"
        assert cancelled.platforms["xhs"].status == "cancelled"
        worker = manager.supervisor._workers.get("xhs")
        assert worker is None or worker.proc.returncode is not None

        again = await _run_to_completion(manager, _req("__result_1__"))
        assert again.platforms["xhs"].status == "succeeded"
        assert _worker_pid(manager, "xhs") is not None

    @pytest.mark.asyncio
    async def test_timeout_kills_worker(self, manager, monkeypatch):
        """平台超时 → 终止对应 worker，状态 timed_out，无残留。"""
        monkeypatch.setattr(sjm, "WORKER_TIMEOUT_SECONDS", 1.0)
        resp = await manager.create_job(_req("__slow_5__"))
        job = manager._active_job
        assert job is not None and job.task is not None
        await asyncio.wait_for(job.task, timeout=15)
        timed = await manager.get_job(resp.job_id)
        assert timed.platforms["xhs"].status == "timed_out"
        worker = manager.supervisor._workers.get("xhs")
        assert worker is None or worker.proc.returncode is not None

    @pytest.mark.asyncio
    async def test_stop_platform_worker_only_target(self, manager):
        """账号操作前 stop_platform_worker 只停目标平台，不影响其他平台。"""
        line = WorkerRequest(
            job_id="z1", mode="search", platform="zhihu",
            keyword="__result_1__",
        ).model_dump_json().encode("utf-8") + b"\n"
        worker = await manager.supervisor.submit("zhihu", line)
        assert worker.proc.returncode is None
        zhihu_pid = worker.proc.pid

        await manager.stop_platform_worker("xhs")  # 未注册 → no-op
        live = manager.supervisor._workers.get("zhihu")
        assert live is not None and live.proc.returncode is None
        assert live.proc.pid == zhihu_pid

        await manager.stop_platform_worker("zhihu")  # 真正停止
        live = manager.supervisor._workers.get("zhihu")
        assert live is None or live.proc.returncode is not None

    @pytest.mark.asyncio
    async def test_cleanup_stops_all_workers(self, manager):
        """shutdown cleanup 停止全部平台 worker。"""
        line = WorkerRequest(
            job_id="z1", mode="search", platform="zhihu",
            keyword="__result_1__",
        ).model_dump_json().encode("utf-8") + b"\n"
        await manager.supervisor.submit("zhihu", line)
        await manager.supervisor.submit("xhs", WorkerRequest(
            job_id="x1", mode="search", platform="xhs",
            keyword="__result_1__",
        ).model_dump_json().encode("utf-8") + b"\n")
        assert len(manager.supervisor._workers) == 2

        await manager.cleanup()
        assert manager.supervisor._workers == {}

    @pytest.mark.asyncio
    async def test_secret_not_in_argv_or_response(self, manager, monkeypatch):
        """会话快照 secret 只经 stdin 传输：不进子进程 argv/env、不进 API 响应。"""
        secret_value = "SUPER_SECRET_COOKIE_VALUE_9f3a"
        await accounts.set_session_snapshot("xhs", {"web_session": secret_value})
        captured_args: list = []
        captured_env: list = []
        original_exec = asyncio.create_subprocess_exec

        async def _recording_exec(*args, **kwargs):
            captured_args.append(args)
            captured_env.append(kwargs.get("env"))
            return await original_exec(*args, **kwargs)

        monkeypatch.setattr(sjm.asyncio, "create_subprocess_exec",
                            _recording_exec)
        try:
            resp = await _run_to_completion(manager, _req("__result_1__"))
            assert resp.platforms["xhs"].status == "succeeded"
            assert captured_args, "worker 应被真实 spawn 一次"
            args = captured_args[0]
            # 子进程 argv 只有解释器 + 脚本 + --resident，绝无 Cookie。
            assert secret_value not in args
            assert not any("web_session" == a for a in args)
            assert args == (sys.executable, _FAKE_WORKER, "--resident")
            # env 只是 os.environ + 编码变量，不含 secret。
            env_str = str(captured_env[0])
            assert secret_value not in env_str
            # API 响应不含 secret。
            body = resp.model_dump_json()
            assert secret_value not in body
            # 正向对照：请求行（stdin 传输通道）确实包含快照。
            request_json = manager._build_request_json(manager._active_job, "xhs")
            assert secret_value in request_json.decode("utf-8")
        finally:
            await accounts.clear_session_snapshot("xhs")


# ── Round 16.1：真实 worker 子进程（坏 stdin → 快速非零退出、不泄露）──────

_REAL_WORKER = str(Path(__file__).parent.parent / "aggregate_search" / "worker.py")


def test_real_worker_malformed_stdin_exits_fast_no_secret():
    """真实 worker：截断 JSON（含 Cookie 样内容）→ 立即非零退出，
    stderr 只输出异常类型，绝不输出输入内容。"""
    import subprocess
    req = (b'{"job_id":"j1","mode":"search","platform":"xhs",'
           b'"session_snapshot":{"web_session":"LEAK-COOKIE-VALUE"}')  # 截断
    proc = subprocess.run(
        [sys.executable, _REAL_WORKER], input=req,
        capture_output=True, timeout=30)
    assert proc.returncode != 0, "坏 stdin 必须非零退出"
    assert b"LEAK-COOKIE-VALUE" not in proc.stderr
    assert b"JSONDecodeError" in proc.stderr


def test_real_worker_bad_snapshot_stdin_exits_fast_no_secret():
    """真实 worker：session_snapshot 类型错误 → ValueError（__init__ 预检），
    stderr 只有异常类型，不回显快照输入值；进程快速非零退出。"""
    import subprocess
    req = (b'{"job_id":"j1","mode":"search","platform":"xhs",'
           b'"keyword":"k","limit":5,'
           b'"session_snapshot":"LEAK-SNAPSHOT-STRING"}\n')
    proc = subprocess.run(
        [sys.executable, _REAL_WORKER], input=req,
        capture_output=True, timeout=30)
    assert proc.returncode != 0
    assert b"LEAK-SNAPSHOT-STRING" not in proc.stderr
    assert b"ValueError" in proc.stderr


# ── worker 日志尾部：脱敏 + 排障输出 ─────────────────────────────────

def test_sanitize_worker_log_lines_drops_secret_lines_and_truncates():
    """含敏感关键字的行整行丢弃；超长行截断。"""
    text = "\n".join([
        "INFO [DouYinCrawler.search] page: 0 is empty",
        "  DEBUG cookie=abc123  ",
        "msToken=xyz",
        "X-S: signature-value",
        "ERROR " + "x" * 500,
        "",
    ])
    lines = worker_process.sanitize_worker_log_lines(text)
    assert lines[0] == "INFO [DouYinCrawler.search] page: 0 is empty"
    assert len(lines) == 2, "含 cookie/token/签名 的行必须整行丢弃"
    assert len(lines[1]) == worker_process.LOG_LINE_LIMIT


def test_worker_log_tail_is_logged_when_platform_ends_empty(caplog):
    """平台 0 结果 / 失败时把 worker 日志尾部打到后端日志（这是唯一能排障的证据）。"""
    manager = sjm.SearchJobManager()
    job = sjm._ActiveJob(job_id="job-1", keyword="测试", platforms=["douyin"],
                         limit_per_platform=3)
    worker = sjm._ResidentWorker(platform="douyin", proc=None)
    worker.log_tail.extend([
        "[DouYinCrawler.search] Current keyword: 测试",
        "[DouYinCrawler.search] page: 0 is empty",
    ])
    manager.supervisor._workers["douyin"] = worker

    with caplog.at_level(logging.WARNING, logger=sjm.__name__):
        manager._log_worker_tail(job, "douyin", "empty")

    messages = [record.getMessage() for record in caplog.records]
    assert any("douyin" in m and "empty" in m for m in messages)
    assert any("page: 0 is empty" in m for m in messages)


@pytest.mark.asyncio
async def test_empty_status_error_summary_reaches_response(manager):
    """worker 上报 `empty` + error_summary 时，原因要出现在响应里。

    抖音"已登录但返回 0 条"就靠这条把"疑似平台风控"告诉前端，
    否则用户只能看到光秃秃的"无结果"。
    """
    resp = await _run_to_completion(manager, _req("__empty_with_reason__"))
    info = resp.platforms["xhs"]
    assert info.status == "empty"
    assert info.error_summary == "平台返回 0 条（测试原因）"


@pytest.mark.asyncio
async def test_succeeded_status_without_done_keeps_success(manager):
    """worker 报过 succeeded 但没发 done 就退出 → 保住 succeeded，不翻 failed。

    这是 Ubuntu CI 上出现过的假失败：进程退得比父进程读完管道快，最后那行 done 丢了。
    """
    resp = await _run_to_completion(manager, _req("__succeeded_no_done_exit0__"))
    assert resp.platforms["xhs"].status == "succeeded"
    assert resp.platforms["xhs"].result_count == 1


def test_worker_log_tail_not_logged_when_platform_succeeded(caplog):
    """有结果时不该刷日志（正常路径保持安静）。"""
    manager = sjm.SearchJobManager()
    job = sjm._ActiveJob(job_id="job-2", keyword="测试", platforms=["xhs"],
                         limit_per_platform=3)
    from aggregate_search.models import UnifiedSearchResult
    job.platform_results["xhs"].append(UnifiedSearchResult(
        platform="xhs", content_id="c1", title="t", url="https://example.com/c1"))
    worker = sjm._ResidentWorker(platform="xhs", proc=None)
    worker.log_tail.append("INFO 一切正常")
    manager.supervisor._workers["xhs"] = worker

    with caplog.at_level(logging.WARNING, logger=sjm.__name__):
        manager._log_worker_tail(job, "xhs", "succeeded")

    assert [r.getMessage() for r in caplog.records] == []
