"""B站收藏的分页全量导入与增量更新：隔离数据库 + 假平台 I/O。"""
import asyncio
import json

import pytest

from aggregate_search.favorites_sync import BilibiliFavoritesSource, synchronize_favorites
from api.schemas.favorites import FavoritesJobRequest
from api.services.remote_favorites_store import RemoteFavoritesStore
from api.services.favorites_job_manager import FavoritesJobManager


class FavoriteClient:
    def __init__(self, folders=None, mid=1):
        self.mid = mid
        self.contents = folders if folders is not None else {10: list(range(125))}
        self.calls = []
        self.fail_page = None
        self.short_pages = set()

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
        if (fid, page) in self.short_pages:
            rows = rows[:-1]
        return {"medias": [{"bvid": f"BV{i}", "id": i + 1, "title": f"video-{i}",
                             "fav_time": 100000 - i, "cnt_info": {"play": 0}}
                            for i in rows], "has_more": page * size < len(self.contents[fid])}


@pytest.fixture
def store(tmp_path):
    return RemoteFavoritesStore(tmp_path / "library.db")


async def sync(store, client, mode="auto", verified=True):
    """`full` = 完整重扫；`auto` = 一键同步（增量）。verified=False 用于只导入、不提前停。"""
    source = BilibiliFavoritesSource(client, interval=0)
    source.incremental_verified = verified
    return await synchronize_favorites(source, store, mode, lambda *_: None)


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
async def test_move_rename_deleted_folder_and_account_isolation(store, monkeypatch):
    monkeypatch.setattr("api.services.remote_favorites_store.DEFAULT_ACCOUNT_KEY", "default")
    client = FavoriteClient({10: [1], 11: []})
    await sync(store, client, "full")
    client.contents = {12: [1]}
    await sync(store, client, "full")
    item = store.archive_page()["items"][0]
    assert item["result"]["collection_names"] == ["folder-12"]
    assert item["state"] == "present"
    # 另一个账号的归档互不影响
    await sync(store, FavoriteClient({10: [1]}, mid=2), "full")
    assert store.archive_page(account="bilibili:2")["total"] == 1
    # 收藏夹在平台上没了，旧内容仍然留着（未取到不等于被删除）
    client.contents = {}
    await sync(store, client, "full")
    assert store.archive_page(account="bilibili:1")["total"] == 1


@pytest.mark.asyncio
async def test_interruption_resume_and_full_restart(store):
    client = FavoriteClient()
    client.fail_page = 4
    with pytest.raises(OSError):
        await sync(store, client)
    assert store.archive_page()["total"] == 60
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
    assert store.archive_page()["total"] == 126  # 旧条目保留，直到完整重扫


@pytest.mark.asyncio
async def test_incremental_stops_after_a_run_of_known_items(store):
    """一键同步只拉新增：页内出现一段连续的本机已有内容，就认定后面都是旧的。"""
    client = FavoriteClient({10: list(range(200))})
    await sync(store, client, "full")
    client.contents[10] = [900, 901, 902, *range(200)]  # 新增 3 条
    client.calls.clear()
    await sync(store, client)  # auto = 增量
    assert max(page for _, page in client.calls) == 1  # 第 1 页就停
    assert store.archive_page()["total"] == 203


@pytest.mark.asyncio
async def test_short_middle_page_continues_and_builds_a_folder_baseline(store):
    """B站中间页少于 20 条但 has_more 为真时，仍应保存并继续。"""
    client = FavoriteClient({10: list(range(60)), 11: [100]})
    client.short_pages.add((10, 2))
    await sync(store, client, "full")
    assert store.archive_page(account="bilibili:1")["total"] == 60
    with store._conn() as conn:
        assert conn.execute("SELECT count(*) FROM remote_baseline WHERE account=? AND folder=?", ("bilibili:1", "10")).fetchone()[0] == 59


@pytest.mark.asyncio
async def test_incremental_stops_after_five_known_items_and_continues_next_folder(store):
    client = FavoriteClient({10: list(range(80)), 11: list(range(100, 130))})
    await sync(store, client, "full")
    client.contents[10] = [900, *range(80)]
    client.contents[11] = [901, *range(100, 130)]
    client.calls.clear()
    await sync(store, client)
    assert max(page for fid, page in client.calls if fid == 10) == 1
    assert max(page for fid, page in client.calls if fid == 11) == 1
    assert store.archive_page(account="bilibili:1")["total"] == 112


@pytest.mark.asyncio
async def test_known_run_crosses_pages_and_new_item_resets_it(store):
    client = FavoriteClient({10: list(range(80))})
    await sync(store, client, "full")
    client.contents[10] = [*range(900, 916), *range(80)]
    client.calls.clear()
    await sync(store, client)
    assert max(page for _, page in client.calls) == 2
    # Four old rows followed by a new row must not stop; the next five old rows do.
    client.contents[10] = [*range(4), 999, *range(4, 80)]
    client.calls.clear()
    await sync(store, client)
    assert max(page for _, page in client.calls) == 1
    assert store.archive_page(account="bilibili:1")["total"] == 97


@pytest.mark.asyncio
async def test_incremental_keeps_paging_when_known_run_is_too_short(store):
    """连续命中不足五条不能停，后续页仍要读取。"""
    client = FavoriteClient({10: list(range(200))})
    await sync(store, client, "full")
    client.contents[10] = [*range(900, 917), *range(200)]  # 第 1 页末尾只有 3 条旧内容
    client.calls.clear()
    await sync(store, client)
    assert max(page for _, page in client.calls) == 2
    assert store.archive_page()["total"] == 217


@pytest.mark.asyncio
async def test_incremental_boundary_is_folder_scoped(store):
    client = FavoriteClient({10: list(range(100)), 11: []})
    await sync(store, client, "full")
    client.contents[11] = list(range(100))
    client.calls.clear()
    await sync(store, client)
    assert (11, 5) in client.calls


@pytest.mark.asyncio
async def test_same_time_and_recollection_do_not_stop_at_first_known_item(store):
    """同一时间批量收藏、重新收藏都会让顺序抖动：撞到一条旧的不能立刻停。"""
    client = FavoriteClient({10: list(range(160))})
    original = client.get_favorite_folder_contents

    async def same_time(*args):
        response = await original(*args)
        for row in response["medias"]:
            row["fav_time"] = 12345
        return response

    client.get_favorite_folder_contents = same_time
    await sync(store, client, "full")
    client.contents[10] = [159, 999, *range(159)]  # 旧内容被重新收藏顶到最前
    client.calls.clear()
    await sync(store, client)
    assert store.archive_page()["total"] == 161
    assert max(page for _, page in client.calls) == 1


@pytest.mark.asyncio
async def test_changed_head_during_scan_keeps_saved_pages(store):
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
    assert store.archive_page()["total"] == 50  # 报错也不丢已保存的页


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
async def test_duplicate_page_rejected(store):
    client = FavoriteClient({10: list(range(20)) * 2})
    assert await sync(store, client, "full") == ["收藏夹 1：分页返回异常（第 2 页，已保存 20 条）"]
    assert store.archive_page()["total"] == 20


@pytest.mark.asyncio
async def test_one_bad_folder_does_not_block_the_next_folder(store):
    """可预期的分页异常只影响当前收藏夹，后面的夹仍可建立基线。"""
    client = FavoriteClient({10: list(range(20)) * 2, 11: [200, 201]})
    errors = await sync(store, client, "full")
    assert errors == ["收藏夹 1：分页返回异常（第 2 页，已保存 20 条）"]
    assert store.archive_page(account="bilibili:1")["total"] == 22
    with store._conn() as conn:
        assert conn.execute(
            "SELECT count(*) FROM remote_baseline WHERE account=? AND folder=?",
            ("bilibili:1", "11"),
        ).fetchone()[0] == 2


@pytest.mark.asyncio
async def test_resumed_partial_job_rechecks_completed_folders(store):
    """局部失败留下的未完成扫描不能让下一次点击跳过其它收藏夹。"""
    client = FavoriteClient({10: list(range(20)) * 2, 11: [200, 201]})
    await sync(store, client, "full")
    client.contents[10] = list(range(40))
    client.calls.clear()
    await sync(store, client)
    assert (11, 1) in client.calls
    assert store.archive_page(account="bilibili:1")["total"] == 42


@pytest.mark.asyncio
async def test_archive_page_groups_by_platform(store):
    """归档取全量结果时按平台分组、组内最近入库在前，界面按平台页签切过去看到的顺序是稳定的。"""
    store.save_platform("xhs", [{"platform": "xhs", "content_id": f"x{i}"} for i in range(3)], status="succeeded")
    store.save_platform("bilibili", [{"platform": "bilibili", "content_id": f"b{i}"} for i in range(2)], status="succeeded")

    items = store.archive_page(limit=10)["items"]
    assert [item["result"]["platform"] for item in items] == ["bilibili", "bilibili", "xhs", "xhs", "xhs"]
    assert [item["result"]["content_id"] for item in items] == ["b1", "b0", "x2", "x1", "x0"]
    assert store.archive_page(platform="xhs", limit=10)["total"] == 3


@pytest.mark.asyncio
async def test_summary_counts_states(store):
    store.save_platform("bilibili", [{"content_id": "legacy"}], status="succeeded")
    client = FavoriteClient({10: list(range(25))})
    await sync(store, client, "full")

    summary = store.archive_summary()
    assert summary["states"]["present"] == 25
    assert summary["states"]["legacy"] == 1


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
