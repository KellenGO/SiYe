"""Move legacy per-folder libraries into the stable per-user SQLite file."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from base.runtime_paths import application_root, library_data_root
from .favorite_snapshot import decode_metrics
from .library_store import LibraryStore
from .remote_favorites_store import RemoteFavoritesStore
from .sqlite_base import default_db_path


def legacy_library_candidates(root: Path | None = None) -> List[Path]:
    base = Path(root) if root is not None else application_root()
    paths = [
        base / "data" / "library.db",
        base / "dist" / "MediaCrawler" / "data" / "library.db",
        base / "dist" / "SiYe" / "data" / "library.db",
        base.parent / "MediaCrawler" / "data" / "library.db",
        base.parent / "SiYe" / "data" / "library.db",
    ]
    target = default_db_path().resolve()
    unique: List[Path] = []
    for path in paths:
        resolved = path.resolve()
        if resolved == target or resolved in unique or not resolved.is_file():
            continue
        unique.append(resolved)
    return unique


def _tables(conn: sqlite3.Connection) -> set[str]:
    return {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def _fingerprint(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _merge_local(source: sqlite3.Connection, target: LibraryStore) -> int:
    if not {"items", "collections", "item_collections"}.issubset(_tables(source)):
        return 0
    source.row_factory = sqlite3.Row
    target_collections = {row["name"].casefold(): row["id"] for row in target.list_collections()}
    collection_map: Dict[int, int] = {}
    for row in source.execute("SELECT id, name FROM collections ORDER BY position, id"):
        raw_name = str(row["name"] or "").strip()
        if not raw_name:
            continue
        key = raw_name.casefold()
        if key not in target_collections:
            target_collections[key] = target.ensure_imported_collection(raw_name)["id"]
        collection_map[int(row["id"])] = target_collections[key]

    item_columns = _columns(source, "items")
    imported = 0
    for row in source.execute("SELECT * FROM items ORDER BY saved_at, id"):
        source_tags = [
            int(tag[0]) for tag in source.execute(
                "SELECT collection_id FROM item_collections WHERE item_id = ?", (row["id"],)
            )
        ]
        collection_ids = [collection_map[tag] for tag in source_tags if tag in collection_map]
        result = {
            "platform": row["platform"], "content_id": row["content_id"],
            "content_type": row["content_type"], "title": row["title"],
            "snippet": row["snippet"], "author": row["author"], "url": row["url"],
            "published_at": row["published_at"], "cover_url": row["cover_url"],
            **decode_metrics(row["metrics"]),
        }
        before = target.get_item(row["platform"], row["content_id"])
        target.add_item(
            result,
            note=row["note"] or "",
            saved_at=row["saved_at"],
            fetched_at=row["fetched_at"],
            collection_ids=collection_ids,
            in_default=bool(row["in_default"]) if "in_default" in item_columns else not collection_ids,
            watch_later=bool(row["watch_later"]) if "watch_later" in item_columns else False,
        )
        if before and not before["note"] and row["note"]:
            target.set_note(row["platform"], row["content_id"], row["note"])
        imported += int(before is None)
    return imported


def _merge_remote(source: sqlite3.Connection, target_path: Path) -> int:
    if "remote_favorites" not in _tables(source):
        return 0
    source.row_factory = sqlite3.Row
    imported = 0
    with sqlite3.connect(target_path) as target:
        target.row_factory = sqlite3.Row
        for row in source.execute("SELECT * FROM remote_favorites ORDER BY last_seen_at, id"):
            existing = target.execute(
                "SELECT * FROM remote_favorites WHERE account_key=? AND platform=? AND content_id=?",
                (row["account_key"], row["platform"], row["content_id"]),
            ).fetchone()
            old_names = json.loads(existing["collection_names"] or "[]") if existing else []
            new_names = json.loads(row["collection_names"] or "[]")
            names = list(dict.fromkeys([name for name in [*old_names, *new_names] if isinstance(name, str)]))[:20]
            if existing is None:
                target.execute(
                    """INSERT INTO remote_favorites
                    (account_key,platform,content_id,content_type,title,snippet,author,url,published_at,
                     cover_url,metrics,collection_names,first_seen_at,last_seen_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    tuple(row[key] for key in (
                        "account_key", "platform", "content_id", "content_type", "title", "snippet",
                        "author", "url", "published_at", "cover_url", "metrics"
                    )) + (json.dumps(names, ensure_ascii=False), row["first_seen_at"], row["last_seen_at"]),
                )
                imported += 1
            else:
                newer = row["last_seen_at"] >= existing["last_seen_at"]
                content = row if newer else existing
                target.execute(
                    """UPDATE remote_favorites SET content_type=?,title=?,snippet=?,author=?,url=?,
                    published_at=?,cover_url=?,metrics=?,collection_names=?,first_seen_at=?,last_seen_at=?
                    WHERE id=?""",
                    (
                        content["content_type"], content["title"], content["snippet"], content["author"],
                        content["url"], content["published_at"], content["cover_url"], content["metrics"],
                        json.dumps(names, ensure_ascii=False), min(row["first_seen_at"], existing["first_seen_at"]),
                        max(row["last_seen_at"], existing["last_seen_at"]), existing["id"],
                    ),
                )
        if "remote_sync_runs" in _tables(source):
            latest = source.execute(
                """SELECT r.* FROM remote_sync_runs r JOIN (
                SELECT account_key, platform, MAX(id) AS id FROM remote_sync_runs
                GROUP BY account_key, platform) x ON x.id=r.id"""
            ).fetchall()
            for row in latest:
                current = target.execute(
                    "SELECT finished_at FROM remote_sync_runs WHERE account_key=? AND platform=? ORDER BY id DESC LIMIT 1",
                    (row["account_key"], row["platform"]),
                ).fetchone()
                if current and current["finished_at"] >= row["finished_at"]:
                    continue
                target.execute(
                    """INSERT INTO remote_sync_runs
                    (account_key,platform,status,error_summary,requested_limit,result_count,finished_at)
                    VALUES (?,?,?,?,?,?,?)""",
                    tuple(row[key] for key in (
                        "account_key", "platform", "status", "error_summary", "requested_limit",
                        "result_count", "finished_at"
                    )),
                )
    return imported


def migrate_legacy_libraries(root: Path | None = None) -> Dict[str, Any]:
    """Merge known legacy databases without modifying or deleting any source."""
    target_path = default_db_path()
    target_path.parent.mkdir(parents=True, exist_ok=True)
    local = LibraryStore(target_path)
    RemoteFavoritesStore(target_path)
    candidates = sorted(legacy_library_candidates(root), key=lambda path: path.stat().st_mtime)
    summary: Dict[str, Any] = {"source_count": 0, "local_items": 0, "remote_items": 0, "warnings": []}
    if not candidates:
        return summary

    pending: List[tuple[Path, str]] = []
    for source_path in candidates:
        try:
            fingerprint = _fingerprint(source_path)
            marker = f"legacy_db:{fingerprint}"
            with sqlite3.connect(target_path) as target_conn:
                seen = target_conn.execute("SELECT 1 FROM meta WHERE key=?", (marker,)).fetchone()
            if not seen:
                pending.append((source_path, fingerprint))
        except OSError as error:
            summary["warnings"].append(type(error).__name__)
    if not pending:
        return summary

    if target_path.exists() and target_path.stat().st_size:
        backup_dir = library_data_root() / "migration-backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        backup = backup_dir / f"library-before-merge-{stamp}.db"
        with sqlite3.connect(target_path) as source_conn, sqlite3.connect(backup) as backup_conn:
            source_conn.backup(backup_conn)

    successful_fingerprints: List[str] = []
    for source_path, fingerprint in pending:
        rollback = sqlite3.connect(":memory:")
        try:
            with sqlite3.connect(target_path) as target_conn:
                target_conn.backup(rollback)
            marker = f"legacy_db:{fingerprint}"
            with sqlite3.connect(f"file:{source_path.as_posix()}?mode=ro", uri=True) as source_conn:
                local_added = _merge_local(source_conn, local)
                remote_added = _merge_remote(source_conn, target_path)
            with sqlite3.connect(target_path) as target_conn:
                target_conn.execute("INSERT OR REPLACE INTO meta(key,value) VALUES (?,?)", (marker, source_path.name))
            summary["local_items"] += local_added
            summary["remote_items"] += remote_added
            summary["source_count"] += 1
            successful_fingerprints.append(fingerprint)
        except (OSError, sqlite3.DatabaseError, ValueError, json.JSONDecodeError) as error:
            summary["warnings"].append(type(error).__name__)
            # A source may fail after some of its tables were merged. Restore
            # the exact pre-source snapshot so a broken old DB cannot partly
            # refresh or overwrite an otherwise healthy target library.
            try:
                with sqlite3.connect(target_path) as target_conn:
                    rollback.backup(target_conn)
            except (OSError, sqlite3.DatabaseError) as rollback_error:
                summary["warnings"].append(f"Rollback{type(rollback_error).__name__}")
        finally:
            rollback.close()

    summary["id"] = hashlib.sha256("".join(successful_fingerprints).encode("ascii")).hexdigest()[:16]
    with sqlite3.connect(target_path) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO meta(key,value) VALUES ('last_legacy_migration',?)",
            (json.dumps(summary, ensure_ascii=False),),
        )
    return summary
