"""Offline regressions for eliminating redundant search startup and requests."""
from types import SimpleNamespace

import pytest

from aggregate_search import worker
from aggregate_search.pagination import PageState, PaginationRun, current_pagination
from base.exceptions import RateLimitError
from tests.fixtures.browser import FakeBrowserContext, FakePlaywright
from tests.test_worker_fast_path import (
    _FakeCrawler, _patch_factory, _run_capture, _results, _metrics,
    _ZH_FAKE_SEARCH_RESPONSE,
)


@pytest.fixture(autouse=True)
def isolated_snapshots(monkeypatch):
    monkeypatch.setattr(worker, "_search_session_snapshots", {})
    monkeypatch.setattr(worker, "_search_session_inputs", {})


class XhsListClient:
    cookie_dict = {"a1": "private-session", "web_session": "private-login"}

    async def get_note_by_keyword(self, **kwargs):
        return {"has_more": False, "items": [
            {"id": "note-1", "note_card": {"display_title": "测试内容"}}
        ]}


def run_search(platform="xhs", **kwargs):
    return _run_capture(worker.run_worker, "test-job", "search", platform,
                        "测试", 1, fast_path=True, pagination={}, **kwargs)


def test_browser_session_reused_on_next_search_without_browser(monkeypatch):
    launches = []
    async def browser(crawler):
        launches.append(True)
        crawler.xhs_client = XhsListClient()
        await current_pagination.get().run(crawler.xhs_client)
    async def fast(crawler):
        await current_pagination.get().run(XhsListClient())
    crawler = _FakeCrawler(browser_start=browser, fp_search=fast)
    _patch_factory(monkeypatch, crawler)
    first, second = run_search(), run_search()
    assert len(launches) == 1
    assert _results(first) == _results(second)
    assert crawler.snapshot_seen == XhsListClient.cookie_dict
    assert any(m.get("provider_used") == "session_api" for m in _metrics(second))
    assert "private-session" not in repr(first + second)
    assert "private-login" not in repr(first + second)


def test_explicit_snapshot_wins_and_failed_session_is_discarded(monkeypatch):
    worker._search_session_snapshots["xhs"] = {"a1": "old"}
    async def fail(crawler):
        raise RateLimitError("xhs")
    crawler = _FakeCrawler(fp_search=fail)
    _patch_factory(monkeypatch, crawler)
    events = run_search(session_snapshot={"a1": "new"})
    assert crawler.snapshot_seen == {"a1": "new"}
    assert "xhs" not in worker._search_session_snapshots
    assert not crawler.browser_path_used
    assert any(e.event == "error" and e.data["type"] == "rate_limited" for e in events)


def test_failed_fast_session_still_falls_back_and_can_recover(monkeypatch):
    worker._search_session_snapshots["xhs"] = {"a1": "old"}
    async def fail(crawler):
        raise RuntimeError("expired")
    async def browser(crawler):
        crawler.xhs_client = XhsListClient()
        await current_pagination.get().run(crawler.xhs_client)
    crawler = _FakeCrawler(fp_search=fail, browser_start=browser)
    _patch_factory(monkeypatch, crawler)
    events = run_search()
    assert len(_results(events)) == 1
    assert crawler.browser_path_used
    assert worker._search_session_snapshots["xhs"] == XhsListClient.cookie_dict


def test_empty_browser_result_does_not_cache_session(monkeypatch):
    async def browser(crawler):
        crawler.xhs_client = XhsListClient()
    _patch_factory(monkeypatch, _FakeCrawler(browser_start=browser))
    run_search()
    assert not worker._search_session_snapshots


def test_bilibili_fallback_refresh_survives_unchanged_upstream_snapshot(monkeypatch):
    from tests.test_search_exploration import Client, page
    snapshots, launches = [], []
    async def fast(crawler):
        snapshots.append(crawler.snapshot_seen)
        if crawler.snapshot_seen == {"session": "old"}:
            raise RuntimeError("old session")
        await current_pagination.get().run(Client([page("bilibili", [1])]))
    async def browser(crawler):
        launches.append(True)
        crawler.bili_client = SimpleNamespace(cookie_dict={"session": "refreshed"})
        await current_pagination.get().run(Client([page("bilibili", [1])]))
    _patch_factory(monkeypatch, _FakeCrawler(fp_search=fast, browser_start=browser))
    first = run_search("bilibili", session_snapshot={"session": "old"})
    second = run_search("bilibili", session_snapshot={"session": "old"})
    assert len(launches) == 1
    assert snapshots == [{"session": "old"}, {"session": "refreshed"}]
    assert _results(first) == _results(second)
    run_search("bilibili", session_snapshot={"session": "different-account"})
    assert snapshots[-1] == {"session": "different-account"}


def test_zhihu_browser_session_reused_and_failure_clears_it(monkeypatch):
    context = FakeBrowserContext(existing=[{"name": "d_c0", "value": "private-dc0"}])
    playwright = FakePlaywright()
    launches, closed = [], []
    async def launch(**kwargs):
        launches.append(True)
        return context
    playwright.chromium = SimpleNamespace(launch_persistent_context=launch)
    monkeypatch.setattr("playwright.async_api.async_playwright", lambda: playwright)
    monkeypatch.setattr("tools.browser_launcher.resolve_playwright_browser",
                        lambda: ("fake-browser", None, "test"))
    fail = False
    class Client:
        def __init__(self, **kwargs):
            pass
        async def pong(self):
            return True
        async def get(self, *args):
            if fail:
                raise RateLimitError("zhihu")
            return _ZH_FAKE_SEARCH_RESPONSE
        async def aclose(self):
            closed.append(True)
    monkeypatch.setattr("media_platform.zhihu.client.ZhiHuClient", Client)
    first, second = run_search("zhihu"), run_search("zhihu")
    assert _results(first) == _results(second)
    assert len(_results(first)) == 1
    assert len(launches) == 1
    assert len(closed) == 2
    assert context.closed
    assert "private-dc0" not in repr(first + second)
    fail = True
    run_search("zhihu")
    assert len(launches) == 1
    assert "zhihu" not in worker._search_session_snapshots
    assert len(closed) == 3


@pytest.mark.asyncio
async def test_bilibili_pagination_reuses_keys_but_refreshes_after_ttl(monkeypatch):
    from media_platform.bilibili.client import BilibiliClient
    calls = []
    async def request(**kwargs):
        calls.append(kwargs)
        return {"wbi_img": {"img_url": "https://example.test/img.png",
                            "sub_url": "https://example.test/sub.png"}}
    client = BilibiliClient(headers={}, playwright_page=None, cookie_dict={})
    monkeypatch.setattr(client, "request", request)
    now = [0.0]
    monkeypatch.setattr("media_platform.bilibili.client.time.monotonic", lambda: now[0])
    run = PaginationRun("bilibili", "测试", 40, PageState(), [], lambda r: None, lambda m: None)
    token = current_pagination.set(run)
    try:
        assert await client.get_wbi_keys() == ("img", "sub")
        assert await client.get_wbi_keys() == ("img", "sub")
        assert len(calls) == 1
        now[0] = 60
        await client.get_wbi_keys()
        assert len(calls) == 2
    finally:
        current_pagination.reset(token)
    # Other operations retain their existing key lookup behavior.
    await client.get_wbi_keys()
    assert len(calls) == 3


@pytest.mark.asyncio
@pytest.mark.parametrize("count, expected", [(None, "15"), (20, "20"), (100, "20"), (0, "1")])
async def test_douyin_client_sends_bounded_page_count(monkeypatch, count, expected):
    from media_platform.douyin.client import DouYinClient
    client = DouYinClient(headers={}, playwright_page=None, cookie_dict={})
    captured = []
    async def get(uri, params, **kwargs):
        captured.append(params)
        return {"status_code": 0, "data": []}
    monkeypatch.setattr(client, "get", get)
    await client.search_info_by_keyword("测试", **({"count": count} if count is not None else {}))
    assert captured[0]["count"] == expected
