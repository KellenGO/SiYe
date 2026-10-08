# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler/blob/main/tests/test_account_coordinator.py
# GitHub: https://github.com/NanmiCoder
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
# 使用本代码即表示您同意遵守上述原则和LICENSE中的所有条款。

"""Phase 4 测试：

- 4.1 单次浏览器上下文完成导入+验证（_launch_profile_context 调用次数=1、
      import 与 pong 同一 context、close/stop 各一次、失败也清理、
      超时后台继续并最终释放、shutdown 取消并释放、不泄漏 Cookie）；
- 4.2 OperationCoordinator 确定性协调（不同平台重叠、并发恰为 2、同平台
      串行、搜索/登录/账号操作互斥、异常不泄漏槽位）。

全部使用 fake context / fake gate / 调用计数，不连接真实浏览器。
"""

import asyncio
import json


import pytest

import api.services.accounts as acc
from tests.fixtures.browser import (
    FakeBrowserContext,
    FakePage,
    FakePlaywright,
)


COOKIE_FORMAT = "chrome-v1"

_XHS_COOKIES = [
    {"name": "web_session", "value": "fake-xhs-session",
     "domain": ".xiaohongshu.com", "path": "/"},
    {"name": "a1", "value": "fake-a1", "domain": ".xiaohongshu.com"},
]


# ── Fakes（仅测试数据/生命周期记录，不含生产判断） ──────────────────────

def _patch_launch(monkeypatch, launch_calls=None, ctx=None, pw=None):
    ctx = ctx or FakeBrowserContext()
    pw = pw or FakePlaywright()
    calls = launch_calls if launch_calls is not None else {"n": 0}

    async def fake_launch(platform):
        calls["n"] += 1
        return pw, ctx, "edge"

    monkeypatch.setattr("api.services.accounts._launch_profile_context", fake_launch)
    return calls, ctx, pw


def _run_sync(monkeypatch, cookies=None, **kwargs):
    return asyncio.run(acc.sync_platform_cookies(
        "xhs", cookies if cookies is not None else _XHS_COOKIES,
        cookie_format=kwargs.pop("cookie_format", COOKIE_FORMAT),
        extension_protocol_version=kwargs.pop("extension_protocol_version", 2),
        **kwargs))


# ═══════════════════════════════════════════════════════════════════════
# 4.1 单次浏览器上下文完成导入 + 验证
# ═══════════════════════════════════════════════════════════════════════

def test_sync_launches_exactly_one_context(monkeypatch):
    """一次 sync 的 _launch_profile_context 调用次数严格为 1。"""
    calls, ctx, pw = _patch_launch(monkeypatch)
    monkeypatch.setattr(
        "api.services.accounts._pong_with_profile",
        lambda p, c, metrics=None: asyncio.sleep(0, result=True))

    result = _run_sync(monkeypatch)
    assert result["success"] is True
    assert result["verified"] is True
    assert calls["n"] == 1  # 不再"导入一次 + 验证一次"两次启动
    assert ctx.close_count == 1
    assert pw.stop_count == 1


def test_sync_import_and_pong_share_same_context(monkeypatch):
    """import 与 pong 使用同一 fake context 对象。"""
    calls, ctx, pw = _patch_launch(monkeypatch)
    pong_ctxs = []

    async def fake_pong(platform, context, metrics=None):
        pong_ctxs.append(context)
        return True

    monkeypatch.setattr("api.services.accounts._pong_with_profile", fake_pong)
    result = _run_sync(monkeypatch)
    assert result["verified"] is True
    assert pong_ctxs == [ctx]  # pong 收到的正是导入 Cookie 的 context
    assert [c["name"] for c in ctx.added] == ["web_session", "a1"]


def test_sync_import_failure_cleans_up(monkeypatch):
    """import 失败也正确清理（context/playwright 各关一次，状态=failed）。"""
    calls, ctx, pw = _patch_launch(monkeypatch)

    async def boom(*args, **kwargs):
        raise RuntimeError("add_cookies failed")

    ctx.add_cookies = boom
    result = _run_sync(monkeypatch)
    assert result["success"] is False
    assert result["status"] == "failed"
    assert result["safe_error_code"] == "session_import_failed"
    assert ctx.close_count == 1
    assert pw.stop_count == 1
    assert acc._state_of("xhs")["status"] == "failed"


def test_sync_timeout_background_continues_and_releases(monkeypatch):
    """verify 超时返回 verifying；后台任务继续、最终更新状态并释放协调槽位。"""
    calls, ctx, pw = _patch_launch(monkeypatch)
    gate = asyncio.Event()

    async def gated_pong(platform, context, metrics=None):
        await gate.wait()
        return True

    monkeypatch.setattr("api.services.accounts._pong_with_profile", gated_pong)
    monkeypatch.setattr(acc, "SYNC_VERIFY_TIMEOUT_SECONDS", 0.05)

    async def scenario():
        # 模拟 router 在调用前获取账号操作槽位。
        assert await acc.operation_coordinator.acquire_account("xhs", "sync") == ""
        try:
            result = await acc.sync_platform_cookies(
                "xhs", _XHS_COOKIES, cookie_format=COOKIE_FORMAT,
                extension_protocol_version=2)
            assert result["status"] == "verifying"
            # 后台任务仍在跑：槽位不提前失效。
            assert "xhs" in acc.operation_coordinator._account_ops
        finally:
            gate.set()
        # 后台任务完成：状态更新 + done 回调释放槽位。
        for _ in range(50):
            if acc._state_of("xhs")["status"] == "connected" and \
                    "xhs" not in acc.operation_coordinator._account_ops:
                break
            await asyncio.sleep(0.02)
        assert acc._state_of("xhs")["status"] == "connected"
        assert "xhs" not in acc.operation_coordinator._account_ops

    asyncio.run(scenario())


def test_shutdown_cancels_task_and_releases(monkeypatch):
    """shutdown 取消后台任务并清理协调状态。"""
    calls, ctx, pw = _patch_launch(monkeypatch)
    gate = asyncio.Event()

    async def stuck_pong(platform, context, metrics=None):
        await gate.wait()
        return True

    monkeypatch.setattr("api.services.accounts._pong_with_profile", stuck_pong)
    monkeypatch.setattr(acc, "SYNC_VERIFY_TIMEOUT_SECONDS", 0.05)

    async def scenario():
        await acc.operation_coordinator.acquire_account("xhs", "sync")
        task = asyncio.create_task(acc.sync_platform_cookies(
            "xhs", _XHS_COOKIES, cookie_format=COOKIE_FORMAT,
            extension_protocol_version=2))
        await asyncio.sleep(0.15)
        await acc.cancel_verify_tasks()  # shutdown：取消任务 + clear 协调状态
        assert acc.operation_coordinator._account_ops == {}
        assert acc.operation_coordinator._exclusive is None
        gate.set()
        await asyncio.wait_for(task, timeout=5)

    asyncio.run(scenario())


def test_sync_response_never_leaks_cookie_values(monkeypatch):
    """同步响应不包含任何 Cookie 值。"""
    calls, ctx, pw = _patch_launch(monkeypatch)
    monkeypatch.setattr(
        "api.services.accounts._pong_with_profile",
        lambda p, c, metrics=None: asyncio.sleep(0, result=True))
    result = _run_sync(monkeypatch)
    dump = json.dumps(result, ensure_ascii=False)
    assert "fake-xhs-session" not in dump
    assert "fake-a1" not in dump


def test_router_sync_same_platform_returns_structured_conflict(monkeypatch):
    """同平台并发 sync：router 返回结构化 409（不消费 ticket、不启动任务）。"""
    import api.routers.search as router
    from api.routers.search import SyncCookiesRequest
    from starlette.requests import Request

    async def scenario():
        assert await acc.operation_coordinator.acquire_account("xhs", "sync") == ""
        try:
            scope = {
                "type": "http", "method": "POST", "path": "/x",
                "query_string": b"",
                "headers": [(b"origin", b"chrome-extension://abc")],
                "client": ("127.0.0.1", 1), "server": ("127.0.0.1", 8080),
                "scheme": "http",
            }
            request = Request(scope)
            req_body = SyncCookiesRequest(
                cookies=[{"name": "web_session", "value": "x",
                          "domain": ".xiaohongshu.com"}],
                cookie_format=COOKIE_FORMAT,
                extension_protocol_version=2)
            resp = await router.sync_account_cookies(
                "xhs", req_body, request, "dummy-ticket")
            assert resp.status_code == 409
            body = json.loads(resp.body)
            assert body.get("safe_error_code") == "verification_in_progress"
        finally:
            await acc.operation_coordinator.release_account("xhs")

    asyncio.run(scenario())


# ═══════════════════════════════════════════════════════════════════════
# 4.2 OperationCoordinator 确定性协调
# ═══════════════════════════════════════════════════════════════════════

class TestOperationCoordinator:
    @pytest.fixture
    def coord(self):
        return acc.OperationCoordinator(max_account_concurrency=2)

    @pytest.mark.asyncio
    async def test_two_different_platforms_overlap(self, coord):
        assert await coord.acquire_account("xhs", "sync") == ""

        assert await coord.acquire_account("douyin", "sync") == ""  # 可重叠

    @pytest.mark.asyncio
    async def test_max_concurrency_is_two(self, coord):
        await coord.acquire_account("xhs", "sync")
        await coord.acquire_account("douyin", "sync")
        assert await coord.acquire_account("bilibili", "sync") == "slots"  # 第 3 个无槽

    @pytest.mark.asyncio
    async def test_same_platform_serial(self, coord):
        await coord.acquire_account("xhs", "sync")
        assert await coord.acquire_account("xhs", "delete") == "platform"  # 同平台串行

    @pytest.mark.asyncio
    async def test_search_rejected_during_account_op(self, coord):
        await coord.acquire_account("xhs", "sync")
        assert await coord.acquire_exclusive("search") is False

    @pytest.mark.asyncio
    async def test_account_op_rejected_during_search(self, coord):
        assert await coord.acquire_exclusive("search") is True
        assert await coord.acquire_account("xhs", "sync") == "search"

    @pytest.mark.asyncio
    async def test_account_op_rejected_during_login(self, coord):
        assert await coord.acquire_exclusive("login") is True
        assert await coord.acquire_account("xhs", "sync") == "login"

    @pytest.mark.asyncio
    async def test_background_verify_blocks_search_until_released(self, coord):
        """后台 verify 未结束时 search 仍被拒绝；结束后恢复可操作。"""
        await coord.acquire_account("xhs", "sync")  # 后台 verify 持有槽位
        assert await coord.acquire_exclusive("search") is False
        await coord.release_account("xhs")  # 后台任务结束 → 槽位释放
        assert await coord.acquire_exclusive("search") is True

    @pytest.mark.asyncio
    async def test_exception_path_does_not_leak_slot(self, coord):
        """异常路径 finally 释放后，槽位不泄漏、可再次操作。"""
        assert await coord.acquire_account("xhs", "sync") == ""
        # 模拟异常路径：finally 中释放。
        await coord.release_account("xhs")
        assert await coord.acquire_exclusive("search") is True
        await coord.release_exclusive("search")
        assert await coord.acquire_account("xhs", "sync") == ""

    @pytest.mark.asyncio
    async def test_release_is_idempotent(self, coord):
        await coord.acquire_account("xhs", "sync")
        await coord.release_account("xhs")
        await coord.release_account("xhs")  # 幂等：不抛异常、不影响其他平台
        assert await coord.acquire_account("douyin", "verify") == ""

    @pytest.mark.asyncio
    async def test_clear_resets_all_state(self, coord):
        await coord.acquire_account("xhs", "sync")
        await coord.acquire_exclusive("login")
        await coord.clear()
        assert coord._account_ops == {}
        assert coord._exclusive is None
        assert await coord.acquire_account("xhs", "sync") == ""

@pytest.mark.asyncio
@pytest.mark.parametrize("error", [RuntimeError("worker stop failed"), asyncio.CancelledError()])
async def test_router_gate_releases_slot_when_worker_stop_fails(monkeypatch, error):
    from api.routers import search as router

    coord = acc.OperationCoordinator()
    monkeypatch.setattr(router, "_operation_coordinator", coord)
    monkeypatch.setattr(router.search_job_manager, "is_search_active", lambda: False)
    monkeypatch.setattr(acc, "is_verify_active", lambda _: False)

    async def stop(_):
        raise error

    monkeypatch.setattr(router.search_job_manager, "stop_platform_worker", stop)
    with pytest.raises(type(error)):
        async with router._account_operation_gate("xhs", "sync", "同步", "正在验证", skip_release_if_verifying=True):
            pytest.fail("worker 停止失败时不能开始账号操作")
    assert await coord.acquire_exclusive("search") is True


@pytest.mark.asyncio
async def test_router_gate_keeps_slot_for_background_verification(monkeypatch):
    from api.routers import search as router

    coord = acc.OperationCoordinator()
    monkeypatch.setattr(router, "_operation_coordinator", coord)
    monkeypatch.setattr(router.search_job_manager, "is_search_active", lambda: False)
    monkeypatch.setattr(router.search_job_manager, "stop_platform_worker", lambda _: asyncio.sleep(0))
    monkeypatch.setattr(acc, "is_verify_active", lambda _: True)
    async with router._account_operation_gate("xhs", "sync", "同步", "正在验证", skip_release_if_verifying=True) as blocked:
        assert blocked is None
    assert await coord.acquire_account("xhs", "verify") == "platform"
    await coord.release_account("xhs")
    assert await coord.acquire_exclusive("search") is True
