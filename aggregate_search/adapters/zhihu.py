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

from aggregate_search.models import UnifiedSearchResult, ZhihuQuestion, _parse_timestamp, clean_snippet, clean_title

from .base import BasePlatformAdapter

ZHIHU_URL = "https://www.zhihu.com"
ZHIHU_ZHUANLAN_URL = "https://zhuanlan.zhihu.com"

# search_v3 的 data[].object 里除了内容还会混入问题、话题、用户等卡片。
# 这些对象没有可阅读或可收藏的正文，不能当作搜索结果。
SUPPORTED_CONTENT_TYPES = frozenset({"answer", "article", "zvideo"})

SEARCH_BODY_MAX_CHARS = 128000
SEARCH_BODY_FLAGS = (
    "is_visible", "is_public", "can_read", "is_deleted", "is_hidden",
    "is_paid", "is_premium", "is_truncated", "truncated", "content_truncated",
    "is_limited", "has_more_content", "is_content_hidden",
)


def search_reading_payload(item: Dict) -> Optional[Dict]:
    """Project only an already-returned body and its access markers for the local pipe."""
    if not isinstance(item, dict) or item.get("type") not in ("answer", "article"):
        return None
    identity = str(item.get("id") or "")
    content = item.get("content")
    if not identity.isascii() or not identity.isdigit() or len(identity) > 30:
        return None
    if not isinstance(content, str) or not content.strip() or len(content) > SEARCH_BODY_MAX_CHARS:
        return None
    detail = {"id": identity, "content": content}
    for key in SEARCH_BODY_FLAGS:
        value = item.get(key)
        if isinstance(value, (bool, int)) or isinstance(value, str) and len(value) <= 16:
            detail[key] = value
    paid = item.get("paid_info")
    if isinstance(paid, dict):
        detail["paid_info"] = {key: value for key, value in paid.items() if key in SEARCH_BODY_FLAGS
                               and (isinstance(value, (bool, int)) or isinstance(value, str) and len(value) <= 16)}
    return {"content_type": item["type"], "content_id": identity, "detail": detail}


class ZhihuAdapter(BasePlatformAdapter):
    PLATFORM = "zhihu"

    def adapt(self, raw_results: List[Any], keyword: str = "") -> List[UnifiedSearchResult]:
        results: List[UnifiedSearchResult] = []
        for rank, item in enumerate(raw_results):
            if not isinstance(item, dict):
                continue

            content_type = self._safe_str(item.get("type"))
            content_id = self._safe_str(item.get("id"))
            if not content_id or content_type not in SUPPORTED_CONTENT_TYPES:
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
                    question=self._extract_question(item) if content_type == "answer" else None,
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
        # 搜索回答的 visits_count 可在不同回答上重复，不能当作回答阅读量。
        mapping = [
            ("voteup_count", "like_count"),
            ("comment_count", "comment_count"),
            ("favorites_count", "collect_count"),
        ]
        if raw_item.get("type") != "answer":
            mapping.append(("visits_count", "view_count"))
        for src, dst in mapping:
            raw = raw_item.get(src)
            val = self._safe_int(raw, -1)
            if raw is not None and not isinstance(raw, bool) and val >= 0:
                metrics[dst] = val
        return metrics

    def _extract_question(self, item: Dict) -> Optional[ZhihuQuestion]:
        question = item.get("question")
        if not isinstance(question, dict):
            return None
        identity = str(question.get("id") or "")
        if not identity.isascii() or not identity.isdigit() or len(identity) > 30:
            return None
        metrics = {}
        for source, target in (("visit_count", "view_count"), ("answer_count", "answer_count")):
            raw = question.get(source)
            count = self._safe_int(raw, -1)
            # search_v3 的 answer_count=0 是未提供总量的占位，不当作真实总数。
            if raw is not None and not isinstance(raw, bool) and count > 0:
                metrics[target] = count
        return ZhihuQuestion(id=identity, title=clean_title(self._safe_str(question.get("title") or item.get("title"))),
                             url=f"{ZHIHU_URL}/question/{identity}", metrics=metrics)
