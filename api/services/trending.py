# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
# This file is part of MediaCrawler project.
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

"""各平台热搜词（只取词，不取内容）。

边界（严格）：

- **只取词**。榜单里的内容由既有的聚合搜索去取 —— 这个模块的产物只有"词 + 热度"。
- **抖音走公开榜单接口**：不需要登录态、不需要签名、不需要浏览器，纯 HTTP 一次请求。
  实测见 `docs/plans/2026-09-19-热搜榜方案.md`。
- **不带 cookie**：请求不携带任何登录态，所以既不碰账号验证，也不占"账号/搜索互斥租约"。
- **独立缓存**（默认 300 秒，``MC_TRENDING_CACHE_TTL_SECONDS`` 可覆盖）：
  与搜索结果缓存（`result_cache`）无关，**不进搜索冷却** —— 看榜不该把搜索拖进冷却。
- **单飞**：同一平台的并发请求只打一次上游。
- **不写库、不上传**：纯进程内缓存，进程退出即失效。
- **失败只给安全文案**：不回显上游响应原文，也不让一个平台挂掉影响其它平台。
"""

from __future__ import annotations

import asyncio
import os
import time
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

import httpx

from aggregate_search.models import PLATFORM_SLUGS

# ── 可调参数 ────────────────────────────────────────────────────────────

#: 缓存 TTL（秒）。抖音榜是分钟级在动，默认 5 分钟足够，也顺便把请求量压到很低。
DEFAULT_CACHE_TTL_SECONDS = 300
_MIN_TTL = 60
_MAX_TTL = 3600

#: 单次上游请求超时（秒）。热榜是轻量接口，不需要长等待。
REQUEST_TIMEOUT_SECONDS = 10.0

#: 单平台返回词数上限（抖音公开榜单是 50 条）。
MAX_WORDS = 50

#: 未接入平台的固定文案（前端按"缺一列"处理，不报错）。
UNAVAILABLE_MESSAGE = "该平台热搜暂未接入"
FAILED_MESSAGE = "热搜暂时取不到，稍后再试"

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
)

#: 抖音公开榜单：不需要登录、签名与浏览器。
DOUYIN_TRENDING_URL = "https://www.iesdouyin.com/web/api/v2/hotsearch/billboard/word/"

#: 平台 → 取数协程。没有登记的平台一律 unavailable。
Fetcher = Callable[[], Awaitable[Tuple[List[Dict[str, Any]], Optional[str]]]]


# ── 缓存 ────────────────────────────────────────────────────────────────

#: platform → (写入时间戳, 结果)
_cache: Dict[str, Tuple[float, Dict[str, Any]]] = {}
#: platform → 单飞锁（并发时只打一次上游）
_locks: Dict[str, asyncio.Lock] = {}


def _parse_env_ttl() -> int:
    raw = os.environ.get("MC_TRENDING_CACHE_TTL_SECONDS", "")
    if not raw:
        return DEFAULT_CACHE_TTL_SECONDS
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_CACHE_TTL_SECONDS
    if value <= 0:
        return 0
    return max(_MIN_TTL, min(_MAX_TTL, value))


def cache_ttl_seconds() -> int:
    """当前缓存 TTL（秒）。0 表示禁用缓存。"""
    return _parse_env_ttl()


def clear_cache() -> None:
    """清空缓存（测试与 shutdown 用）。"""
    _cache.clear()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── 取数 ────────────────────────────────────────────────────────────────


def _normalize_words(raw_items: Any) -> List[Dict[str, Any]]:
    """把上游的榜单数组规整成 ``[{rank, word, hot_value}]``。

    只认 ``word`` 非空的项；``hot_value`` 不是数字就留空（不猜、不补零）。
    """
    if not isinstance(raw_items, list):
        return []
    words: List[Dict[str, Any]] = []
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        word = item.get("word") or item.get("sentence") or item.get("name")
        if not isinstance(word, str) or not word.strip():
            continue
        hot_value = item.get("hot_value")
        if isinstance(hot_value, bool) or not isinstance(hot_value, int):
            hot_value = None
        words.append({"rank": len(words) + 1, "word": word.strip()[:200], "hot_value": hot_value})
        if len(words) >= MAX_WORDS:
            break
    return words


async def _fetch_douyin() -> Tuple[List[Dict[str, Any]], Optional[str]]:
    """抖音公开榜单（无 cookie、无签名、无浏览器）。"""
    headers = {"User-Agent": _USER_AGENT, "Accept": "application/json"}
    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS) as client:
        response = await client.get(DOUYIN_TRENDING_URL, headers=headers)
        response.raise_for_status()
        payload = response.json()
    if not isinstance(payload, dict):
        raise ValueError("unexpected trending payload")
    active_time = payload.get("active_time")
    return _normalize_words(payload.get("word_list")), active_time if isinstance(active_time, str) else None


_FETCHERS: Dict[str, Fetcher] = {
    "douyin": _fetch_douyin,
}


async def _load(platform: str) -> Dict[str, Any]:
    """取单个平台的热搜（带缓存与单飞）。"""
    fetcher = _FETCHERS.get(platform)
    if fetcher is None:
        return {"status": "unavailable", "words": [], "message": UNAVAILABLE_MESSAGE}

    ttl = cache_ttl_seconds()
    now = time.monotonic()
    cached = _cache.get(platform)
    if cached is not None and ttl > 0 and now - cached[0] < ttl:
        return {**cached[1], "cached": True}

    lock = _locks.setdefault(platform, asyncio.Lock())
    async with lock:
        # 等锁期间别人可能已经取回来了，再查一次缓存。
        cached = _cache.get(platform)
        now = time.monotonic()
        if cached is not None and ttl > 0 and now - cached[0] < ttl:
            return {**cached[1], "cached": True}
        try:
            words, active_time = await fetcher()
        except Exception:
            # 失败只给安全文案；绝不上抛，也不回显上游响应。
            return {"status": "failed", "words": [], "message": FAILED_MESSAGE}
        if not words:
            return {"status": "failed", "words": [], "message": FAILED_MESSAGE}
        result: Dict[str, Any] = {
            "status": "ok",
            "words": words,
            "active_time": active_time,
            "cached": False,
        }
        if ttl > 0:
            _cache[platform] = (time.monotonic(), {k: v for k, v in result.items() if k != "cached"})
        return result


async def get_trending(
    platforms: Optional[List[str]] = None,
    *,
    bypass_cache: bool = False,
) -> Dict[str, Any]:
    """按平台分组返回热搜。

    ``bypass_cache`` 只跳过读取（用户手动刷新），成功仍会更新缓存。
    """
    wanted = [p for p in (platforms or PLATFORM_SLUGS) if p in PLATFORM_SLUGS]
    if bypass_cache:
        for platform in wanted:
            _cache.pop(platform, None)

    results = await asyncio.gather(*(_load(platform) for platform in wanted))
    payload: Dict[str, Any] = {}
    any_cached = False
    for platform, result in zip(wanted, results):
        cached = bool(result.pop("cached", False))
        any_cached = any_cached or cached
        payload[platform] = result
    return {
        "platforms": payload,
        "fetched_at": _now_iso(),
        "cached": any_cached,
    }
