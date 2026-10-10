# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
# This file is part of MediaCrawler project.
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

"""Existing platform clients adapted to text, image and combined MP4 reading."""

import asyncio
import re
from urllib.parse import parse_qs, urlencode, urlsplit

from .accounts import ensure_session_snapshot
from .reading_content import MAX_TEXT_CHARS
from .reading_media import media_url
from .research_diagnostics import AcquisitionError, classify
from .research_materials import MaterialCollector


def platform_reference(platform, kind, identity, url):
    parsed = urlsplit(url)
    if parsed.scheme != "https" or parsed.username or parsed.password or parsed.port not in {None, 443}:
        raise ValueError("invalid reference")
    path = parsed.path.rstrip("/")
    if platform == "xhs" and kind in {"note", "video"} and re.fullmatch(r"[a-fA-F0-9]{24}", identity):
        if parsed.hostname in {"www.xiaohongshu.com", "xiaohongshu.com"} and path in {f"/explore/{identity}", f"/discovery/item/{identity}"}:
            query = parse_qs(parsed.query)
            token = (query.get("xsec_token") or [""])[0]
            source = (query.get("xsec_source") or ["pc_search"])[0]
            if len(token) > 1024 or not re.fullmatch(r"[A-Za-z0-9_-]{1,40}", source):
                raise ValueError("invalid token")
            return "https://www.xiaohongshu.com/explore/" + identity + "?" + urlencode({"xsec_token": token, "xsec_source": source})
    if platform == "douyin" and kind in {"video", "note"} and re.fullmatch(r"[0-9]{1,30}", identity):
        if parsed.hostname in {"www.douyin.com", "douyin.com"} and path in {f"/video/{identity}", f"/note/{identity}"}:
            return "https://www.douyin.com" + path
    if platform == "bilibili" and kind == "video" and parsed.hostname in {"www.bilibili.com", "bilibili.com"}:
        if (re.fullmatch(r"BV[A-Za-z0-9]{10}", identity) and path == f"/video/{identity}" or
                re.fullmatch(r"[0-9]{1,20}", identity) and path == f"/video/av{identity}"):
            return "https://www.bilibili.com" + path
    raise ValueError("invalid reference")


def first_url(platform, value):
    if not isinstance(value, dict):
        return None
    candidates = [value.get(key) for key in ("master_url", "masterUrl", "url_default", "url", "url_pre")]
    candidates.extend(value.get("url_list") or [])
    for row in value.get("info_list") or []:
        if isinstance(row, dict):
            candidates.append(row.get("url"))
    return next((url for raw in candidates if (url := media_url(platform, raw))), None)


def platform_detail(platform, kind, identity, detail, playback=None, media_notice=""):
    if not isinstance(detail, dict):
        raise ValueError("missing detail")
    if platform == "xhs":
        actual = detail.get("note_id") or detail.get("id")
    elif platform == "douyin":
        actual = detail.get("aweme_id")
    else:
        actual = detail.get("bvid") if identity.startswith("BV") else detail.get("aid")
    if str(actual) != identity:
        raise ValueError("detail identity mismatch")
    text = detail.get("desc") or detail.get("description") or ""
    if not isinstance(text, str):
        raise ValueError("invalid description")
    # These platform descriptions are plain text, including their literal angle brackets.
    blocks = [{"type": "paragraph", "text": text[:MAX_TEXT_CHARS]}] if text.strip() else []
    notices = [media_notice] if media_notice else []
    limited = len(text) > MAX_TEXT_CHARS
    if limited:
        notices.append("正文较长，当前保留前部内容，请在原文中继续阅读。")
    video = detail.get("video") if isinstance(detail.get("video"), dict) else {}
    media = []
    images = []
    if platform == "xhs" and detail.get("type") != "video":
        images = detail.get("image_list")
    elif platform == "douyin":
        images = detail.get("images")
    if platform == "douyin" and not images and isinstance(detail.get("image_post_info"), dict):
        images = detail["image_post_info"].get("images") or []
    if not isinstance(images, list):
        images = []
    for index, image in enumerate(images[:60]):
        url = first_url(platform, image)
        blocks.append({"type": "image", "text": f"原文图片 {index + 1}", "url": url} if url else
                      {"type": "unsupported", "text": f"原文图片 {index + 1} 暂不可用，请在原平台查看。"})
    if len(images) > 60:
        limited = True
        notices.append("图集较长，当前保留前 60 张图片。")
    poster = None
    if platform == "xhs" and not images and detail.get("type") == "video":
        streams = ((video.get("media") or {}).get("stream") or {}).get("h264") or []
        rows = sorted((row for row in streams if isinstance(row, dict)), key=lambda row: row.get("avg_bitrate") or 0)
        url = next((url for row in rows if (url := first_url(platform, row))), None)
        if url:
            media.append({"url": url, "label": "视频"})
        image_list = detail.get("image_list") or []
        poster = first_url(platform, image_list[0]) if image_list else None
    elif platform == "douyin" and not images:
        variants = [row for row in video.get("bit_rate") or [] if isinstance(row, dict) and
                    row.get("format") in {None, "mp4"} and not row.get("is_bytevc1")]
        variants.sort(key=lambda row: row.get("bit_rate") or 0)
        rows = [row.get("play_addr") for row in variants]
        if not video.get("is_bytevc1") and not video.get("is_h265"):
            rows.append(video.get("play_addr"))
        url = next((url for row in rows if (url := first_url(platform, row))), None)
        if url:
            media.append({"url": url, "label": "视频"})
        poster = first_url(platform, video.get("cover") or video.get("origin_cover"))
    elif platform == "bilibili":
        poster = media_url(platform, detail.get("pic"))
        segments = (playback or {}).get("durl") or []
        for index, row in enumerate(segments[:16]):
            url = first_url(platform, row)
            if url:
                media.append({"url": url, "label": "视频" if len(segments) == 1 else f"第 {index + 1} 段"})
        if len(detail.get("pages") or []) > 1:
            notices.append("当前播放主分集，其余分集请在原平台查看。")
        if len(segments) > 16 or len(media) != len(segments):
            limited = True
            notices.append("部分视频段暂不可用，请在原平台继续观看。")
    if not images and (platform == "bilibili" or kind == "video" or detail.get("type") == "video") and not media:
        notices.append("平台暂未提供可播放的视频，请打开原文观看。")
    if not blocks and not media and not notices:
        raise ValueError("empty content")
    dimensions = detail.get("dimension") if platform == "bilibili" else video
    dimensions = dimensions if isinstance(dimensions, dict) else {}
    width, height = dimensions.get("width"), dimensions.get("height")
    portrait = isinstance(width, (int, float)) and isinstance(height, (int, float)) and 0 < width < height
    return {"platform": platform, "content_id": identity, "content_type": kind, "blocks": blocks, "portrait": portrait,
            "media": media, "poster": poster, "limited": limited, "notices": list(dict.fromkeys(notices))}


async def xhs_browser_detail(collector, client, identity, query):
    page = await collector.browser_provider.page("xhs", client)
    payload = {"source_note_id": identity, "image_formats": ["jpg", "webp", "avif"],
               "extra": {"need_body_topic": 1}, "xsec_source": query["xsec_source"], "xsec_token": query["xsec_token"]}
    path = "/api/sns/web/v1/feed"
    headers = await client._pre_headers(path, payload=payload)
    headers = {key: value for key, value in headers.items() if key.lower() in {"x-s", "x-t", "x-s-common", "x-b3-traceid"}}
    response = await page.evaluate("""async ({payload, headers}) => {
        const r = await fetch('https://edith.xiaohongshu.com/api/sns/web/v1/feed', {
            method: 'POST', credentials: 'include', headers: {...headers, 'content-type': 'application/json'}, body: JSON.stringify(payload)});
        const text = await r.text();
        if (!r.ok || text.length > 2 * 1024 * 1024) return {status: r.status};
        try {return {status: r.status, data: JSON.parse(text)}} catch {return {status: r.status}}
    }""", {"payload": payload, "headers": headers})
    status = response.get("status") if isinstance(response, dict) else None
    if status != 200:
        raise AcquisitionError("rate_limited" if status in {429, 461, 471} else "restricted", status)
    data = response.get("data") or {}
    if not data.get("success") or not (data.get("data") or {}).get("items"):
        raise AcquisitionError("restricted")
    return data["data"]["items"][0]["note_card"]


async def fetch_platform_reading(platform, kind, identity, url):
    snapshot = await ensure_session_snapshot(platform, raise_on_error=True) or {}
    collector = MaterialCollector({platform: snapshot})
    try:
        client = await collector.client(platform)
        playback, notice = None, ""
        if platform == "xhs":
            raw = parse_qs(urlsplit(url).query)
            query = {"xsec_source": (raw.get("xsec_source") or ["pc_search"])[0], "xsec_token": (raw.get("xsec_token") or [""])[0]}
            if not query["xsec_token"]:
                raise AcquisitionError("restricted")
            try:
                detail = await collector.request(client.get_note_by_id, identity, query["xsec_source"], query["xsec_token"])
                if not isinstance(detail, dict) or str(detail.get("note_id") or detail.get("id")) != identity:
                    raise AcquisitionError("malformed_response")
            except Exception as error:
                if classify(error)[0] not in {"restricted", "malformed_response"}:
                    raise
                detail = await collector.request(xhs_browser_detail, collector, client, identity, query)
        elif platform == "douyin":
            params = {"aweme_id": identity}
            try:
                response = await collector.request(client.get, "/aweme/v1/web/aweme/detail/", dict(params))
                collector.check_douyin(response)
                if not isinstance(response.get("aweme_detail"), dict):
                    raise AcquisitionError("malformed_response")
            except Exception as error:
                if classify(error)[0] not in {"restricted", "malformed_response"}:
                    raise
                response = await collector.request(collector.browser_provider.douyin_detail, client, params)
                collector.check_douyin(response)
            detail = response.get("aweme_detail")
        else:
            response = await collector.request(client.get_video_info, bvid=identity if identity.startswith("BV") else None,
                                               aid=int(identity) if identity.isdigit() else None)
            detail = response.get("View", response)
            # Identity is checked before requesting a stream from its video/cid fields.
            platform_detail(platform, kind, identity, detail)
            if detail.get("aid") and detail.get("cid"):
                try:
                    playback = await collector.bilibili_player(client, "/x/player/wbi/playurl", {
                        "avid": detail["aid"], "cid": detail["cid"], "qn": 32, "fnval": 0, "fnver": 0, "fourk": 0, "platform": "html5"})
                except Exception as error:
                    code, _ = classify(error)
                    notice = "视频读取触发访问限制，请在原平台检查后稍后重试。" if code == "rate_limited" else "视频读取失败，请重试或在原平台观看。"
        return await asyncio.to_thread(platform_detail, platform, kind, identity, detail, playback, notice)
    finally:
        await collector.close()
