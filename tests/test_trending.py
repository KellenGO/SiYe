# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
# This file is part of MediaCrawler project.
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

"""热搜词服务的边界测试。

守住的几件事：
- 三个平台各自的上游结构都能正确取词（B站用 show_name、知乎没有热度值）；
- 空词跳过、热度不是整数就留空（不猜、不补零），条数有上限；
- 单平台失败不影响其它平台，错误只给安全文案（绝不回显上游异常文本）；
- 缓存**不过期**：命中不再打上游，``bypass_cache`` 只跳过读取；
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


def test_build_words_keeps_only_named_items_and_ranks_them():
    words = svc._build_words([
        ("第一条", 100),
        ("   ", 1),
        (None, 5),
        ("第二条", "很高"),
    ])
    assert [w["word"] for w in words] == ["第一条", "第二条"]
    assert [w["rank"] for w in words] == [1, 2]
    assert words[0]["hot_value"] == 100
    assert words[1]["hot_value"] is None  # 非整数热度留空，不补 0


def test_build_words_respects_cap():
    words = svc._build_words([(f"w{i}", i) for i in range(svc.MAX_WORDS + 20)])
    assert len(words) == svc.MAX_WORDS


def test_pick_prefers_first_non_empty_string():
    assert svc._pick({"show_name": "展示名", "keyword": "原始词"}, "show_name", "keyword") == "展示名"
    assert svc._pick({"show_name": "  ", "keyword": "原始词"}, "show_name", "keyword") == "原始词"
    assert svc._pick({"keyword": 123}, "show_name", "keyword") is None


# ── 各平台上游结构 ──────────────────────────────────────────────────────


def test_douyin_payload_is_parsed(monkeypatch):
    payload = {
        "active_time": "2026-09-19 13:41:06",
        "word_list": [{"word": "抖音热词", "hot_value": 123}, {"word": "", "hot_value": 1}],
    }

    async def fake_get(url, params=None, headers=None):
        assert url == svc.DOUYIN_TRENDING_URL
        return payload

    monkeypatch.setattr(svc, "_get_json", fake_get)
    words, active_time = asyncio.run(svc._fetch_douyin())
    assert [w["word"] for w in words] == ["抖音热词"]
    assert words[0]["hot_value"] == 123
    assert active_time == "2026-09-19 13:41:06"


def test_bilibili_payload_uses_show_name_and_heat_score(monkeypatch):
    payload = {"code": 0, "data": {"trending": {"list": [
        {"keyword": "IG JDG", "show_name": "IG vs JDG LPL资格赛", "heat_score": 21025540},
        {"keyword": "只有 keyword"},
    ]}}}

    async def fake_get(url, params=None, headers=None):
        assert url == svc.BILIBILI_TRENDING_URL
        assert params["limit"] == str(svc.MAX_WORDS)
        return payload

    monkeypatch.setattr(svc, "_get_json", fake_get)
    words, _ = asyncio.run(svc._fetch_bilibili())
    assert words[0]["word"] == "IG vs JDG LPL资格赛"
    assert words[0]["hot_value"] == 21025540
    assert words[1]["word"] == "只有 keyword"
    assert words[1]["hot_value"] is None


def test_zhihu_payload_has_words_without_heat(monkeypatch):
    payload = {"top_search": {"words": [
        {"query": "法考客观题成绩公布", "display_query": "法考客观题成绩公布"},
        {"query": "只有 query"},
    ]}}

    async def fake_get(url, params=None, headers=None):
        assert url == svc.ZHIHU_TRENDING_URL
        return payload

    monkeypatch.setattr(svc, "_get_json", fake_get)
    words, _ = asyncio.run(svc._fetch_zhihu())
    assert [w["word"] for w in words] == ["法考客观题成绩公布", "只有 query"]
    assert all(w["hot_value"] is None for w in words)


def test_xhs_stays_unavailable():
    """小红书没有可用的公开入口，必须保持 unavailable 而不是 failed。"""
    assert "xhs" not in svc._FETCHERS
    result = asyncio.run(svc.get_trending(["xhs"]))
    assert result["platforms"]["xhs"]["status"] == "unavailable"


# ── 取数 ────────────────────────────────────────────────────────────────


def test_unknown_platform_is_unavailable():
    result = asyncio.run(svc.get_trending(["weibo"]))
    assert result["platforms"] == {}


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


def test_all_platforms_are_fetched_together(monkeypatch):
    """一次请求就把登记的平台的都取回来（进软件时只获取一次）。"""
    calls = []
    for platform in ("douyin", "bilibili", "zhihu"):
        monkeypatch.setitem(svc._FETCHERS, platform,
                            _fetcher_returning([{"rank": 1, "word": platform, "hot_value": 1}], calls=calls))
    result = asyncio.run(svc.get_trending())
    assert set(result["platforms"]) == {"douyin", "bilibili", "zhihu", "xhs"}
    assert result["platforms"]["xhs"]["status"] == "unavailable"
    assert len(calls) == 3


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
