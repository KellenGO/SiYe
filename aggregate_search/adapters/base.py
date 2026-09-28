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
Base adapter for platform search result normalization.

Each platform adapter receives *native* data from the crawler client
(a dict, list, or model object) and converts it to a list of
``UnifiedSearchResult`` objects.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
import math
from typing import Any, Dict, List, Optional

from aggregate_search.models import UnifiedSearchResult


class BasePlatformAdapter(ABC):
    """Abstract base for per-platform result normalization."""

    PLATFORM: str  # Must be set by subclasses

    @abstractmethod
    def adapt(self, raw_results: List[Any], keyword: str = "") -> List[UnifiedSearchResult]:
        """Convert native platform results into unified results.

        Args:
            raw_results: Platform-native search result items (list of dict or model).
            keyword: The search keyword (for context if needed).

        Returns:
            Normalized list of ``UnifiedSearchResult``.
        """
        ...

    @staticmethod
    def _safe_str(value: Any, default: str = "") -> str:
        """Coerce a value to string safely."""
        if value is None:
            return default
        return str(value)

    @staticmethod
    def _safe_int(value: Any, default: int = 0) -> int:
        """Coerce a value to int safely."""
        if value is None:
            return default
        try:
            return int(value)
        except (ValueError, TypeError):
            return default

    @staticmethod
    def _numeric_duration_seconds(value: Any, *, milliseconds: bool = False) -> Optional[int]:
        """Normalize a known numeric duration; units come from the source field."""
        if isinstance(value, bool) or not isinstance(value, (int, float, str)):
            return None
        try:
            seconds = float(value) / (1000 if milliseconds else 1)
        except (ValueError, OverflowError):
            return None
        if not math.isfinite(seconds) or not 0 < seconds <= 2**53 - 1:
            return None
        # Display whole seconds, retaining a positive sub-second video as 00:01.
        return max(1, int(seconds))

    def _extract_cover_url(self, raw_item: Dict) -> Optional[str]:
        """Extract cover image URL with platform-specific fallbacks."""
        return None

    def _extract_metrics(self, raw_item: Dict) -> Dict[str, int]:
        """Extract interaction metrics from platform-native item."""
        return {}

    def _build_url(self, raw_item: Dict) -> str:
        """Build the public-facing URL for the content."""
        return ""
