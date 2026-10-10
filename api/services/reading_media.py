# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
# This file is part of MediaCrawler project.
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

"""Short-lived media handles and bounded, public-CDN streaming for the reader."""

import re
import secrets
import time
from collections import OrderedDict
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx
from fastapi import HTTPException
from starlette.background import BackgroundTask
from starlette.responses import StreamingResponse

from .accounts import get_account_generation
from .public_network import public_target

CDN_HOSTS = {
    "xhs": ("xhscdn.com", "xiaohongshu.com"),
    "douyin": ("douyincdn.com", "douyinvod.com", "douyinpic.com", "byteimg.com", "ibytedtos.com", "pstatp.com", "snssdk.com", "amemv.com"),
    "bilibili": ("bilivideo.com", "bilivideo.cn", "hdslb.com", "biliapi.net"),
}
REFERERS = {"xhs": "https://www.xiaohongshu.com/", "douyin": "https://www.douyin.com/", "bilibili": "https://www.bilibili.com/"}
IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp", "image/avif", "image/gif"}
MAX_IMAGE_BYTES = 20 * 1024 * 1024
MAX_VIDEO_BYTES = 512 * 1024 * 1024


def media_url(platform, raw):
    if not isinstance(raw, str) or len(raw) > 4096:
        return None
    try:
        parsed = urlsplit("https:" + raw if raw.startswith("//") else raw)
        host = (parsed.hostname or "").lower()
        if parsed.scheme not in {"http", "https"} or parsed.username or parsed.password or parsed.port not in {None, 80, 443}:
            return None
        official_play = (platform == "douyin" and host == "www.douyin.com" and parsed.path == "/aweme/v1/play/")
        if not official_play and not any(host == suffix or host.endswith("." + suffix) for suffix in CDN_HOSTS.get(platform, ())):
            return None
        return urlunsplit(("https", host, parsed.path or "/", parsed.query, ""))
    except ValueError:
        return None


class ReadingMedia:
    def __init__(self):
        self.resources = OrderedDict()

    def register(self, platform, url, kind):
        url = media_url(platform, url)
        if not url or kind not in {"image", "video"}:
            return None
        token = secrets.token_urlsafe(24)
        self.resources[token] = (time.monotonic() + 3600, platform, get_account_generation(platform), url, kind)
        while len(self.resources) > 512:
            self.resources.popitem(last=False)
        return "/api/reading/media/" + token

    def resolve(self, token):
        row = self.resources.get(token)
        if not row or row[0] <= time.monotonic() or row[2] != get_account_generation(row[1]):
            self.resources.pop(token, None)
            raise HTTPException(410, "媒体地址已失效，请重新读取内容")
        self.resources.move_to_end(token)
        return row[1], row[3], row[4]

    def clear(self):
        self.resources.clear()

    def available(self, url):
        if not isinstance(url, str) or not url.startswith("/api/reading/media/"):
            return False
        row = self.resources.get(url.rsplit("/", 1)[-1])
        return bool(row and row[0] > time.monotonic() and row[2] == get_account_generation(row[1]))

    async def stream(self, token, range_header=None, method="GET"):
        platform, url, kind = self.resolve(token)
        if range_header and (len(range_header) > 100 or not re.fullmatch(r"bytes=(?:[0-9]{1,18}-[0-9]{0,18}|-[0-9]{1,18})", range_header)):
            raise HTTPException(416, "仅支持单段媒体读取")
        client = httpx.AsyncClient(timeout=httpx.Timeout(20, read=30), trust_env=False, follow_redirects=False)
        response = None
        try:
            for _ in range(6):
                url = media_url(platform, url)
                if not url:
                    raise ValueError("untrusted redirect")
                pinned, host = await public_target(url)
                headers = {"Host": host, "User-Agent": "Mozilla/5.0", "Referer": REFERERS[platform], "Accept-Encoding": "identity"}
                if range_header:
                    headers["Range"] = range_header
                response = await client.send(client.build_request(method, pinned, headers=headers,
                    extensions={"sni_hostname": host}), stream=True)
                if response.status_code not in {301, 302, 303, 307, 308}:
                    break
                location = response.headers.get("location")
                await response.aclose()
                response = None
                if not location:
                    raise ValueError("missing redirect")
                url = urljoin(url, location)
            if response is None or response.status_code not in {200, 206, 416}:
                raise ValueError("media unavailable")
            limit = MAX_IMAGE_BYTES if kind == "image" else MAX_VIDEO_BYTES
            headers = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}
            for name in ("content-length", "content-range", "accept-ranges"):
                if name in response.headers:
                    headers[name] = response.headers[name]
            if response.status_code == 416:
                await response.aclose()
                await client.aclose()
                return StreamingResponse(iter(()), status_code=416, headers={"Cache-Control": "no-store", **({"Content-Range": headers["content-range"]} if "content-range" in headers else {})})
            content_type = response.headers.get("content-type", "").split(";")[0].lower()
            if kind == "image" and content_type not in IMAGE_TYPES:
                raise ValueError("not an image")
            if kind == "video" and content_type not in {"video/mp4", "application/octet-stream"}:
                raise ValueError("not a supported video")
            if int(response.headers.get("content-length", "0")) > limit:
                raise ValueError("media too large")

            async def close():
                await response.aclose()
                await client.aclose()

            async def body():
                used = 0
                try:
                    if method != "HEAD":
                        async for chunk in response.aiter_raw(64 * 1024):
                            used += len(chunk)
                            if used > limit:
                                break
                            yield chunk
                finally:
                    await close()

            return StreamingResponse(body(), status_code=response.status_code, headers=headers,
                                     media_type=content_type if kind == "image" else "video/mp4", background=BackgroundTask(close))
        except BaseException as error:
            if response is not None:
                await response.aclose()
            await client.aclose()
            if isinstance(error, HTTPException):
                raise
            if not isinstance(error, Exception):
                raise
            raise HTTPException(502, "媒体暂不可用，请重新读取或打开原文") from None


reading_media = ReadingMedia()
