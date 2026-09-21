"""Unbounded sync and reconciliation use isolated databases and fake platform I/O."""
import asyncio
import json

import pytest

from aggregate_search.favorites_sync import BilibiliFavoritesSource, synchronize_favorites
from api.schemas.favorites import MissingDecision, FavoritesJobRequest
from api.services.remote_favorites_store import RemoteFavoritesStore
from api.services.favorites_job_manager import FavoritesJobManager


class FavoriteClient:
    def __init__(self, folders=None, mid=1):
        self.mid = mid
        self.contents = folders if folders is not None else {10: list(range(125))}
        self.calls = []
        self.fail_page = None

    async def get(self, *_args, **_kwargs):
        return {"mid": self.mid}

    async def get_created_favorite_folders(self, _mid):
        return {"count": len(self.contents), "list": [
            {"id": fid, "title": f"folder-{fid}", "media_count": len(rows)}
            for fid, rows in self.contents.items()]}

    async def get_favorite_folder_contents(self, fid, page, size):
        self.calls.append((fid, page))
        if page == self.fail_page:
            raise OSError("synthetic network failure")
        rows = self.contents[fid][(page - 1) * size:page * size]
        return {"medias": [{"bvid": f"BV{i}", "id": i + 1, "title": f"video-{i}",
                             "fav_time": 100000 - i, "cnt_info": {"play": 0}}
                            for i in rows], "has_more": page * size < len(self.contents[fid])}


@pytest.fixture
def store(tmp_path):
    return RemoteFavoritesStore(tmp_path / "library.db")


async def sync(store, client, mode="auto", verified=False):
    source = BilibiliFavoritesSource(client, interval=0)
    source.incremental_verified = verified
    await synchronize_favorites(source, store, mode, lambda *_: None)


def decisions(store, action):
    return [MissingDecision(id=item["id"], missing_batch=item["missing_batch"], action=action)
            for item in store.archive_page(state="pending")["items"]]


@pytest.mark.asyncio
async def test_full_import_over_100_and_duplicate_folders(store):
    client = FavoriteClient({10: list(range(125)), 11: [1, 2], 12: []})
    await sync(store, client)
    assert store.archive_page()["total"] == 125
    assert len(store.archive_page()["items"]) == 50
    row = store.archive_page(offset=120)["items"][-2]
    assert row["result"]["metrics"]["view_count"] == 0
    with store._conn() as conn:
        assert conn.execute("SELECT count(*) FROM remote_memberships WHERE content_id='BV1'").fetchone()[0] == 2


@pytest.mark.asyncio
async def test_missing_keep_remove_reappearance_and_stale_decision(store):
    client = FavoriteClient({10: [1, 2, 3]})
    await sync(store, client, "full")
    # Use a sentinel unrelated table to prove decisions do not mutate library data.
    with store._conn() as conn:
        conn.execute("CREATE TABLE local_notes(note TEXT)")
        conn.execute("INSERT INTO local_notes VALUES('keep me')")
    client.contents = {10: [3]}
    await sync(store, client, "auto")
    assert store.archive_summary()["pending_count"] == 0
    await sync(store, client, "full")
    old = decisions(store, "remove")
    assert len(old) == 2
    keep = old[0].model_copy(update={"action": "keep"})
    assert store.resolve_missing([keep])["applied"] == 1
    assert store.resolve_missing([keep])["applied"] == 0
    await sync(store, client, "full")
    assert store.archive_page(state="archived")["total"] == 1
    assert store.resolve_missing([old[1]])["applied"] == 1
    client.contents = {10: [1, 2, 3]}
    await sync(store, client)
    assert store.archive_page(state="present")["total"] == 3
    assert store.resolve_missing(old)["applied"] == 0
    with store._conn() as conn:
        assert conn.execute("SELECT note FROM local_notes").fetchone()[0] == "keep me"
    client.contents = {10: []}
    await sync(store, client, "full")
    assert all(d.missing_batch != old[0].missing_batch for d in decisions(store, "keep"))


@pytest.mark.asyncio
async def test_move_rename_deleted_folder_and_account_isolation(store):
    client = FavoriteClient({10: [1], 11: []})
    await sync(store, client, "full")
    client.contents = {12: [1]}
    await sync(store, client, "full")
    item = store.archive_page()["items"][0]
    assert item["result"]["collection_names"] == ["folder-12"]
    assert item["state"] == "present"
    await sync(store, FavoriteClient({10: [1]}, mid=2), "full")
    client.contents = {}
    await sync(store, client, "full")
    store.resolve_missing(decisions(store, "remove"))
    assert store.archive_page(account="bilibili:2")["total"] == 1


@pytest.mark.asyncio
async def test_interruption_resume_and_full_restart(store):
    client = FavoriteClient()
    client.fail_page = 4
    with pytest.raises(OSError):
        await sync(store, client)
    assert store.archive_page()["total"] == 60
    assert store.archive_summary()["pending_count"] == 0
    client.fail_page = None
    client.calls.clear()
    await sync(store, client)
    assert (10, 2) not in client.calls  # head + boundary, then remaining pages
    assert store.archive_page()["total"] == 125
    client.calls.clear()
    await sync(store, client, "full")
    assert (10, 2) in client.calls


@pytest.mark.asyncio
async def test_resume_drift_restarts_folder(store):
    client = FavoriteClient()
    client.fail_page = 3
    with pytest.raises(OSError):
        await sync(store, client)
    client.contents[10][0] = 999
    client.fail_page = None
    client.calls.clear()
    await sync(store, client)
    assert (10, 2) in client.calls
    assert store.archive_page()["total"] == 126  # old item retained until full check


@pytest.mark.asyncio
async def test_verified_incremental_and_conservative_fallback(store):
    client = FavoriteClient({10: list(range(400))})
    await sync(store, client, "full")
    client.calls.clear()
    await sync(store, client, verified=True)
    assert len(client.calls) == 3  # two overlap pages, head verification
    client.contents[10] = [999, 998, *client.contents[10]]
    client.calls.clear()
    await sync(store, client, verified=True)
    assert max(page for _, page in client.calls) == 3
    assert store.archive_page()["total"] == 402
    client.calls.clear()
    await sync(store, client)
    assert max(page for _, page in client.calls) == 21


@pytest.mark.asyncio
async def test_incremental_boundary_is_folder_scoped(store):
    client = FavoriteClient({10: list(range(100)), 11: []})
    await sync(store, client, "full")
    client.contents[11] = list(range(100))
    client.calls.clear()
    await sync(store, client, verified=True)
    assert (11, 5) in client.calls


@pytest.mark.asyncio
async def test_same_time_and_recollection_do_not_stop_at_first_known_item(store):
    client = FavoriteClient({10: list(range(160))})
    original = client.get_favorite_folder_contents
    async def same_time(*args):
        response = await original(*args)
        for row in response["medias"]:
            row["fav_time"] = 12345
        return response
    client.get_favorite_folder_contents = same_time
    await sync(store, client, "full")
    client.contents[10] = [159, 999, *range(159)]
    client.calls.clear()
    await sync(store, client, verified=True)
    assert store.archive_page()["total"] == 161
    assert max(page for _, page in client.calls) == 3


@pytest.mark.asyncio
async def test_changed_head_during_full_scan_does_not_mark_missing(store):
    client = FavoriteClient({10: list(range(50))})
    await sync(store, client, "full")
    client.contents[10] = list(range(1, 50))
    original = client.get_favorite_folder_contents
    heads = 0
    async def drift(fid, page, size):
        nonlocal heads
        if page == 1:
            heads += 1
            if heads == 2:
                client.contents[10][0] = 999
        return await original(fid, page, size)
    client.get_favorite_folder_contents = drift
    with pytest.raises(ValueError):
        await sync(store, client, "full")
    assert store.archive_summary()["pending_count"] == 0


@pytest.mark.asyncio
async def test_disk_failure_rolls_back_page_and_checkpoint(store, monkeypatch):
    original = store.save_platform
    def fail(*args, **kwargs):
        original(*args, **kwargs)
        raise OSError("disk failed")
    monkeypatch.setattr(store, "save_platform", fail)
    with pytest.raises(OSError):
        await sync(store, FavoriteClient())
    assert store.archive_page()["total"] == 0
    assert store.checkpoint("bilibili:1", "10") is None


@pytest.mark.asyncio
async def test_duplicate_page_rejected_without_missing_judgment(store):
    client = FavoriteClient({10: list(range(20)) * 2})
    with pytest.raises(ValueError):
        await sync(store, client, "full")
    assert store.archive_summary()["pending_count"] == 0
    assert store.archive_page()["total"] == 20


@pytest.mark.asyncio
async def test_pending_survives_restart_and_legacy_unclaimed(store, tmp_path):
    store.save_platform("bilibili", [{"content_id": "legacy"}], status="succeeded")
    client = FavoriteClient({10: [1]})
    await sync(store, client, "full")
    client.contents = {}
    await sync(store, client, "full")
    reopened = RemoteFavoritesStore(store.db_path)
    assert reopened.archive_page(state="pending")["total"] == 1
    assert reopened.archive_page(state="legacy")["total"] == 1


@pytest.mark.asyncio
async def test_archive_page_groups_by_platform(store):
    """归档分页按平台分组、组内最近入库在前：翻页时同一平台是连续的，不会跨平台乱跳。"""
    store.save_platform("xhs", [{"platform": "xhs", "content_id": f"x{i}"} for i in range(3)], status="succeeded")
    store.save_platform("bilibili", [{"platform": "bilibili", "content_id": f"b{i}"} for i in range(2)], status="succeeded")

    items = store.archive_page(limit=10)["items"]
    assert [item["result"]["platform"] for item in items] == ["bilibili", "bilibili", "xhs", "xhs", "xhs"]
    # 组内：后入库的排在前面
    assert [item["result"]["content_id"] for item in items] == ["b1", "b0", "x2", "x1", "x0"]
    # 收窄到某一个平台时，分页范围也跟着变
    assert store.archive_page(platform="xhs", limit=10)["total"] == 3


@pytest.mark.asyncio
async def test_summary_counts_states_so_legacy_need_not_be_listed(store):
    """摘要给出各类状态的条数：界面靠它做汇总，不必把「账号未确认」逐条铺出来。"""
    store.save_platform("bilibili", [{"content_id": "legacy"}], status="succeeded")
    client = FavoriteClient({10: list(range(25))})
    await sync(store, client, "full")

    summary = store.archive_summary()
    assert summary["states"]["present"] == 25
    assert summary["states"]["legacy"] == 1
    assert summary["states"]["pending"] == 0
    # save_platform 不建账号行；老版本用默认账号开过扫描时，账号行要能被标成 legacy
    store.begin_scan("bilibili", "default", [{"id": "10"}], "full")
    legacy_accounts = [a for a in store.archive_summary()["accounts"] if a["account"] == "default"]
    assert legacy_accounts and legacy_accounts[0]["legacy"] is True


@pytest.mark.asyncio
async def test_purge_legacy_archive_keeps_confirmed_accounts(store):
    """清理历史归档只删「账号未确认」那一份，已确认账号的归档不受影响。"""
    store.save_platform("bilibili", [{"content_id": "legacy-1"}, {"content_id": "legacy-2"}], status="succeeded")
    client = FavoriteClient({10: [1]})
    await sync(store, client, "full")

    assert store.purge_legacy_archive() == {"removed": 2}
    assert store.archive_page(state="legacy")["total"] == 0
    assert store.archive_page(state="present")["total"] == 1
    # 再清一次是幂等的，且不会连已确认账号一起删
    assert store.purge_legacy_archive() == {"removed": 0}
    assert store.archive_page()["total"] == 1


@pytest.mark.asyncio
async def test_sync_conflicts_use_a_dedicated_error_type(store):
    """重复页这类可预期冲突要是 SyncStateError，别和普通 ValueError（代码 bug）混在一起。"""
    from api.services.remote_sync_state import SyncStateError

    scan = store.begin_scan("bilibili", "bilibili:1", [{"id": "10"}], "full")
    store.save_sync_page("bilibili", "bilibili:1", scan, "10", "收藏夹", 1, "same",
                         [{"platform": "bilibili", "content_id": "BV1"}], False, 20)
    with pytest.raises(SyncStateError):
        store.save_sync_page("bilibili", "bilibili:1", scan, "10", "收藏夹", 2, "same",
                             [{"platform": "bilibili", "content_id": "BV2"}], True, 20)
    assert issubclass(SyncStateError, ValueError)


@pytest.mark.asyncio
async def test_large_archive_summary_does_not_load_results(store, monkeypatch):
    for offset in range(0, 10000, 50):
        store.save_platform("bilibili", [{"platform": "bilibili", "content_id": str(i)} for i in range(offset, offset + 50)], status="succeeded", record_run=False)
    monkeypatch.setattr("api.services.favorites_job_manager.get_remote_favorites_store", lambda: store)
    monkeypatch.setattr(store, "load", lambda: pytest.fail("summary loaded whole archive"))
    response = await FavoritesJobManager().latest(summary=True)
    assert response.counts["bilibili"] == 10000
    assert response.results == []
    assert len(response.model_dump_json()) < 2000
    assert len(store.archive_page(offset=9900)["items"]) == 50


@pytest.mark.asyncio
async def test_request_timeout_and_cancel_preserve_pages(store, monkeypatch):
    client = FavoriteClient()
    original = client.get_favorite_folder_contents
    async def cancelled(fid, page, size):
        if page == 2:
            raise asyncio.CancelledError()
        return await original(fid, page, size)
    client.get_favorite_folder_contents = cancelled
    with pytest.raises(asyncio.CancelledError):
        await sync(store, client, "full")
    assert store.archive_page()["total"] == 20
    assert store.archive_summary()["pending_count"] == 0


def test_archive_api_validation_and_idempotent_decisions(store, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from api.routers.search import search_router
    monkeypatch.setattr("api.routers.search.get_remote_favorites_store", lambda: store)
    app = FastAPI()
    app.include_router(search_router)
    with TestClient(app) as client:
        assert client.get("/api/search/favorites/archive?limit=1000").status_code == 422
        assert client.get("/api/search/favorites/archive?offset=-1").status_code == 422
        assert client.get("/api/search/favorites/archive?state=invalid").status_code == 422
        response = client.get("/api/search/favorites/archive?state=pending")
        assert response.json()["items"] == []
        decision = {"decisions": [{"id": 99, "missing_batch": "old", "action": "remove"}]}
        assert client.post("/api/search/favorites/missing/resolve", json=decision).json() == {"applied": 0, "stale": 1}
        assert client.post("/api/search/favorites/missing/resolve", json=decision).json() == {"applied": 0, "stale": 1}


@pytest.mark.asyncio
@pytest.mark.parametrize("status,code", [(429, 0), (403, 0), (500, -500), (200, -352), (200, -412)])
async def test_new_client_does_not_retry_restricted_responses(monkeypatch, status, code):
    import httpx
    from unittest.mock import AsyncMock
    from media_platform.bilibili.client import BilibiliClient
    client = BilibiliClient(headers={}, playwright_page=None, cookie_dict={})
    client.favorites_sync = True
    send = AsyncMock(return_value=httpx.Response(status, json={"code": code, "message": "test"}))
    monkeypatch.setattr(client, "_send", send)
    with pytest.raises(Exception):
        await client.request("GET", "/x/v3/fav/resource/list")
    assert send.await_count == 1


@pytest.mark.asyncio
async def test_paged_worker_protocol_persists_without_full_result_events(store, monkeypatch):
    import sys
    monkeypatch.setenv("SIYE_DATA_DIR", str(store.db_path.parent))
    monkeypatch.setattr("api.services.favorites_job_manager.get_remote_favorites_store", lambda: store)
    monkeypatch.setattr("api.services.favorites_job_manager._TIMEOUT", 0.01)
    code = '''import sys,json,time
from api.services.remote_favorites_store import get_remote_favorites_store
r=json.loads(sys.stdin.readline())
assert r['sync_mode']=='auto'
s=get_remote_favorites_store()
scan=s.begin_scan('bilibili','bilibili:1',[], 'auto')
for event,data in [('status',{'status':'running','account':'bilibili:1','result_count':0})]:
 print('MC_AGG_EVENT\\t'+json.dumps({'event':event,'job_id':r['job_id'],'platform':'bilibili','data':data}),flush=True)
time.sleep(.1)
s.finish_scan('bilibili:1',scan,False)
for event,data in [('status',{'status':'succeeded'}),('done',{})]:
 print('MC_AGG_EVENT\\t'+json.dumps({'event':event,'job_id':r['job_id'],'platform':'bilibili','data':data}),flush=True)
'''
    monkeypatch.setattr("api.services.worker_process.worker_command", lambda *args: [sys.executable, "-c", code])
    manager = FavoritesJobManager()
    created = await manager.create(FavoritesJobRequest(platforms=["bilibili"], sync_mode="auto"), summary=True)
    await asyncio.wait_for(manager.active_task(), 15)
    response = await manager.get(created.job_id, summary=True)
    assert response.platforms["bilibili"].status == "succeeded"
    assert response.accounts[0]["account"] == "bilibili:1"
    assert response.results == []
    assert manager._recent.items["bilibili"] == []
