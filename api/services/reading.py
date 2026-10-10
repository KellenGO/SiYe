# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
# This file is part of MediaCrawler project.
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

"""On-demand reading using the existing local account session."""

import asyncio
import time
from collections import OrderedDict
from contextlib import AsyncExitStack

from .accounts import ensure_session_snapshot, get_account_generation, operation_coordinator
from .reading_content import detail_from_page, reading_detail, reading_reference
from .reading_media import reading_media
from .reading_platforms import fetch_platform_reading, platform_reference
from .research_diagnostics import AcquisitionError, classify
from .research_platforms import ResearchBrowserProvider
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
        client = await hydrator._get_zhihu(snapshot)
        path = f"/api/v4/{'answers' if kind == 'answer' else 'articles'}/{identity}"
        params = {"include": "content"} if kind == "answer" else None
        try:
            detail = await asyncio.wait_for(client.get(path, params), timeout=12)
            return await asyncio.to_thread(reading_detail, detail, kind, identity)
        except PermissionError:
            # An explicit content permission refusal is final for this read.
            raise ReadingError("restricted") from None
        except Exception as error:
            code, _ = classify(error)
            if code not in {"restricted", "malformed_response"}:
                raise
        async with AsyncExitStack() as stack:
            provider = ResearchBrowserProvider(stack, {"zhihu": snapshot})
            page = await provider.page("zhihu", client)
            response = await page.goto(url, wait_until="domcontentloaded", timeout=20000)
            if response is not None and response.status != 200:
                raise AcquisitionError("session_expired" if response.status == 401 else
                    "rate_limited" if response.status in {412, 429} else "restricted", response.status)
            detail = await asyncio.to_thread(detail_from_page, await page.content(), kind, identity)
            try:
                return await asyncio.to_thread(reading_detail, detail, kind, identity)
            except PermissionError:
                raise ReadingError("restricted") from None
    finally:
        await hydrator.close()


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


reading_service = ReadingService()
