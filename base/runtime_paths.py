"""Paths shared by source and PyInstaller runtime modes."""

from __future__ import annotations

import sys
import os
from pathlib import Path


def bundle_root() -> Path:
    """Return the directory containing packaged read-only resources."""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parents[1]


def application_root() -> Path:
    """Return the stable application directory for user-writable data."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


def resource_path(*parts: str) -> Path:
    """Resolve a bundled/source resource without depending on cwd."""
    return bundle_root().joinpath(*parts)


def writable_path(*parts: str) -> Path:
    """Resolve data that must survive restarts outside PyInstaller resources."""
    return application_root().joinpath(*parts)


def library_data_root() -> Path:
    """Stable per-user location for the SQLite collection library.

    ``SIYE_DATA_DIR`` keeps tests and development tools isolated. Windows
    installations otherwise share one library across source and EXE folders.
    """
    override = os.environ.get("SIYE_DATA_DIR")
    if override:
        return Path(override).expanduser().resolve()
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        return Path(local_app_data).resolve() / "SiYe" / "data"
    return application_root() / "data"
