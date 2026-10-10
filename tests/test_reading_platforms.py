# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
# This file is part of MediaCrawler project.
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

"""Platform reading and opaque media streaming without user profiles or network."""

from types import SimpleNamespace

import httpx
import pytest
from fastapi import HTTPException

from api.services import reading, reading_media, reading_platforms
from api.services.operation_coordinator import OperationCoordinator
from api.services.reading_platforms import platform_detail, platform_reference
from api.services.research_diagnostics import AcquisitionError
from tests.fixtures.browser import FakePage

NOTE = "abcdef0123456789abcdef01"
BV = "BV1234567890"


@pytest.mark.parametrize("platform,kind,identity,url", [
    ("xhs", "note", NOTE, f"https://www.xiaohongshu.com/explore/{NOTE}?xsec_token=t&xsec_source=pc_search"),
    ("douyin", "video", "123", "https://www.douyin.com/video/123"),
    ("douyin", "note", "123", "https://www.douyin.com/note/123"),
    ("bilibili", "video", BV, f"https://www.bilibili.com/video/{BV}"),
    ("bilibili", "video", "123", "https://www.bilibili.com/video/av123"),
])
def test_platform_references_preserve_only_required_token(platform, kind, identity, url):
    canonical = platform_reference(platform, kind, identity, url + "&track=secret#fragment" if "?" in url else url + "?track=secret#fragment")
    assert "track" not in canonical and "fragment" not in canonical
    if platform == "xhs":
        assert "xsec_token=t" in canonical
    for bad in (url.replace(".com", ".com.evil.test"), url.replace(identity, "other"), url.replace("https:", "http:")):
        with pytest.raises(ValueError):
            platform_reference(platform, kind, identity, bad)


def test_xhs_order_literal_text_and_image_limit():
    detail = {"note_id": NOTE, "type": "normal", "desc": "第一行\n<script>这是普通文字</script>",
              "image_list": [{"url_default": "http://sns-webpic-qc.xhscdn.com/one"}, {"url_default": "https://evil.test/two"}]}
    value = platform_detail("xhs", "note", NOTE, detail)
    assert value["blocks"][0]["text"] == detail["desc"]
    assert value["blocks"][1]["url"] == "https://sns-webpic-qc.xhscdn.com/one"
    assert value["blocks"][2]["type"] == "unsupported" and not value["media"]
    detail["image_list"] *= 31
    value = platform_detail("xhs", "note", NOTE, detail)
    assert len(value["blocks"]) == 61 and value["limited"]
    with pytest.raises(ValueError):
        platform_detail("xhs", "note", "other", detail)


def test_douyin_gallery_does_not_play_background_music_and_video_prefers_h264():
    detail = {"aweme_id": "123", "desc": "正文", "images": [{"url_list": ["https://p3.douyinpic.com/image"]}],
              "music": {"play_url": {"url_list": ["https://v.douyincdn.com/music"]}},
              "video": {"width": 360, "height": 640, "play_addr": {"url_list": ["https://v.douyincdn.com/default"]}, "bit_rate": [
                  {"bit_rate": 100, "is_bytevc1": 1, "play_addr": {"url_list": ["https://v.douyincdn.com/hevc"]}},
                  {"bit_rate": 200, "format": "mp4", "play_addr": {"url_list": ["https://v.douyincdn.com/h264"]}}]}}
    gallery = platform_detail("douyin", "video", "123", detail)
    assert not gallery["media"] and gallery["blocks"][1]["type"] == "image"
    detail.pop("images")
    video = platform_detail("douyin", "video", "123", detail)
    assert video["media"] == [{"url": "https://v.douyincdn.com/h264", "label": "视频"}] and video["portrait"]


def test_xhs_h264_and_bilibili_combined_segments_not_dash():
    value = platform_detail("xhs", "video", NOTE, {"note_id": NOTE, "type": "video", "video": {
        "media": {"stream": {"h264": [{"master_url": "https://sns-video-bd.xhscdn.com/a.mp4"}]}}}})
    assert value["media"][0]["url"].endswith("a.mp4")
    detail = {"bvid": BV, "aid": 12, "cid": 34, "desc": "简介", "pages": [{"cid": 34}, {"cid": 35}]}
    value = platform_detail("bilibili", "video", BV, detail, {"durl": [
        {"url": "https://upos.bilivideo.com/a.mp4"}, {"url": "https://upos.bilivideo.com/b.mp4"}]})
    assert [row["label"] for row in value["media"]] == ["第 1 段", "第 2 段"] and value["notices"]
    value = platform_detail("bilibili", "video", BV, detail, {"dash": {"video": [{"baseUrl": "https://upos.bilivideo.com/silent"}]}})
    assert not value["media"] and any("原文观看" in row for row in value["notices"])


@pytest.fixture
def collector(monkeypatch):
    closed, calls = [], []
    client = SimpleNamespace()
    async def session(*args, **kwargs):
        return {"a1": "private", "SESSDATA": "private"}
    async def get_client(self, platform):
        calls.append(("client", platform))
        return client
    async def request(self, method, *args, **kwargs):
        return await method(*args, **kwargs)
    async def close(self):
        closed.append(True)
    monkeypatch.setattr(reading_platforms, "ensure_session_snapshot", session)
    monkeypatch.setattr(reading_platforms.MaterialCollector, "client", get_client)
    monkeypatch.setattr(reading_platforms.MaterialCollector, "request", request)
    monkeypatch.setattr(reading_platforms.MaterialCollector, "close", close)
    return client, calls, closed


@pytest.mark.asyncio
async def test_bilibili_stream_uses_verified_identity_and_retains_body_on_playback_error(collector, monkeypatch):
    client, calls, closed = collector
    async def info(**kwargs):
        calls.append(kwargs)
        return {"View": {"bvid": BV, "aid": 12, "cid": 34, "desc": "详情正文"}}
    async def playback(self, client, path, params):
        assert path == "/x/player/wbi/playurl" and params["cid"] == 34 and params["fnval"] == 0
        raise AcquisitionError("rate_limited", 429)
    client.get_video_info = info
    monkeypatch.setattr(reading_platforms.MaterialCollector, "bilibili_player", playback)
    value = await reading_platforms.fetch_platform_reading("bilibili", "video", BV, f"https://www.bilibili.com/video/{BV}")
    assert value["blocks"][0]["text"] == "详情正文" and not value["media"] and value["notices"] and closed == [True]


@pytest.mark.asyncio
async def test_xhs_token_is_used_without_exporting_credentials_and_client_is_closed(collector):
    client, calls, closed = collector
    async def note(identity, source, token):
        assert (identity, source, token) == (NOTE, "pc_search", "private-token")
        return {"note_id": NOTE, "desc": "正文", "type": "normal", "cookie": "private-cookie"}
    client.get_note_by_id = note
    value = await reading_platforms.fetch_platform_reading("xhs", "note", NOTE, f"https://www.xiaohongshu.com/explore/{NOTE}?xsec_token=private-token")
    assert "private" not in str(value) and closed == [True]


@pytest.mark.asyncio
async def test_douyin_only_one_browser_fallback_and_rate_limit_is_final(collector, monkeypatch):
    client, calls, closed = collector
    async def get(*args):
        raise AcquisitionError("restricted", 403)
    async def browser(*args):
        calls.append("fallback")
        return {"status_code": 0, "aweme_detail": {"aweme_id": "123", "desc": "浏览器正文"}}
    client.get = get
    from api.services.research_platforms import ResearchBrowserProvider
    monkeypatch.setattr(ResearchBrowserProvider, "douyin_detail", browser)
    value = await reading_platforms.fetch_platform_reading("douyin", "video", "123", "https://www.douyin.com/video/123")
    assert value["blocks"][0]["text"] == "浏览器正文" and calls.count("fallback") == 1
    async def rate(*args):
        raise AcquisitionError("rate_limited", 429)
    client.get = rate
    with pytest.raises(AcquisitionError):
        await reading_platforms.fetch_platform_reading("douyin", "video", "123", "https://www.douyin.com/video/123")
    assert calls.count("fallback") == 1 and closed == [True, True]


@pytest.mark.asyncio
async def test_xhs_browser_response_is_identity_checked(collector, monkeypatch):
    client, calls, closed = collector
    async def headers(*args, **kwargs):
        return {"x-s": "private", "cookie": "secret"}
    client._pre_headers = headers
    page = FakePage(evaluate_result={"status": 200, "data": {"success": True, "data": {
        "items": [{"note_card": {"note_id": NOTE, "desc": "正文"}}]}}})
    async def browser_page(*args):
        return page
    from api.services.research_platforms import ResearchBrowserProvider
    monkeypatch.setattr(ResearchBrowserProvider, "page", browser_page)
    async def fail(*args):
        raise AcquisitionError("restricted", 403)
    client.get_note_by_id = fail
    value = await reading_platforms.fetch_platform_reading("xhs", "note", NOTE,
        f"https://www.xiaohongshu.com/explore/{NOTE}?xsec_token=t")
    assert value["blocks"][0]["text"] == "正文" and closed == [True]


@pytest.mark.asyncio
async def test_cache_is_platform_isolated_and_exports_only_media_handles(monkeypatch):
    monkeypatch.setattr(reading, "operation_coordinator", OperationCoordinator())
    monkeypatch.setattr(reading, "get_account_generation", lambda _: 1)
    monkeypatch.setattr(reading_media, "get_account_generation", lambda _: 1)
    async def fetch(kind, identity, url, platform):
        return platform_detail(platform, kind, identity, {"aweme_id": identity, "desc": "正文", "video": {
            "play_addr": {"url_list": ["https://v.douyincdn.com/video?private-sign"]}}})
    monkeypatch.setattr(reading, "fetch_reading", fetch)
    service = reading.ReadingService()
    value = await service.read("video", "123", "https://www.douyin.com/video/123", platform="douyin")
    assert "private-sign" not in str(value) and value["media"][0]["url"].startswith("/api/reading/media/")
    assert ("douyin", 1, "video", "123") in service.cache
    reading_media.reading_media.clear()
    renewed = await service.read("video", "123", "https://www.douyin.com/video/123", platform="douyin")
    assert renewed["media"][0]["url"] != value["media"][0]["url"]
    await service.cleanup()
    assert not reading_media.reading_media.resources


class BytesStream(httpx.AsyncByteStream):
    def __init__(self, body=b"1234"):
        self.body, self.closed = body, False
    async def __aiter__(self):
        yield self.body
    async def aclose(self):
        self.closed = True


@pytest.fixture
def media_service(monkeypatch):
    requests, streams, clients = [], [], []
    next_response = {"status": 206, "headers": {"content-type": "video/mp4", "content-length": "4", "content-range": "bytes 0-3/10", "accept-ranges": "bytes", "set-cookie": "secret"}}
    async def handler(request):
        requests.append(request)
        body = BytesStream()
        streams.append(body)
        return httpx.Response(next_response["status"], headers=next_response["headers"], stream=body)
    original = httpx.AsyncClient
    def client(**kwargs):
        value = original(transport=httpx.MockTransport(handler), **kwargs)
        clients.append(value)
        return value
    async def target(url):
        return "https://8.8.8.8/video", "v.douyincdn.com"
    monkeypatch.setattr(reading_media.httpx, "AsyncClient", client)
    monkeypatch.setattr(reading_media, "public_target", target)
    monkeypatch.setattr(reading_media, "get_account_generation", lambda _: 1)
    return reading_media.ReadingMedia(), requests, streams, clients, next_response


@pytest.mark.asyncio
async def test_media_range_headers_no_cookies_and_stream_closure(media_service):
    service, requests, streams, clients, _ = media_service
    url = service.register("douyin", "https://v.douyincdn.com/video?sign=private", "video")
    response = await service.stream(url.rsplit("/", 1)[-1], "bytes=0-3")
    assert response.status_code == 206 and response.headers["content-range"] == "bytes 0-3/10"
    assert "set-cookie" not in response.headers and response.headers["cache-control"] == "no-store"
    assert b"".join([chunk async for chunk in response.body_iterator]) == b"1234"
    assert requests[0].headers["range"] == "bytes=0-3" and requests[0].headers["referer"] == "https://www.douyin.com/"
    assert "cookie" not in requests[0].headers and streams[0].closed and clients[0].is_closed


@pytest.mark.asyncio
@pytest.mark.parametrize("headers", [{"content-type": "text/html"}, {"content-type": "image/svg+xml"},
    {"content-type": "video/mp4", "content-length": str(reading_media.MAX_VIDEO_BYTES + 1)}])
async def test_media_rejects_html_and_oversize_without_returning_upstream(media_service, headers):
    service, requests, streams, clients, next_response = media_service
    next_response["headers"] = headers
    token = service.register("douyin", "https://v.douyincdn.com/video", "video").rsplit("/", 1)[-1]
    with pytest.raises(HTTPException) as error:
        await service.stream(token)
    assert error.value.status_code == 502 and streams[0].closed and clients[0].is_closed


@pytest.mark.asyncio
async def test_media_rechecks_redirect_and_blocks_private_dns(media_service, monkeypatch):
    service, requests, streams, clients, next_response = media_service
    next_response.update(status=302, headers={"location": "https://localhost/private"})
    token = service.register("douyin", "https://v.douyincdn.com/video", "video").rsplit("/", 1)[-1]
    with pytest.raises(HTTPException):
        await service.stream(token)
    assert len(requests) == 1 and streams[0].closed and clients[0].is_closed
    async def private(url):
        raise ValueError("private dns")
    monkeypatch.setattr(reading_media, "public_target", private)
    with pytest.raises(HTTPException):
        await service.stream(token)
    assert len(requests) == 1 and clients[-1].is_closed


@pytest.mark.asyncio
async def test_media_expiry_generation_and_invalid_ranges(media_service, monkeypatch):
    service, requests, _, _, _ = media_service
    url = service.register("douyin", "https://v.douyincdn.com/video", "video")
    token = url.rsplit("/", 1)[-1]
    for header in ("bytes=0-1,3-4", "bytes=foo", "private\r\ncookie=x"):
        with pytest.raises(HTTPException) as error:
            await service.stream(token, header)
        assert error.value.status_code == 416
    monkeypatch.setattr(reading_media, "get_account_generation", lambda _: 2)
    with pytest.raises(HTTPException) as error:
        service.resolve(token)
    assert error.value.status_code == 410 and not requests
    assert service.register("douyin", "https://evil.test/video", "video") is None
    assert service.register("douyin", "https://v.douyincdn.com.evil.test/video", "video") is None


@pytest.mark.asyncio
async def test_media_head_and_upstream_unsatisfiable_range(media_service):
    service, requests, streams, clients, next_response = media_service
    token = service.register("douyin", "https://v.douyincdn.com/video", "video").rsplit("/", 1)[-1]
    response = await service.stream(token, method="HEAD")
    assert [chunk async for chunk in response.body_iterator] == [] and streams[-1].closed and clients[-1].is_closed
    next_response.update(status=416, headers={"content-range": "bytes */10"})
    response = await service.stream(token, "bytes=99-")
    assert response.status_code == 416 and response.headers["content-range"] == "bytes */10" and streams[-1].closed


@pytest.mark.asyncio
async def test_stream_closed_when_consumer_stops_early(media_service):
    service, _, streams, clients, _ = media_service
    token = service.register("douyin", "https://v.douyincdn.com/video", "video").rsplit("/", 1)[-1]
    response = await service.stream(token)
    await anext(response.body_iterator)
    await response.body_iterator.aclose()
    assert streams[-1].closed and clients[-1].is_closed


@pytest.mark.asyncio
async def test_disconnect_before_stream_iteration_still_closes(media_service):
    service, _, streams, clients, _ = media_service
    token = service.register("douyin", "https://v.douyincdn.com/video", "video").rsplit("/", 1)[-1]
    response = await service.stream(token)
    await response.background()
    assert streams[-1].closed and clients[-1].is_closed
