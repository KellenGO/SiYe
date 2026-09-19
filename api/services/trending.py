# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
# This file is part of MediaCrawler project.
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

"""各平台热搜词（只取词，不取内容）。

边界（严格）：

- **只取词**。榜单里的内容由既有的聚合搜索去取 —— 这个模块的产物只有"词 + 热度"。
- **公开接口**：抖音 / B站 / 知乎都走各自公开榜单，不需要登录态、不需要签名、不需要浏览器。
  小红书目前没有可用的公开入口（实测 404 / 500），返回 `unavailable`。
  实测记录见 `docs/plans/2026-09-19-热搜榜方案.md`。
- **不带 cookie**：请求不携带任何登录态，所以既不碰账号验证，也不占"账号/搜索互斥租约"。
- **缓存不过期**：进软件时取一次，之后只有用户手动刷新（`bypass_cache`）才会重新获取。
  这是刻意的 —— 切平台不应该触发重新获取。
- **不进搜索冷却**：看榜不该把搜索拖进冷却。
- **单飞**：同一平台的并发请求只打一次上游。
- **不写库、不上传**：纯进程内缓存，进程退出即失效。
- **失败只给安全文案**：不回显上游响应原文，也不让一个平台挂掉影响其它平台。
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

import httpx

from aggregate_search.models import PLATFORM_SLUGS

# ── 可调参数 ────────────────────────────────────────────────────────────

#: 单次上游请求超时（秒）。热榜是轻量接口，不需要长等待。
REQUEST_TIMEOUT_SECONDS = 10.0

#: 单平台返回词数上限。
MAX_WORDS = 50

#: 未接入平台的固定文案（前端按"该平台暂不可用"处理，不报错）。
UNAVAILABLE_MESSAGE = "该平台热搜暂未接入"
FAILED_MESSAGE = "热搜暂时取不到，稍后再试"

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
)

#: 抖音公开榜单：不需要登录、签名与浏览器。
DOUYIN_TRENDING_URL = "https://www.iesdouyin.com/web/api/v2/hotsearch/billboard/word/"
#: B站搜索热搜：`data.trending.list[]`，带 heat_score。
BILIBILI_TRENDING_URL = "https://api.bilibili.com/x/web-interface/search/square"
#: 知乎热搜词：`top_search.words[]`，没有热度值（只有词）。
ZHIHU_TRENDING_URL = "https://www.zhihu.com/api/v4/search/top_search"

#: 平台 → 取数协程。没有登记的平台一律 unavailable。
Fetcher = Callable[[], Awaitable[Tuple[List[Dict[str, Any]], Optional[str]]]]


# ── 缓存 ────────────────────────────────────────────────────────────────

#: platform → 结果（不过期；只有手动刷新与进程退出会清）
_cache: Dict[str, Dict[str, Any]] = {}
#: platform → 单飞锁（并发时只打一次上游）
_locks: Dict[str, asyncio.Lock] = {}


def clear_cache() -> None:
    """清空缓存（测试与 shutdown 用）。"""
    _cache.clear()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── 规整 ────────────────────────────────────────────────────────────────


def _build_words(pairs: List[Tuple[str, Any]]) -> List[Dict[str, Any]]:
    """把 ``[(词, 热度)]`` 规整成 ``[{rank, word, hot_value}]``。

    空词跳过；热度不是整数就留空（不猜、不补零）；条数按 MAX_WORDS 截断。
    """
    words: List[Dict[str, Any]] = []
    for raw_word, raw_heat in pairs:
        word = raw_word.strip() if isinstance(raw_word, str) else ""
        if not word:
            continue
        hot_value = raw_heat if isinstance(raw_heat, int) and not isinstance(raw_heat, bool) else None
        words.append({"rank": len(words) + 1, "word": word[:200], "hot_value": hot_value})
        if len(words) >= MAX_WORDS:
            break
    return words


def _pick(item: Dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return None


# ── 取数 ────────────────────────────────────────────────────────────────


async def _get_json(url: str, *, params: Optional[Dict[str, str]] = None,
                    headers: Optional[Dict[str, str]] = None) -> Any:
    merged = {"User-Agent": _USER_AGENT, "Accept": "application/json"}
    merged.update(headers or {})
    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS, follow_redirects=True) as client:
        response = await client.get(url, params=params or {}, headers=merged)
        response.raise_for_status()
        return response.json()


async def _fetch_douyin() -> Tuple[List[Dict[str, Any]], Optional[str]]:
    payload = await _get_json(DOUYIN_TRENDING_URL)
    if not isinstance(payload, dict):
        raise ValueError("unexpected douyin payload")
    items = payload.get("word_list")
    pairs = []
    if isinstance(items, list):
        for item in items:
            if isinstance(item, dict):
                pairs.append((_pick(item, "word", "sentence", "name"), item.get("hot_value")))
    active_time = payload.get("active_time")
    return _build_words(pairs), active_time if isinstance(active_time, str) else None


async def _fetch_bilibili() -> Tuple[List[Dict[str, Any]], Optional[str]]:
    payload = await _get_json(BILIBILI_TRENDING_URL, params={"limit": str(MAX_WORDS)},
                              headers={"Referer": "https://www.bilibili.com/"})
    if not isinstance(payload, dict):
        raise ValueError("unexpected bilibili payload")
    data = payload.get("data")
    trending = data.get("trending") if isinstance(data, dict) else None
    items = trending.get("list") if isinstance(trending, dict) else None
    pairs = []
    if isinstance(items, list):
        for item in items:
            if isinstance(item, dict):
                # show_name 是展示名（可能带赛程等补充信息），回落到 keyword。
                pairs.append((_pick(item, "show_name", "keyword"), item.get("heat_score")))
    return _build_words(pairs), None


async def _fetch_zhihu() -> Tuple[List[Dict[str, Any]], Optional[str]]:
    payload = await _get_json(ZHIHU_TRENDING_URL, headers={"Referer": "https://www.zhihu.com/"})
    if not isinstance(payload, dict):
        raise ValueError("unexpected zhihu payload")
    top = payload.get("top_search")
    items = top.get("words") if isinstance(top, dict) else None
    pairs = []
    if isinstance(items, list):
        for item in items:
            if isinstance(item, dict):
                # 知乎热搜只有词，没有热度值。
                pairs.append((_pick(item, "display_query", "query"), None))
    return _build_words(pairs), None


_FETCHERS: Dict[str, Fetcher] = {
    "douyin": _fetch_douyin,
    "bilibili": _fetch_bilibili,
    "zhihu": _fetch_zhihu,
}


async def _load(platform: str) -> Dict[str, Any]:
    """取单个平台的热搜（带缓存与单飞）。"""
    fetcher = _FETCHERS.get(platform)
    if fetcher is None:
        return {"status": "unavailable", "words": [], "message": UNAVAILABLE_MESSAGE}

    cached = _cache.get(platform)
    if cached is not None:
        return {**cached, "cached": True}

    lock = _locks.setdefault(platform, asyncio.Lock())
    async with lock:
        # 等锁期间别人可能已经取回来了，再查一次缓存。
        cached = _cache.get(platform)
        if cached is not None:
            return {**cached, "cached": True}
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
        _cache[platform] = {k: v for k, v in result.items() if k != "cached"}
        return result


async def get_trending(
    platforms: Optional[List[str]] = None,
    *,
    bypass_cache: bool = False,
) -> Dict[str, Any]:
    """按平台分组返回热搜。

    **默认只读缓存**：``bypass_cache=True``（用户手动刷新）才会重新获取上游。
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
