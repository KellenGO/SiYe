"""观看历史：把用户点开看过的搜索结果记进本机 SQLite。

与收藏库（``items`` 表）刻意分开：收藏页「全部」视图包含库里每一条内容，
写进收藏等于自动收藏——历史会污染收藏。所以这里在同库（``library.db``）里
新建独立的 ``views`` 表，而不是复用收藏表。

设计要点（取舍见 ``docs/decisions/``）：
- 同一条内容（platform, content_id）只存一份；再点只更新 last_viewed_at 与 view_count；
- 只保留最近 1000 条，写入时按 last_viewed_at 滚动淘汰最旧的；
- 数据只在本机库里，不上传；
- 记录请求失败失败也不该挡住用户跳转（前端 fire-and-forget，见 historyApi.recordView）。
"""

from __future__ import annotations

import sqlite3
import threading
from typing import Any, Dict, List, Optional

from urllib.parse import urlsplit

from .favorite_snapshot import decode_metrics, encode_metrics
from .sqlite_base import RESULT_FIELDS, SqliteStoreBase, default_db_path, utc_now

MAX_VIEWS = 1000

_VIEWS_SCHEMA = """
CREATE TABLE IF NOT EXISTS views (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    platform        TEXT NOT NULL,
    content_id      TEXT NOT NULL,
    content_type    TEXT NOT NULL DEFAULT '',
    title           TEXT NOT NULL DEFAULT '',
    snippet         TEXT,
    author          TEXT NOT NULL DEFAULT '',
    url             TEXT NOT NULL DEFAULT '',
    published_at    TEXT,
    cover_url       TEXT,
    metrics         TEXT NOT NULL DEFAULT '{}',
    first_viewed_at TEXT NOT NULL,
    last_viewed_at  TEXT NOT NULL,
    view_count      INTEGER NOT NULL DEFAULT 1,
    UNIQUE (platform, content_id)
);

CREATE INDEX IF NOT EXISTS idx_views_last_viewed_at
    ON views (last_viewed_at, id);
"""


class ViewHistoryStore(SqliteStoreBase):
    """观看历史存储。连接、事务见 `api/services/sqlite_base.py`（与收藏库同库）。"""

    _SCHEMA = _VIEWS_SCHEMA
    _FOREIGN_KEYS = False

    def _bootstrap(self, conn: sqlite3.Connection) -> None:
        super()._bootstrap(conn)
        self._enable_wal(conn)

    # ------------------------------------------------------ 行 <-> 结果

    @staticmethod
    def _split_result(result: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(result, dict):
            raise ValueError("观看历史内容格式无效")
        for field in ("platform", "content_id", "title", "url"):
            if not isinstance(result.get(field), str) or not result[field]:
                raise ValueError(f"观看历史缺少有效的 {field}")
        try:
            url = urlsplit(result["url"])
            if url.scheme not in ("http", "https") or not url.netloc:
                raise ValueError()
        except ValueError:
            raise ValueError("观看历史原文链接无效") from None
        for field in RESULT_FIELDS:
            if result.get(field) is not None and not isinstance(result[field], str):
                raise ValueError(f"观看历史 {field} 必须是文字")
        row: Dict[str, Any] = {field: result.get(field) for field in RESULT_FIELDS}
        row["metrics"] = encode_metrics(result)
        # 规范化必填字段，避免 None 写进 NOT NULL 列
        for field in ("platform", "content_id", "content_type", "title", "author", "url"):
            if row.get(field) is None:
                row[field] = ""
        return row

    @staticmethod
    def _row_to_view(row: sqlite3.Row) -> Dict[str, Any]:
        result: Dict[str, Any] = {
            "platform": row["platform"],
            "content_id": row["content_id"],
            "content_type": row["content_type"],
            "title": row["title"],
            "snippet": row["snippet"],
            "author": row["author"],
            "url": row["url"],
            "published_at": row["published_at"],
            "cover_url": row["cover_url"],
            **decode_metrics(row["metrics"]),
        }
        return {
            "id": row["id"],
            "key": f'{row["platform"]}|{row["content_id"]}',
            "result": result,
            "first_viewed_at": row["first_viewed_at"],
            "last_viewed_at": row["last_viewed_at"],
            "view_count": int(row["view_count"]),
        }

    # ----------------------------------------------------------------- 写入

    def record_view(self, result: Dict[str, Any]) -> Dict[str, Any]:
        """记录一次观看：首次插入、再次只更新最后浏览时间与次数（不重复插入）。"""
        platform = str(result.get("platform") or "")
        content_id = str(result.get("content_id") or "")
        if not platform or not content_id:
            raise ValueError("观看历史缺少 platform 或 content_id")

        row = self._split_result(result)
        now = utc_now()

        with self._conn(write=True) as conn:
            existing = conn.execute(
                "SELECT id, view_count FROM views WHERE platform = ? AND content_id = ?",
                (platform, content_id),
            ).fetchone()
            if existing is None:
                cursor = conn.execute(
                    """
                    INSERT INTO views (platform, content_id, content_type, title, snippet, author,
                                       url, published_at, cover_url, metrics, first_viewed_at, last_viewed_at, view_count)
                    VALUES (:platform, :content_id, :content_type, :title, :snippet, :author,
                            :url, :published_at, :cover_url, :metrics, :now, :now, 1)
                    """,
                    {**row, "now": now},
                )
                view_id = int(cursor.lastrowid)
            else:
                view_id = int(existing["id"])
                # 再次观看：刷新快照但保留首次浏览时间；计数 +1
                conn.execute(
                    """
                    UPDATE views SET content_type = :content_type, title = :title, snippet = :snippet,
                                     author = :author, url = :url, published_at = :published_at,
                                     cover_url = :cover_url, metrics = :metrics,
                                     last_viewed_at = :now, view_count = view_count + 1
                    WHERE id = :id
                    """,
                    {**row, "now": now, "id": view_id},
                )
            self._enforce_capacity(conn)

        view = self.get_view(platform, content_id)
        assert view is not None
        return view

    def _enforce_capacity(self, conn: sqlite3.Connection) -> None:
        """滚动淘汰：超过上限时删除最旧的（last_viewed_at 升序、id 升序）。"""
        total = conn.execute("SELECT COUNT(*) AS n FROM views").fetchone()["n"]
        if total > MAX_VIEWS:
            excess = total - MAX_VIEWS
            conn.execute(
                "DELETE FROM views WHERE id IN ("
                "SELECT id FROM views ORDER BY last_viewed_at ASC, id ASC LIMIT ?)",
                (excess,),
            )

    # ----------------------------------------------------------------- 读取

    def get_view(self, platform: str, content_id: str) -> Optional[Dict[str, Any]]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM views WHERE platform = ? AND content_id = ?",
                (platform, content_id),
            ).fetchone()
            if row is None:
                return None
            return self._row_to_view(row)

    def list_views(
        self,
        limit: Optional[int] = None,
        offset: int = 0,
    ) -> Dict[str, Any]:
        """列出观看历史，按最近浏览倒序。"""
        with self._conn() as conn:
            total = conn.execute("SELECT COUNT(*) AS n FROM views").fetchone()["n"]
            sql = "SELECT * FROM views ORDER BY last_viewed_at DESC, id DESC"
            params: List[Any] = []
            if limit is not None:
                sql += " LIMIT ? OFFSET ?"
                params = [limit, offset]
            rows = conn.execute(sql, params).fetchall()
            items = [self._row_to_view(row) for row in rows]
        return {"items": items, "total": int(total)}

    def delete_view(self, platform: str, content_id: str) -> int:
        """删除单条观看历史。"""
        with self._conn() as conn:
            cursor = conn.execute(
                "DELETE FROM views WHERE platform = ? AND content_id = ?",
                (platform, content_id),
            )
            return int(cursor.rowcount)

    def clear(self) -> int:
        """清空全部观看历史。"""
        with self._conn() as conn:
            cursor = conn.execute("DELETE FROM views")
            return int(cursor.rowcount)


_store: Optional[ViewHistoryStore] = None
_store_lock = threading.Lock()


def get_history_store() -> ViewHistoryStore:
    """首次真正用到时才建库（避免 import 阶段就往磁盘写文件）。"""
    global _store
    if _store is None:
        with _store_lock:
            if _store is None:
                _store = ViewHistoryStore()
    return _store


def reset_history_store() -> None:
    """Drop the cached instance（测试用）。"""
    global _store
    with _store_lock:
        _store = None
