"""四野 · 桌面启动器（无窗口启动 + 系统托盘）。

打包后会产生两个 EXE，共用这一份入口脚本：

- ``SiYe.exe``（console=True）：承担后端服务与平台 worker。
  worker 子进程是通过 ``sys.executable`` 拉起的，且父子进程用 stdin/stdout 管道通信，
  所以**这个 EXE 必须保留控制台**（PyInstaller 的 windowed 模式会让 stdout 失效）。
- ``四野.exe``（console=False）：本文件里的"启动器"角色 = 无窗口 + 托盘图标。
  它不会自己跑后端，而是把 ``SiYe.exe --no-browser`` 以隐藏窗口方式拉起来，
  把它的输出重定向到 ``data/logs/backend-YYYYMMDD.log``。

角色由参数决定（``detect_role``）：
- ``--aggregate-worker`` / ``--frozen-runtime-smoke`` / ``--no-browser`` → 交给 desktop_main 处理；
- 不带参数 → 启动器（托盘）模式。

托盘菜单：打开四野 / 打开日志目录 / 退出四野。
重复双击只会打开已有页面（单实例锁 + 复用已在运行的后端），退出时结束整个后端进程树。
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Sequence

from base.runtime_paths import application_root, resource_path
from base.server_port import resolve_port

HOST = "127.0.0.1"
PORT = resolve_port()
BASE_URL = f"http://{HOST}:{PORT}"
HEALTH_URL = f"{BASE_URL}/api/health"
BACKEND_READY_TIMEOUT_SECONDS = 60
SHUTDOWN_TIMEOUT_SECONDS = 25
APP_TITLE = "四野 · 聚合搜索"
MAIN_EXE_NAME = "SiYe.exe"
LOCK_NAME = "launcher.lock"
LOG_DIR_PARTS = ("data", "logs")

ROLE_WORKER = "worker"
ROLE_SMOKE = "smoke"
ROLE_SERVER = "server"
ROLE_LAUNCHER = "launcher"


# ── 纯逻辑（可单测） ────────────────────────────────────────────────────


def detect_role(argv: Sequence[str]) -> str:
    """根据命令行参数决定当前进程的角色。"""
    if "--aggregate-worker" in argv:
        return ROLE_WORKER
    if "--frozen-runtime-smoke" in argv:
        return ROLE_SMOKE
    if "--no-browser" in argv or "--server" in argv or "--managed-backend" in argv:
        return ROLE_SERVER
    return ROLE_LAUNCHER


def main_exe_command(
    *args: str,
    frozen: bool = False,
    executable: Optional[str] = None,
    root: Optional[Path] = None,
) -> List[str]:
    """构造"主程序（console 版）"的启动命令。

    启动器自身是无窗口 EXE，不能拿 ``sys.executable`` 去跑后端——那样后端也会没有控制台，
    worker 子进程的 stdout 管道就废了。所以这里显式找同目录的 ``SiYe.exe``；
    源码模式下则用当前解释器跑 ``tray_main.py``。
    """
    exe = executable if executable is not None else sys.executable
    base = Path(root) if root is not None else application_root()
    if frozen:
        sibling = Path(exe).with_name(MAIN_EXE_NAME)
        target = sibling if sibling.is_file() else Path(exe)
        return [str(target), *args]
    return [exe, str(base / "tray_main.py"), *args]


def log_file(directory: Path, when: Optional[datetime] = None) -> Path:
    """后端日志文件（按天滚动）。"""
    stamp = (when or datetime.now()).strftime("%Y%m%d")
    return Path(directory) / f"backend-{stamp}.log"


def is_our_backend(payload: object) -> bool:
    """只在确认是本项目的后端时才复用 8080，避免抢占别人的端口。"""
    if not isinstance(payload, dict):
        return False
    return (
        payload.get("backend_available") is True
        and isinstance(payload.get("api_version"), str)
        and isinstance(payload.get("platforms"), dict)
    )


def tray_menu_items() -> List[str]:
    """托盘菜单项（供测试断言文案稳定）。"""
    return ["打开四野", "打开日志目录", "退出四野"]


# ── 运行时辅助 ──────────────────────────────────────────────────────────


def _configure_standard_streams() -> None:
    """windowed（无控制台）模式下 stdout/stderr 是 None，任何 print 都会崩。"""
    devnull = None
    if sys.stdout is None or sys.stderr is None:
        devnull = open(os.devnull, "w", encoding="utf-8")
    if sys.stdout is None:
        sys.stdout = devnull
    if sys.stderr is None:
        sys.stderr = devnull


def _request_health(timeout: float = 2.0) -> Optional[dict]:
    try:
        with urllib.request.urlopen(HEALTH_URL, timeout=timeout) as response:
            if response.status != 200:
                return None
            import json

            payload = json.loads(response.read().decode("utf-8"))
            return payload if isinstance(payload, dict) else None
    except (OSError, ValueError, urllib.error.URLError):
        return None


def backend_alive() -> bool:
    return is_our_backend(_request_health())


def wait_for_backend(proc: Optional[subprocess.Popen], timeout: float = BACKEND_READY_TIMEOUT_SECONDS) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if backend_alive():
            return True
        if proc is not None and proc.poll() is not None:
            return False
        time.sleep(0.5)
    return False


def show_error(title: str, message: str) -> None:
    """无窗口模式下的可读提示（打包后没有控制台可看）。"""
    if os.name == "nt":
        try:
            import ctypes

            ctypes.windll.user32.MessageBoxW(None, message, title, 0x10)  # MB_ICONERROR
            return
        except Exception:
            pass
    print(f"{title}: {message}", file=sys.stderr)


def make_icon_image(size: int = 64):
    """加载四野品牌图标作为托盘图标，缺少资源时保留程序化兜底。"""
    from PIL import Image, ImageDraw

    try:
        icon = Image.open(resource_path("assets", "siye-icon.png")).convert("RGBA")
        resampling = getattr(Image, "Resampling", Image)
        return icon.resize((size, size), resampling.LANCZOS)
    except (OSError, ValueError):
        # 源码目录或旧发行包缺少新资源时，托盘仍应能正常启动。
        pass

    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle(
        [0, 0, size - 1, size - 1], radius=int(size * 0.24), fill=(238, 240, 255, 255)
    )
    left, right = (102, 119, 251), (41, 221, 204)
    bar_top, bar_height = int(size * 0.42), max(3, int(size * 0.15))
    bar_left, bar_right = int(size * 0.2), int(size * 0.8)
    width = bar_right - bar_left
    for offset in range(width):
        ratio = offset / max(1, width - 1)
        color = tuple(int(left[i] + (right[i] - left[i]) * ratio) for i in range(3))
        draw.line(
            [(bar_left + offset, bar_top), (bar_left + offset, bar_top + bar_height)],
            fill=(*color, 255),
            width=1,
        )
    return image


# ── 单实例 ──────────────────────────────────────────────────────────────


class SingleInstanceLock:
    """用文件锁保证同时只有一个托盘实例（第二次双击只打开页面）。"""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._handle = None

    def acquire(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = open(self.path, "a+b")
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            self._handle = handle
            return True
        except OSError:
            handle.close()
            return False

    def release(self) -> None:
        if self._handle is None:
            return
        try:
            if os.name == "nt":
                import msvcrt

                self._handle.seek(0)
                msvcrt.locking(self._handle.fileno(), msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
        finally:
            self._handle.close()
            self._handle = None


# ── 后端进程 ────────────────────────────────────────────────────────────


def start_backend(log_dir: Path) -> subprocess.Popen:
    """以隐藏窗口方式拉起后端，输出重定向到日志文件。"""
    log_dir = Path(log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)
    creationflags = 0
    if os.name == "nt":
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
    # 父进程只保留 stdin 控制管道；关闭管道（包括托盘崩溃）通知后端正常退出。
    with open(log_file(log_dir), "ab", buffering=0) as handle:
        return subprocess.Popen(
            main_exe_command("--no-browser", "--managed-backend", frozen=getattr(sys, "frozen", False)),
            cwd=str(application_root()),
            stdin=subprocess.PIPE,
            stdout=handle,
            stderr=subprocess.STDOUT,
            creationflags=creationflags,
        )


def stop_backend(proc: Optional[subprocess.Popen], timeout: float = SHUTDOWN_TIMEOUT_SECONDS) -> None:
    """结束后端及其子进程（搜索/收藏 worker 也是它的子进程）。"""
    if proc is None or proc.poll() is not None:
        return
    if proc.stdin:
        try:
            proc.stdin.close()
            proc.wait(timeout=timeout)
            return
        except (OSError, subprocess.TimeoutExpired):
            pass
    if os.name == "nt":
        try:
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                capture_output=True,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000),
                timeout=5,
                check=True,
            )
        except Exception:
            proc.terminate()
    else:
        proc.terminate()
    try:
        proc.wait(timeout=timeout)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


# ── 托盘 ────────────────────────────────────────────────────────────────


class TrayLauncher:
    """托盘图标 + 菜单；退出时把整个后端进程树一起收掉。"""

    def __init__(self, backend: Optional[subprocess.Popen], log_dir: Path, lock: SingleInstanceLock) -> None:
        self.backend = backend
        self.log_dir = Path(log_dir)
        self.lock = lock
        self._icon = None

    def open_app(self) -> None:
        webbrowser.open(BASE_URL)

    def open_logs(self) -> None:
        path = str(self.log_dir)
        try:
            os.startfile(path)  # type: ignore[attr-defined]  Windows only
        except AttributeError:
            webbrowser.open(Path(path).as_uri())
        except OSError as error:
            show_error(APP_TITLE, f"打不开日志目录：{error}\n路径：{path}")

    def quit(self) -> None:
        stop_backend(self.backend)
        if self._icon is not None:
            self._icon.stop()
        self.lock.release()

    def run(self) -> None:
        import pystray

        icon = pystray.Icon(
            "siye",
            make_icon_image(),
            APP_TITLE,
            pystray.Menu(
                pystray.MenuItem(tray_menu_items()[0], lambda: self.open_app(), default=True),
                pystray.MenuItem(tray_menu_items()[1], lambda: self.open_logs()),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem(tray_menu_items()[2] if self.backend else "关闭托盘（已有服务保持运行）", lambda: self.quit()),
            ),
        )
        self._icon = icon
        icon.run()


# ── 入口 ────────────────────────────────────────────────────────────────


def run_launcher() -> int:
    root = application_root()
    log_dir = root.joinpath(*LOG_DIR_PARTS)
    log_dir.mkdir(parents=True, exist_ok=True)

    lock = SingleInstanceLock(root / "data" / LOCK_NAME)
    if not lock.acquire():
        # 已经有托盘实例在运行：只打开页面，不重复启动
        if wait_for_backend(None):
            webbrowser.open(BASE_URL)
        else:
            show_error(APP_TITLE, "已有启动器尚未就绪，请稍后重试或检查日志。")
        return 0

    backend: Optional[subprocess.Popen] = None
    if not backend_alive():
        try:
            backend = start_backend(log_dir)
        except OSError as error:
            show_error(APP_TITLE, f"启动后端失败：{error}\n日志目录：{log_dir}")
            lock.release()
            return 1
        if not wait_for_backend(backend):
            show_error(
                APP_TITLE,
                "后端启动失败或超时（60 秒）。\n"
                f"请查看日志：{log_file(log_dir)}\n"
                "常见原因：8080 端口被其他程序占用、杀毒软件拦截、解压目录不可写。",
            )
            stop_backend(backend)
            lock.release()
            return 1

    webbrowser.open(BASE_URL)

    launcher = TrayLauncher(backend, log_dir, lock)
    try:
        launcher.run()
    except Exception as error:  # 托盘不可用（极少见）时不要让后端成为孤儿进程
        show_error(APP_TITLE, f"托盘图标不可用：{error}\n本次启动的后台服务将关闭，请检查日志后重试。")
        return 1
    finally:
        stop_backend(backend)
        lock.release()
    return 0


def main() -> int:
    _configure_standard_streams()
    if "--research-worker" in sys.argv:
        import desktop_main
        desktop_main._configure_frozen_runtime()
        from api.services.research_worker import main as research_main
        return research_main()
    role = detect_role(sys.argv[1:])

    if role == ROLE_LAUNCHER:
        return run_launcher()

    # worker / smoke / server 三种角色复用 desktop_main 的实现，避免两套逻辑漂移。
    import desktop_main

    if role == ROLE_SERVER:
        desktop_main._configure_frozen_runtime()
        os.chdir(application_root())
        stop_event = None
        if "--managed-backend" in sys.argv:
            stop_event = threading.Event()
            control_fd = sys.stdin.fileno() if sys.stdin is not None else None
            def watch_parent() -> None:
                try:
                    if control_fd is not None and os.name == "nt":
                        # A blocking CRT read can deadlock native module imports
                        # (NumPy changes stream modes). Probe the pipe without reading.
                        import ctypes
                        import msvcrt
                        from ctypes import wintypes
                        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
                        peek = kernel.PeekNamedPipe
                        peek.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD,
                                         ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
                        peek.restype = wintypes.BOOL
                        handle = msvcrt.get_osfhandle(control_fd)
                        while peek(handle, None, 0, None, None, None):
                            time.sleep(0.2)
                    elif control_fd is not None:
                        while os.read(control_fd, 1):
                            pass
                except OSError:
                    pass
                finally:
                    stop_event.set()
            threading.Thread(target=watch_parent, daemon=True, name="launcher-control").start()
        return desktop_main._run_server(open_browser=False, stop_event=stop_event)
    return desktop_main.main()


if __name__ == "__main__":
    raise SystemExit(main())
