"""Bounded aggregate-only pagination; state travels inside the worker pipe."""
from __future__ import annotations

import asyncio
import time
from contextvars import ContextVar
from typing import Callable, Optional

from pydantic import BaseModel, Field

from .models import UnifiedSearchResult


class PageState(BaseModel):
    page: int = Field(default=1, ge=1, le=10000)
    offset: int = Field(default=0, ge=0, le=100000)
    search_id: str = Field(default="", max_length=512)
    exhausted: bool = False
    pending: list[UnifiedSearchResult] = Field(default_factory=list, max_length=100)


class PaginationRun:
    MAX_REQUESTS = 4

    def __init__(self, platform: str, keyword: str, limit: int, state: PageState,
                 seen: list[str], emit: Callable, checkpoint: Callable):
        self.platform, self.keyword, self.limit = platform, keyword, limit
        self.state = state.model_copy(deep=True)
        self.seen = set(seen)
        self.emit, self.checkpoint = emit, checkpoint
        self.emitted = self.requests = self.duplicates = 0
        self.fetching = False
        self.started_at = time.perf_counter()
        self.first_api_ms = None
        self.next_request_at = 0.0

    def report(self):
        metrics = {"pagination": self.state.model_dump(), "page_requests": self.requests,
                   "duplicate_count": self.duplicates}
        if self.first_api_ms is not None:
            metrics["search_api_ms"] = self.first_api_ms
        self.checkpoint(metrics)

    def drain(self):
        while self.state.pending and self.emitted < self.limit:
            result = self.state.pending.pop(0)
            if result.content_id in self.seen:
                self.duplicates += 1
                continue
            self.seen.add(result.content_id)
            result.rank = self.emitted
            self.emit(result.model_dump())
            self.emitted += 1
        self.report()

    async def run(self, client):
        import config
        from .adapters.xhs import XhsAdapter
        from .adapters.bilibili import BilibiliAdapter
        from .adapters.douyin import DouyinAdapter
        from .adapters.zhihu import ZhihuAdapter
        adapters = {"xhs": XhsAdapter, "bilibili": BilibiliAdapter,
                    "douyin": DouyinAdapter, "zhihu": ZhihuAdapter}
        adapter = adapters[self.platform]()
        self.drain()
        while self.emitted < self.limit and not self.state.exhausted and self.requests < self.MAX_REQUESTS:
            if self.requests:
                # Browser fallback/setup already counts toward the quiet
                # interval after a failed request; do not wait twice.
                delay = self.next_request_at - time.perf_counter()
                if delay > 0:
                    await asyncio.sleep(delay)
            self.requests += 1
            self.report()  # Failed attempts also count against this action's budget.
            self.fetching = True
            try:
                data = await self.fetch(client)
            finally:
                self.fetching = False
                self.next_request_at = time.perf_counter() + config.CRAWLER_MAX_SLEEP_SEC
            if self.first_api_ms is None:
                self.first_api_ms = int((time.perf_counter() - self.started_at) * 1000)
            items, exhausted, next_offset, search_id = self.unpack(data)
            results = adapter.adapt(items, self.keyword)
            self.state.page += 1
            self.state.offset = next_offset
            self.state.search_id = search_id
            self.state.exhausted = exhausted
            self.state.pending = results[:100]
            self.drain()
        return self.emitted

    async def fetch(self, client):
        if self.platform == "xhs":
            from media_platform.xhs.help import get_search_id
            from media_platform.xhs.field import SearchSortType
            if not self.state.search_id:
                self.state.search_id = get_search_id()
            return await client.get_note_by_keyword(keyword=self.keyword, page=self.state.page,
                search_id=self.state.search_id, sort=SearchSortType.GENERAL)
        if self.platform == "bilibili":
            from media_platform.bilibili.field import SearchOrderType
            return await client.search_video_by_keyword(keyword=self.keyword, page=self.state.page,
                page_size=20, order=SearchOrderType.DEFAULT, pubtime_begin_s=0, pubtime_end_s=0)
        if self.platform == "douyin":
            from media_platform.douyin.field import PublishTimeType
            return await client.search_info_by_keyword(keyword=self.keyword, offset=self.state.offset,
                publish_time=PublishTimeType(0), search_id=self.state.search_id,
                count=min(20, self.limit - self.emitted))
        return await client.get("/api/v4/search_v3", {"gk_version": "gz-gaokao", "t": "general",
            "q": self.keyword, "correction": 1, "offset": self.state.offset, "limit": 20,
            "filter_fields": "", "lc_idx": 0, "show_all_topics": 0, "search_source": "Filter"})

    def unpack(self, data):
        if not isinstance(data, dict):
            raise ValueError("Invalid search page")
        search_id = self.state.search_id
        if self.platform == "xhs":
            items = data.get("items")
            exhausted = not data.get("has_more", False)
        elif self.platform == "bilibili":
            items = data.get("result")
            pages = data.get("numPages")
            exhausted = isinstance(pages, int) and self.state.page >= pages
        elif self.platform == "douyin":
            from media_platform.douyin.core import _classify_douyin_search_response
            _classify_douyin_search_response(data)
            raw = data.get("data", [])
            items = []
            for item in raw:
                if not isinstance(item, dict):
                    continue
                value = item.get("aweme_info")
                if not value:
                    values = (item.get("aweme_mix_info") or {}).get("mix_items") or []
                    value = values[0] if values else None
                if value:
                    items.append(value)
            search_id = str((data.get("extra") or {}).get("logid") or search_id)[:512]
            exhausted = data.get("has_more") in (False, 0)
        else:
            raw = data.get("data")
            if not isinstance(raw, list):
                raise ValueError("Invalid search page")
            items = [item["object"] for item in raw if isinstance(item, dict)
                     and item.get("type") in ("search_result", "zvideo") and item.get("object")]
            exhausted = bool((data.get("paging") or {}).get("is_end")) or not raw
        if not isinstance(items, list):
            raise ValueError("Invalid search page")
        step = 10 if self.platform == "douyin" else 20
        offset = self.state.offset + step
        cursor = data.get("cursor") if self.platform == "douyin" else None
        if isinstance(cursor, int) and not isinstance(cursor, bool) and cursor > self.state.offset:
            offset = cursor
        # A page containing only recommendations is not necessarily the last page.
        empty = not (data.get("data") if self.platform in ("douyin", "zhihu") else items)
        return items, exhausted or empty, offset, search_id


current_pagination: ContextVar[Optional[PaginationRun]] = ContextVar("search_pagination", default=None)


def allow_client_retry(exc=None):
    """Pagination owns list-request retries; legacy callers keep their policy."""
    run = current_pagination.get()
    return run is None or not run.fetching


def check_search_http_status(status):
    """Classify explicit throttling before parsing an HTML/empty error body."""
    if not allow_client_retry() and status in (403, 429, 461, 471):
        from base.exceptions import RateLimitError
        raise RateLimitError(current_pagination.get().platform, "平台请求受限，请稍后重试")
