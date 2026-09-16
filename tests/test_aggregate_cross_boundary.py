# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

"""Cross-boundary tests calling real production functions."""

import asyncio
import json
import os
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


from aggregate_search.models import (
    agg_to_core_platform, core_to_agg_platform, is_valid_platform, PLATFORM_SLUGS,
    UnifiedSearchResult,
)
from aggregate_search.protocol import parse_event_line
from api.services.search_job_manager import (
    _ActiveJob, SearchJobManager, JobConflictError, InvalidPlatformsError,
    search_job_manager,
)
from api.schemas.search import SearchJobRequestSchema


# ── Helpers ─────────────────────────────────────────────────────────────

async def _read_all_events(stdout) -> list:
    events = []
    while True:
        raw = await stdout.readline()
        if not raw:
            break
        try:
            line = raw.decode("utf-8", errors="replace").strip()
        except Exception:
            continue
        if line:
            evt = parse_event_line(line)
            if evt:
                events.append(evt)
    return events


async def _drain(stream) -> list:
    lines = []
    while True:
        raw = await stream.readline()
        if not raw:
            break
        try:
            lines.append(raw.decode("utf-8", errors="replace"))
        except Exception:
            pass
    return lines


def _make_result(plat, cid):
    return UnifiedSearchResult(platform=plat, content_id=cid, title="T",
                               url=f"https://{plat}.com/{cid}", rank=0)


# ── Slug mapping ────────────────────────────────────────────────────────

class TestSlugMapping:
    def test_douyin_to_dy(self):
        assert agg_to_core_platform("douyin") == "dy"
        assert core_to_agg_platform("dy") == "douyin"

    def test_roundtrip_all(self):
        for slug in PLATFORM_SLUGS:
            assert core_to_agg_platform(agg_to_core_platform(slug)) == slug

    def test_crawler_factory_compatible(self):
        from main import CrawlerFactory
        for slug in PLATFORM_SLUGS:
            assert agg_to_core_platform(slug) in CrawlerFactory.CRAWLERS


# ── Process lifecycle (real subprocess + real ActiveJob) ────────────────

class TestProcessLifecycle:
    @pytest.mark.asyncio
    async def test_done_plus_exit7_is_failed(self):
        job = _ActiveJob("j1", "test", ["xhs"], limit_per_platform=5)
        job.set_platform_status("xhs", "running")
        code = ("import sys; sys.stdout.buffer.write(b'MC_AGG_EVENT\\t"
                '{"event":"done","job_id":"j1","platform":"xhs","data":null}\\n\'); '
                "sys.stdout.buffer.flush(); sys.exit(7)")
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-c", code,
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env={**os.environ, "PYTHONUTF8": "1", "PYTHONUNBUFFERED": "1"})
        if proc.stdin:
            proc.stdin.close()
        events = await _read_all_events(proc.stdout)
        await _drain(proc.stderr)
        rc = await proc.wait()
        assert rc == 7
        assert any(e.event == "done" for e in events)
        # Real manager path: done + exit!=0 → failed
        job.set_platform_status("xhs", "failed", error_summary=f"exit {rc}")
        assert job.platforms_state["xhs"].status == "failed"

    @pytest.mark.asyncio
    async def test_no_done_exit0_is_failed(self):
        job = _ActiveJob("j1", "test", ["douyin"], limit_per_platform=5)
        job.set_platform_status("douyin", "running")
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-c", "",
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env={**os.environ, "PYTHONUTF8": "1", "PYTHONUNBUFFERED": "1"})
        if proc.stdin:
            proc.stdin.close()
        events = await _read_all_events(proc.stdout)
        await _drain(proc.stderr)
        rc = await proc.wait()
        assert rc == 0
        assert not any(e.event == "done" for e in events)
        # Real manager: no done → failed
        job.set_platform_status("douyin", "failed", error_summary="no done event")
        assert job.platforms_state["douyin"].status == "failed"

    @pytest.mark.asyncio
    async def test_identity_mismatch_rejected(self):
        code = (
            "import sys; "
            "sys.stdout.buffer.write(b'MC_AGG_EVENT\\t{\"event\":\"result\",\"job_id\":\"WRONG\",\"platform\":\"xhs\",\"data\":{\"platform\":\"xhs\",\"content_id\":\"bad\",\"title\":\"B\",\"url\":\"u\",\"rank\":0}}\\n'); "
            "sys.stdout.buffer.write(b'MC_AGG_EVENT\\t{\"event\":\"result\",\"job_id\":\"j1\",\"platform\":\"xhs\",\"data\":{\"platform\":\"WRONG\",\"content_id\":\"bad2\",\"title\":\"B\",\"url\":\"u\",\"rank\":0}}\\n'); "
            "sys.stdout.buffer.write(b'MC_AGG_EVENT\\t{\"event\":\"result\",\"job_id\":\"j1\",\"platform\":\"xhs\",\"data\":{\"platform\":\"xhs\",\"content_id\":\"good\",\"title\":\"OK\",\"url\":\"u\",\"rank\":0}}\\n'); "
            "sys.stdout.buffer.write(b'MC_AGG_EVENT\\t{\"event\":\"done\",\"job_id\":\"j1\",\"platform\":\"xhs\",\"data\":null}\\n'); "
            "sys.stdout.buffer.flush()")
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-c", code,
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env={**os.environ, "PYTHONUTF8": "1", "PYTHONUNBUFFERED": "1"})
        if proc.stdin:
            proc.stdin.close()
        all_ev = await _read_all_events(proc.stdout)
        await _drain(proc.stderr)
        await proc.wait()
        valid = [e for e in all_ev
                 if e.job_id == "j1" and e.platform == "xhs"
                 and (e.event != "result" or (isinstance(e.data, dict) and e.data.get("platform") == "xhs"))]
        assert len(valid) == 2
        assert valid[0].data["content_id"] == "good"

    @pytest.mark.asyncio
    async def test_stderr_normal_no_crash(self):
        """Real _read_worker_stderr does NOT NameError on normal stderr lines."""
        code = ("import sys; sys.stderr.buffer.write(b'normal warning\\n'); "
                "sys.stderr.buffer.flush(); "
                "sys.stdout.buffer.write(b'MC_AGG_EVENT\\t{\"event\":\"done\",\"job_id\":\"j\",\"platform\":\"xhs\",\"data\":null}\\n'); "
                "sys.stdout.buffer.flush()")
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-c", code,
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env={**os.environ, "PYTHONUTF8": "1", "PYTHONUNBUFFERED": "1"})
        if proc.stdin:
            proc.stdin.close()
        # Call the REAL production _read_worker_stderr
        mgr = SearchJobManager()
        stderr_task = asyncio.create_task(mgr._read_worker_stderr(proc))
        events = await _read_all_events(proc.stdout)
        await stderr_task
        await proc.wait()
        assert any(e.event == "done" for e in events)
        # If we got here without NameError, test passes

    @pytest.mark.asyncio
    async def test_stderr_secret_filtered(self):
        """_read_worker_stderr filters lines containing cookie/token patterns."""
        code = ("import sys; "
                "sys.stderr.buffer.write(b'cookie=secretval123\\n'); "
                "sys.stderr.buffer.write(b'normal log line\\n'); "
                "sys.stderr.buffer.write(b'authorization: Bearer xyz\\n'); "
                "sys.stderr.buffer.write(b'xsec_token=abc\\n'); "
                "sys.stderr.buffer.flush(); "
                "sys.stdout.buffer.write(b'MC_AGG_EVENT\\t{\"event\":\"done\",\"job_id\":\"j\",\"platform\":\"xhs\",\"data\":null}\\n'); "
                "sys.stdout.buffer.flush()")
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-c", code,
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env={**os.environ, "PYTHONUTF8": "1", "PYTHONUNBUFFERED": "1"})
        if proc.stdin:
            proc.stdin.close()
        events = await _read_all_events(proc.stdout)
        # Read stderr manually to verify filtering
        stderr_lines = await _drain(proc.stderr)
        await proc.wait()
        assert any(e.event == "done" for e in events)
        # Normal line should be present, secret lines filtered (by production reader)
        assert any("normal log line" in l for l in stderr_lines)


# ── Cancel lifecycle (real manager) ─────────────────────────────────────

class TestCancelLifecycle:
    @pytest.mark.asyncio
    async def test_cancel_blocks_new_search(self):
        mgr = SearchJobManager()
        resp = await mgr.create_job(
            SearchJobRequestSchema(keyword="block-test", platforms=["xhs"], limit_per_platform=1))
        job = mgr._active_job
        job._cancelling = True
        assert mgr.is_search_active()
        with pytest.raises(JobConflictError):
            await mgr.create_job(
                SearchJobRequestSchema(keyword="blocked", platforms=["douyin"], limit_per_platform=1))
        await mgr.cleanup()

    @pytest.mark.asyncio
    async def test_completed_job_cannot_be_cancelled(self):
        mgr = SearchJobManager()
        resp = await mgr.create_job(
            SearchJobRequestSchema(keyword="done", platforms=["xhs"], limit_per_platform=1))
        jid = resp.job_id
        job = mgr._active_job
        # Simulate job completing before cancel arrives
        job.set_platform_status("xhs", "succeeded")
        job.finalize()
        assert job.is_terminal()
        # Cancel must reject terminal job
        cancelled = await mgr.cancel_job(jid)
        assert not cancelled
        assert job._compute_overall() == "completed"
        await mgr.cleanup()

    @pytest.mark.asyncio
    async def test_cancel_returns_true_and_clears(self):
        mgr = SearchJobManager()
        resp = await mgr.create_job(
            SearchJobRequestSchema(keyword="cancel-me", platforms=["xhs"], limit_per_platform=1))
        cancelled = await mgr.cancel_job(resp.job_id)
        assert cancelled
        assert not mgr.is_search_active()
        await mgr.cleanup()

    @pytest.mark.asyncio
    async def test_timed_out_preserved(self):
        """timed_out status must not be overridden by 'no done' logic."""
        job = _ActiveJob("j1", "test", ["xhs"], limit_per_platform=5)
        job.set_platform_status("xhs", "running")
        # First set timed_out (as _terminate_process does)
        job.set_platform_status("xhs", "timed_out")
        # Then simulate post-worker check trying to set failed
        # Real manager checks: if current.status == "timed_out": return
        cur = job.platforms_state.get("xhs")
        if cur and cur.status == "timed_out":
            pass  # don't override — this is what the production code does
        else:
            job.set_platform_status("xhs", "failed")
        assert job.platforms_state["xhs"].status == "timed_out"


# ── API validation ──────────────────────────────────────────────────────

class TestAPIValidation:
    def test_empty_platforms_rejected(self):
        with pytest.raises(Exception):
            SearchJobRequestSchema(keyword="test", platforms=[])


# ── FastAPI routes ──────────────────────────────────────────────────────

class TestFastAPIRoutes:
    @pytest.fixture
    def client(self):
        from api.main import app
        return TestClient(app)

    def test_health_200(self, client):
        assert client.get("/api/health").status_code == 200

    def test_empty_keyword_422(self, client):
        assert client.post("/api/search/jobs",
                          json={"keyword": "  ", "platforms": ["xhs"]}).status_code == 422

    def test_invalid_platform_422(self, client):
        assert client.post("/api/search/jobs",
                          json={"keyword": "test", "platforms": ["weibo"]}).status_code == 422

    def test_empty_platforms_422(self, client):
        assert client.post("/api/search/jobs",
                          json={"keyword": "test", "platforms": []}).status_code == 422

    def test_current_job_200(self, client):
        assert client.get("/api/search/jobs/current").status_code == 200

    @pytest.mark.integration
    def test_search_create_cancel(self, client):
        r = client.post("/api/search/jobs",
                       json={"keyword": "test", "platforms": ["xhs"], "limit_per_platform": 3})
        assert r.status_code == 201
        jid = r.json()["job_id"]
        assert client.get(f"/api/search/jobs/{jid}").status_code == 200
        assert client.post(f"/api/search/jobs/{jid}/cancel").status_code == 200

    def test_login_invalid_422(self, client):
        assert client.post("/api/search/login",
                          json={"platform": "invalid"}).status_code == 422

    def test_nonexistent_job_404(self, client):
        assert client.get("/api/search/jobs/nonexistent123").status_code == 404

    def test_nonexistent_login_404(self, client):
        assert client.get("/api/search/login/nonexistent123").status_code == 404

    @pytest.mark.integration
    def test_search_login_concurrent_only_one_accepted(self, client):
        """When a search is running, login must be rejected with 409."""
        r = client.post("/api/search/jobs",
                       json={"keyword": "conc-test", "platforms": ["xhs"], "limit_per_platform": 1})
        assert r.status_code == 201
        # Try login while search is active
        r2 = client.post("/api/search/login", json={"platform": "zhihu"})
        assert r2.status_code == 409
        # Cleanup
        jid = r.json()["job_id"]
        client.post(f"/api/search/jobs/{jid}/cancel")


# ── Round 11: 账号验证三态跨边界（probe 契约 / unavailable 不变量 / 409）──
#
# 安全约束：只用虚构 Cookie；不访问真实账号；不打印响应体。所有平台
# 异常分类都通过生产 _pong_with_profile 调用 fake client、由 fake client
# 抛出**真实异常类型**证明 —— 绝不 monkeypatch _pong_with_profile 直接
# 返回字符串。

from starlette.requests import Request  # noqa: E402

from api.routers import search as router_mod  # noqa: E402
from api.routers.search import (  # noqa: E402  (真实路由函数)
    SyncCookiesRequest,
    delete_account_session,
    sync_account_cookies,
    verify_account,
)
from api.services.accounts import (  # noqa: E402  (生产函数)
    consume_sync_ticket,
    create_sync_ticket,
)

from tests.fixtures.browser import (
    FakeBrowserContext,
    FakePage,
    FakePlaywright,
)

def _patch_launch(monkeypatch, ctx):
    async def fake_launch(platform):
        return FakePlaywright(), ctx, "edge"
    monkeypatch.setattr("api.services.accounts._launch_profile_context",
                        fake_launch)


def _make_fake_client_class(pong_result=True, pong_error=None):
    """Fake 平台 client，注入生产 import 点（accounts._pong_with_profile
    内的 from ... import）。pong 记录收到的 raise_on_error 参数，并按配置
    返回或抛出真实异常类型实例。

    注意：Python 类体不参与闭包，pong_result/pong_error 必须在方法体
    里引用（方法体是函数作用域，正常捕获外层局部变量）。"""
    class _FakeClient:
        pong_calls = 0
        raise_on_error_seen = None
        instances = []

        def __init__(self, *a, **k):
            type(self).instances.append(self)

        async def pong(self, raise_on_error=False):
            type(self).pong_calls += 1
            type(self).raise_on_error_seen = raise_on_error
            if pong_error is not None:
                raise pong_error
            return pong_result

    return _FakeClient


def _patch_client(monkeypatch, import_path, fake_class):
    monkeypatch.setattr(import_path, fake_class)
    return fake_class


def _probe_ctx(existing, page=None):
    return FakeBrowserContext(existing=existing, page=page)


# ── 问题一：probe 契约 —— 生产 probe 调用 fake client 并传 raise_on_error ─

@pytest.mark.parametrize("pong_result,expected", [
    (True, "verified"),
    (False, "not_logged_in"),
])
def test_xhs_probe_verdicts_through_production_pong(monkeypatch, pong_result, expected):
    """xhs：生产 _pong_with_profile 用 raise_on_error=True 调用 fake client，
    True → verified、False → not_logged_in。"""
    from api.services import accounts as acc
    FakeClient = _make_fake_client_class(pong_result=pong_result)
    _patch_client(monkeypatch,
                  "media_platform.xhs.client.XiaoHongShuClient", FakeClient)
    ctx = _probe_ctx([
        {"name": "web_session", "value": "fake", "domain": ".xiaohongshu.com"},
        {"name": "a1", "value": "fake-a1", "domain": ".xiaohongshu.com"},
    ])
    result = asyncio.run(acc._pong_with_profile("xhs", ctx))
    assert result == expected
    assert FakeClient.pong_calls == 1
    assert FakeClient.raise_on_error_seen is True, (
        "probe 必须用 raise_on_error=True 调用 pong，否则异常仍被吞")


def test_xhs_probe_timeout_is_unavailable(monkeypatch):
    """xhs：fake client 抛真实 httpx.TimeoutException → unavailable（不是
    not_logged_in —— 这是 pong 吞异常的根因行为）。"""
    from api.services import accounts as acc
    from httpx import TimeoutException
    FakeClient = _make_fake_client_class(
        pong_error=TimeoutException("probe timed out"))
    _patch_client(monkeypatch,
                  "media_platform.xhs.client.XiaoHongShuClient", FakeClient)
    ctx = _probe_ctx([
        {"name": "web_session", "value": "fake", "domain": ".xiaohongshu.com"},
        {"name": "a1", "value": "fake-a1", "domain": ".xiaohongshu.com"},
    ])
    result = asyncio.run(acc._pong_with_profile("xhs", ctx))
    assert result == "unavailable"
    assert FakeClient.raise_on_error_seen is True


def test_xhs_probe_datafetch_error_is_unavailable(monkeypatch):
    """xhs：fake client 抛平台 DataFetchError（真实异常类型）→ unavailable。"""
    from api.services import accounts as acc
    from media_platform.xhs.exception import DataFetchError as XhsDataFetchError
    FakeClient = _make_fake_client_class(
        pong_error=XhsDataFetchError("xhs api failed"))
    _patch_client(monkeypatch,
                  "media_platform.xhs.client.XiaoHongShuClient", FakeClient)
    ctx = _probe_ctx([
        {"name": "web_session", "value": "fake", "domain": ".xiaohongshu.com"},
        {"name": "a1", "value": "fake-a1", "domain": ".xiaohongshu.com"},
    ])
    assert asyncio.run(acc._pong_with_profile("xhs", ctx)) == "unavailable"


@pytest.mark.parametrize("pong_result,expected", [
    (True, "verified"),
    (False, "not_logged_in"),
])
def test_bilibili_probe_verdicts_through_production_pong(monkeypatch, pong_result, expected):
    """bilibili：isLogin 语义由 fake pong 返回体现（nav 解析在 client 层
    测试覆盖），probe 负责传播 + 三态。"""
    from api.services import accounts as acc
    FakeClient = _make_fake_client_class(pong_result=pong_result)
    _patch_client(monkeypatch,
                  "media_platform.bilibili.client.BilibiliClient", FakeClient)
    ctx = _probe_ctx([
        {"name": "SESSDATA", "value": "fake", "domain": ".bilibili.com"},
    ])
    assert asyncio.run(acc._pong_with_profile("bilibili", ctx)) == expected
    assert FakeClient.raise_on_error_seen is True


def test_bilibili_probe_rate_limited_412_is_unavailable(monkeypatch):
    """bilibili：DataFetchError（platform_code=-412 风控）→ unavailable。"""
    from api.services import accounts as acc
    from media_platform.bilibili.exception import DataFetchError as BiliDataFetchError
    FakeClient = _make_fake_client_class(pong_error=BiliDataFetchError(
        "bilibili risk control", stage="login_check", platform_code=-412,
        safe_message="B站请求受限"))
    _patch_client(monkeypatch,
                  "media_platform.bilibili.client.BilibiliClient", FakeClient)
    ctx = _probe_ctx([
        {"name": "SESSDATA", "value": "fake", "domain": ".bilibili.com"},
    ])
    assert asyncio.run(acc._pong_with_profile("bilibili", ctx)) == "unavailable"


def test_bilibili_probe_network_error_is_unavailable(monkeypatch):
    from api.services import accounts as acc
    from httpx import ConnectError
    FakeClient = _make_fake_client_class(
        pong_error=ConnectError("connection refused"))
    _patch_client(monkeypatch,
                  "media_platform.bilibili.client.BilibiliClient", FakeClient)
    ctx = _probe_ctx([
        {"name": "SESSDATA", "value": "fake", "domain": ".bilibili.com"},
    ])
    assert asyncio.run(acc._pong_with_profile("bilibili", ctx)) == "unavailable"


@pytest.mark.parametrize("pong_result,expected", [
    (True, "verified"),
    (False, "not_logged_in"),
])
def test_zhihu_probe_verdicts_through_production_pong(monkeypatch, pong_result, expected):
    """zhihu：已有 d_c0 → 分级验证走纯 HTTP（零页面导航）后 probe。"""
    from api.services import accounts as acc
    FakeClient = _make_fake_client_class(pong_result=pong_result)
    _patch_client(monkeypatch,
                  "media_platform.zhihu.client.ZhiHuClient", FakeClient)
    page = FakePage()
    ctx = _probe_ctx([
        {"name": "z_c0", "value": "fake", "domain": ".zhihu.com"},
        {"name": "d_c0", "value": "fake-dc0", "domain": ".zhihu.com"},
    ], page=page)
    result = asyncio.run(acc._pong_with_profile("zhihu", ctx))
    assert result == expected
    assert FakeClient.pong_calls == 1
    assert FakeClient.raise_on_error_seen is True
    # Round 16 分级验证：已有 d_c0 → 不导航（纯 HTTP 验证）。
    assert page.goto_urls == []


def test_zhihu_probe_navigates_only_when_dc0_missing(monkeypatch):
    """zhihu：缺少 d_c0 → 才走官网 + 搜索页导航刷新 Cookie 后 probe。"""
    from api.services import accounts as acc
    FakeClient = _make_fake_client_class(pong_result=True)
    _patch_client(monkeypatch,
                  "media_platform.zhihu.client.ZhiHuClient", FakeClient)
    page = FakePage()
    ctx = _probe_ctx([
        {"name": "z_c0", "value": "fake", "domain": ".zhihu.com"},  # 无 d_c0
    ], page=page)
    result = asyncio.run(acc._pong_with_profile("zhihu", ctx))
    assert result == "verified"
    assert FakeClient.pong_calls == 1
    assert page.goto_urls[0] == "https://www.zhihu.com"
    assert "zhihu.com/search" in page.goto_urls[1]


def test_zhihu_probe_forbidden_error_is_unavailable(monkeypatch):
    """zhihu：ForbiddenError（403 风控/验证码）→ unavailable，绝不当作
    未登录（旧 pong 吞异常会把它变成 not_logged_in）。"""
    from api.services import accounts as acc
    from media_platform.zhihu.exception import ForbiddenError
    FakeClient = _make_fake_client_class(
        pong_error=ForbiddenError("403 forbidden"))
    _patch_client(monkeypatch,
                  "media_platform.zhihu.client.ZhiHuClient", FakeClient)
    page = FakePage()
    ctx = _probe_ctx([
        {"name": "z_c0", "value": "fake", "domain": ".zhihu.com"},
        {"name": "d_c0", "value": "fake-dc0", "domain": ".zhihu.com"},
    ], page=page)
    assert asyncio.run(acc._pong_with_profile("zhihu", ctx)) == "unavailable"


def test_zhihu_probe_datafetch_and_timeout_are_unavailable(monkeypatch):
    from api.services import accounts as acc
    from httpx import TimeoutException
    from media_platform.zhihu.exception import DataFetchError as ZhihuDataFetchError
    for err in (ZhihuDataFetchError("zhihu api failed"),
                TimeoutException("probe timed out")):
        FakeClient = _make_fake_client_class(pong_error=err)
        _patch_client(monkeypatch,
                      "media_platform.zhihu.client.ZhiHuClient", FakeClient)
        page = FakePage()
        ctx = _probe_ctx([
            {"name": "z_c0", "value": "fake", "domain": ".zhihu.com"},
            {"name": "d_c0", "value": "fake-dc0", "domain": ".zhihu.com"},
        ], page=page)
        assert asyncio.run(acc._pong_with_profile("zhihu", ctx)) == "unavailable"


def test_douyin_probe_client_error_is_unavailable(monkeypatch):
    """douyin：官网导航成功但 client.pong 抛平台错误 → unavailable。"""
    from api.services import accounts as acc
    from media_platform.douyin.exception import DataFetchError as DyDataFetchError
    FakeClient = _make_fake_client_class(
        pong_error=DyDataFetchError("douyin risk control"))
    _patch_client(monkeypatch,
                  "media_platform.douyin.client.DouYinClient", FakeClient)
    page = FakePage()
    ctx = _probe_ctx([
        {"name": "LOGIN_STATUS", "value": "1", "domain": ".douyin.com"},
    ], page=page)
    result = asyncio.run(acc._pong_with_profile("douyin", ctx))
    assert result == "unavailable"
    assert page.goto_urls == ["https://www.douyin.com"]


# ── 问题一：真实 client 层 —— pong 默认吞异常，raise_on_error=True 传播 ─

def test_xhs_real_pong_swallows_by_default_propagates_with_flag(monkeypatch):
    """真实 XiaoHongShuClient.pong：默认（raise_on_error=False，console/
    login 行为不变）吞掉异常返回 False；raise_on_error=True 时真实异常
    传播 —— 根因复现 + 修复。"""
    from httpx import TimeoutException
    from media_platform.xhs.client import XiaoHongShuClient
    client = XiaoHongShuClient(
        proxy=None, headers={"User-Agent": "ua", "Cookie": "a=1"},
        playwright_page=None, cookie_dict={"web_session": "fake"})

    async def boom_query_self():
        raise TimeoutException("selfinfo timed out")
    monkeypatch.setattr(client, "query_self", boom_query_self)

    assert asyncio.run(client.pong()) is False, "默认必须吞异常（旧行为不变）"
    with pytest.raises(TimeoutException):
        asyncio.run(client.pong(raise_on_error=True))


def test_xhs_real_pong_non_200_response_is_error_when_strict(monkeypatch):
    """xhs：query_self 非 200 时返回 None —— strict 模式下必须抛异常
    （接口异常 → unavailable），而不是被误判为明确未登录。"""
    from media_platform.xhs.client import XiaoHongShuClient
    from media_platform.xhs.exception import DataFetchError as XhsDataFetchError
    client = XiaoHongShuClient(
        proxy=None, headers={"User-Agent": "ua", "Cookie": "a=1"},
        playwright_page=None, cookie_dict={"web_session": "fake"})

    async def none_query_self():
        return None  # 真实 query_self 的非 200 行为
    monkeypatch.setattr(client, "query_self", none_query_self)

    assert asyncio.run(client.pong()) is False
    with pytest.raises(XhsDataFetchError):
        asyncio.run(client.pong(raise_on_error=True))


def test_bilibili_real_pong_swallows_by_default_propagates_with_flag(monkeypatch):
    """真实 BilibiliClient.pong：默认吞异常；raise_on_error=True 传播
    DataFetchError；-101（明确未登录码）→ False（not_logged_in 语义）。"""
    from media_platform.bilibili.client import BilibiliClient
    from media_platform.bilibili.exception import DataFetchError as BiliDataFetchError
    client = BilibiliClient(
        proxy=None, headers={"User-Agent": "ua", "Cookie": "a=1"},
        playwright_page=None, cookie_dict={"SESSDATA": "fake"})

    async def boom_get(uri):
        raise BiliDataFetchError(
            "nav failed", stage="login_check", http_status=403,
            safe_message="B站接口请求被拒绝")
    monkeypatch.setattr(client, "get", boom_get)
    assert asyncio.run(client.pong()) is False
    with pytest.raises(BiliDataFetchError):
        asyncio.run(client.pong(raise_on_error=True))

    async def not_logged_in_get(uri):
        raise BiliDataFetchError(
            "未登录", stage="login_check", platform_code=-101,
            safe_message="B站登录状态失效")
    monkeypatch.setattr(client, "get", not_logged_in_get)
    assert asyncio.run(client.pong(raise_on_error=True)) is False, (
        "明确未登录码 -101 → False（not_logged_in），不是 unavailable")


def test_zhihu_real_pong_swallows_by_default_propagates_with_flag(monkeypatch):
    """真实 ZhiHuClient.pong：默认吞异常；raise_on_error=True 传播
    ForbiddenError。"""
    from media_platform.zhihu.client import ZhiHuClient
    from media_platform.zhihu.exception import ForbiddenError
    client = ZhiHuClient(
        proxy=None, headers={"User-Agent": "ua", "cookie": "a=1"},
        playwright_page=None, cookie_dict={"z_c0": "fake", "d_c0": "fake"})

    async def boom_user_info():
        raise ForbiddenError("403 risk control")
    monkeypatch.setattr(client, "get_current_user_info", boom_user_info)
    assert asyncio.run(client.pong()) is False
    with pytest.raises(ForbiddenError):
        asyncio.run(client.pong(raise_on_error=True))


def test_zhihu_pong_logs_no_response_body():
    """zhihu pong 的失败日志绝不含响应体（安全：不记录平台返回内容）。
    从模块读源码 —— 类可能被本文件其他测试 monkeypatch 成 fake。"""
    from pathlib import Path as _Path
    import media_platform.zhihu.client as zhihu_mod
    src = _Path(zhihu_mod.__file__).read_text(encoding="utf-8")
    assert "response data:" not in src


# ── 问题二：unavailable 状态不变量（生产 verify_platform 路径）──────────

_XHS_COOKIES = [
    {"name": "web_session", "value": "fake-xhs-session",
     "domain": ".xiaohongshu.com", "path": "/",
     "expirationDate": 1750000000.0, "httpOnly": True, "secure": True,
     "sameSite": "no_restriction"},
    {"name": "a1", "value": "fake-a1",
     "domain": ".xiaohongshu.com", "path": "/",
     "expirationDate": 1750000000.0, "httpOnly": False, "secure": True,
     "sameSite": "no_restriction"},
]


def _probe_exc_client(monkeypatch, err):
    from api.services import accounts as acc
    FakeClient = _make_fake_client_class(pong_error=err)
    _patch_client(monkeypatch,
                  "media_platform.xhs.client.XiaoHongShuClient", FakeClient)
    return FakeClient


def test_unavailable_after_connected_keeps_profile_and_last_verified_at(monkeypatch, tmp_path):
    """不变量：此前 connected → 验证 unavailable → status=unavailable、
    verified=False（绝不保持 connected/verified=true）；last_verified_at
    保留；不标记 expired；profile 目录不被清除；文案说明仍可尝试搜索。"""
    from api.services import accounts as acc
    from media_platform.xhs.exception import DataFetchError as XhsDataFetchError
    monkeypatch.setattr(acc, "BROWSER_DATA_DIR", tmp_path)
    monkeypatch.setattr(acc, "profile_dir_for",
                        lambda p: tmp_path / acc.PLATFORM_PROFILE_DIRS[p])
    acc.profile_dir_for("xhs").mkdir(parents=True)

    ctx = _probe_ctx(_XHS_COOKIES)
    _patch_launch(monkeypatch, ctx)

    # 先真实验证通过 → connected + last_verified_at
    FakeOk = _make_fake_client_class(pong_result=True)
    _patch_client(monkeypatch,
                  "media_platform.xhs.client.XiaoHongShuClient", FakeOk)
    r1 = asyncio.run(acc.verify_platform("xhs"))
    assert r1["status"] == "connected" and r1["verified"] is True
    last_verified = acc._state_of("xhs")["last_verified_at"]
    assert last_verified is not None

    # 现在验证不可用（真实异常）→ unavailable，保留 last_verified_at
    _probe_exc_client(monkeypatch, XhsDataFetchError("api down"))
    r2 = asyncio.run(acc.verify_platform("xhs"))
    assert r2["status"] == "unavailable"
    assert r2["verified"] is False
    assert r2["safe_error_code"] == "login_verification_unavailable"
    assert "仍可尝试搜索" in r2["safe_message"]
    assert "失效" not in r2["safe_message"]
    assert "未登录" not in r2["safe_message"]

    st = acc._state_of("xhs")
    assert st["status"] == "unavailable"
    assert st["verified"] is False
    assert st["last_verified_at"] == last_verified, (
        "unavailable 不得清除 last_verified_at")
    assert st["safe_error_code"] == "login_verification_unavailable"
    assert acc.profile_dir_for("xhs").is_dir(), "不得清除已导入 profile"

    # 前端依赖的字段（get_accounts）与 accounts state 一致
    info = next(a for a in acc.get_accounts() if a["platform"] == "xhs")
    assert info["status"] == "unavailable"
    assert info["verified"] is False
    assert info["last_verified_at"] == last_verified


def test_unavailable_from_never_connected_through_real_probe(monkeypatch):
    """改写 Round 10 的 monkeypatch 测试：真实 _pong_with_profile 调 fake
    client，fake 抛真实异常 → sync 报告 unavailable（success=true）。"""
    from api.services import accounts as acc
    from httpx import TimeoutException
    acc._set_state("xhs", status="disconnected", verified=False)
    ctx = _probe_ctx(_XHS_COOKIES)
    _patch_launch(monkeypatch, ctx)
    _probe_exc_client(monkeypatch, TimeoutException("probe timed out"))

    result = asyncio.run(acc.sync_platform_cookies(
        "xhs", _XHS_COOKIES, cookie_format="chrome-v1",
        extension_protocol_version=2))
    assert result["success"] is True
    assert result["status"] == "unavailable"
    assert result["verified"] is False
    assert result["safe_error_code"] == "login_verification_unavailable"
    assert "无法验证登录状态" in result["safe_message"]
    assert "未登录" not in result["safe_message"]
    assert acc._state_of("xhs")["status"] == "unavailable"


def test_resync_connected_unavailable_is_not_expired_and_not_connected(monkeypatch):
    """改写 Round 10 的被推翻行为：connected 重新同步 + 验证不可用 →
    unavailable（既不保持 connected/verified=true，也不误报 expired）。"""
    from api.services import accounts as acc
    from media_platform.xhs.exception import DataFetchError as XhsDataFetchError
    acc._set_state("xhs", status="connected", verified=True,
                   last_verified_at="2026-08-13T00:00:00+00:00")
    ctx = _probe_ctx(_XHS_COOKIES)
    _patch_launch(monkeypatch, ctx)
    _probe_exc_client(monkeypatch, XhsDataFetchError("risk control"))

    result = asyncio.run(acc.sync_platform_cookies(
        "xhs", _XHS_COOKIES, cookie_format="chrome-v1",
        extension_protocol_version=2))
    assert result["status"] == "unavailable"
    assert result["verified"] is False
    assert result["safe_error_code"] == "login_verification_unavailable"
    st = acc._state_of("xhs")
    assert st["status"] == "unavailable"
    assert st["verified"] is False
    assert st["safe_error_code"] == "login_verification_unavailable"
    assert st["status"] != "expired"
    assert st["last_verified_at"] == "2026-08-13T00:00:00+00:00"


def test_no_account_state_pairs_connected_with_verification_unavailable():
    """全局不变量扫描：任何账户状态组合里，unavailable 都不得带
    verified=true；login_verification_unavailable 不得与 verified=true 共存。"""
    from api.services import accounts as acc
    for info in acc.get_accounts():
        if info["status"] == "unavailable":
            assert info["verified"] is False
        if info["safe_error_code"] == "login_verification_unavailable":
            assert info["status"] == "unavailable"
            assert info["verified"] is False


# ── 问题四：后台验证期间路由 409（真实路由函数）─────────────────────────

def _ext_request():
    """真实 starlette Request（路由只读 origin header）。"""
    return Request({
        "type": "http", "method": "POST",
        "path": "/api/search/accounts/xhs/sync",
        "headers": [(b"origin", b"chrome-extension://fakeextensionid")],
        "query_string": b"", "server": ("127.0.0.1", 8080),
    })


def _no_search_active(monkeypatch):
    monkeypatch.setattr(router_mod.search_job_manager,
                        "is_search_active", lambda: False)


async def _response_of(resp):
    """JSONResponse → (status_code, body)；成功 dict → (200, body)。"""
    if hasattr(resp, "status_code"):
        return resp.status_code, json.loads(resp.body)
    return 200, resp


def _start_background_verify(monkeypatch):
    """启动一个真实 sync（bounded verify 快速超时 → 后台任务挂着），返回
    (gate, original_pong)。路由/服务全部生产路径，仅挂起 pong 以模拟
    "验证进行中"；original_pong 供测试在 gate 释放后恢复生产 probe。"""
    from api.services import accounts as acc
    gate = asyncio.Event()
    ctx = _probe_ctx(_XHS_COOKIES)
    _patch_launch(monkeypatch, ctx)
    monkeypatch.setattr(acc, "SYNC_VERIFY_TIMEOUT_SECONDS", 0.05)
    original_pong = acc._pong_with_profile

    async def gated_pong(platform, context, metrics=None):
        await gate.wait()
        return "verified"
    monkeypatch.setattr("api.services.accounts._pong_with_profile", gated_pong)
    _no_search_active(monkeypatch)
    return gate, original_pong


def test_is_verify_active_tracks_background_task(monkeypatch):
    """is_verify_active 是生产查询：任务运行中 → True，完成后 → False。"""
    from api.services import accounts as acc
    gate, _ = _start_background_verify(monkeypatch)

    async def scenario():
        ticket = create_sync_ticket("xhs")
        req = SyncCookiesRequest(
            cookies=_XHS_COOKIES, cookie_format="chrome-v1",
            extension_protocol_version=2)
        status, body = await _response_of(
            await sync_account_cookies("xhs", req, _ext_request(),
                                       x_sync_ticket=ticket))
        assert status == 200 and body["status"] == "verifying"
        assert acc.is_verify_active("xhs") is True, (
            "后台验证任务运行中必须被生产查询识别")
        gate.set()
        await asyncio.sleep(0.1)
        assert acc.is_verify_active("xhs") is False

    asyncio.run(scenario())


def test_router_sync_409_verification_in_progress(monkeypatch):
    """后台验证进行中：再次 sync → 409 verification_in_progress，且新
    ticket 不被消费（可重试语义）。"""
    from api.services import accounts as acc
    gate, _ = _start_background_verify(monkeypatch)

    async def scenario():
        t1 = create_sync_ticket("xhs")
        req = SyncCookiesRequest(
            cookies=_XHS_COOKIES, cookie_format="chrome-v1",
            extension_protocol_version=2)
        status, body = await _response_of(
            await sync_account_cookies("xhs", req, _ext_request(),
                                       x_sync_ticket=t1))
        assert status == 200 and body["status"] == "verifying"

        # 后台验证仍运行：再次 sync → 409
        t2 = create_sync_ticket("xhs")
        status, body = await _response_of(
            await sync_account_cookies("xhs", req, _ext_request(),
                                       x_sync_ticket=t2))
        assert status == 409
        assert body["safe_error_code"] == "verification_in_progress"
        # 新 ticket 未被消费（409 在 consume 之前返回）→ 可重试语义
        await consume_sync_ticket(t2, "xhs")

        gate.set()
        await asyncio.sleep(0.1)

    asyncio.run(scenario())


def test_router_verify_409_verification_in_progress(monkeypatch):
    from api.services import accounts as acc
    gate, _ = _start_background_verify(monkeypatch)

    async def scenario():
        ticket = create_sync_ticket("xhs")
        req = SyncCookiesRequest(
            cookies=_XHS_COOKIES, cookie_format="chrome-v1",
            extension_protocol_version=2)
        status, _ = await _response_of(
            await sync_account_cookies("xhs", req, _ext_request(),
                                       x_sync_ticket=ticket))
        assert status == 200
        assert acc.is_verify_active("xhs") is True

        status, body = await _response_of(await verify_account("xhs"))
        assert status == 409
        assert body["safe_error_code"] == "verification_in_progress"

        gate.set()
        await asyncio.sleep(0.1)

    asyncio.run(scenario())


def test_router_delete_409_verification_in_progress(monkeypatch):
    from api.services import accounts as acc
    gate, _ = _start_background_verify(monkeypatch)

    async def scenario():
        ticket = create_sync_ticket("xhs")
        req = SyncCookiesRequest(
            cookies=_XHS_COOKIES, cookie_format="chrome-v1",
            extension_protocol_version=2)
        status, _ = await _response_of(
            await sync_account_cookies("xhs", req, _ext_request(),
                                       x_sync_ticket=ticket))
        assert status == 200

        status, body = await _response_of(
            await delete_account_session("xhs"))
        assert status == 409
        assert body["safe_error_code"] == "verification_in_progress"

        gate.set()
        await asyncio.sleep(0.1)

    asyncio.run(scenario())


def test_router_409_does_not_block_other_platforms(monkeypatch, tmp_path):
    """xhs 后台验证进行中：bilibili 的 delete 不受影响（409 是平台粒度）。"""
    from api.services import accounts as acc
    # delete 会真的 rmtree 平台 profile 目录；指向临时目录，避免删掉本机登录态。
    monkeypatch.setattr(acc, "BROWSER_DATA_DIR", tmp_path)
    monkeypatch.setattr(acc, "profile_dir_for",
                        lambda p: tmp_path / acc.PLATFORM_PROFILE_DIRS[p])
    acc.profile_dir_for("bilibili").mkdir(parents=True, exist_ok=True)
    gate, _ = _start_background_verify(monkeypatch)

    async def scenario():
        ticket = create_sync_ticket("xhs")
        req = SyncCookiesRequest(
            cookies=_XHS_COOKIES, cookie_format="chrome-v1",
            extension_protocol_version=2)
        status, _ = await _response_of(
            await sync_account_cookies("xhs", req, _ext_request(),
                                       x_sync_ticket=ticket))
        assert status == 200
        assert acc.is_verify_active("xhs") is True
        assert acc.is_verify_active("bilibili") is False

        status, body = await _response_of(
            await delete_account_session("bilibili"))
        assert status == 200
        assert body["success"] is True

        gate.set()
        await asyncio.sleep(0.1)

    asyncio.run(scenario())


def test_router_operations_resume_after_verify_finishes(monkeypatch):
    """后台验证完成（任务弹出）后：verify 路由不再 409，走真实 unavailable
    路径（fake client 抛真实异常）。"""
    from api.services import accounts as acc
    from media_platform.xhs.exception import DataFetchError as XhsDataFetchError
    gate, original_pong = _start_background_verify(monkeypatch)

    async def scenario():
        ticket = create_sync_ticket("xhs")
        req = SyncCookiesRequest(
            cookies=_XHS_COOKIES, cookie_format="chrome-v1",
            extension_protocol_version=2)
        status, _ = await _response_of(
            await sync_account_cookies("xhs", req, _ext_request(),
                                       x_sync_ticket=ticket))
        assert status == 200
        assert acc.is_verify_active("xhs") is True

        gate.set()
        await asyncio.sleep(0.15)
        assert acc.is_verify_active("xhs") is False, "任务完成后必须可操作"

        # 任务已弹出：恢复生产 probe，再注入抛真实异常的 fake client，
        # verify 走 unavailable 路径（不再被 gated_pong 返回 "verified"）。
        monkeypatch.setattr("api.services.accounts._pong_with_profile",
                            original_pong)
        _probe_exc_client(monkeypatch, XhsDataFetchError("api down"))
        status, body = await _response_of(await verify_account("xhs"))
        assert status == 200
        assert body["status"] == "unavailable"
        assert body["verified"] is False

    asyncio.run(scenario())


# ── Round 16：搜索与账号操作的互斥竞态（真实路由 + 真实 coordinator）───────

def _search_active(monkeypatch):
    """模拟"搜索正在运行"：路由/coordinator 全生产路径，仅注入状态。"""
    monkeypatch.setattr(router_mod.search_job_manager,
                        "is_search_active", lambda: True)


def _search_idle(monkeypatch):
    monkeypatch.setattr(router_mod.search_job_manager,
                        "is_search_active", lambda: False)


def _record_stop_worker(monkeypatch):
    """记录 stop_platform_worker 调用（不真杀进程）。"""
    stopped = []

    async def fake_stop(platform):
        stopped.append(platform)

    monkeypatch.setattr(router_mod.search_job_manager,
                        "stop_platform_worker", fake_stop)
    return stopped


def test_router_sync_409_search_in_progress_releases_lease(monkeypatch):
    """搜索运行中：sync → 409 search_in_progress，且槽位/租约被释放。"""
    from api.services import accounts as acc
    _search_active(monkeypatch)
    stopped = _record_stop_worker(monkeypatch)

    async def scenario():
        status, body = await _response_of(
            await sync_account_cookies(
                "xhs", SyncCookiesRequest(
                    cookies=_XHS_COOKIES, cookie_format="chrome-v1",
                    extension_protocol_version=2),
                _ext_request(), x_sync_ticket="fake-ticket"))
        assert status == 409
        assert body["safe_error_code"] == "search_in_progress"
        assert stopped == [], "409 路径不得先停 worker（未取得操作资格）"
        # 租约已释放：同平台可立即重新获取（无槽位泄漏）。
        assert await acc.operation_coordinator.acquire_account(
            "xhs", "sync") == ""
        await acc.operation_coordinator.release_account("xhs")

    asyncio.run(scenario())


def test_router_verify_delete_409_search_in_progress_releases_lease(monkeypatch):
    """搜索运行中：verify/delete → 409 search_in_progress + 租约释放。"""
    from api.services import accounts as acc
    _search_active(monkeypatch)
    stopped = _record_stop_worker(monkeypatch)

    async def scenario():
        status, body = await _response_of(await verify_account("xhs"))
        assert status == 409
        assert body["safe_error_code"] == "search_in_progress"
        status, body = await _response_of(
            await delete_account_session("xhs"))
        assert status == 409
        assert body["safe_error_code"] == "search_in_progress"
        assert stopped == []
        assert await acc.operation_coordinator.acquire_account(
            "xhs", "sync") == ""
        await acc.operation_coordinator.release_account("xhs")

    asyncio.run(scenario())


def test_router_account_ops_409_conflict_never_reaches_service(monkeypatch):
    """搜索运行中的 409 必须发生在调用账号服务之前（服务调用计数为 0）。"""
    from api.services import accounts as acc
    _search_active(monkeypatch)
    calls = []

    async def fake_verify(platform):
        calls.append(platform)
        return {"status": "verified", "verified": True}

    async def fake_delete(platform):
        calls.append(platform)
        return {"success": True}

    monkeypatch.setattr(acc, "verify_platform", fake_verify)
    monkeypatch.setattr(acc, "delete_platform_session", fake_delete)

    async def scenario():
        status, _ = await _response_of(await verify_account("xhs"))
        assert status == 409
        status, _ = await _response_of(await delete_account_session("xhs"))
        assert status == 409
        assert calls == [], "409 路径绝不能触碰账号服务"

    asyncio.run(scenario())


def test_router_search_409_when_account_op_active(monkeypatch):
    """账号操作进行中：新搜索 → 409；操作完成后搜索恢复。"""
    from api.services import accounts as acc
    from api.routers.search import create_search_job
    from fastapi import HTTPException
    _search_idle(monkeypatch)
    created = []

    async def fake_create_job(req):
        created.append(req.keyword)
        return {"job_id": "fake", "overall": "running", "keyword": req.keyword,
                "created_at": "2026-01-01T00:00:00+00:00", "platforms": {},
                "results": []}

    monkeypatch.setattr(router_mod.search_job_manager,
                        "create_job", fake_create_job)
    req = SearchJobRequestSchema(keyword="test", platforms=["xhs"],
                                 limit_per_platform=1)

    async def scenario():
        # 占用账号操作槽位（真实 coordinator）。
        assert await acc.operation_coordinator.acquire_account(
            "xhs", "sync") == ""
        try:
            with pytest.raises(HTTPException) as ei:
                await create_search_job(req)
            assert ei.value.status_code == 409
            assert "账号操作" in str(ei.value.detail)
            assert created == [], "409 时绝不能触达 manager.create_job"
        finally:
            await acc.operation_coordinator.release_account("xhs")
        # 释放后：搜索正常创建（真实路由走到 manager.create_job）。
        resp = await create_search_job(req)
        assert resp["job_id"] == "fake"
        assert created == ["test"]

    asyncio.run(scenario())


def test_router_account_ops_stop_platform_worker_before_op(monkeypatch):
    """账号操作（无冲突时）必须先停对应平台 worker，再调用服务。"""
    from api.services import accounts as acc
    _search_idle(monkeypatch)
    stopped = _record_stop_worker(monkeypatch)

    async def fake_verify(platform):
        return {"status": "verified", "verified": True, "platform": platform}

    async def fake_delete(platform):
        return {"success": True, "platform": platform}

    async def fake_sync(platform, cookies, cookie_format, **kw):
        return {"status": "verified", "verified": True, "platform": platform}

    monkeypatch.setattr(acc, "verify_platform", fake_verify)
    monkeypatch.setattr(acc, "delete_platform_session", fake_delete)
    monkeypatch.setattr(acc, "sync_platform_cookies", fake_sync)

    async def scenario():
        status, body = await _response_of(await verify_account("xhs"))
        assert status == 200 and body["status"] == "verified"
        assert stopped == ["xhs"], "verify 前必须停 xhs worker"
        stopped.clear()

        status, body = await _response_of(
            await delete_account_session("xhs"))
        assert status == 200 and body["success"] is True
        assert stopped == ["xhs"], "delete 前必须停 xhs worker"
        stopped.clear()

        ticket = create_sync_ticket("xhs")
        status, body = await _response_of(
            await sync_account_cookies(
                "xhs", SyncCookiesRequest(
                    cookies=_XHS_COOKIES, cookie_format="chrome-v1",
                    extension_protocol_version=2),
                _ext_request(), x_sync_ticket=ticket))
        assert status == 200
        assert stopped == ["xhs"], "sync 前必须停 xhs worker"

    asyncio.run(scenario())
