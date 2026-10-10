# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
# This file is part of MediaCrawler project.
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

"""On-demand comments preserve read isolation, cancellation and safe errors."""

import asyncio
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from api.services import reading
from api.services.operation_coordinator import OperationCoordinator
from api.routers.reading import reading_router, get_reading_service


@pytest.mark.asyncio
async def test_comments_cancel_releases_lease(monkeypatch):
    coordinator = OperationCoordinator()
    monkeypatch.setattr(reading, "operation_coordinator", coordinator)
    started, closed = asyncio.Event(), asyncio.Event()
    async def fetch(*args):
        started.set()
        try:
            await asyncio.Future()
        finally:
            closed.set()
    monkeypatch.setattr(reading, "fetch_comments", fetch)
    service = reading.ReadingService()
    task = asyncio.create_task(service.comments("answer", "42", "https://www.zhihu.com/question/1/answer/42"))
    await started.wait()
    with pytest.raises(reading.ReadingError, match="正在进行"):
        await service.comments("answer", "42", "https://www.zhihu.com/question/1/answer/42")
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert closed.is_set() and not service.tasks
    assert await coordinator.acquire_exclusive("reading")
    await coordinator.release_exclusive("reading")


@pytest.mark.asyncio
async def test_comments_only_no_body_or_transcription(monkeypatch):
    from api.services import research_materials
    calls = []
    async def prepare(platform): calls.append("prepare")
    async def snapshot(*args, **kwargs): return {"a1": "test"}
    class Collector:
        def __init__(self, sessions): pass
        async def client(self, platform): return object()
        async def comments(self, client, source, detail):
            assert detail is None and source["platform"] == "xhs"
            return {"entries": [{"id": "1", "text": "评论", "author": "读者", "parent_id": None}], "truncated": True, "sort": "平台默认", "reason": "部分失败", "diagnostics": {"secret": "never expose"}}
        async def close(self): calls.append("close")
    monkeypatch.setattr(reading, "prepare_reading", prepare)
    monkeypatch.setattr(reading, "ensure_session_snapshot", snapshot)
    monkeypatch.setattr(research_materials, "MaterialCollector", Collector)
    result = await reading.fetch_comments("note", "a" * 24, "https://www.xiaohongshu.com/explore/" + "a" * 24, "xhs")
    assert result["limited"] and result["notice"] == "部分失败"
    assert "diagnostics" not in result and calls == ["prepare", "close"]


def test_comments_endpoint_validates_source_and_errors():
    app = FastAPI()
    app.include_router(reading_router)
    app.dependency_overrides[get_reading_service] = lambda: reading.ReadingService()
    payload = {"platform": "zhihu", "content_type": "answer", "content_id": "42", "url": "https://evil.test/answer/42"}
    with TestClient(app, base_url="http://127.0.0.1") as client:
        assert client.post("/api/reading/comments", json=payload).status_code == 422
        assert client.post("/api/reading/comments", json=payload, headers={"origin": "https://evil.test"}).status_code == 403
        async def failure(*args, **kwargs): raise reading.ReadingError("busy")
        app.dependency_overrides[get_reading_service] = lambda: SimpleNamespace(comments=failure)
        response = client.post("/api/reading/comments", json=payload)
        assert response.status_code == 409 and "评论" in response.json()["detail"]["message"]


@pytest.mark.asyncio
@pytest.mark.parametrize("returned_id", ["BV1234567890", "BV0000000000"])
async def test_bilibili_view_is_matched_before_comments(monkeypatch, returned_id):
    from api.services import research_materials
    calls = []
    async def prepare(*args): pass
    async def snapshot(*args, **kwargs): return {}
    class Collector:
        def __init__(self, sessions): pass
        async def client(self, platform): return SimpleNamespace(get_video_info=object())
        async def request(self, *args, **kwargs): return {"View": {"bvid": returned_id, "aid": 12}}
        async def comments(self, client, source, detail):
            assert detail["aid"] == 12
            calls.append("comments")
            return {"entries": []}
        async def close(self): calls.append("close")
    monkeypatch.setattr(reading, "prepare_reading", prepare)
    monkeypatch.setattr(reading, "ensure_session_snapshot", snapshot)
    monkeypatch.setattr(research_materials, "MaterialCollector", Collector)
    if returned_id == "BV1234567890":
        await reading.fetch_comments("video", returned_id, "https://www.bilibili.com/video/" + returned_id, "bilibili")
        assert calls == ["comments", "close"]
    else:
        with pytest.raises(ValueError):
            await reading.fetch_comments("video", "BV1234567890", "https://www.bilibili.com/video/BV1234567890", "bilibili")
        assert calls == ["close"]
