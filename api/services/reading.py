# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
# This file is part of MediaCrawler project.
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

"""On-demand reading using the existing local account session."""

import asyncio
import time
from collections import OrderedDict

from .accounts import ensure_session_snapshot, get_account_generation, operation_coordinator
from .reading_content import reading_detail, reading_reference
from .reading_browser import reading_page, zhihu_page_detail, logger
from .reading_media import reading_media
from .reading_platforms import fetch_platform_reading, platform_reference
from .acquisition_diagnostics import classify
from .result_hydration import ResultHydrator

MESSAGES = {
    "busy": "搜索、研究或账号操作正在进行，请结束后重试读取正文。",
    "login_required": "请先在设置中登录此平台，再重试读取内容。",
    "session_expired": "平台登录已失效，请重新登录后重试。",
    "rate_limited": "平台触发验证码或访问限制，请在原平台检查后稍后重试。",
    "restricted": "平台暂未提供此内容的读取权限，请打开原文查看。",
    "timeout": "正文读取超时，请重试或打开原文。",
    "unavailable": "暂时无法读取正文，请重试或打开原文。",
}


class ReadingError(Exception):
    def __init__(self, code):
        self.code = code if code in MESSAGES else "unavailable"
        super().__init__(MESSAGES[self.code])


async def prepare_reading(platform="zhihu"):
    from .search_job_manager import search_job_manager
    if search_job_manager.is_search_active():
        raise ReadingError("busy")
    await search_job_manager.stop_platform_worker(platform)


async def fetch_reading(kind, identity, url, platform="zhihu"):
    await prepare_reading(platform)
    if platform != "zhihu":
        return await fetch_platform_reading(platform, kind, identity, url)
    snapshot = await ensure_session_snapshot("zhihu", raise_on_error=True)
    if not snapshot or not snapshot.get("d_c0"):
        raise ReadingError("login_required")
    hydrator = ResultHydrator()
    try:
        async with reading_page("zhihu", snapshot) as page:
            client = await hydrator._get_zhihu(snapshot)
            client.default_headers["user-agent"] = await page.evaluate("() => navigator.userAgent")
            path = f"/api/v4/{'answers' if kind == 'answer' else 'articles'}/{identity}"
            params = {"include": "content"} if kind == "answer" else None
            try:
                detail = await asyncio.wait_for(client.get(path, params), timeout=12)
                logger.info("reader platform=zhihu stage=detail_api outcome=ok")
                return await asyncio.to_thread(reading_detail, detail, kind, identity)
            except PermissionError:
                raise ReadingError("restricted") from None
            except Exception as error:
                code, status = classify(error)
                logger.info("reader platform=zhihu stage=detail_api outcome=%s http_status=%s", code, status)
                if code not in {"restricted", "malformed_response"}:
                    raise
            try:
                return await zhihu_page_detail(page, kind, identity, url)
            except PermissionError:
                raise ReadingError("restricted") from None
    finally:
        await hydrator.close()


async def fetch_comments(kind, identity, url, platform):
    from .platform_materials import MaterialCollector
    await prepare_reading(platform)
    snapshot = await ensure_session_snapshot(platform, raise_on_error=True) or {}
    collector = MaterialCollector({platform: snapshot})
    try:
        client = await collector.client(platform)
        detail = None
        if platform == "bilibili":
            response = await collector.request(client.get_video_info,
                bvid=identity if identity.startswith("BV") else None,
                aid=int(identity) if identity.isdigit() else None)
            detail = response.get("View", response)
            if (identity.startswith("BV") and detail.get("bvid") != identity) or (identity.isdigit() and str(detail.get("aid")) != identity):
                raise ValueError("video identity mismatch")
        result = await collector.comments(client, {"platform": platform, "content_id": identity,
            "content_type": kind, "url": url}, detail)
        return {"platform": platform, "content_id": identity, "content_type": kind,
                "entries": result.get("entries", []), "limited": result.get("truncated", False),
                "sort": result.get("sort", "平台默认"), "notice": result.get("reason", "")}
    finally:
        await collector.close()


class ReadingService:
    def __init__(self):
        self.cache = OrderedDict()
        self.tasks = set()

    async def read(self, kind, identity, url, refresh=False, platform="zhihu"):
        canonical = reading_reference(kind, identity, url) if platform == "zhihu" else platform_reference(platform, kind, identity, url)
        generation = get_account_generation(platform)
        key = (platform, generation, kind, identity)
        cached = self.cache.get(key)
        if cached and platform != "zhihu":
            value = cached[1]
            urls = [row["url"] for row in value["media"]] + [row["url"] for row in value["blocks"] if row["type"] == "image"]
            if value.get("poster"):
                urls.append(value["poster"])
            if not all(reading_media.available(url) for url in urls):
                cached = None
        if not refresh and cached and time.monotonic() - cached[0] < 120:
            self.cache.move_to_end(key)
            return cached[1]
        if not await operation_coordinator.acquire_exclusive("reading"):
            raise ReadingError("busy")
        task = None
        try:
            task = asyncio.create_task(fetch_reading(kind, identity, canonical, platform))
            self.tasks.add(task)
            value = await asyncio.wait_for(task, timeout=75)
            if platform != "zhihu":
                for block in value["blocks"]:
                    if block["type"] == "image":
                        block["url"] = reading_media.register(platform, block["url"], "image")
                for segment in value["media"]:
                    segment["url"] = reading_media.register(platform, segment["url"], "video")
                if value.get("poster"):
                    value["poster"] = reading_media.register(platform, value["poster"], "image")
            # Restoring the session can advance its generation during the read.
            key = (platform, get_account_generation(platform), kind, identity)
            self.cache[key] = (time.monotonic(), value)
            self.cache.move_to_end(key)
            while len(self.cache) > 6:
                self.cache.popitem(last=False)
            return value
        except ReadingError:
            raise
        except Exception as error:
            code, _ = classify(error)
            logger.info("reader platform=%s stage=read outcome=%s", platform, code)
            raise ReadingError(code) from None
        finally:
            if task is not None:
                self.tasks.discard(task)
            await operation_coordinator.release_exclusive("reading")

    async def cleanup(self):
        tasks = list(self.tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self.cache.clear()
        reading_media.clear()

    async def comments(self, kind, identity, url, platform="zhihu"):
        canonical = reading_reference(kind, identity, url) if platform == "zhihu" else platform_reference(platform, kind, identity, url)
        if not await operation_coordinator.acquire_exclusive("reading"):
            raise ReadingError("busy")
        task = None
        try:
            task = asyncio.create_task(fetch_comments(kind, identity, canonical, platform))
            self.tasks.add(task)
            return await asyncio.wait_for(task, timeout=75)
        except ReadingError:
            raise
        except Exception as error:
            code, _ = classify(error)
            raise ReadingError(code) from None
        finally:
            if task is not None:
                self.tasks.discard(task)
            await operation_coordinator.release_exclusive("reading")


reading_service = ReadingService()
