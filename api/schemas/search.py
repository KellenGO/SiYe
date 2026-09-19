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
Pydantic schemas for the aggregate search API.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field, field_validator

from aggregate_search.models import (
    PLATFORM_SLUGS,
    OverallStatus,
    PlatformSlug,
    PlatformStatus,
    UnifiedSearchResult,
)

MAX_LIMIT_PER_PLATFORM = 40
MIN_LIMIT_PER_PLATFORM = 1


class SearchJobRequestSchema(BaseModel):
    keyword: str = Field(..., min_length=1, max_length=200)
    platforms: List[PlatformSlug] = Field(
        default_factory=lambda: PLATFORM_SLUGS.copy(),
        min_length=1,
    )
    limit_per_platform: int = Field(default=20, ge=1, le=MAX_LIMIT_PER_PLATFORM)
    # Per-platform limits use Any so the validator can reject lax coercion,
    # 避免 pydantic lax 模式把 "5"/true 强转成 int 而绕过上限校验。
    # 优先于 limit_per_platform；缺失平台回退 limit_per_platform（默认 20）。
    platform_limits: Optional[Dict[str, Any]] = Field(default=None)
    # Explicit refreshes bypass the in-memory result cache (default: False).
    bypass_cache: bool = Field(default=False)
    continue_from: Optional[str] = Field(default=None, max_length=64)
    # 单平台重搜（搜索结果页"搜索范围"里的 ⟳）：
    # 把本次结果作为**当前批次里这些平台的替换**，而不是新增一批。
    # 语义差别很重要 —— 换批（continue_from 且不带本字段）是往后叠加新的批次。
    replace_platforms: bool = Field(default=False)

    @field_validator("platforms")
    @classmethod
    def platforms_not_empty(cls, v: List[str]) -> List[str]:
        if not v:
            raise ValueError("At least one platform is required.")
        return v

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
                raise ValueError(f"{key} 的数量必须是 1–{MAX_LIMIT_PER_PLATFORM} 的整数")
            if val < MIN_LIMIT_PER_PLATFORM or val > MAX_LIMIT_PER_PLATFORM:
                raise ValueError(f"{key} 的数量必须在 1–{MAX_LIMIT_PER_PLATFORM} 之间")
        return v


class PlatformTimingInfo(BaseModel):
    """平台搜索耗时指标（毫秒，perf_counter 单调时钟；无数据为 None）。

    All timing fields are cumulative milliseconds from the platform request;
    they are intentionally not interchangeable:
    - spawn_ms            parent 创建/取得 worker 的耗时（复用既有进程≈0）；
    - worker_ready_ms     进程启动→模块加载完成的固定常量（不随空闲增长）；
    - reused_worker       本次搜索是否复用了既有常驻 worker 进程；
    - browser_launch_ms   从请求开始到浏览器 context 启动完成（累计）；
    - navigation_ms       从请求开始到初始页面导航完成（累计）；
    - preflight_ms        从请求开始到登录预检完成（累计）；
    - search_api_ms       从请求开始到首次搜索 API 响应（累计）；
    - first_result_ms     从 job 开始到首条合法结果；
    - total_ms            平台进入终态的总耗时；
    - fast_path_used      是否命中无浏览器快速路径；
    - provider_used       实际完成搜索的安全 provider slug；
    - provider_attempts   本次串行尝试过的安全 provider slug；
    - fallback_active     是否发生过 provider 回退；
    - fallback_reason     回退原因安全枚举（无响应体）。

    只包含耗时数字与安全枚举，绝不包含 Cookie/URL/响应体等敏感信息。
    """

    spawn_ms: Optional[int] = None            # worker 子进程创建/取得耗时
    worker_ready_ms: Optional[int] = None     # 进程启动→就绪（固定常量）
    reused_worker: Optional[bool] = None      # 本次是否复用常驻 worker
    browser_launch_ms: Optional[int] = None   # 浏览器 context 启动（累计）
    navigation_ms: Optional[int] = None       # 初始页面导航（累计）
    preflight_ms: Optional[int] = None        # 登录预检（累计）
    search_api_ms: Optional[int] = None       # 首次搜索 API 响应（累计）
    first_result_ms: Optional[int] = None     # 从 job 开始到首条合法结果
    total_ms: Optional[int] = None            # 平台进入终态的总耗时
    fast_path_used: Optional[bool] = None     # 是否命中无浏览器快速路径
    provider_used: Optional[str] = None       # 实际完成搜索的安全 provider slug
    provider_attempt_count: Optional[int] = None
    provider_attempts: Optional[List[str]] = None
    fallback_active: Optional[bool] = None
    fallback_reason: Optional[str] = None     # 回退原因安全枚举（无响应体）
    page_requests: int = 0
    duplicate_count: int = 0


class PlatformStatusInfo(BaseModel):
    status: PlatformStatus = "pending"
    result_count: int = 0
    error_summary: Optional[str] = None
    timings: Optional[PlatformTimingInfo] = None
    cache_hit: bool = False
    fetched_at: Optional[str] = None  # Original collection time, not replay time.
    cooldown_until: Optional[str] = None  # UTC deadline for the local retry gate.
    cooldown_skipped: bool = False       # No worker request was made.


class PlatformDiagnostic(BaseModel):
    """Small, safe capability summary for the account settings page."""

    platform: PlatformSlug
    search_available: bool
    search_mode: Optional[Literal[
        "fast_path", "browser_fallback", "api", "page", "unavailable"
    ]] = None
    account_state: str
    # User-visible snippet capability is separate from detail hydration.
    snippet_available: Optional[bool] = None
    hydration_available: Optional[bool] = None
    fallback_active: bool = False
    limitation_code: Optional[str] = None
    user_message: Optional[str] = None
    recommended_action: Optional[str] = None
    checked_at: Optional[datetime] = None


class HealthPlatformStatus(BaseModel):
    """Safe per-platform summary for startup diagnostics."""

    account_state: str
    profile_exists: bool
    search_available: bool
    search_mode: Optional[Literal[
        "fast_path", "browser_fallback", "api", "page", "unavailable"
    ]] = None
    snippet_available: Optional[bool] = None


class HealthResponse(BaseModel):
    """Liveness plus local environment capabilities; never contains secrets."""

    # ``status`` remains the backwards-compatible API liveness signal.
    status: Literal["ok"] = "ok"
    environment_status: Literal["ok", "degraded"] = "ok"
    backend_available: bool = True
    version: str
    api_version: str
    web_version: Optional[str] = None
    version_match: Optional[bool] = None
    browser_available: bool
    browser_backend: Optional[str] = None
    redis_required: bool = False
    redis_available: Optional[bool] = None
    platforms: Dict[str, HealthPlatformStatus] = Field(default_factory=dict)


class SearchJobResponse(BaseModel):
    job_id: str
    overall: OverallStatus
    keyword: str
    created_at: str
    completed_at: Optional[str] = None
    total_ms: Optional[int] = None  # job 级总耗时（毫秒）
    platforms: Dict[str, PlatformStatusInfo] = Field(default_factory=dict)
    results: List[UnifiedSearchResult] = Field(default_factory=list)
    # Search is terminal before best-effort description hydration finishes.
    hydration_status: Literal["not_started", "running", "completed"] = "not_started"
    exploration: Optional[Dict[str, Any]] = None
