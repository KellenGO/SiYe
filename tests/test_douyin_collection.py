"""抖音收藏列表的页面内读取（虚构页面，不访问平台）。"""
import asyncio
from types import SimpleNamespace

import pytest

from media_platform.douyin import collection
from media_platform.douyin.collection import (
    COLLECTION_URL, CollectionUnavailableError, ensure_collection_page,
    fetch_collected_page,
)


class _Page:
    """记录导航与 evaluate 的假页面；evaluate 按队列返回脚本结果。"""

    def __init__(self, url="https://www.douyin.com/", results=None):
        self.url = url
        self.results = list(results or [])
        self.goto_calls = []
        self.evaluate_calls = []
        self.fail_goto = False

    async def goto(self, url, **kwargs):
        if self.fail_goto:
            raise RuntimeError("navigation failed")
        self.goto_calls.append((url, kwargs))
        self.url = url

    async def evaluate(self, script, arg=None):
        self.evaluate_calls.append((script, arg))
        if not self.results:
            raise AssertionError("unexpected extra evaluate")
        return self.results.pop(0)


def _run(coro):
    return asyncio.run(coro)


def test_ensure_collection_page_navigates_when_not_on_collection():
    page = _Page(url="https://www.douyin.com/")
    _run(ensure_collection_page(page))
    assert page.goto_calls[0][0] == COLLECTION_URL
    assert page.goto_calls[0][1]["wait_until"] == "domcontentloaded"


def test_ensure_collection_page_does_not_renavigate_when_already_there():
    page = _Page(url=COLLECTION_URL)
    _run(ensure_collection_page(page))
    assert page.goto_calls == []


def test_ensure_collection_page_without_page_is_unavailable():
    with pytest.raises(CollectionUnavailableError) as exc:
        _run(ensure_collection_page(None))
    assert "浏览器" in str(exc.value)


def test_ensure_collection_page_navigation_failure_is_unavailable():
    page = _Page(url="https://www.douyin.com/")
    page.fail_goto = True
    with pytest.raises(CollectionUnavailableError):
        _run(ensure_collection_page(page))


def test_fetch_collected_page_retries_through_the_warmup_window(monkeypatch):
    """预热期的 403 要重试，而不是当成空收藏或登录失效。"""
    monkeypatch.setattr(collection, "WARMUP_INTERVAL", 0)
    body = {"status_code": 0, "has_more": 0, "cursor": "0", "aweme_list": []}
    page = _Page(url=COLLECTION_URL, results=[
        {"status": 403, "body": None},
        {"status": 403, "body": None},
        {"status": 200, "body": body},
    ])
    assert _run(fetch_collected_page(page, 0, 20)) == body
    assert len(page.evaluate_calls) == 3
    # 游标与数量以数字传入脚本，脚本自己再定界。
    assert page.evaluate_calls[0][1] == {"cursor": 0, "count": 20}


def test_fetch_collected_page_exhausted_warmup_reports_retryable_failure(monkeypatch):
    monkeypatch.setattr(collection, "WARMUP_ATTEMPTS", 3)
    monkeypatch.setattr(collection, "WARMUP_INTERVAL", 0)
    page = _Page(url=COLLECTION_URL, results=[{"status": 403, "body": None}] * 3)
    with pytest.raises(CollectionUnavailableError) as exc:
        _run(fetch_collected_page(page))
    assert exc.value.safe_code == "failed"
    assert "稍后重试" in str(exc.value)


def test_fetch_collected_page_malformed_body_is_unavailable(monkeypatch):
    monkeypatch.setattr(collection, "WARMUP_ATTEMPTS", 2)
    monkeypatch.setattr(collection, "WARMUP_INTERVAL", 0)
    page = _Page(url=COLLECTION_URL, results=[{"status": 200, "body": None}] * 2)
    with pytest.raises(CollectionUnavailableError):
        _run(fetch_collected_page(page))


def test_fetch_collected_page_passes_through_platform_status_code(monkeypatch):
    """明确未登录的 status_code 要原样交给调用方判定，不能在这里吞掉。"""
    body = {"status_code": 8, "has_more": 0, "cursor": None, "aweme_list": []}
    page = _Page(url=COLLECTION_URL, results=[{"status": 200, "body": body}])
    assert _run(fetch_collected_page(page)) == body


def test_fetch_collected_page_clamps_arguments(monkeypatch):
    body = {"status_code": 0, "has_more": 0, "cursor": "0", "aweme_list": []}
    page = _Page(url=COLLECTION_URL, results=[{"status": 200, "body": body}])
    _run(fetch_collected_page(page, -5, 999))
    assert page.evaluate_calls[0][1] == {"cursor": 0, "count": 20}
