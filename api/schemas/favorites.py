from __future__ import annotations

from datetime import datetime
from typing import Dict, List, Optional, Literal, Any

from pydantic import BaseModel, Field, field_validator

from aggregate_search.models import PLATFORM_SLUGS, PlatformSlug, PlatformStatus, UnifiedSearchResult


class FavoritesJobRequest(BaseModel):
    sync_mode: Optional[Literal["auto", "full"]] = None
    platforms: List[PlatformSlug] = Field(default_factory=lambda: PLATFORM_SLUGS.copy(), min_length=1)
    # 每个平台的目标总量（不是单次请求量）：worker 会按平台允许的分页方式逐页读取。
    # 默认保持 20 保守取值，前端「同步收藏」显式传 100。
    limit_per_platform: int = Field(default=20, ge=1, le=100)

    @field_validator("platforms")
    @classmethod
    def unique_platforms(cls, value: List[PlatformSlug]) -> List[PlatformSlug]:
        if len(value) != len(set(value)):
            raise ValueError("平台不能重复")
        return value


class FavoritePlatformInfo(BaseModel):
    status: PlatformStatus = "pending"
    result_count: int = 0
    error_summary: Optional[str] = None
    synced_at: Optional[datetime] = None
    phase: Optional[str] = None
    account: Optional[str] = None


class FavoritesJobResponse(BaseModel):
    job_id: str
    overall: str
    created_at: datetime
    completed_at: Optional[datetime] = None
    platforms: Dict[str, FavoritePlatformInfo]
    results: List[UnifiedSearchResult]
    persistence_error: Optional[str] = None
    counts: Dict[str, int] = Field(default_factory=dict)
    accounts: List[Dict[str, Any]] = Field(default_factory=list)
    pending_count: int = 0
    # 归档各类状态的条数（present / pending / archived / legacy），界面用它做汇总，
    # 不必把「账号未确认」这类历史数据逐条铺出来。
    states: Dict[str, int] = Field(default_factory=dict)
    data_version: str = ""


