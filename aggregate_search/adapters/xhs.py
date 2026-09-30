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
Xiaohongshu (小红书) adapter.

Native data: note detail dict returned by ``XiaoHongShuClient.get_note_by_id``
and similar methods. Typical fields:

- note_id: str
- title: str
- desc: str
- type: str ("normal" = note, "video" = video)
- time: int (unix seconds)
- user: {nickname: str, user_id: str, avatar: str}
- interact_info: {liked_count, collected_count, comment_count, share_count}
- image_list: [{url_default: str, ...}]
- note_url: str (optional)
- tag_list: [{name: str, ...}]
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from aggregate_search.models import UnifiedSearchResult, _parse_timestamp, clean_snippet, clean_title

from .base import BasePlatformAdapter

# Official xhs domains allowed in navigation URLs. rednote.com is the
# international site — a note_url from it is accepted only because data that
# carries a rednote.com URL can only have come from international mode.
XHS_ALLOWED_HOSTS = {
    "www.xiaohongshu.com",
    "xiaohongshu.com",
    "www.rednote.com",
}
XHS_EXPLORE_URL = "https://www.xiaohongshu.com/explore/{note_id}"


def _is_allowed_xhs_host(url: str) -> bool:
    try:
        host = urlsplit(url).netloc.lower()
    except Exception:
        return False
    return host in XHS_ALLOWED_HOSTS


def build_note_url(
    note_id: str,
    note_url: Optional[str] = None,
    xsec_token: Optional[str] = None,
    xsec_source: Optional[str] = None,
) -> Optional[str]:
    """Build the navigation URL for an xhs note.

    Priority:
      1. an existing note_url, but only if its host is in the official
         domain whitelist — external domains are rejected;
      2. a generated ``https://www.xiaohongshu.com/explore/{note_id}`` URL.
    When xsec_token is present it is appended via ``urllib.parse.urlencode``
    (xsec_source defaults to ``pc_search``). Without a token the plain
    explore URL is used.

    The xsec_token may ONLY appear inside the returned navigation URL —
    never in logs, error_summary, or separate API fields.
    """
    if note_url and isinstance(note_url, str) and _is_allowed_xhs_host(note_url):
        # Search responses may provide an official note_url alongside the
        # opaque search token. Keep that context on the navigation URL so a
        # later best-effort hydration can call the existing detail API.
        if xsec_token and isinstance(xsec_token, str):
            parts = urlsplit(note_url)
            query = dict(parse_qsl(parts.query, keep_blank_values=True))
            if not query.get("xsec_token"):
                query["xsec_token"] = xsec_token
            if not query.get("xsec_source"):
                query["xsec_source"] = xsec_source or "pc_search"
            return urlunsplit(parts._replace(query=urlencode(query)))
        return note_url

    base = XHS_EXPLORE_URL.format(note_id=note_id)
    if not xsec_token or not isinstance(xsec_token, str):
        return base
    query = urlencode({
        "xsec_token": xsec_token,
        "xsec_source": xsec_source or "pc_search",
    })
    return f"{base}?{query}"


class XhsAdapter(BasePlatformAdapter):
    PLATFORM = "xhs"

    def adapt(self, raw_results: List[Any], keyword: str = "") -> List[UnifiedSearchResult]:
        """同时兼容搜索列表项与详情对象：

        A. 原详情对象：``note_id`` / ``title`` / ``user`` / ``interact_info`` /
           ``image_list`` / ``video`` / ``type`` / ``time`` / ``note_url``；
        B. get_note_by_keyword 搜索列表项：外层 ``id`` + 内层 ``note_card``
           （note_id / display_title / user / cover / image_list / type /
           interact_info / time），外层带 xsec_token / xsec_source。

        不得假设所有字段存在；缺 content_id 的项跳过，可选字段缺失照常
        输出（绝不自动回退逐条详情请求）。
        """
        results: List[UnifiedSearchResult] = []
        for rank, item in enumerate(raw_results):
            if not isinstance(item, dict):
                continue
            card = item.get("note_card")
            if not isinstance(card, dict):
                card = {}

            # content_id：外层 id → note_card.note_id → 原详情 note_id
            content_id = self._safe_str(
                item.get("id") or card.get("note_id") or item.get("note_id"))
            if not content_id:
                continue

            # rank：优先 source_index（轻量/流式 sink 已盖章原始搜索序号），
            # 否则保持原始稳定顺序（enumerate）。
            src_rank = item.get("source_index")
            if isinstance(src_rank, int) and not isinstance(src_rank, bool):
                rank = src_rank

            # title：display_title → title → desc 截断 → 安全占位
            # （绝不为了标题去抓详情）。
            title = clean_title(self._safe_str(
                card.get("display_title") or card.get("title")
                or item.get("title") or item.get("display_title")))
            if not title:
                title = clean_title(self._safe_str(item.get("desc", ""))[:100])
            if not title:
                title = "小红书笔记"

            author = self._get_public_nickname(card) \
                or self._get_public_nickname(item)

            url = build_note_url(
                content_id,
                note_url=self._safe_str(item.get("note_url")) or None,
                xsec_token=self._safe_str(item.get("xsec_token")) or None,
                xsec_source=self._safe_str(item.get("xsec_source")) or None,
            ) or XHS_EXPLORE_URL.format(note_id=content_id)

            # 搜索列表有可靠时间字段才解析；没有则 null（不为发布时间抓详情）。
            published_at = _parse_timestamp(
                card.get("time") or item.get("time"))

            # 搜索列表/详情已有 desc，直接作为片段；不额外抓详情。
            snippet = clean_snippet(card.get("desc") or item.get("desc"))

            cover_url = self._extract_cover_url(card) \
                or self._extract_cover_url(item)

            metrics = self._extract_metrics(card) \
                or self._extract_metrics(item)

            content_type = "video" \
                if (card.get("type") or item.get("type")) == "video" else "note"

            duration_seconds = (self._extract_duration(card) or self._extract_duration(item)) \
                if content_type == "video" else None
            if (content_type == "video" and duration_seconds is None
                    and os.environ.get("SIYE_XHS_DURATION_DIAG") == "1"):
                self._record_missing_duration_shape(item)

            results.append(
                UnifiedSearchResult(
                    platform="xhs",
                    content_id=content_id,
                    content_type=content_type,
                    title=title,
                    snippet=snippet,
                    author=author,
                    url=url,
                    published_at=published_at,
                    cover_url=cover_url,
                    duration_seconds=duration_seconds,
                    metrics=metrics,
                    rank=rank,
                    collection_names=[str(item["_collection_name"])] if item.get("_collection_name") else [],
                )
            )
        return results

    @staticmethod
    def _record_missing_duration_shape(item: Dict) -> None:
        """Opt-in local diagnostic: field structure only, never note content or tokens."""
        def shape(value: Any, field: str = "", depth: int = 0) -> Any:
            if depth >= 7:
                return type(value).__name__
            if isinstance(value, dict):
                return {key: shape(child, key, depth + 1)
                        for key, child in value.items()
                        if isinstance(key, str) and re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,49}", key)}
            if isinstance(value, list):
                return {"count": len(value), "first": shape(value[0], field, depth + 1) if value else None}
            if "duration" in field.lower() and isinstance(value, (int, float, str)):
                number = str(value)
                if re.fullmatch(r"\d+(?:\.\d+)?", number):
                    return number
            return type(value).__name__

        try:
            output = Path(__file__).resolve().parents[2] / "build" / f"xhs-duration-shape-{os.getpid()}.jsonl"
            output.parent.mkdir(exist_ok=True)
            with output.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(shape(item), ensure_ascii=False) + "\n")
        except OSError:
            pass

    def _extract_duration(self, item: Dict) -> Optional[int]:
        video = item.get("video")
        if not isinstance(video, dict):
            return None
        capa = video.get("capa")
        if isinstance(capa, dict):
            duration = self._numeric_duration_seconds(capa.get("duration"))
            if duration is not None:
                return duration
        media = video.get("media")
        if not isinstance(media, dict):
            return None
        metadata = media.get("video")
        if isinstance(metadata, dict):
            duration = self._numeric_duration_seconds(metadata.get("duration"))
            if duration is not None:
                return duration
        # Overall metadata uses seconds; individual encoded streams use milliseconds.
        streams = media.get("stream")
        if isinstance(streams, dict):
            for variants in streams.values():
                if not isinstance(variants, list):
                    continue
                for variant in variants:
                    if isinstance(variant, dict):
                        for field in ("duration", "video_duration"):
                            duration = self._numeric_duration_seconds(variant.get(field), milliseconds=True)
                            if duration is not None:
                                return duration
        return None

    def _get_public_nickname(self, item: Dict) -> Optional[str]:
        user = item.get("user") or item.get("author")
        if isinstance(user, dict):
            return (user.get("nickname") or user.get("nick_name")
                    or user.get("name"))
        return None

    @staticmethod
    def _valid_cover_url(candidate: Any) -> Optional[str]:
        """封面 URL 最小安全校验（Round 17.1）：只接受 http/https 或
        protocol-relative（``//host/...``，浏览器按页面协议解析）；去除首尾
        空白；空串与 javascript: 等其他协议返回 None。不做 CDN 升级/代理。
        """
        if not isinstance(candidate, str):
            return None
        c = candidate.strip()
        if not c:
            return None
        if (c.startswith("http://") or c.startswith("https://")
                or c.startswith("//")):
            return c
        return None

    def _extract_cover_url(self, raw_item: Dict) -> Optional[str]:
        # 搜索列表项：note_card.cover{url_default, url_pre, url}
        cover_obj = raw_item.get("cover")
        if isinstance(cover_obj, dict):
            c = (cover_obj.get("url_default") or cover_obj.get("url_pre")
                 or cover_obj.get("url"))
            if self._valid_cover_url(c):
                return c
            # 收藏列表（v2 collect/page）把真实图片地址放在 cover.info_list[].url，
            # cover 本身只有 width/height。
            info_list = cover_obj.get("info_list") or []
            if isinstance(info_list, list):
                for info in info_list:
                    if isinstance(info, dict) and self._valid_cover_url(info.get("url")):
                        return info.get("url")
        # Image list first
        image_list = raw_item.get("image_list") or []
        if isinstance(image_list, list) and image_list:
            first_img = image_list[0]
            if isinstance(first_img, dict):
                url = first_img.get("url_default") or first_img.get("url")
                if self._valid_cover_url(url):
                    return url
                # Check info_list for alternate URL format
                info_list = first_img.get("info_list") or []
                if info_list and isinstance(info_list, list):
                    info = info_list[0]
                    if isinstance(info, dict):
                        if self._valid_cover_url(info.get("url")):
                            return info.get("url")

        # Video cover fallback
        video = raw_item.get("video")
        if isinstance(video, dict):
            cover = video.get("image") or video.get("cover")
            if isinstance(cover, dict):
                c = cover.get("url_default") or cover.get("url")
                if self._valid_cover_url(c):
                    return c
            if self._valid_cover_url(cover):
                return cover

        # Any cover field (string form)
        cover = raw_item.get("cover") or raw_item.get("cover_url")
        if self._valid_cover_url(cover):
            return cover

        return None

    @staticmethod
    def _parse_count(value: Any) -> int:
        """Parse an interaction count that may carry a Chinese unit suffix.

        搜索响应给的是纯数字字符串，而收藏列表给的是 ``"10万"`` / ``"1.2亿"``
        这类带单位的文本。两者都必须还原成整数，否则互动排序会把所有收藏项
        当成 0。无法解析时返回 0（不猜数字）。
        """
        if isinstance(value, bool) or value is None:
            return 0
        if isinstance(value, (int, float)):
            return int(value)
        # 收藏列表里大数会写成 ``"10万+"``（表示"不少于"），末尾的 ``+`` 要去掉，
        # 否则整个数字解析失败、这条内容一个指标都显示不出来。
        text = str(value).strip().replace(",", "").replace(" ", "").rstrip("+")
        if not text:
            return 0
        for suffix, scale in (("亿", 100_000_000), ("万", 10_000),
                              ("w", 10_000), ("W", 10_000)):
            if text.endswith(suffix):
                try:
                    return int(float(text[: -len(suffix)]) * scale)
                except ValueError:
                    return 0
        try:
            return int(float(text))
        except ValueError:
            return 0

    def _extract_metrics(self, raw_item: Dict) -> Dict[str, int]:
        interact = raw_item.get("interact_info") or {}
        metrics: Dict[str, int] = {}
        if isinstance(interact, dict):
            # 搜索列表与收藏列表用 ``shared_count``，详情接口用 ``share_count``；
            # 逐个候选字段取值，命中即止（不猜、不累加）。
            for sources, dst in [
                (("liked_count",), "like_count"),
                (("collected_count",), "collect_count"),
                (("comment_count",), "comment_count"),
                (("shared_count", "share_count"), "share_count"),
            ]:
                for src in sources:
                    val = self._parse_count(interact.get(src))
                    if val > 0:
                        metrics[dst] = val
                        break
        return metrics
