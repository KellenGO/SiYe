# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
# This file is part of MediaCrawler project.
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

"""热搜词服务的边界测试。

守住的几件事：
- 只认有 ``word`` 的项，热度不是整数就留空（不猜、不补零），条数有上限；
- 单平台失败不影响其它平台，错误只给安全文案（绝不回显上游异常文本）；
- 缓存命中不重复打上游，``bypass_cache`` 只跳过读取；
- 没登记的平台是 ``unavailable``，不是 ``failed``。

全部用假 fetcher，不访问网络。
"""

from __future__ import annotations

import asyncio

import pytest

from api.services import trending as svc


@pytest.fixture(autouse=True)
def _clean_cache():
    svc.clear_cache()
    yield
    svc.clear_cache()


def _fetcher_returning(words, active_time="2026-09-19 13:41:06", calls=None):
    async def fake():
        if calls is not None:
            calls.append(1)
        return words, active_time

    return fake


# ── 规整 ────────────────────────────────────────────────────────────────


def test_normalize_keeps_only_named_items_and_ranks_them():
    raw = [
        {"word": "第一条", "hot_value": 100},
        {"word": "   ", "hot_value": 1},
        {"hot_value": 5},
        "不是字典",
        {"word": "第二条", "hot_value": "很高"},
    ]
    words = svc._normalize_words(raw)
    assert [w["word"] for w in words] == ["第一条", "第二条"]
    assert [w["rank"] for w in words] == [1, 2]
    assert words[0]["hot_value"] == 100
    assert words[1]["hot_value"] is None  # 非整数热度留空，不补 0


def test_normalize_respects_word_cap():
    raw = [{"word": f"w{i}", "hot_value": i} for i in range(svc.MAX_WORDS + 20)]
    words = svc._normalize_words(raw)
    assert len(words) == svc.MAX_WORDS


def test_normalize_handles_non_list():
    assert svc._normalize_words(None) == []
    assert svc._normalize_words({"word": "x"}) == []


# ── 取数 ────────────────────────────────────────────────────────────────


def test_unknown_platform_is_unavailable(monkeypatch):
    result = asyncio.run(svc.get_trending(["xhs"]))
    assert result["platforms"]["xhs"]["status"] == "unavailable"
    assert result["platforms"]["xhs"]["message"] == svc.UNAVAILABLE_MESSAGE
    assert result["platforms"]["xhs"]["words"] == []


def test_success_is_cached_and_can_be_bypassed(monkeypatch):
    calls = []
    monkeypatch.setitem(svc._FETCHERS, "douyin",
                        _fetcher_returning([{"rank": 1, "word": "热词", "hot_value": 9}], calls=calls))

    first = asyncio.run(svc.get_trending(["douyin"]))
    assert first["platforms"]["douyin"]["status"] == "ok"
    assert first["platforms"]["douyin"]["words"][0]["word"] == "热词"
    assert first["platforms"]["douyin"]["active_time"] == "2026-09-19 13:41:06"
    assert len(calls) == 1

    second = asyncio.run(svc.get_trending(["douyin"]))
    assert len(calls) == 1, "缓存命中不应重复请求上游"
    assert second["cached"] is True

    third = asyncio.run(svc.get_trending(["douyin"], bypass_cache=True))
    assert len(calls) == 2, "bypass_cache 只跳过读取"
    assert third["cached"] is False


def test_failure_is_isolated_and_message_is_safe(monkeypatch):
    async def boom():
        raise RuntimeError("上游返回了 461 和一堆内部细节")

    monkeypatch.setitem(svc._FETCHERS, "douyin", boom)
    monkeypatch.setitem(svc._FETCHERS, "bilibili",
                        _fetcher_returning([{"rank": 1, "word": "B站热词", "hot_value": 1}]))

    result = asyncio.run(svc.get_trending(["douyin", "bilibili"]))
    failed = result["platforms"]["douyin"]
    assert failed["status"] == "failed"
    assert failed["message"] == svc.FAILED_MESSAGE
    assert "461" not in failed["message"]
    # 一个平台失败不影响另一个
    assert result["platforms"]["bilibili"]["status"] == "ok"


def test_empty_payload_is_treated_as_failure(monkeypatch):
    monkeypatch.setitem(svc._FETCHERS, "douyin", _fetcher_returning([]))
    result = asyncio.run(svc.get_trending(["douyin"]))
    assert result["platforms"]["douyin"]["status"] == "failed"


def test_unknown_slugs_are_dropped():
    result = asyncio.run(svc.get_trending(["douyin", "../etc/passwd", "weibo"]))
    assert set(result["platforms"]) == {"douyin"}


def test_cache_ttl_is_clamped(monkeypatch):
    monkeypatch.setenv("MC_TRENDING_CACHE_TTL_SECONDS", "5")
    assert svc.cache_ttl_seconds() == svc._MIN_TTL
    monkeypatch.setenv("MC_TRENDING_CACHE_TTL_SECONDS", "99999")
    assert svc.cache_ttl_seconds() == svc._MAX_TTL
    monkeypatch.setenv("MC_TRENDING_CACHE_TTL_SECONDS", "0")
    assert svc.cache_ttl_seconds() == 0
    monkeypatch.setenv("MC_TRENDING_CACHE_TTL_SECONDS", "not-a-number")
    assert svc.cache_ttl_seconds() == svc.DEFAULT_CACHE_TTL_SECONDS
