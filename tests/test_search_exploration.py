"""Offline pagination and exploration regressions; never contacts a platform."""
import asyncio
from copy import deepcopy

import pytest
import pytest_asyncio

import config
from aggregate_search.models import UnifiedSearchResult, WorkerRequest
from aggregate_search.pagination import PageState, PaginationRun, current_pagination, allow_client_retry, check_search_http_status
from api.schemas.search import SearchJobRequestSchema
from api.services import search_job_manager as sjm, result_cache


def result(platform, content_id, title=None):
    return UnifiedSearchResult(platform=platform, content_id=str(content_id),
        title=title or f"独立素材 {content_id}", author="测试作者",
        url=f"https://example.test/{platform}/{content_id}")


def page(platform, ids, more=True):
    if platform == "xhs":
        return {"has_more": more, "items": [{"id": str(i), "note_card": {"display_title": f"素材 {i}"}} for i in ids]}
    if platform == "bilibili":
        return {"numPages": 99 if more else 1, "result": [{"bvid": str(i), "title": f"素材 {i}"} for i in ids]}
    if platform == "douyin":
        return {"status_code": 0, "has_more": more, "cursor": 30, "extra": {"logid": "search-token"},
                "data": [{"aweme_info": {"aweme_id": str(i), "desc": f"素材 {i}"}} for i in ids]}
    return {"paging": {"is_end": not more}, "data": [{"type": "search_result",
        "object": {"id": str(i), "type": "article", "title": f"素材 {i}"}} for i in ids]}


class Client:
    def __init__(self, responses):
        self.responses, self.calls = list(responses), []

    async def request_page(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return deepcopy(response)

    get_note_by_keyword = search_video_by_keyword = search_info_by_keyword = get = request_page


def run_for(platform, limit, state=None, seen=()):
    emitted, checkpoints = [], []
    run = PaginationRun(platform, "素材", limit, state or PageState(), list(seen), emitted.append, checkpoints.append)
    return run, emitted, checkpoints


@pytest.fixture(autouse=True)
def no_page_delay(monkeypatch):
    monkeypatch.setattr(config, "CRAWLER_MAX_SLEEP_SEC", 0)


@pytest.mark.asyncio
@pytest.mark.parametrize("platform", ["xhs", "bilibili", "douyin", "zhihu"])
async def test_all_platforms_keep_page_tail_and_resume_position(platform):
    client = Client([page(platform, [1, 2, 3]), page(platform, [3, 4], more=False)])
    first, emitted, checkpoints = run_for(platform, 2)
    assert await first.run(client) == 2
    assert [r["content_id"] for r in emitted] == ["1", "2"]
    state = PageState.model_validate(checkpoints[-1]["pagination"])
    assert [r.content_id for r in state.pending] == ["3"]
    second, emitted2, _ = run_for(platform, 2, state, ["1", "2"])
    assert await second.run(client) == 2
    assert [r["content_id"] for r in emitted2] == ["3", "4"]
    assert second.duplicates == 1
    assert len(client.calls) == 2
    args, kwargs = client.calls[1]
    if platform in ("xhs", "bilibili"):
        assert kwargs["page"] == 2
    elif platform == "douyin":
        assert kwargs["offset"] == 30 and kwargs["search_id"] == "search-token"
    else:
        assert args[1]["offset"] == 20
    assert second.state.exhausted


@pytest.mark.asyncio
async def test_tail_needs_no_request_even_on_exhausted_page():
    run, emitted, _ = run_for("xhs", 1, PageState(exhausted=True, pending=[result("xhs", "tail")]))
    client = Client([])
    await run.run(client)
    assert emitted[0]["content_id"] == "tail" and client.calls == []


@pytest.mark.asyncio
async def test_duplicate_only_pages_stop_at_request_budget():
    run, emitted, _ = run_for("xhs", 40, seen=["old"])
    client = Client([page("xhs", ["old"])] * 8)
    await run.run(client)
    assert emitted == [] and len(client.calls) == 4
    assert run.requests == run.duplicates == 4
    assert run.state.page == 5 and not run.state.exhausted


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [ValueError("unavailable"), asyncio.CancelledError()])
async def test_error_or_cancel_preserves_last_confirmed_cursor(failure):
    run, emitted, checkpoints = run_for("xhs", 4)
    client = Client([page("xhs", [1, 2]), failure])
    with pytest.raises(type(failure)):
        await run.run(client)
    assert len(emitted) == 2
    state = PageState.model_validate(checkpoints[-1]["pagination"])
    assert state.page == 2 and not state.exhausted
    assert checkpoints[-1]["page_requests"] == 2
    assert not run.fetching


@pytest.mark.asyncio
async def test_context_limits_implicit_retries_only_during_fetch():
    run, _, _ = run_for("xhs", 1)
    token = current_pagination.set(run)
    try:
        assert allow_client_retry()
        run.fetching = True
        assert not allow_client_retry()
    finally:
        current_pagination.reset(token)
    assert allow_client_retry()


@pytest.mark.parametrize("status", [403, 429, 461, 471])
def test_explicit_http_limits_stop_before_body_parsing(status):
    from base.exceptions import RateLimitError
    run, _, _ = run_for("xhs", 1)
    token = current_pagination.set(run)
    try:
        check_search_http_status(status)  # Outside pagination fetch: legacy policy.
        run.fetching = True
        with pytest.raises(RateLimitError):
            check_search_http_status(status)
    finally:
        current_pagination.reset(token)


def test_real_worker_dispatch_pagination_and_no_fallback_on_rate_limit(monkeypatch):
    from base.exceptions import RateLimitError
    from aggregate_search import worker
    from tests.test_worker_fast_path import _FakeCrawler, _patch_factory, _run_capture, _results
    async def paginated_search(crawler):
        await current_pagination.get().run(Client([page("xhs", [1, 2, 3])]))
    crawler = _FakeCrawler(fp_search=paginated_search)
    _patch_factory(monkeypatch, crawler)
    events = _run_capture(worker.run_worker, "j1", "search", "xhs", "素材", 2,
        session_snapshot={"web_session": "fake"}, fast_path=True, pagination={})
    assert [r["content_id"] for r in _results(events)] == ["1", "2"]
    checkpoints = [e.data for e in events if e.event == "metrics" and "pagination" in e.data]
    assert checkpoints[-1]["pagination"]["pending"][0]["content_id"] == "3"
    assert not crawler.browser_path_used and current_pagination.get() is None
    async def rate_limited(crawler):
        await current_pagination.get().run(Client([RateLimitError("xhs")]))
    crawler.fp_search = rate_limited
    events = _run_capture(worker.run_worker, "j2", "search", "xhs", "素材", 2,
        session_snapshot={"web_session": "fake"}, fast_path=True, pagination={})
    assert not crawler.browser_path_used
    assert any(e.event == "error" and e.data["type"] == "rate_limited" for e in events)
    assert current_pagination.get() is None


@pytest_asyncio.fixture
async def manager(monkeypatch):
    manager = sjm.SearchJobManager()
    result_cache.clear()
    monkeypatch.setattr(sjm, "hydration_candidates", lambda results: [])
    yield manager
    await manager.cleanup()
    result_cache.clear()


async def search(manager, **kwargs):
    await manager.create_job(SearchJobRequestSchema(keyword="素材", platforms=kwargs.pop("platforms", ["xhs"]), **kwargs))
    job = manager._active_job
    await job.task
    return await manager.get_job(job.job_id)


def install_worker(manager, monkeypatch, fail_platform=None):
    calls = []
    async def worker(job, platform):
        req = WorkerRequest.model_validate_json(manager._build_request_json(job, platform))
        calls.append(req)
        start = req.pagination["page"]
        if platform == fail_platform:
            job.set_platform_status(platform, "rate_limited")
            return
        for i in range(req.limit):
            job.add_result(platform, result(platform, str(start + i)))
        job.apply_metrics(platform, {"pagination": PageState(page=start + req.limit).model_dump(), "page_requests": 2})
        job.set_platform_status(platform, "succeeded")
    monkeypatch.setattr(manager, "_run_worker", worker)
    return calls


@pytest.mark.asyncio
async def test_continuation_cache_round_history_and_stale_identity(manager, monkeypatch):
    calls = install_worker(manager, monkeypatch)
    first = await search(manager, limit_per_platform=2)
    second = await search(manager, limit_per_platform=2, continue_from=first.job_id)
    assert [r.content_id for r in second.results] == ["3", "4"]
    assert calls[-1].seen_ids == ["1", "2"] and calls[-1].pagination["page"] == 3
    assert second.model_dump(mode="json")["exploration"]["previous_batches"][0]["results"][0]["content_id"] == "1"
    assert second.exploration["round"] == 2
    assert second.exploration["platforms"]["xhs"]["collected"] == 4
    assert result_cache.lookup("素材", "xhs", 2).pagination["page"] == 3
    with pytest.raises(sjm.InvalidPlatformsError):
        await search(manager, continue_from=first.job_id)
    replay = await search(manager, limit_per_platform=2)
    assert replay.platforms["xhs"].cache_hit
    assert [r.content_id for r in replay.results] == ["1", "2"]
    assert replay.exploration["round"] == 1
    after_cache = await search(manager, limit_per_platform=2, continue_from=replay.job_id)
    assert [r.content_id for r in after_cache.results] == ["3", "4"]


@pytest.mark.asyncio
async def test_cumulative_limit_clamps_worker_and_stops(manager, monkeypatch):
    calls = install_worker(manager, monkeypatch)
    job = await search(manager, limit_per_platform=40)
    job = await search(manager, limit_per_platform=40, continue_from=job.job_id)
    job = await search(manager, limit_per_platform=40, continue_from=job.job_id)
    assert [req.limit for req in calls] == [40, 40, 20]
    assert job.exploration["platforms"]["xhs"] == {"collected": 100, "has_more": False}
    with pytest.raises(sjm.InvalidPlatformsError):
        await search(manager, continue_from=job.job_id)


@pytest.mark.asyncio
async def test_cross_round_new_source_updates_original_card(manager, monkeypatch):
    async def worker(job, platform):
        if not job.continuation and platform == "xhs" or job.continuation and platform == "bilibili":
            job.add_result(platform, result(platform, platform, "Python学习入门完整教程"))
            if job.continuation:
                assert job.response_results() == []
        job.apply_metrics(platform, {"pagination": PageState(page=2).model_dump()})
        job.set_platform_status(platform, "succeeded")
    monkeypatch.setattr(manager, "_run_worker", worker)
    first = await search(manager, platforms=["xhs", "bilibili"])
    second = await search(manager, platforms=["xhs", "bilibili"], continue_from=first.job_id)
    assert second.results == []
    old = second.model_dump(mode="json")["exploration"]["previous_batches"][0]["results"]
    assert len(old) == 1 and len(old[0]["grouped_sources"]) == 2
    assert second.exploration["new_sources"] == 1


@pytest.mark.asyncio
async def test_cooldown_does_not_advance_failed_platform(manager, monkeypatch):
    calls = install_worker(manager, monkeypatch, fail_platform="bilibili")
    first = await search(manager, platforms=["xhs", "bilibili"], limit_per_platform=2)
    assert first.overall == "partial"
    second = await search(manager, platforms=["xhs", "bilibili"], limit_per_platform=2, continue_from=first.job_id)
    assert second.platforms["bilibili"].cooldown_skipped
    assert [r.platform for r in calls].count("bilibili") == 1
    assert manager._active_job.exploration.states["bilibili"].page == 1
    assert [r.content_id for r in second.results] == ["3", "4"]


@pytest.mark.asyncio
async def test_account_change_rejects_old_progress(manager, monkeypatch):
    install_worker(manager, monkeypatch)
    first = await search(manager)
    generation = manager._active_job.account_generations["xhs"]
    monkeypatch.setattr(sjm, "get_account_generation", lambda p: generation + 1)
    with pytest.raises(sjm.InvalidPlatformsError, match="账号"):
        await search(manager, continue_from=first.job_id)


@pytest.mark.asyncio
async def test_replace_platforms_reruns_one_platform_without_a_new_batch(manager, monkeypatch):
    """单平台重搜（⟳）：只重搜这一个平台、结果替换它在当前批次里的内容、不开新批次。

    用户场景：这一轮只搜了小红书和B站，之后想补看知乎（或把某个平台重搜一遍），
    不想重搜全部、也不想多出一个"第 2 批"。
    """
    calls = []

    async def worker(job, platform):
        req = WorkerRequest.model_validate_json(manager._build_request_json(job, platform))
        calls.append(req)
        start = req.pagination["page"]
        for i in range(req.limit):
            # 各平台内容互不相同，避免跨平台合卡干扰断言。
            job.add_result(platform, result(platform, f"{platform}-{start + i}"))
        job.apply_metrics(platform, {"pagination": PageState(page=start + req.limit).model_dump()})
        job.set_platform_status(platform, "succeeded")

    monkeypatch.setattr(manager, "_run_worker", worker)

    first = await search(manager, platforms=["xhs", "bilibili"], limit_per_platform=2)
    assert [c.platform for c in calls] == ["xhs", "bilibili"]
    assert first.exploration["round"] == 1

    # 重搜知乎（第一轮没有它）：从第 1 页搜
    second = await search(manager, platforms=["zhihu"], limit_per_platform=2,
                          continue_from=first.job_id, replace_platforms=True)
    assert [c.platform for c in calls] == ["xhs", "bilibili", "zhihu"]
    assert calls[-1].pagination["page"] == 1

    # 响应是**整轮完整视图**：小红书的 2 条 + 其它平台的 4 条都在
    # （曾因响应只装被重搜平台，前端把其它平台的结果整批覆盖掉）
    assert len(second.results) == 6
    assert {r.platform for r in second.results} == {"xhs", "bilibili", "zhihu"}

    # 不开新批次：round 不变
    assert second.exploration["round"] == 1
    session = manager._active_job.exploration
    assert len(session.batches) == 1
    # 批次内容与当前视图一致
    assert len(session.batches[-1]["results"]) == 6
    assert second.exploration["new_contents"] == 6
    # 新平台进了会话，原平台进度没被重置
    assert session.platforms == ["xhs", "bilibili", "zhihu"]
    assert session.states["xhs"].page == 3 and session.states["bilibili"].page == 3
    # 响应里三个平台的状态都在（状态条与各平台条数不能只剩一个）
    assert set(second.platforms) == {"xhs", "bilibili", "zhihu"}
    assert second.overall == "completed"

    # hydration 迟到轮询（同一 job 再取）：结果仍是全量，不会缩水
    late = await manager.get_job(second.job_id)
    assert late is not None and len(late.results) == 6

    # 重搜一个**已有结果**的平台：接着它的分页继续抓 → 拿到与上一轮不重复的新内容
    third = await search(manager, platforms=["xhs"], limit_per_platform=2,
                         continue_from=second.job_id, replace_platforms=True)
    session = manager._active_job.exploration
    assert third.exploration["round"] == 1
    assert {r.content_id for r in session.results["xhs"]} == {"xhs-3", "xhs-4"}
    assert {r.content_id for r in session.results["bilibili"]} == {"bilibili-1", "bilibili-2"}
    # 响应仍是整轮视图（含其它平台）
    assert len(third.results) == 6

    # 原会话仍在 → 之后"换一批"照常可用
    fourth = await search(manager, platforms=["xhs", "bilibili"], limit_per_platform=2,
                          continue_from=third.job_id)
    assert fourth.exploration["round"] == 2


@pytest.mark.asyncio
async def test_single_platform_research_continues_pagination_and_skips_seen(manager, monkeypatch):
    """重搜 = 单平台版的"换一批"：接着分页继续，且把已见 id 交给 worker 去重。"""
    calls = []

    async def worker(job, platform):
        req = WorkerRequest.model_validate_json(manager._build_request_json(job, platform))
        calls.append(req)
        start = req.pagination["page"]
        for i in range(req.limit):
            job.add_result(platform, result(platform, f"{platform}-{start + i}"))
        job.apply_metrics(platform, {"pagination": PageState(page=start + req.limit).model_dump()})
        job.set_platform_status(platform, "succeeded")

    monkeypatch.setattr(manager, "_run_worker", worker)

    first = await search(manager, platforms=["bilibili"], limit_per_platform=2)
    assert {r.content_id for r in first.results} == {"bilibili-1", "bilibili-2"}

    second = await search(manager, platforms=["bilibili"], limit_per_platform=2,
                          continue_from=first.job_id, replace_platforms=True)
    # worker 收到的是下一页 + 已见 id（不重复）
    assert calls[-1].pagination["page"] == 3
    assert calls[-1].seen_ids == ["bilibili-1", "bilibili-2"]
    assert {r.content_id for r in second.results} == {"bilibili-3", "bilibili-4"}


@pytest.mark.asyncio
async def test_single_platform_research_rejects_exhausted_platform(manager, monkeypatch):
    """已取尽的平台不静默返回重复内容，而是明确报错（0 结果平台走的是"从头搜"分支）。"""
    async def worker(job, platform):
        req = WorkerRequest.model_validate_json(manager._build_request_json(job, platform))
        for i in range(req.limit):
            job.add_result(platform, result(platform, f"{platform}-{req.pagination['page']}-{i}"))
        # 明确声明没有下一页
        job.apply_metrics(platform, {"pagination": PageState(page=99, exhausted=True).model_dump()})
        job.set_platform_status(platform, "succeeded")

    monkeypatch.setattr(manager, "_run_worker", worker)
    first = await search(manager, platforms=["zhihu"], limit_per_platform=2)
    assert first.results  # 有内容
    assert not manager._active_job.exploration.more("zhihu")

    with pytest.raises(sjm.InvalidPlatformsError, match="已经取完"):
        await search(manager, platforms=["zhihu"], limit_per_platform=2,
                     continue_from=first.job_id, replace_platforms=True)


@pytest.mark.asyncio
async def test_hydration_scope_covers_only_job_platforms(manager, monkeypatch):
    """replace 任务的补全范围只含本任务平台。

    response_results() 是整轮全量视图（含会话里其它平台的结果），而本任务的
    platforms_state 只有自己跑过的平台 —— 不过滤会 KeyError 让整个补全失败
    （用户日志里的 `result hydration failed: KeyError`）。
    """
    async def worker(job, platform):
        for i in range(2):
            job.add_result(platform, result(platform, f"{platform}-{i}"))
        job.set_platform_status(platform, "succeeded")

    monkeypatch.setattr(manager, "_run_worker", worker)
    first = await search(manager, platforms=["xhs", "bilibili"], limit_per_platform=2)
    await search(manager, platforms=["zhihu"], limit_per_platform=2,
                 continue_from=first.job_id, replace_platforms=True)

    job = manager._active_job
    # 全量视图里有三个平台，但本任务只跑过知乎
    assert {r.platform for r in job.response_results()} == {"xhs", "bilibili", "zhihu"}
    scoped = manager._hydration_scope(job)
    assert {r.platform for r in scoped} == {"zhihu"}


@pytest.mark.asyncio
async def test_replace_platforms_reruns_a_platform_that_collected_nothing(manager, monkeypatch):
    """上一轮一条没搜到、还被判定"取尽"的平台，也能重搜（不该报"没有更多可获取内容"）。

    这正是用户遇到的抖音场景：登录状态可用但 0 条结果，点重搜却提示达到上限。
    """
    calls = []

    async def worker(job, platform):
        req = WorkerRequest.model_validate_json(manager._build_request_json(job, platform))
        calls.append((req.platform, req.pagination["page"]))
        if platform == "douyin" and len(calls) == 1:
            # 第一轮：抖音空结果 + 后端认为"没有下一页了"
            job.apply_metrics(platform, {"pagination": PageState(page=1, exhausted=True).model_dump()})
            job.set_platform_status(platform, "empty")
            return
        for i in range(req.limit):
            job.add_result(platform, result(platform, f"{platform}-{req.pagination['page']}-{i}"))
        job.apply_metrics(platform, {"pagination": PageState(page=req.pagination["page"] + req.limit).model_dump()})
        job.set_platform_status(platform, "succeeded")

    monkeypatch.setattr(manager, "_run_worker", worker)

    first = await search(manager, platforms=["douyin", "xhs"], limit_per_platform=2)
    assert first.platforms["douyin"].status == "empty"
    assert not manager._active_job.exploration.more("douyin")

    # 换批会被"没有更多"挡住（这是换批该有的语义）
    with pytest.raises(sjm.InvalidPlatformsError, match="没有更多"):
        await search(manager, platforms=["douyin"], continue_from=first.job_id)

    # 而"重搜"必须能跑，并从第 1 页重来；响应仍是整轮视图（小红书的结果保留）
    second = await search(manager, platforms=["douyin"], limit_per_platform=2,
                          continue_from=first.job_id, replace_platforms=True)
    assert calls[-1] == ("douyin", 1)
    assert {r.content_id for r in second.results if r.platform == "douyin"} == {"douyin-1-0", "douyin-1-1"}
    assert {r.content_id for r in second.results if r.platform == "xhs"} == {"xhs-1-0", "xhs-1-1"}
    assert second.platforms["douyin"].status == "succeeded"


@pytest.mark.asyncio
async def test_cancel_commits_checkpoint_and_allows_next_batch(manager, monkeypatch):
    install_worker(manager, monkeypatch)
    first = await search(manager, limit_per_platform=2)
    entered = asyncio.Event()
    async def worker(job, platform):
        job.add_result(platform, result(platform, "partial"))
        job.apply_metrics(platform, {"pagination": PageState(page=9).model_dump()})
        entered.set()
        await asyncio.Event().wait()
    monkeypatch.setattr(manager, "_run_worker", worker)
    response = await manager.create_job(SearchJobRequestSchema(keyword="素材", platforms=["xhs"], continue_from=first.job_id))
    await entered.wait()
    task = manager._active_job.task
    await manager.cancel_job(response.job_id)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    cancelled = await manager.get_job(response.job_id)
    assert cancelled.overall == "cancelled"
    assert cancelled.exploration["round"] == 2
    calls = install_worker(manager, monkeypatch)
    await search(manager, continue_from=cancelled.job_id, limit_per_platform=1)
    assert calls[0].pagination["page"] == 9
    assert "partial" in calls[0].seen_ids
