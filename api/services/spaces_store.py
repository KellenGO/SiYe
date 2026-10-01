"""Independent local snapshots and rich-text notes for research spaces."""

import json
import math
from urllib.parse import urlsplit

from .favorite_snapshot import METRIC_NAMES, decode_metrics, encode_metrics
from .space_notes import EMPTY_NOTE, validate_note
from .sqlite_base import RESULT_FIELDS, SqliteStoreBase, singleton_store, utc_now

MAX_SPACE_ITEMS = 500


class SpaceMissing(ValueError):
    pass


class SpaceConflict(ValueError):
    pass


def public_snapshot(result):
    if not isinstance(result, dict) or result.get("platform") not in {"xhs", "douyin", "bilibili", "zhihu"}:
        raise ValueError("资料平台无效")
    for field in RESULT_FIELDS:
        if result.get(field) is not None and not isinstance(result[field], str):
            raise ValueError("资料字段必须是文字")
    if not all(result.get(field) for field in ("content_id", "title", "url")):
        raise ValueError("资料缺少标题、标识或链接")
    url = urlsplit(result["url"])
    domains = ("xiaohongshu.com", "xhslink.com", "rednote.com", "douyin.com", "bilibili.com", "zhihu.com")
    if url.scheme not in {"http", "https"} or not url.hostname or url.username or url.password or not any(url.hostname == domain or url.hostname.endswith("." + domain) for domain in domains):
        raise ValueError("资料原文链接无效")
    snapshot = {field: result.get(field) for field in RESULT_FIELDS}
    metrics = result.get("metrics", {})
    if not isinstance(metrics, dict):
        raise ValueError("资料指标无效")
    snapshot["metrics"] = {key: value for key, value in metrics.items()
                           if key in METRIC_NAMES and type(value) in (int, float) and math.isfinite(value) and value >= 0}
    metadata = {**result, "metrics": snapshot["metrics"], "collection_names": []}
    snapshot.update(decode_metrics(encode_metrics(metadata)))
    snapshot["rank"] = 0
    return snapshot


class SpacesStore(SqliteStoreBase):
    _FOREIGN_KEYS = True
    _SCHEMA = """
    CREATE TABLE IF NOT EXISTS spaces (
        id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL,
        description TEXT NOT NULL DEFAULT '', archived INTEGER NOT NULL DEFAULT 0,
        note_document TEXT NOT NULL, note_format_version INTEGER NOT NULL DEFAULT 1,
        note_revision INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS space_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        space_id INTEGER NOT NULL REFERENCES spaces(id) ON DELETE CASCADE,
        platform TEXT NOT NULL, content_id TEXT NOT NULL, result TEXT NOT NULL,
        added_at TEXT NOT NULL, UNIQUE(space_id, platform, content_id)
    );
    CREATE INDEX IF NOT EXISTS idx_space_items_space ON space_items(space_id, added_at DESC, id DESC);
    CREATE TABLE IF NOT EXISTS space_state (
        id INTEGER PRIMARY KEY CHECK(id = 1),
        active_space_id INTEGER REFERENCES spaces(id) ON DELETE SET NULL
    );
    INSERT OR IGNORE INTO space_state(id) VALUES (1);
    """

    def _bootstrap(self, conn):
        super()._bootstrap(conn)
        self._enable_wal(conn)

    @staticmethod
    def _space(conn, space_id, writable=False):
        row = conn.execute("SELECT * FROM spaces WHERE id = ?", (space_id,)).fetchone()
        if row is None:
            raise SpaceMissing("空间不存在")
        if writable and row["archived"]:
            raise SpaceConflict("空间已归档，请先继续研究")
        return row

    @staticmethod
    def _summary(row):
        return {key: bool(row[key]) if key == "archived" else row[key]
                for key in ("id", "name", "description", "archived", "created_at", "updated_at", "item_count")}

    def list_spaces(self):
        with self._conn() as conn:
            rows = conn.execute("SELECT s.*, (SELECT COUNT(*) FROM space_items i WHERE i.space_id = s.id) item_count FROM spaces s ORDER BY updated_at DESC, id DESC").fetchall()
            active = conn.execute("SELECT active_space_id FROM space_state WHERE id=1").fetchone()[0]
            return {"spaces": [self._summary(row) for row in rows], "active_space_id": active}

    def get_space(self, space_id):
        with self._conn() as conn:
            row = self._space(conn, space_id)
            items = conn.execute("SELECT * FROM space_items WHERE space_id=? ORDER BY added_at DESC, id DESC", (space_id,)).fetchall()
            return {**self._summary({**dict(row), "item_count": len(items)}),
                    "note_document": json.loads(row["note_document"]), "note_format_version": row["note_format_version"],
                    "note_revision": row["note_revision"],
                    "items": [{"key": item["platform"] + "|" + item["content_id"], "result": json.loads(item["result"]), "added_at": item["added_at"]} for item in items]}

    @staticmethod
    def _info(name, description):
        name = name.strip()
        if not name or len(name) > 60 or len(description) > 200:
            raise ValueError("名称须为 1–60 字，简介最多 200 字")
        return name, description

    def create_space(self, name, description=""):
        name, description = self._info(name, description)
        now = utc_now()
        with self._conn(write=True) as conn:
            space_id = conn.execute("INSERT INTO spaces(name,description,note_document,created_at,updated_at) VALUES(?,?,?,?,?)",
                                    (name, description, json.dumps(EMPTY_NOTE), now, now)).lastrowid
            conn.execute("UPDATE space_state SET active_space_id=? WHERE id=1", (space_id,))
            return self.get_space(space_id)

    def update_info(self, space_id, name, description):
        name, description = self._info(name, description)
        with self._conn(write=True) as conn:
            self._space(conn, space_id, writable=True)
            conn.execute("UPDATE spaces SET name=?, description=?, updated_at=? WHERE id=?", (name, description, utc_now(), space_id))
            return self.get_space(space_id)

    def set_active(self, space_id):
        with self._conn(write=True) as conn:
            if space_id is not None:
                self._space(conn, space_id, writable=True)
            conn.execute("UPDATE space_state SET active_space_id=? WHERE id=1", (space_id,))
            return {"active_space_id": space_id}

    def set_archived(self, space_id, archived):
        with self._conn(write=True) as conn:
            self._space(conn, space_id)
            conn.execute("UPDATE spaces SET archived=?, updated_at=? WHERE id=?", (archived, utc_now(), space_id))
            if archived:
                conn.execute("UPDATE space_state SET active_space_id=NULL WHERE active_space_id=?", (space_id,))
            else:
                conn.execute("UPDATE space_state SET active_space_id=? WHERE id=1", (space_id,))
            return self.get_space(space_id)

    def delete_space(self, space_id):
        with self._conn(write=True) as conn:
            self._space(conn, space_id)
            conn.execute("DELETE FROM spaces WHERE id=?", (space_id,))
            return {"removed": 1}

    def add_items(self, space_id, results):
        snapshots = [public_snapshot(result) for result in results]
        snapshots = {(item["platform"], item["content_id"]): item for item in snapshots}
        with self._conn(write=True) as conn:
            self._space(conn, space_id, writable=True)
            existing = {(row[0], row[1]) for row in conn.execute("SELECT platform,content_id FROM space_items WHERE space_id=?", (space_id,))}
            additions = {key: item for key, item in snapshots.items() if key not in existing}
            if len(existing) + len(additions) > MAX_SPACE_ITEMS:
                raise ValueError("每个空间最多保存 500 条资料；请先移出部分资料")
            now = utc_now()
            for (platform, content_id), item in additions.items():
                conn.execute("INSERT INTO space_items(space_id,platform,content_id,result,added_at) VALUES(?,?,?,?,?)", (space_id, platform, content_id, json.dumps(item, ensure_ascii=False), now))
            if additions:
                conn.execute("UPDATE spaces SET updated_at=? WHERE id=?", (now, space_id))
            return {"added": len(additions), "total": len(existing) + len(additions)}

    def remove_items(self, space_id, keys):
        with self._conn(write=True) as conn:
            self._space(conn, space_id, writable=True)
            removed = 0
            for key in keys:
                removed += conn.execute("DELETE FROM space_items WHERE space_id=? AND platform=? AND content_id=?", (space_id, key["platform"], key["content_id"])).rowcount
            if removed:
                conn.execute("UPDATE spaces SET updated_at=? WHERE id=?", (utc_now(), space_id))
            return {"removed": removed}

    def save_note(self, space_id, document, format_version, revision):
        encoded = validate_note(document, format_version)
        with self._conn(write=True) as conn:
            row = self._space(conn, space_id, writable=True)
            if row["note_revision"] != revision:
                raise SpaceConflict("笔记已在其他页面更新；本地草稿已保留，请查看最新笔记后再保存")
            now = utc_now()
            conn.execute("UPDATE spaces SET note_document=?, note_format_version=?, note_revision=note_revision+1, updated_at=? WHERE id=?", (encoded, format_version, now, space_id))
            return {"note_revision": revision + 1, "updated_at": now}


get_spaces_store, reset_spaces_store = singleton_store(SpacesStore)
