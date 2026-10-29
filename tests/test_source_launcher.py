"""Exercise the source BAT's preflight in a path containing spaces and Chinese."""

import os
from pathlib import Path
import socket
import subprocess
import sys
import venv

import pytest


pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows BAT entry point")
ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def launcher(tmp_path):
    root = tmp_path / "源码 with spaces"
    root.mkdir()
    venv.EnvBuilder(with_pip=False).create(root / ".venv")
    # Stop before starting the real backend or opening a browser. Execute the
    # interpreter selection and socket probe exactly as shipped in the BAT.
    source = (ROOT / "启动-源码.bat").read_text(encoding="utf-8")
    preflight = source.split("rem 3.", 1)[0]
    failure_handlers = source[source.index("\n:PORT_FOREIGN\n"):]
    script = root / "check.bat"
    script.write_bytes((preflight + "exit /b 0\n" + failure_handlers).replace("\n", "\r\n").encode("utf-8"))
    return script


def run_launcher(script, port, **env):
    return subprocess.run(
        [os.environ.get("COMSPEC", "cmd.exe"), "/d", "/c", str(script)],
        env={**os.environ, "SIYE_PORT": str(port), "PYTHONIOENCODING": "utf-8", **env},
        input="\n",
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=15,
    )


def test_local_venv_runs_from_path_with_spaces(launcher):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    result = run_launcher(launcher, port)
    assert result.returncode == 0, result.stdout + result.stderr
    assert str(launcher.parent / ".venv" / "Scripts" / "python.exe") in result.stdout
    assert "Python 版本:" in result.stdout
    assert "not recognized" not in result.stderr


@pytest.mark.parametrize("port", [0, 70000, "invalid"])
def test_invalid_port_stops_before_backend_start(launcher, port):
    result = run_launcher(launcher, port)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "后端尚未启动" in result.stdout
    assert 'set "SIYE_PORT=18090"' in result.stdout


def test_windows_permission_denial_is_reported_without_waiting(launcher, tmp_path):
    (tmp_path / "sitecustomize.py").write_text(
        "import socket\n"
        "def deny_bind(self, address):\n"
        "    raise PermissionError(13, 'simulated WinError 10013')\n"
        "socket.socket.bind = deny_bind\n",
        encoding="utf-8",
    )
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    result = run_launcher(launcher, port, PYTHONPATH=str(tmp_path))
    assert result.returncode == 1, result.stdout + result.stderr
    assert "simulated WinError 10013" in result.stderr
    assert "后端尚未启动" in result.stdout
    assert "Windows 拒绝使用该端口" in result.stdout


def test_full_launcher_starts_backend_and_waits_for_health(launcher):
    root = launcher.parent
    api = root / "api"
    api.mkdir()
    (api / "__init__.py").write_text("", encoding="utf-8")
    (api / "main.py").write_text(
        "import os\n"
        "from http.server import BaseHTTPRequestHandler, HTTPServer\n"
        "class Health(BaseHTTPRequestHandler):\n"
        "    def do_GET(self):\n"
        "        assert self.path == '/api/health'\n"
        "        self.send_response(200)\n"
        "        self.send_header('Content-Type', 'application/json')\n"
        "        self.end_headers()\n"
        "        self.wfile.write(b'{\"status\":\"ok\"}')\n"
        "with HTTPServer(('127.0.0.1', int(os.environ['SIYE_PORT'])), Health) as server:\n"
        "    server.timeout = 8\n"
        "    server.handle_request()\n",
        encoding="utf-8",
    )
    source = (ROOT / "启动-源码.bat").read_text(encoding="utf-8")
    source = source.replace('start "" "%PAGE_URL%"', "echo BROWSER_URL=%PAGE_URL%")
    source = source.replace("\npause\n", "\nrem No interactive pause in tests.\n")
    launcher.write_bytes(source.replace("\n", "\r\n").encode("utf-8"))
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    result = run_launcher(launcher, port)
    assert result.returncode == 0, result.stdout + result.stderr
    assert f"BROWSER_URL=http://127.0.0.1:{port}" in result.stdout
    assert 'GET /api/health HTTP/1.1" 200' in result.stderr
