# -*- coding: utf-8 -*-
"""Small, deterministic helpers for post-search result hydration."""

from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass
from typing import Awaitable, Callable, List, Optional, Sequence

from .models import UnifiedSearchResult, clean_snippet

HYDRATION_MAX_RESULTS = 12
HYDRATION_CONCURRENCY = 3
HYDRATION_TIMEOUT_SECONDS = 8.0


def metric_candidates(results: Sequence[UnifiedSearchResult]) -> List[UnifiedSearchResult]:
    """Counters need their own selection, independent of snippet quality/rank."""
    from .favorite_metrics import CACHE_TTL, TARGETS
    return [r for r in results if r.platform in ("bilibili", "zhihu")
            and not TARGETS[r.platform] <= r.metrics.keys()
            and not (r.metrics_status in ("partial", "unavailable") and r.metrics_updated_at
                     and 0 <= time.time() - r.metrics_updated_at < CACHE_TTL)]


def _plain(value: Optional[str]) -> str:
    return re.sub(r"[\W_]+", "", (value or "").casefold(), flags=re.UNICODE)


def needs_hydration(result: UnifiedSearchResult) -> bool:
    """Return whether the current snippet is too weak to be useful."""
    snippet = clean_snippet(result.snippet)
    if not snippet:
        return True
    title = _plain(result.title)
    snippet_text = _plain(snippet)
    if title and snippet_text == title:
        return True
    # Do not treat every short sentence as bad, but discard empty-looking
    # labels and fragments that cannot explain the result.
    if len(snippet_text) < 8:
        return True
    if len(snippet_text) < 14 and any(
        marker in snippet for marker in ("暂无", "无内容", "无简介", "点击查看")
    ):
        return True
    return False


def hydration_candidates(
    results: Sequence[UnifiedSearchResult],
    limit: int = HYDRATION_MAX_RESULTS,
) -> List[UnifiedSearchResult]:
    """Select only deficient results from the already ordered first page."""
    if limit <= 0:
        return []
    return [result for result in results[:limit] if needs_hydration(result)]


@dataclass(frozen=True)
class HydrationUpdate:
    result: UnifiedSearchResult
    snippet: Optional[str]


async def hydrate_results(
    results: Sequence[UnifiedSearchResult],
    fetch_snippet: Callable[[UnifiedSearchResult], Awaitable[Optional[str]]],
    *,
    limit: int = HYDRATION_MAX_RESULTS,
    concurrency: int = HYDRATION_CONCURRENCY,
    timeout: float = HYDRATION_TIMEOUT_SECONDS,
    cancel_event: Optional[asyncio.Event] = None,
) -> List[HydrationUpdate]:
    """Hydrate a bounded set with isolated failures and bounded concurrency."""
    candidates = hydration_candidates(results, limit)
    if not candidates:
        return []
    semaphore = asyncio.Semaphore(max(1, concurrency))

    async def one(result: UnifiedSearchResult) -> Optional[HydrationUpdate]:
        if cancel_event is not None and cancel_event.is_set():
            return None
        async with semaphore:
            if cancel_event is not None and cancel_event.is_set():
                return None
            try:
                previous_duration = result.duration_seconds
                value = await asyncio.wait_for(fetch_snippet(result), timeout=timeout)
            except asyncio.CancelledError:
                raise
            except (asyncio.TimeoutError, Exception):
                return None
            if cancel_event is not None and cancel_event.is_set():
                return None
            snippet = clean_snippet(value)
            if not snippet:
                if result.duration_seconds != previous_duration:
                    return HydrationUpdate(result=result, snippet=result.snippet)
                return None
            result.snippet = snippet
            return HydrationUpdate(result=result, snippet=snippet)

    updates = await asyncio.gather(*(one(result) for result in candidates))
    return [update for update in updates if update is not None]
