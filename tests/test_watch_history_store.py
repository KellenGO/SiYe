"""观看历史（views 表）存储层测试。

覆盖产品方案里明确的几条规则：
- 同一条内容只存一份，唯一键 (platform, content_id)；
- 再次观看只更新 last_viewed_at 与 view_count，不新增行、保留首次浏览时间；
- 只保留最近 1000 条，写入时按 last_viewed_at 滚动淘汰最旧的；
- 删除单条、清空。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from api.services.watch_history_store import MAX_VIEWS, ViewHistoryStore


@pytest.fixture()
def store(tmp_path: Path) -> ViewHistoryStore:
    return ViewHistoryStore(tmp_path / "history.db")


def _result(platform: str = "xhs", content_id: str = "n1", **overrides: object) -> dict:
    base = {
        "platform": platform,
        "content_id": content_id,
        "content_type": "note",
        "title": "露营装备怎么选",
        "snippet": "摘要文本",
        "author": "某作者",
        "url": "https://example.com/n1",
        "published_at": "2026-09-01T00:00:00",
        "cover_url": "https://example.com/cover.jpg",
        "metrics": {"likes": 12, "comments": 3},
    }
    base.update(overrides)
    return base


def test_record_insert_and_read(store: ViewHistoryStore) -> None:
    view = store.record_view(_result())
    assert view["key"] == "xhs|n1"
    assert view["view_count"] == 1
    assert view["first_viewed_at"] == view["last_viewed_at"]
    assert store.list_views()["total"] == 1


def test_duplicate_only_updates_not_inserts(store: ViewHistoryStore) -> None:
    """再次观看：同一条内容不新增行，计数 +1，首次浏览时间保留。"""
    first = store.record_view(_result())
    again = store.record_view(_result(title="标题被更新了"))

    assert again["id"] == first["id"]
    assert again["view_count"] == 2
    assert again["result"]["title"] == "标题被更新了"  # 内容快照刷新
    assert again["first_viewed_at"] == first["first_viewed_at"]  # 保留首次浏览时间
    assert again["last_viewed_at"] >= first["last_viewed_at"]  # 最后浏览时间推进
    assert store.list_views()["total"] == 1


def test_invalid_result_is_rejected_before_storage(store: ViewHistoryStore) -> None:
    with pytest.raises(ValueError):
        store.record_view(_result(url="javascript:alert(1)"))
    with pytest.raises(ValueError):
        store.record_view(_result(platform="", content_id="n1"))
    with pytest.raises(ValueError):
        store.record_view(_result(content_id=""))
    assert store.list_views()["total"] == 0


def test_max_views_constant_is_1000(store: ViewHistoryStore) -> None:
    assert MAX_VIEWS == 1000


def test_capacity_rolls_oldest(store: ViewHistoryStore, monkeypatch) -> None:
    """写入超过上限时，最旧的内容被淘汰，最新的保留。"""
    monkeypatch.setattr("api.services.watch_history_store.MAX_VIEWS", 50)
    for i in range(60):
        store.record_view(_result(content_id=f"c{i}"))

    assert store.list_views()["total"] == 50
    # 最早写入的被淘汰
    assert store.get_view("xhs", "c0") is None
    # 最近写入的保留
    assert store.get_view("xhs", "c59") is not None


def test_list_orders_by_last_viewed_desc(store: ViewHistoryStore) -> None:
    store.record_view(_result(content_id="a"))
    store.record_view(_result(content_id="b"))
    # 再次观看 a -> 排到最前
    store.record_view(_result(content_id="a"))

    items = store.list_views()["items"]
    assert items[0]["key"] == "xhs|a"


def test_delete_single(store: ViewHistoryStore) -> None:
    store.record_view(_result())
    assert store.delete_view("xhs", "n1") == 1
    assert store.get_view("xhs", "n1") is None
    assert store.list_views()["total"] == 0


def test_clear(store: ViewHistoryStore) -> None:
    store.record_view(_result(content_id="a"))
    store.record_view(_result(content_id="b"))
    assert store.clear() == 2
    assert store.list_views()["total"] == 0
