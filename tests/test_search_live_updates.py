"""The first real item must reach waiting clients before workers finish."""
import asyncio

import httpx
import pytest
from fastapi import FastAPI

from api.services.search_job_manager import SearchJobManager, _ActiveJob
from aggregate_search.models import UnifiedSearchResult


def item(platform="xhs", content_id="first"):
    return UnifiedSearchResult(platform=platform, content_id=content_id,
                               title="首条内容", url="https://example.test/item")


@pytest.fixture
def live_job():
    manager = SearchJobManager()
    job = _ActiveJob("live-job", "测试", ["xhs", "douyin"], 20)
    manager._active_job = job
    return manager, job


@pytest.mark.asyncio
async def test_first_item_wakes_all_readers_while_both_platforms_still_running(live_job):
    manager, job = live_job
    for p in job.platforms:
        job.set_platform_status(p, "running")
    tasks = [asyncio.create_task(manager.get_job(job.job_id,
             after_revision=job.revision, wait_seconds=15)) for _ in range(2)]
    await asyncio.sleep(0)
    assert all(not t.done() for t in tasks)
    job.add_result("xhs", item())
    responses = await asyncio.wait_for(asyncio.gather(*tasks), 1)
    for response in responses:
        assert response.overall == "running"
        assert response.completed_at is None
        assert len(response.results) == 1
        assert all(s.status == "running" for s in response.platforms.values())


@pytest.mark.asyncio
async def test_update_before_subscribe_is_not_lost(live_job):
    manager, job = live_job
    revision = job.revision
    job.add_result("xhs", item())
    response = await asyncio.wait_for(manager.get_job(job.job_id,
        after_revision=revision, wait_seconds=15), 1)
    assert len(response.results) == 1


@pytest.mark.asyncio
async def test_timeout_and_cancelled_reader_do_not_cancel_search(live_job):
    manager, job = live_job
    response = await manager.get_job(job.job_id, after_revision=0, wait_seconds=0.01)
    assert response.revision == 0
    task = asyncio.create_task(manager.get_job(job.job_id, after_revision=0, wait_seconds=15))
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    job.add_result("xhs", item())
    assert not job._cancelled
    assert len((await manager.get_job(job.job_id)).results) == 1


@pytest.mark.asyncio
async def test_terminal_and_hydration_completion_wake_reader(live_job):
    manager, job = live_job
    for complete_hydration in (False, True):
        task = asyncio.create_task(manager.get_job(job.job_id,
            after_revision=job.revision, wait_seconds=15))
        await asyncio.sleep(0)
        if complete_hydration:
            job.hydration_status = "completed"
            job.notify_changed()
        else:
            job.finalize()
            job.hydration_status = "running"
        response = await asyncio.wait_for(task, 1)
        assert response.completed_at
    response = await asyncio.wait_for(manager.get_job(job.job_id,
        after_revision=job.revision, wait_seconds=15), 1)
    assert response.hydration_status == "completed"


@pytest.mark.asyncio
async def test_real_route_waits_for_item_and_validates_bounds(live_job, monkeypatch):
    from api.routers import search
    manager, job = live_job
    monkeypatch.setattr(search, "search_job_manager", manager)
    app = FastAPI()
    app.include_router(search.search_router)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        request = asyncio.create_task(client.get("/api/search/jobs/live-job",
            params={"after_revision": job.revision, "wait_seconds": 15}))
        await asyncio.sleep(0)
        job.add_result("xhs", item())
        response = await asyncio.wait_for(request, 1)
        assert response.status_code == 200
        assert response.json()["results"][0]["content_id"] == "first"
        assert (await client.get("/api/search/jobs/missing")).status_code == 404
        for params in ({"wait_seconds": 16}, {"after_revision": -1}):
            assert (await client.get("/api/search/jobs/live-job", params=params)).status_code == 422


def test_research_preview_does_not_hide_target_matching_an_existing_source(live_job):
    from api.services.search_exploration import Exploration
    _, job = live_job
    job.exploration = Exploration(job)
    old = item("douyin", "old")
    job.exploration.results["douyin"] = [old]
    job.exploration.owners["douyin:old"] = 1
    job.continuation = job.replaces_platforms = True
    job.add_result("xhs", item())
    assert [r.content_id for r in job.response_results()] == ["first"]
