# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
# This file is part of MediaCrawler project.
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

"""热搜词服务的边界测试。

守住的几件事：
- 三个平台各自的上游结构都能正确取词（抖音 / B站 / 知乎）；
- 小红书不在其中：没有取数器，请求它不会打上游，也不返回空占位；
- 空词跳过、热度不是整数就留空（不猜、不补零），条数有上限；
- 单平台失败不影响其它平台，错误只给安全文案（绝不回显上游异常文本）；
- 缓存**不过期**：命中不再打上游，``bypass_cache`` 只跳过读取。

全部用假 fetcher，不访问网络。
"""

from __future__ import annotations

import asyncio

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.routers.trending import trending_router
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


def test_zhihu_uses_home_hot_list_question_titles_in_source_order(monkeypatch):
    payload = {"data": [
        {"target": {"type": "question", "title": "当前热榜第一个问题？"}, "detail_text": "1091 万热度"},
        {"target": {"type": "question", "title": "当前热榜第二个问题？"}, "detail_text": "0 热度"},
        {"target": {"type": "question", "title": "缺少热度的问题？"}},
        {"target": {"type": "question", "title": " "}, "detail_text": "123 万热度"},
        {"target": {"type": "answer", "title": "不是问题热榜项"}},
        {"target": "invalid"},
        "not an item",
    ], "top_search": {"words": [{"query": "旧入口残留词"}]},
        "hot_search_queries": [{"query": "搜索框词榜也不应混入"}]}

    async def fake_get(url, params=None, headers=None):
        assert url == "https://api.zhihu.com/topstory/hot-lists/total"
        assert params == {"limit": str(svc.MAX_WORDS), "reverse_order": "0"}
        return payload

    monkeypatch.setattr(svc, "_get_json", fake_get)
    words, active_time = asyncio.run(svc._fetch_zhihu())
    assert words == [
        {"rank": 1, "word": "当前热榜第一个问题？", "hot_value": 10910000},
        {"rank": 2, "word": "当前热榜第二个问题？", "hot_value": 0},
        {"rank": 3, "word": "缺少热度的问题？", "hot_value": None},
    ]
    assert active_time is None


@pytest.mark.parametrize("raw, expected", [
    ("1091 万热度", 10910000), ("1.2 亿热度", 120000000), (" 123 热度 ", 123),
    ("0 热度", 0), ("上升中", None), ("-1 万热度", None),
    ("619万", None), ("1.5 热度", None), (True, None), (None, None),
])
def test_zhihu_heat_uses_only_recognized_source_text(raw, expected):
    assert svc._zhihu_heat(raw) == expected


@pytest.mark.parametrize("payload", [
    [], {}, {"data": {}}, {"data": []},
    {"data": [{"target": {"type": "question", "title": " "}}]},
    {"data": [{"target": {"type": "answer", "title": "不是问题热榜"}}]},
    {"top_search": {"words": [{"query": "旧入口词不应冒充当前热搜"}]}},
    {"hot_search_queries": [{"query": "不能以搜索框词榜替代问题热榜"}]},
    {"data": [], "top_search": {"words": [{"query": "旧入口词"}]}},
])
def test_zhihu_failure_never_falls_back_to_old_words(monkeypatch, payload):
    async def fake_get(*args, **kwargs):
        return payload

    monkeypatch.setattr(svc, "_get_json", fake_get)
    result = asyncio.run(svc.get_trending(["zhihu"]))["platforms"]["zhihu"]
    assert result["status"] == "failed"
    assert result["words"] == []
    assert result["message"] == svc.FAILED_MESSAGE


def test_zhihu_api_refresh_replaces_cached_words_from_anonymous_official_endpoint(monkeypatch):
    calls = []

    def respond(request):
        assert request.url == httpx.URL("https://api.zhihu.com/topstory/hot-lists/total?limit=50&reverse_order=0")
        assert "cookie" not in request.headers
        assert "authorization" not in request.headers
        calls.append(1)
        return httpx.Response(200, json={"data": [
            {"target": {"type": "question", "title": f"当前热榜问题{len(calls)}？"}, "detail_text": "1091 万热度"},
        ]})

    original_client = httpx.AsyncClient
    monkeypatch.setattr(svc.httpx, "AsyncClient", lambda **kwargs:
                        original_client(transport=httpx.MockTransport(respond), **kwargs))
    app = FastAPI()
    app.include_router(trending_router)
    with TestClient(app) as client:
        first_response = client.get("/api/trending?platforms=zhihu")
        assert first_response.status_code == 200
        first = first_response.json()
        words = first["platforms"]["zhihu"]["words"]
        assert words == [{"rank": 1, "word": "当前热榜问题1？", "hot_value": 10910000}]
        cached = client.get("/api/trending?platforms=zhihu").json()
        assert cached["cached"] is True
        assert cached["platforms"]["zhihu"]["words"] == words
        assert len(calls) == 1
        fresh = client.get("/api/trending?platforms=zhihu&refresh=true").json()
        assert fresh["cached"] is False
        assert fresh["platforms"]["zhihu"]["words"][0]["word"] == "当前热榜问题2？"
        assert len(calls) == 2


# ── 小红书 ──────────────────────────────────────────────────────────────


def test_xhs_has_no_fetcher_and_is_never_requested(monkeypatch):
    """小红书没有可用的全站热榜：不登记取数器、不请求上游、不返回占位。"""
    assert "xhs" not in svc._FETCHERS
    assert "xhs" not in svc.TRENDING_PLATFORMS

    def fail_if_called(*args, **kwargs):
        raise AssertionError("must not request any upstream for xhs")

    monkeypatch.setattr(svc, "_get_json", fail_if_called)
    result = asyncio.run(svc.get_trending(["xhs"]))
    assert result["platforms"] == {}


def test_requesting_xhs_together_with_real_platforms_only_fetches_the_real_ones(monkeypatch):
    calls = []

    async def fake_get(*args, **kwargs):
        calls.append(1)
        raise AssertionError("xhs 不应计入上游请求")

    monkeypatch.setattr(svc, "_get_json", fake_get)
    monkeypatch.setitem(svc._FETCHERS, "bilibili",
                        _fetcher_returning([{"rank": 1, "word": "B站热词", "hot_value": 1}]))
    result = asyncio.run(svc.get_trending(["xhs", "bilibili"]))["platforms"]
    assert set(result) == {"bilibili"}
    assert result["bilibili"]["status"] == "ok"
    assert calls == []


# ── 取数 ────────────────────────────────────────────────────────────────


def test_unknown_platform_is_ignored():
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


def test_all_trending_platforms_are_fetched_together(monkeypatch):
    """一次请求就把三个平台都取回来（进软件时只获取一次）。"""
    calls = []
    for platform in ("douyin", "bilibili", "zhihu"):
        monkeypatch.setitem(svc._FETCHERS, platform,
                            _fetcher_returning([{"rank": 1, "word": platform, "hot_value": 1}], calls=calls))
    result = asyncio.run(svc.get_trending())
    assert set(result["platforms"]) == {"douyin", "bilibili", "zhihu"}
    assert result["platforms"]["zhihu"]["status"] == "ok"
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


def test_network_failure_does_not_hide_other_platforms(monkeypatch):
    async def failed_get(*args, **kwargs):
        raise httpx.ReadTimeout("private upstream details")

    monkeypatch.setattr(svc, "_get_json", failed_get)
    monkeypatch.setitem(svc._FETCHERS, "bilibili",
                        _fetcher_returning([{"rank": 1, "word": "B站热词", "hot_value": 1}]))
    result = asyncio.run(svc.get_trending(["douyin", "bilibili"]))["platforms"]
    assert result["douyin"]["status"] == "failed"
    assert result["douyin"]["message"] == svc.FAILED_MESSAGE
    assert result["bilibili"]["status"] == "ok"


def test_empty_payload_is_treated_as_failure(monkeypatch):
    monkeypatch.setitem(svc._FETCHERS, "douyin", _fetcher_returning([]))
    result = asyncio.run(svc.get_trending(["douyin"]))
    assert result["platforms"]["douyin"]["status"] == "failed"


def test_unknown_slugs_are_dropped(monkeypatch):
    monkeypatch.setitem(svc._FETCHERS, "douyin", _fetcher_returning([]))
    result = asyncio.run(svc.get_trending(["douyin", "../etc/passwd", "weibo"]))
    assert set(result["platforms"]) == {"douyin"}
