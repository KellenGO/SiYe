"""Sync integration boundaries, using isolated SQLite and synthetic workers only."""

import asyncio
import sys
from datetime import datetime, timezone

import pytest

from api.schemas.favorites import FavoritesJobRequest
from api.services.favorites_job_manager import FavoritesJobManager, _Job
from api.services.remote_favorites_store import RemoteFavoritesStore


@pytest.fixture
def store(tmp_path, monkeypatch):
    db = RemoteFavoritesStore(tmp_path / "library.db")
    monkeypatch.setattr("api.services.favorites_job_manager.get_remote_favorites_store", lambda: db)
    return db


def row(platform, content_id):
    return {"platform": platform, "content_id": content_id, "title": "示例", "url": "https://example.com"}


@pytest.mark.asyncio
async def test_subset_and_failed_sync_keep_saved_results_before_restart(store):
    store.save_platform("bilibili", [row("bilibili", "old-b")], status="succeeded")
    store.save_platform("xhs", [row("xhs", "old-x")], status="succeeded")
    manager = FavoritesJobManager()
    job = _Job(FavoritesJobRequest(platforms=["xhs"]))
    job.platforms["xhs"].status = "failed"
    job.completed_at = datetime.now(timezone.utc)
    manager._recent = job
    response = await manager.get(job.job_id)
    assert {r.content_id for r in response.results} == {"old-b", "old-x"}
    assert response.platforms["xhs"].status == "failed"
    assert response.platforms["bilibili"].synced_at is not None


@pytest.mark.asyncio
async def test_noisy_worker_does_not_deadlock_and_disk_failure_is_visible(store, monkeypatch):
    code = '''
import json, sys
r=json.loads(sys.stdin.readline())
sys.stderr.write('x'*1048576)
sys.stderr.flush()
for event, data in [('status', {'status':'empty'}), ('done', None)]:
 print('MC_AGG_EVENT\\t'+json.dumps({'event':event,'job_id':r['job_id'],'platform':r['platform'],'data':data}), flush=True)
'''
    monkeypatch.setattr("api.services.worker_process.worker_command", lambda *a: [sys.executable, "-c", code])
    def fail(*args, **kwargs):
        raise OSError("synthetic disk error")
    monkeypatch.setattr(store, "save_platform", fail)
    manager = FavoritesJobManager()
    job = _Job(FavoritesJobRequest(platforms=["xhs"]))
    await asyncio.wait_for(manager._run(job), timeout=15)
    assert job.platforms["xhs"].status == "empty"
    assert job.response().persistence_error
    from api.services.accounts import usage_evidence
    assert usage_evidence("xhs")["favorites"]["status"] == "failed"


@pytest.mark.asyncio
async def test_cancel_persists_terminal_status(store, monkeypatch):
    code = "import sys,time;sys.stdin.readline();time.sleep(30)"
    monkeypatch.setattr("api.services.worker_process.worker_command", lambda *a: [sys.executable, "-c", code])
    manager = FavoritesJobManager()
    await manager.create(FavoritesJobRequest(platforms=["xhs"]))
    for _ in range(100):
        if manager._active.procs:
            break
        await asyncio.sleep(.01)
    await manager.cleanup()
    assert store.load()["platforms"]["xhs"]["status"] == "cancelled"
    assert (await manager.latest()).overall != "running"


@pytest.mark.asyncio
async def test_user_cancel_stops_workers_preserves_partial_and_is_idempotent(store, monkeypatch):
    store.save_platform("bilibili", [row("bilibili", "old")], status="succeeded")
    code = '''
import json,sys,time
r=json.loads(sys.stdin.readline())
print('MC_AGG_EVENT\\t'+json.dumps({'event':'result','job_id':r['job_id'],'platform':r['platform'],
'data':{'platform':r['platform'],'content_id':'partial','title':'partial','url':'https://example.com'}}),flush=True)
time.sleep(30)
'''
    monkeypatch.setattr("api.services.worker_process.worker_command", lambda *a: [sys.executable, "-c", code])
    manager = FavoritesJobManager()
    created = await manager.create(FavoritesJobRequest(platforms=["xhs", "douyin"]))
    try:
        for _ in range(200):
            if all(manager._active.items.values()):
                break
            await asyncio.sleep(.01)
        assert all(manager._active.items.values())
        procs = list(manager._active.procs)
        result, repeated = await asyncio.wait_for(asyncio.gather(
            manager.cancel(created.job_id), manager.cancel(created.job_id)), timeout=10)
        assert result.overall != "running" and repeated.overall != "running"
        assert all(p.returncode is not None for p in procs)
        assert result.platforms["xhs"].status == "cancelled"
        assert result.platforms["douyin"].status == "cancelled"
        restored = await FavoritesJobManager().latest()
        assert {(r.platform, r.content_id) for r in restored.results} == {
            ("bilibili", "old"), ("xhs", "partial"), ("douyin", "partial")}
        assert await manager.cancel("unknown") is None
    finally:
        await manager.cleanup()


@pytest.mark.asyncio
async def test_cancel_endpoint_handles_pending_and_unknown_jobs(store, monkeypatch):
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient
    from api.routers.search import search_router

    manager = FavoritesJobManager()
    job = _Job(FavoritesJobRequest(platforms=["xhs"]))
    manager._active = manager._recent = job
    monkeypatch.setattr("api.routers.search.favorites_job_manager", manager)
    app = FastAPI()
    app.include_router(search_router)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(f"/api/search/favorites/jobs/{job.job_id}/cancel")
        assert response.status_code == 200
        assert response.json()["platforms"]["xhs"]["status"] == "cancelled"
        assert response.json()["overall"] != "running"
        assert (await client.post("/api/search/favorites/jobs/unknown/cancel")).status_code == 404


@pytest.mark.asyncio
async def test_cancel_records_status_under_real_account(store):
    """分页同步取消后，状态必须落在真实账号名下。

    以前 cancel() 没传 account_key，状态写到 default 账号；重启后
    archive_summary 取每平台 max(id) 会把该平台显示成「已取消 / 0 条」，
    把上一次成功的记录盖掉。
    """
    import sqlite3

    store.save_platform("xhs", [row("xhs", "kept")], status="succeeded", account_key="xhs:u1")
    manager = FavoritesJobManager()
    job = _Job(FavoritesJobRequest(platforms=["xhs"], sync_mode="auto"))
    job.platforms["xhs"].status = "running"
    job.platforms["xhs"].account = "xhs:u1"
    manager._active = manager._recent = job

    response = await manager.cancel(job.job_id)
    assert response.platforms["xhs"].status == "cancelled"

    with sqlite3.connect(store.db_path) as conn:
        latest = conn.execute(
            "SELECT account_key, status, result_count FROM remote_sync_runs "
            "WHERE platform='xhs' ORDER BY id DESC LIMIT 1").fetchone()
    assert latest == ("xhs:u1", "cancelled", 0)
    # 汇总取的是这条 run：它属于真实账号，不会把 default 的假状态摆到平台上。
    assert store.archive_summary()["platforms"]["xhs"]["status"] == "cancelled"


@pytest.mark.asyncio
async def test_cancel_before_account_is_known_writes_nothing(store):
    """还没拿到账号就取消：宁可不写，也不能写一条 default 账号的假状态。"""
    import sqlite3

    manager = FavoritesJobManager()
    job = _Job(FavoritesJobRequest(platforms=["xhs"], sync_mode="auto"))
    job.platforms["xhs"].status = "running"
    manager._active = manager._recent = job

    response = await manager.cancel(job.job_id)
    assert response.platforms["xhs"].status == "cancelled"

    with sqlite3.connect(store.db_path) as conn:
        assert conn.execute("SELECT count(*) FROM remote_sync_runs").fetchone()[0] == 0


@pytest.mark.asyncio
async def test_paged_sync_has_overall_deadline_despite_progress(store, monkeypatch):
    """worker 一直吐进度也不能无限跑：分页同步有总时长上限。

    以前只对每行读加 90 秒，worker 每 90 秒发一行 status 就能一直 running，
    收藏同步攥着 exclusive 租约不放，别的账号操作永远 409。
    """
    code = '''
import json,sys,time
r=json.loads(sys.stdin.readline())
while True:
    print('MC_AGG_EVENT\\t'+json.dumps({'event':'status','job_id':r['job_id'],'platform':r['platform'],
        'data':{'status':'running','account':'xhs:1','result_count':0,'phase':'分页'}}),flush=True)
    time.sleep(0.05)
'''
    monkeypatch.setattr("api.services.worker_process.worker_command", lambda *a: [sys.executable, "-c", code])
    monkeypatch.setattr("api.services.favorites_job_manager._PAGED_TIMEOUT", 0.5)
    manager = FavoritesJobManager()
    created = await manager.create(FavoritesJobRequest(platforms=["xhs"], sync_mode="auto"))
    try:
        await asyncio.wait_for(manager.active_task(), 15)
        response = await manager.get(created.job_id)
        assert response.platforms["xhs"].status == "timed_out"
        assert manager._recent.completed_at is not None
    finally:
        await manager.cleanup()


@pytest.mark.asyncio
async def test_paged_failure_before_account_is_known_updates_the_real_account(store, monkeypatch):
    """worker 还没报账号就挂了，失败也要写回本机已有的真实账号。

    否则这次失败完全不落盘，重启后页面还显示上一次的「成功」。
    """
    scan = store.begin_scan("xhs", "xhs:1", [], "auto")
    store.finish_scan("xhs:1", scan, False)
    assert store.archive_summary()["platforms"]["xhs"]["status"] == "succeeded"

    code = "import sys;sys.stdin.readline();sys.exit(1)"
    monkeypatch.setattr("api.services.worker_process.worker_command", lambda *a: [sys.executable, "-c", code])
    manager = FavoritesJobManager()
    created = await manager.create(FavoritesJobRequest(platforms=["xhs"], sync_mode="auto"))
    try:
        await asyncio.wait_for(manager.active_task(), 20)
        summary = store.archive_summary()
        assert summary["platforms"]["xhs"]["status"] == "failed"
        assert summary["platforms"]["xhs"]["account"] == "xhs:1"
        assert (await manager.get(created.job_id)).platforms["xhs"].status == "failed"
    finally:
        await manager.cleanup()
