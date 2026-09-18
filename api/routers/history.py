"""观看历史 API（/api/history）。

设计要点：
- 历史只在本机 SQLite 落库，不上传；
- 记录请求失败不应阻断前端跳转（前端 fire-and-forget，见 webui/src/lib/historyApi.ts）；
- 重复观看只更新 last_viewed_at 与 view_count，不新增行；
- 路由定义为同步函数，由 FastAPI 放进线程池，避免 SQLite 同步 I/O 阻塞事件循环。
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from ..schemas.history import HistoryViewInput
from ..services.watch_history_store import ViewHistoryStore, get_history_store

history_router = APIRouter(prefix="/api/history", tags=["history"])


def _bad_request(error: ValueError) -> HTTPException:
    return HTTPException(status_code=400, detail=str(error))


@history_router.post("/views", status_code=201)
def record_view(
    payload: HistoryViewInput,
    store: ViewHistoryStore = Depends(get_history_store),
) -> Dict[str, Any]:
    """记录一次观看（重复观看只更新最后浏览时间与次数）。"""
    try:
        return store.record_view(payload.result)
    except ValueError as error:
        raise _bad_request(error)


@history_router.get("/views")
def list_views(
    limit: Optional[int] = Query(default=None, ge=1, le=2000),
    offset: int = Query(default=0, ge=0),
    store: ViewHistoryStore = Depends(get_history_store),
) -> Dict[str, Any]:
    """列出观看历史，按最近浏览倒序。"""
    return store.list_views(limit=limit, offset=offset)


@history_router.delete("/views")
def clear_views(
    store: ViewHistoryStore = Depends(get_history_store),
) -> Dict[str, Any]:
    """清空全部观看历史。"""
    return {"removed": store.clear()}


@history_router.delete("/views/{platform}/{content_id}")
def delete_view(
    platform: str,
    content_id: str,
    store: ViewHistoryStore = Depends(get_history_store),
) -> Dict[str, Any]:
    """删除单条观看历史。"""
    return {"removed": store.delete_view(platform, content_id)}
