"""本地收藏库：把用户收藏持久化到本机 SQLite，并支持收藏夹（多对多）。

为什么不用浏览器 localStorage：
- localStorage 会随"清除网站数据 / 换浏览器 / 换访问地址"丢失，也不能承载收藏夹关系与批量操作；
- 放进本机 SQLite 后，数据跟着解压目录走，用户可以直接备份整个 data/ 目录。

核心规则（与产品方案一致）：
- 内容本体只存一份，唯一键 (platform, content_id)；同一条内容可以同时属于多个收藏夹。
- 「全部」= 所有条目；「默认收藏夹」与「稍后再看」是彼此独立的内置归属。
- 删除收藏夹默认**保留**其中的内容，只解除归属；取消收藏是独立操作，避免误删。
- 重复收藏只更新内容快照，保留首次收藏时间与已有备注。

数据文件默认位于用户的稳定系统数据目录，源码与不同发行目录共用同一份数据库。
"""

from __future__ import annotations

import json
import sqlite3
import threading
from typing import Any, Dict, Iterable, Iterator, List, Optional, Sequence, Tuple
from urllib.parse import urlsplit

from .favorite_snapshot import decode_metrics, encode_metrics, merge_into_result
from .sqlite_base import RESULT_FIELDS, SqliteStoreBase, default_db_path, utc_now

SCHEMA_VERSION = 3
MAX_NOTE_LENGTH = 1000
MAX_ITEMS = 500
SYSTEM_COLLECTIONS = {"default": "默认收藏夹", "watch_later": "稍后再看"}
RESERVED_COLLECTION_NAMES = {"全部", "全部收藏", *SYSTEM_COLLECTIONS.values()}

_LIBRARY_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS collections (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL UNIQUE COLLATE NOCASE,
    position   INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS items (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    platform      TEXT NOT NULL,
    content_id    TEXT NOT NULL,
    content_type  TEXT NOT NULL DEFAULT '',
    title         TEXT NOT NULL DEFAULT '',
    snippet       TEXT,
    author        TEXT NOT NULL DEFAULT '',
    url           TEXT NOT NULL DEFAULT '',
    published_at  TEXT,
    cover_url     TEXT,
    metrics       TEXT NOT NULL DEFAULT '{}',
    note          TEXT NOT NULL DEFAULT '',
    saved_at      TEXT NOT NULL,
    fetched_at    TEXT,
    in_default    INTEGER NOT NULL DEFAULT 0,
    watch_later   INTEGER NOT NULL DEFAULT 0,
    saved         INTEGER NOT NULL DEFAULT 0,
    UNIQUE (platform, content_id)
);

CREATE TABLE IF NOT EXISTS item_collections (
    item_id       INTEGER NOT NULL REFERENCES items (id) ON DELETE CASCADE,
    collection_id INTEGER NOT NULL REFERENCES collections (id) ON DELETE CASCADE,
    added_at      TEXT NOT NULL,
    PRIMARY KEY (item_id, collection_id)
);

CREATE INDEX IF NOT EXISTS idx_item_collections_collection
    ON item_collections (collection_id);
CREATE INDEX IF NOT EXISTS idx_items_saved_at
    ON items (saved_at DESC);
"""


class LibraryStore(SqliteStoreBase):
    """SQLite-backed local bookmark library.

    连接、事务与外键开关见 `api/services/sqlite_base.py`（与跨平台收藏归档共用）。
    """

    _SCHEMA = _LIBRARY_SCHEMA
    _FOREIGN_KEYS = True

    def _bootstrap(self, conn: sqlite3.Connection) -> None:
        old_columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(items)").fetchall()
        }
        super()._bootstrap(conn)
        if old_columns and "in_default" not in old_columns:
            conn.execute("ALTER TABLE items ADD COLUMN in_default INTEGER NOT NULL DEFAULT 0")
            conn.execute(
                """
                UPDATE items SET in_default = 1
                WHERE NOT EXISTS (
                    SELECT 1 FROM item_collections ic WHERE ic.item_id = items.id
                )
                """
            )
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(items)").fetchall()}
        if "watch_later" not in columns:
            conn.execute("ALTER TABLE items ADD COLUMN watch_later INTEGER NOT NULL DEFAULT 0")
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(items)").fetchall()}
        if "saved" not in columns:
            conn.execute("ALTER TABLE items ADD COLUMN saved INTEGER NOT NULL DEFAULT 0")
            # 旧数据里只挂「稍后再看」的条目不算已收藏：它们不该出现在「全部」里。
            conn.execute("UPDATE items SET saved = 1")
            conn.execute(
                """
                UPDATE items SET saved = 0
                WHERE in_default = 0 AND watch_later = 1
                  AND NOT EXISTS (SELECT 1 FROM item_collections ic WHERE ic.item_id = items.id)
                """
            )
        self._enable_wal(conn)
        conn.execute(
            "INSERT INTO meta (key, value) VALUES ('schema_version', ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (str(SCHEMA_VERSION),),
        )

    # -------------------------------------------------------------- 行 <- 模型

    @staticmethod
    def _split_result(result: Dict[str, Any]) -> Dict[str, Any]:
        """Peel a front-end result object into columns + metrics JSON."""
        if not isinstance(result, dict):
            raise ValueError("收藏内容格式无效")
        if result.get("platform") not in ("xhs", "douyin", "bilibili", "zhihu"):
            raise ValueError("收藏平台无效")
        for field in ("content_id", "title", "url"):
            if not isinstance(result.get(field), str) or not result[field]:
                raise ValueError(f"收藏项缺少有效的 {field}")
        try:
            url = urlsplit(result["url"])
            if url.scheme not in ("http", "https") or not url.netloc:
                raise ValueError()
        except ValueError:
            raise ValueError("收藏原文链接无效") from None
        for field in RESULT_FIELDS:
            if result.get(field) is not None and not isinstance(result[field], str):
                raise ValueError(f"收藏项 {field} 必须是文字")
        row: Dict[str, Any] = {field: result.get(field) for field in RESULT_FIELDS}
        row["metrics"] = encode_metrics(result)
        # 规范化必填字段，避免 None 写进 NOT NULL 列
        for field in ("platform", "content_id", "content_type", "title", "author", "url"):
            if row.get(field) is None:
                row[field] = ""
        return row

    @staticmethod
    def _row_to_item(row: sqlite3.Row, collections: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
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
            "note": row["note"] or "",
            "saved_at": row["saved_at"],
            "fetched_at": row["fetched_at"],
            "in_default": bool(row["in_default"]),
            "watch_later": bool(row["watch_later"]),
            "saved": bool(row["saved"]),
            "collections": collections or [],
        }

    # ------------------------------------------------------------------ 条目

    @staticmethod
    def _find_item(conn: sqlite3.Connection, platform: str, content_id: str) -> Optional[sqlite3.Row]:
        return conn.execute(
            "SELECT id, note, saved_at, fetched_at, metrics FROM items WHERE platform = ? AND content_id = ?",
            (platform, content_id),
        ).fetchone()

    def add_item(
        self,
        result: Dict[str, Any],
        note: str = "",
        saved_at: Optional[str] = None,
        fetched_at: Optional[str] = None,
        collection_ids: Optional[Sequence[int]] = None,
        in_default: Optional[bool] = None,
        watch_later: bool = False,
        saved: Optional[bool] = None,
    ) -> Dict[str, Any]:
        """Insert or refresh one item; keeps first saved_at and existing note.

        `saved` 是「是否收藏」：它决定这条内容出现在不在「全部」里，与
        `in_default`（是否放在默认收藏夹）是两件独立的事。
        """
        platform = str(result.get("platform") or "")
        content_id = str(result.get("content_id") or "")
        if not platform or not content_id:
            raise ValueError("收藏项缺少 platform 或 content_id")

        row = self._split_result(result)
        if not isinstance(note, str):
            raise ValueError("备注必须是文字")
        if any(value is not None and not isinstance(value, str) for value in (saved_at, fetched_at)):
            raise ValueError("收藏时间格式无效")
        note = (note or "")[:MAX_NOTE_LENGTH]
        now = utc_now()
        # 「是否收藏」默认就是收藏：只有稍后再看这条独立的线会显式传 False。
        if saved is None:
            saved = True

        with self._conn(write=True) as conn:
            for collection_id in collection_ids or []:
                if not isinstance(collection_id, int) or not conn.execute(
                    "SELECT 1 FROM collections WHERE id = ?", (collection_id,)
                ).fetchone():
                    raise ValueError("收藏夹不存在")
            existing = self._find_item(conn, platform, content_id)

            if existing is None:
                total = conn.execute("SELECT COUNT(*) AS n FROM items").fetchone()["n"]
                if total >= MAX_ITEMS:
                    raise ValueError(f"收藏已达上限 {MAX_ITEMS} 条")
                # OR IGNORE：并发请求同时插入同一条时退化为更新，保证接口幂等
                # （否则 UNIQUE 冲突会以 500 暴露给前端）。
                cursor = conn.execute(
                    """
                    INSERT OR IGNORE INTO items (platform, content_id, content_type, title, snippet, author,
                                                 url, published_at, cover_url, metrics, note, saved_at, fetched_at,
                                                 in_default, watch_later, saved)
                    VALUES (:platform, :content_id, :content_type, :title, :snippet, :author,
                            :url, :published_at, :cover_url, :metrics, :note, :saved_at, :fetched_at,
                            :in_default, :watch_later, :saved)
                    """,
                    {
                        **row, "note": note, "saved_at": saved_at or now, "fetched_at": fetched_at,
                        "in_default": int(in_default if in_default is not None else not collection_ids),
                        "watch_later": int(bool(watch_later)),
                        "saved": int(bool(saved)),
                    },
                )
                if cursor.rowcount == 0:
                    existing = self._find_item(conn, platform, content_id)

            if existing is not None:
                # 已存在：刷新内容快照，但**保留首次收藏时间与已有备注**——
                # 改备注是独立操作（PATCH），重复收藏不应悄悄覆盖用户写过的字。
                # 指标同样只合并：详情页还没补全就收藏时，不能把以后补上的值清掉。
                item_id = int(existing["id"])
                row["metrics"] = encode_metrics(merge_into_result(result, existing["metrics"]))
                conn.execute(
                    """
                    UPDATE items SET content_type = :content_type, title = :title, snippet = :snippet,
                                     author = :author, url = :url, published_at = :published_at,
                                     cover_url = :cover_url, metrics = :metrics,
                                     fetched_at = COALESCE(:fetched_at, fetched_at)
                    WHERE id = :id
                    """,
                    {**row, "fetched_at": fetched_at, "id": item_id},
                )
                if in_default:
                    conn.execute("UPDATE items SET in_default = 1 WHERE id = ?", (item_id,))
                if watch_later:
                    conn.execute("UPDATE items SET watch_later = 1 WHERE id = ?", (item_id,))
                if saved:
                    conn.execute("UPDATE items SET saved = 1 WHERE id = ?", (item_id,))
            else:
                item_id = int(cursor.lastrowid)

            if collection_ids:
                self._attach(conn, item_id, collection_ids)

        item = self.get_item(platform, content_id)
        assert item is not None
        return item

    def add_items(self, entries: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
        """Batch add (迁移/导入备份用)。返回 added / updated / skipped 统计。"""
        with self._conn(write=True):
            return self._add_items(entries)

    def _add_items(self, entries: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
        added = updated = skipped = 0
        for entry in entries:
            if not isinstance(entry, dict) or not isinstance(entry.get("result"), dict):
                skipped += 1
                continue
            result = entry.get("result") or {}
            platform = str(result.get("platform") or "")
            content_id = str(result.get("content_id") or "")
            if not platform or not content_id:
                skipped += 1
                continue
            existed = self.get_item(platform, content_id) is not None
            try:
                self.add_item(
                    result,
                    note=entry.get("note") or "",
                    saved_at=entry.get("saved_at"),
                    fetched_at=entry.get("fetched_at"),
                    collection_ids=entry.get("collection_ids"),
                    in_default=entry.get("in_default"),
                    watch_later=bool(entry.get("watch_later")),
                    saved=entry.get("saved"),
                )
            except ValueError:
                skipped += 1
                continue
            if existed:
                updated += 1
            else:
                added += 1
        return {"added": added, "updated": updated, "skipped": skipped}

    def get_item(self, platform: str, content_id: str) -> Optional[Dict[str, Any]]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM items WHERE platform = ? AND content_id = ?",
                (platform, content_id),
            ).fetchone()
            if row is None:
                return None
            return self._row_to_item(row, self._collections_of(conn, int(row["id"])))

    def set_note(self, platform: str, content_id: str, note: str) -> bool:
        with self._conn() as conn:
            cursor = conn.execute(
                "UPDATE items SET note = ? WHERE platform = ? AND content_id = ?",
                ((note or "")[:MAX_NOTE_LENGTH], platform, content_id),
            )
            return cursor.rowcount > 0

    def remove_items(self, keys: Sequence[Tuple[str, str]]) -> int:
        """取消收藏（独立操作）。同时解除收藏夹归属，内容本体删除。"""
        if not keys:
            return 0
        removed = 0
        with self._conn() as conn:
            for platform, content_id in keys:
                cursor = conn.execute(
                    "DELETE FROM items WHERE platform = ? AND content_id = ?",
                    (platform, content_id),
                )
                removed += cursor.rowcount
        return removed

    def list_items(
        self,
        collection_id: Optional[int] = None,
        only_unclassified: bool = False,
        system_collection: Optional[str] = None,
        platform: Optional[str] = None,
        query: Optional[str] = None,
        limit: Optional[int] = None,
        offset: int = 0,
    ) -> Dict[str, Any]:
        where: List[str] = []
        params: List[Any] = []

        if system_collection == "default":
            where.append("items.in_default = 1")
        elif system_collection == "watch_later":
            where.append("items.watch_later = 1")
        elif only_unclassified:
            where.append(
                "items.in_default = 0 AND items.watch_later = 0 AND "
                "NOT EXISTS (SELECT 1 FROM item_collections ic WHERE ic.item_id = items.id)"
            )
        elif collection_id is not None:
            where.append(
                "EXISTS (SELECT 1 FROM item_collections ic WHERE ic.item_id = items.id AND ic.collection_id = ?)"
            )
            params.append(collection_id)

        if platform:
            where.append("items.platform = ?")
            params.append(platform)

        if query:
            like = f"%{query}%"
            where.append("(items.title LIKE ? OR items.author LIKE ? OR items.snippet LIKE ?)")
            params.extend([like, like, like])

        clause = f"WHERE {' AND '.join(where)}" if where else ""

        with self._conn() as conn:
            total = conn.execute(f"SELECT COUNT(*) AS n FROM items {clause}", params).fetchone()["n"]
            sql = f"SELECT * FROM items {clause} ORDER BY saved_at DESC, id DESC"
            if limit is not None:
                sql += " LIMIT ? OFFSET ?"
                params = [*params, limit, offset]
            rows = conn.execute(sql, params).fetchall()
            items = [self._row_to_item(row, self._collections_of(conn, int(row["id"]))) for row in rows]
        return {"items": items, "total": int(total)}

    # ---------------------------------------------------------------- 收藏夹

    def list_collections(self) -> List[Dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute(
                """
                SELECT c.id, c.name, c.position, c.created_at,
                       (SELECT COUNT(*) FROM item_collections ic WHERE ic.collection_id = c.id) AS item_count
                FROM collections c
                ORDER BY c.position ASC, c.id ASC
                """
            ).fetchall()
            return [
                {
                    "id": int(row["id"]),
                    "name": row["name"],
                    "position": int(row["position"]),
                    "created_at": row["created_at"],
                    "item_count": int(row["item_count"]),
                }
                for row in rows
            ]

    def create_collection(self, name: str) -> Dict[str, Any]:
        name = (name or "").strip()
        if not name:
            raise ValueError("收藏夹名称不能为空")
        if len(name) > 60:
            raise ValueError("收藏夹名称最长 60 个字")
        if name.casefold() in {item.casefold() for item in RESERVED_COLLECTION_NAMES}:
            raise ValueError(f'「{name}」是系统收藏夹名称')
        with self._conn() as conn:
            if conn.execute("SELECT 1 FROM collections WHERE name = ?", (name,)).fetchone():
                raise ValueError(f'收藏夹「{name}」已存在')
            position = conn.execute("SELECT COALESCE(MAX(position), 0) + 1 AS p FROM collections").fetchone()["p"]
            cursor = conn.execute(
                "INSERT INTO collections (name, position, created_at) VALUES (?, ?, ?)",
                (name, position, utc_now()),
            )
            collection_id = int(cursor.lastrowid)
        return {"id": collection_id, "name": name, "position": int(position), "item_count": 0}

    def ensure_imported_collection(self, name: str) -> Dict[str, Any]:
        """Restore a historical custom folder, including names now reserved by the UI."""
        name = (name or "").strip()
        if not name:
            raise ValueError("收藏夹名称不能为空")
        if len(name) > 60:
            raise ValueError("收藏夹名称最长 60 个字")
        with self._conn(write=True) as conn:
            existing = conn.execute(
                "SELECT id, name, position FROM collections WHERE name = ?", (name,)
            ).fetchone()
            if existing:
                count = conn.execute(
                    "SELECT COUNT(*) AS n FROM item_collections WHERE collection_id = ?", (existing["id"],)
                ).fetchone()["n"]
                return {
                    "id": int(existing["id"]), "name": existing["name"],
                    "position": int(existing["position"]), "item_count": int(count),
                }
            position = conn.execute(
                "SELECT COALESCE(MAX(position), 0) + 1 AS p FROM collections"
            ).fetchone()["p"]
            cursor = conn.execute(
                "INSERT INTO collections (name, position, created_at) VALUES (?, ?, ?)",
                (name, position, utc_now()),
            )
        return {"id": int(cursor.lastrowid), "name": name, "position": int(position), "item_count": 0}

    def rename_collection(self, collection_id: int, name: str) -> Dict[str, Any]:
        name = (name or "").strip()
        if not name:
            raise ValueError("收藏夹名称不能为空")
        if len(name) > 60:
            raise ValueError("收藏夹名称最长 60 个字")
        if name.casefold() in {item.casefold() for item in RESERVED_COLLECTION_NAMES}:
            raise ValueError(f'「{name}」是系统收藏夹名称')
        with self._conn() as conn:
            conflict = conn.execute(
                "SELECT id FROM collections WHERE name = ? AND id <> ?", (name, collection_id)
            ).fetchone()
            if conflict:
                raise ValueError(f'收藏夹「{name}」已存在')
            cursor = conn.execute("UPDATE collections SET name = ? WHERE id = ?", (name, collection_id))
            if cursor.rowcount == 0:
                raise ValueError("收藏夹不存在")
            count = conn.execute(
                "SELECT COUNT(*) AS n FROM item_collections WHERE collection_id = ?", (collection_id,)
            ).fetchone()["n"]
        return {"id": collection_id, "name": name, "item_count": int(count)}

    def delete_collection(self, collection_id: int) -> Dict[str, Any]:
        """删除收藏夹但**保留**其中的内容（只解除归属）。"""
        with self._conn() as conn:
            row = conn.execute("SELECT name FROM collections WHERE id = ?", (collection_id,)).fetchone()
            if row is None:
                raise ValueError("收藏夹不存在")
            detached = conn.execute(
                "SELECT COUNT(*) AS n FROM item_collections WHERE collection_id = ?", (collection_id,)
            ).fetchone()["n"]
            conn.execute("DELETE FROM collections WHERE id = ?", (collection_id,))
        return {"deleted": row["name"], "kept_items": int(detached)}

    def add_items_to_collection(self, keys: Sequence[Tuple[str, str]], collection_id: int) -> Dict[str, Any]:
        if not keys:
            return {"added": 0, "missing": 0}
        added = missing = 0
        with self._conn() as conn:
            row = conn.execute("SELECT 1 FROM collections WHERE id = ?", (collection_id,)).fetchone()
            if row is None:
                raise ValueError("收藏夹不存在")
            for platform, content_id in keys:
                item = conn.execute(
                    "SELECT id FROM items WHERE platform = ? AND content_id = ?", (platform, content_id)
                ).fetchone()
                if item is None:
                    missing += 1
                    continue
                cursor = conn.execute(
                    """
                    INSERT OR IGNORE INTO item_collections (item_id, collection_id, added_at)
                    VALUES (?, ?, ?)
                    """,
                    (int(item["id"]), collection_id, utc_now()),
                )
                added += cursor.rowcount
        return {"added": added, "missing": missing}

    def remove_items_from_collection(self, keys: Sequence[Tuple[str, str]], collection_id: int) -> Dict[str, Any]:
        if not keys:
            return {"removed": 0}
        removed = 0
        with self._conn() as conn:
            for platform, content_id in keys:
                cursor = conn.execute(
                    """
                    DELETE FROM item_collections
                    WHERE collection_id = ?
                      AND item_id IN (SELECT id FROM items WHERE platform = ? AND content_id = ?)
                    """,
                    (collection_id, platform, content_id),
                )
                removed += cursor.rowcount
        return {"removed": removed}

    # ---------------------------------------------------------- 内置收藏夹

    def add_items_to_system_collection(
        self,
        entries: Iterable[Dict[str, Any]],
        collection: str,
    ) -> Dict[str, Any]:
        if collection not in SYSTEM_COLLECTIONS:
            raise ValueError("系统收藏夹不存在")
        field = "in_default" if collection == "default" else "watch_later"
        normalized = []
        for entry in entries:
            normalized.append({
                **entry,
                # 收藏与稍后再看是两条独立的线：
                # - 点收藏 = 已收藏（进入「全部」），同时放进默认收藏夹；
                # - 点稍后再看只挂稍后再看，不碰「已收藏」，也不进默认收藏夹。
                "in_default": collection == "default",
                "saved": True if collection == "default" else bool(entry.get("saved")),
                field: True,
            })
        return self.add_items(normalized)

    def remove_items_from_system_collection(
        self,
        keys: Sequence[Tuple[str, str]],
        collection: str,
    ) -> Dict[str, Any]:
        if collection not in SYSTEM_COLLECTIONS:
            raise ValueError("系统收藏夹不存在")
        if not keys:
            return {"removed": 0}
        field = "in_default" if collection == "default" else "watch_later"
        removed = 0
        with self._conn(write=True) as conn:
            for platform, content_id in keys:
                cursor = conn.execute(
                    f"UPDATE items SET {field} = 0 "
                    "WHERE platform = ? AND content_id = ? AND " + field + " = 1",
                    (platform, content_id),
                )
                removed += cursor.rowcount
            # 取消稍后再看后，既没收藏也不稍后再看的条目没有任何入口能看到它，
            # 直接删掉，避免变成谁也看不见的残留数据。
            conn.execute("DELETE FROM items WHERE saved = 0 AND watch_later = 0")
        return {"removed": removed}

    # ------------------------------------------------------------ 导入 / 导出

    def export_payload(self) -> Dict[str, Any]:
        """导出为可移植 JSON（等价于原来的「备份全部收藏」）。"""
        with self._conn() as conn:
            rows = conn.execute("SELECT * FROM items ORDER BY saved_at ASC, id ASC").fetchall()
            items = [
                self._row_to_item(row, self._collections_of(conn, int(row["id"])))
                for row in rows
            ]
        return {
            "version": 4,
            "exported_at": utc_now(),
            "collections": self.list_collections(),
            "items": items,
        }

    def import_payload(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        # 文件格式错误或磁盘写入失败时，收藏夹与条目一起回滚。
        with self._conn(write=True):
            return self._import_payload(payload)

    def _import_payload(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """导入备份 / 迁移旧 localStorage 数据。只合并新增，不删除现有内容。

        兼容两种输入：
        - v1（旧浏览器收藏）：``{"version":1,"items":[{"result":..., "note":..., "savedAt":..., "fetchedAt":...}]}``
        - v2（旧本机库导出）：额外带 ``collections`` 与条目内嵌的 ``collections`` 名称；
        - v3：再保存两个内置收藏夹的独立归属；
        - v4：额外保存「是否已收藏」（`saved`，决定它出现在不在「全部」里）。
        """
        raw_items = payload.get("items")
        if not isinstance(raw_items, list):
            raise ValueError("备份文件格式不正确：缺少 items 数组")
        version = payload.get("version", 1)
        if version not in (1, 2, 3, 4):
            raise ValueError("暂不支持这个备份版本")
        folders = payload.get("collections") or []
        if not isinstance(folders, list):
            raise ValueError("备份收藏夹格式无效")

        # 先建收藏夹（v2），并记录 名称 -> id
        name_to_id: Dict[str, int] = {}
        for entry in folders:
            if not isinstance(entry, dict):
                continue
            if not isinstance(entry.get("name"), str):
                raise ValueError("收藏夹名称必须是文字")
            name = entry["name"].strip()
            if not name:
                continue
            existing = next((c for c in self.list_collections() if c["name"].lower() == name.lower()), None)
            name_to_id[name.lower()] = existing["id"] if existing else self.ensure_imported_collection(name)["id"]

        entries: List[Dict[str, Any]] = []
        for raw in raw_items:
            if not isinstance(raw, dict):
                entries.append({"result": {}})
                continue
            result = raw.get("result") if isinstance(raw.get("result"), dict) else raw
            if not isinstance(result, dict):
                continue
            collection_ids: List[int] = []
            tags = raw.get("collections") or []
            if not isinstance(tags, list):
                raise ValueError("条目收藏夹格式无效")
            for tag in tags:
                label = (tag.get("name") if isinstance(tag, dict) else str(tag)) or ""
                if not isinstance(label, str):
                    raise ValueError("收藏夹名称必须是文字")
                label = label.strip().lower()
                if not label:
                    continue
                if label not in name_to_id:
                    existing = next((c for c in self.list_collections() if c["name"].lower() == label), None)
                    name_to_id[label] = existing["id"] if existing else self.ensure_imported_collection(label)["id"]
                collection_ids.append(name_to_id[label])
            if name_to_id and not collection_ids and raw.get("collection_ids"):
                collection_ids = [int(c) for c in raw["collection_ids"]]
            entries.append(
                {
                    "result": result,
                    "note": raw.get("note") or raw.get("remark") or "",
                    # 兼容 camelCase（旧 localStorage 备份）
                    "saved_at": raw.get("saved_at") or raw.get("savedAt"),
                    "fetched_at": raw.get("fetched_at") or raw.get("fetchedAt"),
                    "collection_ids": collection_ids,
                    "in_default": bool(raw.get("in_default")) if version >= 3 else not collection_ids,
                    "watch_later": bool(raw.get("watch_later")) if version >= 3 else False,
                    # v4 之前没有「已收藏」这一说，凡是进了备份的都算已收藏。
                    "saved": bool(raw.get("saved")) if version >= 4 else True,
                }
            )

        stats = self.add_items(entries)
        stats["total"] = len(entries)
        return stats

    def stats(self) -> Dict[str, Any]:
        with self._conn() as conn:
            total = conn.execute("SELECT COUNT(*) AS n FROM items").fetchone()["n"]
            saved_count = conn.execute("SELECT COUNT(*) AS n FROM items WHERE saved = 1").fetchone()["n"]
            unclassified = conn.execute(
                "SELECT COUNT(*) AS n FROM items WHERE saved = 1 AND in_default = 0 "
                "AND NOT EXISTS (SELECT 1 FROM item_collections ic WHERE ic.item_id = items.id)"
            ).fetchone()["n"]
            default_count = conn.execute(
                "SELECT COUNT(*) AS n FROM items WHERE in_default = 1"
            ).fetchone()["n"]
            watch_later_count = conn.execute(
                "SELECT COUNT(*) AS n FROM items WHERE watch_later = 1"
            ).fetchone()["n"]
            collections = conn.execute("SELECT COUNT(*) AS n FROM collections").fetchone()["n"]
            migration_row = conn.execute(
                "SELECT value FROM meta WHERE key = 'last_legacy_migration'"
            ).fetchone()
        migration = None
        if migration_row:
            try:
                migration = json.loads(migration_row["value"])
            except (TypeError, json.JSONDecodeError):
                migration = None
        return {
            "total": int(total),
            "saved_count": int(saved_count),
            "unclassified": int(unclassified),
            "default_count": int(default_count),
            "watch_later_count": int(watch_later_count),
            "collections": int(collections),
            "db_path": str(self.db_path),
            "migration": migration,
        }

    # ------------------------------------------------------------------ 内部

    @staticmethod
    def _collections_of(conn: sqlite3.Connection, item_id: int) -> List[Dict[str, Any]]:
        rows = conn.execute(
            """
            SELECT c.id, c.name FROM item_collections ic
            JOIN collections c ON c.id = ic.collection_id
            WHERE ic.item_id = ?
            ORDER BY c.position ASC, c.id ASC
            """,
            (item_id,),
        ).fetchall()
        return [{"id": int(row["id"]), "name": row["name"]} for row in rows]

    @staticmethod
    def _attach(conn: sqlite3.Connection, item_id: int, collection_ids: Sequence[int]) -> None:
        now = utc_now()
        for collection_id in collection_ids:
            conn.execute(
                "INSERT OR IGNORE INTO item_collections (item_id, collection_id, added_at) VALUES (?, ?, ?)",
                (item_id, int(collection_id), now),
            )


_store: Optional[LibraryStore] = None
_store_lock = threading.Lock()


def get_library_store() -> LibraryStore:
    """FastAPI 依赖：首次真正用到时才建库，避免 import 阶段就往磁盘写文件。"""
    global _store
    if _store is None:
        with _store_lock:
            if _store is None:
                _store = LibraryStore()
    return _store


def reset_library_store() -> None:
    """Drop the cached instance（测试用）。"""
    global _store
    with _store_lock:
        _store = None
