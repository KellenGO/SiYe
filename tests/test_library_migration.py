"""Stable library path and lossless legacy database merge tests."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from api.services.library_migration import migrate_legacy_libraries
from api.services.library_store import LibraryStore
from api.services.remote_favorites_store import RemoteFavoritesStore
from base.runtime_paths import library_data_root


def _result(platform: str, content_id: str, title: str = "旧收藏") -> dict:
    return {
        "platform": platform,
        "content_id": content_id,
        "content_type": "note",
        "title": title,
        "url": f"https://example.com/{platform}/{content_id}",
    }


def test_stable_data_directory_prefers_explicit_override_then_local_appdata(tmp_path: Path, monkeypatch) -> None:
    explicit = tmp_path / "explicit"
    local = tmp_path / "local-app-data"
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    monkeypatch.setenv("SIYE_DATA_DIR", str(explicit))
    assert library_data_root() == explicit.resolve()

    monkeypatch.delenv("SIYE_DATA_DIR")
    assert library_data_root() == local.resolve() / "SiYe" / "data"


def test_merges_known_legacy_paths_and_is_idempotent(tmp_path: Path, monkeypatch) -> None:
    app_root = tmp_path / "app"
    source_a = app_root / "data" / "library.db"
    source_b = app_root / "dist" / "MediaCrawler" / "data" / "library.db"
    target_dir = tmp_path / "stable"
    monkeypatch.setenv("SIYE_DATA_DIR", str(target_dir))

    first = LibraryStore(source_a)
    historical = first.ensure_imported_collection("稍后再看")
    first.add_item(_result("douyin", "same", "来源旧标题"), note="旧备注", collection_ids=[historical["id"]])
    remote_a = RemoteFavoritesStore(source_a)
    remote_a.save_platform("douyin", [_result("douyin", "remote-d")], status="succeeded")
    remote_a.save_platform("zhihu", [_result("zhihu", "remote-z")], status="succeeded")

    second = LibraryStore(source_b)
    second.add_item(_result("douyin", "same", "来源新标题"), note="不应覆盖", in_default=True)
    second.add_item(_result("xhs", "local-x"), in_default=True, watch_later=True)
    remote_b = RemoteFavoritesStore(source_b)
    remote_b.save_platform("xhs", [_result("xhs", "remote-x")], status="succeeded")
    remote_b.save_platform("bilibili", [_result("bilibili", "remote-b")], status="succeeded")

    target_path = target_dir / "library.db"
    target = LibraryStore(target_path)
    target.add_item(_result("douyin", "same", "目标标题"), note="本机备注", in_default=False)

    summary = migrate_legacy_libraries(app_root)
    merged = LibraryStore(target_path)
    merged_item = merged.get_item("douyin", "same")
    assert summary["source_count"] == 2
    assert summary["local_items"] == 1
    assert summary["remote_items"] == 4
    assert merged_item is not None
    assert merged_item["note"] == "本机备注"
    assert merged_item["in_default"] is True
    assert [folder["name"] for folder in merged_item["collections"]] == ["稍后再看"]
    assert merged.get_item("xhs", "local-x")["watch_later"] is True
    assert merged.list_collections()[0]["name"] == "稍后再看"
    assert {item["platform"] for item in RemoteFavoritesStore(target_path).load()["results"]} == {
        "xhs", "douyin", "bilibili", "zhihu",
    }
    assert source_a.is_file() and source_b.is_file()

    backups = list((target_dir / "migration-backups").glob("*.db"))
    repeated = migrate_legacy_libraries(app_root)
    assert repeated["source_count"] == 0
    assert len(list((target_dir / "migration-backups").glob("*.db"))) == len(backups)
    assert merged.stats()["total"] == 2


def test_corrupt_source_keeps_existing_target_and_reports_warning(tmp_path: Path, monkeypatch) -> None:
    app_root = tmp_path / "app"
    source = app_root / "data" / "library.db"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"not a sqlite database")
    target_dir = tmp_path / "stable"
    monkeypatch.setenv("SIYE_DATA_DIR", str(target_dir))
    target = LibraryStore(target_dir / "library.db")
    target.add_item(_result("xhs", "keep"), note="必须保留")

    summary = migrate_legacy_libraries(app_root)

    assert summary["source_count"] == 0
    assert summary["warnings"] == ["DatabaseError"]
    assert LibraryStore(target_dir / "library.db").get_item("xhs", "keep")["note"] == "必须保留"
    with sqlite3.connect(target_dir / "library.db") as conn:
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_partially_invalid_source_rolls_back_its_local_updates(tmp_path: Path, monkeypatch) -> None:
    app_root = tmp_path / "app"
    source_path = app_root / "data" / "library.db"
    source = LibraryStore(source_path)
    source.add_item(_result("xhs", "same", "坏来源的新标题"), note="来源备注")
    remote = RemoteFavoritesStore(source_path)
    remote.save_platform("xhs", [_result("xhs", "remote")], status="succeeded")
    with sqlite3.connect(source_path) as conn:
        conn.execute("UPDATE remote_favorites SET collection_names='not-json'")

    target_dir = tmp_path / "stable"
    monkeypatch.setenv("SIYE_DATA_DIR", str(target_dir))
    target = LibraryStore(target_dir / "library.db")
    target.add_item(_result("xhs", "same", "目标原标题"), note="目标备注")

    summary = migrate_legacy_libraries(app_root)
    preserved = LibraryStore(target_dir / "library.db").get_item("xhs", "same")

    assert summary["source_count"] == 0
    assert summary["local_items"] == 0
    assert summary["warnings"] == ["JSONDecodeError"]
    assert preserved is not None
    assert preserved["result"]["title"] == "目标原标题"
    assert preserved["note"] == "目标备注"
    assert RemoteFavoritesStore(target_dir / "library.db").load() is None
