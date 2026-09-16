import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from aggregate_search import favorite_metrics as metrics
from aggregate_search.adapters import BilibiliAdapter
from api.schemas.favorites import FavoritesJobRequest
from api.services.favorites_job_manager import _Job


@pytest.mark.asyncio
async def test_bili_detail_shape_zero_and_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(metrics, "REQUEST_INTERVAL", 0)
    client = SimpleNamespace(get_video_info=AsyncMock(return_value={"View": {"stat": {
        "view": 120, "like": 0, "reply": 3, "favorite": 5, "coin": 2}}}))
    rows = [{"bvid": "BV1", "_collection_name": "first"},
            {"bvid": "BV1", "_collection_name": "second"}]
    updates = []
    await metrics.enrich_favorites("bilibili", client, rows, updates.extend, cache_dir=tmp_path)
    assert client.get_video_info.await_count == 1
    assert updates[0]["_favorite_metrics"] == {
        "view_count": 120, "like_count": 0, "comment_count": 3, "collect_count": 5, "coin_count": 2}
    assert updates[0]["_metrics_status"] == "complete"
    await metrics.enrich_favorites("bilibili", client, rows, updates.extend, cache_dir=tmp_path)
    assert client.get_video_info.await_count == 1
    assert updates[0]["_metrics_updated_at"] == updates[1]["_metrics_updated_at"]
    assert "first" not in next(tmp_path.glob("*.json")).read_text()


@pytest.mark.asyncio
async def test_xhs_token_and_missing_token(tmp_path):
    client = SimpleNamespace(get_note_by_id=AsyncMock(return_value={"interact_info": {
        "liked_count": "12", "comment_count": "0", "collected_count": "4"}}))
    updates = []
    await metrics.enrich_favorites("xhs", client, [
        {"note_id": "one", "xsec_token": "secret", "xsec_source": "pc_user"},
        {"note_id": "two"}], updates.extend, cache_dir=tmp_path)
    client.get_note_by_id.assert_awaited_once_with("one", "pc_user", "secret")
    assert updates[0]["_favorite_metrics"]["comment_count"] == 0
    assert updates[1]["_metrics_status"] == "unavailable"
    assert "secret" not in next(tmp_path.glob("*.json")).read_text()


@pytest.mark.asyncio
async def test_xhs_formatted_detail_counters_are_marked_approximate(tmp_path):
    client = SimpleNamespace(get_note_by_id=AsyncMock(return_value={"interact_info": {
        "liked_count": "10万+", "comment_count": "7805", "collected_count": "12.3万"}}))
    updates = []
    await metrics.enrich_favorites("xhs", client, [{"note_id": "n", "xsec_token": "t"}],
                                   updates.extend, cache_dir=tmp_path)
    assert updates[0]["_favorite_metrics"] == {
        "like_count": 100000, "comment_count": 7805, "collect_count": 123000}
    assert updates[0]["_metrics_approximate"] == ["collect_count", "like_count"]
    assert updates[0]["_metrics_status"] == "complete"


@pytest.mark.asyncio
async def test_zhihu_raw_detail_does_not_use_question_views():
    client = SimpleNamespace(get=AsyncMock(return_value={"voteup_count": 8, "comment_count": 0,
        "favlists_count": 7, "question": {"visit_count": 9000}}))
    result = await metrics.fetch_metrics("zhihu", client, {"type": "answer", "id": 12})
    assert result == {"like_count": 8, "comment_count": 0, "collect_count": 7}
    assert client.get.call_args.args[0] == "/api/v4/answers/12"
    client.get.return_value = {"play_count": 25, "visit_count": 100}
    result = await metrics.fetch_metrics("zhihu", client, {"type": "zvideo", "id": 13})
    assert result == {"view_count": 25}


@pytest.mark.asyncio
async def test_failure_stops_detail_requests_and_does_not_cache(tmp_path):
    from base.exceptions import RateLimitError
    client = SimpleNamespace(get_video_info=AsyncMock(side_effect=RateLimitError("bilibili")))
    with pytest.raises(RateLimitError):
        await metrics.enrich_favorites("bilibili", client, [{"bvid": "a"}, {"bvid": "b"}],
                                       lambda _: None, cache_dir=tmp_path)
    assert client.get_video_info.await_count == 1
    assert not list(tmp_path.iterdir())


@pytest.mark.asyncio
async def test_expired_and_corrupt_cache_refresh(tmp_path, monkeypatch):
    monkeypatch.setattr(metrics, "CACHE_TTL", 0)
    client = SimpleNamespace(get_video_info=AsyncMock(return_value={"View": {"stat": {"like": 3}}}))
    for _ in range(2):
        await metrics.enrich_favorites("bilibili", client, [{"bvid": "b"}], lambda _: None,
                                       cache_dir=tmp_path)
    assert client.get_video_info.await_count == 2
    next(tmp_path.glob("*.json")).write_text("broken")
    await metrics.enrich_favorites("bilibili", client, [{"bvid": "b"}], lambda _: None, cache_dir=tmp_path)
    assert client.get_video_info.await_count == 3


@pytest.mark.asyncio
async def test_a_full_bilibili_folder_fits_inside_the_budget(tmp_path, monkeypatch):
    """B站单个收藏夹最多 100 条：预算必须覆盖得住。

    旧实现是串行 + 2 秒间隔 + 120 秒总预算，100 条光等间隔就要约 198 秒，
    所以数据库里必然剩下一半 failed。
    """
    monkeypatch.setattr(metrics, "REQUEST_INTERVAL", 0)
    client = SimpleNamespace(get_video_info=AsyncMock(return_value={"View": {"stat": {
        "view": 1, "like": 2, "reply": 3, "favorite": 4, "coin": 5}}}))
    rows = [{"bvid": f"BV{index}"} for index in range(100)]
    updates = []
    await metrics.enrich_favorites("bilibili", client, rows, updates.extend, cache_dir=tmp_path)
    assert client.get_video_info.await_count == 100
    assert len(updates) == 100
    assert all(row["_metrics_status"] == "complete" for row in updates)


@pytest.mark.asyncio
async def test_budget_exhaustion_is_not_a_platform_failure(tmp_path, monkeypatch):
    """预算用尽时把剩下的留给下次同步，不再把整个平台判成「同步超时」。"""
    monkeypatch.setattr(metrics, "REQUEST_INTERVAL", 0)
    monkeypatch.setattr(metrics, "ENRICHMENT_TIMEOUT", 0)
    client = SimpleNamespace(get_video_info=AsyncMock(return_value={"View": {"stat": {"like": 1}}}))
    updates = []
    await metrics.enrich_favorites("bilibili", client, [{"bvid": "BV1"}, {"bvid": "BV2"}],
                                   updates.extend, cache_dir=tmp_path)
    assert updates == []
    assert client.get_video_info.await_count == 0


@pytest.mark.asyncio
async def test_default_cache_sits_next_to_the_library_not_the_program_dir(tmp_path, monkeypatch):
    """缓存跟着收藏库走：换版本 / 源码与发行包之间不必重新打一遍详情请求。"""
    monkeypatch.setattr(metrics, "REQUEST_INTERVAL", 0)
    monkeypatch.setattr(metrics, "library_data_root", lambda: tmp_path)
    client = SimpleNamespace(get_video_info=AsyncMock(return_value={"View": {"stat": {"like": 1}}}))
    await metrics.enrich_favorites("bilibili", client, [{"bvid": "BV1"}], lambda _: None)
    assert (tmp_path / ".cache" / "favorite_metrics").is_dir()


def test_job_updates_same_content_and_keeps_partial_results():
    job = _Job(FavoritesJobRequest(platforms=["bilibili"]))
    row = BilibiliAdapter().adapt([{"bvid": "BV1", "title": "one"}])[0].model_dump()
    job.upsert("bilibili", row)
    job.upsert("bilibili", {**row, "metrics": {"like_count": 0}, "collection_names": ["A", "B"]})
    assert len(job.items["bilibili"]) == 1
    assert job.items["bilibili"][0].metrics == {"like_count": 0}
    from datetime import datetime, timezone
    job.completed_at = datetime.now(timezone.utc)
    job.platforms["bilibili"].status = "rate_limited"
    assert job.response().overall == "partial"


@pytest.mark.asyncio
async def test_worker_streams_before_details_and_retains_list_on_failure(monkeypatch):
    from aggregate_search import worker
    from main import CrawlerFactory
    events = []

    class Crawler:
        async def start(self):
            sink = self.runtime_options.result_sink
            row = {"bvid": "BV1", "title": "one", "_collection_name": "A"}
            sink([row])
            assert events[-1]["metrics_status"] == "pending"
            sink([{**row, "_favorite_metrics": {"like_count": 0}, "_metrics_status": "partial"}])
            sink([{**row, "_collection_name": "B"}])
            sink([{"bvid": "BV2", "title": "two"}])
            raise asyncio.TimeoutError()

    monkeypatch.setattr(CrawlerFactory, "create_crawler", lambda **_: Crawler())
    monkeypatch.setattr(worker, "_cleanup_crawler", AsyncMock())
    monkeypatch.setattr(worker, "emit_result", lambda _j, _p, data: events.append(data))
    monkeypatch.setattr(worker, "emit_status", lambda *_: None)
    monkeypatch.setattr(worker, "emit_error", lambda *_: None)
    monkeypatch.setattr(worker, "emit_done", lambda *_: None)
    # The worker config normally lives in a separate process.
    for name in ("PLATFORM", "KEYWORDS", "CRAWLER_TYPE", "CRAWLER_MAX_NOTES_COUNT", "ENABLE_CDP_MODE",
                 "CDP_CONNECT_EXISTING", "HEADLESS", "SAVE_LOGIN_STATE", "LOGIN_TYPE", "ENABLE_IP_PROXY", "MAX_CONCURRENCY_NUM"):
        monkeypatch.setattr(worker.config, name, getattr(worker.config, name))
    await worker._run_favorites("job", "bilibili", 20)
    assert events[2]["metrics"] == {"like_count": 0}
    assert events[2]["collection_names"] == ["A", "B"]
    assert events[-1]["content_id"] == "BV2"
    assert events[-1]["metrics_status"] == "failed"


@pytest.mark.asyncio
async def test_cached_details_preserve_fresh_list_counts_and_precision(monkeypatch):
    from aggregate_search import worker
    from main import CrawlerFactory
    events = []

    class Crawler:
        async def start(self):
            self.runtime_options.result_sink([{
                "note_id": "n1", "display_title": "note", "interact_info": {"liked_count": "10万+"},
                "_metrics_status": "complete", "_metrics_cached": True,
                "_favorite_metrics": {"like_count": 90000, "collect_count": 20000, "comment_count": 0},
                "_metrics_approximate": ["collect_count"],
            }])

    monkeypatch.setattr(CrawlerFactory, "create_crawler", lambda **_: Crawler())
    monkeypatch.setattr(worker, "_cleanup_crawler", AsyncMock())
    monkeypatch.setattr(worker, "emit_result", lambda _j, _p, data: events.append(data))
    monkeypatch.setattr(worker, "emit_status", lambda *_: None)
    monkeypatch.setattr(worker, "emit_error", lambda *_: None)
    monkeypatch.setattr(worker, "emit_done", lambda *_: None)
    for name in ("PLATFORM", "KEYWORDS", "CRAWLER_TYPE", "CRAWLER_MAX_NOTES_COUNT", "ENABLE_CDP_MODE",
                 "CDP_CONNECT_EXISTING", "HEADLESS", "SAVE_LOGIN_STATE", "LOGIN_TYPE", "ENABLE_IP_PROXY", "MAX_CONCURRENCY_NUM"):
        monkeypatch.setattr(worker.config, name, getattr(worker.config, name))
    await worker._run_favorites("job", "xhs", 20)
    assert events[0]["metrics"] == {"like_count": 100000, "collect_count": 20000, "comment_count": 0}
    assert events[0]["metrics_approximate"] == ["collect_count", "like_count"]
