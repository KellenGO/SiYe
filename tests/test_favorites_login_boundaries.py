"""收藏同步的身份判断，全部使用虚构页面与隔离数据库。"""
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
import json

import pytest

import config
from aggregate_search import worker
from aggregate_search.favorites_sync import (
    DOUYIN_UNAVAILABLE, FavoritesUnavailableError, XhsFavoritesSource,
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


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["auto", "reset"])
async def test_douyin_paged_sync_fails_before_opening_browser(monkeypatch, mode):
    errors = []
    monkeypatch.setattr(worker, "emit_status", lambda *args: None)
    monkeypatch.setattr(worker, "emit_error", lambda *args: errors.append(args))
    monkeypatch.setattr(worker, "emit_done", lambda *args: None)
    monkeypatch.setattr("main.CrawlerFactory.create_crawler", lambda **kwargs: pytest.fail("browser should not start"))
    await worker._run_favorites("job", "douyin", 20, mode)
    assert errors[0][2] == "failed"
    assert DOUYIN_UNAVAILABLE in errors[0][3]


@pytest.mark.asyncio
async def test_unsupported_sync_does_not_change_login_state(monkeypatch, tmp_path):
    job = _Job(FavoritesJobRequest(platforms=["douyin"], sync_mode="auto"))
    def event(kind, data):
        return ("MC_AGG_EVENT\t" + json.dumps({"event": kind, "job_id": job.job_id,
                "platform": "douyin", "data": data}) + "\n").encode()
    proc = SimpleNamespace(
        stdin=SimpleNamespace(write=Mock(), drain=AsyncMock(), close=Mock()),
        stdout=SimpleNamespace(readline=AsyncMock(side_effect=[
            event("error", {"type": "failed", "message": DOUYIN_UNAVAILABLE}),
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
