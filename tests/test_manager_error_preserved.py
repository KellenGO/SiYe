# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

"""Parent-process error preservation tests.

The production ``SearchJobManager`` must never overwrite a real error
event received from the worker with a "exit N, no done event" heuristic —
the exact bug reported as ``✗ 登录失败: exit 0, no done event``.

Uses a fake subprocess object (StreamReader-based) so the full production
``_run_worker`` flow runs without launching a real process.
"""

import asyncio
import json

import pytest


from aggregate_search.protocol import EVENT_PREFIX, EVENT_SEPARATOR
from api.schemas.search import SearchJobRequestSchema
from api.services.search_job_manager import SearchJobManager
from api.services import accounts as acc


class _FakeStdin:
    def write(self, b):
        pass

    async def drain(self):
        pass

    def close(self):
        pass


class _FakeProc:
    """A StreamReader-backed fake subprocess for the manager."""

    def __init__(self, out_lines, rc=0):
        self.stdin = _FakeStdin()
        self.stdout = asyncio.StreamReader()
        for ln in out_lines:
            self.stdout.feed_data((ln + "\n").encode("utf-8"))
        self.stdout.feed_eof()
        self.stderr = asyncio.StreamReader()
        self.stderr.feed_eof()
        self.returncode = None
        self._rc = rc

    async def wait(self):
        if self.returncode is None:
            self.returncode = self._rc
        return self.returncode

    def terminate(self):
        self.returncode = self._rc

    def kill(self):
        self.returncode = self._rc


def _event_line(job_id, event, data, platform="zhihu"):
    payload = json.dumps({
        "event": event, "job_id": job_id, "platform": platform,
        "data": data,
    })
    return f"{EVENT_PREFIX}{EVENT_SEPARATOR}{payload}"


@pytest.mark.asyncio
async def test_real_error_not_overwritten_by_no_done(monkeypatch):
    """Worker emitted login_required error but never emitted done →
    status stays login_required with the worker's own message."""
    manager = SearchJobManager()
    resp = await manager.create_job(SearchJobRequestSchema(
        keyword="k", platforms=["zhihu"], limit_per_platform=1))
    job_id = resp.job_id
    out_lines = [
        _event_line(job_id, "status", {"status": "running"}),
        _event_line(job_id, "error", {
            "type": "login_required",
            "message": "Zhihu login required — please log in first",
        }),
    ]

    async def fake_exec(*a, **k):
        return _FakeProc(out_lines, rc=0)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

    job = manager._active_job
    await asyncio.wait_for(job.task, timeout=10)

    info = job.platforms_state["zhihu"]
    assert info.status == "login_required", \
        f"real error was overwritten: {info.status}"
    assert "no done event" not in (info.error_summary or "")
    assert "Zhihu login required" in (info.error_summary or "")


@pytest.mark.asyncio
async def test_failed_error_not_overwritten_by_no_done(monkeypatch):
    """Worker emitted a generic failed error → kept as-is."""
    manager = SearchJobManager()
    resp = await manager.create_job(SearchJobRequestSchema(
        keyword="k", platforms=["xhs"], limit_per_platform=1))
    job_id = resp.job_id
    out_lines = [
        _event_line(job_id, "status", {"status": "running"}, platform="xhs"),
        _event_line(job_id, "error", {"type": "failed", "message": "boom"},
                    platform="xhs"),
    ]

    async def fake_exec(*a, **k):
        return _FakeProc(out_lines, rc=0)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

    job = manager._active_job
    await asyncio.wait_for(job.task, timeout=10)

    info = job.platforms_state["xhs"]
    assert info.status == "failed"
    assert info.error_summary == "boom"


@pytest.mark.asyncio
async def test_rate_limited_not_overwritten(monkeypatch):
    manager = SearchJobManager()
    resp = await manager.create_job(SearchJobRequestSchema(
        keyword="k", platforms=["douyin"], limit_per_platform=1))
    job_id = resp.job_id
    out_lines = [
        _event_line(job_id, "status", {"status": "running"}, platform="douyin"),
        _event_line(job_id, "error", {"type": "rate_limited", "message": "限流"},
                    platform="douyin"),
    ]

    async def fake_exec(*a, **k):
        return _FakeProc(out_lines, rc=0)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

    job = manager._active_job
    await asyncio.wait_for(job.task, timeout=10)

    info = job.platforms_state["douyin"]
    assert info.status == "rate_limited"
    assert "no done event" not in (info.error_summary or "")


# ── Round 14.2: login_required 反向纠正账号状态 ─────────────────────────
# SearchJobManager 的真实 _read_worker_output 消费 login_required event 后，
# accounts 服务的生产状态必须更新；同步失败绝不能崩溃搜索任务；
# rate_limited/failed 不得误判为登录失效。

def _fake_accounts_profiles(monkeypatch, tmp_path):
    """把 accounts 的 profile 目录指向临时目录并复位目标平台状态。"""
    monkeypatch.setattr(acc, "BROWSER_DATA_DIR", tmp_path)
    monkeypatch.setattr(
        acc, "profile_dir_for",
        lambda p: tmp_path / acc.PLATFORM_PROFILE_DIRS[p])
    acc._platform_state.pop("bilibili", None)
    acc._platform_state.pop("douyin", None)


@pytest.mark.asyncio
async def test_login_required_event_updates_accounts_state(monkeypatch, tmp_path):
    """真实 worker error 路径：login_required event → job 平台状态
    login_required，且 accounts 生产状态 connected → expired / verified=False。
    worker 消息含 Cookie 字样时，账号 safe_message 仍为固定安全文案。"""
    _fake_accounts_profiles(monkeypatch, tmp_path)
    acc.profile_dir_for("bilibili").mkdir(parents=True)
    acc._set_state("bilibili", status="connected", verified=True,
                   last_verified_at="2026-08-13T00:00:00+00:00")

    manager = SearchJobManager()
    resp = await manager.create_job(SearchJobRequestSchema(
        keyword="k", platforms=["bilibili"], limit_per_platform=1))
    job_id = resp.job_id
    out_lines = [
        _event_line(job_id, "status", {"status": "running"},
                    platform="bilibili"),
        _event_line(job_id, "error", {
            "type": "login_required",
            "message": "SESSDATA cookie expired, please login",
        }, platform="bilibili"),
    ]

    async def fake_exec(*a, **k):
        return _FakeProc(out_lines, rc=0)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

    job = manager._active_job
    await asyncio.wait_for(job.task, timeout=10)

    assert job.platforms_state["bilibili"].status == "login_required"
    st = acc._state_of("bilibili")
    assert st["status"] == "expired"
    assert st["verified"] is False
    assert st["safe_error_code"] == "login_required"
    assert st["safe_message"] == "B站登录状态已失效，请前往账号设置重新同步"
    assert "SESSDATA" not in st["safe_message"]
    assert "cookie" not in st["safe_message"].lower()
    assert st["last_verified_at"] == "2026-08-13T00:00:00+00:00", \
        "降级不得清除 last_verified_at"


@pytest.mark.asyncio
async def test_account_sync_failure_does_not_crash_job(monkeypatch, tmp_path):
    """账号状态同步抛异常 → 搜索任务不崩溃，平台状态仍为 login_required。"""
    _fake_accounts_profiles(monkeypatch, tmp_path)
    acc.profile_dir_for("bilibili").mkdir(parents=True)
    acc._set_state("bilibili", status="connected", verified=True)

    def boom(platform):
        raise RuntimeError("accounts service down")

    monkeypatch.setattr(
        "api.services.search_job_manager.mark_login_required_from_search", boom)

    manager = SearchJobManager()
    resp = await manager.create_job(SearchJobRequestSchema(
        keyword="k", platforms=["bilibili"], limit_per_platform=1))
    job_id = resp.job_id
    out_lines = [
        _event_line(job_id, "error", {"type": "login_required",
                                      "message": "need login"},
                    platform="bilibili"),
    ]

    async def fake_exec(*a, **k):
        return _FakeProc(out_lines, rc=0)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

    job = manager._active_job
    await asyncio.wait_for(job.task, timeout=10)

    assert job.platforms_state["bilibili"].status == "login_required"
    assert job.platforms_state["bilibili"].error_summary == "need login"


@pytest.mark.asyncio
async def test_rate_limited_does_not_touch_account_state(monkeypatch, tmp_path):
    """rate_limited / failed 不得误判为登录失效：账号状态保持 connected。"""
    _fake_accounts_profiles(monkeypatch, tmp_path)
    acc.profile_dir_for("douyin").mkdir(parents=True)
    acc._set_state("douyin", status="connected", verified=True)

    manager = SearchJobManager()
    resp = await manager.create_job(SearchJobRequestSchema(
        keyword="k", platforms=["douyin"], limit_per_platform=1))
    job_id = resp.job_id
    out_lines = [
        _event_line(job_id, "status", {"status": "running"},
                    platform="douyin"),
        _event_line(job_id, "error", {"type": "rate_limited",
                                      "message": "限流"},
                    platform="douyin"),
    ]

    async def fake_exec(*a, **k):
        return _FakeProc(out_lines, rc=0)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

    job = manager._active_job
    await asyncio.wait_for(job.task, timeout=10)

    assert job.platforms_state["douyin"].status == "rate_limited"
    st = acc._state_of("douyin")
    assert st["status"] == "connected", "rate_limited 不得降级账号状态"
    assert st["verified"] is True


@pytest.mark.asyncio
async def test_failed_does_not_touch_account_state(monkeypatch, tmp_path):
    """generic failed 同样不得修改账号状态。"""
    _fake_accounts_profiles(monkeypatch, tmp_path)
    acc.profile_dir_for("douyin").mkdir(parents=True)
    acc._set_state("douyin", status="connected", verified=True)

    manager = SearchJobManager()
    resp = await manager.create_job(SearchJobRequestSchema(
        keyword="k", platforms=["douyin"], limit_per_platform=1))
    job_id = resp.job_id
    out_lines = [
        _event_line(job_id, "error", {"type": "failed", "message": "boom"},
                    platform="douyin"),
    ]

    async def fake_exec(*a, **k):
        return _FakeProc(out_lines, rc=0)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

    job = manager._active_job
    await asyncio.wait_for(job.task, timeout=10)

    assert job.platforms_state["douyin"].status == "failed"
    st = acc._state_of("douyin")
    assert st["status"] == "connected"
    assert st["verified"] is True


@pytest.mark.asyncio
async def test_cleanup_marks_cancelled(monkeypatch):
    """cleanup() while a job is still running → cancelled (not failed)."""
    manager = SearchJobManager()
    resp = await manager.create_job(SearchJobRequestSchema(
        keyword="k", platforms=["bilibili", "zhihu"], limit_per_platform=1))
    job_id = resp.job_id
    out_lines = [
        _event_line(job_id, "status", {"status": "running"},
                    platform="bilibili"),
        _event_line(job_id, "status", {"status": "running"},
                    platform="zhihu"),
    ]

    class _HangingProc(_FakeProc):
        """Never EOFs, wait() blocks until killed — worker stays running."""

        def __init__(self):
            super().__init__(out_lines, rc=0)
            self.stdout = asyncio.StreamReader()
            for ln in out_lines:
                self.stdout.feed_data((ln + "\n").encode("utf-8"))
            self.stderr = asyncio.StreamReader()
            # NOTE: no feed_eof() — the reader tasks keep blocking

    async def fake_exec(*a, **k):
        return _HangingProc()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

    job = manager._active_job
    await asyncio.sleep(0.1)  # let the worker start

    await manager.cleanup()
    for p in ("bilibili", "zhihu"):
        assert job.platforms_state[p].status == "cancelled"
    assert job._compute_overall() == "cancelled"


# ── 平台私有错误码不能进 PlatformStatus ─────────────────────────────────
# 小红书 401 会带 safe_code="session_expired"，403 带 "restricted"，
# 返回 HTML 时带 "malformed_response"。这些是平台内部错误码，直接写进
# PlatformStatusInfo 会让 pydantic 的 Literal 校验失败 → GET /jobs 500，
# 且 overall 永远停在 running。

def test_platform_error_status_mapping():
    from aggregate_search.models import PLATFORM_STATUSES, coerce_platform_status, platform_error_status

    assert platform_error_status("session_expired") == "login_required"
    assert platform_error_status("restricted") == "rate_limited"
    assert platform_error_status("malformed_response") == "failed"
    assert platform_error_status("browser_unavailable") == "failed"
    assert platform_error_status("rate_limited") == "rate_limited"
    assert platform_error_status("某个未来才加的错误码") == "failed"
    # 合法状态不做二次映射；未知值必须有落点。
    for value in PLATFORM_STATUSES:
        assert coerce_platform_status(value) == value
    assert coerce_platform_status("session_expired") == "login_required"
    assert coerce_platform_status("") == "failed"


@pytest.mark.asyncio
async def test_unknown_error_code_does_not_break_job_response(monkeypatch):
    """worker 发 session_expired → 平台状态收敛为 login_required，响应可序列化。"""
    manager = SearchJobManager()
    resp = await manager.create_job(SearchJobRequestSchema(
        keyword="k", platforms=["xhs"], limit_per_platform=1))
    job_id = resp.job_id
    out_lines = [
        _event_line(job_id, "status", {"status": "running"}, platform="xhs"),
        _event_line(job_id, "error", {
            "type": "session_expired",
            "message": "小红书登录状态已失效",
        }, platform="xhs"),
    ]

    async def fake_exec(*a, **k):
        return _FakeProc(out_lines, rc=0)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

    job = manager._active_job
    await asyncio.wait_for(job.task, timeout=10)

    info = job.platforms_state["xhs"]
    assert info.status == "login_required"
    response = job.to_response()
    assert response.platforms["xhs"].status == "login_required"
    assert response.overall == "failed"

