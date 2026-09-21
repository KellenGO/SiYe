"""Paged remote-favorite import. Platform I/O is separate from persistence policy."""
from __future__ import annotations

import asyncio
import hashlib
import json
import time
from dataclasses import dataclass
from typing import Protocol

from aggregate_search.adapters.bilibili import BilibiliAdapter


@dataclass
class FavoritePage:
    results: list[dict]
    fingerprint: str
    complete: bool
    count: int
    identities: list[tuple[str, object]]


class FavoritesSource(Protocol):
    incremental_verified: bool
    async def identity(self) -> str: ...
    async def folders(self, account: str) -> list[dict]: ...
    async def page(self, folder: dict, number: int) -> FavoritePage: ...


class FavoritesSyncError(ValueError):
    """Safe fixed-text sync failure shown without platform response contents."""

    def __init__(self, message):
        super().__init__(message)
        self.safe_message = "收藏列表发生变化或返回异常，已保留进度；请稍后重新同步"


class BilibiliFavoritesSource:
    # No verified live-account evidence of stable incremental boundaries yet.
    incremental_verified = False

    def __init__(self, client, interval=2.0):
        self.client = client
        self.client.favorites_sync = True
        self.interval = interval
        self.last_request = 0.0
        self.adapter = BilibiliAdapter()

    async def request(self, method, *args, **kwargs):
        await asyncio.sleep(max(0, self.last_request + self.interval - time.monotonic()))
        self.last_request = time.monotonic()
        return await asyncio.wait_for(method(*args, **kwargs), timeout=30)

    async def identity(self):
        nav = await self.request(self.client.get, "/x/web-interface/nav", enable_params_sign=False)
        if not isinstance(nav, dict) or not nav.get("mid"):
            from base.exceptions import LoginRequiredError
            raise LoginRequiredError(platform="bilibili", message="B站登录状态已失效")
        return f"bilibili:{int(nav['mid'])}"

    async def folders(self, account):
        data = await self.request(self.client.get_created_favorite_folders, int(account.split(":")[1]))
        if not isinstance(data, dict) or not isinstance(data.get("list"), list):
            # The service may return null for an explicitly empty account.
            if isinstance(data, dict) and data.get("count") == 0 and data.get("list") is None:
                return []
            raise FavoritesSyncError("Invalid favorite folders")
        folders = []
        for row in data["list"]:
            if not isinstance(row, dict) or not row.get("id") or not isinstance(row.get("media_count"), int) or row["media_count"] < 0:
                raise FavoritesSyncError("Invalid favorite folder")
            folders.append({"id": str(row["id"]), "name": str(row.get("title") or "默认收藏夹"), "count": row["media_count"], "mtime": row.get("mtime")})
        if len({r["id"] for r in folders}) != len(folders) or data.get("count") != len(folders):
            raise FavoritesSyncError("Incomplete favorite folders")
        return sorted(folders, key=lambda r: r["id"])

    async def page(self, folder, number):
        data = await self.request(self.client.get_favorite_folder_contents, int(folder["id"]), number, 20)
        if not isinstance(data, dict) or data.get("has_more") not in (True, False, 0, 1):
            raise FavoritesSyncError("Invalid favorite pagination")
        rows = data.get("medias")
        if rows is None and folder["count"] == 0 and not data["has_more"]:
            rows = []
        if not isinstance(rows, list) or len(rows) > 20 or (not rows and data["has_more"]):
            raise FavoritesSyncError("Invalid favorite page")
        expected = max(0, min(20, folder["count"] - (number - 1) * 20))
        if len(rows) != expected or bool(data["has_more"]) != (number * 20 < folder["count"]):
            raise FavoritesSyncError("Favorite list changed during sync")
        identities, results = [], []
        for row in rows:
            if not isinstance(row, dict) or not (row.get("bvid") or row.get("id")):
                raise FavoritesSyncError("Invalid favorite item")
            native = {**row, "aid": row.get("aid") or row.get("id"), "_collection_name": folder["name"]}
            converted = self.adapter.adapt([native])
            if len(converted) != 1:
                raise FavoritesSyncError("Unsupported favorite item")
            result = converted[0].model_dump(mode="json")
            result["metrics_status"] = "partial"
            # Explicit zeroes in list counters are observations, not missing values.
            for source, target in (("play", "view_count"), ("collect", "collect_count"), ("reply", "comment_count"), ("danmaku", "danmaku_count")):
                value = (row.get("cnt_info") or {}).get(source)
                if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                    result["metrics"][target] = value
            results.append(result)
            identities.append((result["content_id"], row.get("fav_time")))
        if len({r[0] for r in identities}) != len(identities):
            raise FavoritesSyncError("Duplicate favorite items")
        fingerprint = hashlib.sha256(json.dumps(identities).encode()).hexdigest()
        return FavoritePage(results, fingerprint, not bool(data["has_more"]), len(rows), identities)


async def synchronize_favorites(source: FavoritesSource, store, mode, progress):
    account = await source.identity()
    incremental = mode == "auto" and source.incremental_verified
    progress(account, 0, "检查新增收藏" if incremental else "读取收藏夹；将读取完整列表")
    folders = await source.folders(account)
    # An unfinished auto scan can resume importing, but is never authoritative
    # evidence of absence. Full reconciliation always creates a fresh scan.
    scan = store.begin_scan(account, folders, mode)
    saved = 0
    first_pages = {}
    try:
        for folder in folders:
            fid = folder["id"]
            first = await source.page(folder, 1)
            first_pages[fid] = first.fingerprint
            checkpoint = store.checkpoint(account, fid)
            number = 1
            page = first
            previous_overlap = None
            if checkpoint and checkpoint["scan"] == scan:
                # Verify both head and last committed page before resuming.
                head = store.head_fingerprint(account, fid, scan)
                boundary = first if checkpoint["page"] == 1 else await source.page(folder, checkpoint["page"])
                if head == first.fingerprint and boundary.fingerprint == checkpoint["fingerprint"]:
                    if checkpoint["complete"]:
                        progress(account, saved, f"已核验收藏夹：{folder['name']}")
                        continue
                    number = checkpoint["page"] + 1
                    page = await source.page(folder, number)
                else:
                    store.restart_folder(account, fid)
            while True:
                overlap = store.baseline_overlap(account, fid, page.identities) if incremental else None
                stop = bool(overlap and previous_overlap and overlap[0] == previous_overlap[1] + 1)
                store.save_sync_page(account, scan, fid, folder["name"], number, page.fingerprint, page.results, page.complete or stop, page.identities)
                saved += page.count
                progress(account, saved, f"正在保存 {folder['name']} · 第 {number} 页" + ("（检查新增）" if incremental else "（完整列表读取）"))
                if page.complete or stop:
                    break
                previous_overlap = overlap
                number += 1
                page = await source.page(folder, number)
        if await source.folders(account) != folders:
            raise FavoritesSyncError("Favorite folders changed during sync")
        for folder in folders:
            if (await source.page(folder, 1)).fingerprint != first_pages[folder["id"]]:
                raise FavoritesSyncError("Favorite head changed during sync")
            progress(account, saved, f"正在核验 {folder['name']}")
        store.finish_scan(account, scan, reconcile=mode == "full")
        progress(account, saved, "完整核对完成" if mode == "full" else "导入完成；旧项缺失请执行完整核对")
    except BaseException:
        store.sync_status(account, "cancelled", "同步未完成，可手动继续；未据此判断缺失")
        raise
