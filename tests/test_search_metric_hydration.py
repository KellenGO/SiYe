import asyncio
import time
from unittest.mock import AsyncMock

import pytest

from aggregate_search.hydration import metric_candidates
from aggregate_search.models import UnifiedSearchResult, GroupedSource
from api.services.result_hydration import ResultHydrator
from api.services.search_job_manager import _ActiveJob


def item(index=1, platform="bilibili"):
    return UnifiedSearchResult(platform=platform, content_id=f"BV{index}" if platform == "bilibili" else str(index),
        content_type="video" if platform == "bilibili" else "answer", title="标题",
        snippet="这是已经足够完整的简介，不应该影响指标是否能够补全。", url="https://example.test",
        metrics={"like_count": 10})


def test_metrics_include_later_results_with_good_snippets_and_keep_fresh_partial():
    rows = [item(i) for i in range(20)]
    assert metric_candidates(rows) == rows
    rows[0].metrics_status = "partial"
    rows[0].metrics_updated_at = time.time()
    assert metric_candidates(rows) == rows[1:]
    rows[0].metrics_updated_at = time.time() - 21601
    assert metric_candidates(rows) == rows


@pytest.mark.asyncio
async def test_search_hydrates_all_and_reuses_detail_description(monkeypatch, tmp_path):
    from aggregate_search import favorite_metrics
    monkeypatch.setattr(favorite_metrics, "library_data_root", lambda: tmp_path)
    monkeypatch.setattr(favorite_metrics, "REQUEST_INTERVAL", 0)
    client = type("Client", (), {})()
    client.get_video_info = AsyncMock(return_value={"View": {
        "stat": {"view": 100, "like": 11, "reply": 0, "favorite": 8, "coin": 7}, "desc": "详情里的正文简介。"}})
    hydrator = ResultHydrator()
    monkeypatch.setattr(hydrator, "_get_bilibili", AsyncMock(return_value=client))
    rows = [item(i) for i in range(15)]
    updates = []
    await hydrator.hydrate_metrics(rows, asyncio.Event(), lambda r: updates.append(r.model_dump()))
    assert client.get_video_info.await_count == 15
    assert all(r.metrics["coin_count"] == 7 and r.metrics["comment_count"] == 0 for r in rows)
    assert updates[0]["metrics_status"] == "pending"
    assert rows[-1].metrics_status == "complete"
    assert await hydrator.fetch_snippet(rows[0]) == "详情里的正文简介。"
    assert client.get_video_info.await_count == 15


@pytest.mark.asyncio
async def test_search_rate_limit_stops_platform_and_preserves_list(monkeypatch, tmp_path):
    from aggregate_search import favorite_metrics
    from base.exceptions import RateLimitError
    monkeypatch.setattr(favorite_metrics, "library_data_root", lambda: tmp_path)
    client = type("Client", (), {})()
    client.get_video_info = AsyncMock(side_effect=RateLimitError("bilibili"))
    hydrator = ResultHydrator()
    monkeypatch.setattr(hydrator, "_get_bilibili", AsyncMock(return_value=client))
    rows = [item(i) for i in range(3)]
    await hydrator.hydrate_metrics(rows, asyncio.Event(), lambda _: None)
    assert client.get_video_info.await_count == 1
    assert all(r.metrics_status == "failed" and r.metrics == {"like_count": 10} for r in rows)
    assert await hydrator.fetch_snippet(rows[0]) is None
    assert client.get_video_info.await_count == 1


@pytest.mark.asyncio
async def test_bili_course_does_not_block_later_videos(monkeypatch, tmp_path):
    from aggregate_search import favorite_metrics
    monkeypatch.setattr(favorite_metrics, "library_data_root", lambda: tmp_path)
    client = type("Client", (), {})()
    client.get_video_info = AsyncMock(return_value={"View": {"stat": {"coin": 12}}})
    hydrator = ResultHydrator()
    monkeypatch.setattr(hydrator, "_get_bilibili", AsyncMock(return_value=client))
    course = item()
    course.content_id = "189821697"
    course.url = "https://www.bilibili.com/cheese/play/ss189821697"
    video = item(2)
    await hydrator.hydrate_metrics([course, video], asyncio.Event(), lambda _: None)
    assert course.metrics_status == "unavailable"
    assert video.metrics["coin_count"] == 12
    client.get_video_info.assert_awaited_once_with(bvid="BV2")


@pytest.mark.asyncio
async def test_search_zhihu_raw_counters_and_cancellation(monkeypatch, tmp_path):
    from aggregate_search import favorite_metrics
    monkeypatch.setattr(favorite_metrics, "library_data_root", lambda: tmp_path)
    monkeypatch.setattr("api.services.result_hydration.get_session_snapshot", lambda _: {"d_c0": "test"})
    client = type("Client", (), {})()
    client.get = AsyncMock(return_value={"voteup_count": 12, "comment_count": 0, "favlists_count": 3, "read_count": 200})
    hydrator = ResultHydrator()
    monkeypatch.setattr(hydrator, "_get_zhihu", AsyncMock(return_value=client))
    row = item(platform="zhihu")
    await hydrator.hydrate_metrics([row], asyncio.Event(), lambda _: None)
    assert row.metrics == {"like_count": 12, "comment_count": 0, "collect_count": 3, "view_count": 200}
    cancelled = asyncio.Event()
    cancelled.set()
    await hydrator.hydrate_metrics([item(2, "zhihu")], cancelled, lambda _: None)
    assert client.get.await_count == 1


def test_metric_update_reaches_grouped_source_and_platform_tab():
    job = _ActiveJob("job", "keyword", ["bilibili", "xhs"], 20)
    row = item()
    job.platform_results["bilibili"] = [row]
    group = item(platform="xhs")
    group.grouped_sources = [GroupedSource.from_result(group), GroupedSource.from_result(row)]
    job._final_results = [group]
    row.metrics = {"coin_count": 99, "comment_count": 0}
    row.metrics_status = "complete"
    job.update_metrics(row)
    assert group.grouped_sources[1].metrics == {"coin_count": 99, "comment_count": 0}
    assert job.platform_results["bilibili"][0].metrics["coin_count"] == 99
