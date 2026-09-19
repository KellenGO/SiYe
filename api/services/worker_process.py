# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
# This file is part of MediaCrawler project.
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

"""平台 worker 子进程的统一入口。

有三个地方要拉 `aggregate_search/worker.py` 子进程：搜索任务管理器（常驻 + one-shot）、
收藏同步任务管理器、登录任务的 router。它们各自复制了一份"拼命令 / 拼 env /
create_subprocess_exec / kill 后等退出"的样板 —— 这里收成一份，避免改一处漏两处。

只做**机制**，不做业务：不碰 NDJSON 事件语义、不碰各家的状态机。
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from typing import Dict, List, Optional

from base.runtime_paths import application_root

PROJECT_ROOT = application_root()
WORKER_SCRIPT = str(PROJECT_ROOT / "aggregate_search" / "worker.py")

#: 子进程必须无缓冲、按 UTF-8 输出 —— 否则 NDJSON 协议在 Windows 上会乱码或攒批。
_BASE_ENV = {
    "PYTHONUTF8": "1",
    "PYTHONIOENCODING": "utf-8",
    "PYTHONUNBUFFERED": "1",
}

#: stdout 单行上限（一个 result 事件可能很大）。
DEFAULT_STREAM_LIMIT = 2 * 1024 * 1024

#: 结束进程时先礼后兵的两个等待上限。
DEFAULT_GRACE_SECONDS = 5.0


def worker_command(*args: str) -> List[str]:
    """打包后走 EXE 的内置 worker 入口，源码模式走脚本。"""
    if getattr(sys, "frozen", False):
        return [sys.executable, "--aggregate-worker", *args]
    return [sys.executable, WORKER_SCRIPT, *args]


def worker_env(**extra: str) -> Dict[str, str]:
    """worker 的环境变量（继承当前进程 + 无缓冲/UTF-8，可追加自定义项）。"""
    return {**os.environ, **_BASE_ENV, **extra}


def _creation_flags() -> int:
    """Windows 下不要弹出黑色控制台窗口。"""
    return subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0


async def spawn_worker(
    *args: str,
    env_extra: Optional[Dict[str, str]] = None,
    limit: int = DEFAULT_STREAM_LIMIT,
    cwd: Optional[str] = None,
) -> asyncio.subprocess.Process:
    """起一个 worker 子进程（stdin/stdout/stderr 全是管道）。

    调用方负责写 stdin、读事件、并在结束时 `terminate_worker()`。
    """
    return await asyncio.create_subprocess_exec(
        *worker_command(*args),
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=cwd or str(PROJECT_ROOT),
        env=worker_env(**(env_extra or {})),
        limit=limit,
        creationflags=_creation_flags(),
    )


async def terminate_worker(
    proc: Optional[asyncio.subprocess.Process],
    *,
    grace: float = DEFAULT_GRACE_SECONDS,
    graceful: bool = False,
) -> None:
    """确保子进程已经退出：先礼后兵，任何异常都吞掉。

    - ``graceful=True``：先关 stdin 让它自己收尾（空闲回收用）。
    - ``graceful=False``：直接 kill（worker 可能正卡在浏览器操作里）。
    两次等待都超时后放弃 —— 与既有行为一致（不抛异常，调用方无需 try）。
    """
    if proc is None:
        return
    try:
        if graceful:
            if proc.stdin is not None:
                try:
                    proc.stdin.close()
                except Exception:
                    pass
        elif proc.returncode is None:
            proc.kill()
    except Exception:
        pass

    try:
        await asyncio.wait_for(proc.wait(), timeout=grace)
        return
    except asyncio.TimeoutError:
        pass
    except Exception:
        # 取消清理可能正并发 wait 同一进程 —— 不能让它逃逸。
        return

    try:
        if proc.returncode is None:
            proc.kill()
        await asyncio.wait_for(proc.wait(), timeout=grace)
    except Exception:
        pass


async def cancel_task_quietly(task: Optional[asyncio.Task]) -> None:
    """取消一个后台读取任务并等它收尾（吞掉 CancelledError 等所有异常）。"""
    if task is None:
        return
    if not task.done():
        task.cancel()
    await asyncio.gather(task, return_exceptions=True)


async def drain_stderr_to_eof(proc: asyncio.subprocess.Process) -> None:
    """把 stderr 一直读到 EOF 并丢弃。

    必须做这件事：worker 打日志很多，管道写满后子进程会卡死。
    """
    stderr = proc.stderr
    if stderr is None:
        return
    try:
        while await stderr.read(65536):
            pass
    except Exception:
        pass


#: worker 日志里出现这些关键字的行**整行丢弃**（可能带 cookie / 签名 / token）。
_LOG_SECRET_MARKERS = (
    "cookie", "token", "mstoken", "a_bogus", "x-s", "x_s", "x-bogus",
    "signature", "sec_", "verifyfp", "s_v_web_id", "authorization",
    "password", "phone", "webid", "odin_tt",
)

#: 单行保留长度（worker 日志可能超长）。
LOG_LINE_LIMIT = 300


def sanitize_worker_log_lines(chunk: str) -> List[str]:
    """把一段 worker stderr 变成可以落后端日志的安全行。

    - 按行切分、去空白、截断长度；
    - 命中敏感关键字的**整行丢弃**（不做部分脱敏，避免半截泄漏）；
    - 行数上限由调用方裁（deque maxlen）。
    """
    lines: List[str] = []
    for raw in (chunk or "").splitlines():
        text = raw.strip()
        if not text:
            continue
        lowered = text.lower()
        if any(marker in lowered for marker in _LOG_SECRET_MARKERS):
            continue
        lines.append(text[:LOG_LINE_LIMIT])
    return lines


async def drain_stderr_to_logger(proc: asyncio.subprocess.Process, emit) -> None:
    """把 stderr 一直读到 EOF，并把脱敏后的行交给 `emit(line)`。

    `emit` 必须不抛异常（内部自己兜住）—— 这里不再额外保护，避免把真实错误吞成静默。
    """
    stderr = proc.stderr
    if stderr is None:
        return
    try:
        while True:
            chunk = await stderr.read(65536)
            if not chunk:
                return
            text = chunk.decode("utf-8", errors="replace")
            for line in sanitize_worker_log_lines(text):
                emit(line)
    except Exception:
        pass
