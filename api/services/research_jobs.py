# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
# This file is part of MediaCrawler project.
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

"""One local research task at a time, with explicit source-gap confirmation."""

import asyncio
import copy
import json
import os
import subprocess
import sys
import tempfile
import time
import threading
import uuid
from pathlib import Path

from base.runtime_paths import application_root, library_data_root
from .accounts import operation_coordinator, get_session_snapshot, ensure_session_snapshot
from .research_config import research_config, runtime_status
from .platform_materials import material, body_from_reading
from .acquisition_diagnostics import classify
from .research_history import ResearchHistory
from .worker_process import terminate_worker, windows_process_job

ACTIVE_SECONDS = 600
MAX_FINISHED_JOBS = 10
MAX_RETAINED_BYTES = 32 * 1024 * 1024
MAX_PARTIAL_CHARS = 40000

#: 发给研究子进程的 payload 上限。必须**小于**子进程侧的 readline 上限
#: （`research_worker.py` 是 16MB），留出 JSON 转义与行尾的空间。
MAX_PAYLOAD_BYTES = 12 * 1024 * 1024
#: 超限时每节正文压到多少字符（`material_contract.MAX_SECTION_CHARS` 是 256k）。
TRIMMED_SECTION_CHARS = 20000


def _payload_size(payload) -> int:
    return len(json.dumps(payload, ensure_ascii=False).encode("utf-8"))


def fit_payload(payload):
    """把 payload 压到 `MAX_PAYLOAD_BYTES` 以内，返回 `(payload, trimmed)`。

    子进程用 `readline(16MB)` 读一整行 JSON；超限会被静默截断，表现为
    「研究服务未能完成，请检查配置或重试」—— 用户和开发者都无从下手。
    这里按"正文截断 → 丢弃证据缓存 → 丢弃对话历史"的次序降级，
    保证最重要的资料结构还在。
    """
    if _payload_size(payload) <= MAX_PAYLOAD_BYTES:
        return payload, False

    trimmed = copy.deepcopy(payload)
    # 1) 压缩每条资料的各节正文。
    for row in trimmed.get("materials") or []:
        sections = row.get("sections")
        if isinstance(sections, dict):
            for key, value in list(sections.items()):
                if isinstance(value, str) and len(value) > TRIMMED_SECTION_CHARS:
                    sections[key] = value[:TRIMMED_SECTION_CHARS]
    if _payload_size(trimmed) <= MAX_PAYLOAD_BYTES:
        return trimmed, True

    # 2) 证据缓存只用于复用，丢掉不影响本次分析。
    trimmed["evidence_cache"] = None
    if _payload_size(trimmed) <= MAX_PAYLOAD_BYTES:
        return trimmed, True

    # 3) 对话历史从最新往回保留，直到装得下。
    history = trimmed.get("conversation") or []
    while history and _payload_size(trimmed) > MAX_PAYLOAD_BYTES:
        history = history[1:]
        trimmed["conversation"] = history
    return trimmed, True


def task_command(mode="collect"):
    if mode != "collect":
        from .extensions import extensions
        return extensions.command()
    return [sys.executable, "--research-worker"] if getattr(sys, "frozen", False) else [sys.executable, "-m", "api.services.research_worker"]


def task_environment(workdir, credentials=None):
    env = {key: value for key, value in os.environ.items() if not key.startswith(("ANTHROPIC_", "CLAUDE_")) and key not in {"CLAUDECODE", "SIYE_RESEARCH_API_KEY"}}
    env.update(PYINSTALLER_RESET_ENVIRONMENT="1", PYTHONUTF8="1", PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1", CLAUDE_CONFIG_DIR=str(Path(workdir) / "claude"),
               CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC="1")
    if credentials:
        if credentials.get("protocol", "anthropic") == "openai":
            env.update(SIYE_RESEARCH_API_KEY=credentials["api_key"])
        else:
            env.update(ANTHROPIC_API_KEY=credentials["api_key"], ANTHROPIC_BASE_URL=credentials["base_url"])
    return env


class ResearchJobs:
    def __init__(self, config=research_config):
        self.config = config
        self.jobs = {}
        self.tasks = {}
        self.active = None
        self.lock = asyncio.Lock()
        self._cache_lock = threading.RLock()
        self.probes = set()
        self.history_store = ResearchHistory(config.path.parent / "research-history")

    def get(self, job_id):
        # Polling runs in FastAPI's thread pool; it may load history while an
        # async task prunes completed jobs. Keep insertion and pruning atomic.
        with self._cache_lock:
            if job_id not in self.jobs:
                saved = self.history_store.load(job_id)
                if not saved:
                    raise ValueError("研究任务不存在")
                self.jobs[job_id] = saved
                self.prune()
            return self.jobs[job_id]

    def public(self, job_id, store=None, job=None):
        job = job if job is not None else self.get(job_id)
        visible = {key: copy.deepcopy(value) for key, value in job.items() if key not in {"snapshot", "elapsed", "workdir", "materials", "history", "research_focus", "transcribing_key", "evidence_cache"}}
        visible["total_materials"] = len(job["snapshot"]["items"])
        visible["materials"] = []
        for row in job["materials"]:
            summary = {key: row[key] for key in ("key", "platform", "title", "url")}
            for name in ("body", "comments", "subtitles", "ocr"):
                values = row.get(name) or {"state": "not_applicable", "reason": "", "truncated": False, "entries": []}
                summary[name] = {key: values[key] for key in ("state", "reason", "truncated")}
                summary[name]["count"] = len(values["entries"])
                summary[name]["collection_truncated"] = bool(values["truncated"])
                if "sort" in values:
                    summary[name]["sort"] = values["sort"]
                if name == "subtitles":
                    summary[name]["metadata"] = {key: values.get("metadata", {})[key] for key in
                        ("source", "engine", "model", "language", "duration", "cached") if key in values.get("metadata", {})}
                if name == "ocr":
                    summary[name]["metadata"] = {key: values.get("metadata", {})[key] for key in
                        ("engine", "total", "processed", "recognized", "empty", "failed", "cached_images", "failures") if key in values.get("metadata", {})}
            visible["materials"].append(summary)
        if store and job.get("space_id"):
            current = store.get_space(job["space_id"])
            visible["stale_snapshot"] = current["items"] != job["snapshot"]["items"]
        return visible

    def prune(self):
        with self._cache_lock:
            self._prune_finished()

    def _prune_finished(self):
        retained, size = 0, 0
        for identity in reversed(list(self.jobs)):
            job = self.jobs[identity]
            if identity == self.active or job["status"] not in {"ready", "failed", "cancelled"}:
                continue
            task = self.tasks.get(identity)
            if task and not task.done():
                continue
            weight = len(json.dumps(job, ensure_ascii=False).encode("utf-8"))
            if retained and (retained >= MAX_FINISHED_JOBS or size + weight > MAX_RETAINED_BYTES):
                del self.jobs[identity]
                self.tasks.pop(identity, None)
            else:
                retained += 1
                size += weight

    async def claim(self, identity):
        async with self.lock:
            if self.active is not None:
                raise ValueError("已有研究任务正在运行或等待确认，请先完成或取消")
            self.active = identity
            self.prune()

    async def release(self, identity):
        async with self.lock:
            if self.active == identity:
                self.active = None

    def require_runtime(self):
        credentials = self.config.credentials()
        if credentials["protocol"] == "openai":
            return
        status = runtime_status()
        if not status["sdk_available"] or not status["cli_available"]:
            raise ValueError("AI 运行环境不完整，请使用包含 AI 运行程序的四野安装包")

    def conversation_jobs(self, space_id, conversation_id):
        records = {row["job_id"]: self.history_store.load(row["job_id"]) for row in self.history_store.records()
                   if row["conversation_id"] == conversation_id}
        records.update({job["job_id"]: job for job in list(self.jobs.values())
                        if job.get("conversation_id", job["job_id"]) == conversation_id})
        matches = sorted((job for job in records.values() if job), key=lambda job: job.get("created_at", 0))
        if not matches or any(job["space_id"] != space_id for job in matches):
            raise ValueError("会话不存在或不属于此空间，请新建会话")
        return matches

    def conversations(self, space_id):
        records = {row["job_id"]: row for row in self.history_store.records(space_id)}
        records.update({job["job_id"]: self.history_store.summary(job) for job in list(self.jobs.values()) if job["space_id"] == space_id})
        archived = self.history_store.flags()
        groups = {}
        for job in sorted(records.values(), key=lambda row: row["created_at"]):
            identity = job["conversation_id"]
            if identity not in groups:
                groups[identity] = {"id": identity, "title": job["title"], "turns": 0, "archived": bool(archived.get(identity))}
            groups[identity].update(status=job["status"], latest_job_id=job["job_id"],
                                    archived=bool(archived.get(identity)))
            groups[identity]["turns"] += 1
            groups[identity] = groups.pop(identity)
        return list(reversed(list(groups.values())))

    def archive_conversation(self, space_id, conversation_id, archived):
        self.conversation_jobs(space_id, conversation_id)
        self.history_store.set_archived(conversation_id, archived)
        return {"id": conversation_id, "archived": bool(archived)}

    def latest(self, space_id, store=None):
        groups = self.conversations(space_id)
        return self.public(groups[0]["latest_job_id"], store) if groups else None

    def persist(self, identity):
        """把任务快照落盘（同步接口，供事件循环外/短路径调用）。

        `save()` 是 `json.dumps` + `write_text`，单 job 上限 32MB —— 直接在事件
        循环里跑会把**所有**接口一起卡住（含各平台搜索轮询）。所以调用点优先用
        `persist_async`；这里保留同步版本给已在工作线程里的路径。

        另外以前只 `except OSError`：`json.dumps` 遇到不可序列化对象会抛
        `TypeError`，整条研究历史就此丢失且没有任何提示。现在一并兜住。
        """
        try:
            self.history_store.save(self.jobs[identity])
        except (OSError, TypeError, ValueError):
            warning = "会话历史未能保存到本机，请检查磁盘空间或文件权限"
            errors = self.jobs[identity].setdefault("web_errors", [])
            if warning not in errors:
                errors.append(warning)

    async def persist_async(self, identity, job=None):
        """在事件循环里调用：把落盘挪到工作线程，避免阻塞其它请求。"""
        if job is not None:
            # 调用方可能已经改过 job 内容，直接用它（避免线程里再取一次全局字典）。
            try:
                await asyncio.to_thread(self.history_store.save, job)
            except (OSError, TypeError, ValueError):
                warning = "会话历史未能保存到本机，请检查磁盘空间或文件权限"
                errors = job.setdefault("web_errors", [])
                if warning not in errors:
                    errors.append(warning)
            return
        await asyncio.to_thread(self.persist, identity)

    async def create(self, space, question, web_enabled, conversation_id=None, regenerate_job_id=None):
        if space["archived"]:
            raise ValueError("空间已归档，请先继续研究")
        if not space["items"] and not conversation_id:
            raise ValueError("请先向空间加入资料")
        self.require_runtime()
        previous = self.conversation_jobs(space["id"], conversation_id) if conversation_id else []
        if previous and previous[-1]["status"] != "ready" and not (regenerate_job_id and previous[-1]["status"] in {"failed", "cancelled"}):
            raise ValueError("请先完成当前会话的研究，或新建会话")
        latest = previous[-1] if previous else None
        if regenerate_job_id:
            target = next((row for row in previous if row["job_id"] == regenerate_job_id and row["status"] == "ready"), None)
            if target is None:
                raise ValueError("只能重做当前空间会话中已完成的回答")
            latest = target
            question = target["question"]
            previous = previous[:previous.index(target)]
        history = []
        for row in [row for row in previous if row["status"] == "ready"][-3:]:
            answer = self.document_text(row["document"])
            history.append({"question": row["question"], "answer": answer if len(answer) <= 3000 else answer[:2000] + "\n…\n" + answer[-1000:],
                            "answer_truncated": len(answer) > 3000})
        identity = uuid.uuid4().hex
        await self.claim(identity)
        self.jobs[identity] = {"job_id": identity, "space_id": space["id"], "snapshot": copy.deepcopy(latest["snapshot"] if latest else space),
            "conversation_id": conversation_id or identity, "history": history, "created_at": time.time_ns(),
            "research_focus": (latest.get("research_focus") or latest["question"]) if latest else question,
            "question": question, "web_enabled": web_enabled, "status": "collecting", "phase": "collecting",
            "message": "正在获取空间资料", "materials": [], "elapsed": 0.0, "error": "",
            "document": None, "partial": "", "activity": [], "coverage": [], "external_sources": [], "web_errors": [],
            "usage": None, "cost_usd": None, "context_tokens": 0}
        needs_ocr = False
        if latest:
            self.jobs[identity].update(materials=copy.deepcopy(latest["materials"]), status="analyzing", phase="analyzing", message="正在继续分析当前会话资料")
            # Old history without an explicit type can still recover it from its frozen snapshot.
            types = {row["key"]: row["result"].get("content_type", "") for row in latest["snapshot"]["items"]}
            for row in self.jobs[identity]["materials"]:
                row.setdefault("content_type", types.get(row["key"], ""))
                if row["platform"] == "xhs" and row.get("content_type") not in {"video", "zvideo"} and row.get("ocr", {}).get("state") not in {"ok", "not_applicable"}:
                    needs_ocr = True
            self.jobs[identity]["evidence_cache"] = copy.deepcopy(latest.get("evidence_cache"))
            if needs_ocr:
                self.jobs[identity].update(status="collecting", phase="collecting", message="正在补取原帖图片文字")
        await self.persist_async(identity)
        self.tasks[identity] = asyncio.create_task(self.collect(identity, retry=True) if needs_ocr else self.analyze(identity) if latest else self.collect(identity))
        self.tasks[identity].add_done_callback(lambda _: self.prune())
        return self.public(identity)

    @classmethod
    def document_text(cls, node):
        if not isinstance(node, dict):
            return ""
        if node.get("type") == "text":
            text = node.get("text", "")
            link = next((mark for mark in node.get("marks", []) if mark.get("type") == "link"), None)
            return f"{text} ({link['attrs']['href']})" if link else text
        separator = "" if node.get("type") in {"paragraph", "heading"} else "\n"
        return separator.join(cls.document_text(child) for child in node.get("content", []))

    async def process(self, job, payload, credentials=None):
        root = library_data_root() / "research-tmp"
        root.mkdir(parents=True, exist_ok=True)
        proc, handle, drain = None, None, None
        temporary = tempfile.TemporaryDirectory(prefix="task-", dir=root)
        started = time.monotonic()
        try:
            workdir = temporary.name
            payload.update(workdir=workdir)
            if credentials:
                payload.update(model=credentials["model"], base_url=credentials["base_url"], protocol=credentials.get("protocol", "anthropic"))
            proc = await asyncio.create_subprocess_exec(*task_command(payload["mode"]), cwd=str(application_root()),
                env=task_environment(workdir, credentials), stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, limit=16 * 1024 * 1024,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            handle = windows_process_job(proc)
            async def discard_stderr():
                while await proc.stderr.read(8192):
                    pass
            drain = asyncio.create_task(discard_stderr())
            async with asyncio.timeout(max(1, ACTIVE_SECONDS - job["elapsed"])):
                # payload 是**一行** JSON，子进程用 readline(16MB) 读：整包超限
                # 会被截断 → json 解析失败 → 用户只看到"研究服务未能完成"，
                # 没有任何可排查线索。发送前先压到上限内。
                payload, trimmed = fit_payload(payload)
                if trimmed:
                    job.setdefault("web_errors", []).append(
                        "资料体积超过单次处理上限，已按正文长度截断后再分析")
                proc.stdin.write((json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8"))
                await proc.stdin.drain()
                proc.stdin.close()
                result = None
                async for line in proc.stdout:
                    try:
                        event = json.loads(line)
                    except ValueError:
                        continue
                    kind = event.get("type")
                    if kind == "material":
                        item = event["material"]
                        if item["key"] not in {row["key"] for row in job["snapshot"]["items"]}:
                            raise ValueError("研究资料范围异常")
                        job["materials"] = [row for row in job["materials"] if row["key"] != item["key"]] + [item]
                        if event.get("completed") and job.get("transcribing_key") == item["key"]:
                            job.pop("transcribing_key", None)
                        job["message"] = f"已获取 {len(job['materials'])} / {len(job['snapshot']['items'])} 条资料"
                        if event.get("completed") or event.get("checkpoint"):
                            await self.persist_async(job["job_id"], job)
                    elif kind == "progress":
                        job.update(phase=event["phase"], message=event["message"])
                        if event["phase"] == "transcribing":
                            job["transcribing_key"] = event.get("key")
                    elif kind == "activity":
                        records = job.setdefault("activity", [])
                        record = {key: event[key] for key in ("id", "tool", "message", "status", "summary", "kind") if key in event}
                        existing = next((row for row in records if record.get("id") and row.get("id") == record["id"]), None)
                        if existing is not None:
                            existing.update(record)
                        else:
                            records.append(record)
                        job["activity"] = records[-40:]
                    elif kind == "usage":
                        job.update(usage=event.get("usage"), cost_usd=event.get("cost_usd"))
                        if isinstance(event.get("context_tokens"), int):
                            job["context_tokens"] = event["context_tokens"]
                    elif kind == "delta":
                        text = event.get("text")
                        if isinstance(text, str) and text:
                            partial = job.get("partial") or ""
                            if len(partial) < MAX_PARTIAL_CHARS:
                                job["partial"] = partial + text[:MAX_PARTIAL_CHARS - len(partial)]
                    elif kind == "delta_reset":
                        job["partial"] = ""
                    elif kind == "done":
                        result = event["result"]
                    elif kind == "error":
                        raise ValueError(event["message"])
                await proc.wait()
                if proc.returncode or result is None:
                    raise ValueError("研究进程意外停止，已取得资料保留")
                return result
        finally:
            if handle is not None:
                handle.Close()
            await terminate_worker(proc)
            if drain:
                await asyncio.gather(drain, return_exceptions=True)
            job["elapsed"] += time.monotonic() - started
            temporary.cleanup()

    async def collect(self, identity, retry=False):
        job = self.get(identity)
        leased = False
        try:
            leased = await operation_coordinator.acquire_exclusive("research")
            if not leased:
                raise ValueError("平台正在搜索、同步或处理账号，请稍后重试获取")
            preparation_started = time.monotonic()
            sessions = {}
            session_errors = {}
            from .search_job_manager import search_job_manager
            for platform in {item["result"]["platform"] for item in job["snapshot"]["items"]}:
                await search_job_manager.stop_platform_worker(platform)
                sessions[platform] = get_session_snapshot(platform)
                if platform in {"xhs", "zhihu", "bilibili"} and (
                        not sessions[platform] or platform == "zhihu" and not sessions[platform].get("d_c0")):
                    try:
                        sessions[platform] = await asyncio.wait_for(ensure_session_snapshot(platform, raise_on_error=True), timeout=30)
                    except Exception as error:
                        code, status = classify(error, "session")
                        session_errors[platform] = {"code": code, "http_status": status}
            job["elapsed"] += time.monotonic() - preparation_started
            previous = {row["key"]: row for row in job["materials"]}
            items = job["snapshot"]["items"]
            from .reading import reading_service
            for item in items:
                source = item["result"]
                if source["platform"] != "zhihu" or source.get("content_type") not in {"answer", "article"}:
                    continue
                if previous.get(item["key"], {}).get("body", {}).get("state") == "ok":
                    continue
                try:
                    cached = reading_service.cached_zhihu_body(source["content_type"], source["content_id"], source["url"])
                except ValueError:
                    continue
                if cached:
                    row = previous.setdefault(item["key"], material(item))
                    row["body"] = body_from_reading(cached)
            if retry:
                items = [item for item in items if item["key"] not in previous or any(
                    previous[item["key"]].get(name, material(item).get(name, {})).get("state") in {"missing", "failed", "restricted"}
                    for name in ("body", "comments", "subtitles", "ocr"))]
            await self.process(job, {"mode": "collect", "items": items, "sessions": sessions,
                                     "session_errors": session_errors, "previous": previous})
            job.update(status="awaiting_sources", phase="awaiting_sources", message="请检查读取情况后开始分析", error="")
        except asyncio.CancelledError:
            job.update(status="cancelled", phase="cancelled", message="研究已取消")
            await self.release(identity)
            raise
        except Exception as error:
            job.update(status="failed", phase="failed", error=self.safe_error(error))
            await self.release(identity)
        finally:
            transcribing = job.pop("transcribing_key", None)
            if transcribing and job["status"] in {"failed", "cancelled"}:
                for row in job["materials"]:
                    if row["key"] == transcribing:
                        row["subtitles"].update(state="failed", reason="本地转写已中断，正文和评论保留，可重试")
            known = {row["key"] for row in job["materials"]}
            for item in job["snapshot"]["items"]:
                if item["key"] not in known:
                    missing = material(item)
                    for name in ("body", "comments", "subtitles", "ocr"):
                        if missing[name]["state"] != "not_applicable":
                            missing[name].update(state="failed", reason="本次尚未获取此资料，可重试")
                    job["materials"].append(missing)
            if leased:
                await operation_coordinator.release_exclusive("research")
            await self.persist_async(identity)

    @staticmethod
    def safe_error(error):
        if isinstance(error, (TimeoutError, asyncio.TimeoutError)):
            return "研究达到 10 分钟处理上限，已取得资料保留；可重新生成"
        return str(error) if isinstance(error, ValueError) else "研究未能完成，请重试"

    async def retry(self, identity):
        job = self.get(identity)
        if job["status"] not in {"awaiting_sources", "failed"}:
            raise ValueError("当前阶段不能重试资料获取")
        if job["elapsed"] >= ACTIVE_SECONDS:
            raise ValueError("处理时间已用完，请新建研究任务")
        if self.active != identity:
            await self.claim(identity)
        job.update(status="collecting", phase="collecting", error="")
        await self.persist_async(identity)
        self.tasks[identity] = asyncio.create_task(self.collect(identity, retry=True))
        self.tasks[identity].add_done_callback(lambda _: self.prune())
        return self.public(identity)

    async def generate(self, identity, archived=False):
        job = self.get(identity)
        if archived or job["status"] != "awaiting_sources":
            raise ValueError("请先检查资料读取情况，归档空间不能生成")
        if self.active != identity:
            await self.claim(identity)
        job.update(status="analyzing", phase="analyzing", error="", message="AI 正在整理研究笔记")
        await self.persist_async(identity)
        self.tasks[identity] = asyncio.create_task(self.analyze(identity))
        self.tasks[identity].add_done_callback(lambda _: self.prune())
        return self.public(identity)

    async def analyze(self, identity):
        job = self.get(identity)
        try:
            result = await self.process(job, {"mode": "analyze", "materials": copy.deepcopy(job["materials"]),
                "space_name": job["snapshot"]["name"], "description": job["snapshot"]["description"],
                "question": job["question"], "research_focus": job.get("research_focus", ""),
                "web_enabled": job["web_enabled"], "conversation": job.get("history", []),
                "evidence_cache": copy.deepcopy(job.get("evidence_cache")), "stream": True}, self.config.credentials())
            from .space_notes import validate_note
            if result.get("document") is not None:
                validate_note(result["document"])
            job.update({key: value for key, value in result.items() if key in {"document", "coverage", "external_sources", "web_errors", "evidence_cache"}})
            job.update(status="ready", phase="ready", message="笔记已生成，请预览后追加")
        except asyncio.CancelledError:
            job.update(status="cancelled", phase="cancelled", message="研究已取消")
            raise
        except Exception as error:
            job.update(status="failed", phase="failed", error=self.safe_error(error))
        finally:
            job["partial"] = ""
            for record in job.get("activity", []):
                if record.get("status") == "running":
                    record.update(status="cancelled" if job["status"] == "cancelled" else "failed", summary="执行已中断")
            await self.release(identity)
            await self.persist_async(identity)

    async def cancel(self, identity):
        job = self.get(identity)
        task = self.tasks.get(identity)
        if task and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        if job["status"] not in {"ready", "failed", "cancelled"}:
            job.update(status="cancelled", phase="cancelled", message="研究已取消")
        job["partial"] = ""
        await self.release(identity)
        await self.persist_async(identity)
        return self.public(identity)

    def remove_space(self, space_id):
        self.history_store.remove_space(space_id)
        for identity, job in list(self.jobs.items()):
            if job.get("space_id") == space_id:
                self.jobs.pop(identity)
                self.tasks.pop(identity, None)

    async def cancel_space(self, space_id):
        for identity, job in list(self.jobs.items()):
            if identity in self.jobs and job.get("space_id") == space_id:
                await self.cancel(identity)

    async def probe(self, web_enabled):
        self.require_runtime()
        identity = "probe-" + uuid.uuid4().hex
        await self.claim(identity)
        job = {"elapsed": ACTIVE_SECONDS - 90}
        current = asyncio.current_task()
        self.probes.add(current)
        try:
            return await self.process(job, {"mode": "probe", "web_enabled": web_enabled}, self.config.credentials())
        except Exception as error:
            raise ValueError(self.safe_error(error)) from None
        finally:
            self.probes.discard(current)
            await self.release(identity)

    async def cleanup(self):
        pending = list(self.probes)
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        for identity in list(self.tasks):
            if identity in self.jobs:
                await self.cancel(identity)


research_jobs = ResearchJobs()
