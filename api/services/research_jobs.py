"""One local research task at a time, with explicit source-gap confirmation."""

import asyncio
import copy
import json
import os
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

from base.runtime_paths import application_root, library_data_root
from .accounts import operation_coordinator, get_session_snapshot, ensure_session_snapshot
from .research_config import research_config, runtime_status
from .research_materials import material
from .worker_process import terminate_worker

ACTIVE_SECONDS = 600
MAX_FINISHED_JOBS = 10
MAX_RETAINED_BYTES = 32 * 1024 * 1024


def task_command():
    return [sys.executable, "--research-worker"] if getattr(sys, "frozen", False) else [sys.executable, "-m", "api.services.research_worker"]


def task_environment(workdir, credentials=None):
    env = {key: value for key, value in os.environ.items() if not key.startswith(("ANTHROPIC_", "CLAUDE_")) and key not in {"CLAUDECODE", "SIYE_RESEARCH_API_KEY"}}
    env.update(PYTHONUTF8="1", PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1", CLAUDE_CONFIG_DIR=str(Path(workdir) / "claude"),
               CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC="1")
    if credentials:
        if credentials.get("protocol", "anthropic") == "openai":
            env.update(SIYE_RESEARCH_API_KEY=credentials["api_key"])
        else:
            env.update(ANTHROPIC_API_KEY=credentials["api_key"], ANTHROPIC_BASE_URL=credentials["base_url"])
    return env


def windows_process_job(proc):
    """Closing this handle kills this worker and every descendant on Windows."""
    if os.name != "nt":
        return None
    import win32api
    import win32con
    import win32job
    handle = win32job.CreateJobObject(None, "")
    info = win32job.QueryInformationJobObject(handle, win32job.JobObjectExtendedLimitInformation)
    info["BasicLimitInformation"]["LimitFlags"] = win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    win32job.SetInformationJobObject(handle, win32job.JobObjectExtendedLimitInformation, info)
    process = win32api.OpenProcess(win32con.PROCESS_SET_QUOTA | win32con.PROCESS_TERMINATE, False, proc.pid)
    try:
        win32job.AssignProcessToJobObject(handle, process)
    except Exception:
        handle.Close()
        raise
    finally:
        process.Close()
    return handle


class ResearchJobs:
    def __init__(self, config=research_config):
        self.config = config
        self.jobs = {}
        self.tasks = {}
        self.active = None
        self.lock = asyncio.Lock()
        self.probes = set()

    def get(self, job_id):
        if job_id not in self.jobs:
            raise ValueError("研究任务不存在或应用已重启")
        return self.jobs[job_id]

    def public(self, job_id, store=None):
        job = self.get(job_id)
        visible = {key: copy.deepcopy(value) for key, value in job.items() if key not in {"snapshot", "elapsed", "workdir", "materials", "history", "research_focus", "transcribing_key"}}
        visible["total_materials"] = len(job["snapshot"]["items"])
        visible["materials"] = []
        for row in job["materials"]:
            summary = {key: row[key] for key in ("key", "platform", "title", "url")}
            for name in ("body", "comments", "subtitles"):
                values = row[name]
                summary[name] = {key: values[key] for key in ("state", "reason", "truncated")}
                summary[name]["count"] = len(values["entries"])
                summary[name]["collection_truncated"] = bool(values["truncated"])
                if "sort" in values:
                    summary[name]["sort"] = values["sort"]
                if name == "subtitles":
                    summary[name]["metadata"] = {key: values.get("metadata", {})[key] for key in
                        ("source", "engine", "model", "language", "duration", "cached") if key in values.get("metadata", {})}
            visible["materials"].append(summary)
        if store and job.get("space_id"):
            current = store.get_space(job["space_id"])
            visible["stale_snapshot"] = current["items"] != job["snapshot"]["items"]
        return visible

    def prune(self):
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
        matches = [job for job in self.jobs.values() if job.get("conversation_id", job["job_id"]) == conversation_id]
        if not matches or any(job["space_id"] != space_id for job in matches):
            raise ValueError("会话不存在或不属于此空间，请新建会话")
        return matches

    def conversations(self, space_id):
        groups = {}
        for job in self.jobs.values():
            if job["space_id"] != space_id:
                continue
            identity = job.get("conversation_id", job["job_id"])
            if identity not in groups:
                groups[identity] = {"id": identity, "title": (job["question"] or job["snapshot"]["name"])[:60], "turns": 0}
            groups[identity].update(status=job["status"], latest_job_id=job["job_id"])
            groups[identity]["turns"] += 1
            groups[identity] = groups.pop(identity)
        return list(reversed(list(groups.values())))

    async def create(self, space, question, web_enabled, conversation_id=None):
        if space["archived"]:
            raise ValueError("空间已归档，请先继续研究")
        if not space["items"] and not conversation_id:
            raise ValueError("请先向空间加入资料")
        self.require_runtime()
        previous = self.conversation_jobs(space["id"], conversation_id) if conversation_id else []
        if previous and previous[-1]["status"] != "ready":
            raise ValueError("请先完成当前会话的研究，或新建会话")
        latest = previous[-1] if previous else None
        history = []
        for row in [row for row in previous if row["status"] == "ready"][-3:]:
            answer = self.document_text(row["document"])
            history.append({"question": row["question"], "answer": answer if len(answer) <= 3000 else answer[:2000] + "\n…\n" + answer[-1000:],
                            "answer_truncated": len(answer) > 3000})
        identity = uuid.uuid4().hex
        await self.claim(identity)
        self.jobs[identity] = {"job_id": identity, "space_id": space["id"], "snapshot": copy.deepcopy(latest["snapshot"] if latest else space),
            "conversation_id": conversation_id or identity, "history": history,
            "research_focus": (latest.get("research_focus") or previous[0]["question"]) if latest else question,
            "question": question, "web_enabled": web_enabled, "status": "collecting", "phase": "collecting",
            "message": "正在获取空间资料", "materials": [], "elapsed": 0.0, "error": "",
            "document": None, "activity": [], "coverage": [], "external_sources": [], "web_errors": [], "usage": None, "cost_usd": None}
        if latest:
            self.jobs[identity].update(materials=copy.deepcopy(latest["materials"]), status="analyzing", phase="analyzing", message="正在继续分析当前会话资料")
        self.tasks[identity] = asyncio.create_task(self.analyze(identity) if latest else self.collect(identity))
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
            proc = await asyncio.create_subprocess_exec(*task_command(), cwd=str(application_root()),
                env=task_environment(workdir, credentials), stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, limit=16 * 1024 * 1024,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            handle = windows_process_job(proc)
            async def discard_stderr():
                while await proc.stderr.read(8192):
                    pass
            drain = asyncio.create_task(discard_stderr())
            async with asyncio.timeout(max(1, ACTIVE_SECONDS - job["elapsed"])):
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
            for platform in {item["result"]["platform"] for item in job["snapshot"]["items"]}:
                sessions[platform] = get_session_snapshot(platform)
                if not sessions[platform] and platform in {"xhs", "zhihu"}:
                    try:
                        sessions[platform] = await asyncio.wait_for(ensure_session_snapshot(platform), timeout=30)
                    except Exception:
                        pass
            job["elapsed"] += time.monotonic() - preparation_started
            previous = {row["key"]: row for row in job["materials"]}
            items = job["snapshot"]["items"]
            if retry:
                items = [item for item in items if item["key"] not in previous or any(
                    previous[item["key"]][name]["state"] in {"missing", "failed", "restricted"} for name in ("body", "comments", "subtitles"))]
            await self.process(job, {"mode": "collect", "items": items, "sessions": sessions, "previous": previous})
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
                    for name in ("body", "comments", "subtitles"):
                        if missing[name]["state"] != "not_applicable":
                            missing[name].update(state="failed", reason="本次尚未获取此资料，可重试")
                    job["materials"].append(missing)
            if leased:
                await operation_coordinator.release_exclusive("research")

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
        self.tasks[identity] = asyncio.create_task(self.analyze(identity))
        self.tasks[identity].add_done_callback(lambda _: self.prune())
        return self.public(identity)

    async def analyze(self, identity):
        job = self.get(identity)
        try:
            result = await self.process(job, {"mode": "analyze", "materials": copy.deepcopy(job["materials"]),
                "space_name": job["snapshot"]["name"], "description": job["snapshot"]["description"],
                "question": job["question"], "research_focus": job.get("research_focus", ""),
                "web_enabled": job["web_enabled"], "conversation": job.get("history", [])}, self.config.credentials())
            job.update(result)
            job.update(status="ready", phase="ready", message="笔记已生成，请预览后追加")
        except asyncio.CancelledError:
            job.update(status="cancelled", phase="cancelled", message="研究已取消")
            raise
        except Exception as error:
            job.update(status="failed", phase="failed", error=self.safe_error(error))
        finally:
            for record in job.get("activity", []):
                if record.get("status") == "running":
                    record.update(status="cancelled" if job["status"] == "cancelled" else "failed", summary="执行已中断")
            await self.release(identity)

    async def cancel(self, identity):
        job = self.get(identity)
        task = self.tasks.get(identity)
        if task and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        if job["status"] not in {"ready", "failed", "cancelled"}:
            job.update(status="cancelled", phase="cancelled", message="研究已取消")
        await self.release(identity)
        return self.public(identity)

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
