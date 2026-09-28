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
Zhihu (知乎) adapter.

IMPORTANT: The existing ZhihuExtractor masks nicknames via ``mask_nickname()``
before creating ``ZhihuContent`` objects. For aggregate search, we need the
PUBLIC nickname. This adapter accepts *raw* JSON dicts (from the search API
response) rather than ``ZhihuContent`` objects so we can access
``author.name`` directly.

Native data shape (raw dict from ``data[].object`` in search response):

Answers:
  {id, type: "answer", title, question: {id}, author: {name, id},
   created_time, voteup_count, comment_count, excerpt, thumbnail, ...}

Articles:
  {id, type: "article", title, author: {name, id},
   created_time, voteup_count, comment_count, excerpt, image_url, title_image, ...}

Zvideos:
  {id, type: "zvideo", title, author: {name, id},
   created_at, voteup_count, comment_count, video_url, cover_url, ...}

The public nickname is extracted from ``author.name`` (NOT masked here).
Existing store masking is preserved — this data only lives in-memory
for the current search job.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from aggregate_search.models import UnifiedSearchResult, _parse_timestamp, clean_snippet, clean_title

from .base import BasePlatformAdapter

ZHIHU_URL = "https://www.zhihu.com"
ZHIHU_ZHUANLAN_URL = "https://zhuanlan.zhihu.com"


class ZhihuAdapter(BasePlatformAdapter):
    PLATFORM = "zhihu"

    def adapt(self, raw_results: List[Any], keyword: str = "") -> List[UnifiedSearchResult]:
        results: List[UnifiedSearchResult] = []
        for rank, item in enumerate(raw_results):
            if not isinstance(item, dict):
                continue

            content_type = self._safe_str(item.get("type"))
            content_id = self._safe_str(item.get("id"))
            if not content_id:
                continue

            title = clean_title(self._safe_str(item.get("title")))
            question = item.get("question")
            if not title and isinstance(question, dict):
                title = clean_title(self._safe_str(question.get("title")))
            if not title:
                title = clean_title(self._safe_str(item.get("excerpt", ""))[:100])

            # Extract PUBLIC nickname from raw author dict (NOT masked)
            author = self._get_public_author(item)

            url = self._build_url(item, content_type, content_id)

            published_at = _parse_timestamp(
                item.get("created_time") or item.get("created") or item.get("created_at")
            )

            # search_v3 已返回 excerpt；它是知乎回答/文章最适合的摘要字段。
            snippet = clean_snippet(
                item.get("excerpt") or item.get("description") or item.get("content")
            )

            cover_url = self._extract_cover_url(item)

            metrics = self._extract_metrics(item)

            results.append(
                UnifiedSearchResult(
                    platform="zhihu",
                    content_id=content_id,
                    content_type=content_type,
                    title=title,
                    snippet=snippet,
                    author=author,
                    url=url,
                    published_at=published_at,
                    cover_url=cover_url,
                    duration_seconds=self._extract_duration(item),
                    metrics=metrics,
                    rank=rank,
                    collection_names=[str(item["_collection_name"])] if item.get("_collection_name") else [],
                )
            )
        return results

    def _extract_duration(self, item: Dict) -> Optional[int]:
        # An answer/article may embed a video; that is not the duration of the content.
        if item.get("type") != "zvideo":
            return None
        video = item.get("video")
        if isinstance(video, dict):
            return self._numeric_duration_seconds(video.get("duration"))
        return None

    def _get_public_author(self, item: Dict) -> Optional[str]:
        """Extract PUBLIC nickname directly from raw JSON author field.

        This is the unmasked name. The existing store continues to use
        mask_nickname() for persisted data.
        """
        author = item.get("author")
        if isinstance(author, dict):
            # Check if author is a member wrapper
            member = author.get("member")
            if isinstance(member, dict):
                return member.get("name")
            return author.get("name")
        return None

    def _build_url(self, item: Dict, content_type: str, content_id: str) -> str:
        question = item.get("question") or {}
        question_id = self._safe_str(question.get("id") if isinstance(question, dict) else "")

        if content_type == "answer":
            if question_id:
                return f"{ZHIHU_URL}/question/{question_id}/answer/{content_id}"
            return f"{ZHIHU_URL}/answer/{content_id}"

        if content_type == "article":
            return f"{ZHIHU_ZHUANLAN_URL}/p/{content_id}"

        if content_type == "zvideo":
            video_url = item.get("video_url")
            if isinstance(video_url, str) and video_url:
                return video_url
            return f"{ZHIHU_URL}/zvideo/{content_id}"

        # Fallback
        content_url = item.get("content_url") or item.get("url")
        if isinstance(content_url, str) and content_url:
            return content_url
        return f"{ZHIHU_URL}/search?q={content_id}"

    def _extract_cover_url(self, raw_item: Dict) -> Optional[str]:
        # Try thumbnail (answers)
        thumb = raw_item.get("thumbnail")
        if isinstance(thumb, str) and thumb and not thumb.startswith("data:"):
            return thumb

        # Try title_image or image_url (articles)
        for key in ("title_image", "image_url", "cover_url"):
            val = raw_item.get(key)
            if isinstance(val, str) and val:
                return val

        # Zvideo cover
        if raw_item.get("type") == "zvideo":
            cover = raw_item.get("cover_url")
            if isinstance(cover, str) and cover:
                return cover

        # No cover — return None, frontend shows platform placeholder
        return None

    def _extract_metrics(self, raw_item: Dict) -> Dict[str, int]:
        metrics: Dict[str, int] = {}
        # 知乎搜索/收藏返回的对象同时带浏览量与收藏数：visits_count 是
        # 阅读/浏览量（回答、文章、视频通用），favorites_count 是收藏数。
        mapping = [
            ("voteup_count", "like_count"),
            ("comment_count", "comment_count"),
            ("favorites_count", "collect_count"),
            ("visits_count", "view_count"),
        ]
        for src, dst in mapping:
            val = self._safe_int(raw_item.get(src), 0)
            if val > 0:
                metrics[dst] = val
        return metrics
