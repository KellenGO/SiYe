# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
# This file is part of MediaCrawler project.
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

"""Reader parsing, session lifecycle, bounded cache and local API regressions."""

import asyncio
import json
from types import SimpleNamespace
from contextlib import asynccontextmanager

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.routers.reading import get_reading_service, reading_router
from api.services import reading
from api.services.operation_coordinator import OperationCoordinator
from api.services.reading_content import detail_from_page, parse_reading_html, reading_detail, reading_image, reading_reference
from api.services.research_diagnostics import AcquisitionError
from tests.fixtures.browser import FakePage

PREPARE_READING = reading.prepare_reading


@pytest.fixture(autouse=True)
def isolated_preparation(monkeypatch):
    async def prepare(*args):
        pass
    monkeypatch.setattr(reading, "prepare_reading", prepare)
    @asynccontextmanager
    async def browser_page(*args):
        yield FakePage(evaluate_result="Mozilla/5.0 (Windows NT 10.0) Chrome/140.0")
    monkeypatch.setattr(reading, "reading_page", browser_page)


def test_body_order_and_inert_content():
    value = parse_reading_html('''<h2>标题</h2><p>前文<strong>加粗文字</strong><br>换行
        <img data-original="//pic.zhimg.com/original.jpg" src="https://evil.test/pixel">后文</p>
        <blockquote><p>引用</p></blockquote><pre>  x = 1\n  print(x)</pre><ul><li>列表</li></ul>
        <script>secret()</script><style>secret</style><iframe src="javascript:secret()"></iframe>''')
    blocks = value["blocks"]
    assert [block["type"] for block in blocks] == ["heading", "paragraph", "image", "paragraph", "quote", "code", "list-item", "unsupported"]
    assert blocks[1]["text"].startswith("前文加粗文字\n换行")
    assert blocks[2]["url"] == "https://pic.zhimg.com/original.jpg"
    assert blocks[3]["text"] == "后文"
    assert blocks[5]["text"] == "  x = 1\n  print(x)"
    assert "secret" not in json.dumps(value)


@pytest.mark.parametrize("url", ["javascript:alert(1)", "https://pic.zhimg.com.evil.test/a", "https://user:pass@pic.zhimg.com/a", "https://pic.zhimg.com:444/a", "data:image/png;base64,x", "https://127.0.0.1/a"])
def test_image_sources_are_platform_only(url):
    assert reading_image(url) is None


def test_plain_image_url_upgrade_and_unsupported_content():
    assert reading_image("http://pic.zhimg.com/a?q=1#part") == "https://pic.zhimg.com/a?q=1"
    value = parse_reading_html('<p>A<img src="https://evil.test/a">B<span data-tex="x">x</span>C</p><table><tr><td>1</td></tr></table>')
    assert [row["type"] for row in value["blocks"]].count("unsupported") == 3


def test_paid_truncated_and_empty_are_honest():
    limited = reading_detail({"id": "42", "content": "", "paid_info": {"is_paid": True}}, "answer", "42")
    assert limited["limited"] and not limited["blocks"] and limited["notices"]
    marker = parse_reading_html('<p>可读</p><div data-paywall="true">付费隐藏</div>')
    assert marker["limited"] and "付费隐藏" not in str(marker["blocks"])
    with pytest.raises(ValueError):
        reading_detail({"id": "42", "content": "", "excerpt": "搜索摘要"}, "answer", "42")
    with pytest.raises(PermissionError):
        reading_detail({"id": "42", "content": "正文", "can_read": False}, "answer", "42")


def test_body_and_block_limits(monkeypatch):
    from api.services import reading_content
    monkeypatch.setattr(reading_content, "MAX_TEXT_CHARS", 12)
    value = parse_reading_html("<p>" + "字" * 30 + "</p>")
    assert value["limited"] and len(value["blocks"][0]["text"]) == 12
    monkeypatch.setattr(reading_content, "MAX_BLOCKS", 2)
    value = parse_reading_html("<p>A</p><p>B</p><p>C</p>")
    assert len(value["blocks"]) == 2 and value["limited"]


def test_page_extracts_matching_identity_instead_of_first_entity():
    state = {"initialState": {"entities": {"answers": {"41": {"id": 41, "content": "其它答案"}, "42": {"id": "42", "content": "目标正文"}}}}}
    page = '<script id="js-initialData">' + json.dumps(state) + '</script>'
    assert detail_from_page(page, "answer", "42")["content"] == "目标正文"
    with pytest.raises(ValueError):
        detail_from_page(page, "answer", "43")


@pytest.mark.parametrize("url", ["https://evil.test/question/1/answer/42", "http://www.zhihu.com/question/1/answer/42", "https://www.zhihu.com/question/1/answer/43", "https://www.zhihu.com:444/answer/42", "https://u:p@www.zhihu.com/answer/42"])
def test_reference_rejects_untrusted_or_mismatched_routes(url):
    with pytest.raises(ValueError):
        reading_reference("answer", "42", url)


def test_reference_is_canonical_and_supports_old_snapshots():
    assert reading_reference("answer", "42", "https://zhihu.com/question/1/answer/42?track=x#foo") == "https://www.zhihu.com/question/1/answer/42"
    assert reading_reference("answer", "42", "https://www.zhihu.com/answer/42") == "https://www.zhihu.com/answer/42"
    assert reading_reference("article", "42", "https://zhuanlan.zhihu.com/p/42/") == "https://zhuanlan.zhihu.com/p/42"


@pytest.fixture
def service(monkeypatch):
    monkeypatch.setattr(reading, "operation_coordinator", OperationCoordinator())
    monkeypatch.setattr(reading, "get_account_generation", lambda _: 1)
    return reading.ReadingService()


@pytest.mark.asyncio
async def test_cache_refresh_generation_ttl_and_capacity(service, monkeypatch):
    now = 10.0
    monkeypatch.setattr(reading.time, "monotonic", lambda: now)
    calls = []
    async def fetch(kind, identity, url, platform="zhihu"):
        calls.append(identity)
        return {"content_id": identity, "blocks": [{"type": "paragraph", "text": "正文"}]}
    monkeypatch.setattr(reading, "fetch_reading", fetch)
    url = "https://www.zhihu.com/answer/42"
    first = await service.read("answer", "42", url)
    assert await service.read("answer", "42", url) is first and len(calls) == 1
    await service.read("answer", "42", url, refresh=True)
    monkeypatch.setattr(reading, "get_account_generation", lambda _: 2)
    await service.read("answer", "42", url)
    assert len(calls) == 3
    key = ("zhihu", 2, "answer", "42")
    now += 121
    await service.read("answer", "42", url)
    assert len(calls) == 4
    for identity in range(50, 58):
        await service.read("article", str(identity), f"https://zhuanlan.zhihu.com/p/{identity}")
    assert len(service.cache) == 6


@pytest.mark.asyncio
async def test_busy_and_failed_read_do_not_hold_lease_or_leak_errors(service, monkeypatch):
    await reading.operation_coordinator.acquire_exclusive("search")
    with pytest.raises(reading.ReadingError, match="搜索"):
        await service.read("answer", "42", "https://www.zhihu.com/answer/42")
    await reading.operation_coordinator.release_exclusive("search")
    async def fail(*args):
        raise RuntimeError("cookie=private-secret https://sensitive.test")
    monkeypatch.setattr(reading, "fetch_reading", fail)
    with pytest.raises(reading.ReadingError) as error:
        await service.read("answer", "42", "https://www.zhihu.com/answer/42")
    assert "private-secret" not in str(error.value) and not service.cache
    assert await reading.operation_coordinator.acquire_exclusive("search")


@pytest.mark.asyncio
async def test_cancellation_releases_resources_and_shutdown_clears_cache(service, monkeypatch):
    started, closed = asyncio.Event(), asyncio.Event()
    async def fetch(*args):
        try:
            started.set()
            await asyncio.Event().wait()
        finally:
            closed.set()
    monkeypatch.setattr(reading, "fetch_reading", fetch)
    task = asyncio.create_task(service.read("answer", "42", "https://www.zhihu.com/answer/42"))
    await started.wait()
    await service.cleanup()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert closed.is_set() and not service.tasks and not service.cache
    assert await reading.operation_coordinator.acquire_exclusive("search")


@pytest.mark.asyncio
async def test_fetch_reuses_client_and_closes_on_success_and_permission_failure(monkeypatch):
    closed = []
    async def session(*args, **kwargs):
        return {"d_c0": "private-sign", "z_c0": "private-login"}
    monkeypatch.setattr(reading, "ensure_session_snapshot", session)
    client = SimpleNamespace(default_headers={"user-agent": "Mozilla/5.0"})
    async def get(path, params):
        assert path == "/api/v4/answers/42"
        assert "Chrome/" in client.default_headers["user-agent"]
        return {"id": "42", "content": "<p>正文</p>", "cookie": "private-secret"}
    client.get = get
    async def get_client(self, snapshot):
        return client
    async def close(self):
        closed.append(True)
    monkeypatch.setattr(reading.ResultHydrator, "_get_zhihu", get_client)
    monkeypatch.setattr(reading.ResultHydrator, "close", close)
    result = await reading.fetch_reading("answer", "42", "https://www.zhihu.com/answer/42")
    assert "private" not in json.dumps(result) and closed == [True]
    async def denied(*args):
        return {"id": "42", "content": "<p>正文</p>", "can_read": False}
    client.get = denied
    with pytest.raises(reading.ReadingError) as error:
        await reading.fetch_reading("answer", "42", "https://www.zhihu.com/answer/42")
    assert error.value.code == "restricted" and len(closed) == 2


@pytest.mark.asyncio
async def test_browser_fallback_uses_canonical_page_and_always_closes(monkeypatch):
    closed = []
    async def session(*args, **kwargs):
        return {"d_c0": "sign"}
    async def get(*args):
        raise AcquisitionError("restricted", 403)
    async def get_client(*args):
        return SimpleNamespace(get=get, default_headers={})
    async def close(*args):
        closed.append("client")
    page = FakePage(evaluate_result="Browser UA")
    page.url = "https://zhuanlan.zhihu.com/p/42"
    async def content():
        return '<script id="js-initialData">' + json.dumps({"initialState": {"entities": {"articles": {"42": {"id": 42, "content": "<p>页面正文</p>"}}}}}) + '</script>'
    page.content = content
    @asynccontextmanager
    async def provider_page(platform, snapshot):
        try:
            yield page
        finally:
            closed.append("browser")
    monkeypatch.setattr(reading, "ensure_session_snapshot", session)
    monkeypatch.setattr(reading.ResultHydrator, "_get_zhihu", get_client)
    monkeypatch.setattr(reading.ResultHydrator, "close", close)
    monkeypatch.setattr(reading, "reading_page", provider_page)
    result = await reading.fetch_reading("article", "42", "https://zhuanlan.zhihu.com/p/42")
    assert result["blocks"][0]["text"] == "页面正文"
    assert page.goto_urls == ["https://zhuanlan.zhihu.com/p/42"] and closed == ["browser", "client"]


@pytest.mark.asyncio
async def test_running_search_is_checked_before_stopping_idle_worker(monkeypatch):
    from api.services.search_job_manager import search_job_manager
    stopped = []
    async def stop(platform):
        stopped.append(platform)
    monkeypatch.setattr(search_job_manager, "is_search_active", lambda: True)
    monkeypatch.setattr(search_job_manager, "stop_platform_worker", stop)
    with pytest.raises(reading.ReadingError) as error:
        await PREPARE_READING()
    assert error.value.code == "busy" and not stopped
    monkeypatch.setattr(search_job_manager, "is_search_active", lambda: False)
    await PREPARE_READING()
    assert stopped == ["zhihu"]


@pytest.mark.asyncio
async def test_closed_page_cancels_pending_api_read():
    from api.routers.reading import ReadingInput, read_detail
    from fastapi import HTTPException
    closed = asyncio.Event()
    class Service:
        async def read(self, *args, **kwargs):
            try:
                await asyncio.Event().wait()
            finally:
                closed.set()
    async def disconnected():
        return True
    payload = ReadingInput(platform="zhihu", content_type="answer", content_id="42", url="https://www.zhihu.com/answer/42")
    with pytest.raises(HTTPException) as error:
        await read_detail(payload, SimpleNamespace(is_disconnected=disconnected), Service())
    assert error.value.status_code == 499 and closed.is_set()


def test_api_checks_local_origin_and_input_without_touching_profiles():
    class Service:
        async def read(self, kind, identity, url, refresh, platform="zhihu"):
            if platform != "zhihu":
                raise ValueError("unsupported fixture")
            reading_reference(kind, identity, url)
            return reading_detail({"id": identity, "content": "<p>正文</p>"}, kind, identity)
    app = FastAPI()
    app.include_router(reading_router)
    app.dependency_overrides[get_reading_service] = lambda: Service()
    client = TestClient(app, base_url="http://127.0.0.1")
    payload = {"platform": "zhihu", "content_type": "answer", "content_id": "42", "url": "https://www.zhihu.com/answer/42"}
    assert client.post("/api/reading/detail", json=payload).status_code == 200
    assert client.post("/api/reading/detail", json=payload, headers={"origin": "https://evil.test"}).status_code == 403
    assert client.post("/api/reading/detail", json=payload, headers={"host": "evil.test", "origin": "http://evil.test"}).status_code == 403
    assert client.post("/api/reading/detail", json=payload, headers={"sec-fetch-site": "cross-site"}).status_code == 403
    assert client.post("/api/reading/detail", json={**payload, "url": "https://evil.test/answer/42"}).status_code == 422
    assert client.post("/api/reading/detail", json={**payload, "platform": "xhs"}).status_code == 422
