"""Search-provided bodies reach only the bounded reader cache, without detail requests."""

import asyncio
import json
from types import SimpleNamespace

import pytest

from aggregate_search import worker
from aggregate_search.adapters.zhihu import SEARCH_BODY_MAX_CHARS, search_reading_payload
from aggregate_search.models import WorkerEvent
from aggregate_search.pagination import PageState, PaginationRun
from aggregate_search.protocol import EVENT_PREFIX, EVENT_SEPARATOR
from api.services import reading, search_job_manager as sjm
from tests.test_worker_fast_path import _run_capture


def answer(identity="42", **extra):
    return {"id": identity, "type": "answer", "question": {"id": "1", "title": "测试问题"},
            "content": '<p>搜索返回的正文<img src="https://pic.zhimg.com/a.jpg"></p><script>private-script</script>',
            "excerpt": "只有摘要", "cookie": "private-cookie", "author": {"name": "作者"}, **extra}


@pytest.mark.parametrize("pagination", [None, {}])
def test_worker_transports_body_for_both_search_paths_without_leaking_state(monkeypatch, pagination):
    calls = []
    class Client:
        def __init__(self, **kwargs):
            self.cookie_dict = kwargs["cookie_dict"]
        async def get(self, path, params):
            calls.append(path)
            return {"paging": {"is_end": True}, "data": [{"type": "search_result", "object": answer()}]}
        async def aclose(self):
            pass
    monkeypatch.setattr("media_platform.zhihu.client.ZhiHuClient", Client)
    events = _run_capture(worker.run_worker, "job", "search", "zhihu", "测试", 1,
                         session_snapshot={"d_c0": "private-cookie"}, fast_path=True, pagination=pagination)
    bodies = [event.data for event in events if event.event == "reading_body"]
    results = [event.data for event in events if event.event == "result"]
    assert len(bodies) == len(results) == 1 and calls == ["/api/v4/search_v3"]
    assert bodies[0]["detail"]["content"].startswith("<p>")
    assert "private-cookie" not in repr(events)
    assert "content" not in results[0] and results[0]["snippet"] == "只有摘要"


@pytest.fixture
def cached_reader(monkeypatch):
    clock = [10.0]
    monkeypatch.setattr(reading, "get_account_generation", lambda _: 7)
    monkeypatch.setattr(sjm, "get_account_generation", lambda _: 7)
    monkeypatch.setattr(reading.time, "monotonic", lambda: clock[0])
    service = reading.ReadingService()
    monkeypatch.setattr(reading, "reading_service", service)
    return service, clock


@pytest.mark.asyncio
async def test_parent_consumes_body_but_public_search_and_pagination_remain_light(cached_reader):
    service, _ = cached_reader
    job = sjm._ActiveJob("job", "测试", ["zhihu"], 20)
    manager = sjm.SearchJobManager()
    events = [WorkerEvent(event="reading_body", job_id="job", platform="zhihu", data=search_reading_payload(answer())),
              WorkerEvent(event="result", job_id="job", platform="zhihu", data={"platform": "zhihu", "content_id": "42",
                          "content_type": "answer", "title": "测试问题", "url": "https://www.zhihu.com/answer/42", "snippet": "只有摘要"}),
              WorkerEvent(event="done", job_id="job", platform="zhihu")]
    stream = asyncio.StreamReader()
    for event in events:
        stream.feed_data((EVENT_PREFIX + EVENT_SEPARATOR + event.model_dump_json() + "\n").encode())
    stream.feed_eof()
    assert await manager._read_worker_output(job, "zhihu", "job", SimpleNamespace(stdout=stream))
    detail = await service.read("answer", "42", "https://www.zhihu.com/answer/42")
    assert detail["blocks"][0]["text"] == "搜索返回的正文"
    assert detail["blocks"][1]["type"] == "image" and not detail["limited"]
    assert not detail["notices"]
    assert "private" not in json.dumps(detail)
    assert "搜索返回的正文" not in job.to_response().model_dump_json()
    assert "搜索返回的正文" not in job.page_states["zhihu"].model_dump_json()


@pytest.mark.asyncio
async def test_search_cache_survives_time_and_many_results_but_refresh_and_generation_still_apply(cached_reader, monkeypatch):
    service, clock = cached_reader
    calls = []
    async def unavailable(*args):
        calls.append(True)
        raise reading.ReadingError("rate_limited")
    monkeypatch.setattr(reading, "fetch_reading", unavailable)
    service.remember_search_body(search_reading_payload(answer()), 7)
    assert (await service.read("answer", "42", "https://www.zhihu.com/answer/42"))["blocks"]
    assert not calls
    with pytest.raises(reading.ReadingError):
        await service.read("answer", "42", "https://www.zhihu.com/answer/42", refresh=True)
    clock[0] += 86400
    for identity in range(100, 180):
        service.remember_search_body(search_reading_payload(answer(str(identity))), 7)
    assert (await service.read("answer", "42", "https://www.zhihu.com/answer/42"))["blocks"]
    assert len(calls) == 1 and len(service.search_bodies) == 81
    monkeypatch.setattr(reading, "get_account_generation", lambda _: 8)
    with pytest.raises(reading.ReadingError):
        await service.read("answer", "42", "https://www.zhihu.com/answer/42")
    service.remember_search_body(search_reading_payload(answer("99")), 7)
    assert not any(key[3] == "99" for key in service.search_bodies)
    for identity in range(100, 145):
        service.remember_search_body(search_reading_payload(answer(str(identity))), 8)
    assert len(service.search_bodies) == 126
    await service.cleanup()
    assert not service.search_bodies


@pytest.mark.parametrize("extra", [{"content": "", "excerpt": "摘要"}, {"content": None},
    {"content": "字" * (SEARCH_BODY_MAX_CHARS + 1)}, {"can_read": False}, {"is_hidden": True},
    {"content": "<script>private-script</script>"}])
def test_unusable_or_denied_search_body_never_becomes_readable(cached_reader, extra):
    service, _ = cached_reader
    service.remember_search_body(search_reading_payload(answer(**extra)), 7)
    assert not service.search_bodies


@pytest.mark.parametrize("extra", [{"type": []}, {"type": "zvideo"}, {"id": "٤٢"}, {"id": ""}])
def test_invalid_search_body_identity_is_ignored(extra):
    assert search_reading_payload(answer(**extra)) is None


def test_mismatched_identity_and_paid_content_stay_honest(cached_reader):
    service, _ = cached_reader
    payload = search_reading_payload(answer(paid_info={"is_paid": True, "secret": "private-paid"}))
    service.remember_search_body({**payload, "content_id": "43"}, 7)
    assert not service.search_bodies
    service.remember_search_body(payload, 7)
    detail = next(iter(service.search_bodies.values()))[1]
    assert detail["limited"] and any("付费" in notice for notice in detail["notices"])
    assert "private-paid" not in json.dumps(detail)


@pytest.mark.asyncio
async def test_pagination_does_not_put_body_into_pending_or_checkpoint():
    emitted, checkpoints, bodies = [], [], []
    run = PaginationRun("zhihu", "测试", 1, PageState(), [], emitted.append, checkpoints.append, bodies.append)
    async def get(*args):
        return {"paging": {"is_end": True}, "data": [{"type": "search_result", "object": answer(str(i))} for i in (42, 43)]}
    await run.run(SimpleNamespace(get=get))
    assert len(bodies) == 2 and len(run.state.pending) == 1
    assert "搜索返回的正文" not in json.dumps(checkpoints + emitted)


@pytest.mark.asyncio
@pytest.mark.parametrize("scenario", ["wrong_job", "wrong_platform", "cancelled", "stale_account"])
async def test_unowned_body_events_do_not_seed_cache(cached_reader, scenario):
    service, _ = cached_reader
    job = sjm._ActiveJob("job", "测试", ["zhihu"], 20)
    job._cancelled = scenario == "cancelled"
    if scenario == "stale_account":
        job.account_generations["zhihu"] = 6
    event = WorkerEvent(event="reading_body", job_id="old" if scenario == "wrong_job" else "job",
                        platform="xhs" if scenario == "wrong_platform" else "zhihu", data=search_reading_payload(answer()))
    stream = asyncio.StreamReader()
    stream.feed_data((EVENT_PREFIX + EVENT_SEPARATOR + event.model_dump_json() + "\n").encode())
    stream.feed_eof()
    await sjm.SearchJobManager()._read_worker_output(job, "zhihu", "job", SimpleNamespace(stdout=stream))
    assert not service.search_bodies
