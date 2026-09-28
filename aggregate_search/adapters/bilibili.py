# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
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

"""
Bilibili (B站) adapter.

Native data: EITHER the video detail dict from Bilibili's view API
(get_video_info_task returns a dict with a ``View`` sub-dict), OR the FLAT
search-list item from /x/web-interface/wbi/search/type (light-list mode,
The crawler passes lightweight list items. Both list and detail-like shapes
are supported here:

- View (detail):   aid, bvid, title, pic, pubdate,
                   owner: {name, mid}, stat: {view, danmaku, reply,
                   favorite, coin, share, like}
- Flat (search):   aid, bvid, title, pic, pubdate/senddate,
                   author: <string>, arcurl, play, video_review,
                   review, favorites

Privacy: only the PUBLIC display name is mapped — never mid / upic /
personal homepages / internal user IDs.
"""

from __future__ import annotations

import re

from typing import Any, Dict, List, Optional

from aggregate_search.models import UnifiedSearchResult, _parse_timestamp, clean_snippet, clean_title

from .base import BasePlatformAdapter

class BilibiliAdapter(BasePlatformAdapter):
    PLATFORM = "bilibili"

    def adapt(self, raw_results: List[Any], keyword: str = "") -> List[UnifiedSearchResult]:
        results: List[UnifiedSearchResult] = []
        for rank, item in enumerate(raw_results):
            if not isinstance(item, dict):
                continue

            view = item.get("View") or item
            if not isinstance(view, dict):
                continue

            bvid = self._safe_str(view.get("bvid"))
            aid = self._safe_str(view.get("aid"))
            content_id = bvid or aid
            if not content_id:
                continue

            # Search-list titles wrap keywords in <em class="keyword">…</em>
            title = clean_title(self._safe_str(view.get("title")))
            if not title:
                title = "B站视频"

            author = self._get_author(view)

            url = self._extract_url(view, bvid, aid)

            # Flat search items publish pubdate or senddate (both are unix
            # seconds); detail items use pubdate.
            published_at = _parse_timestamp(
                view.get("pubdate") if view.get("pubdate") else view.get("senddate"))

            # 扁平搜索项与 View 详情均可能带 description/desc；仅使用已拿到的字段。
            snippet = clean_snippet(
                view.get("description") or view.get("desc")
                or view.get("dynamic")
            )

            cover_url = self._extract_cover_url(view)

            metrics = self._extract_metrics(view)

            results.append(
                UnifiedSearchResult(
                    platform="bilibili",
                    content_id=content_id,
                    content_type="video",
                    title=title,
                    snippet=snippet,
                    author=author,
                    url=url,
                    published_at=published_at,
                    cover_url=cover_url,
                    duration_seconds=self._duration_seconds(view.get("duration")),
                    metrics=metrics,
                    rank=rank,
                    collection_names=[str(item["_collection_name"])] if item.get("_collection_name") else [],
                )
            )
        return results

    @staticmethod
    def _duration_seconds(value: Any) -> Optional[int]:
        # Search lists use m:ss (or h:mm:ss); detail/favourite lists use seconds.
        if isinstance(value, int) and not isinstance(value, bool):
            return value if value > 0 else None
        if isinstance(value, str):
            value = value.strip()
            if re.fullmatch(r"[0-9]+", value):
                seconds = int(value)
                return seconds if seconds > 0 else None
            if re.fullmatch(r"[0-9]+:[0-5][0-9](?::[0-5][0-9])?", value):
                seconds = 0
                for part in value.split(":"):
                    seconds = seconds * 60 + int(part)
                return seconds if seconds > 0 else None
        return None

    def _get_author(self, view: Dict) -> Optional[str]:
        # Detail shape: owner: {name, mid}. Flat search shape: author: "名".
        # Only the PUBLIC display name is returned — never mid/upic.
        owner = view.get("owner")
        if isinstance(owner, dict):
            name = owner.get("name") or owner.get("uname")
            if name:
                return self._safe_str(name)
        author = view.get("author")
        if isinstance(author, str) and author:
            return self._safe_str(author)
        upper = view.get("upper")
        if isinstance(upper, dict) and upper.get("name"):
            return self._safe_str(upper.get("name"))
        return None

    def _extract_url(self, view: Dict, bvid: str, aid: str) -> str:
        arcurl = view.get("arcurl")
        if isinstance(arcurl, str) and arcurl.startswith("https://www.bilibili.com"):
            return arcurl
        if bvid:
            return f"https://www.bilibili.com/video/{bvid}"
        if aid:
            return f"https://www.bilibili.com/video/av{aid}"
        return ""

    def _extract_cover_url(self, raw_item: Dict) -> Optional[str]:
        pic = raw_item.get("pic")
        if isinstance(pic, str) and pic:
            return pic
        # Some API variants
        cover = raw_item.get("cover")
        if isinstance(cover, str) and cover:
            return cover
        return None

    def _extract_metrics(self, raw_item: Dict) -> Dict[str, int]:
        metrics: Dict[str, int] = {}
        # Detail shape: stat: {view, danmaku, reply, favorite, coin, share, like}
        # 收藏夹列表 shape: cnt_info: {play, danmaku, collect, reply, ...} ——
        # 收藏数在这里叫 ``collect``，不是详情接口的 ``favorite``。
        stats = raw_item.get("stat") or raw_item.get("statistics") or raw_item.get("cnt_info") or {}
        if isinstance(stats, dict):
            mapping = [
                ("view", "view_count"),
                ("danmaku", "danmaku_count"),
                ("reply", "comment_count"),
                ("favorite", "collect_count"),
                ("collect", "collect_count"),
                ("coin", "coin_count"),
                ("share", "share_count"),
                ("like", "like_count"),
                ("play", "view_count"),
            ]
            for src, dst in mapping:
                val = self._safe_int(stats.get(src), 0)
                if val > 0:
                    metrics[dst] = val
        # Flat search-list shape: play / video_review / review / favorites / like
        # (only fills keys not already set by the stat dict). 搜索列表不下发
        # coin，投币只在详情 stat 里存在。
        flat_mapping = [
            ("play", "view_count"),
            ("video_review", "danmaku_count"),
            ("review", "comment_count"),
            ("favorites", "collect_count"),
            ("like", "like_count"),
        ]
        for src, dst in flat_mapping:
            if dst not in metrics:
                val = self._safe_int(raw_item.get(src), 0)
                if val > 0:
                    metrics[dst] = val
        return metrics
