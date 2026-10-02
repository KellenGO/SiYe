"""Frozen desktop entry point for the node-free Windows distribution."""

from __future__ import annotations

import os
import socket
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser

from base.runtime_paths import application_root, resource_path
from base.server_port import resolve_port

HOST = "127.0.0.1"
# 端口统一由 base.server_port 解析（产品默认 8080，可用 SIYE_PORT 覆盖）——
# 同一仓库的多个 check-out 并行运行时靠它避开端口冲突。
PORT = resolve_port()
BASE_URL = f"http://{HOST}:{PORT}"
HEALTH_URL = f"{BASE_URL}/api/health"
READY_TIMEOUT_SECONDS = 45


def _configure_frozen_runtime() -> None:
    """Make packaged resources and the bundled JS runtime discoverable."""
    if not getattr(sys, "frozen", False):
        return
    node_root = resource_path()
    os.environ["PATH"] = str(node_root) + os.pathsep + os.environ.get("PATH", "")
    # PyExecJS otherwise prefers a system Node installation. The packaged
    # node.exe is private to this application and is never added system-wide.
    os.environ.setdefault("EXECJS_RUNTIME", "Node")


def _request_health() -> dict | None:
    try:
        with urllib.request.urlopen(HEALTH_URL, timeout=2) as response:
            if response.status != 200:
                return None
            import json

            payload = json.loads(response.read().decode("utf-8"))
            return payload if isinstance(payload, dict) else None
    except (OSError, ValueError, urllib.error.URLError):
        return None


def _wait_for_health(server_thread: threading.Thread | None = None) -> dict:
    deadline = time.monotonic() + READY_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        payload = _request_health()
        if payload and payload.get("status") == "ok":
            return payload
        if server_thread is not None and not server_thread.is_alive():
            raise RuntimeError("Backend exited before /api/health became ready")
        time.sleep(0.5)
    raise TimeoutError("Backend did not become ready within 45 seconds")


def _is_mediacrawler_health(payload: dict) -> bool:
    """Recognize this app's health shape before reusing the configured port."""
    return (
        payload.get("backend_available") is True
        and isinstance(payload.get("api_version"), str)
        and isinstance(payload.get("platforms"), dict)
    )


def _port_is_open() -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(0.3)
        return probe.connect_ex((HOST, PORT)) == 0


def _run_existing_backend() -> int:
    health = _request_health()
    if not health or not _is_mediacrawler_health(health):
        print(f"[X] {PORT} 端口已被其他程序占用，无法启动四野。")
        return 1
    print("已检测到正在运行的 四野 backend，直接打开页面。")
    webbrowser.open(BASE_URL)
    return 0


def _run_server(open_browser: bool = True, stop_event: threading.Event | None = None) -> int:
    import uvicorn
    from api.main import app

    config = uvicorn.Config(
        app,
        host=HOST,
        port=PORT,
        log_level="info",
        reload=False,
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, name="siye-backend")
    thread.start()
    try:
        health = _wait_for_health(thread)
        print("Backend ready")
        print(f"Browser: {health.get('browser_backend') or 'unavailable'}")
        if open_browser:
            webbrowser.open(BASE_URL)
            print(f"已打开 {BASE_URL}")
        while thread.is_alive():
            if stop_event is not None and stop_event.is_set():
                server.should_exit = True
            thread.join(timeout=0.5)
        return 0 if server.should_exit else 1
    except KeyboardInterrupt:
        print("\n正在关闭四野...")
        return 0
    finally:
        server.should_exit = True
        thread.join(timeout=15)


def _run_worker() -> int:
    from aggregate_search.worker import main as worker_main

    worker_main()
    return 0


def main() -> int:
    _configure_frozen_runtime()
    os.chdir(application_root())
    if "--research-worker" in sys.argv:
        from api.services.research_worker import main as research_main
        return research_main()
    if "--aggregate-worker" in sys.argv:
        return _run_worker()
    if "--frozen-runtime-smoke" in sys.argv:
        from base.frozen_runtime_smoke import run_frozen_runtime_smoke

        run_frozen_runtime_smoke()
        return 0
    health = _request_health()
    if health or _port_is_open():
        return _run_existing_backend()
    return _run_server(open_browser="--no-browser" not in sys.argv)


if __name__ == "__main__":
    raise SystemExit(main())
