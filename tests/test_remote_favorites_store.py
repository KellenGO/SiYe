"""跨平台收藏同步结果持久化测试（api/services/remote_favorites_store.py）。

覆盖产品方案里明确的持久化规则：
- 上次结果可读回（含各平台状态与同步时间）；
- 按 (账号, 平台, 内容ID) 去重合并、更新已有条目；
- 本次没取到的旧条目**不删除**；
- 某个平台失败不影响其他平台已保存的数据；
- 不同账号分开保存；
- 从未同步过时返回 None。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from api.services.remote_favorites_store import DEFAULT_ACCOUNT_KEY, RemoteFavoritesStore


@pytest.fixture()
def store(tmp_path: Path) -> RemoteFavoritesStore:
    return RemoteFavoritesStore(tmp_path / "library.db")


def _result(platform: str = "xhs", content_id: str = "n1", **overrides: object) -> dict:
    base = {
        "platform": platform,
        "content_id": content_id,
        "content_type": "note",
        "title": "标题",
        "snippet": "摘要",
        "author": "作者",
        "url": "https://example.com/x",
        "published_at": "2026-09-01T00:00:00Z",
        "cover_url": "https://example.com/c.jpg",
        "metrics": {"like_count": 5},
        "collection_names": ["稍后学习"],
    }
    base.update(overrides)
    return base


def test_load_returns_none_before_first_sync(store: RemoteFavoritesStore) -> None:
    assert store.load() is None


def test_save_then_load_round_trip(store: RemoteFavoritesStore) -> None:
    written = store.save_platform("xhs", [_result()], status="succeeded", requested_limit=100)
    assert written == 1

    saved = store.load()
    assert saved is not None
    assert saved["overall"] == "completed"
    assert saved["job_id"] == "saved"
    assert saved["platforms"]["xhs"]["status"] == "succeeded"
    assert saved["platforms"]["xhs"]["result_count"] == 1
    assert saved["completed_at"]

    result = saved["results"][0]
    assert result["content_id"] == "n1"
    assert result["metrics"] == {"like_count": 5}
    assert result["collection_names"] == ["稍后学习"]
    assert result["grouped_sources"] is None  # 前端 ResultTabs 会读这个字段


def test_resync_updates_existing_item_without_duplicating(store: RemoteFavoritesStore) -> None:
    store.save_platform("xhs", [_result()], status="succeeded")
    store.save_platform("xhs", [_result(title="标题已更新")], status="succeeded")

    saved = store.load()
    assert saved is not None
    assert len(saved["results"]) == 1
    assert saved["results"][0]["title"] == "标题已更新"
    assert saved["platforms"]["xhs"]["result_count"] == 1


def test_items_missing_from_this_run_are_kept(store: RemoteFavoritesStore) -> None:
    """本次只取到最新 100 条时，未出现的旧条目不能当作"已取消收藏"删掉。"""
    store.save_platform("xhs", [_result(content_id="old"), _result(content_id="new")], status="succeeded")
    store.save_platform("xhs", [_result(content_id="new")], status="succeeded")

    saved = store.load()
    assert saved is not None
    ids = sorted(item["content_id"] for item in saved["results"])
    assert ids == ["new", "old"]
    # 本次实际写入条数只反映本次取到的
    assert saved["platforms"]["xhs"]["result_count"] == 1


def test_platform_failure_keeps_other_platforms_data(store: RemoteFavoritesStore) -> None:
    store.save_platform("xhs", [_result(platform="xhs")], status="succeeded")
    store.save_platform("zhihu", [], status="login_required", error_summary="登录已失效")

    saved = store.load()
    assert saved is not None
    assert saved["platforms"]["xhs"]["status"] == "succeeded"
    assert saved["platforms"]["zhihu"]["status"] == "login_required"
    assert saved["platforms"]["zhihu"]["error_summary"] == "登录已失效"
    # 失败平台不清空自己之前的数据，也不影响其他平台
    assert [item["platform"] for item in saved["results"]] == ["xhs"]
    assert saved["overall"] == "partial"


def test_two_login_failures_keep_all_four_platforms_after_restart(store: RemoteFavoritesStore) -> None:
    for platform in ("xhs", "douyin", "bilibili", "zhihu"):
        store.save_platform(platform, [_result(platform=platform, content_id=f"old-{platform}")], status="succeeded")

    store.save_platform("xhs", [], status="login_required", error_summary="未登录")
    store.save_platform("bilibili", [], status="login_required", error_summary="未登录")
    saved = RemoteFavoritesStore(store.db_path).load()

    assert saved is not None
    assert {(item["platform"], item["content_id"]) for item in saved["results"]} == {
        ("xhs", "old-xhs"), ("douyin", "old-douyin"),
        ("bilibili", "old-bilibili"), ("zhihu", "old-zhihu"),
    }
    assert saved["platforms"]["xhs"]["status"] == "login_required"
    assert saved["platforms"]["bilibili"]["status"] == "login_required"
    assert saved["platforms"]["xhs"]["result_count"] == 0


def test_accounts_are_isolated(store: RemoteFavoritesStore) -> None:
    store.save_platform("xhs", [_result(content_id="mine")], status="succeeded", account_key="account-a")
    store.save_platform("xhs", [_result(content_id="other")], status="succeeded", account_key="account-b")

    mine = store.load("account-a")
    other = store.load("account-b")
    assert mine is not None and other is not None
    assert [item["content_id"] for item in mine["results"]] == ["mine"]
    assert [item["content_id"] for item in other["results"]] == ["other"]
    assert store.load(DEFAULT_ACCOUNT_KEY) is None


def test_clear_removes_saved_snapshot(store: RemoteFavoritesStore) -> None:
    store.save_platform("xhs", [_result()], status="succeeded")
    assert store.clear() == 1
    assert store.load() is None


def test_run_history_keeps_latest_status_per_platform(store: RemoteFavoritesStore) -> None:
    store.save_platform("xhs", [], status="failed", error_summary="平台风控")
    store.save_platform("xhs", [_result()], status="succeeded")

    saved = store.load()
    assert saved is not None
    assert saved["platforms"]["xhs"]["status"] == "succeeded"
    assert saved["platforms"]["xhs"]["error_summary"] is None


def test_latest_snapshot_time_reflects_most_recent_run(store: RemoteFavoritesStore) -> None:
    store.save_platform("xhs", [_result()], status="succeeded")
    first = store.load()
    store.save_platform("zhihu", [_result(platform="zhihu")], status="succeeded")
    second = store.load()

    assert first is not None and second is not None
    assert second["completed_at"] >= first["completed_at"]
    assert set(second["platforms"]) == {"xhs", "zhihu"}


def test_approximate_metrics_remain_approximate_after_restart(store: RemoteFavoritesStore) -> None:
    store.save_platform("xhs", [_result(metrics_status="partial", metrics_approximate=["like_count"], metrics_updated_at=123.0)], status="succeeded")
    result = RemoteFavoritesStore(store.db_path).load()["results"][0]
    assert result["metrics_status"] == "partial"
    assert result["metrics_approximate"] == ["like_count"]
    assert result["metrics_updated_at"] == 123.0
