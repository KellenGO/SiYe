"""Process-local temporary storage for the Windows browser driver."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from base.runtime_paths import application_root, library_data_root


def configure_browser_temp() -> Path | None:
    """Keep Playwright's Node driver off a potentially restricted system Temp.

    Run before starting Playwright so its driver and browser children inherit
    the directory. This changes only this process, never Windows settings or
    the persistent platform profiles.
    """
    if sys.platform != "win32":
        return None

    root = library_data_root() if os.environ.get("SIYE_DATA_DIR") else application_root()
    directory = root / ".cache" / "browser-temp"
    directory.mkdir(parents=True, exist_ok=True)
    os.environ["TEMP"] = str(directory)
    os.environ["TMP"] = str(directory)
    return directory
