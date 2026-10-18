# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
# This file is part of MediaCrawler project.
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

"""热搜词服务的边界测试。

守住的几件事：
- 四个平台各自的上游结构都能正确取词（小红书使用匿名第三方榜单并保留来源）；
- 空词跳过、热度不是整数就留空（不猜、不补零），条数有上限；
- 单平台失败不影响其它平台，错误只给安全文案（绝不回显上游异常文本）；
- 缓存**不过期**：命中不再打上游，``bypass_cache`` 只跳过读取；
- 没登记的平台是 ``unavailable``，不是 ``failed``。

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


def test_unregistered_fetcher_is_unavailable(monkeypatch):
    monkeypatch.delitem(svc._FETCHERS, "xhs")
    result = asyncio.run(svc.get_trending(["xhs"]))
    assert result["platforms"]["xhs"]["status"] == "unavailable"


def test_xhs_public_payload_and_source_survive_api_response(monkeypatch):
    calls = []

    async def fake_get(url, params=None, headers=None):
        assert url == svc.XHS_TRENDING_URL
        assert params == {"type": "xiaohongshu"}
        assert not headers
        calls.append(1)
        return {"type": "xiaohongshu", "update_time": "2026-10-18T07:02:09.011Z", "list": [
            {"title": "小红书热词", "hot_value": "947.5w", "url": "https://unused.example/note"},
            {"title": "  ", "hot_value": "1w"},
            {"title": "没有热度", "hot_value": "热"},
            "invalid",
        ]}

    monkeypatch.setattr(svc, "_get_json", fake_get)
    app = FastAPI()
    app.include_router(trending_router)
    with TestClient(app) as client:
        first = client.get("/api/trending?platforms=xhs")
        assert first.status_code == 200
        item = first.json()["platforms"]["xhs"]
        assert item["status"] == "ok"
        assert item["words"] == [
            {"rank": 1, "word": "小红书热词", "hot_value": 9475000},
            {"rank": 2, "word": "没有热度", "hot_value": None},
        ]
        assert item["active_time"] == "2026-10-18T07:02:09.011Z"
        assert item["source_name"] == "UApiPro"
        assert item["source_url"] == svc._SOURCE_METADATA["xhs"]["source_url"]
        second = client.get("/api/trending?platforms=xhs").json()
        assert second["cached"] is True
        assert second["platforms"]["xhs"] == item
        assert len(calls) == 1
        client.get("/api/trending?platforms=xhs&refresh=true")
        assert len(calls) == 2


@pytest.mark.parametrize("raw, expected", [
    ("947.5w", 9475000), ("886W", 8860000), ("3.4万", 34000),
    ("1.2亿", 120000000), ("12345", 12345), (" 0 ", 0), (123, 123),
    ("热", None), ("1.2", None), ("-1", None), ("NaN", None),
    (True, None), (-1, None), (1.5, None), (None, None), ("9" * 100, None),
])
def test_xhs_heat_handles_observed_units_without_inventing_values(raw, expected):
    assert svc._xhs_heat(raw) == expected


@pytest.mark.parametrize("payload", [
    [], {}, {"type": "weibo", "list": [{"title": "别的平台"}]},
    {"type": "xiaohongshu", "list": {}},
    {"type": "xiaohongshu", "list": []},
    {"type": "xiaohongshu", "list": [{"title": " "}]},
    {"type": "xiaohongshu", "code": "UPSTREAM_ERROR", "message": "private upstream details"},
])
def test_xhs_rejects_wrong_platform_empty_or_malformed_payloads(monkeypatch, payload):
    async def fake_get(*args, **kwargs):
        return payload

    monkeypatch.setattr(svc, "_get_json", fake_get)
    result = asyncio.run(svc.get_trending(["xhs"]))["platforms"]["xhs"]
    assert result["status"] == "failed"
    assert result["words"] == []
    assert result["message"] == svc.FAILED_MESSAGE
    assert result["source_name"] == "UApiPro"


def test_xhs_http_request_is_anonymous_and_only_selects_platform(monkeypatch):
    def respond(request):
        assert request.url == httpx.URL(svc.XHS_TRENDING_URL + "?type=xiaohongshu")
        assert "cookie" not in request.headers
        assert "authorization" not in request.headers
        assert "referer" not in request.headers
        assert request.content == b""
        return httpx.Response(200, json={"type": "xiaohongshu", "list": [{"title": "匿名榜单"}]})

    original_client = httpx.AsyncClient
    monkeypatch.setattr(svc.httpx, "AsyncClient", lambda **kwargs:
                        original_client(transport=httpx.MockTransport(respond), **kwargs))
    words, _ = asyncio.run(svc._fetch_xhs())
    assert words[0]["word"] == "匿名榜单"


def test_xhs_network_failure_does_not_hide_other_platforms(monkeypatch):
    async def failed_get(*args, **kwargs):
        raise httpx.ReadTimeout("private upstream details")

    monkeypatch.setattr(svc, "_get_json", failed_get)
    monkeypatch.setitem(svc._FETCHERS, "bilibili",
                        _fetcher_returning([{"rank": 1, "word": "B站热词", "hot_value": 1}]))
    result = asyncio.run(svc.get_trending(["xhs", "bilibili"]))["platforms"]
    assert result["xhs"]["status"] == "failed"
    assert result["xhs"]["message"] == svc.FAILED_MESSAGE
    assert result["xhs"]["source_name"] == "UApiPro"
    assert result["bilibili"]["status"] == "ok"


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
    for platform in ("xhs", "douyin", "bilibili", "zhihu"):
        monkeypatch.setitem(svc._FETCHERS, platform,
                            _fetcher_returning([{"rank": 1, "word": platform, "hot_value": 1}], calls=calls))
    result = asyncio.run(svc.get_trending())
    assert set(result["platforms"]) == {"douyin", "bilibili", "zhihu", "xhs"}
    assert result["platforms"]["xhs"]["status"] == "ok"
    assert len(calls) == 4


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


def test_unknown_slugs_are_dropped(monkeypatch):
    monkeypatch.setitem(svc._FETCHERS, "douyin", _fetcher_returning([]))
    result = asyncio.run(svc.get_trending(["douyin", "../etc/passwd", "weibo"]))
    assert set(result["platforms"]) == {"douyin"}
