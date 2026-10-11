"""Question discovery stays separate from keyword search and answer identity."""

import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

from aggregate_search.adapters.zhihu import ZhihuAdapter, search_reading_payload
from api.routers.reading import reading_router, get_reading_service
from api.services import accounts, reading, reading_zhihu_questions as questions
from api.services.operation_coordinator import OperationCoordinator


def answer(identity="42", question="1", votes=4, **extra):
    return {"id": identity, "type": "answer", "question": {"id": question, "title": "测试问题"},
            "content": "<p>回答正文</p>", "excerpt": "摘要", "author": {"name": f"作者{identity}"},
            "voteup_count": votes, "comment_count": 5, "visits_count": 112424515, **extra}


def page(*answers, end=False, offset=20):
    return {"data": [{"object": row} for row in answers],
            "paging": {"is_end": end, "next": f"https://www.zhihu.com/api/v4/search_v3?offset={offset}"}}


@pytest.fixture
def service(monkeypatch):
    generation = [7]
    monkeypatch.setattr(reading, "get_account_generation", lambda _: generation[0])
    monkeypatch.setattr(reading, "operation_coordinator", OperationCoordinator())
    async def prepare(*args):
        pass
    async def snapshot(*args, **kwargs):
        return {"d_c0": "private-session"}
    monkeypatch.setattr(reading, "prepare_reading", prepare)
    monkeypatch.setattr(reading, "ensure_session_snapshot", snapshot)
    service = reading.ReadingService()
    monkeypatch.setattr(reading, "reading_service", service)
    return service, generation


def test_adapter_separates_question_scope_and_zero_answer_interactions():
    raw = answer(votes=0, question="1")
    raw["question"].update({"visit_count": 112424515, "answer_count": 120})
    result = ZhihuAdapter().adapt([raw])[0]
    assert result.metrics == {"like_count": 0, "comment_count": 5}
    assert result.question.metrics == {"view_count": 112424515, "answer_count": 120}
    raw["question"]["answer_count"] = 0
    assert "answer_count" not in ZhihuAdapter().adapt([raw])[0].question.metrics


@pytest.mark.asyncio
async def test_question_pages_merge_seeds_exact_ids_and_body_cache(service, monkeypatch):
    reader, _ = service
    reader.remember_search_result(ZhihuAdapter().adapt([answer()])[0], 7)
    calls = []
    async def fetch(snapshot, title, offset):
        assert "d_c0" in snapshot and title == "测试问题"
        calls.append(offset)
        return page(answer("99", votes=5436), answer("98", question="2")) if offset == 0 else page(answer("99", votes=5437), answer("100"), end=True)
    monkeypatch.setattr(reading, "fetch_question_page", fetch)
    first = await reader.question_answers("1", "其它标题", "https://www.zhihu.com/question/1")
    assert [r["content_id"] for r in first["answers"]] == ["42", "99"]
    assert "view_count" not in first["answers"][1]["metrics"]
    assert "回答正文" not in json.dumps(first) and "private-session" not in json.dumps(first)
    cached = await reader.question_answers("1", "测试问题", "https://www.zhihu.com/question/1")
    assert cached == first and calls == [0]
    second = await reader.question_answers("1", "测试问题", "https://www.zhihu.com/question/1", first["next_cursor"])
    assert [r["content_id"] for r in second["answers"]] == ["42", "99", "100"]
    assert second["exhausted"] and second["next_cursor"] is None and calls == [0, 20]
    assert await reader.question_answers("1", "测试问题", "https://www.zhihu.com/question/1") == second
    assert (await reader.read("answer", "99", "https://www.zhihu.com/question/1/answer/99"))["blocks"][0]["text"] == "回答正文"
    assert not any(key[3] == "98" for key in reader.search_bodies)


@pytest.mark.asyncio
async def test_empty_matching_page_can_continue_and_failure_preserves_cursor(service, monkeypatch):
    reader, _ = service
    failure = [False]
    async def fetch(*args):
        if failure[0]:
            raise RuntimeError("cookie=private https://secret.test")
        return page(answer("99", question="2"))
    monkeypatch.setattr(reading, "fetch_question_page", fetch)
    first = await reader.question_answers("1", "测试问题", "https://www.zhihu.com/question/1")
    assert not first["answers"] and first["next_cursor"] and not first["exhausted"]
    failure[0] = True
    with pytest.raises(reading.ReadingError) as error:
        await reader.question_answers("1", "测试问题", "https://www.zhihu.com/question/1", first["next_cursor"])
    assert "private" not in str(error.value)
    failure[0] = False
    assert (await reader.question_answers("1", "测试问题", "https://www.zhihu.com/question/1", first["next_cursor"]))["next_cursor"]


@pytest.mark.asyncio
async def test_cursors_are_bound_to_question_and_account(service, monkeypatch):
    reader, generation = service
    async def fetch(*args):
        return page(answer())
    monkeypatch.setattr(reading, "fetch_question_page", fetch)
    first = await reader.question_answers("1", "测试问题", "https://www.zhihu.com/question/1")
    with pytest.raises(ValueError):
        await reader.question_answers("2", "测试问题", "https://www.zhihu.com/question/2", first["next_cursor"])
    generation[0] += 1
    with pytest.raises(ValueError):
        await reader.question_answers("1", "测试问题", "https://www.zhihu.com/question/1", first["next_cursor"])


@pytest.mark.asyncio
async def test_busy_cancel_and_generation_change_do_not_commit_pages(service, monkeypatch):
    reader, generation = service
    await reading.operation_coordinator.acquire_exclusive("search")
    with pytest.raises(reading.ReadingError, match="搜索"):
        await reader.question_answers("1", "测试问题", "https://www.zhihu.com/question/1")
    await reading.operation_coordinator.release_exclusive("search")
    started, cancelled = asyncio.Event(), asyncio.Event()
    async def fetch(*args):
        try:
            started.set()
            await asyncio.Event().wait()
        finally:
            cancelled.set()
    monkeypatch.setattr(reading, "fetch_question_page", fetch)
    task = asyncio.create_task(reader.question_answers("1", "测试问题", "https://www.zhihu.com/question/1"))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert cancelled.is_set() and not reader.questions[(7, "1")]["pages"]
    assert await reading.operation_coordinator.acquire_exclusive("search")
    await reading.operation_coordinator.release_exclusive("search")
    async def changed(*args):
        generation[0] += 1
        return page(answer())
    monkeypatch.setattr(reading, "fetch_question_page", changed)
    with pytest.raises(reading.ReadingError):
        await reader.question_answers("1", "测试问题", "https://www.zhihu.com/question/1")
    assert not reader.search_bodies and not reader.questions[(7, "1")]["pages"]


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["pages", "answers"])
async def test_question_discovery_has_per_question_limits(service, monkeypatch, mode):
    reader, _ = service
    calls = []
    async def fetch(snapshot, title, offset):
        calls.append(offset)
        return page(*(answer(str(offset+i+1)) for i in range(20))) if mode == "answers" else page()
    monkeypatch.setattr(reading, "fetch_question_page", fetch)
    cursor = None
    for _ in range(25):
        result = await reader.question_answers("1", "测试问题", "https://www.zhihu.com/question/1", cursor)
        cursor = result["next_cursor"]
        if not cursor:
            break
    assert result["limit_reached"] and len(calls) == (5 if mode == "answers" else 20)
    assert len(result["answers"]) == (100 if mode == "answers" else 0)


@pytest.mark.asyncio
async def test_account_changes_eagerly_remove_runtime_bodies_and_questions(service, monkeypatch):
    reader, _ = service
    monkeypatch.setattr(accounts, "_session_snapshots", {})
    monkeypatch.setattr(accounts, "_account_generation", {})
    for action in (lambda: accounts.clear_session_snapshot("zhihu"), lambda: accounts.set_session_snapshot("zhihu", {"d_c0": "new-session"})):
        reader.remember_search_body(search_reading_payload(answer()), 7)
        reader.remember_search_result(ZhihuAdapter().adapt([answer()])[0], 7)
        assert reader.search_bodies and reader.questions
        await action()
        assert not reader.search_bodies and not reader.questions


@pytest.mark.asyncio
async def test_question_fetch_uses_one_search_request_and_closes_client(monkeypatch):
    calls, closed = [], []
    client = SimpleNamespace(default_headers={})
    async def get(path, params):
        calls.append((path, params))
        assert params["sort"] == "upvoted_count" and params["q"] == "测试问题"
        assert params["limit"] == 20 and params["offset"] == params["lc_idx"] == 40
        assert "Chrome/" in client.default_headers["user-agent"]
        return page()
    client.get = get
    async def get_client(*args):
        return client
    async def close(*args):
        closed.append(True)
    monkeypatch.setattr(questions.ResultHydrator, "_get_zhihu", get_client)
    monkeypatch.setattr(questions.ResultHydrator, "close", close)
    await questions.fetch_question_page({"d_c0": "private"}, "测试问题", 40)
    assert calls[0][0] == "/api/v4/search_v3" and len(calls) == len(closed) == 1


@pytest.mark.asyncio
async def test_local_question_api_validates_routes_origins_and_cursor(service, monkeypatch):
    reader, _ = service
    async def fetch(*args):
        return page(answer(), end=True)
    monkeypatch.setattr(reading, "fetch_question_page", fetch)
    app = FastAPI()
    app.include_router(reading_router)
    app.dependency_overrides[get_reading_service] = lambda: reader
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1") as client:
        payload = {"question_id": "1", "title": "测试问题", "url": "https://www.zhihu.com/question/1"}
        assert (await client.post("/api/reading/zhihu/question/answers", json=payload)).status_code == 200
        for changes in ({"url": "https://evil.test/question/1"}, {"url": "https://www.zhihu.com/question/2"}, {"cursor": "unknown"}, {"question_id": "١"}, {"cookie": "private"}):
            assert (await client.post("/api/reading/zhihu/question/answers", json={**payload, **changes})).status_code == 422
        assert (await client.post("/api/reading/zhihu/question/answers", json=payload, headers={"origin": "https://evil.test"})).status_code == 403
