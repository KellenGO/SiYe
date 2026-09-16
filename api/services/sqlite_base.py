# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
# This file is part of MediaCrawler project.
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

"""两个 SQLite 存储层共用的底座。

`library_store`（本地收藏库）与 `remote_favorites_store`（跨平台收藏归档）写的是
**同一个**稳定系统目录下的 `library.db`，但历史上各自复制了一份 `_now()` / `default_db_path()`
/ `_conn()` / 字段白名单。这里把它们收成一份，避免改一处漏一处。

只放"两边确实一样"的东西：建库、连接、事务、时间戳、结果字段白名单。
各自表结构的差异留在各自的 `_SCHEMA` 里。
"""

from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Optional, Tuple

from base.runtime_paths import library_data_root

#: 结果的公开字段白名单：只持久化这些，避免把内部/嵌套字段写进库里。
#: 两个表共用同一套列名（收藏同步表额外自己拼 collection_names）。
RESULT_FIELDS: Tuple[str, ...] = (
    "platform",
    "content_id",
    "content_type",
    "title",
    "snippet",
    "author",
    "url",
    "published_at",
    "cover_url",
)


def default_db_path() -> Path:
    """统一收藏库位置；源码与不同版本 EXE 共用。"""
    return library_data_root() / "library.db"


def utc_now() -> str:
    """统一的 ISO-8601 UTC 时间戳（所有落库时间都走这里）。"""
    return datetime.now(timezone.utc).isoformat()


class SqliteStoreBase:
    """短连接 + 线程内复用的 SQLite 底座。

    每条连接都是短命的：SQLite 支持多连接，配合 WAL 足以应付
    "前端并发点击 + 后台同步写库"这种低并发场景，也避免跨线程复用连接。

    子类只需给出 `_SCHEMA`，需要额外初始化时覆写 `_bootstrap()`。
    """

    #: 子类的建表脚本。
    _SCHEMA = ""

    #: 是否开启外键约束（只有带 REFERENCES 的表需要）。
    _FOREIGN_KEYS = False

    def __init__(self, db_path: Optional[Path] = None) -> None:
        self.db_path = Path(db_path) if db_path else default_db_path()
        self._connections = threading.local()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as conn:
            self._bootstrap(conn)

    def _bootstrap(self, conn: sqlite3.Connection) -> None:
        """建表。子类可覆写以追加初始化（如写入版本号）。"""
        conn.executescript(self._SCHEMA)

    @contextmanager
    def _conn(self, write: bool = False) -> Iterator[sqlite3.Connection]:
        """开/复用一条连接。

        ``write=True`` 时用 ``BEGIN IMMEDIATE`` 立即取写锁，
        把"读-改-写"之间的竞争窗口关掉。
        """
        current = getattr(self._connections, "current", None)
        if current is not None:
            yield current
            return
        conn = sqlite3.connect(str(self.db_path), timeout=10)
        try:
            conn.row_factory = sqlite3.Row
            if self._FOREIGN_KEYS:
                conn.execute("PRAGMA foreign_keys = ON")
            # journal_mode 是数据库级持久设置，只在建库时设一次即可，
            # 每条连接都设等于每次都要抢一次写锁。
            if write:
                conn.execute("BEGIN IMMEDIATE")
            self._connections.current = conn
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            self._connections.current = None
            conn.close()

    @staticmethod
    def _enable_wal(conn: sqlite3.Connection) -> None:
        """在建库阶段把库切到 WAL（只需成功一次，之后是持久属性）。"""
        conn.execute("PRAGMA journal_mode = WAL")


_instance_lock = threading.Lock()


def singleton_store(factory):
    """把一个"无参工厂"包装成进程内单例（带锁，供 FastAPI 依赖注入用）。

    两个 store 都需要"整个进程共用一个实例"，且都要能在测试里 reset。
    """
    state = {"store": None}

    def getter():
        if state["store"] is None:
            with _instance_lock:
                if state["store"] is None:
                    state["store"] = factory()
        return state["store"]

    def reset() -> None:
        with _instance_lock:
            state["store"] = None

    return getter, reset
