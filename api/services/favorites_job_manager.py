from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from typing import Dict, List, Optional

from aggregate_search.models import UnifiedSearchResult
from aggregate_search.protocol import WorkerRequest, parse_event_line
from base.runtime_paths import application_root
from ..schemas.favorites import FavoritePlatformInfo, FavoritesJobRequest, FavoritesJobResponse
from .accounts import mark_login_required_from_search, evidence_token, record_usage
from .favorite_snapshot import merge_snapshot
from .remote_favorites_store import get_remote_favorites_store
from .worker_process import drain_stderr_to_eof, spawn_worker, terminate_worker

_ROOT = application_root()
# 每个平台最多取 100 条时，分页请求会明显变多，超时相应放宽。
_TIMEOUT = 300


def _merge_result(previous: UnifiedSearchResult, current: UnifiedSearchResult) -> UnifiedSearchResult:
    """本机已保存的完整指标不能被本次残缺结果覆盖（逐字段合并）。

    本次同步超时、被限流、或只走到列表阶段时，内存里的结果字段会比库里少，
    直接替换会让以前完整的指标倒退成残缺版本 —— 库里的数据仍在，只是这次
    没取到，所以缺的字段沿用旧值。
    """
    return current.model_copy(update=merge_snapshot(
        {"metrics": previous.metrics, "metrics_status": previous.metrics_status,
         "metrics_updated_at": previous.metrics_updated_at,
         "metrics_approximate": previous.metrics_approximate},
        {"metrics": current.metrics, "metrics_status": current.metrics_status,
         "metrics_updated_at": current.metrics_updated_at,
         "metrics_approximate": current.metrics_approximate},
    ))


def _deduplicate_results(results: List[UnifiedSearchResult]) -> List[UnifiedSearchResult]:
    """合并跨账号归档中的同一远端内容，避免把重复 identity 交给列表组件。

    ``default`` 是旧版没有确认账号时留下的历史归档；它和已识别账号的归档可以
    合法地同时存在于 SQLite。读取全量快照时只展示一张卡片，后出现的账号归档
    作为当前快照，并从旧记录补齐未返回的指标。
    """
    rows: Dict[tuple[str, str], UnifiedSearchResult] = {}
    for result in results:
        key = (result.platform, result.content_id)
        rows[key] = _merge_result(rows[key], result) if key in rows else result
    return list(rows.values())


class _Job:
    def __init__(self, request: FavoritesJobRequest):
        self.job_id = uuid.uuid4().hex[:12]
        self.created_at = datetime.now(timezone.utc)
        self.completed_at: Optional[datetime] = None
        self.limit = request.limit_per_platform
        self.sync_mode = request.sync_mode
        self.order = list(request.platforms)
        self.evidence_tokens = {p: evidence_token(p) for p in self.order}
        self.platforms = {p: FavoritePlatformInfo() for p in self.order}
        self.items: Dict[str, List[UnifiedSearchResult]] = {p: [] for p in self.order}
        self.task: Optional[asyncio.Task] = None
        self.procs: List[asyncio.subprocess.Process] = []
        self.persistence_error: Optional[str] = None

    def terminal(self) -> bool:
        return self.completed_at is not None

    def upsert(self, platform: str, data: dict) -> None:
        result = UnifiedSearchResult(**data)
        for index, existing in enumerate(self.items[platform]):
            if (existing.content_id, existing.content_type) == (result.content_id, result.content_type):
                self.items[platform][index] = result
                return
        if len(self.items[platform]) < self.limit:
            self.items[platform].append(result)

    def response(self) -> FavoritesJobResponse:
        merged: List[UnifiedSearchResult] = []
        maximum = max((len(v) for v in self.items.values()), default=0)
        for index in range(maximum):
            for platform in self.order:
                if index < len(self.items[platform]):
                    merged.append(self.items[platform][index])
        statuses = [info.status for info in self.platforms.values()]
        success = sum(s in ("succeeded", "empty") for s in statuses)
        overall = "running" if not self.terminal() else (
            "completed" if success == len(statuses) else "partial" if success or merged else "failed")
        return FavoritesJobResponse(
            job_id=self.job_id, overall=overall, created_at=self.created_at,
            completed_at=self.completed_at, platforms=self.platforms, results=merged,
            persistence_error=self.persistence_error)


class FavoritesJobManager:
    def __init__(self) -> None:
        self._active: Optional[_Job] = None
        self._recent: Optional[_Job] = None

    def is_active(self) -> bool:
        return bool(self._active and not self._active.terminal())

    def active_task(self) -> Optional[asyncio.Task]:
        return self._active.task if self._active else None

    async def create(self, request: FavoritesJobRequest, summary: bool = False) -> FavoritesJobResponse:
        if self.is_active():
            raise RuntimeError("favorites_in_progress")
        job = _Job(request)
        self._active = self._recent = job
        job.task = asyncio.create_task(self._run(job), name=f"favorites-{job.job_id}")
        return await self._snapshot(job, summary)

    async def get(self, job_id: str, summary: bool = False) -> Optional[FavoritesJobResponse]:
        job = self._active if self._active and self._active.job_id == job_id else self._recent
        return await self._snapshot(job, summary) if job and job.job_id == job_id else None

    async def _snapshot(self, job: Optional[_Job] = None, summary: bool = False) -> Optional[FavoritesJobResponse]:
        if summary:
            try:
                saved_summary = await asyncio.to_thread(get_remote_favorites_store().archive_summary)
            except Exception:
                raise RuntimeError("本机同步收藏读取失败，请检查磁盘与数据库") from None
            response = job.response() if job else FavoritesJobResponse(
                job_id="saved", overall="completed", created_at=datetime.now(timezone.utc),
                platforms={}, results=[])
            response.results = []
            persisted = {platform: FavoritePlatformInfo(**info)
                         for platform, info in saved_summary.pop("platforms").items()}
            response.platforms = {**persisted, **response.platforms}
            if job is None and persisted:
                statuses = [info.status for info in persisted.values()]
                success = sum(status in ("succeeded", "empty") for status in statuses)
                response.overall = "completed" if success == len(statuses) else (
                    "partial" if success or any(saved_summary["counts"].values()) else "failed")
            for name, value in saved_summary.items():
                setattr(response, name, value)
            return response
        # 始终合并持久化内容；局部同步/失败不能让其他平台或旧条目消失。
        try:
            saved = await asyncio.to_thread(lambda: get_remote_favorites_store().load())
            # Legacy full-snapshot clients can still read the new account archives.
            def include_accounts(snapshot):
                store = get_remote_favorites_store()
                metadata = store.archive_summary()
                if not metadata["accounts"]:
                    return snapshot
                if snapshot is None:
                    snapshot = {"job_id": "saved", "overall": "completed",
                                "created_at": datetime.now(timezone.utc), "platforms": {}, "results": []}
                snapshot["platforms"].update(metadata["platforms"])
                for account in metadata["accounts"]:
                    offset = 0
                    while True:
                        page = store.archive_page(account=account["account"], offset=offset, limit=100)
                        snapshot["results"].extend(item["result"] for item in page["items"])
                        offset += 100
                        if offset >= page["total"]:
                            break
                return snapshot
            saved = await asyncio.to_thread(include_accounts, saved)
        except Exception:
            if job is None:
                raise RuntimeError("本机同步收藏读取失败，请检查磁盘与数据库") from None
            saved = None
            job.persistence_error = "本机收藏读取失败，当前仅显示本次同步结果"
        if job is None:
            if not saved:
                return None
            snapshot = FavoritesJobResponse(**saved)
            snapshot.results = _deduplicate_results(snapshot.results)
            return snapshot
        response = job.response()
        if saved:
            previous = FavoritesJobResponse(**saved)
            response.results = _deduplicate_results(previous.results + response.results)
            response.platforms = {**previous.platforms, **response.platforms}
        return response

    async def latest(self, summary: bool = False) -> Optional[FavoritesJobResponse]:
        """Return the most recent favourites snapshot for the page to restore.

        Prefers the in-memory job (freshest), and falls back to the copy persisted
        in the local SQLite library so a backend restart does not force the user to
        re-sync every platform. Both paths are local reads: they never contact a
        platform. ``completed_at`` is the original sync time, which the page shows
        as the snapshot's age.
        """
        return await self._snapshot(self._recent, summary)

    async def _run(self, job: _Job) -> None:
        tasks = [asyncio.create_task(self._run_platform(job, platform)) for platform in job.order]
        try:
            await asyncio.gather(*tasks)
        finally:
            # Cancellation is complete only after every platform has saved its partial results.
            await asyncio.gather(*tasks, return_exceptions=True)
            job.completed_at = datetime.now(timezone.utc)

    async def cancel(self, job_id: str, summary: bool = False) -> Optional[FavoritesJobResponse]:
        job = self._active if self._active and self._active.job_id == job_id else self._recent
        if not job or job.job_id != job_id:
            return None
        if job.task and not job.task.done():
            if not job.task.cancelling():
                job.task.cancel()
            await asyncio.shield(asyncio.gather(job.task, return_exceptions=True))
        # A task cancelled before its first turn has no platform cleanup to run.
        for platform, info in job.platforms.items():
            if info.status in ("pending", "running"):
                info.status, info.error_summary = "cancelled", "同步已取消"
                info.synced_at = datetime.now(timezone.utc)
                try:
                    await asyncio.to_thread(lambda p=platform: get_remote_favorites_store().save_platform(
                        p, [item.model_dump(mode="json") for item in job.items[p]],
                        status="cancelled", error_summary="同步已取消", requested_limit=job.limit))
                except Exception:
                    job.persistence_error = "同步结果未能保存到本机，请检查磁盘空间后重试"
        job.completed_at = job.completed_at or datetime.now(timezone.utc)
        return await self._snapshot(job, summary)

    async def _run_platform(self, job: _Job, platform: str) -> None:
        info = job.platforms[platform]
        info.status = "running"
        paged = platform == "bilibili" and bool(job.sync_mode)
        proc = None
        try:
            proc = await spawn_worker()
            job.procs.append(proc)
            payload = WorkerRequest(job_id=job.job_id, mode="favorites", platform=platform,
                                    limit=job.limit, sync_mode=job.sync_mode).model_dump_json().encode("utf-8") + b"\n"
            assert proc.stdin and proc.stdout
            proc.stdin.write(payload)
            await proc.stdin.drain()
            proc.stdin.close()

            async def read() -> bool:
                done = False
                while True:
                    raw = await asyncio.wait_for(proc.stdout.readline(), timeout=90 if paged else _TIMEOUT)
                    if not raw:
                        break
                    event = parse_event_line(raw.decode("utf-8", errors="replace").strip())
                    if not event or event.job_id != job.job_id or event.platform != platform:
                        continue
                    if event.event == "result" and isinstance(event.data, dict):
                        try:
                            job.upsert(platform, event.data)
                            info.result_count = len(job.items[platform])
                        except Exception:
                            pass
                    elif event.event == "status":
                        status = (event.data or {}).get("status")
                        if paged and isinstance(event.data, dict):
                            account = event.data.get("account")
                            if isinstance(account, str) and account.startswith("bilibili:"):
                                info.account = account
                            phase = event.data.get("phase")
                            if isinstance(phase, str):
                                info.phase = phase[:160]
                            count = event.data.get("result_count")
                            if isinstance(count, int) and count >= 0:
                                info.result_count = count
                        if status in ("running", "succeeded", "empty"):
                            info.status = status
                    elif event.event == "error":
                        error = event.data or {}
                        info.status = error.get("type", "failed")
                        info.error_summary = str(error.get("message") or "收藏夹同步失败")[:160]
                        if info.status == "login_required" and record_usage(platform, "favorites", info.status, job.evidence_tokens[platform]):
                            mark_login_required_from_search(platform)
                    elif event.event == "done":
                        done = True
                        break
                return done

            # 持续排空 stderr，避免大量日志填满管道后 worker 卡死。
            stderr_task = asyncio.create_task(drain_stderr_to_eof(proc))
            try:
                done = await read() if paged else await asyncio.wait_for(read(), timeout=_TIMEOUT)
            finally:
                stderr_task.cancel()
                await asyncio.gather(stderr_task, return_exceptions=True)
            await terminate_worker(proc, grace=5)
            if not done and info.status == "running":
                info.status, info.error_summary = "failed", "平台 worker 未正常结束"
            elif info.status == "running":
                info.status = "succeeded" if job.items[platform] else "empty"
        except asyncio.TimeoutError:
            info.status, info.error_summary = "timed_out", "收藏夹同步超时"
        except Exception as exc:
            info.status, info.error_summary = "failed", type(exc).__name__
        finally:
            await terminate_worker(proc)
            if proc in job.procs:
                job.procs.remove(proc)
            # 逐平台落库：本平台失败也保留其他平台已保存的数据，
            # 且不删除"这次没取到"的旧条目（见 remote_favorites_store 的说明）。
            # 任务被取消（后端关闭）时不能落库成 running——否则前端会一直转圈。
            current_task = asyncio.current_task()
            was_cancelled = current_task is not None and current_task.cancelling() > 0
            effective_status = info.status
            effective_error = info.error_summary
            if was_cancelled and effective_status in ("running", "pending"):
                effective_status = "cancelled"
                effective_error = "同步已取消"
            info.status = effective_status
            info.error_summary = effective_error
            info.synced_at = datetime.now(timezone.utc)
            try:
                if paged:
                    if info.account:
                        await asyncio.to_thread(get_remote_favorites_store().sync_status, info.account, effective_status, effective_error)
                else:
                    await asyncio.to_thread(lambda: get_remote_favorites_store().save_platform(
                        platform,
                        [item.model_dump(mode="json") for item in job.items[platform]],
                        status=effective_status,
                        error_summary=effective_error,
                        requested_limit=job.limit,
                    ))
                record_usage(platform, "favorites", effective_status, job.evidence_tokens[platform])
            except Exception:
                record_usage(platform, "favorites", "failed", job.evidence_tokens[platform])
                job.persistence_error = "同步结果未能保存到本机，关闭程序后可能丢失，请检查磁盘空间后重试"

    async def cleanup(self) -> None:
        job = self._active
        if job and job.task and not job.task.done():
            job.task.cancel()
            for proc in list(job.procs):
                try:
                    proc.kill()
                except Exception:
                    pass
            await asyncio.gather(job.task, return_exceptions=True)


favorites_job_manager = FavoritesJobManager()
