"""Silently install, exercise, and uninstall the Windows installer."""

from __future__ import annotations

import argparse
import os
import socket
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _available_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def _run(command: list[str], *, env: dict[str, str] | None = None, timeout: int = 300) -> str:
    result = subprocess.run(
        command,
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        errors="replace",
        timeout=timeout,
    )
    if result.returncode != 0:
        raise AssertionError(
            f"command failed ({result.returncode}): {(result.stdout + result.stderr)[-4000:]}"
        )
    return result.stdout + result.stderr


def main() -> int:
    if sys.platform != "win32":
        raise SystemExit("installer clean-room smoke must run on Windows")
    parser = argparse.ArgumentParser()
    parser.add_argument("--installer", type=Path, required=True)
    args = parser.parse_args()
    installer = args.installer.resolve()
    if not installer.is_file():
        raise AssertionError(f"installer not found: {installer}")

    with tempfile.TemporaryDirectory(prefix="siye-installer-smoke-") as temporary:
        root = Path(temporary)
        install_dir = root / "installed" / "SiYe"
        install_log = root / "install.log"
        data_dir = root / "user-data"
        command = [
            str(installer),
            "/VERYSILENT",
            "/SUPPRESSMSGBOXES",
            "/NORESTART",
            "/NOICONS",
            f'/DIR={install_dir}',
            f'/LOG={install_log}',
        ]
        uninstaller = install_dir / "unins000.exe"
        try:
            _run(command)
            required = (
                "四野.exe", "SiYe.exe", "browser_extension", "LICENSE",
                "RELEASE_VERSION",
            )
            for relative in required:
                if not (install_dir / relative).exists():
                    raise AssertionError(f"installed payload missing: {relative}")
            if any((install_dir / name).exists() for name in ("browser_data", ".cache")):
                raise AssertionError("installer included user login or cache data")
            if not uninstaller.is_file():
                raise AssertionError("standard Windows uninstaller was not installed")

            env = os.environ.copy()
            env["SIYE_DATA_DIR"] = str(data_dir)
            port = _available_port()
            _run(
                [sys.executable, str(ROOT / "scripts" / "exe_clean_room_smoke.py"),
                 "--package", str(install_dir), "--port", str(port)],
                env=env,
                timeout=360,
            )
        finally:
            if uninstaller.is_file():
                _run([
                    str(uninstaller),
                    "/VERYSILENT",
                    "/SUPPRESSMSGBOXES",
                    "/NORESTART",
                ])
        if (install_dir / "四野.exe").exists() or (install_dir / "SiYe.exe").exists():
            raise AssertionError("uninstaller left installed application binaries behind")

    print("installer silent install: PASS")
    print("installed executable runtime: PASS")
    print("standard uninstall: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
