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
Unified data models for aggregate cross-platform search.

These DTOs are used by the aggregate_search layer only. MediaCrawler core
and store layers continue to use their own native data structures.
"""

from __future__ import annotations

import html
import re
import unicodedata
from datetime import datetime
from typing import Any, Dict, List, Literal, Optional, get_args

from pydantic import BaseModel, ConfigDict, Field, field_validator


# ── Platform & status enums ────────────────────────────────────────────

PlatformSlug = Literal["xhs", "douyin", "bilibili", "zhihu"]

PLATFORM_SLUGS: List[PlatformSlug] = ["xhs", "douyin", "bilibili", "zhihu"]

# Mapping from aggregate-search slug → MediaCrawler core slug.
# The core uses "dy" for Douyin and "bili" for Bilibili.
_AGG_TO_CORE: Dict[str, str] = {
    "xhs": "xhs",
    "douyin": "dy",
    "bilibili": "bili",
    "zhihu": "zhihu",
}

# Reverse mapping: core slug → aggregate slug.
_CORE_TO_AGG: Dict[str, str] = {v: k for k, v in _AGG_TO_CORE.items()}


def agg_to_core_platform(agg_slug: str) -> str:
    """Convert an aggregate-search platform slug to a MediaCrawler core slug.

    >>> agg_to_core_platform("douyin")
    'dy'
    >>> agg_to_core_platform("bilibili")
    'bili'
    >>> agg_to_core_platform("xhs")
    'xhs'
    """
    return _AGG_TO_CORE.get(agg_slug, agg_slug)


def core_to_agg_platform(core_slug: str) -> str:
    """Convert a MediaCrawler core slug back to an aggregate-search slug.

    >>> core_to_agg_platform("dy")
    'douyin'
    >>> core_to_agg_platform("bili")
    'bilibili'
    """
    return _CORE_TO_AGG.get(core_slug, core_slug)


def is_valid_platform(platform: str) -> bool:
    """Check if the given platform slug is valid for aggregate search."""
    return platform in _AGG_TO_CORE

PlatformStatus = Literal[
    "pending",
    "running",
    "succeeded",
    "partial",
    "empty",
    "login_required",
    "rate_limited",
    "timed_out",
    "failed",
    "cancelled",
]

PLATFORM_STATUSES = frozenset(get_args(PlatformStatus))

# 平台异常自带的 safe_code 是"平台私有错误码"，不是 PlatformStatus。
# 直接透传会让 API 响应在 pydantic 的 Literal 校验处抛错（接口 500），
# 也会让 overall 永远停在 running，所以统一在这里做一次映射。
_SAFE_CODE_STATUS: Dict[str, str] = {
    "session_expired": "login_required",
    "login_required": "login_required",
    "unauthorized": "login_required",
    "rate_limited": "rate_limited",
    "restricted": "rate_limited",
    "timed_out": "timed_out",
    "malformed_response": "failed",
    "browser_unavailable": "failed",
    "failed": "failed",
}


def platform_error_status(value: str) -> str:
    """把平台错误码收敛到 PlatformStatus；未知值按 failed 处理。"""
    if not isinstance(value, str) or not value:
        return "failed"
    mapped = _SAFE_CODE_STATUS.get(value)
    if mapped is not None:
        return mapped
    if value in PLATFORM_STATUSES and value not in ("pending", "running", "succeeded", "empty", "partial"):
        return value
    return "failed"


def coerce_platform_status(value: str) -> str:
    """状态字段的兜底：合法状态原样返回，其余按错误码映射。"""
    if isinstance(value, str) and value in PLATFORM_STATUSES:
        return value
    return platform_error_status(value)


OverallStatus = Literal["running", "completed", "partial", "failed", "cancelling", "cancelled"]


# ── Unified search result ──────────────────────────────────────────────

class ZhihuQuestion(BaseModel):
    """Public question metadata, separate from the matched answer's counters."""

    id: str = Field(pattern=r"^[0-9]{1,30}$")
    title: str
    url: str
    metrics: Dict[str, int] = Field(default_factory=dict)


class GroupedSource(BaseModel):
    """Public fields for one platform version of a grouped result."""

    platform: PlatformSlug
    content_id: str
    content_type: str = "note"
    title: str
    url: str
    author: Optional[str] = None
    published_at: Optional[str] = None
    snippet: Optional[str] = None
    metrics: Dict[str, int] = Field(default_factory=dict)
    duration_seconds: Optional[int] = Field(default=None, gt=0)
    cover_url: Optional[str] = None
    # Kept for stable reconstruction of a single-platform tab.  It is the
    # existing platform rank, not a new grouping score.
    rank: int = 0
    question: Optional[ZhihuQuestion] = None

    @classmethod
    def from_result(cls, result: "UnifiedSearchResult") -> "GroupedSource":
        return cls(
            platform=result.platform,
            content_id=result.content_id,
            content_type=result.content_type,
            title=result.title,
            url=result.url,
            author=result.author,
            published_at=result.published_at,
            snippet=result.snippet,
            metrics=dict(result.metrics),
            cover_url=result.cover_url,
            duration_seconds=result.duration_seconds,
            rank=result.rank,
            question=result.question,
        )

class UnifiedSearchResult(BaseModel):
    """Normalized result from any supported platform."""

    platform: PlatformSlug
    content_id: str
    content_type: str = "note"  # "note", "video", "answer", "article", "zvideo"
    title: str
    snippet: Optional[str] = None  # cleaned description/excerpt, if available
    author: Optional[str] = None  # public display name only
    url: str
    published_at: Optional[str] = None  # ISO 8601
    duration_seconds: Optional[int] = Field(default=None, gt=0)
    cover_url: Optional[str] = None
    metrics: Dict[str, int] = Field(default_factory=dict)
    rank: int = 0  # original platform rank (0-based)
    grouped_sources: Optional[List[GroupedSource]] = None
    question: Optional[ZhihuQuestion] = None
    # Remote favourites may belong to one or more platform folders. Search
    # results leave this empty; it is public display metadata only.
    collection_names: List[str] = Field(default_factory=list)
    metrics_status: Optional[Literal["pending", "complete", "partial", "unavailable", "failed"]] = None
    metrics_updated_at: Optional[float] = None
    metrics_approximate: List[str] = Field(default_factory=list)

    # Allow extra fields from adapters for internal use
    model_config = {"extra": "ignore"}


# ── Platform-specific search results (aggregate layer internal) ────────

class PlatformResult(BaseModel):
    """Container for one platform's search outcome."""

    platform: PlatformSlug
    status: PlatformStatus
    results: List[UnifiedSearchResult] = Field(default_factory=list)
    error_summary: Optional[str] = None  # safe, no stack traces or secrets


# ── Job-level models ───────────────────────────────────────────────────

class SearchJobRequest(BaseModel):
    keyword: str = Field(..., min_length=1, description="Search keyword")
    platforms: List[PlatformSlug] = Field(
        default_factory=lambda: PLATFORM_SLUGS.copy(),
        description="Platforms to search",
    )
    limit_per_platform: int = Field(default=20, ge=1, le=40)
    # Round 15: 按平台独立数量。优先于 limit_per_platform；缺失平台回退
    # limit_per_platform（默认 20）。值必须是 1–40 的严格整数。
    platform_limits: Optional[Dict[str, Any]] = Field(default=None)

    @field_validator("platform_limits")
    @classmethod
    def platform_limits_valid(cls, v: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        if v is None:
            return v
        if not isinstance(v, dict):
            raise ValueError("platform_limits 必须是对象")
        for key, val in v.items():
            if key not in PLATFORM_SLUGS:
                raise ValueError(f"未知平台: {key}")
            if isinstance(val, bool) or not isinstance(val, int):
                raise ValueError(f"{key} 的数量必须是 1–40 的整数")
            if val < 1 or val > 40:
                raise ValueError(f"{key} 的数量必须在 1–40 之间")
        return v


class SearchJobStatus(BaseModel):
    job_id: str
    overall: OverallStatus
    keyword: str
    created_at: str
    completed_at: Optional[str] = None
    platforms: Dict[str, PlatformResult] = Field(default_factory=dict)


# ── Worker protocol models ─────────────────────────────────────────────

class WorkerRequest(BaseModel):
    """Request sent from parent process to worker via stdin."""

    # Round 16.2: 校验错误不回显输入值（model_validate 路径）—— 否则
    # ValidationError 会打印 session_snapshot 的原始输入（可能含 Cookie）。
    model_config = ConfigDict(hide_input_in_errors=True)

    job_id: str
    mode: Literal["search", "login", "favorites"]
    sync_mode: Optional[Literal["auto", "full", "reset"]] = None
    platform: PlatformSlug
    keyword: str = ""
    limit: int = 10
    # Round 16: 内存会话快照（cookie name→value，仅经 stdin 传输，绝不
    # 进 argv/env/日志；后端重启后为 None，worker 自动回退浏览器路径）。
    # Round 16.1/16.2: repr=False —— 默认 repr 省略该字段；安全 __repr__/
    # __str__ 显式用 <redacted> 占位。model_dump_json 仍保留（唯一 stdin
    # 传输序列化）。__init__ 预检把类型错误变成普通 ValueError，避免
    # pydantic ValidationError 回显 input_value（其中可能含 Cookie）。
    session_snapshot: Optional[Dict[str, str]] = Field(default=None, repr=False)
    # Round 16: 允许无浏览器快速路径（worker 内部仍会安全回退）。
    fast_path: bool = False
    # Round 16: 用户主动重新搜索时绕过结果缓存（默认 False）。
    bypass_cache: bool = False
    pagination: Optional[Dict[str, Any]] = Field(default=None, repr=False)
    seen_ids: List[str] = Field(default_factory=list, max_length=100, repr=False)

    def __init__(self, **data):
        snap = data.get("session_snapshot")
        if snap is not None and not isinstance(snap, dict):
            # 不回显输入值（可能含 Cookie/快照内容）。
            raise ValueError("session_snapshot must be a dict or None")
        if isinstance(snap, dict):
            for k, v in snap.items():
                if not isinstance(k, str) or not isinstance(v, str):
                    raise ValueError("session_snapshot must map str to str")
        super().__init__(**data)

    def __repr__(self) -> str:
        """安全 repr：会话快照用 <redacted> 占位，绝不打印 Cookie 值。"""
        return (
            f"WorkerRequest(job_id={self.job_id!r}, mode={self.mode!r}, "
            f"platform={self.platform!r}, keyword={self.keyword!r}, "
            f"limit={self.limit!r}, session_snapshot=<redacted>, "
            f"fast_path={self.fast_path!r}, bypass_cache={self.bypass_cache!r})"
        )

    def __str__(self) -> str:
        """str 与 repr 一致（%s 日志路径同样安全）。"""
        return self.__repr__()


class WorkerEvent(BaseModel):
    """Event emitted by worker on stdout (NDJSON with prefix)."""

    event: Literal["status", "result", "done", "error", "metrics", "reading_body"]
    job_id: str
    platform: PlatformSlug
    data: Any = None


# ── De-duplication helpers ─────────────────────────────────────────────

def make_dedup_key(platform: str, content_id: str) -> str:
    """Composite key for de-duplication across pages or platforms."""
    return f"{platform}:{content_id}"


# ── Snippet cleaning ──────────────────────────────────────────────────

_SNIPPET_TAG_RE = re.compile(r"<[^>]*>")
_SNIPPET_SCRIPT_RE = re.compile(
    r"<\s*(script|style)[^>]*>.*?<\s*/\s*\1\s*>",
    flags=re.IGNORECASE | re.DOTALL,
)
SNIPPET_MAX_LENGTH = 180


def _clean_html_text(value: Any, tag_replacement: str = " ") -> str:
    if value is None:
        return ""
    text = value if isinstance(value, str) else str(value)
    text = html.unescape(text)
    text = _SNIPPET_SCRIPT_RE.sub(tag_replacement, text)
    text = _SNIPPET_TAG_RE.sub(tag_replacement, text)
    return re.sub(r"[\s\u200b\u200c\u200d\ufeff]+", " ", text).strip()


def clean_snippet(value: Any, max_length: int = SNIPPET_MAX_LENGTH) -> Optional[str]:
    """Clean a platform description/excerpt for a compact search snippet.

    This deliberately performs no summarisation: it only decodes entities,
    removes obvious HTML, collapses whitespace, and applies a length cap.
    """
    if value is None:
        return None
    text = _clean_html_text(value)
    if not text:
        return None
    if max_length < 1:
        return None
    if len(text) <= max_length:
        return text
    return text[: max_length - 1].rstrip() + "…"


def clean_title(value: Any) -> str:
    """Clean a search title without truncating it."""
    return _clean_html_text(value, tag_replacement="")


# ── Cross-platform de-duplication V1 ──────────────────────────────────

CROSS_PLATFORM_DEDUP_MIN_TITLE_LENGTH = 6
CROSS_PLATFORM_DEDUP_MIN_EXACT_TITLE_LENGTH = 4
CROSS_PLATFORM_DEDUP_FUZZY_THRESHOLD = 0.90
CROSS_PLATFORM_DEDUP_MIN_SNIPPET_LENGTH = 20
#: 长度比下界（两条标题里短/长）。低于它的配对相似度天花板也到不了阈值，
#: 直接跳过 Levenshtein。取值与 `core_title_contained` 分支的 0.65 对齐。
CROSS_PLATFORM_DEDUP_MIN_LENGTH_RATIO = 0.65
#: 作者相同时的标题相似度门槛（比模糊阈值低，预筛必须按更低的下界来）。
CROSS_PLATFORM_DEDUP_MIN_TITLE_SIMILARITY = 0.86
_DEDUP_TITLE_SUFFIX_RE = re.compile(
    r"(?:附(?:完整)?(?:文档|资料|教程)|完整(?:版|文档)|完整版)$"
)


def normalize_dedup_text(value: Any) -> str:
    """Normalize public text for conservative cross-platform comparison."""
    if value is None:
        return ""
    # Adapters decode HTML entities before constructing the public DTO.
    # Match the frontend's Unicode letter/number normalization exactly.
    text = str(value)
    text = unicodedata.normalize("NFKC", text).lower()
    text = re.sub(r"<[^>]*>", "", text)
    return "".join(char for char in text if unicodedata.category(char)[0] in "LN")


def _normalize_dedup_title(value: Any) -> str:
    """Normalize a title and remove a small class of trailing add-ons."""
    normalized = normalize_dedup_text(value)
    return _DEDUP_TITLE_SUFFIX_RE.sub("", normalized)


def _same_dedup_author(left: Any, right: Any) -> bool:
    left_author = normalize_dedup_text(left)
    right_author = normalize_dedup_text(right)
    if not left_author or not right_author:
        return False
    if left_author == right_author:
        return True
    shorter, longer = sorted((left_author, right_author), key=len)
    # 例如“秋芝”和“秋芝2046”；只接受明确的四位数字后缀。
    return (
        len(shorter) >= 2
        and longer.startswith(shorter)
        and bool(re.fullmatch(r"[0-9]{4}", longer[len(shorter):]))
    )


#: 相似度预筛：只看公共前后缀就能否定的配对，不进 Levenshtein。
#: 编辑距离 d 满足 `d >= max_len - (公共前缀长 + 公共后缀长)`；
#: 相似度 = 1 - d/max_len，因此前缀+后缀长度之和够不到阈值就直接返回 0。
def _prefix_suffix_reject(left: str, right: str, threshold: float) -> bool:
    if not left or not right:
        return True
    max_len = max(len(left), len(right))
    need = max_len * threshold
    # 前缀
    limit = min(len(left), len(right))
    head = 0
    while head < limit and left[head] == right[head]:
        head += 1
    if head >= need:
        return False  # 仅前缀就已达标，仍需精确计算（这里只做否定判断）
    # 后缀（不与前缀重叠的部分）
    tail = 0
    while tail < limit - head and left[-1 - tail] == right[-1 - tail]:
        tail += 1
    return head + tail < need


def _title_similarity(left: str, right: str, threshold: float = 0.0) -> float:
    """Normalized Levenshtein distance over Unicode code points, as in the UI.

    `threshold > 0` 时先做公共前后缀预筛：够不到阈值直接返回 0，跳过 O(L²)
    的动态规划。去重路径下绝大多数配对都死在这一步。
    """
    if not left or not right:
        return 0.0
    if threshold > 0 and _prefix_suffix_reject(left, right, threshold):
        return 0.0
    previous = list(range(len(right) + 1))
    for i, left_char in enumerate(left, 1):
        current = [i]
        for j, right_char in enumerate(right, 1):
            current.append(previous[j - 1] if left_char == right_char else
                           1 + min(previous[j - 1], previous[j], current[j - 1]))
        previous = current
    return 1 - previous[-1] / max(len(left), len(right))


#: 摘要相似度阈值（比标题更严格：摘要更长，误合并代价更高）。
CROSS_PLATFORM_DEDUP_SNIPPET_SIMILARITY = 0.88


def _similar_dedup_snippets(
    left: UnifiedSearchResult, right: UnifiedSearchResult,
    left_title: str, right_title: str,
    left_snippet: Optional[str] = None,
    right_snippet: Optional[str] = None,
) -> bool:
    """两条摘要是否指向同一段文字。

    `left_snippet` / `right_snippet` 允许调用方传入已归一化的值（主循环里
    每条只算一次）。摘要有 180 字上限，Levenshtein 是 O(L²)，所以这里的
    长度预筛比标题那步更值钱：长度比不足阈值的一对直接跳过。
    """
    if left_snippet is None:
        left_snippet = normalize_dedup_text(left.snippet)
    if right_snippet is None:
        right_snippet = normalize_dedup_text(right.snippet)
    if min(len(left_snippet), len(right_snippet)) < CROSS_PLATFORM_DEDUP_MIN_SNIPPET_LENGTH:
        return False
    # 摘要完全相同是最常见的情况（同一篇内容被两个平台返回），直接判定，
    # 不进 O(L²) 的动态规划。
    if left_snippet == right_snippet:
        return left_snippet != left_title and right_snippet != right_title
    # 相似度天花板 = 短/长；够不到阈值就不必算距离。
    if not _is_plausible_pair(
        len(left_snippet), len(right_snippet), CROSS_PLATFORM_DEDUP_SNIPPET_SIMILARITY
    ):
        return False
    return (
        left_snippet != left_title
        and right_snippet != right_title
        and _title_similarity(
            left_snippet, right_snippet, CROSS_PLATFORM_DEDUP_SNIPPET_SIMILARITY
        ) >= CROSS_PLATFORM_DEDUP_SNIPPET_SIMILARITY
    )


def _is_plausible_pair(shorter: int, longer: int, threshold: float) -> bool:
    """长度预筛：相似度天花板够不到阈值的一对，连 Levenshtein 都不必算。

    归一化 Levenshtein 相似度 = `1 - distance/max_len`，而 `distance >= |len差|`，
    所以相似度上限是 `min_len / max_len`。用这个上界先否掉绝大多数配对，
    是把 `deduplicate_cross_platform_results` 从 O(n²·L²) 压下来的关键一步。

    `threshold` 传该分支真正要求的相似度；标题分支还允许 `core_title_contained`
    这条更低的路（长度比 ≥ 0.65），所以调用方传 0.65 而不是 0.9。
    """
    if longer <= 0:
        return False
    return shorter / longer >= min(threshold, CROSS_PLATFORM_DEDUP_MIN_LENGTH_RATIO)


def _is_plausible_title_pair(left_title: str, right_title: str) -> bool:
    """标题预筛：0.65 的下界同时覆盖模糊阈值与包含关系两条分支。"""
    return _is_plausible_pair(
        *sorted((len(left_title), len(right_title))),
        CROSS_PLATFORM_DEDUP_MIN_LENGTH_RATIO,
    )


def _same_cross_platform_content(
    left: UnifiedSearchResult,
    right: UnifiedSearchResult,
    left_title: Optional[str] = None,
    right_title: Optional[str] = None,
    left_snippet: Optional[str] = None,
    right_snippet: Optional[str] = None,
) -> bool:
    """跨平台同内容判定。

    `left_title` / `right_title` 允许调用方预先归一化后传入 —— 去重主循环里
    每条结果只归一化一次，而不是每两两配对都重算一遍（原来 80 条要做
    10 万+ 次 NFKC + 正则）。
    """
    if left.platform == right.platform:
        return False

    if left_title is None:
        left_title = _normalize_dedup_title(left.title)
    if right_title is None:
        right_title = _normalize_dedup_title(right.title)
    if min(len(left_title), len(right_title)) < CROSS_PLATFORM_DEDUP_MIN_EXACT_TITLE_LENGTH:
        return False
    if not _is_plausible_title_pair(left_title, right_title):
        return False

    same_author = _same_dedup_author(left.author, right.author)
    # A common title or nearby publication dates alone do not identify content.
    if left_title == right_title:
        return same_author or _similar_dedup_snippets(
            left, right, left_title, right_title, left_snippet, right_snippet)

    if min(len(left_title), len(right_title)) < CROSS_PLATFORM_DEDUP_MIN_TITLE_LENGTH:
        return False

    shorter_title, longer_title = sorted((left_title, right_title), key=len)
    core_title_contained = (
        len(shorter_title) >= CROSS_PLATFORM_DEDUP_MIN_TITLE_LENGTH
        and len(shorter_title) / len(longer_title) >= 0.65
        and shorter_title in longer_title
    )
    # 0.86 是 `same_author` 分支的门槛，比模糊阈值低；预筛按更低的那个来，
    # 保证不会把 `same_author and title_similarity >= 0.86` 的情况误否。
    title_similarity = _title_similarity(
        left_title, right_title, CROSS_PLATFORM_DEDUP_MIN_TITLE_SIMILARITY
    )
    if same_author and (core_title_contained or title_similarity >= 0.86):
        return True

    # Fuzzy titles also require an independent author or substantial snippet.
    if title_similarity < CROSS_PLATFORM_DEDUP_FUZZY_THRESHOLD:
        return False

    return same_author or _similar_dedup_snippets(
        left, right, left_title, right_title, left_snippet, right_snippet)


def _result_completeness_score(result: UnifiedSearchResult) -> float:
    """Prefer a representative with more useful fields, not raw metrics."""
    score = min(len(normalize_dedup_text(result.title)), 40) / 40
    score += 3 if normalize_dedup_text(result.snippet) else 0
    score += 2 if normalize_dedup_text(result.author) else 0
    score += 1 if result.published_at else 0
    score += 1 if result.cover_url else 0
    score += min(sum(1 for value in result.metrics.values() if value > 0), 4) * 0.25
    return score


def _has_common_title_anchor(
    indexes: List[int],
    results: List[UnifiedSearchResult],
) -> bool:
    if len(indexes) <= 2:
        return True
    titles = [_normalize_dedup_title(results[index].title) for index in indexes]
    if len(set(titles)) == 1:
        return True
    for anchor in titles:
        if len(anchor) < CROSS_PLATFORM_DEDUP_MIN_TITLE_LENGTH:
            continue
        if all(
            candidate == anchor
            or (
                len(anchor) / len(candidate) >= 0.65
                and anchor in candidate
            )
            for candidate in titles
        ):
            return True
    return False


def deduplicate_cross_platform_results(
    results: List[UnifiedSearchResult],
    platform_order: Optional[List[str]] = None,
) -> List[UnifiedSearchResult]:
    """Keep one representative for conservative cross-platform duplicates.

    Existing same-platform ``platform + content_id`` de-duplication remains
    in ``interleave_results``. This function only compares different
    platforms, so an equal ID with unrelated titles is retained.
    """
    if len(results) <= 1:
        return list(results)

    if platform_order is None:
        platform_order = PLATFORM_SLUGS
    platform_priority = {platform: index for index, platform in enumerate(platform_order)}
    parent = list(range(len(results)))

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def union(left: int, right: int) -> None:
        left_root, right_root = find(left), find(right)
        if left_root != right_root:
            parent[right_root] = left_root

    def component_members(root: int) -> List[int]:
        return [index for index in range(len(results)) if find(index) == root]

    # 每条结果只归一化一次：原来每两两配对都要重算 NFKC + 正则 + 后缀剥离，
    # 80 条时一次去重会执行 10 万+ 次。归一化结果是纯函数产物，可以安全复用。
    normalized_titles = [_normalize_dedup_title(result.title) for result in results]
    normalized_snippets = [normalize_dedup_text(result.snippet) for result in results]

    for left_index in range(len(results)):
        for right_index in range(left_index + 1, len(results)):
            if not _same_cross_platform_content(
                results[left_index], results[right_index],
                normalized_titles[left_index], normalized_titles[right_index],
                normalized_snippets[left_index], normalized_snippets[right_index],
            ):
                continue
            left_root = find(left_index)
            right_root = find(right_index)
            if left_root == right_root:
                continue
            merged = component_members(left_root) + component_members(right_root)
            if len({results[index].platform for index in merged}) != len(merged):
                continue  # Cross-platform bridges must not merge distinct IDs on one platform.
            # 两条结果保持原有 predicate 语义；三条以上还要共享一个标题核心，
            # 防止 A~B、B~C 的弱链路把明显不同的 C 传递合并进来。
            if _has_common_title_anchor(merged, results):
                union(left_index, right_index)

    groups: Dict[int, List[int]] = {}
    for index in range(len(results)):
        groups.setdefault(find(index), []).append(index)

    representatives: List[tuple[int, int]] = []
    for indexes in groups.values():
        winner = max(
            indexes,
            key=lambda index: (
                _result_completeness_score(results[index]),
                -results[index].rank,
                -platform_priority.get(results[index].platform, 10_000),
                -index,
            ),
        )
        representatives.append((min(indexes), winner))

    representatives.sort(key=lambda pair: pair[0])
    grouped_results: List[UnifiedSearchResult] = []
    for _, winner in representatives:
        indexes = next(
            members for members in groups.values() if winner in members
        )
        representative = results[winner]
        if len(indexes) <= 1:
            grouped_results.append(representative)
            continue

        other_indexes = sorted(
            (index for index in indexes if index != winner),
            key=lambda index: (
                results[index].rank,
                platform_priority.get(results[index].platform, 10_000),
                index,
            ),
        )
        source_indexes = [winner, *other_indexes]
        # Keep the same representative object from platform_results so later
        # hydration updates and cache writes observe the grouped card too.
        representative.grouped_sources = [
            GroupedSource.from_result(results[index])
            for index in source_indexes
        ]
        grouped_results.append(representative)
    return grouped_results


# ── Interleaved merge ──────────────────────────────────────────────────

def interleave_results(
    platform_results: Dict[str, List[UnifiedSearchResult]],
    platform_order: Optional[List[str]] = None,
) -> List[UnifiedSearchResult]:
    """Round-robin interleave results from multiple platforms.

    Picks one result from each platform in order until all exhausted.
    Maintains each platform's internal rank order.
    """
    if platform_order is None:
        platform_order = PLATFORM_SLUGS

    queues: Dict[str, List[UnifiedSearchResult]] = {
        p: list(platform_results.get(p, [])) for p in platform_order
    }

    merged: List[UnifiedSearchResult] = []
    seen: set = set()
    changed = True
    while changed:
        changed = False
        for p in platform_order:
            while queues.get(p):
                item = queues[p].pop(0)
                key = make_dedup_key(item.platform, item.content_id)
                if key not in seen:
                    seen.add(key)
                    merged.append(item)
                    changed = True
                    break
    return deduplicate_cross_platform_results(merged, platform_order=platform_order)


# ── Time parsing ───────────────────────────────────────────────────────

def _parse_timestamp(value: Any) -> Optional[str]:
    """Convert a platform timestamp (int seconds, int ms, or ISO string)
    to an ISO 8601 string, or return None."""
    if value is None:
        return None
    if isinstance(value, str):
        # Try parsing as ISO
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).isoformat()
        except (ValueError, TypeError):
            pass
        # Try parsing as int
        try:
            value = int(value)
        except (ValueError, TypeError):
            return None
    if isinstance(value, (int, float)):
        if value <= 0:
            return None
        # Heuristic: if < 10000000000 assume seconds; else ms
        if value < 10_000_000_000:
            ts = value
        else:
            ts = value / 1000.0
        try:
            return datetime.fromtimestamp(ts, tz=None).isoformat()
        except (OSError, ValueError, OverflowError):
            return None
    return None
