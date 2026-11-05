# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
# This file is part of MediaCrawler project.
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

"""热搜词的响应模型。

只描述"词"这一层：平台榜位 + 词 + 热度。内容交给既有的聚合搜索去取，
所以这里没有标题、作者、封面这类字段。
"""

from __future__ import annotations

from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, Field

from aggregate_search.models import PlatformSlug

TrendingStatus = Literal["ok", "failed"]


class TrendingWord(BaseModel):
    """一条热搜词。"""

    rank: int = Field(ge=1)
    word: str = Field(min_length=1, max_length=200)
    hot_value: Optional[int] = Field(default=None, ge=0)


class PlatformTrending(BaseModel):
    """单个平台的热搜结果。

    ``status`` 的语义：
    - ``ok``：拿到了词；
    - ``failed``：这次取不到，``message`` 是给用户看的安全文案。

    没有小红书的位置：它没有可用的全站热榜，不返回空占位。
    """

    status: TrendingStatus
    words: List[TrendingWord] = Field(default_factory=list)
    active_time: Optional[str] = None
    message: Optional[str] = None


class TrendingResponse(BaseModel):
    """按平台分组的热搜；聚合视图由前端自行决定要不要做。"""

    platforms: Dict[PlatformSlug, PlatformTrending] = Field(default_factory=dict)
    fetched_at: Optional[str] = None
    cached: bool = False
