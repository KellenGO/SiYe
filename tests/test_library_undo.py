# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

"""删除后撤销（软删除）的回归测试。

用户反馈（2026-11-05）：删除收藏要能撤销。实现方式是**前端先本地隐藏、
5 秒后才真正删除**，不做服务端延迟删除 —— 那样"已删除"会变成一段不确定状态，
还得维护定时任务与清理逻辑。

因此服务端需要提供两件事：
1. `DELETE /items` 返回被删条目的完整快照（含备注与收藏夹归属）；
2. `POST /items/restore` 能把这些快照原样写回。

这里锁住第 1 点的完整性：只恢复标题和链接是不够的，备注和归属必须一起回来。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from api.main import app
from api.services.library_store import LibraryStore


@pytest.fixture()
def store(tmp_path):
    return LibraryStore(tmp_path / "library.db")


def _result(content_id: str = "n1") -> dict:
    return {
        "platform": "xhs",
        "content_id": content_id,
        "content_type": "note",
        "title": "露营装备怎么选",
        "snippet": "摘要文本",
        "author": "某作者",
        "url": f"https://example.com/{content_id}",
        "cover_url": None,
        "metrics": {"like_count": 12},
    }


def test_snapshot_keeps_note_and_collection_membership(store):
    collection = store.create_collection("周末出行")
    store.add_item(
        _result(), note="我的备注", in_default=True, watch_later=True,
        collection_ids=[collection["id"]],
    )

    snapshots = store.snapshot_items([("xhs", "n1")])
    assert len(snapshots) == 1
    entry = snapshots[0]
    assert entry["note"] == "我的备注"
    assert entry["in_default"] is True
    assert entry["watch_later"] is True
    assert entry["collection_ids"] == [collection["id"]]
    assert entry["result"]["title"] == "露营装备怎么选"


def test_remove_then_restore_round_trip(store):
    collection = store.create_collection("周末出行")
    store.add_item(
        _result(), note="我的备注", in_default=True, watch_later=True,
        collection_ids=[collection["id"]],
    )
    snapshots = store.snapshot_items([("xhs", "n1")])

    assert store.remove_items([("xhs", "n1")]) == 1
    assert store.get_item("xhs", "n1") is None

    stats = store.add_items(snapshots)
    assert stats["added"] == 1

    restored = store.get_item("xhs", "n1")
    assert restored["result"]["title"] == "露营装备怎么选"
    assert restored["note"] == "我的备注"
    assert restored["in_default"] is True
    assert restored["watch_later"] is True
    assert [entry["id"] for entry in restored["collections"]] == [collection["id"]]


def test_snapshot_of_missing_item_is_empty(store):
    assert store.snapshot_items([("xhs", "nope")]) == []


def test_delete_endpoint_returns_restore_payload_and_restore_endpoint_accepts_it():
    """走真实路由，确认两个端点的字段是对得上的。"""
    with TestClient(app) as client:
        created = client.post("/api/library/items", json={
            "result": _result("route1"), "note": "路由备注",
        })
        assert created.status_code in (200, 201), created.text

        # httpx 的 delete() 不支持 json=，用 request() 带 body。
        deleted = client.request("DELETE", "/api/library/items", json={
            "keys": [{"platform": "xhs", "content_id": "route1"}],
        })
        assert deleted.status_code == 200, deleted.text
        payload = deleted.json()
        assert payload["removed"] == 1
        assert payload["restore"], "删除必须返回可用于撤销的快照"

        restored = client.post("/api/library/items/restore", json={"entries": payload["restore"]})
        assert restored.status_code == 200, restored.text
        assert restored.json()["restored"] == 1

        listed = client.get("/api/library/items")
        keys = [item["key"] for item in listed.json()["items"]]
        assert "xhs|route1" in keys


def test_restore_rejects_empty_entries():
    with TestClient(app) as client:
        response = client.post("/api/library/items/restore", json={"entries": []})
    assert response.status_code == 422
