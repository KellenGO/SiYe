# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler/blob/main/test/test_expiring_local_cache.py
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

# 声明：本代码仅供学习和研究目的使用。使用者应遵守以下原则：
# 1. 不得用于任何商业用途。
# 2. 使用时应遵守目标平台的使用条款和robots.txt规则。
# 3. 不得进行大规模爬取或对平台造成运营干扰。
# 4. 应合理控制请求频率，避免给目标平台带来不必要的负担。
# 5. 不得用于任何非法或不当的用途。
#
# 详细许可条款请参阅项目根目录下的LICENSE文件。
# 使用本代码即表示您同意遵守上述原则和LICENSE中的所有条款。


# -*- coding: utf-8 -*-
# @Author  : relakkes@gmail.com
# @Name: Programmer Ajiang-Relakkes
# @Time    : 2024/6/2 10:35
# @Desc    :

import asyncio
import gc
import weakref
from unittest.mock import patch

import pytest

from cache.local_cache import ExpiringLocalCache


def test_set_get_and_expired_key():
    with patch("cache.local_cache.time.time", return_value=100):
        cache = ExpiringLocalCache()
        cache.set("key", "value", 10)
        assert cache.get("key") == "value"
    with patch("cache.local_cache.time.time", return_value=111):
        assert cache.get("key") is None
    assert "key" not in cache._cache_container


def test_clear_removes_multiple_expired_keys_and_keeps_live_values():
    with patch("cache.local_cache.time.time", return_value=100):
        cache = ExpiringLocalCache()
        cache.set("expired-1", "one", 1)
        cache.set("expired-2", "two", 2)
        cache.set("live", "three", 10)
    with patch("cache.local_cache.time.time", return_value=103):
        cache._clear()
    assert cache.keys("*") == ["live"]
    assert cache.keys("li*") == ["live"]


def test_sync_cache_does_not_create_an_idle_event_loop():
    with patch("asyncio.new_event_loop", side_effect=AssertionError("unexpected loop")):
        cache = ExpiringLocalCache()
    assert cache._cron_task is None


@pytest.mark.asyncio
async def test_scheduled_clear_continues_and_close_joins_task():
    cache = ExpiringLocalCache(cron_interval=0.001)
    cache.set("expired-1", "one", -1)
    cache.set("expired-2", "two", -1)
    task = cache._cron_task
    try:
        async with asyncio.timeout(1):
            while cache._cache_container:
                await asyncio.sleep(0)
        assert not task.done()
    finally:
        await cache.aclose()
    assert task.done()
    await cache.aclose()


@pytest.mark.asyncio
async def test_timer_does_not_keep_discarded_cache_alive():
    cache = ExpiringLocalCache()
    task, reference = cache._cron_task, weakref.ref(cache)
    await asyncio.sleep(0)
    del cache
    gc.collect()
    assert reference() is None
    await asyncio.gather(task, return_exceptions=True)
    assert task.done()
