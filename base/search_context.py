# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
# This file is part of MediaCrawler project.
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

"""Search request context shared by platform clients and pagination."""
from __future__ import annotations

from contextvars import ContextVar
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from aggregate_search.pagination import PaginationRun


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
