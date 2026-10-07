"""Atomic local research records; only lightweight summaries stay in memory."""

import copy
import json
import re
import threading
from pathlib import Path


FINISHED = {"ready", "failed", "cancelled"}
FIELDS = {"job_id", "conversation_id", "space_id", "created_at", "snapshot", "research_focus", "question",
          "web_enabled", "status", "phase", "message", "materials", "elapsed", "error", "document", "activity",
          "coverage", "external_sources", "web_errors", "usage", "cost_usd"}


class ResearchHistory:
    def __init__(self, root):
        self.root = Path(root)
        self.index = None
        self.lock = threading.RLock()

    def path(self, identity):
        if not isinstance(identity, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", identity):
            raise ValueError("研究任务标识无效")
        return self.root / (identity + ".json")

    @staticmethod
    def summary(job):
        return {"job_id": job["job_id"], "conversation_id": job.get("conversation_id", job["job_id"]),
                "space_id": job["space_id"], "created_at": job.get("created_at", 0), "status": job["status"],
                "title": (job["question"] or job["snapshot"]["name"])[:60]}

    def records(self, space_id=None):
        with self.lock:
            if self.index is None:
                self.index = {}
                for path in self.root.glob("*.json"):
                    job = self.load(path.stem)
                    if job:
                        self.index[job["job_id"]] = self.summary(job)
            return sorted((row for row in self.index.values() if space_id is None or row["space_id"] == space_id),
                          key=lambda row: row["created_at"])

    def load(self, identity):
        try:
            job = json.loads(self.path(identity).read_text(encoding="utf-8"))
            if job["job_id"] != identity or not isinstance(job["materials"], list):
                return None
            self.summary(job)
        except (OSError, ValueError, KeyError, TypeError):
            return None
        if job["status"] not in FINISHED:
            job.update(status="failed", phase="failed", error="软件已重启，上次研究已中断；已保存资料保留，可重试")
        return job

    def save(self, job):
        # Snapshot, materials and answers contain public research content, never worker credentials.
        saved = {key: copy.deepcopy(value) for key, value in job.items() if key in FIELDS}
        saved["snapshot"] = {key: value for key, value in saved["snapshot"].items()
                             if key in {"id", "name", "description", "items", "archived"}}
        with self.lock:
            self.records()
            self.root.mkdir(parents=True, exist_ok=True)
            target = self.path(saved["job_id"])
            temporary = target.with_suffix(".tmp")
            temporary.write_text(json.dumps(saved, ensure_ascii=False), encoding="utf-8")
            temporary.replace(target)
            self.index[saved["job_id"]] = self.summary(saved)

    def remove_space(self, space_id):
        with self.lock:
            for row in self.records(space_id):
                self.path(row["job_id"]).unlink(missing_ok=True)
                self.index.pop(row["job_id"], None)
