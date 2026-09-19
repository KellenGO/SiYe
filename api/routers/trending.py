# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
# This file is part of MediaCrawler project.
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

"""热搜词 API（/api/trending）。

设计要点：
- 只返回"词"，不返回内容；内容由既有的聚合搜索去取。
- 按平台分组返回，聚合视图交给前端决定（聚合不可逆，分平台数据随时能合）。
- 公开接口不带登录态，因此这里既不读账号，也不影响搜索冷却。
- 单个平台失败只影响它自己，其它平台照常返回。
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, Query

from ..schemas.trending import TrendingResponse
from ..services import trending as trending_service

trending_router = APIRouter(prefix="/api/trending", tags=["trending"])


@trending_router.get("", response_model=TrendingResponse)
async def get_trending(
    platforms: Optional[str] = Query(default=None, description="逗号分隔的平台 slug，省略表示全部"),
    refresh: bool = Query(default=False, description="用户主动刷新时跳过缓存读取"),
) -> Dict[str, Any]:
    """按平台返回热搜词。"""
    wanted = [p.strip() for p in platforms.split(",") if p.strip()] if platforms else None
    return await trending_service.get_trending(wanted, bypass_cache=refresh)
