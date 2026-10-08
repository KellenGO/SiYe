# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
# This file is part of MediaCrawler project.
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

"""Shared leases for search, login and per-platform account operations."""

import asyncio
from typing import Dict, Optional


class OperationCoordinator:
    """搜索 / 可见登录 / 账号操作的最小互斥协调（无任务队列框架）。

    - 搜索与可见登录占用"排他"租约（同一时间至多一个）。
    - 账号同步/验证/删除使用共享槽位：不同平台最多
      ``max_account_concurrency`` 个同时执行；同一平台严格串行
      （per-platform key，直到该平台后台验证完成才释放）。
    - 有界验证超时后后台任务继续运行期间，该平台槽位不提前失效
      （由任务 done 回调释放），搜索/登录/同平台操作继续被拒绝。
    - 所有释放幂等；shutdown 调用 ``clear()`` 清空协调状态。
    """

    def __init__(self, max_account_concurrency: int = 2):
        self._lock = asyncio.Lock()
        self._max_accounts = max_account_concurrency
        self._exclusive: Optional[str] = None
        self._account_ops: Dict[str, str] = {}

    async def acquire_exclusive(self, kind: str) -> bool:
        """搜索/登录排他租约；账号操作进行中时拒绝。"""
        async with self._lock:
            if self._exclusive is not None:
                return False
            if self._account_ops:
                return False
            self._exclusive = kind
            return True

    async def release_exclusive(self, kind: str) -> None:
        async with self._lock:
            if self._exclusive == kind:
                self._exclusive = None

    async def acquire_account(self, platform: str, kind: str) -> str:
        """账号操作槽位。返回 ""=成功；否则返回拒绝原因字符串：
        ``search``/``login``（排他占用）、``platform``（同平台进行中）、
        ``slots``（并发槽位已满）。"""
        async with self._lock:
            if self._exclusive is not None:
                return self._exclusive
            if platform in self._account_ops:
                return "platform"
            if len(self._account_ops) >= self._max_accounts:
                return "slots"
            self._account_ops[platform] = kind
            return ""

    async def release_account(self, platform: str) -> None:
        async with self._lock:
            self._account_ops.pop(platform, None)

    async def clear(self) -> None:
        """shutdown：清空全部协调状态（幂等）。"""
        async with self._lock:
            self._exclusive = None
            self._account_ops.clear()


operation_coordinator = OperationCoordinator()
