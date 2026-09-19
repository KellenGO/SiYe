# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

"""Fake resident worker for supervisor tests.

Deterministic stand-in for ``aggregate_search/worker.py --resident``: reads
``WorkerRequest`` JSON lines from stdin, echoes protocol events on stdout,
and honors behavior keywords carried in the request's ``keyword`` field:

- ``__result_K__``      → emit K result events, then status succeeded + done
- ``__no_status__``     → emit only done (manager must decide succeeded/empty
                          on its own, same as the real worker's contract)
- ``__slow_5__``        → sleep 5s before succeeded + done (timeout tests)
- ``__empty_with_reason__`` → status empty + safe ``error_summary``
                          （验证 manager 把 0 结果的原因透传给前端）
- ``__exit_3__``        → succeeded + done, then ``os._exit(3)``
                          (done + nonzero → strict failed)
- ``__delayed_exit_0__`` → succeeded + done, sleep 0.3s, then exit 0
                          (退出耗时超过旧 50ms 启发式的确定性退役场景)
- ``__no_done_exit0__`` → status running only, then ``os._exit(0)``
                          (no done + exit0 → strict failed)
- ``__succeeded_no_done_exit0__`` → result + status succeeded, then ``os._exit(0)``
                          with **no done**（已报成功就必须保住成功）
- ``__crash_7__``       → ``os._exit(7)`` immediately, no done (crash recovery)
- ``__uncaught_boom__`` → raise RuntimeError inside request handling, caught
                          by the loop and converted to a safe stderr line +
                          immediate exit (mirrors the real worker's
                          uncaught-exception handling)
Anything else → status running + succeeded + done.

Honors ``MC_WORKER_MAX_REQUESTS`` (>=1) like the real worker: after that
many requests it exits 0. Exits 0 when stdin closes (graceful stop).
"""

import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from aggregate_search.protocol import (
    emit_done,
    emit_metrics,
    emit_result,
    emit_status,
    read_request,
)

_RESULT_FIELDS = {
    "content_id": "fake-0",
    "content_type": "note",
    "title": "Fake title",
    "author": "Fake author",
    "url": "https://example.com/fake-0",
    "published_at": None,
    "cover_url": None,
    "metrics": {},
    "rank": 0,
}


def _handle_request(request) -> int:
    """处理单个请求；返回进程退出码（0=继续，非 0=退出）。"""
    keyword = request.keyword or ""
    platform = request.platform

    emit_status(request.job_id, platform, "running")
    if keyword.startswith("__result_"):
        try:
            count = int(keyword.strip("_").split("_")[1])
        except (IndexError, ValueError):
            count = 1
        for i in range(max(0, count)):
            item = dict(_RESULT_FIELDS)
            item["platform"] = request.platform
            item["content_id"] = f"fake-{i}"
            item["url"] = f"https://example.com/fake-{i}"
            item["rank"] = i
            emit_result(request.job_id, platform, item)
        emit_metrics(request.job_id, platform, {
            "worker_ready_ms": 5, "search_api_ms": 7,
            "fast_path_used": True,
        })
        emit_status(request.job_id, platform, "succeeded")
    elif keyword == "__no_status__":
        pass
    elif keyword == "__empty_with_reason__":
        # 0 结果 + 安全原因：验证 manager 会把 error_summary 透传给前端
        emit_status(request.job_id, platform, "empty", {
            "message": "No results found.",
            "error_summary": "平台返回 0 条（测试原因）",
        })
    elif keyword == "__crash_7__":
        os._exit(7)  # 硬崩溃：不发 done
    elif keyword == "__slow_5__":
        time.sleep(5)
        emit_status(request.job_id, platform, "succeeded")
    elif keyword == "__exit_3__":
        emit_status(request.job_id, platform, "succeeded")
        emit_done(request.job_id, platform)
        os._exit(3)  # done + nonzero：严格语义应判 failed
        return 3  # pragma: no cover
    elif keyword == "__delayed_exit_0__":
        emit_status(request.job_id, platform, "succeeded")
        emit_done(request.job_id, platform)
        time.sleep(0.3)  # 退出耗时超过旧 50ms 启发式
        return 0
    elif keyword == "__no_done_exit0__":
        os._exit(0)  # 无 done + exit0：严格语义应判 failed
        return 0  # pragma: no cover
    elif keyword == "__succeeded_no_done_exit0__":
        # 已报终态 succeeded 但**没发 done** 就退出：manager 必须保住成功，
        # 不能把它翻成 "no done event" 的 failed（Linux CI 上真实出现过）。
        item = dict(_RESULT_FIELDS)
        item["platform"] = platform
        item["content_id"] = "fake-no-done"
        item["url"] = "https://example.com/fake-no-done"
        emit_result(request.job_id, platform, item)
        emit_status(request.job_id, platform, "succeeded")
        os._exit(0)
        return 0  # pragma: no cover
    elif keyword == "__uncaught_boom__":
        raise RuntimeError("boom detail")  # 未捕获异常
    else:
        emit_status(request.job_id, platform, "succeeded")
    emit_done(request.job_id, platform)
    return 0


def main() -> None:
    resident = "--resident" in sys.argv
    max_requests = 1
    if resident:
        try:
            max_requests = max(1, int(os.environ.get("MC_WORKER_MAX_REQUESTS", "20")))
        except (TypeError, ValueError):
            max_requests = 20
    processed = 0
    exit_code = 0
    while True:
        try:
            request = read_request()
        except EOFError:
            break  # stdin closed → 优雅退出
        except Exception as exc:  # pragma: no cover
            # 与真实 worker 一致：只输出异常类型，立即退出。
            sys.stderr.buffer.write(
                ("fake worker failed to read request: "
                 f"{type(exc).__name__}\n").encode("utf-8"))
            sys.stderr.buffer.flush()
            exit_code = 1
            break
        processed += 1
        request_exit = 0
        try:
            request_exit = _handle_request(request)
        except Exception as exc:
            # 与真实 worker 一致：未捕获异常 → 安全 stderr + 立即退出，
            # 不打印 traceback、不继续读取下一请求。
            sys.stderr.buffer.write(
                ("fake worker aborted by uncaught exception "
                 f"({type(exc).__name__}); exiting\n").encode("utf-8"))
            sys.stderr.buffer.flush()
            request_exit = 1
        if request_exit != 0:
            exit_code = request_exit
        if not resident or processed >= max_requests or request_exit != 0:
            break
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
