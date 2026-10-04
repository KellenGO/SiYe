"""Bounded media acquisition and optional local ASR, never exposed as an AI tool."""

import asyncio
import hashlib
import importlib.util
import json
import math
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import httpx

from base.runtime_paths import library_data_root, resource_path
from .research_web import public_target
from .worker_process import terminate_worker

MODEL = "small"
MAX_MEDIA_BYTES = 96 * 1024 * 1024
MAX_SECONDS = 1800
MAX_ENTRIES = 10000
MAX_TEXT = 256000
CACHE_SECONDS = 30 * 86400
CACHE_BYTES = 32 * 1024 * 1024
CACHE_FILES = 64


def normalize_segments(rows):
    entries, size, truncated = [], 0, False
    for row in rows:
        try:
            start, end = float(row["start"]), float(row["end"])
            text = row["text"].strip()
            if not text or not math.isfinite(start) or not math.isfinite(end) or start < 0 or end < start:
                continue
        except (KeyError, TypeError, ValueError, AttributeError):
            continue
        if len(entries) >= MAX_ENTRIES or size + len(text) > MAX_TEXT or end > MAX_SECONDS:
            truncated = True
            break
        size += len(text)
        entries.append({"start": round(start, 3), "end": round(end, 3), "text": text})
    return entries, truncated


def asr_python():
    # Frozen applications use a separately installed venv, not bundle imports/DLLs.
    root = library_data_root() / "research-asr" / "runtime"
    executable = root / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if executable.is_file():
        return str(executable)
    if not getattr(sys, "frozen", False) and importlib.util.find_spec("faster_whisper"):
        return sys.executable
    return None


def worker_environment():
    # Do not give ASR the platform sessions, provider keys, proxy auth or user Python settings.
    env = {key: value for key, value in os.environ.items() if key.upper() in {
        "SYSTEMROOT", "WINDIR", "PATH", "TEMP", "TMP", "LOCALAPPDATA", "APPDATA", "USERPROFILE"}}
    env.update(PYTHONUTF8="1", PYTHONIOENCODING="utf-8", HF_HUB_DISABLE_IMPLICIT_TOKEN="1",
               HF_HUB_DISABLE_TELEMETRY="1", DO_NOT_TRACK="1")
    return env


async def download_media(url, target, referer):
    """Stream to disk, pin public DNS and recheck every redirect; send no cookies."""
    async with httpx.AsyncClient(timeout=30, trust_env=False, follow_redirects=False) as client:
        async with asyncio.timeout(90):
            for _ in range(6):
                pinned, host = await public_target(url)
                async with client.stream("GET", pinned, headers={"Host": urlsplit(url).netloc,
                        "User-Agent": "Mozilla/5.0", "Referer": referer}, extensions={"sni_hostname": host}) as response:
                    if response.status_code in {301, 302, 303, 307, 308}:
                        location = response.headers.get("location")
                        if not location:
                            raise ValueError("媒体跳转地址缺失")
                        url = urljoin(url, location)
                        continue
                    if response.status_code in {401, 403, 429}:
                        raise PermissionError("media restricted")
                    response.raise_for_status()
                    if int(response.headers.get("content-length", "0")) > MAX_MEDIA_BYTES:
                        raise ValueError("媒体超过 96 MB 转写上限")
                    size = 0
                    with target.open("wb") as output:
                        async for chunk in response.aiter_bytes(64 * 1024):
                            size += len(chunk)
                            if size > MAX_MEDIA_BYTES:
                                raise ValueError("媒体超过 96 MB 转写上限")
                            output.write(chunk)
                    if not size:
                        raise ValueError("媒体没有可读取内容")
                    return
    raise ValueError("媒体跳转次数过多")


async def run_asr(executable, media, models, progress):
    command = [executable, "-I", "-X", "utf8", str(resource_path("scripts", "research_asr_worker.py"))]
    proc, handle, drain = None, None, None
    try:
        proc = await asyncio.create_subprocess_exec(*command, env=worker_environment(),
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            limit=2 * 1024 * 1024, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        from .research_jobs import windows_process_job
        handle = windows_process_job(proc)
        async def discard():
            while await proc.stderr.read(8192):
                pass
        drain = asyncio.create_task(discard())
        proc.stdin.write((json.dumps({"media": str(media), "models": str(models), "model": MODEL,
            "language": None, "max_seconds": MAX_SECONDS}) + "\n").encode("utf-8"))
        await proc.stdin.drain()
        proc.stdin.close()
        result = None
        async with asyncio.timeout(360):
            async for line in proc.stdout:
                event = json.loads(line)
                if event.get("type") == "progress":
                    # Fixed public messages; never reflect arbitrary worker text.
                    messages = {"model": "正在准备 small 转写模型（首次使用按需下载）",
                        "download": "首次使用：正在下载 small 转写模型", "decode": "正在提取音轨",
                        "transcribe": "正在本地 AI 转写"}
                    stage = event.get("stage")
                    if stage in messages:
                        progress(messages[stage])
                elif event.get("type") == "done":
                    result = event
                elif event.get("type") == "error":
                    reasons = {"duration": "音频超过 30 分钟转写上限", "model": "转写模型准备失败，请检查网络或可选组件",
                        "unavailable": "本地转写组件不完整，请重新安装可选组件"}
                    raise ValueError(reasons.get(event.get("code"), "本地转写失败，可重试"))
            await proc.wait()
            if proc.returncode or result is None:
                raise ValueError("本地转写进程未能完成")
        return result
    finally:
        if handle is not None:
            handle.Close()
        await terminate_worker(proc)
        if drain:
            await asyncio.gather(drain, return_exceptions=True)


class TranscriptionService:
    def __init__(self, root=None, temporary_root=None):
        self.root = Path(root) if root is not None else library_data_root() / "research-asr"
        self.temporary_root = Path(temporary_root) if temporary_root else self.root

    def cache_path(self, identity):
        digest = hashlib.sha256(json.dumps([1, identity, MODEL, "auto", "cpu", "int8", True]).encode()).hexdigest()
        return self.root / "transcripts" / f"{digest}.json"

    def cached(self, identity):
        path = self.cache_path(identity)
        try:
            if time.time() - path.stat().st_mtime > CACHE_SECONDS or path.stat().st_size > 2 * 1024 * 1024:
                return None
            data = json.loads(path.read_text(encoding="utf-8"))
            entries, truncated = normalize_segments(data["entries"])
            metadata = data["metadata"]
            duration = float(metadata["duration"])
            if (not entries or truncated or data.get("truncated") or data.get("state") != "ok"
                    or metadata["model"] != MODEL or metadata["source"] != "local_asr"
                    or metadata["engine"] != "faster-whisper" or not math.isfinite(duration) or not 0 <= duration <= MAX_SECONDS):
                return None
            return {"state": "ok", "entries": entries, "text": "", "reason": "", "truncated": False,
                "metadata": {"source": "local_asr", "engine": "faster-whisper", "model": MODEL,
                    "language": str(metadata.get("language") or "unknown")[:20], "duration": duration, "cached": True}}
        except (OSError, ValueError, KeyError, TypeError):
            return None

    def save(self, identity, result):
        path = self.cache_path(identity)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            # Atomic replacement; no raw URL, session, key or media bytes in cache.
            with tempfile.NamedTemporaryFile(dir=path.parent, suffix=".tmp", delete=False) as output:
                temporary = Path(output.name)
                output.write(json.dumps(result, ensure_ascii=False, allow_nan=False).encode("utf-8"))
            try:
                temporary.replace(path)
            finally:
                temporary.unlink(missing_ok=True)
            size, count = 0, 0
            for old in sorted(path.parent.glob("*.json"), key=lambda file: file.stat().st_mtime, reverse=True):
                size += old.stat().st_size
                count += 1
                if count > CACHE_FILES or size > CACHE_BYTES or time.time() - old.stat().st_mtime > CACHE_SECONDS:
                    old.unlink(missing_ok=True)
        except OSError:
            pass  # A read-only/full cache must not discard a valid transcript.

    async def transcribe(self, identity, url, referer, progress=lambda _: None):
        previous = self.cached(identity)
        if previous:
            progress("已复用本地视频转写缓存")
            return previous
        executable = asr_python()
        if not executable:
            return self.missing("平台无可读字幕且本地转写不可用：未安装可选 faster-whisper 组件")
        stage = "media"
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(prefix="audio-", dir=self.temporary_root) as directory:
                media = Path(directory) / "media"
                progress("正在获取转写所需音轨或媒体流")
                await download_media(url, media, referer)
                stage = "asr"
                output = await run_asr(executable, media, self.root / "models", progress)
            entries, limited = normalize_segments(output.get("entries", []))
            if not entries:
                return self.missing("本地转写未识别到可读语音")
            duration = float(output.get("duration", entries[-1]["end"]))
            if not math.isfinite(duration) or not 0 <= duration <= MAX_SECONDS:
                raise ValueError("音频超过 30 分钟转写上限")
            result = {"state": "ok", "text": "", "entries": entries, "reason": "转写内容达到上限" if limited else "",
                "truncated": limited, "metadata": {"source": "local_asr", "engine": "faster-whisper", "model": MODEL,
                    "language": str(output.get("language") or "unknown")[:20], "duration": duration, "cached": False}}
            if not limited:
                self.save(identity, result)
            return result
        except PermissionError:
            return self.missing("媒体 CDN 拒绝公开访问或要求鉴权，保留正文和评论", "restricted")
        except (TimeoutError, httpx.TimeoutException):
            reason = "音轨获取超时（最多 90 秒）" if stage == "media" else "本地转写 worker 超时（模型准备与转写最多 6 分钟）"
            return self.missing(reason + "，保留正文和评论，可重试", "failed")
        except Exception as error:
            reason = str(error) if isinstance(error, ValueError) and str(error).startswith(("媒体超过", "音频超过", "转写模型", "本地转写")) else (
                "音轨下载失败或媒体地址不可用，保留正文和评论，可重试" if stage == "media" else "本地转写失败，保留正文和评论，可重试")
            return self.missing(reason, "failed")

    @staticmethod
    def missing(reason, state="missing"):
        return {"state": state, "text": "", "entries": [], "reason": reason, "truncated": False}
