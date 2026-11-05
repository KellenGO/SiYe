# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
# This file is part of MediaCrawler project.
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

"""On-demand reading using the existing local account session."""

import asyncio
import time
import secrets
from collections import OrderedDict

from .accounts import ensure_session_snapshot, get_account_generation, operation_coordinator
from .reading_content import reading_detail, reading_reference
from .reading_browser import reading_page, zhihu_page_detail, logger
from .reading_media import reading_media
from .reading_platforms import fetch_platform_reading, platform_reference
from .acquisition_diagnostics import classify
from aggregate_search.adapters.zhihu import search_reading_payload
from .result_hydration import ResultHydrator
from .reading_zhihu_questions import question_reference, result_question, fetch_question_page, question_page
from aggregate_search.models import clean_title

#: 正文缓存上限。`search_bodies` 记录搜索结果里顺带拿到的知乎正文 ——
#: 以前它**完全没有上限**，一次大搜索命中多少条就涨多少，只有账号切换才清。
#: 单条正文最多 256k 字符，所以必须封顶。
MAX_SEARCH_BODIES = 120
#: 问题分组同样要封顶：一次大搜索可能带出几百个不同问题，每组还挂着回答页。
MAX_QUESTION_GROUPS = 80

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
        self.search_bodies = OrderedDict()
        self.questions = {}
        self.tasks = set()

    @staticmethod
    def _trim(cache, limit):
        while len(cache) > limit:
            cache.popitem(last=False)

    def _remember_search_body(self, key, value):
        self.search_bodies[key] = (time.monotonic(), value)
        self.search_bodies.move_to_end(key)
        self._trim(self.search_bodies, MAX_SEARCH_BODIES)

    def cached_zhihu_body(self, kind, identity, url):
        reading_reference(kind, identity, url)
        row = self.search_bodies.get(("zhihu", get_account_generation("zhihu"), kind, identity))
        return row[1] if row else None

    def remember_search_body(self, payload, generation):
        if generation != get_account_generation("zhihu") or not isinstance(payload, dict):
            return
        kind, identity = payload.get("content_type"), payload.get("content_id")
        detail = payload.get("detail")
        projected = search_reading_payload({**detail, "type": kind}) if isinstance(detail, dict) else None
        if projected is None or projected["content_id"] != identity:
            return
        try:
            value = reading_detail(projected["detail"], kind, identity)
        except (ValueError, TypeError, KeyError, PermissionError):
            return
        if not any(block["type"] != "unsupported" for block in value["blocks"]):
            return
        key = ("zhihu", generation, kind, identity)
        self._remember_search_body(key, value)

    def remember_search_result(self, result, generation):
        if generation != get_account_generation("zhihu"):
            return
        try:
            question = result_question(result)
        except ValueError:
            return
        if not question:
            return
        state = self._question_state(generation, question["id"], question["title"])
        if result.content_id in state["answers"] or len(state["answers"]) < 100:
            state["answers"][result.content_id] = result

    def _question_state(self, generation, identity, title):
        key = (generation, identity)
        if key not in self.questions and len(self.questions) >= MAX_QUESTION_GROUPS:
            # 淘汰最早建立的一组（不再引用它的回答页），防止大搜索把内存吃满。
            self.questions.pop(next(iter(self.questions)), None)
        return self.questions.setdefault(key, {"title": title, "answers": OrderedDict(),
            "pages": {}, "latest": None, "cursor": None, "offset": 0, "requests": 0})

    async def question_answers(self, identity, title, url, cursor=None):
        question_reference(identity, url)
        title = clean_title(title)
        if not title or len(title) > 500:
            raise ValueError("问题标题无效")
        generation = get_account_generation("zhihu")
        state = self._question_state(generation, identity, title)
        if cursor in state["pages"]:
            return {**state["latest"], "answers": [r.model_dump() for r in state["answers"].values()]}
        if cursor != state["cursor"]:
            raise ValueError("回答分页已失效，请重新打开问题")
        if state["requests"] >= 20 or len(state["answers"]) >= 100:
            return {"question_id": identity, "answers": [r.model_dump() for r in state["answers"].values()],
                    "next_cursor": None, "exhausted": False, "limit_reached": True}
        if not await operation_coordinator.acquire_exclusive("reading"):
            raise ReadingError("busy")
        task = None
        try:
            await prepare_reading("zhihu")
            snapshot = await ensure_session_snapshot("zhihu", raise_on_error=True)
            if not snapshot or not snapshot.get("d_c0"):
                raise ReadingError("login_required")
            # 冷启动恢复会话会推进代数；没有种子记录时可以在新代数下开始。
            current = get_account_generation("zhihu")
            if current != generation:
                if state["answers"] or cursor:
                    raise ReadingError("session_expired")
                generation = current
                state = self._question_state(generation, identity, title)
            task = asyncio.create_task(fetch_question_page(snapshot, state["title"], state["offset"]))
            self.tasks.add(task)
            raw = await task
            if generation != get_account_generation("zhihu"):
                raise ReadingError("session_expired")
            try:
                answers, bodies, offset, exhausted = question_page(raw, identity, state["offset"])
            except ValueError:
                raise ReadingError("unavailable") from None
            for answer in answers:
                self.remember_search_result(answer, generation)
            accepted = set(state["answers"])
            for body in bodies:
                if body["content_id"] in accepted:
                    self.remember_search_body(body, generation)
            state["requests"] += 1
            state["offset"] = offset
            limited = state["requests"] >= 20 or len(state["answers"]) >= 100
            next_cursor = None if exhausted or limited else secrets.token_urlsafe(24)
            state["pages"][cursor] = {"question_id": identity, "next_cursor": next_cursor,
                                     "exhausted": exhausted, "limit_reached": limited}
            state["latest"] = state["pages"][cursor]
            state["cursor"] = next_cursor
            return {**state["pages"][cursor], "answers": [r.model_dump() for r in state["answers"].values()]}
        except (ReadingError, ValueError):
            raise
        except Exception as error:
            code, _ = classify(error)
            logger.info("reader platform=zhihu stage=question_search outcome=%s", code)
            raise ReadingError(code) from None
        finally:
            if task is not None:
                self.tasks.discard(task)
            await operation_coordinator.release_exclusive("reading")

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
        search_body = self.search_bodies.get(key)
        if not refresh and search_body:
            self.search_bodies.move_to_end(key)
            logger.info("reader platform=zhihu stage=search_body outcome=ok")
            return search_body[1]
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
            # 详情缓存维持 6 条的既有语义（一次阅读会话里来回看的就那么几条）。
            self._trim(self.cache, 6)
            if platform == "zhihu":
                # 读到正文的同时给搜索命中的条目留一份，供后续打开详情秒开。
                self._remember_search_body(key, value)
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

    def invalidate(self, platform):
        for cache in (self.cache, self.search_bodies):
            for key in list(cache):
                if key[0] == platform:
                    del cache[key]
        if platform == "zhihu":
            self.questions.clear()

    async def cleanup(self):
        tasks = list(self.tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self.cache.clear()
        self.search_bodies.clear()
        self.questions.clear()
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
