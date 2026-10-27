"""收藏同步的身份判断，全部使用虚构页面与隔离数据库。"""
from dataclasses import dataclass
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
import json

import pytest

import config
from aggregate_search import worker
from aggregate_search.favorites_sync import (
    FavoritesSyncError, FavoritesUnavailableError, XhsFavoritesSource,
    synchronize_favorites,
)
from api.services.remote_favorites_store import RemoteFavoritesStore
from api.schemas.favorites import FavoritesJobRequest
from api.services.favorites_job_manager import FavoritesJobManager, _Job
from media_platform.xhs.core import XiaoHongShuCrawler
from media_platform.xhs.exception import XhsRateLimitError
from tests.fixtures.browser import FakeBrowserContext, FakePage, FakePlaywright


@pytest.mark.asyncio
async def test_xhs_sync_uses_page_identity_and_accepts_empty_collection(tmp_path):
    client = SimpleNamespace(
        playwright_page=FakePage(evaluate_result="self-id"),
        get_collected_notes=AsyncMock(return_value={"notes": [], "has_more": False}),
    )
    store = RemoteFavoritesStore(tmp_path / "library.db")
    source = XhsFavoritesSource(client, interval=0)
    assert await synchronize_favorites(source, store, "auto", lambda *_: None) == []
    assert client.get_collected_notes.call_args_list[0].args == ("", 20, "self-id")
    assert store.archive_summary()["accounts"][0]["account"] == "xhs:self-id"


@pytest.mark.asyncio
async def test_xhs_unknown_identity_is_failure_not_login_required():
    source = XhsFavoritesSource(SimpleNamespace(playwright_page=FakePage(evaluate_result="")), interval=0)
    with pytest.raises(FavoritesUnavailableError) as exc:
        await source.identity()
    assert worker._classify_platform_error(exc.value) == "failed"
    assert "无法识别" in worker._safe_error_message(exc.value)


@dataclass
class _DouyinClient:
    """抖音分页同步的最小 client 替身：页面内读取已由 collection 模块测过。"""

    playwright_page: object
    profile: dict
    pages: list

    async def get(self, uri, params=None, headers=None):
        return self.profile

    async def get_collected_awemes(self, cursor=0, count=20):
        for row in self.pages:
            if row["cursor"] == cursor:
                return row["body"]
        raise AssertionError(f"unexpected cursor {cursor}")


def _douyin_pages(rows_per_page):
    pages = []
    cursor = 0
    for index, size in enumerate(rows_per_page):
        last = index == len(rows_per_page) - 1
        body = {
            "status_code": 0,
            "has_more": 0 if last else 1,
            "cursor": 0 if last else cursor + size,
            "aweme_list": [
                {"aweme_id": f"{cursor + offset}", "desc": f"item {cursor + offset}",
                 "create_time": 1700000000 + cursor + offset,
                 "video": {"cover": {"url_list": ["https://p3.douyinpic.com/x.jpg"]}}}
                for offset in range(size)
            ],
        }
        pages.append({"cursor": cursor, "body": body})
        cursor += size
    return pages


@pytest.mark.asyncio
async def test_douyin_sync_uses_verified_identity_and_paginates(tmp_path):
    client = _DouyinClient(
        playwright_page=FakePage(),
        profile={"status_code": 0, "user": {"sec_uid": "MS4wLjABAAAAowner"}},
        pages=_douyin_pages([2, 1]),
    )
    store = RemoteFavoritesStore(tmp_path / "library.db")
    from aggregate_search.favorites_sync import DouyinFavoritesSource

    assert await synchronize_favorites(
        DouyinFavoritesSource(client, interval=0), store, "auto", lambda *_: None) == []
    summary = store.archive_summary()
    assert summary["platforms"]["douyin"]["status"] == "succeeded"
    assert summary["accounts"][0]["account"] == "douyin:MS4wLjABAAAAowner"
    assert store.archive_page(limit=10)["total"] == 3


@pytest.mark.asyncio
async def test_douyin_identity_without_owner_is_login_required():
    from base.exceptions import LoginRequiredError
    from aggregate_search.favorites_sync import DouyinFavoritesSource

    client = _DouyinClient(playwright_page=FakePage(),
                           profile={"status_code": 8, "status_msg": "用户未登录"},
                           pages=[])
    with pytest.raises(LoginRequiredError):
        await DouyinFavoritesSource(client, interval=0).identity()


@pytest.mark.asyncio
async def test_douyin_collection_unavailable_is_not_login_required(tmp_path):
    """收藏页没就绪是可重试失败，绝不能把账号标成登录失效。"""
    from media_platform.douyin.collection import CollectionUnavailableError
    from aggregate_search.favorites_sync import DouyinFavoritesSource

    client = _DouyinClient(
        playwright_page=FakePage(),
        profile={"status_code": 0, "user": {"sec_uid": "MS4wLjABAAAAowner"}},
        pages=[])
    client.get_collected_awemes = AsyncMock(
        side_effect=CollectionUnavailableError("抖音收藏页没有就绪，暂不能同步收藏"))
    source = DouyinFavoritesSource(client, interval=0)
    with pytest.raises(CollectionUnavailableError) as exc:
        await synchronize_favorites(
            source, RemoteFavoritesStore(tmp_path / "library.db"), "auto",
            lambda *_: None)
    assert worker._classify_platform_error(exc.value) == "failed"


@pytest.mark.asyncio
async def test_unsupported_sync_does_not_change_login_state(monkeypatch, tmp_path):
    job = _Job(FavoritesJobRequest(platforms=["douyin"], sync_mode="auto"))
    def event(kind, data):
        return ("MC_AGG_EVENT\t" + json.dumps({"event": kind, "job_id": job.job_id,
                "platform": "douyin", "data": data}) + "\n").encode()
    proc = SimpleNamespace(
        stdin=SimpleNamespace(write=Mock(), drain=AsyncMock(), close=Mock()),
        stdout=SimpleNamespace(readline=AsyncMock(side_effect=[
            event("error", {"type": "failed", "message": "抖音收藏页没有就绪，暂不能同步收藏"}),
            event("done", None)])),
    )
    monkeypatch.setattr("api.services.favorites_job_manager.spawn_worker", AsyncMock(return_value=proc))
    monkeypatch.setattr("api.services.favorites_job_manager.terminate_worker", AsyncMock())
    monkeypatch.setattr("api.services.favorites_job_manager.drain_stderr_to_eof", AsyncMock())
    monkeypatch.setattr("api.services.favorites_job_manager.record_usage", lambda *args: True)
    mark = Mock()
    monkeypatch.setattr("api.services.favorites_job_manager.mark_login_required_from_search", mark)
    monkeypatch.setattr("api.services.favorites_job_manager.get_remote_favorites_store",
                        lambda: RemoteFavoritesStore(tmp_path / "library.db"))
    await FavoritesJobManager()._run_platform(job, "douyin")
    assert job.platforms["douyin"].status == "failed"
    mark.assert_not_called()


@pytest.mark.asyncio
async def test_xhs_favorites_preflight_propagates_rate_limit(monkeypatch):
    crawler = XiaoHongShuCrawler()
    context = FakeBrowserContext(page=FakePage())
    client = SimpleNamespace(pong=AsyncMock(side_effect=XhsRateLimitError(http_status=461)))
    monkeypatch.setattr("media_platform.xhs.core.async_playwright", FakePlaywright)
    monkeypatch.setattr(config, "ENABLE_CDP_MODE", False)
    monkeypatch.setattr(config, "CRAWLER_TYPE", "favorites")
    monkeypatch.setattr(crawler, "launch_browser", AsyncMock(return_value=context))
    monkeypatch.setattr(crawler, "create_xhs_client", AsyncMock(return_value=client))
    monkeypatch.setattr(crawler, "_login_fail_fast", lambda: True)
    with pytest.raises(XhsRateLimitError):
        await crawler.start()
    client.pong.assert_awaited_once_with(raise_on_error=True)
