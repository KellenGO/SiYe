"""Account-scoped remote archive state; never mutates the local library tables."""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone, timedelta

from .sqlite_base import utc_now

# 还没有确认平台账号时，归档挂在这个键下（历史上所有同步结果都在这儿）。
# 常量放这里是为了让 mixin 自己也能用，同时避免和 remote_favorites_store 互相 import。
DEFAULT_ACCOUNT_KEY = "default"


class SyncStateError(ValueError):
    """归档落库时的**可预期**冲突（重复页、跨页重复条目、扫描不完整）。

    单独一个类型是为了让上层能把它和"代码 bug 抛出的 ValueError"区分开：
    前者该翻译成"列表变化，请重新同步"，后者要原样暴露。
    """


SYNC_SCHEMA = """
CREATE TABLE IF NOT EXISTS remote_accounts (
 account TEXT PRIMARY KEY, platform TEXT NOT NULL, last_full TEXT,
 version INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL DEFAULT 'cancelled',
 updated_at TEXT NOT NULL, error TEXT, result_count INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS remote_scans (
 id TEXT PRIMARY KEY, account TEXT NOT NULL, mode TEXT NOT NULL,
 folders TEXT NOT NULL, complete INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_remote_scans_account ON remote_scans(account);
CREATE TABLE IF NOT EXISTS remote_checkpoints (
 account TEXT NOT NULL, folder TEXT NOT NULL, scan TEXT NOT NULL,
 page INTEGER NOT NULL, fingerprint TEXT NOT NULL, complete INTEGER NOT NULL,
 PRIMARY KEY(account, folder)
);
CREATE TABLE IF NOT EXISTS remote_memberships (
 account TEXT NOT NULL, folder TEXT NOT NULL, content_id TEXT NOT NULL,
 name TEXT NOT NULL, scan TEXT NOT NULL,
 PRIMARY KEY(account, folder, content_id)
);
CREATE INDEX IF NOT EXISTS idx_remote_memberships_content
 ON remote_memberships(account, content_id);
CREATE TABLE IF NOT EXISTS remote_presence (
 account TEXT NOT NULL, content_id TEXT NOT NULL,
 state TEXT NOT NULL DEFAULT 'present', missing_batch TEXT,
 PRIMARY KEY(account, content_id)
);
CREATE INDEX IF NOT EXISTS idx_remote_presence_state ON remote_presence(state, account);
CREATE TABLE IF NOT EXISTS remote_page_observations (
 account TEXT NOT NULL, folder TEXT NOT NULL, scan TEXT NOT NULL,
 page INTEGER NOT NULL, fingerprint TEXT NOT NULL,
 PRIMARY KEY(account,folder,scan,page)
);
CREATE TABLE IF NOT EXISTS remote_observed_items (
 account TEXT NOT NULL, folder TEXT NOT NULL, content_id TEXT NOT NULL,
 position INTEGER NOT NULL, favorite_time TEXT NOT NULL,
 PRIMARY KEY(account,folder,content_id)
);
CREATE TABLE IF NOT EXISTS remote_baseline (
 account TEXT NOT NULL, folder TEXT NOT NULL, content_id TEXT NOT NULL,
 position INTEGER NOT NULL, favorite_time TEXT NOT NULL,
 PRIMARY KEY(account,folder,content_id)
);
"""


class RemoteSyncStateMixin:
    def begin_scan(self, platform, account, folders, mode):
        """Resume import only; reconciliation always starts a fresh observation."""
        encoded = json.dumps(folders, sort_keys=True, ensure_ascii=False)
        with self._conn(write=True) as conn:
            old = conn.execute("SELECT * FROM remote_scans WHERE account=? AND complete=0 ORDER BY rowid DESC LIMIT 1", (account,)).fetchone()
            if mode == "auto" and old and old["mode"] == "auto" and old["folders"] == encoded:
                scan = old["id"]
            else:
                scan = uuid.uuid4().hex
                conn.execute("DELETE FROM remote_scans WHERE account=?", (account,))
                conn.execute("DELETE FROM remote_checkpoints WHERE account=?", (account,))
                conn.execute("DELETE FROM remote_page_observations WHERE account=?", (account,))
                conn.execute("DELETE FROM remote_observed_items WHERE account=?", (account,))
                conn.execute("INSERT INTO remote_scans VALUES(?,?,?,?,0)", (scan, account, mode, encoded))
            conn.execute("INSERT INTO remote_accounts(account,platform,updated_at) VALUES(?,?,?) ON CONFLICT(account) DO NOTHING", (account, platform, utc_now()))
            conn.execute("UPDATE remote_accounts SET status='running', error=NULL, result_count=0, updated_at=? WHERE account=?", (utc_now(), account))
        return scan

    def checkpoint(self, account, folder):
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM remote_checkpoints WHERE account=? AND folder=?", (account, folder)).fetchone()
            return dict(row) if row else None

    def restart_folder(self, account, folder):
        with self._conn(write=True) as conn:
            conn.execute("DELETE FROM remote_checkpoints WHERE account=? AND folder=?", (account, folder))
            conn.execute("DELETE FROM remote_page_observations WHERE account=? AND folder=?", (account, folder))
            conn.execute("UPDATE remote_memberships SET scan='' WHERE account=? AND folder=?", (account, folder))
            conn.execute("DELETE FROM remote_observed_items WHERE account=? AND folder=?", (account, folder))
            # Old memberships remain until an authoritative complete scan.

    def baseline_overlap(self, account, folder, identities):
        """本页里「和本机基线完全对得上」的最长连续段，返回 (起, 止) 位置；没有则 None。

        认出一条"早就在本机"的条目只有三个条件同时成立：内容 ID 在基线里、收藏时间一致、
        位置也接得上。任意一条对不上就在那里断开——断点之后可能藏着新增，不能据此停止翻页。
        调用方再按段长决定停不停（见 `INCREMENTAL_STOP_RUN`）。
        """
        if not identities or any(stamp is None for _, stamp in identities):
            return None
        best = None
        run_start = run_end = None
        with self._conn() as conn:
            for cid, stamp in identities:
                row = conn.execute("SELECT position,favorite_time FROM remote_baseline WHERE account=? AND folder=? AND content_id=?", (account, folder, cid)).fetchone()
                if not row or row["favorite_time"] != str(stamp):
                    run_start = run_end = None
                    continue
                position = row["position"]
                run_end = position if (run_end is not None and position == run_end + 1) else None
                if run_end is None:
                    run_start = run_end = position
                if best is None or run_end - run_start > best[1] - best[0]:
                    best = (run_start, run_end)
        return best

    def head_fingerprint(self, account, folder, scan):
        with self._conn() as conn:
            row = conn.execute("SELECT fingerprint FROM remote_page_observations WHERE account=? AND folder=? AND scan=? AND page=1", (account, folder, scan)).fetchone()
            return row[0] if row else None

    def save_sync_page(self, platform, account, scan, folder, name, page, fingerprint, results, complete, page_size, identities=None):
        """落一页。`page_size` 由调用方给（分页大小是平台侧的事，这里不做假设）。"""
        with self._conn(write=True) as conn:
            duplicate = conn.execute("SELECT 1 FROM remote_page_observations WHERE account=? AND folder=? AND scan=? AND fingerprint=? AND page<>?", (account, folder, scan, fingerprint, page)).fetchone()
            if duplicate and results:
                raise SyncStateError("Repeated favorite page")
            for index, (cid, stamp) in enumerate(identities or [(r["content_id"], None) for r in results]):
                position = (page - 1) * page_size + index
                old = conn.execute("SELECT position FROM remote_observed_items WHERE account=? AND folder=? AND content_id=?", (account, folder, cid)).fetchone()
                if old and old[0] != position:
                    raise SyncStateError("Favorite item repeated across pages")
                conn.execute("INSERT OR REPLACE INTO remote_observed_items VALUES(?,?,?,?,?)", (account, folder, cid, position, str(stamp)))
            self.save_platform(platform, results, status="running", account_key=account, record_run=False)
            for result in results:
                cid = result["content_id"]
                conn.execute("INSERT INTO remote_presence VALUES(?,?,'present',NULL) ON CONFLICT(account,content_id) DO UPDATE SET state='present',missing_batch=NULL", (account, cid))
                conn.execute("INSERT INTO remote_memberships VALUES(?,?,?,?,?) ON CONFLICT(account,folder,content_id) DO UPDATE SET name=excluded.name,scan=excluded.scan", (account, folder, cid, name, scan))
            conn.execute("INSERT INTO remote_checkpoints VALUES(?,?,?,?,?,?) ON CONFLICT(account,folder) DO UPDATE SET scan=excluded.scan,page=excluded.page,fingerprint=excluded.fingerprint,complete=excluded.complete", (account, folder, scan, page, fingerprint, int(complete)))
            conn.execute("INSERT OR REPLACE INTO remote_page_observations VALUES(?,?,?,?,?)", (account, folder, scan, page, fingerprint))
            conn.execute("UPDATE remote_accounts SET version=version+1,result_count=result_count+?,updated_at=? WHERE account=?", (len(results), utc_now(), account))

    def finish_scan(self, account, scan, reconcile):
        with self._conn(write=True) as conn:
            run = conn.execute("SELECT * FROM remote_scans WHERE id=? AND account=?", (scan, account)).fetchone()
            if not run:
                raise SyncStateError("Unknown scan")
            folders = json.loads(run["folders"])
            finished = conn.execute("SELECT count(*) FROM remote_checkpoints WHERE account=? AND scan=? AND complete=1", (account, scan)).fetchone()[0]
            if finished != len(folders):
                raise SyncStateError("Incomplete scan")
            # 「完整重扫」扫到了整份列表，才有资格清理旧的收藏夹归属；
            # 增量只走到头部就停，没看到的旧归属一律留着（未取到的旧内容不删）。
            if reconcile:
                conn.execute("DELETE FROM remote_memberships WHERE account=? AND scan<>?", (account, scan))
                conn.execute("UPDATE remote_accounts SET last_full=? WHERE account=?", (utc_now(), account))
            # Refresh the ordered baseline in SQLite, without loading the library
            # into Python. Keep the unvisited suffix after an incremental stop.
            conn.execute("CREATE TEMP TABLE next_remote_baseline AS SELECT * FROM remote_baseline WHERE 0")
            conn.execute("""INSERT INTO next_remote_baseline
                SELECT account,folder,content_id,
                       row_number() OVER(PARTITION BY account,folder ORDER BY section,position)-1,
                       favorite_time
                FROM (
                    SELECT *,0 AS section FROM remote_observed_items WHERE account=?
                    UNION ALL
                    SELECT b.*,1 AS section FROM remote_baseline b WHERE b.account=? AND ?=0
                    AND NOT EXISTS(SELECT 1 FROM remote_observed_items o
                        WHERE o.account=b.account AND o.folder=b.folder AND o.content_id=b.content_id)
                )""", (account, account, int(reconcile)))
            conn.execute("DELETE FROM remote_baseline WHERE account=?", (account,))
            conn.execute("INSERT INTO remote_baseline SELECT * FROM next_remote_baseline")
            conn.execute("DROP TABLE next_remote_baseline")
            conn.execute("UPDATE remote_scans SET complete=1 WHERE id=?", (scan,))
            conn.execute("UPDATE remote_accounts SET status='succeeded',version=version+1,updated_at=? WHERE account=?", (utc_now(), account))

    def sync_status(self, account, status, error=None):
        with self._conn(write=True) as conn:
            conn.execute("UPDATE remote_accounts SET status=?,error=?,updated_at=?,version=version+1 WHERE account=?", (status, error, utc_now(), account))

    def archive_page(self, *, platform=None, state=None, account=None, offset=0, limit=50):
        where, args = ["1=1"], []
        for clause, value in (("r.platform=?", platform), ("r.account_key=?", account), ("COALESCE(p.state,'legacy')=?", state)):
            if value:
                where.append(clause)
                args.append(value)
        source = " FROM remote_favorites r LEFT JOIN remote_presence p ON p.account=r.account_key AND p.content_id=r.content_id WHERE " + " AND ".join(where)
        with self._conn() as conn:
            total = conn.execute("SELECT count(*)" + source, args).fetchone()[0]
            # 按平台分组、组内最近入库在前：翻页时同一个平台是连续的，不会跨平台乱跳。
            # 想只看某一个平台，用平台筛选把范围收窄（分页也随之限定在该平台内）。
            rows = conn.execute(
                "SELECT r.*,COALESCE(p.state,'legacy') AS presence,p.missing_batch" + source
                + " ORDER BY r.platform ASC, r.id DESC LIMIT ? OFFSET ?", (*args, limit, offset)
            ).fetchall()
            items = []
            for row in rows:
                result = self._row_to_result(row)
                if row["presence"] != "legacy":
                    result["collection_names"] = [r[0] for r in conn.execute("SELECT DISTINCT name FROM remote_memberships WHERE account=? AND content_id=?", (row["account_key"], row["content_id"]))]
                items.append({"id": row["id"], "account": row["account_key"], "state": row["presence"], "missing_batch": row["missing_batch"], "result": result})
        return {"items": items, "total": total, "offset": offset, "limit": limit}

    def archive_summary(self):
        with self._conn() as conn:
            counts = {r[0]: r[1] for r in conn.execute("SELECT platform,count(*) FROM remote_favorites GROUP BY platform")}
            accounts = []
            for row in conn.execute("SELECT * FROM remote_accounts ORDER BY account"):
                value = dict(row)
                # last_full 是本机自己写的 ISO 字符串；脏数据不该让整页摘要取不出来。
                try:
                    due = not row["last_full"] or datetime.fromisoformat(row["last_full"]) < datetime.now(timezone.utc) - timedelta(days=30)
                except (TypeError, ValueError):
                    due = True
                value["full_due"] = due
                value["legacy"] = row["account"] == DEFAULT_ACCOUNT_KEY
                accounts.append(value)
            # 归档列表默认只铺"平台中已找到"和"待确认"，这里给出各类计数供界面做汇总。
            states = {state: 0 for state in ("present", "pending", "archived", "legacy")}
            for row in conn.execute(
                "SELECT COALESCE(p.state,'legacy') AS state, count(*) AS n "
                "FROM remote_favorites r LEFT JOIN remote_presence p "
                "ON p.account=r.account_key AND p.content_id=r.content_id GROUP BY 1"
            ):
                states[row["state"]] = int(row["n"])
            pending = conn.execute("SELECT count(*) FROM remote_presence WHERE state='pending'").fetchone()[0]
            runs = conn.execute("SELECT * FROM remote_sync_runs WHERE id IN (SELECT max(id) FROM remote_sync_runs GROUP BY platform)").fetchall()
            platforms = {r["platform"]: {"status": r["status"] if r["status"] != "running" else "cancelled", "result_count": r["result_count"], "synced_at": r["finished_at"], "error_summary": r["error_summary"]} for r in runs}
            for account in accounts:
                previous = platforms.get(account["platform"])
                if not previous or account["updated_at"] >= previous["synced_at"]:
                    platforms[account["platform"]] = {
                        "status": account["status"] if account["status"] != "running" else "cancelled",
                        "result_count": account["result_count"], "synced_at": account["updated_at"],
                        "error_summary": account["error"], "account": account["account"],
                    }
        return {"counts": counts, "accounts": accounts, "pending_count": pending, "states": states,
                "platforms": platforms,
                "data_version": ":".join([str(sum(counts.values())), *[str(a["version"]) for a in accounts], *[str(r["id"]) for r in runs]])}
