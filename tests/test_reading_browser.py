# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
# This file is part of MediaCrawler project.
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

"""Official-page identity, delayed content, limits and cancellation."""

import asyncio
import json
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from api.services import reading_browser as browser
from api.services.research_diagnostics import AcquisitionError
from tests.fixtures.browser import FakeBrowserContext, FakePage


@pytest.mark.parametrize("status,code", [(401, "session_expired"), (403, "rate_limited"), (412, "rate_limited"),
    (429, "rate_limited"), (404, "restricted"), (500, "detail_api_failed")])
def test_navigation_errors_distinguish_challenge_and_permission(status, code):
    with pytest.raises(AcquisitionError) as error:
        browser.page_status(SimpleNamespace(status=status))
    assert error.value.safe_code == code and error.value.http_status == status


@pytest.mark.parametrize("url,allowed", [
    ("https://www.douyin.com/aweme/v1/web/aweme/detail/?aweme_id=42", True),
    ("https://www-hj.douyin.com/aweme/v1/web/aweme/detail/", True),
    ("https://other.douyin.com/aweme/v1/web/aweme/detail/", False),
    ("https://www-hj.douyin.com.evil.test/aweme/v1/web/aweme/detail/", False),
    ("https://user:pass@www.douyin.com/aweme/v1/web/aweme/detail/", False),
    ("http://www.douyin.com/aweme/v1/web/aweme/detail/", False),
    ("https://www.douyin.com:444/aweme/v1/web/aweme/detail/", False),
])
def test_response_origins_are_exact(url, allowed):
    assert browser.douyin_detail_response_url(url) is allowed


@pytest.mark.asyncio
async def test_zhihu_waits_for_matching_body_instead_of_using_other_entity(monkeypatch):
    page = FakePage()
    page.url = "https://www.zhihu.com/answer/42"
    attempts = []
    async def content():
        attempts.append(True)
        rows = {"41": {"id": "41", "content": "其它答案"}}
        if len(attempts) > 1:
            rows["42"] = {"id": "42", "content": "<p>延迟正文</p>"}
        return '<script id="js-initialData">' + json.dumps({"initialState": {"entities": {"answers": rows}}}) + '</script>'
    page.content = content
    result = await browser.zhihu_page_detail(page, "answer", "42", page.url)
    assert result["blocks"][0]["text"] == "延迟正文" and len(attempts) == 2
    assert page.goto_urls == [page.url]


@pytest.mark.asyncio
async def test_zhihu_challenge_and_missing_body_do_not_become_permission_errors(monkeypatch):
    page = FakePage(evaluate_result={"challenge": True})
    page.url = "https://www.zhihu.com/answer/42"
    async def content():
        return "<html>安全验证</html>"
    page.content = content
    with pytest.raises(AcquisitionError) as error:
        await browser.zhihu_page_detail(page, "answer", "42", page.url)
    assert error.value.safe_code == "rate_limited"
    page.evaluate_result = {"login": True}
    with pytest.raises(AcquisitionError) as error:
        await browser.zhihu_page_detail(page, "answer", "42", page.url)
    assert error.value.safe_code == "login_required"
    page.evaluate_result = {}
    monkeypatch.setattr(browser, "ZHIHU_BODY_TIMEOUT", 0)
    with pytest.raises(AcquisitionError) as error:
        await browser.zhihu_page_detail(page, "answer", "42", page.url)
    assert error.value.safe_code == "malformed_response"


@pytest.fixture
def official_page(monkeypatch):
    page = FakePage()
    page.url = "https://www.douyin.com/video/42"
    handlers, closed = {}, []
    page.on = lambda name, handler: handlers.update({name: handler})
    page.remove_listener = lambda name, handler: handlers.pop(name)
    @asynccontextmanager
    async def opened(*args):
        try:
            yield page
        finally:
            closed.append(True)
    monkeypatch.setattr(browser, "reading_page", opened)
    return page, handlers, closed


@pytest.mark.asyncio
async def test_response_capture_checks_ids_sizes_stale_routes_and_cancels(official_page):
    page, handlers, closed = official_page
    pending = asyncio.Event()
    body_calls = []
    def response(detail, length="100", url="https://www-hj.douyin.com/aweme/v1/web/aweme/detail/"):
        async def body():
            body_calls.append(detail)
            if detail == "pending":
                pending.set()
                await asyncio.Future()
            return json.dumps({"aweme_detail": detail}).encode()
        return SimpleNamespace(status=200, headers={"content-length": length}, url=url, body=body,
            request=SimpleNamespace(method="GET", url=url))
    async def goto(url, **kwargs):
        handlers["response"](response({"aweme_id": "43", "desc": "其它内容"}))
        handlers["response"](response({"aweme_id": "42", "desc": "太大"}, str(browser.MAX_RESPONSE_BYTES + 1)))
        handlers["response"](response("pending"))
        await pending.wait()
        handlers["response"](response({"aweme_id": "42", "desc": "目标正文", "cookie": "private",
            "video": {"play_addr": {"url_list": ["https://v.douyincdn.com/video"]}}}))
    page.goto = goto
    value = await browser.douyin_page_detail({}, "42", "video", page.url)
    assert value["blocks"][0]["text"] == "目标正文" and value["media"] and "private" not in str(value)
    assert len(body_calls) == 3 and closed == [True] and not handlers


@pytest.mark.asyncio
async def test_douyin_waits_for_video_after_text_and_returns_text_on_timeout(official_page, monkeypatch):
    page, handlers, closed = official_page
    polls = []
    async def evaluate(script, *args):
        if args:
            polls.append(True)
            detail = {"aweme_id": "42", "desc": "正文先到"}
            if len(polls) > 1:
                detail["video"] = {"play_addr": {"url_list": ["https://www.douyin.com/aweme/v1/play/?video_id=public"]}}
            return detail
        return {}
    page.evaluate = evaluate
    value = await browser.douyin_page_detail({}, "42", "video", page.url)
    assert value["media"] and len(polls) == 2
    polls.clear()
    monkeypatch.setattr(browser, "PAGE_TIMEOUT", 0.03)
    value = await browser.douyin_page_detail({}, "42", "video", page.url)
    assert not value["media"] and value["blocks"][0]["text"] == "正文先到" and value["notices"]
    assert closed == [True, True] and not handlers


@pytest.mark.asyncio
async def test_stale_route_and_oversize_response_body_are_rejected(official_page, monkeypatch):
    page, handlers, closed = official_page
    monkeypatch.setattr(browser, "PAGE_TIMEOUT", 0.03)
    async def body():
        page.url = "https://www.douyin.com/video/43"
        return json.dumps({"aweme_detail": {"aweme_id": "42", "desc": "旧页面"}}).encode()
    async def goto(*args, **kwargs):
        url = "https://www.douyin.com/aweme/v1/web/aweme/detail/"
        handlers["response"](SimpleNamespace(status=200, headers={"content-length": "10"}, url=url, body=body,
            request=SimpleNamespace(method="GET", url=url)))
    page.goto = goto
    with pytest.raises(TimeoutError):
        await browser.douyin_page_detail({}, "42", "video", "https://www.douyin.com/video/42")
    assert not handlers and closed == [True]


@pytest.mark.asyncio
async def test_page_read_cancellation_removes_listener_and_closes(official_page):
    page, handlers, closed = official_page
    waiting = asyncio.Event()
    async def evaluate(*args):
        waiting.set()
        await asyncio.Future()
    page.evaluate = evaluate
    task = asyncio.create_task(browser.douyin_page_detail({}, "42", "video", page.url))
    await waiting.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert closed == [True] and not handlers


@pytest.mark.asyncio
async def test_reading_page_closes_real_context_lease_on_cancellation(monkeypatch):
    context = FakeBrowserContext()
    @asynccontextmanager
    async def session(*args):
        try:
            yield context
        finally:
            await context.close()
    monkeypatch.setattr(browser, "platform_session_context", session)
    async with browser.reading_page("douyin", {}) as page:
        assert page in context.pages
    assert context.closed and context.close_count == 1
