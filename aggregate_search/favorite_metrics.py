"""Bounded, throttled enrichment of public favourite counters.

Only counters are cached: never cookies, signed note URLs or collection names.
The list is emitted by the crawler before this optional detail phase starts.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import time
from pathlib import Path

from base.runtime_paths import library_data_root
from tools import utils

CACHE_TTL = 6 * 60 * 60
REQUEST_TIMEOUT = 12
# 每个并发槽位两条请求之间的间隔。整体速率 = ENRICHMENT_CONCURRENCY / REQUEST_INTERVAL。
REQUEST_INTERVAL = 1.5
# 详情阶段总预算。B站单个收藏夹最多 100 条，串行 + 2 秒间隔在 120 秒内只能补
# 到约 50 条（数据库里也就表现为「一半完整、一半 failed」），所以改成小并发：
# 100 条约 100 秒，留得出余量。
ENRICHMENT_TIMEOUT = 150
ENRICHMENT_CONCURRENCY = 2
TARGETS = {
    "bilibili": {"view_count", "like_count", "comment_count", "collect_count", "coin_count"},
    "xhs": {"like_count", "comment_count", "collect_count"},
    "zhihu": {"view_count", "like_count", "comment_count", "collect_count"},
}


def _counts(raw, mapping, approximate=None):
    """Keep genuine zeroes; missing/invalid values must remain unknown."""
    result = {}
    if not isinstance(raw, dict):
        return result
    for source, target in mapping:
        value = raw.get(source)
        if isinstance(value, bool):
            continue
        if isinstance(value, int) and value >= 0:
            result.setdefault(target, value)
        elif isinstance(value, str) and re.fullmatch(r"\d+", value.strip()):
            result.setdefault(target, int(value))
        elif isinstance(value, str):
            match = re.fullmatch(r"(\d+(?:\.\d+)?)(万|亿)?(\+)?", value.strip())
            if match and (match[2] or match[3]) and target not in result:
                result[target] = int(float(match[1]) * {None: 1, "万": 10000, "亿": 100000000}[match[2]])
                if approximate is not None:
                    approximate.add(target)
    return result


async def fetch_metrics(platform, client, row, approximate=None):
    if platform == "bilibili":
        bvid = row.get("bvid")
        aid = row.get("id") or row.get("aid")
        if not bvid and not aid:
            return None
        detail = await client.get_video_info(bvid=bvid) if bvid else await client.get_video_info(aid=aid)
        if not isinstance(detail, dict):
            return {}
        view = detail.get("View", detail)
        return _counts(view.get("stat") if isinstance(view, dict) else None, [
            ("view", "view_count"), ("like", "like_count"), ("reply", "comment_count"),
            ("favorite", "collect_count"), ("coin", "coin_count"), ("share", "share_count"),
        ], approximate)
    if platform == "xhs":
        note = row.get("note_card") or row
        note_id = note.get("note_id") or note.get("id") or row.get("id")
        token = row.get("xsec_token") or note.get("xsec_token")
        if not note_id or not token:
            return None
        detail = await client.get_note_by_id(
            str(note_id), row.get("xsec_source") or note.get("xsec_source") or "pc_user", str(token))
        return _counts(detail.get("interact_info") if isinstance(detail, dict) else None, [
            ("liked_count", "like_count"), ("comment_count", "comment_count"),
            ("collected_count", "collect_count"), ("share_count", "share_count"),
        ], approximate)
    kind = row.get("type")
    content_id = str(row.get("id") or "")
    if kind not in ("answer", "article", "zvideo") or not content_id.isdigit():
        return None
    # Read the raw content object, before ZhihuContent drops extra fields.
    detail = await client.get(f"/api/v4/{kind}s/{content_id}", {
        "include": "voteup_count,comment_count,favlists_count,favorites_count,"
                   "read_count,visit_count,visits_count,play_count",
    })
    mapping = [("voteup_count", "like_count"), ("comment_count", "comment_count"),
               ("favlists_count", "collect_count"), ("favorites_count", "collect_count")]
    # Never use question.visit_count as the reading count of an answer.
    mapping += ([("play_count", "view_count")] if kind == "zvideo" else
                [("read_count", "view_count"), ("visit_count", "view_count"), ("visits_count", "view_count")])
    return _counts(detail, mapping, approximate)


def list_approximations(platform, row):
    approximate = set()
    if platform == "xhs":
        note = row.get("note_card") or row
        _counts(note.get("interact_info"), [
            ("liked_count", "like_count"), ("comment_count", "comment_count"),
            ("collected_count", "collect_count"), ("share_count", "share_count"),
        ], approximate)
    return sorted(approximate)


def _key(platform, row):
    note = row.get("note_card") or row
    content_id = row.get("bvid") or note.get("note_id") or note.get("id") or row.get("id")
    return f"{platform}:{row.get('type', '')}:{content_id}" if content_id else None


def _read_cache(path, now):
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if (isinstance(data, dict) and data.get("version") == 1 and isinstance(data.get("at"), (int, float))
                and 0 <= now - data["at"] < CACHE_TTL
                and isinstance(data.get("metrics"), dict)
                and isinstance(data.get("approximate", []), list)
                and all(k in data["metrics"] for k in data.get("approximate", []))
                and all(k in {"view_count", "like_count", "comment_count", "collect_count", "coin_count", "share_count"}
                        and type(v) is int and v >= 0 for k, v in data["metrics"].items())):
            return data
    except (OSError, ValueError, TypeError):
        pass
    return None


def _save_cache(path, metrics, at, approximate):
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(f".{os.getpid()}.tmp")
        temporary.write_text(json.dumps({"version": 1, "at": at, "metrics": metrics,
                                         "approximate": sorted(approximate)}), encoding="utf-8")
        temporary.replace(path)
    except OSError:
        pass  # An unwritable cache must not fail a successful remote read.


def _emit(sink, platform, row, metrics, at, approximate, cached):
    sink([{**row, "_favorite_metrics": metrics, "_metrics_updated_at": at,
           "_metrics_approximate": sorted(approximate), "_metrics_cached": cached,
           "_metrics_status": "complete" if TARGETS[platform] <= metrics.keys() else "partial"}])


async def enrich_favorites(platform, client, rows, sink, *, cache_dir: Path | None = None,
                           concurrency: int | None = None):
    """Stream one update per row: cached counters first, then fresh details.

    缓存命中不占预算也不占间隔，所以第二次同步基本是零请求的。缓存没命中的行
    交给一个小并发、带节流的工作池 —— 一个 100 条的 B站收藏夹必须在
    ENRICHMENT_TIMEOUT 内跑完，串行 + 固定间隔在算术上做不到。

    预算用尽**不是错误**：已经流出去的行保留列表里就有的计数，剩下的等下次同步
    （那时它们多半是缓存命中）。只有平台自己的错误（限流等）才终止整批。
    """
    cache_dir = cache_dir if cache_dir is not None else library_data_root() / ".cache" / "favorite_metrics"
    seen = set()
    misses: list[tuple[Path, dict]] = []
    for row in rows:
        key = _key(platform, row)
        if not key or key in seen:
            continue
        seen.add(key)
        path = cache_dir / (hashlib.sha256(key.encode()).hexdigest() + ".json")
        cached = _read_cache(path, time.time())
        if cached is not None:
            _emit(sink, platform, row, cached["metrics"], cached["at"], cached.get("approximate", []), True)
        else:
            misses.append((path, row))
    if not misses:
        return

    workers = max(1, min(int(concurrency or ENRICHMENT_CONCURRENCY), len(misses)))
    deadline = time.monotonic() + ENRICHMENT_TIMEOUT

    async def drain(slot: int) -> None:
        if slot:
            # 错开各槽位的首次请求，避免开局就是一个并发突刺。
            await asyncio.sleep(REQUEST_INTERVAL * slot / workers)
        first = True
        while misses:
            # 先取走任务再让出事件循环：两个槽位不会抢到同一条。
            path, row = misses.pop(0)
            if not first:
                await asyncio.sleep(REQUEST_INTERVAL)
            first = False
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                misses.insert(0, (path, row))
                break
            # Exceptions stop this platform immediately; streamed list rows survive.
            approximate = set()
            try:
                metrics = await asyncio.wait_for(fetch_metrics(platform, client, row, approximate),
                                                 timeout=min(REQUEST_TIMEOUT, remaining))
            except asyncio.TimeoutError:
                continue  # 单条拖慢不该连坐整批
            if metrics is None:
                sink([{**row, "_metrics_status": "unavailable"}])
                continue
            at = time.time()
            _save_cache(path, metrics, at, approximate)
            _emit(sink, platform, row, metrics, at, approximate, False)

    tasks = [asyncio.create_task(drain(slot)) for slot in range(workers)]
    try:
        await asyncio.gather(*tasks)
    except BaseException:
        # 一个槽位撞上限流/风控就整批停下，不让其余槽位继续往同一个平台上撞。
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise
    if misses:
        utils.logger.info(
            "[favorite_metrics] %s 指标补全预算用尽，还有 %d 条未补全（下次同步优先命中缓存）",
            platform, len(misses))
