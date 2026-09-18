"""观看历史请求模型。

历史记录的是一条被点开看过的搜索结果（与前端 UnifiedSearchResult 对齐），
后端只按白名单字段持久化，避免把内部/嵌套字段写进库。
"""

from __future__ import annotations

from typing import Any, Dict

from pydantic import BaseModel


class HistoryViewInput(BaseModel):
    """一条被观看的结果。"""

    result: Dict[str, Any]
