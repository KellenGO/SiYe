import asyncio

import pytest

from aggregate_search.hydration import (
    HYDRATION_CONCURRENCY,
    hydrate_results,
    hydration_candidates,
    needs_hydration,
)
from aggregate_search.models import UnifiedSearchResult
from api.services.result_hydration import ResultHydrator, extract_xhs_snippet


def result(title="Claude Code 教程", snippet=None, index=0):
    return UnifiedSearchResult(
        platform="bilibili", content_id=str(index), title=title,
        snippet=snippet, author="作者", url="https://example.test", rank=index,
    )


def test_needs_hydration_for_empty_title_duplicate_and_weak_text():
    assert needs_hydration(result(snippet=None))
    assert needs_hydration(result(snippet="Claude Code 教程"))
    assert needs_hydration(result(snippet="暂无简介"))


def test_normal_snippet_is_not_hydrated():
    assert not needs_hydration(result(
        snippet="从安装、配置到实际项目使用，整理了完整操作流程和常见问题。"
    ))


def test_xhs_detail_snippet_supports_unwrapped_and_nested_shapes():
    assert extract_xhs_snippet({"desc": "直接描述"}) == "直接描述"
    assert extract_xhs_snippet({"note_card": {"desc": "卡片描述"}}) == "卡片描述"
    assert extract_xhs_snippet({
        "data": {"items": [{"note_card": {"desc": "嵌套描述"}}]}
    }) == "嵌套描述"
    assert extract_xhs_snippet({"desc": "   <br>  "}) is None


class _FakeXhsDetailClient:
    def __init__(self, detail=None, error=None):
        self.detail = detail
        self.error = error
        self.calls = []

    async def get_note_by_id(self, note_id, xsec_source, xsec_token):
        self.calls.append((note_id, xsec_source, xsec_token))
        if self.error:
            raise self.error
        return self.detail


class _TestXhsHydrator(ResultHydrator):
    def __init__(self, client):
        super().__init__()
        self.client = client

    async def _get_xhs(self, snapshot):
        return self.client


@pytest.mark.asyncio
@pytest.mark.parametrize("shape", ["direct", "card", "nested"])
@pytest.mark.parametrize("description", [None, "Detailed video description"])
async def test_xhs_reuses_description_request_for_duration(monkeypatch, shape, description):
    detail = {"video": {"capa": {"duration": 146}}, "desc": description}
    if shape == "card":
        detail = {"note_card": detail}
    elif shape == "nested":
        detail = {"data": {"items": [{"note_card": detail}]}}
    client = _FakeXhsDetailClient(detail)
    monkeypatch.setattr("api.services.result_hydration.get_session_snapshot", lambda _: {})
    video = UnifiedSearchResult(
        platform="xhs", content_id="n1", content_type="video", title="Video",
        url="https://www.xiaohongshu.com/explore/n1?xsec_token=tok",
    )
    updates = await hydrate_results([video], _TestXhsHydrator(client).fetch_snippet)
    assert video.duration_seconds == 146
    assert video.snippet == description
    assert len(updates) == 1
    assert client.calls == [("n1", "pc_search", "tok")]


@pytest.mark.asyncio
@pytest.mark.parametrize("kind, existing", [("note", None), ("video", 94)])
async def test_xhs_detail_duration_preserves_content_type_and_existing_value(monkeypatch, kind, existing):
    client = _FakeXhsDetailClient({"video": {"capa": {"duration": 146}}})
    monkeypatch.setattr("api.services.result_hydration.get_session_snapshot", lambda _: {})
    item = UnifiedSearchResult(
        platform="xhs", content_id="n1", content_type=kind, title="Content",
        duration_seconds=existing,
        url="https://www.xiaohongshu.com/explore/n1?xsec_token=tok",
    )
    assert await hydrate_results([item], _TestXhsHydrator(client).fetch_snippet) == []
    assert item.duration_seconds == existing


def test_hydrated_duration_reaches_grouped_sources_and_platform_results():
    from api.services.search_job_manager import _ActiveJob
    from aggregate_search.models import GroupedSource

    video = UnifiedSearchResult(
        platform="xhs", content_id="n1", content_type="video", title="Video",
        url="https://example.test/video",
    )
    job = _ActiveJob("duration-job", "test", ["xhs"], 20)
    job.add_result("xhs", video.model_copy(deep=True))
    grouped = video.model_copy(deep=True)
    grouped.grouped_sources = [GroupedSource.from_result(video)]
    job._final_results = [grouped]
    revision = job.revision
    video.duration_seconds = 146
    job.update_snippet(video, None)
    assert job.platform_results["xhs"][0].duration_seconds == 146
    assert grouped.duration_seconds == 146
    assert grouped.grouped_sources[0].duration_seconds == 146
    assert job.revision > revision


@pytest.mark.asyncio
async def test_xhs_hydration_passes_existing_token_and_updates_snippet(monkeypatch):
    client = _FakeXhsDetailClient({"note_card": {"desc": "详情正文简介"}})
    monkeypatch.setattr(
        "api.services.result_hydration.get_session_snapshot", lambda _: None)
    monkeypatch.setattr(
        "api.services.result_hydration.ensure_session_snapshot",
        lambda _: asyncio.sleep(0, result={"a1": "restored"}),
    )
    item = UnifiedSearchResult(
        platform="xhs", content_id="n1", title="标题", snippet=None,
        url="https://www.xiaohongshu.com/explore/n1?xsec_token=tok&xsec_source=pc_search",
    )
    hydrator = _TestXhsHydrator(client)
    updates = await hydrate_results([item], hydrator.fetch_snippet)
    assert item.snippet == "详情正文简介"
    assert len(updates) == 1
    assert client.calls == [("n1", "pc_search", "tok")]


@pytest.mark.asyncio
async def test_xhs_hydration_missing_token_or_empty_desc_is_safe(monkeypatch):
    client = _FakeXhsDetailClient({"desc": ""})
    monkeypatch.setattr(
        "api.services.result_hydration.get_session_snapshot", lambda _: {})
    no_token = UnifiedSearchResult(
        platform="xhs", content_id="n1", title="标题", snippet=None,
        url="https://www.xiaohongshu.com/explore/n1",
    )
    empty_desc = UnifiedSearchResult(
        platform="xhs", content_id="n2", title="标题", snippet=None,
        url="https://www.xiaohongshu.com/explore/n2?xsec_token=tok",
    )
    hydrator = _TestXhsHydrator(client)
    updates = await hydrate_results([no_token, empty_desc], hydrator.fetch_snippet)
    assert updates == []
    assert client.calls == [("n2", "pc_search", "tok")]
    assert no_token.snippet is None
    assert empty_desc.snippet is None


@pytest.mark.asyncio
async def test_hydration_updates_success_and_preserves_failure():
    items = [result(snippet=None, index=1), result(snippet=None, index=2)]

    async def fetch(item):
        if item.content_id == "2":
            raise RuntimeError("detail failed")
        return "这是补全后的正文简介。"

    updates = await hydrate_results(items, fetch)
    assert [(u.result.content_id, u.snippet) for u in updates] == [
        ("1", "这是补全后的正文简介。")
    ]
    assert items[0].snippet == "这是补全后的正文简介。"
    assert items[1].snippet is None


@pytest.mark.asyncio
async def test_timeout_does_not_block_other_results_and_concurrency_is_bounded():
    items = [result(snippet=None, index=i) for i in range(20)]
    active = 0
    maximum = 0
    never = asyncio.Event()

    async def fetch(item):
        nonlocal active, maximum
        active += 1
        maximum = max(maximum, active)
        try:
            if item.content_id == "0":
                await never.wait()
            return f"正文简介 {item.content_id}"
        finally:
            active -= 1

    updates = await hydrate_results(
        items, fetch, limit=12, concurrency=HYDRATION_CONCURRENCY,
        timeout=0.01,
    )
    assert len(updates) == 11
    assert maximum <= HYDRATION_CONCURRENCY
    assert len(hydration_candidates(items, limit=12)) == 12
