# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

"""抖音收藏列表的页面内读取。

收藏列表接口由页面上下文里的 Argus 安全插件校验：后端用自己拼的
``a_bogus`` 发同一请求，返回 403 ``Blocked by ArgusSecurityPlugin Uifid
Not Found``（2026-10-27 实测，``media_platform/douyin/client.py`` 的历史
POST 路径）。官方收藏页自己发出的同一请求是 200，因此改为在页面内
``fetch``，只读取并把结果投影成适配器需要的公开字段（``libs/douyin_collection.js``）。

这不是"另造一条绕过风控的通道"：请求、Cookie、签名都由用户已登录的
官方页面发出，与站内阅读复用官方页面响应的做法一致；本模块不写平台数据。
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict

from base.runtime_paths import resource_path

#: 官方收藏页。列表接口只在页面处于该路由时才被 Argus 放行。
COLLECTION_URL = "https://www.douyin.com/user/self?showTab=collection"
#: 页面脚本装载完成、安全插件就绪前的请求会被拒（实测 1.6s 仍 403、3.2s 通过），
#: 重试覆盖这个预热窗口。
WARMUP_ATTEMPTS = 12
WARMUP_INTERVAL = 0.5
NAVIGATION_TIMEOUT = 30_000


class CollectionUnavailableError(Exception):
    """收藏页没能给出可用数据；可重试，不等同于登录失效。"""

    safe_code = "failed"

    def __init__(self, message: str):
        self.safe_message = message
        super().__init__(message)


def _script() -> str:
    return resource_path("libs", "douyin_collection.js").read_text(encoding="utf-8")


async def ensure_collection_page(page) -> None:
    """把页面导航到官方收藏页；已在该页时不重复导航。

    列表接口在首页（``/``）会被 Argus 拒绝，在收藏页才放行，所以"页面可用"
    的前提是路由正确，而不只是浏览器开着。
    """
    if page is None:
        raise CollectionUnavailableError("抖音收藏需要浏览器页面，暂不能同步收藏")
    current = getattr(page, "url", "") or ""
    if "/user/self" in current and "showTab=collection" in current:
        return
    try:
        await page.goto(COLLECTION_URL, wait_until="domcontentloaded",
                        timeout=NAVIGATION_TIMEOUT)
    except Exception:
        raise CollectionUnavailableError("打不开抖音收藏页，暂不能同步收藏") from None


async def _evaluate(page, cursor: int, count: int) -> Dict[str, Any]:
    result = await page.evaluate(_script(), {"cursor": max(int(cursor), 0),
                                            "count": min(max(int(count), 1), 20)})
    return result if isinstance(result, dict) else {}


async def fetch_collected_page(page, cursor: int = 0, count: int = 20) -> Dict[str, Any]:
    """读取一页收藏；页面尚未就绪时在预热窗口内重试。

    成功返回 ``{"status_code": 0, "has_more": ..., "cursor": ..., "aweme_list": [...]}``，
    与原来的接口响应同形，调用方无需区分数据来自页面还是后端。
    明确未登录（``status_code`` 非零）原样返回，由调用方判定登录失效；
    预热窗口耗尽、HTTP 非 200 或结构异常抛 ``CollectionUnavailableError``。
    """
    await ensure_collection_page(page)
    last_status = None
    for attempt in range(WARMUP_ATTEMPTS):
        result = await _evaluate(page, cursor, count)
        body = result.get("body")
        if isinstance(body, dict):
            return body
        last_status = result.get("status")
        if attempt + 1 < WARMUP_ATTEMPTS:
            await asyncio.sleep(WARMUP_INTERVAL)
    if last_status == 403:
        raise CollectionUnavailableError(
            "抖音收藏页没有就绪，暂不能同步收藏；请稍后重试")
    raise CollectionUnavailableError("抖音收藏接口返回异常，暂不能同步收藏")
