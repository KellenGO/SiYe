"""本地收藏库（SQLite）存储层测试。

覆盖产品方案里明确的几条规则：
- 内容本体只存一份，唯一键 (platform, content_id)；
- 一条内容可以同时属于多个收藏夹；
- 「默认收藏夹」与「稍后再看」彼此独立，移出后内容仍留在「全部」；
- 删除收藏夹保留内容，取消收藏是独立操作；
- 重复收藏保留首次收藏时间与已有备注；
- 兼容旧 localStorage 备份（v1）、旧本机备份（v2）与当前 v3 结构。
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from api.services.library_store import MAX_NOTE_LENGTH, LibraryStore


@pytest.fixture()
def store(tmp_path: Path) -> LibraryStore:
    return LibraryStore(tmp_path / "library.db")


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


def test_add_and_read_item(store: LibraryStore) -> None:
    item = store.add_item(_result(), note="先看这个", fetched_at="2026-09-13T10:00:00")

    assert item["key"] == "xhs|n1"
    assert item["note"] == "先看这个"
    assert item["fetched_at"] == "2026-09-13T10:00:00"
    assert item["result"]["title"] == "露营装备怎么选"
    assert item["result"]["metrics"] == {"likes": 12, "comments": 3}
    assert item["collections"] == []
    assert item["in_default"] is True
    assert item["watch_later"] is False

    stats = store.stats()
    assert stats["total"] == 1
    assert stats["default_count"] == 1
    assert stats["unclassified"] == 0


def test_duplicate_does_not_regress_metrics(store: LibraryStore) -> None:
    """搜索结果先出现、详情指标后补：补全前收藏一次不能把以后补上的值清掉。"""
    store.add_item(_result())
    again = store.add_item(_result(metrics={"comments": 9}))

    assert again["result"]["metrics"] == {"likes": 12, "comments": 9}


def test_duplicate_keeps_first_saved_at_and_existing_note(store: LibraryStore) -> None:
    first = store.add_item(_result(), note="我的备注", saved_at="2026-09-01T00:00:00")
    again = store.add_item(_result(title="标题被更新了"), saved_at="2026-09-10T00:00:00")

    assert again["id"] == first["id"]
    assert again["saved_at"] == "2026-09-01T00:00:00"  # 保留首次收藏时间
    assert again["note"] == "我的备注"  # 不带备注的重复收藏不会清空旧备注
    assert again["result"]["title"] == "标题被更新了"  # 但内容快照会刷新
    assert store.stats()["total"] == 1


def test_one_item_can_belong_to_multiple_collections(store: LibraryStore) -> None:
    ai = store.create_collection("AI 学习")
    later = store.create_collection("待看")

    store.add_item(_result(), collection_ids=[ai["id"], later["id"]])
    item = store.get_item("xhs", "n1")

    assert item is not None
    assert sorted(c["name"] for c in item["collections"]) == ["AI 学习", "待看"]
    assert {c["name"]: c["item_count"] for c in store.list_collections()} == {"AI 学习": 1, "待看": 1}
    # 底层只有一份
    assert store.stats()["total"] == 1
    assert store.stats()["unclassified"] == 0


def test_unclassified_view_excludes_filed_items(store: LibraryStore) -> None:
    collection = store.create_collection("已整理")
    store.add_item(_result(content_id="a"))
    store.remove_items_from_system_collection([("xhs", "a")], "default")
    store.add_item(_result(content_id="b"), collection_ids=[collection["id"]])

    unclassified = store.list_items(only_unclassified=True)
    assert [i["result"]["content_id"] for i in unclassified["items"]] == ["a"]
    assert unclassified["total"] == 1

    filed = store.list_items(collection_id=collection["id"])
    assert [i["result"]["content_id"] for i in filed["items"]] == ["b"]


def test_batch_add_and_remove_from_collection(store: LibraryStore) -> None:
    collection = store.create_collection("批量")
    store.add_item(_result(content_id="a"))
    store.add_item(_result(content_id="b"))
    store.add_item(_result(content_id="c"))

    result = store.add_items_to_collection([("xhs", "a"), ("xhs", "b"), ("xhs", "missing")], collection["id"])
    assert result == {"added": 2, "missing": 1}
    assert store.list_items(collection_id=collection["id"])["total"] == 2

    removed = store.remove_items_from_collection([("xhs", "a")], collection["id"])
    assert removed["removed"] == 1
    # 移出收藏夹不等于取消收藏
    assert store.stats()["total"] == 3
    assert store.list_items(collection_id=collection["id"])["total"] == 1


def test_delete_collection_keeps_items(store: LibraryStore) -> None:
    collection = store.create_collection("临时")
    store.add_item(_result(), collection_ids=[collection["id"]])

    result = store.delete_collection(collection["id"])

    assert result == {"deleted": "临时", "kept_items": 1}
    assert store.stats()["total"] == 1
    assert store.stats()["unclassified"] == 1
    assert store.list_collections() == []


def test_remove_item_is_separate_operation(store: LibraryStore) -> None:
    collection = store.create_collection("夹子")
    store.add_item(_result(), collection_ids=[collection["id"]])

    assert store.remove_items([("xhs", "n1")]) == 1
    assert store.stats()["total"] == 0
    # 关联随之清理，收藏夹本身保留
    assert store.list_collections()[0]["item_count"] == 0


def test_note_is_truncated_and_can_be_updated(store: LibraryStore) -> None:
    store.add_item(_result())
    long_note = "字" * (MAX_NOTE_LENGTH + 50)

    assert store.set_note("xhs", "n1", long_note) is True
    item = store.get_item("xhs", "n1")
    assert item is not None
    assert len(item["note"]) == MAX_NOTE_LENGTH
    assert store.set_note("xhs", "nope", "x") is False


def test_collection_name_rules(store: LibraryStore) -> None:
    store.create_collection("重复")

    with pytest.raises(ValueError):
        store.create_collection("重复")
    with pytest.raises(ValueError):
        store.create_collection("  ")
    with pytest.raises(ValueError):
        store.rename_collection(999, "不存在")
    for reserved in ("全部", "默认收藏夹", "稍后再看"):
        with pytest.raises(ValueError, match="系统收藏夹"):
            store.create_collection(reserved)


def test_export_then_import_roundtrip(store: LibraryStore) -> None:
    collection = store.create_collection("我的夹")
    store.add_item(_result(content_id="a"), note="备注 A", collection_ids=[collection["id"]])
    store.add_item(_result(content_id="b"), note="备注 B")

    payload = store.export_payload()
    assert payload["version"] == 3
    assert len(payload["items"]) == 2

    fresh = LibraryStore(store.db_path.parent / "restored.db")
    stats = fresh.import_payload(json.loads(json.dumps(payload)))

    assert stats["added"] == 2
    assert stats["total"] == 2
    assert fresh.stats()["total"] == 2
    restored = fresh.get_item("xhs", "a")
    assert restored is not None
    assert restored["note"] == "备注 A"
    assert [c["name"] for c in restored["collections"]] == ["我的夹"]


def test_system_collections_are_independent_and_removal_keeps_item(store: LibraryStore) -> None:
    entry = {"result": _result(content_id="later")}
    store.add_items_to_system_collection([entry], "watch_later")
    item = store.get_item("xhs", "later")
    assert item is not None
    assert item["in_default"] is False
    assert item["watch_later"] is True

    store.add_items_to_system_collection([entry], "default")
    item = store.get_item("xhs", "later")
    assert item is not None and item["in_default"] and item["watch_later"]

    assert store.remove_items_from_system_collection([("xhs", "later")], "default") == {"removed": 1}
    item = store.get_item("xhs", "later")
    assert item is not None and not item["in_default"] and item["watch_later"]
    assert store.list_items()["total"] == 1


def test_v3_roundtrip_preserves_system_membership(store: LibraryStore) -> None:
    store.add_item(_result(content_id="none"), in_default=False)
    store.add_item(_result(content_id="both"), in_default=True, watch_later=True)
    fresh = LibraryStore(store.db_path.parent / "v3-restored.db")
    fresh.import_payload(store.export_payload())

    none = fresh.get_item("xhs", "none")
    both = fresh.get_item("xhs", "both")
    assert none is not None and not none["in_default"] and not none["watch_later"]
    assert both is not None and both["in_default"] and both["watch_later"]


def test_import_keeps_historical_reserved_custom_folder(store: LibraryStore) -> None:
    payload = {
        "version": 2,
        "collections": [{"name": "稍后再看"}],
        "items": [{"result": _result(), "collections": [{"name": "稍后再看"}]}],
    }
    store.import_payload(payload)
    assert store.list_collections()[0]["name"] == "稍后再看"
    assert store.get_item("xhs", "n1")["in_default"] is False


def test_import_legacy_localstorage_backup(store: LibraryStore) -> None:
    """旧版浏览器备份：{version:1, items:[{result, savedAt, fetchedAt, note}]}"""
    legacy = {
        "version": 1,
        "items": [
            {
                "result": _result(content_id="old1"),
                "savedAt": "2026-08-01T00:00:00",
                "fetchedAt": "2026-08-01T00:00:00",
                "note": "旧备注",
            },
            {"result": _result(content_id="old2"), "savedAt": "2026-08-02T00:00:00", "fetchedAt": None, "note": ""},
            {"result": {"platform": "xhs"}, "savedAt": "2026-08-03T00:00:00"},  # 缺 content_id，跳过
        ],
    }

    stats = store.import_payload(legacy)

    assert stats == {"added": 2, "updated": 0, "skipped": 1, "total": 3}
    assert store.stats()["total"] == 2
    migrated = store.get_item("xhs", "old1")
    assert migrated is not None
    assert migrated["saved_at"] == "2026-08-01T00:00:00"
    assert migrated["note"] == "旧备注"


def test_import_rejects_bad_payload(store: LibraryStore) -> None:
    with pytest.raises(ValueError):
        store.import_payload({"version": 1})
    with pytest.raises(ValueError):
        store.import_payload({"items": "不是数组"})


def test_list_items_filters_by_platform_and_query(store: LibraryStore) -> None:
    store.add_item(_result(platform="xhs", content_id="a", title="露营装备"))
    store.add_item(_result(platform="douyin", content_id="b", title="拍摄技巧"))
    store.add_item(_result(platform="xhs", content_id="c", author="露营博主", title="别的东西"))

    assert store.list_items(platform="xhs")["total"] == 2
    assert store.list_items(query="露营")["total"] == 2
    assert store.list_items(platform="douyin", query="露营")["total"] == 0


def test_max_items_guard(store: LibraryStore) -> None:
    import api.services.library_store as module

    original = module.MAX_ITEMS
    module.MAX_ITEMS = 2
    try:
        store.add_item(_result(content_id="a"))
        store.add_item(_result(content_id="b"))
        with pytest.raises(ValueError):
            store.add_item(_result(content_id="c"))
    finally:
        module.MAX_ITEMS = original


def test_import_is_atomic_on_invalid_folder(store: LibraryStore) -> None:
    with pytest.raises(ValueError):
        store.import_payload({"version": 2, "items": [], "collections": [
            {"name": "必须回滚"}, {"name": "字" * 61},
        ]})
    assert store.list_collections() == []


def test_import_inline_folders_are_reused(store: LibraryStore) -> None:
    stats = store.import_payload({"items": [
        {"result": _result(content_id="a"), "collections": [{"name": "共用"}]},
        {"result": _result(content_id="b"), "collections": [{"name": "共用"}]},
    ]})
    assert stats["added"] == 2
    assert store.list_collections()[0]["item_count"] == 2


def test_invalid_collection_does_not_leave_an_unfiled_item(store: LibraryStore) -> None:
    with pytest.raises(ValueError, match="收藏夹不存在"):
        store.add_item(_result(), collection_ids=[999])
    assert store.stats()["total"] == 0


@pytest.mark.parametrize("changes", [{"title": []}, {"url": "javascript:alert(1)"}, {"platform": "unknown"}])
def test_invalid_result_is_rejected_before_storage(store: LibraryStore, changes: dict) -> None:
    with pytest.raises(ValueError):
        store.add_item(_result(**changes))
    assert store.stats()["total"] == 0


def test_capacity_reports_partial_import_without_erasing_old_items(store: LibraryStore, monkeypatch) -> None:
    monkeypatch.setattr("api.services.library_store.MAX_ITEMS", 2)
    store.add_item(_result(content_id="old"), note="保留")
    stats = store.import_payload({"items": [
        {"result": _result(content_id="new1")}, {"result": _result(content_id="new2")},
    ]})
    assert stats == {"added": 1, "updated": 0, "skipped": 1, "total": 2}
    assert store.get_item("xhs", "old")["note"] == "保留"


def test_old_schema_upgrade_moves_only_unfiled_items_to_default(tmp_path: Path) -> None:
    db_path = tmp_path / "old-library.db"
    with sqlite3.connect(db_path) as conn:
        conn.executescript("""
        CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE collections (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL UNIQUE COLLATE NOCASE, position INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL);
        CREATE TABLE items (id INTEGER PRIMARY KEY AUTOINCREMENT, platform TEXT NOT NULL, content_id TEXT NOT NULL, content_type TEXT NOT NULL DEFAULT '', title TEXT NOT NULL DEFAULT '', snippet TEXT, author TEXT NOT NULL DEFAULT '', url TEXT NOT NULL DEFAULT '', published_at TEXT, cover_url TEXT, metrics TEXT NOT NULL DEFAULT '{}', note TEXT NOT NULL DEFAULT '', saved_at TEXT NOT NULL, fetched_at TEXT, UNIQUE(platform, content_id));
        CREATE TABLE item_collections (item_id INTEGER NOT NULL, collection_id INTEGER NOT NULL, added_at TEXT NOT NULL, PRIMARY KEY(item_id, collection_id));
        INSERT INTO collections(id,name,position,created_at) VALUES (1,'资料',1,'2026-09-01');
        INSERT INTO items(id,platform,content_id,title,url,saved_at) VALUES (1,'xhs','loose','未分类','https://example.com/loose','2026-09-01');
        INSERT INTO items(id,platform,content_id,title,url,saved_at) VALUES (2,'xhs','filed','已分类','https://example.com/filed','2026-09-01');
        INSERT INTO item_collections(item_id,collection_id,added_at) VALUES (2,1,'2026-09-01');
        """)

    upgraded = LibraryStore(db_path)
    assert upgraded.get_item("xhs", "loose")["in_default"] is True
    assert upgraded.get_item("xhs", "filed")["in_default"] is False
    assert upgraded.get_item("xhs", "loose")["watch_later"] is False
    assert upgraded.stats()["default_count"] == 1
