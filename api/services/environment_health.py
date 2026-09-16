"""Local startup health checks for the aggregate search application.

This module deliberately performs no platform request. It only checks local
browser/configuration state and reuses the account service's safe diagnostics.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path
from typing import Dict, Optional, Tuple

from api.schemas.search import HealthPlatformStatus, HealthResponse
from base.runtime_paths import resource_path

from . import accounts as accounts_service

API_VERSION = "0.2.2"
_PLATFORMS = ("xhs", "douyin", "bilibili", "zhihu")
_VALID_SEARCH_MODES = {
    "fast_path", "browser_fallback", "api", "page", "unavailable",
}
_BROWSER_CACHE_TTL_SECONDS = 30.0
_browser_cache: Optional[Tuple[float, bool, Optional[str]]] = None
_browser_cache_lock = asyncio.Lock()


def _read_web_version() -> Optional[str]:
    package_path = resource_path("webui", "package.json")
    try:
        with package_path.open(encoding="utf-8") as f:
            version = json.load(f).get("version")
        return version if isinstance(version, str) and version else None
    except (OSError, ValueError, TypeError):
        return None


async def _probe_browser() -> Tuple[bool, Optional[str]]:
    """Check the resolver's selected browser without launching a browser."""
    try:
        from tools.browser_launcher import resolve_playwright_browser

        executable_path, channel, backend = resolve_playwright_browser()
        if executable_path:
            return os.path.isfile(executable_path), backend
        if channel:
            return True, backend

        # The bundled Chromium path is exposed by Playwright after starting
        # its lightweight driver. This does not create a browser/context.
        from playwright.async_api import async_playwright

        playwright = await async_playwright().start()
        try:
            executable = playwright.chromium.executable_path
        finally:
            await playwright.stop()
        return bool(executable and os.path.isfile(executable)), backend
    except Exception:
        # Health output must stay safe and stable; callers only need the bool.
        return False, None


async def _browser_status() -> Tuple[bool, Optional[str]]:
    global _browser_cache
    now = time.monotonic()
    if _browser_cache and now - _browser_cache[0] < _BROWSER_CACHE_TTL_SECONDS:
        return _browser_cache[1], _browser_cache[2]
    async with _browser_cache_lock:
        now = time.monotonic()
        if _browser_cache and now - _browser_cache[0] < _BROWSER_CACHE_TTL_SECONDS:
            return _browser_cache[1], _browser_cache[2]
        available, backend = await _probe_browser()
        _browser_cache = (time.monotonic(), available, backend)
        return available, backend


async def _redis_status(required: bool) -> Optional[bool]:
    if not required:
        return None
    try:
        from redis import Redis
        from config import db_config

        client = Redis(
            host=db_config.REDIS_DB_HOST,
            port=int(db_config.REDIS_DB_PORT),
            db=int(db_config.REDIS_DB_NUM),
            password=db_config.REDIS_DB_PWD,
            socket_connect_timeout=0.5,
            socket_timeout=0.5,
        )
        try:
            return bool(await asyncio.to_thread(client.ping))
        finally:
            client.close()
    except Exception:
        return False


def _platform_statuses() -> Dict[str, HealthPlatformStatus]:
    try:
        accounts = accounts_service.get_accounts()
    except Exception:
        accounts = []
    by_platform = {
        item.get("platform"): item for item in accounts
        if isinstance(item, dict) and item.get("platform") in _PLATFORMS
    }
    result: Dict[str, HealthPlatformStatus] = {}
    for platform in _PLATFORMS:
        item = by_platform.get(platform, {})
        diagnostic = item.get("diagnostic")
        if not isinstance(diagnostic, dict):
            diagnostic = {}
        mode = diagnostic.get("search_mode")
        result[platform] = HealthPlatformStatus(
            account_state=str(item.get("status") or "unavailable"),
            profile_exists=bool(item.get("profile_exists")),
            search_available=bool(diagnostic.get("search_available")),
            search_mode=mode if mode in _VALID_SEARCH_MODES else None,
            snippet_available=(
                diagnostic.get("snippet_available")
                if isinstance(diagnostic.get("snippet_available"), bool)
                else None
            ),
        )
    return result


async def build_health_response() -> HealthResponse:
    browser_available, browser_backend = await _browser_status()
    import config

    redis_required = bool(getattr(config, "ENABLE_IP_PROXY", False))
    redis_available = await _redis_status(redis_required)
    web_version = _read_web_version()
    version_match = None if web_version is None else web_version == API_VERSION
    degraded = (
        not browser_available
        or version_match is False
        or (redis_required and redis_available is False)
    )
    return HealthResponse(
        environment_status="degraded" if degraded else "ok",
        backend_available=True,
        version=API_VERSION,
        api_version=API_VERSION,
        web_version=web_version,
        version_match=version_match,
        browser_available=browser_available,
        browser_backend=browser_backend,
        redis_required=redis_required,
        redis_available=redis_available,
        platforms=_platform_statuses(),
    )
