"""跨平台收藏同步结果的本机持久化。

原来 job 结果只存在进程内存（见 favorites_job_manager.py 的注释），后端一重启就没了，
用户每次打开收藏页都得重新同步一次。这里把同步结果落到稳定的本机 SQLite（与收藏库共用
``%LOCALAPPDATA%/SiYe/data/library.db``），目标是：

- 打开页面直接显示上次保存的数据与同步时间，**不访问平台**；
- 只有用户点击同步才更新；
- **逐平台落库**：某个平台失败或限流，不影响其他平台已保存的数据；
- 按 (账号, 平台, 内容ID) 去重合并，更新已有条目的信息；
- **指标只合并、不倒退**：本次没取到的指标字段沿用本机已有的值，完整度只升不降；
- 本次没取到的旧内容**不删除**（只刷新取到条目的 last_seen_at）——
  "这次没出现"不等于"用户取消了收藏"；
- 不同账号分开保存（account_key），避免把两个人的收藏混在一起。
"""

from __future__ import annotations

import json
import sqlite3
import threading
from typing import Any, Dict, Iterator, List, Optional, Sequence

from .favorite_snapshot import decode_metrics, encode_metrics, merge_into_result
# DEFAULT_ACCOUNT_KEY 定义在 remote_sync_state 里（mixin 自己也要用），这里重新导出保持既有引用可用。
from .remote_sync_state import DEFAULT_ACCOUNT_KEY, RemoteSyncStateMixin, SYNC_SCHEMA
from .sqlite_base import (
    RESULT_FIELDS as _RESULT_FIELDS,
    SqliteStoreBase,
    utc_now,
)

_REMOTE_SCHEMA = """
CREATE TABLE IF NOT EXISTS remote_favorites (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    account_key      TEXT NOT NULL DEFAULT 'default',
    platform         TEXT NOT NULL,
    content_id       TEXT NOT NULL,
    content_type     TEXT NOT NULL DEFAULT '',
    content_key      TEXT NOT NULL DEFAULT '',
    title            TEXT NOT NULL DEFAULT '',
    snippet          TEXT,
    author           TEXT NOT NULL DEFAULT '',
    url              TEXT NOT NULL DEFAULT '',
    published_at     TEXT,
    cover_url        TEXT,
    metrics          TEXT NOT NULL DEFAULT '{}',
    collection_names TEXT NOT NULL DEFAULT '[]',
    first_seen_at    TEXT NOT NULL,
    last_seen_at     TEXT NOT NULL,
    UNIQUE (account_key, platform, content_key)
);

CREATE TABLE IF NOT EXISTS remote_sync_runs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    account_key     TEXT NOT NULL DEFAULT 'default',
    platform        TEXT NOT NULL,
    status          TEXT NOT NULL,
    error_summary   TEXT,
    requested_limit INTEGER NOT NULL DEFAULT 0,
    result_count    INTEGER NOT NULL DEFAULT 0,
    finished_at     TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_remote_favorites_platform
    ON remote_favorites (account_key, platform);
CREATE INDEX IF NOT EXISTS idx_remote_sync_runs_platform
    ON remote_sync_runs (account_key, platform, finished_at DESC);
"""


class RemoteFavoritesStore(RemoteSyncStateMixin, SqliteStoreBase):
    """Persist synced favourites so they survive a backend restart.

    连接、事务与时间戳见 `api/services/sqlite_base.py`（与本地收藏库共用一个库文件）。
    """

    _SCHEMA = _REMOTE_SCHEMA + SYNC_SCHEMA

    def _bootstrap(self, conn: sqlite3.Connection) -> None:
        super()._bootstrap(conn)
        self._migrate_remote_content_identity(conn)
        columns = {row[1] for row in conn.execute("PRAGMA table_info(remote_accounts)")}
        if "result_count" not in columns:
            conn.execute("ALTER TABLE remote_accounts ADD COLUMN result_count INTEGER NOT NULL DEFAULT 0")
        checkpoint_columns = {row[1] for row in conn.execute("PRAGMA table_info(remote_checkpoints)")}
        if "token" not in checkpoint_columns:
            conn.execute("ALTER TABLE remote_checkpoints ADD COLUMN token TEXT")
        if "next_token" not in checkpoint_columns:
            conn.execute("ALTER TABLE remote_checkpoints ADD COLUMN next_token TEXT")
        folder_columns = {row[1] for row in conn.execute("PRAGMA table_info(remote_folders)")}
        if not folder_columns:
            conn.execute("""CREATE TABLE remote_folders (
                account TEXT NOT NULL, folder TEXT NOT NULL, platform TEXT NOT NULL, name TEXT NOT NULL,
                observed_state TEXT NOT NULL DEFAULT 'present', last_observed_at TEXT NOT NULL,
                last_content_complete_at TEXT, PRIMARY KEY(account, folder))""")
        self._enable_wal(conn)

    @staticmethod
    def _migrate_remote_content_identity(conn: sqlite3.Connection) -> None:
        """Give Zhihu type/id pairs an internal key without touching local items.

        Earlier caches only had ``content_id`` in their uniqueness constraint,
        so an article and answer with the same number could overwrite one
        another.  The old row is retained (a past overwrite cannot be
        reconstructed), while all future writes use the typed key.
        """
        columns = {row[1] for row in conn.execute("PRAGMA table_info(remote_favorites)")}
        if not columns or "content_key" in columns:
            return
        conn.execute("ALTER TABLE remote_favorites RENAME TO remote_favorites_legacy_identity")
        conn.execute("""CREATE TABLE remote_favorites (
            id INTEGER PRIMARY KEY AUTOINCREMENT, account_key TEXT NOT NULL DEFAULT 'default',
            platform TEXT NOT NULL, content_id TEXT NOT NULL, content_type TEXT NOT NULL DEFAULT '',
            content_key TEXT NOT NULL, title TEXT NOT NULL DEFAULT '', snippet TEXT, author TEXT NOT NULL DEFAULT '',
            url TEXT NOT NULL DEFAULT '', published_at TEXT, cover_url TEXT, metrics TEXT NOT NULL DEFAULT '{}',
            collection_names TEXT NOT NULL DEFAULT '[]', first_seen_at TEXT NOT NULL, last_seen_at TEXT NOT NULL,
            UNIQUE(account_key, platform, content_key))""")
        conn.execute("""INSERT INTO remote_favorites
            (id,account_key,platform,content_id,content_type,content_key,title,snippet,author,url,published_at,cover_url,metrics,collection_names,first_seen_at,last_seen_at)
            SELECT id,account_key,platform,content_id,content_type,
              CASE WHEN platform='zhihu' AND content_type<>'' THEN content_type || ':' || content_id ELSE content_id END,
              title,snippet,author,url,published_at,cover_url,metrics,collection_names,first_seen_at,last_seen_at
            FROM remote_favorites_legacy_identity""")
        conn.execute("DROP TABLE remote_favorites_legacy_identity")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_remote_favorites_platform ON remote_favorites(account_key, platform)")

    # ------------------------------------------------------------------ 写入

    def save_platform(
        self,
        platform: str,
        results: Sequence[Dict[str, Any]],
        *,
        status: str,
        error_summary: Optional[str] = None,
        requested_limit: int = 0,
        account_key: str = DEFAULT_ACCOUNT_KEY,
        record_run: bool = True,
    ) -> int:
        """保存某个平台本次同步到的条目 + 本次运行状态。返回写入条数。"""
        now = utc_now()
        written = 0
        with self._conn() as conn:
            # 本次没拿到的指标要沿用本机已有的值（见 favorite_snapshot.merge_counts）：
            # 一次超时或只拿到列表字段的同步，不能把以前完整的指标覆盖成残缺版本。
            ids = [self._content_key(platform, row) for row in results]
            previous_metrics = {}
            for offset in range(0, len(ids), 500):
                batch = ids[offset:offset + 500]
                placeholders = ",".join("?" for _ in batch)
                previous_metrics.update({
                    row["content_key"]: row["metrics"]
                    for row in conn.execute(
                        "SELECT content_key, metrics FROM remote_favorites "
                        "WHERE account_key = ? AND platform = ? "
                        f"AND content_key IN ({placeholders})",
                        (account_key, platform, *batch),
                    ).fetchall()
                })
            for raw in results:
                row = self._split(raw)
                if not row["content_id"]:
                    continue
                row["content_key"] = self._content_key(platform, raw)
                previous_raw = previous_metrics.get(row["content_key"])
                if previous_raw:
                    row["metrics"] = encode_metrics(merge_into_result(raw, previous_raw))
                conn.execute(
                    """
                    INSERT INTO remote_favorites (
                        account_key, platform, content_id, content_type, content_key, title, snippet, author,
                        url, published_at, cover_url, metrics, collection_names,
                        first_seen_at, last_seen_at)
                    VALUES (
                        :account_key, :platform, :content_id, :content_type, :content_key, :title, :snippet, :author,
                        :url, :published_at, :cover_url, :metrics, :collection_names, :now, :now)
                    ON CONFLICT (account_key, platform, content_key) DO UPDATE SET
                        content_type = excluded.content_type,
                        title = excluded.title,
                        snippet = excluded.snippet,
                        author = excluded.author,
                        url = excluded.url,
                        published_at = excluded.published_at,
                        cover_url = excluded.cover_url,
                        metrics = excluded.metrics,
                        collection_names = excluded.collection_names,
                        last_seen_at = excluded.last_seen_at
                    """,
                    {**row, "account_key": account_key, "now": now},
                )
                written += 1

            if not record_run:
                return written
            conn.execute(
                """
                INSERT INTO remote_sync_runs (
                    account_key, platform, status, error_summary,
                    requested_limit, result_count, finished_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (account_key, platform, status, error_summary,
                 int(requested_limit), written, now),
            )
        return written

    # ------------------------------------------------------------------ 读取

    def load(self, account_key: str = DEFAULT_ACCOUNT_KEY) -> Optional[Dict[str, Any]]:
        """读取上次保存的同步结果；从未同步过时返回 None。"""
        with self._conn() as conn:
            runs = conn.execute(
                """
                SELECT r.platform, r.status, r.error_summary, r.result_count, r.finished_at
                FROM remote_sync_runs r
                JOIN (
                    SELECT platform, MAX(id) AS max_id FROM remote_sync_runs
                    WHERE account_key = ? GROUP BY platform
                ) latest ON latest.max_id = r.id
                """,
                (account_key,),
            ).fetchall()
            if not runs:
                return None

            rows = conn.execute(
                """
                SELECT * FROM remote_favorites
                WHERE account_key = ?
                ORDER BY platform ASC, last_seen_at DESC, id DESC
                """,
                (account_key,),
            ).fetchall()

        platforms: Dict[str, Dict[str, Any]] = {}
        latest_time = ""
        for run in runs:
            platforms[run["platform"]] = {
                "status": run["status"],
                "result_count": int(run["result_count"]),
                "error_summary": run["error_summary"],
                "synced_at": run["finished_at"],
            }
            latest_time = max(latest_time, run["finished_at"])

        results = [self._row_to_result(row) for row in rows]
        if not results and not platforms:
            return None

        statuses = [info["status"] for info in platforms.values()]
        success = sum(status in ("succeeded", "empty") for status in statuses)
        if all(status in ("running", "pending") for status in statuses):
            overall = "running"
        elif success == len(statuses):
            overall = "completed"
        elif success or results:
            overall = "partial"
        else:
            overall = "failed"

        return {
            "job_id": "saved",
            "overall": overall,
            "created_at": latest_time or utc_now(),
            "completed_at": latest_time or None,
            "platforms": platforms,
            "results": results,
            "saved": True,
        }

    def clear(self, account_key: str = DEFAULT_ACCOUNT_KEY) -> int:
        with self._conn() as conn:
            removed = conn.execute(
                "DELETE FROM remote_favorites WHERE account_key = ?", (account_key,)
            ).rowcount
            conn.execute("DELETE FROM remote_sync_runs WHERE account_key = ?", (account_key,))
        return int(removed)

    # ------------------------------------------------------------------ 内部

    @staticmethod
    def _split(result: Dict[str, Any]) -> Dict[str, Any]:
        collection_names = result.get("collection_names")
        row: Dict[str, Any] = {field: result.get(field) for field in _RESULT_FIELDS}
        for field in ("platform", "content_id", "content_type", "title", "author", "url"):
            if row.get(field) is None:
                row[field] = ""
        row["metrics"] = encode_metrics(result)
        row["collection_names"] = json.dumps(
            [name for name in collection_names if isinstance(name, str)][:20]
            if isinstance(collection_names, list) else [],
            ensure_ascii=False,
        )
        return row

    @staticmethod
    def _content_key(platform: str, result: Dict[str, Any]) -> str:
        content_id = str(result.get("content_id") or "")
        content_type = str(result.get("content_type") or "")
        return f"{content_type}:{content_id}" if platform == "zhihu" and content_type else content_id

    @staticmethod
    def _row_to_result(row: sqlite3.Row) -> Dict[str, Any]:
        try:
            collection_names = json.loads(row["collection_names"] or "[]")
        except json.JSONDecodeError:
            collection_names = []
        return {
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
            "rank": 0,
            "grouped_sources": None,
            "collection_names": collection_names if isinstance(collection_names, list) else [],
        }


_store: Optional[RemoteFavoritesStore] = None
_store_lock = threading.Lock()


def get_remote_favorites_store() -> RemoteFavoritesStore:
    """首次真正用到时才建库（避免 import 阶段就往磁盘写文件）。"""
    global _store
    if _store is None:
        with _store_lock:
            if _store is None:
                _store = RemoteFavoritesStore()
    return _store


def reset_remote_favorites_store() -> None:
    """Drop the cached instance（测试用）。"""
    global _store
    with _store_lock:
        _store = None
