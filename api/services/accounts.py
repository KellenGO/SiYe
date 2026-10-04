# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

"""Account session service for the aggregate search backend.

Responsibilities:
- one-time sync tickets (>=128-bit, 60s TTL, single-use, memory-only);
- cookie domain whitelist validation per platform;
- Chrome-cookie -> Playwright-cookie mapping;
- importing cookies into an independent headless profile under browser_data/;
- verifying a profile via the platform's own pong/check_login_state;
- deleting a profile (browser_data only, path-verified);
- in-memory per-platform account state.

Security: no cookies/tokens/profile absolute paths ever leave this service
in responses; no raw cookie JSON is persisted — only the browser profile
itself persists the session. No usernames/passwords are stored.
"""

from __future__ import annotations

import asyncio
import math
import os
import secrets
import shutil
import sqlite3
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..schemas.search import PlatformDiagnostic
from base.runtime_paths import resource_path, writable_path

BROWSER_DATA_DIR = writable_path("browser_data")

# Platform -> official cookie domains (subdomain match allowed).
PLATFORM_COOKIE_DOMAINS: Dict[str, tuple] = {
    "xhs": ("xiaohongshu.com",),
    "douyin": ("douyin.com",),
    "bilibili": ("bilibili.com",),
    "zhihu": ("zhihu.com",),
}

# Platform -> profile dir name (must match config.USER_DATA_DIR % platform).
PLATFORM_PROFILE_DIRS: Dict[str, str] = {
    "xhs": "xhs_user_data_dir",
    "douyin": "dy_user_data_dir",
    "bilibili": "bili_user_data_dir",
    "zhihu": "zhihu_user_data_dir",
}

# Platform slug -> 展示名（用于固定安全文案，无任何敏感信息）。
PLATFORM_DISPLAY_NAMES: Dict[str, str] = {
    "xhs": "小红书",
    "douyin": "抖音",
    "bilibili": "B站",
    "zhihu": "知乎",
}

PLATFORM_HOME_URLS: Dict[str, str] = {
    "xhs": "https://www.xiaohongshu.com",
    "douyin": "https://www.douyin.com",
    "bilibili": "https://www.bilibili.com",
    "zhihu": "https://www.zhihu.com",
}

# Platform -> URL for cookie reading / pong verification.
PLATFORM_COOKIE_URLS: Dict[str, list] = {
    "xhs": ["https://www.xiaohongshu.com"],
    "douyin": ["https://www.douyin.com"],
    "bilibili": ["https://www.bilibili.com"],
    "zhihu": ["https://www.zhihu.com"],
}

TICKET_TTL_SECONDS = 60
_SYNC_TICKET_LENGTH_BITS = 256

# Cookie wire contract: the extension sends raw Chrome-cookies-API fields
# exactly once; the backend performs the single Chrome -> Playwright mapping.
COOKIE_FORMAT_CHROME_V1 = "chrome-v1"
CHROME_V1_FIELDS = frozenset({
    "name", "value", "domain", "path", "expirationDate",
    "httpOnly", "secure", "sameSite", "session", "storeId",
})
# Playwright-style sameSite values are NOT part of the chrome-v1 contract.
PLAYWRIGHT_SAME_SITE_VALUES = frozenset({"Lax", "Strict", "None"})

# Bounded verification after a sync: if pong takes longer, the profile keeps
# verifying in the background and the sync reports status=verifying.
SYNC_VERIFY_TIMEOUT_SECONDS = 30

# Wire protocol version: must match browser_extension/sync_protocol.js's
# EXTENSION_PROTOCOL_VERSION. Older/newer extensions are rejected with a
# structured extension_protocol_outdated error.
EXTENSION_PROTOCOL_VERSION = 2

# Platform login cookies (name presence only — never values). Names follow
# the repo's real pong / login checks:
#   bilibili  login.py:76  "SESSDATA or DedeUserID"  (bili_jct = CSRF for POSTs)
#   zhihu     client.py:76 d_c0 required; login.py:68 uses z_c0
#   xhs       login.py:78  web_session changes on login
#   douyin    client.py:159 pong checks cookie LOGIN_STATUS == "1"
# Login-marker whitelist for the login_marker_presence diagnostic: marker
# NAME + presence boolean ONLY — values never leave this module. This is a
# heuristic (import-time signal), NOT the verification result: import is
# allowed with any valid platform cookie, and only the platform's own pong
# decides connected/verified (zhihu d_c0 may only be generated after the
# browser visits the official site).
LOGIN_MARKER_NAMES: Dict[str, tuple] = {
    "douyin": ("LOGIN_STATUS", "sessionid", "sessionid_ss"),
    "zhihu": ("z_c0", "d_c0"),
    "bilibili": ("SESSDATA", "DedeUserID"),
    "xhs": ("web_session",),
}


def _diagnostics(received: int = 0, stage: str = "cookie_validation") -> Dict[str, Any]:
    """Structured sync diagnostics — counts and marker booleans only,
    never cookie values."""
    return {
        "received_cookie_count": received,
        "accepted_cookie_count": 0,
        "skipped_cookie_count": 0,
        "rejected_cookie_count": 0,
        "required_cookie_present": False,
        "login_marker_presence": {},
        "browser_cookie_store_count": 0,
        "sync_stage": stage,
    }


class TicketError(Exception):
    """Invalid / expired / reused / mismatched sync ticket."""

    def __init__(self, message: str = "同步票据无效"):
        self.safe_code = "sync_ticket_invalid"
        super().__init__(message)


class CookieDomainRejectedError(Exception):
    def __init__(self, message: str = "Cookie 域名不在该平台允许的官方域名内",
                 diagnostics: Optional[Dict[str, Any]] = None):
        self.safe_code = "cookie_domain_rejected"
        self.diagnostics = diagnostics or {}
        super().__init__(message)


class SessionImportError(Exception):
    def __init__(self, message: str = "会话导入失败",
                 diagnostics: Optional[Dict[str, Any]] = None):
        self.safe_code = "session_import_failed"
        self.diagnostics = diagnostics or {}
        super().__init__(message)


class CookieFormatInvalidError(Exception):
    """The payload is not the chrome-v1 wire contract (mixed/unknown fields)."""

    def __init__(self, message: str = "Cookie 格式不兼容，请重新加载浏览器扩展后再试",
                 diagnostics: Optional[Dict[str, Any]] = None):
        self.safe_code = "cookie_format_invalid"
        self.diagnostics = diagnostics or {}
        super().__init__(message)


class ExtensionProtocolOutdatedError(Exception):
    """The extension speaks a different wire protocol version."""

    def __init__(self, message: str = "扩展版本与后端协议不兼容，"
                                      "请在扩展管理页点击\"重新加载\"后刷新本页",
                 diagnostics: Optional[Dict[str, Any]] = None):
        self.safe_code = "extension_protocol_outdated"
        self.diagnostics = diagnostics or {}
        super().__init__(message)


class PlatformError(Exception):
    def __init__(self, message: str = "不支持的平台"):
        self.safe_code = "sync_ticket_invalid"
        super().__init__(message)


# ── One-time tickets ────────────────────────────────────────────────────

class _Ticket:
    __slots__ = ("platform", "expires_at", "used")

    def __init__(self, platform: str) -> None:
        self.platform = platform
        self.expires_at = time.monotonic() + TICKET_TTL_SECONDS
        self.used = False


_tickets: Dict[str, _Ticket] = {}
_tickets_lock = asyncio.Lock()


def create_sync_ticket(platform: str) -> str:
    """Create a one-time sync ticket (memory-only)."""
    if platform not in PLATFORM_PROFILE_DIRS:
        raise PlatformError(f"不支持的平台: {platform}")
    ticket = secrets.token_urlsafe(_SYNC_TICKET_LENGTH_BITS // 8)
    _tickets[ticket] = _Ticket(platform)
    return ticket


def _purge_expired_tickets() -> None:
    now = time.monotonic()
    for key in [k for k, t in _tickets.items() if t.expires_at <= now]:
        _tickets.pop(key, None)


async def consume_sync_ticket(ticket: str, platform: str) -> None:
    """Validate and consume a one-time ticket.

    Raises TicketError unless the ticket exists, is unexpired, unused, and
    bound to the same platform.
    """
    async with _tickets_lock:
        _purge_expired_tickets()
        t = _tickets.pop(ticket, None)
        if t is None:
            raise TicketError("同步票据无效或已过期")
        if t.used:
            raise TicketError("同步票据已被使用")
        if t.platform != platform:
            raise TicketError("同步票据与平台不匹配")
        t.used = True  # single-use


# ── Cookie validation & mapping ─────────────────────────────────────────

def cookie_domain_allowed(platform: str, domain: str) -> bool:
    """A cookie domain must belong to the platform's official domains."""
    allowed = PLATFORM_COOKIE_DOMAINS.get(platform, ())
    if not allowed:
        return False
    d = (domain or "").lower().lstrip(".")
    if not d:
        return False
    return any(d == a or d.endswith("." + a) for a in allowed)


def map_chrome_cookie(cookie: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Map ONE chrome-v1 cookie to the Playwright cookie shape.

    The backend performs this mapping exactly once — the extension must NOT
    pre-convert. Rules:
    - ``expirationDate`` -> ``expires`` only when it is a finite positive
      number; session cookies get NO ``expires`` key (never ``-1``).
    - ``sameSite``: no_restriction->None, lax->Lax, strict->Strict; any other
      value (unspecified/null/unknown) omits the key entirely — NEVER
      ``sameSite: null`` (Playwright rejects it).
    - partitioned cookies (``partitionKey``) are skipped — caller counts.
    - malformed single cookies return None — caller counts them as skipped
      instead of failing the whole batch.
    """
    name = cookie.get("name")
    value = cookie.get("value")
    domain = cookie.get("domain")
    if not name or value is None or not domain:
        return None
    if cookie.get("partitionKey"):
        return None  # partitioned cookie — unsupported, skip
    out: Dict[str, Any] = {
        "name": name,
        "value": value,
        "domain": domain,
        "path": cookie.get("path") or "/",
        "httpOnly": bool(cookie.get("httpOnly")),
        "secure": bool(cookie.get("secure")),
    }
    expiration = cookie.get("expirationDate")
    if isinstance(expiration, (int, float)) and not isinstance(expiration, bool):
        f = float(expiration)
        if math.isfinite(f) and f > 0:
            out["expires"] = f
    # NaN / Infinity / non-positive / non-numeric -> no expires key at all.
    same_site = cookie.get("sameSite")
    if same_site == "no_restriction":
        out["sameSite"] = "None"
    elif same_site == "lax":
        out["sameSite"] = "Lax"
    elif same_site == "strict":
        out["sameSite"] = "Strict"
    # unspecified / null / unknown -> omit sameSite entirely (never null).
    return out


def validate_chrome_v1_cookie_list(
    platform: str, cookies: List[Dict[str, Any]],
) -> "tuple[List[Dict[str, Any]], Dict[str, Any]]":
    """Enforce the chrome-v1 wire contract and map to Playwright shape.

    Returns ``(mapped, diagnostics)``. Raises:
    - CookieFormatInvalidError — unknown fields, both expirationDate and
      expires, or Playwright-style sameSite values (old-format payloads);
    - CookieDomainRejectedError — third-party cookie (backend whitelist).

    Login markers are a HEURISTIC diagnostic only: as long as at least one
    legal platform cookie maps, import is allowed. The platform's own pong
    after profile import is the only source of connected/verified.
    """
    diag = _diagnostics(received=len(cookies or []))
    mapped: List[Dict[str, Any]] = []
    # name -> (first non-empty value). Only used by the platform login
    # predicates below; values NEVER enter diagnostics or responses.
    received_values: Dict[str, str] = {}
    for c in cookies or []:
        if not isinstance(c, dict):
            diag["skipped_cookie_count"] += 1
            continue
        unknown = set(c) - CHROME_V1_FIELDS - {"partitionKey"}
        if unknown:
            raise CookieFormatInvalidError(
                f"Cookie 包含不兼容字段: {sorted(unknown)[0]}", diagnostics=diag)
        if c.get("expirationDate") is not None and "expires" in c:
            raise CookieFormatInvalidError(
                "Cookie 同时包含新旧两种过期字段", diagnostics=diag)
        if c.get("sameSite") in PLAYWRIGHT_SAME_SITE_VALUES:
            raise CookieFormatInvalidError(
                "Cookie sameSite 使用了非 Chrome 原始格式", diagnostics=diag)
        name = c.get("name")
        if name:
            value = c.get("value")
            if isinstance(value, str) and value:
                received_values.setdefault(name, value)
        if c.get("partitionKey"):
            diag["skipped_cookie_count"] += 1
            continue
        domain = (c.get("domain") or "").lower()
        if not cookie_domain_allowed(platform, domain):
            diag["rejected_cookie_count"] += 1
            raise CookieDomainRejectedError(diagnostics=diag)
        m = map_chrome_cookie(c)
        if m is None:
            diag["skipped_cookie_count"] += 1
            continue
        mapped.append(m)
        diag["accepted_cookie_count"] += 1

    markers = _login_marker_presence(platform, received_values)
    diag["login_marker_presence"] = markers
    # Compat heuristic field: at least one whitelisted login marker was
    # received. It is a diagnostic only — it never blocks import and never
    # implies a logged-in session.
    diag["required_cookie_present"] = any(markers.values())
    return mapped, diag


def _login_marker_presence(platform: str,
                           received_values: Dict[str, str]) -> Dict[str, bool]:
    """Presence booleans for the platform's whitelisted login markers —
    NAME + true/false only, values never leave this module. A marker is
    present when its name was received with a non-empty value. This is a
    diagnostic, NOT a login verdict: e.g. zhihu often only has z_c0 before
    the browser visits the official site (d_c0 is generated by the page).
    """
    return {
        name: bool(received_values.get(name))
        for name in LOGIN_MARKER_NAMES.get(platform, ())
    }


# ── Profile paths ───────────────────────────────────────────────────────

def profile_dir_for(platform: str) -> Path:
    """Profile path is always generated from the backend whitelist."""
    name = PLATFORM_PROFILE_DIRS.get(platform)
    if not name:
        raise PlatformError(f"不支持的平台: {platform}")
    return BROWSER_DATA_DIR / name


def _resolve_profile_path(platform: str) -> Path:
    """Resolve and verify the profile path stays inside browser_data."""
    resolved = profile_dir_for(platform).resolve()
    base = BROWSER_DATA_DIR.resolve()
    if str(resolved) != str(base) and not str(resolved).startswith(str(base) + os.sep):
        raise SessionImportError("非法路径：profile 不在 browser_data 目录内")
    return resolved


# ── In-memory platform state ────────────────────────────────────────────

_platform_state: Dict[str, Dict[str, Any]] = {}


def _fresh_state() -> Dict[str, Any]:
    return {
        "status": "disconnected",
        "verified": False,
        "display_name": None,
        "last_verified_at": None,
        "safe_error_code": None,
        "safe_message": None,
        "browser_backend": None,
    }


def _state_of(platform: str) -> Dict[str, Any]:
    if platform not in PLATFORM_PROFILE_DIRS:
        raise PlatformError(f"不支持的平台: {platform}")
    st = _platform_state.get(platform)
    if st is None:
        st = _fresh_state()
        # A leftover profile only means "exists locally, not yet verified" —
        # never "connected": connected implies verified=True.
        if profile_dir_for(platform).is_dir():
            st["status"] = "unverified"
        _platform_state[platform] = st
    # Invariant: connected requires verified. Enforced on every read.
    if st.get("status") == "connected" and not st.get("verified"):
        st["status"] = "unverified"
    return st


def _set_state(platform: str, **kw: Any) -> Dict[str, Any]:
    if kw.get("status") in ("syncing", "verifying", "disconnected"):
        _evidence_revision[platform] = _evidence_revision.get(platform, 0) + 1
        _verification_checks.pop(platform, None)
        if kw["status"] in ("syncing", "disconnected"):
            _usage_evidence.pop(platform, None)
            _last_search_outcomes.pop(platform, None)
    st = _state_of(platform)
    for k, v in kw.items():
        st[k] = v
    return st


# ── In-memory session snapshot ──────────────────────────────────────────
# 同步/验证成功后在 API 进程内存中保存该平台的 Cookie 会话快照，供聚合搜索
# worker 通过 stdin 使用。约束：
# - 只存在内存，绝不写入磁盘/数据库；
# - 不进入命令行参数、环境变量、日志、traceback、API 响应、stdout/stderr；
# - 清除账号、重新同步、登录失效、应用 shutdown 时清除；
# - 后端重启后没有快照属于正常情况，自动回退原 profile 浏览器路径。

_session_snapshots: Dict[str, Dict[str, str]] = {}

# Increment the account generation whenever a snapshot changes so result
# cache entries become invalid after account operations.
_account_generation: Dict[str, int] = {}

# 只保留最近一次搜索的安全状态与路径信息，绝不保存异常文本、请求体或
# Cookie。Platform Doctor 读取它做本地状态汇总，不参与搜索决策。
_last_search_outcomes: Dict[str, Dict[str, Any]] = {}
_evidence_revision: Dict[str, int] = {}
_evidence_sequence = 0
_usage_evidence: Dict[str, Dict[str, Any]] = {}
_verification_checks: Dict[str, Dict[str, Any]] = {}


def evidence_token(platform: str):
    global _evidence_sequence
    _evidence_sequence += 1
    return (_evidence_revision.get(platform, 0), _evidence_sequence)


def record_usage(platform: str, operation: str, status: str, token) -> bool:
    """Accept only current-session, live observations; never persist secrets."""
    if token[0] != _evidence_revision.get(platform, 0):
        return False
    if status not in {"succeeded", "empty", "login_required", "rate_limited", "timed_out", "failed"}:
        return False
    records = _usage_evidence.setdefault(platform, {})
    previous = max(records.values(), key=lambda item: item["started"], default=None)
    if previous and previous["started"] > token[1]:
        return False
    if status in ("succeeded", "empty") and _state_of(platform)["status"] == "expired":
        _set_state(platform, status="unverified", verified=False,
                   safe_error_code=None, safe_message="最近操作已成功，登录身份等待重新确认。")
        _verification_checks.pop(platform, None)
    records[operation] = {"status": status, "checked_at": datetime.now(timezone.utc).isoformat(),
                          "started": token[1]}
    return True


def usage_evidence(platform: str):
    return {key: {"status": value["status"], "checked_at": value["checked_at"]}
            for key, value in _usage_evidence.get(platform, {}).items()}


async def begin_scan_login(platform: str) -> None:
    _set_state(platform, status="syncing", verified=False, safe_error_code=None, safe_message=None)
    await clear_session_snapshot(platform)


def finish_scan_login(platform: str, succeeded: bool) -> None:
    # The login worker emits success only after verification while its browser
    # context is still open. Reopening a different context must not replace it.
    _finalize_verdict(platform, "verified" if succeeded else "unavailable", None, False)
    _verification_checks[platform]["evidence"]["source"] = "scan_login"


def recent_verification(platform: str):
    check = _verification_checks.get(platform)
    if check and time.monotonic() - check["at"] < 60:
        return check["result"]
    return None



def get_account_generation(platform: str) -> int:
    """当前账号代数（无任何变更时 0）。缓存 key 的一部分。"""
    return _account_generation.get(platform, 0)


def _clear_snapshot_sync(platform: str) -> None:
    """清除快照并推进账号代数（同步调用点也走这里，保证代数一致）。"""
    _session_snapshots.pop(platform, None)
    _account_generation[platform] = _account_generation.get(platform, 0) + 1


def _capture_cookie_dict(cookies: List[Dict[str, Any]]) -> Dict[str, str]:
    """把 Playwright cookie 列表转成 name→value 快照（不落任何日志）。"""
    out: Dict[str, str] = {}
    for c in cookies or []:
        name = c.get("name")
        value = c.get("value")
        if name and value is not None:
            out[str(name)] = str(value)
    return out


async def _ensure_xhs_signing_cookies(
    context,
) -> Optional[Dict[str, str]]:
    """Read XHS cookies and initialize the official page once when a1 is absent.

    ``a1`` is signing material for the HTTP client, not a login marker.  The
    returned dictionary is always the latest context state, so callers must
    construct clients from it rather than from a pre-navigation cookie read.
    """
    try:
        cookies = await context.cookies(PLATFORM_COOKIE_URLS["xhs"])
        cookie_dict = _capture_cookie_dict(cookies)
        if cookie_dict.get("a1"):
            return cookie_dict

        stealth_path = resource_path("libs", "stealth.min.js")
        add_init_script = getattr(context, "add_init_script", None)
        if add_init_script is not None and stealth_path.is_file():
            await add_init_script(path=str(stealth_path))

        page = await context.new_page()
        try:
            await page.goto(
                PLATFORM_HOME_URLS["xhs"],
                wait_until="domcontentloaded",
                timeout=15_000,
            )
            await page.wait_for_timeout(1_000)
        finally:
            await page.close()

        # The page may have created a1.  Re-read and return this refreshed
        # state; never continue with the pre-navigation cookie dictionary.
        cookies = await context.cookies(PLATFORM_COOKIE_URLS["xhs"])
        cookie_dict = _capture_cookie_dict(cookies)
        return cookie_dict if cookie_dict.get("a1") else None
    except Exception:
        return None


async def set_session_snapshot(platform: str, cookie_dict: Dict[str, str]) -> None:
    if platform not in PLATFORM_PROFILE_DIRS:
        return
    if not cookie_dict:
        _clear_snapshot_sync(platform)
        return
    _session_snapshots[platform] = dict(cookie_dict)
    _account_generation[platform] = _account_generation.get(platform, 0) + 1


async def clear_session_snapshot(platform: str) -> None:
    _clear_snapshot_sync(platform)


async def clear_all_session_snapshots() -> None:
    for platform in PLATFORM_PROFILE_DIRS:
        _clear_snapshot_sync(platform)


def get_session_snapshot(platform: str) -> Optional[Dict[str, str]]:
    """返回该平台的会话快照（None=无快照，worker 回退浏览器路径）。

    返回值仅供通过 stdin 请求体传输；调用方绝不打印/序列化到响应。
    """
    snap = _session_snapshots.get(platform)
    return dict(snap) if snap else None


def record_search_outcome(platform: str, status: str, timings: Any = None) -> None:
    """Store only safe, terminal search metadata for Platform Doctor."""
    if platform not in PLATFORM_PROFILE_DIRS:
        return
    safe_statuses = {
        "succeeded", "empty", "login_required", "rate_limited",
        "timed_out", "failed", "cancelled",
    }
    if status not in safe_statuses:
        return
    fast_path_used = getattr(timings, "fast_path_used", None)
    allowed_providers = {
        "session_api", "light_api", "browser", "page_api", "public_search"
    }
    provider_used = getattr(timings, "provider_used", None)
    provider_attempts = getattr(timings, "provider_attempts", None)
    if provider_used not in allowed_providers:
        provider_used = None
    if (not isinstance(provider_attempts, list) or
            not all(isinstance(item, str) and item in allowed_providers
                    for item in provider_attempts[:10])):
        provider_attempts = None
    fallback_active = getattr(timings, "fallback_active", None)
    fallback_reason = getattr(timings, "fallback_reason", None)
    _last_search_outcomes[platform] = {
        "status": status,
        "fast_path_used": fast_path_used if isinstance(fast_path_used, bool) else None,
        "provider_used": provider_used,
        "provider_attempts": list(provider_attempts[:10]) if provider_attempts is not None else None,
        "fallback_active": fallback_active if isinstance(fallback_active, bool) else None,
        "fallback_reason": fallback_reason if isinstance(fallback_reason, str) else None,
        "checked_at": datetime.now(timezone.utc),
    }


async def ensure_session_snapshot(platform: str, *, raise_on_error: bool = False) -> Optional[Dict[str, str]]:
    """Restore an in-memory snapshot from an existing browser profile.

    This is intentionally a best-effort session bridge, not account
    verification.  In particular, XHS may be searchable through the browser
    fallback even when an imported Cookie cannot pass the API client's pong:
    opening the official page can initialize browser-managed cookies such as
    ``a1``.  Only the browser profile persists; the captured snapshot remains
    in API memory and is never returned through a worker or API response.

    The shared profile lock makes concurrent hydration candidates re-check
    the snapshot after the first restore, so one job opens the profile at most
    once. Missing profiles or signing cookies are normal best-effort misses.
    """
    if platform not in {"xhs", "zhihu", "bilibili"}:
        return get_session_snapshot(platform)

    current = get_session_snapshot(platform)
    if current is not None and (platform != "zhihu" or current.get("d_c0")):
        return current

    if not profile_dir_for(platform).is_dir():
        return None

    async with _profile_lock(platform):
        # Another hydration task may have restored it while this task waited.
        current = get_session_snapshot(platform)
        if current is not None and (platform != "zhihu" or current.get("d_c0")):
            return current

        playwright = context = None
        try:
            playwright, context, _ = await _launch_profile_context(platform)
            if platform == "xhs":
                cookie_dict = await _ensure_xhs_signing_cookies(context)
            elif platform == "bilibili":
                cookie_dict = _capture_cookie_dict(await context.cookies(PLATFORM_COOKIE_URLS[platform]))
            else:
                cookie_dict = _capture_cookie_dict(await context.cookies(PLATFORM_COOKIE_URLS[platform]))
                if cookie_dict.get("z_c0") and not cookie_dict.get("d_c0"):
                    from tools.light_page import install_light_page_routes, light_goto_kwargs
                    await install_light_page_routes(context)
                    page = await context.new_page()
                    await page.goto(PLATFORM_HOME_URLS[platform], **light_goto_kwargs())
                    cookie_dict = _capture_cookie_dict(await context.cookies(PLATFORM_COOKIE_URLS[platform]))
                if not cookie_dict.get("z_c0") or not cookie_dict.get("d_c0"):
                    return None
            if not cookie_dict:
                return None

            await set_session_snapshot(platform, cookie_dict)
            return dict(cookie_dict)
        except Exception:
            if raise_on_error:
                raise
            # Hydration is best effort.  Do not turn profile/browser failures
            # into a search failure, and do not log the exception because it
            # may contain browser or request details.
            return None
        finally:
            if context is not None:
                try:
                    await context.close()
                except Exception:
                    pass
            if playwright is not None:
                try:
                    await playwright.stop()
                except Exception:
                    pass


def mark_login_required_from_search(platform: str) -> None:
    """Search worker reported ``login_required`` for this platform — downgrade
    the in-memory account state WITHOUT launching a browser, touching the
    profile, or reading cookies.

    This is the only search→account state write; the accounts service remains
    the single source of truth for account status.

    Rules:
    - a local profile exists (previously imported) or the platform was
      connected → status ``expired``;
    - no local profile at all → ``disconnected``;
    - ``verified`` is always False;
    - fixed ``safe_error_code="login_required"`` and a fixed safe_message
      (never the worker's raw body — no cookies/headers/tracebacks);
    - profile dir, display_name, last_verified_at and browser_backend are
      preserved (never cleared, never read);
    - idempotent: repeating the call leaves the same state.
    """
    if platform not in PLATFORM_PROFILE_DIRS:
        raise PlatformError(f"不支持的平台: {platform}")
    st = _state_of(platform)
    profile_exists = profile_dir_for(platform).is_dir()
    if profile_exists or st.get("status") == "connected":
        st["status"] = "expired"
    else:
        st["status"] = "disconnected"
    st["verified"] = False
    st["safe_error_code"] = "login_required"
    # A live rejection invalidates a previously cached successful check.
    _verification_checks.pop(platform, None)
    name = PLATFORM_DISPLAY_NAMES.get(platform, platform)
    st["safe_message"] = f"{name}登录状态已失效，请前往账号设置重新同步"
    # Clear the in-memory snapshot with the account state so stale cookies
    # cannot survive a login-expiry transition.
    _clear_snapshot_sync(platform)


# ── 搜索前登录预检 ──────────────────────────────────────────────────────

# 聚合搜索必须依赖有效会话的平台。抖音走的是公开搜索接口，未登录也能搜，
# 因此绝不参与预检（否则会拦掉本来可用的匿名搜索）。
SEARCH_NEEDS_SESSION = frozenset({"xhs", "bilibili", "zhihu"})

# Chromium cookie 库在 persistent profile 中的相对位置（新版在 Default/Network
# 下，旧版在 Default 或 profile 根目录），按存在顺序探测。
_PROFILE_COOKIE_DB_RELPATHS: tuple = (
    os.path.join("Default", "Network", "Cookies"),
    os.path.join("Default", "Cookies"),
    "Cookies",
)

# 1601-01-01 → 1970-01-01 的秒数（Chromium expires_utc 基准）。
_CHROMIUM_EPOCH_OFFSET_SECONDS = 11644473600

# 搜索预检只认**真正承载登录态**的 cookie。
#
# 不复用上面的 LOGIN_MARKER_NAMES：那是导入期诊断白名单，包含 d_c0、
# DedeUserID 这类匿名/辅助 cookie —— 它们存在并不代表已登录（知乎访问一次
# 官网就会生成 d_c0，`_pong_with_profile` 里 d_c0 单独存在时 pong 仍返回
# False）。用诊断白名单做预检会把"没登录"判成"可能已登录"，从而退回到
# 启动浏览器白等十几秒的老行为。
#
# 这三项正是各平台 pong() 判定登录所依赖的会话 cookie：
# xhs query_self() / bilibili /x/web-interface/nav isLogin / zhihu get_current_user_info()。
_SEARCH_LOGIN_MARKERS: Dict[str, tuple] = {
    "xhs": ("web_session",),
    "bilibili": ("SESSDATA",),
    "zhihu": ("z_c0",),
}


def _live_login_marker_in_profile(platform: str) -> bool:
    """profile 的 cookie 库里是否存在**尚未过期**的登录会话 cookie。

    只读取 cookie NAME + 过期时间，绝不读取 cookie 值（Windows 上值是
    DPAPI 加密的，本函数也不需要它）。

    **失败开放（fail-open）**：任何异常（库不存在/被浏览器占用/表结构变化）
    一律返回 True，即"未能证伪就放行"。这样预检永远不会把本来能成功的
    浏览器备用路径拦掉，最坏情况只是退回原来的慢路径。
    """
    marker_names = _SEARCH_LOGIN_MARKERS.get(platform)
    domain = (PLATFORM_COOKIE_DOMAINS.get(platform) or (None,))[0]
    if not marker_names or not domain:
        return True
    profile_dir = profile_dir_for(platform)
    now_chromium = int((time.time() + _CHROMIUM_EPOCH_OFFSET_SECONDS) * 1_000_000)
    placeholders = ", ".join("?" for _ in marker_names)
    query = (
        f"SELECT 1 FROM cookies WHERE name IN ({placeholders}) "
        "AND host_key LIKE ? AND (expires_utc = 0 OR expires_utc > ?) LIMIT 1"
    )
    for rel in _PROFILE_COOKIE_DB_RELPATHS:
        db_path = profile_dir / rel
        try:
            if not db_path.is_file():
                continue
        except OSError:
            return True
        try:
            # 只读 + 立即完成，不创建任何 journal/WAL 文件。
            conn = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
            try:
                row = conn.execute(
                    query, (*marker_names, f"%{domain}", now_chromium)).fetchone()
            finally:
                conn.close()
            return row is not None
        except Exception:
            # 库文件存在但读不了（被正在运行的浏览器独占等）：不阻断搜索。
            return True
    # 没有任何 cookie 库 → profile 里确实没有登录态。
    return False


def search_login_block(platform: str) -> Optional[str]:
    """搜索开始前的本地登录判定：确定搜不了时返回原因，否则返回 None。

    完全本地：不联网、不启动浏览器，因此是毫秒级的。只拦截三种"必然失败"
    的情况，避免把本来能成功的浏览器备用路径也拦掉：

    0. 存在内存会话快照 → 直接放行（快速路径可以直接用这份会话）；
    1. 账号状态已被判定为 ``expired`` / ``disconnected``；
    2. 既没有内存会话快照、也没有本地 profile（从未同步过）；
    3. profile 存在但 cookie 库里没有任何**未过期**的登录标记 cookie ——
       浏览器路径必然走到 ``pong() == False``，也就是必然要花十几秒启动
       浏览器、导航之后才报"需要登录"。

    第 3 条是这套预检的关键：Playwright 一旦启动就会创建 profile 目录，
    所以"有没有 profile 目录"在真实使用中几乎恒为真，单靠目录判断等于
    没有预检。真正的判据是 profile 里有没有活的登录标记。

    快照判定放在状态判定之前是安全的：所有把状态降级为
    ``expired``/``disconnected`` 的路径（verify 判定非 verified、
    mark_login_required_from_search、删除会话）都会同时清除快照，
    所以"有快照"与"已失效"不会共存。

    只要本地还剩活的登录标记，就仍可能通过浏览器路径搜到内容，放行。
    """
    if platform not in SEARCH_NEEDS_SESSION:
        return None
    name = PLATFORM_DISPLAY_NAMES.get(platform, platform)
    if get_session_snapshot(platform) is not None:
        return None
    state = _state_of(platform)
    if state.get("status") in ("expired", "disconnected"):
        return f"{name}尚未登录或登录已失效，请先到账号设置同步登录状态。"
    if not profile_dir_for(platform).is_dir():
        return f"{name}尚未登录，请先到账号设置同步登录状态。"
    if not _live_login_marker_in_profile(platform):
        return f"{name}尚未登录或登录已失效，请先到账号设置同步登录状态。"
    return None


# ── Browser resolution (server side) ────────────────────────────────────

async def _launch_profile_context(platform: str):
    """Open the platform's headless persistent profile context.

    Uses the shared resolver (CUSTOM_BROWSER_PATH > Chrome > Edge > bundled
    Chromium); exactly one of executable_path/channel is used. Returns
    (playwright, context, backend_name) — caller must close the context and
    stop the playwright instance.
    """
    from playwright.async_api import async_playwright
    from tools.browser_launcher import (
        BrowserUnavailableError, resolve_playwright_browser,
    )

    profile_dir = _resolve_profile_path(platform)
    profile_dir.mkdir(parents=True, exist_ok=True)

    playwright = await async_playwright().start()
    try:
        chromium = playwright.chromium
        executable_path, channel, backend = resolve_playwright_browser()
        if backend == "playwright-chromium":
            bundled_path = chromium.executable_path
            if not bundled_path or not os.path.isfile(bundled_path):
                raise BrowserUnavailableError(
                    "没有找到可用的浏览器，请安装 Chrome 或 Edge 后重试")
            executable_path = bundled_path
        kwargs: Dict[str, Any] = {
            "user_data_dir": str(profile_dir),
            "accept_downloads": True,
            "headless": True,
            "viewport": {"width": 1920, "height": 1080},
        }
        if executable_path:
            kwargs["executable_path"] = executable_path
        elif channel:
            kwargs["channel"] = channel
        context = await chromium.launch_persistent_context(**kwargs)
        return playwright, context, backend
    except Exception:
        await playwright.stop()
        raise


# ── Per-platform profile lock ───────────────────────────────────────────

_profile_locks: Dict[str, asyncio.Lock] = {}


def _profile_lock(platform: str) -> asyncio.Lock:
    lock = _profile_locks.get(platform)
    if lock is None:
        lock = asyncio.Lock()
        _profile_locks[platform] = lock
    return lock


@asynccontextmanager
async def platform_session_context(platform: str, snapshot=None):
    """Lease the account profile, or a transient context for an in-memory session.

    Callers must hold the operation coordinator's platform lease across use.
    No new cookie store, login flow or account verification is created here.
    """
    if platform not in PLATFORM_PROFILE_DIRS:
        raise PlatformError("不支持的平台")
    playwright = context = None
    async with _profile_lock(platform):
        try:
            if profile_dir_for(platform).is_dir():
                playwright, context, _ = await _launch_profile_context(platform)
            elif snapshot:
                from playwright.async_api import async_playwright
                from tools.browser_launcher import resolve_playwright_browser
                playwright = await async_playwright().start()
                executable, channel, _ = resolve_playwright_browser()
                options = {"headless": True}
                if executable:
                    options["executable_path"] = executable
                elif channel:
                    options["channel"] = channel
                browser = await playwright.chromium.launch(**options)
                context = await browser.new_context()
            else:
                raise PermissionError("session required")
            if snapshot:
                await context.add_cookies([{"name": name, "value": value,
                    "url": PLATFORM_HOME_URLS[platform]} for name, value in snapshot.items()])
            yield context
        finally:
            try:
                if context is not None:
                    await context.close()
            finally:
                if playwright is not None:
                    await playwright.stop()


# ── Operation coordinator ───────────────────────────────────────────────

class OperationCoordinator:
    """搜索 / 可见登录 / 账号操作的最小互斥协调（无任务队列框架）。

    - 搜索与可见登录占用"排他"租约（同一时间至多一个）。
    - 账号同步/验证/删除使用共享槽位：不同平台最多
      ``max_account_concurrency`` 个同时执行；同一平台严格串行
      （per-platform key，直到该平台后台验证完成才释放）。
    - 有界验证超时后后台任务继续运行期间，该平台槽位不提前失效
      （由任务 done 回调释放），搜索/登录/同平台操作继续被拒绝。
    - 所有释放幂等；shutdown 调用 ``clear()`` 清空协调状态。
    """

    def __init__(self, max_account_concurrency: int = 2):
        self._lock = asyncio.Lock()
        self._max_accounts = max_account_concurrency
        self._exclusive: Optional[str] = None
        self._account_ops: Dict[str, str] = {}

    async def acquire_exclusive(self, kind: str) -> bool:
        """搜索/登录排他租约；账号操作进行中时拒绝。"""
        async with self._lock:
            if self._exclusive is not None:
                return False
            if self._account_ops:
                return False
            self._exclusive = kind
            return True

    async def release_exclusive(self, kind: str) -> None:
        async with self._lock:
            if self._exclusive == kind:
                self._exclusive = None

    async def acquire_account(self, platform: str, kind: str) -> str:
        """账号操作槽位。返回 ""=成功；否则返回拒绝原因字符串：
        ``search``/``login``（排他占用）、``platform``（同平台进行中）、
        ``slots``（并发槽位已满）。"""
        async with self._lock:
            if self._exclusive is not None:
                return self._exclusive
            if platform in self._account_ops:
                return "platform"
            if len(self._account_ops) >= self._max_accounts:
                return "slots"
            self._account_ops[platform] = kind
            return ""

    async def release_account(self, platform: str) -> None:
        async with self._lock:
            self._account_ops.pop(platform, None)

    async def clear(self) -> None:
        """shutdown：清空全部协调状态（幂等）。"""
        async with self._lock:
            self._exclusive = None
            self._account_ops.clear()


operation_coordinator = OperationCoordinator()


# ── Public operations ───────────────────────────────────────────────────

def _platform_diagnostic(platform: str) -> PlatformDiagnostic:
    """Build a safe, local capability summary; never probes a platform."""
    st = _state_of(platform)
    profile_exists = profile_dir_for(platform).is_dir()
    snapshot = get_session_snapshot(platform)
    outcome = _last_search_outcomes.get(platform)
    outcome_status = outcome.get("status") if outcome else None
    unavailable_statuses = {"login_required", "rate_limited", "timed_out", "failed"}
    search_available = outcome_status not in unavailable_statuses
    # Every current search adapter can expose a user-visible snippet from its
    # search response. Detail hydration is a separate best-effort path.
    snippet_available: Optional[bool] = bool(search_available)

    provider_used = outcome.get("provider_used") if outcome else None
    if outcome_status in unavailable_statuses:
        search_mode = "unavailable"
        fallback_active = False
    elif provider_used == "session_api" or provider_used == "light_api":
        search_mode = "fast_path"
        fallback_active = bool(outcome.get("fallback_active")) if outcome else False
    elif provider_used == "browser":
        search_mode = "browser_fallback" if platform in ("xhs", "bilibili") else "page"
        fallback_active = bool(outcome.get("fallback_active")) if outcome else False
    elif provider_used == "public_search" or provider_used == "page_api":
        search_mode = "page" if platform in ("douyin", "zhihu") else "api"
        fallback_active = bool(outcome.get("fallback_active")) if outcome else False
    elif outcome and outcome.get("fast_path_used") is True:
        search_mode = "fast_path"
        fallback_active = False
    elif outcome and outcome.get("fast_path_used") is False:
        search_mode = "browser_fallback" if platform in ("xhs", "bilibili") else "page"
        fallback_active = platform in ("xhs", "bilibili")
    elif platform in ("xhs", "bilibili"):
        search_mode = "fast_path" if snapshot else "browser_fallback"
        fallback_active = not bool(snapshot)
    elif platform in ("douyin", "zhihu"):
        search_mode = "page"
        fallback_active = False
    else:  # pragma: no cover - platform whitelist guards this branch
        search_mode = None
        fallback_active = False

    if platform == "xhs":
        # XHS detail hydration can restore its API snapshot from the existing
        # browser profile; account verification is not required for this.
        hydration_available: Optional[bool] = True
    elif platform == "bilibili":
        hydration_available = True
    elif platform == "douyin":
        # Douyin does not use ResultHydrator, but DouyinAdapter already maps
        # caption/description/video_description/text/desc from search data.
        hydration_available = False
    else:
        # Zhihu V1 detail clients require the in-memory d_c0 session.
        hydration_available = bool(snapshot and snapshot.get("d_c0"))

    limitation_code: Optional[str] = None
    user_message: Optional[str] = None
    recommended_action: Optional[str] = None
    if outcome_status == "rate_limited":
        limitation_code = "rate_limited"
        user_message = "平台请求暂时受限，可稍后重试。"
        recommended_action = "稍后重试，避免连续请求。"
    elif outcome_status == "login_required":
        limitation_code = "login_required"
        user_message = "当前会话不足，请重新同步账号。"
        recommended_action = "重新同步该平台账号。"
    elif outcome_status in ("failed", "timed_out"):
        limitation_code = "platform_unavailable"
        user_message = "当前无法完成搜索，请稍后重试。"
        recommended_action = "稍后重试；如果持续失败，再重新验证账号。"
    elif platform == "zhihu" and not hydration_available:
        limitation_code = "hydration_unavailable"
        user_message = "搜索结果可提供简介；详情补全需要有效会话。"
        recommended_action = "当前无需处理；需要补抓缺失片段时再同步账号。"
    elif platform == "xhs" and st["status"] == "unverified":
        limitation_code = "account_unverified"
        if fallback_active:
            user_message = "搜索正常，当前正在使用浏览器备用路径。账号未验证不影响当前公开搜索。"
        else:
            user_message = "搜索正常，账号尚未验证，不影响当前公开搜索。"
        recommended_action = "当前无需处理；如平台要求登录，再重新同步账号。"
    elif platform == "xhs" and fallback_active:
        limitation_code = "browser_fallback_active"
        user_message = "搜索正常，当前正在使用浏览器备用路径。"
        recommended_action = "当前无需处理。"
    elif platform == "bilibili" and fallback_active:
        limitation_code = "browser_fallback_active"
        user_message = "搜索正常，当前正在使用浏览器备用路径。"
        recommended_action = "当前无需处理。"
    elif search_available and hydration_available:
        user_message = "搜索和简介补全均可用。"
        recommended_action = "当前无需处理。"
    elif search_available and snippet_available:
        user_message = "搜索结果可提供简介。"
        recommended_action = "当前无需处理。"
    else:
        user_message = "当前搜索可用。"
        recommended_action = "当前无需处理。"

    return PlatformDiagnostic(
        platform=platform,
        search_available=search_available,
        search_mode=search_mode,
        account_state=st["status"],
        snippet_available=snippet_available,
        hydration_available=hydration_available,
        fallback_active=fallback_active,
        limitation_code=limitation_code,
        user_message=user_message,
        recommended_action=recommended_action,
        checked_at=(outcome.get("checked_at") if outcome else
                    _verification_checks.get(platform, {}).get("evidence", {}).get("checked_at")),
    )


def get_platform_diagnostic(platform: str) -> Dict[str, Any]:
    """Return the redacted JSON-ready Platform Doctor payload."""
    return _platform_diagnostic(platform).model_dump(mode="json")


def get_accounts() -> List[Dict[str, Any]]:
    """Per-platform account status — never includes secrets or paths."""
    out: List[Dict[str, Any]] = []
    for platform in ("xhs", "douyin", "bilibili", "zhihu"):
        st = _state_of(platform)
        out.append({
            "platform": platform,
            "profile_exists": profile_dir_for(platform).is_dir(),
            "status": st["status"],
            "verified": bool(st["verified"]),
            "display_name": st["display_name"],
            "last_verified_at": st["last_verified_at"],
            "safe_error_code": st["safe_error_code"],
            "safe_message": st["safe_message"],
            "browser_backend": st["browser_backend"],
            "diagnostic": get_platform_diagnostic(platform),
            "usage": usage_evidence(platform),
            "verification": _verification_checks.get(platform, {}).get("evidence"),
        })
    return out


async def sync_platform_cookies(
    platform: str,
    cookies: List[Dict[str, Any]],
    *,
    cookie_format: str,
    extension_protocol_version: int = 0,
    browser_cookie_store_count: int = 0,
) -> Dict[str, Any]:
    """Import chrome-v1 cookies, then verify the session within a bound.

    The extension sends raw Chrome cookies exactly once; the backend performs
    the single Chrome -> Playwright mapping here. Import success is NOT login
    verification — the profile is re-opened and checked with the platform's
    own pong (bounded by SYNC_VERIFY_TIMEOUT_SECONDS). On timeout the verify
    keeps running in the background and this returns status=verifying.
    """
    if platform not in PLATFORM_PROFILE_DIRS:
        raise PlatformError(f"不支持的平台: {platform}")
    if cookie_format != COOKIE_FORMAT_CHROME_V1:
        raise CookieFormatInvalidError(
            "Cookie 格式不兼容，请重新加载浏览器扩展后再试")
    # Structured protocol check: an old/new extension must get its own error
    # code, not a generic session_import_failed.
    if extension_protocol_version != EXTENSION_PROTOCOL_VERSION:
        raise ExtensionProtocolOutdatedError()

    mapped, diag = validate_chrome_v1_cookie_list(platform, cookies)
    diag["browser_cookie_store_count"] = max(0, int(browser_cookie_store_count or 0))
    if not mapped:
        raise SessionImportError("没有可导入的合法 Cookie", diagnostics=diag)

    # 同步入口在改变状态（syncing）之前捕获此前状态 —— 之后 _import 和
    # verify 都读不到 "connected"（已被覆盖）。显式传给本次验证，绝不依赖
    # 已被覆盖的全局状态，并发时也不会串用其他平台/上一任务的状态。
    previous_status = _state_of(platform).get("status")
    # Clear the old in-memory snapshot before resync (an import or verification
    # 不残留旧 Cookie 快照；验证通过后再重新写入）；账号代数推进 →
    # 结果缓存自动失效。
    _clear_snapshot_sync(platform)

    # Import and verification share one task and one browser context.
    result = await _bounded_verify(
        platform, diag, previous_status=previous_status, mapped=mapped)
    counts = {k: diag[k] for k in (
        "received_cookie_count", "accepted_cookie_count",
        "skipped_cookie_count", "rejected_cookie_count",
        "required_cookie_present", "login_marker_presence",
        "browser_cookie_store_count", "sync_stage")}
    # Stage timings contain integer milliseconds only; no sensitive data.
    sync_timings = result.get("sync_timings_ms") if result else None
    _timings = ({"sync_timings_ms": sync_timings}
                if sync_timings else {})
    if result is None:  # bound expired — verification continues in background
        return {
            "success": True, "platform": platform, "verified": False,
            "status": "verifying", "safe_error_code": None,
            "safe_message": "会话已导入，仍在后台验证", **counts,
        }
    status = result.get("status", "unverified")
    safe_error_code = result.get("safe_error_code")
    safe_message = result.get("safe_message")
    if result.get("verified"):
        return {
            "success": True, "platform": platform, "verified": True,
            "status": status, "safe_error_code": safe_error_code,
            "safe_message": safe_message or "会话导入成功且验证通过",
            **counts, "sync_stage": "completed", **_timings,
        }
    if status == "failed":
        # Real technical failure during verification (browser launch etc.):
        # still a hard failure, not "unverified".
        return {
            "success": False, "platform": platform, "verified": False,
            "status": "failed", "safe_error_code": safe_error_code or "login_verification_failed",
            "safe_message": safe_message or "会话验证失败，请重新同步",
            **counts, **_timings,
        }
    if status == "unavailable":
        # 验证过程不可用（网络/超时/403 风控/导航失败）：不得声称未登录或
        # 会话失效，只报告"当前无法验证登录状态"。
        return {
            "success": True, "platform": platform, "verified": False,
            "status": "unavailable",
            "safe_error_code": safe_error_code or "login_verification_unavailable",
            "safe_message": safe_message or "当前无法验证登录状态，仍可尝试搜索或稍后重新验证",
            **counts, **_timings,
        }
    # 明确未登录（expired / unverified）——会话已导入，公开搜索仍可尝试；
    # 只有真实验证决定 connected vs unverified vs expired。
    return {
        "success": True, "platform": platform, "verified": False,
        "status": status,
        "safe_error_code": safe_error_code or "login_not_verified",
        "safe_message": safe_message or (
            "会话已导入，但尚未确认账号登录。"
            "你仍可以尝试搜索；如搜索需要登录，再重新同步。"),
        **counts, **_timings,
    }


async def _sync_and_verify_platform(
    platform: str, mapped: List[Dict[str, Any]], diag: Dict[str, Any],
    previous_status: Optional[str],
) -> Dict[str, Any]:
    """Phase 4.1：一次 persistent context 完成 导入 + 验证。

    流程：syncing → launch → 清该平台旧 Cookie → add_cookies → 在同一
    context 上 pong → 写 connected/unverified/expired/unavailable/failed →
    finally 只关闭一次 context 与 playwright。任何异常都转为 failed 结果。

    Round 16.1：不再无条件预访问官网 —— 页面只在验证真正需要时创建
    （douyin 需页面读 localStorage；zhihu 缺 d_c0 时才导航）。纯 HTTP
    平台（xhs/bilibili）零页面、零导航。同步全程最多一个验证页面。

    计时（sync_timings_ms，阶段耗时，各自起止，只含整数毫秒）：
    browser_launch_ms / cookie_import_ms / navigation_ms / verification_ms /
    total_ms。
    """
    async with _profile_lock(platform):
        _set_state(platform, status="syncing",
                   safe_error_code=None, safe_message=None)
        was_connected = previous_status == "connected"
        playwright = None
        context = None
        verdict = "unavailable"
        backend = None
        snapshot_cookies: Optional[Dict[str, str]] = None
        sync_t: Dict[str, int] = {}
        _t0 = time.perf_counter()
        try:
            _t = time.perf_counter()
            playwright, context, backend = await _launch_profile_context(platform)
            sync_t["browser_launch_ms"] = int((time.perf_counter() - _t) * 1000)

            _t = time.perf_counter()
            diag["sync_stage"] = "profile_import"
            await _clear_platform_cookies(context, platform)
            await context.add_cookies(mapped)
            sync_t["cookie_import_ms"] = int((time.perf_counter() - _t) * 1000)

            # 同一 context 上验证（导入的 Cookie 就在这个 context 里）。
            # 页面/navigation 由 _pong_with_profile 按平台需要创建，最多一个。
            diag["sync_stage"] = "verification"
            _t = time.perf_counter()
            verdict = await _pong_with_profile(platform, context, metrics=sync_t)
            sync_t["verification_ms"] = int((time.perf_counter() - _t) * 1000)
            if verdict is True or verdict == "verified":
                # 会话确认有效 → 捕获内存快照（绝不落日志/响应）。
                try:
                    urls = PLATFORM_COOKIE_URLS.get(platform, [])
                    snapshot_cookies = _capture_cookie_dict(
                        await context.cookies(urls) if urls else [])
                except Exception:
                    snapshot_cookies = None
        except Exception as exc:
            _set_state(platform, status="failed", verified=False,
                       safe_error_code="session_import_failed",
                       safe_message="会话导入失败，请重新同步")
            sync_t["total_ms"] = int((time.perf_counter() - _t0) * 1000)
            return {
                "success": False, "platform": platform, "verified": False,
                "status": "failed", "safe_error_code": "session_import_failed",
                "safe_message": "会话导入失败，请重新同步",
                "sync_timings_ms": sync_t,
            }
        finally:
            if context is not None:
                try:
                    await context.close()
                except Exception:
                    pass
            if playwright is not None:
                try:
                    await playwright.stop()
                except Exception:
                    pass
        _set_state(platform, browser_backend=backend)
        # Keep a snapshot only after real verification succeeds.
        if verdict is True or verdict == "verified":
            await set_session_snapshot(platform, snapshot_cookies or {})
        else:
            await clear_session_snapshot(platform)
        sync_t["total_ms"] = int((time.perf_counter() - _t0) * 1000)
        result = _finalize_verdict(platform, verdict, backend, was_connected)
        result["sync_timings_ms"] = sync_t
        return result


async def _run_sync_and_verify(
    platform: str, mapped: List[Dict[str, Any]], diag: Dict[str, Any],
    previous_status: Optional[str],
) -> Dict[str, Any]:
    """后台任务包装：任何异常都收敛为 failed 结果，task 正常结束。"""
    try:
        return await _sync_and_verify_platform(
            platform, mapped, diag, previous_status)
    except Exception:
        return {
            "success": False, "platform": platform, "verified": False,
            "status": "failed", "safe_error_code": "session_import_failed",
            "safe_message": "会话导入失败，请重新同步",
        }


async def _clear_platform_cookies(context, platform: str) -> None:
    """Remove ONLY this platform's cookies from the profile before import.

    Playwright's BrowserContext.clear_cookies is KEYWORD-ONLY
    (clear_cookies(*, name=None, domain=None, path=None)); passing a
    positional list raises TypeError. That error used to be swallowed here,
    so stale cookies were never actually cleared. Now nothing is swallowed:
    any read/clear failure propagates to _sync_and_verify_platform and becomes
    session_import_failed — a visible, honest failure.
    """
    urls = PLATFORM_COOKIE_URLS.get(platform, [])
    if not urls:
        return
    existing = await context.cookies(urls)
    domains = {
        d for d in (c.get("domain") for c in existing)
        if d and cookie_domain_allowed(platform, d)  # only whitelisted domains
    }
    for d in domains:
        await context.clear_cookies(domain=d)


# ONE bounded verify task per platform at a time, held by a strong reference
# here: asyncio.wait_for's timeout only cancels the shield, NOT the task, so
# without this dict the task could be garbage-collected mid-flight or —
# worse — a new verify could be spawned while the old one is still running.
# The task removes itself on completion; cancel_verify_tasks() is called on
# app shutdown.
_verify_tasks: Dict[str, asyncio.Task] = {}


async def _bounded_verify(
    platform: str, diag: Dict[str, Any],
    previous_status: Optional[str] = None,
    mapped: Optional[List[Dict[str, Any]]] = None,
) -> Optional[Dict[str, Any]]:
    """在单一后台任务中完成"导入 + 验证"（单次浏览器上下文），
    有界等待 SYNC_VERIFY_TIMEOUT_SECONDS。

    Returns the full result dict; None when the bound expired (the task keeps
    running and the caller reports status=verifying). Exactly one task per
    platform: a timeout keeps the original task running and a later call
    reuses it instead of spawning a duplicate. 每平台最多一个 sync/verify
    task —— 并发 sync 会复用/等待同一任务，绝不并发操作同一 profile。
    """
    diag["sync_stage"] = "verification"
    task = _verify_tasks.get(platform)
    if task is None or task.done():
        if mapped is None:
            return None  # 无 Cookie 无法创建导入+验证任务（仅复用场景）
        task = asyncio.create_task(
            _run_sync_and_verify(platform, mapped, diag, previous_status))
        _verify_tasks[platform] = task
        task.add_done_callback(_make_verify_done_cb(platform))
    try:
        result = await asyncio.wait_for(
            asyncio.shield(task), timeout=SYNC_VERIFY_TIMEOUT_SECONDS)
        return result
    except asyncio.TimeoutError:
        return None
    except Exception:
        # 后台任务内部异常路径已把状态写成 failed；这里如实返回，
        # 让 sync 报告 login_verification_failed，而不是当作"未登录"。
        return {
            "success": False, "platform": platform,
            "verified": False, "status": "failed",
            "safe_error_code": "login_verification_failed",
            "safe_message": "会话验证失败，请重新同步",
        }


def _make_verify_done_cb(platform: str):
    def _on_done(task: asyncio.Task) -> None:
        if _verify_tasks.get(platform) is task:
            _verify_tasks.pop(platform, None)
        # Phase 4.2: 后台验证结束才释放该平台的账号操作槽位
        # （bounded verify 超时返回 verifying 后槽位不提前失效）。
        asyncio.ensure_future(operation_coordinator.release_account(platform))
    return _on_done


def is_verify_active(platform: str) -> bool:
    """该平台是否存在仍在运行的后台验证任务。

    竞态防护：有界验证超时（30s）后任务在后台继续跑，期间再次
    sync / verify / delete 同一平台会与后台任务并发操作同一 profile ——
    路由必须返回 409 verification_in_progress，不允许两个任务顺序覆盖
    同一个平台 profile。任务完成（done callback 弹出）后恢复可操作。
    """
    task = _verify_tasks.get(platform)
    return task is not None and not task.done()


async def cancel_verify_tasks() -> None:
    """Cancel any in-flight bounded verify tasks (app shutdown)."""
    pending = [t for t in _verify_tasks.values() if not t.done()]
    for t in pending:
        t.cancel()
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)
    # Clear coordination state during shutdown.
    await operation_coordinator.clear()
    # Shutdown also clears all in-memory session snapshots.
    await clear_all_session_snapshots()


async def verify_platform(platform: str, previous_status: Optional[str] = None) -> Dict[str, Any]:
    """Open the profile headless and verify the session via the platform's
    own pong / check_login_state. Profile dir existing is NOT proof of login.

    ``previous_status``: the caller (sync_platform_cookies) captures the
    pre-sync state BEFORE it writes status="syncing" — reading the global
    state here would always see "syncing"/"verifying" and was_connected
    would be dead. When None (direct /verify endpoint), falls back to
    reading the current global state.
    """
    if platform not in PLATFORM_PROFILE_DIRS:
        raise PlatformError(f"不支持的平台: {platform}")

    async with _profile_lock(platform):
        # 在把状态改成 verifying 之前记录"此前是否已确认登录"——否则
        # was_connected 恒为 False，expired 分支永远走不到。显式传入的
        # previous_status 优先（同步入口在写入 syncing 之前捕获）。
        if previous_status is not None:
            was_connected = previous_status == "connected"
        else:
            was_connected = _state_of(platform).get("status") == "connected"
        _set_state(platform, status="verifying",
                   safe_error_code=None, safe_message=None)
        playwright = None
        context = None
        verdict = "unavailable"
        backend = None
        snapshot_cookies: Optional[Dict[str, str]] = None
        verify_t: Dict[str, int] = {}
        _t0 = time.perf_counter()
        try:
            _t = time.perf_counter()
            playwright, context, backend = await _launch_profile_context(platform)
            verify_t["browser_launch_ms"] = int(
                (time.perf_counter() - _t) * 1000)
            # Platform client for pong — production function, not test logic.
            _t = time.perf_counter()
            verdict = await _pong_with_profile(platform, context,
                                               metrics=verify_t)
            verify_t["verification_ms"] = int(
                (time.perf_counter() - _t) * 1000)
            if verdict is True or verdict == "verified":
                try:
                    urls = PLATFORM_COOKIE_URLS.get(platform, [])
                    snapshot_cookies = _capture_cookie_dict(
                        await context.cookies(urls) if urls else [])
                except Exception:
                    snapshot_cookies = None
        except Exception as exc:
            # 真实验证阶段的技术失败（浏览器启动等）→ failed，不是 unverified。
            _set_state(platform, status="failed", verified=False,
                       safe_error_code="login_verification_failed",
                       safe_message="会话验证失败，请重新同步")
            raise SessionImportError("会话验证失败") from exc
        finally:
            if context is not None:
                try:
                    await context.close()
                except Exception:
                    pass
            if playwright is not None:
                try:
                    await playwright.stop()
                except Exception:
                    pass
        # An inconclusive check must preserve a still-usable snapshot.
        if verdict is True or verdict == "verified":
            await set_session_snapshot(platform, snapshot_cookies or {})
        elif verdict is False or verdict == "not_logged_in":
            await clear_session_snapshot(platform)
        verify_t["total_ms"] = int((time.perf_counter() - _t0) * 1000)
        result = _finalize_verdict(platform, verdict, backend, was_connected)
        result["sync_timings_ms"] = verify_t
        return result


def _finalize_verdict(
    platform: str, verdict, backend: Optional[str], was_connected: bool,
) -> Dict[str, Any]:
    """三态判定并写状态（sync 与 direct verify 共享的生产逻辑）。

    - verdict True/"verified" → connected（connected 只能来自真实 pong）；
    - "unavailable" → 无法验证（网络/超时/403/导航失败），绝不声称未登录；
    - 明确未登录：之前 connected → expired；从未确认 → unverified。
    profile_exists 本身绝不被当作已登录。
    """
    _verification_checks[platform] = {
        "at": time.monotonic(),
        "evidence": {"status": "verified" if verdict is True else "not_logged_in" if verdict is False else verdict,
                     "checked_at": datetime.now(timezone.utc).isoformat(), "source": "platform_check"},
        "result": {"success": True, "platform": platform, "verified": verdict is True or verdict == "verified", "cached": True},
    }
    # 兼容旧式布尔（True/False monkeypatch 与内部调用点）
    if verdict is True or verdict == "verified":
        _set_state(platform, status="connected", verified=True,
                   display_name=None,
                   last_verified_at=datetime.now(timezone.utc).isoformat(),
                   safe_error_code=None, safe_message=None,
                   browser_backend=backend)
        return {
            "success": True,
            "platform": platform,
            "verified": True,
            "status": "connected",
            "safe_error_code": None,
            "safe_message": "会话验证通过",
        }

    # 三态：验证过程不可用（网络/超时/403 风控/导航失败/客户端技术
    # 错误）→ 无法得出登录结论 —— 不得声称"未登录"或"会话失效"。
    if verdict not in (True, False, "verified", "not_logged_in", "rate_limited"):
        msg = "当前无法验证登录状态，仍可尝试搜索或稍后重新验证"
        _set_state(platform, status="unavailable", verified=False,
                   safe_error_code="login_verification_unavailable",
                   safe_message=msg, browser_backend=backend)
        return {
            "success": True, "platform": platform,
            "verified": False, "status": "unavailable",
            "safe_error_code": "login_verification_unavailable",
            "safe_message": msg,
        }

    # 平台风控（461/471 验证码/访问限制）→ 独立"验证受限"语义：
    # 账号 status 仍用现有 "unavailable"，绝不虚构已连接或登录失效；
    # profile 保留、不清 Cookie、不生成 login_required、不提示重新同步。
    if verdict == "rate_limited":
        msg = "小红书暂时限制了登录验证请求，请稍后再试；这不代表登录状态已经失效"
        _set_state(platform, status="unavailable", verified=False,
                   safe_error_code="login_verification_rate_limited",
                   safe_message=msg, browser_backend=backend)
        return {
            "success": True, "platform": platform,
            "verified": False, "status": "unavailable",
            "safe_error_code": "login_verification_rate_limited",
            "safe_message": msg,
        }

    # 明确未登录（pong=False）：之前曾确认过登录，现在真实验证明确
    # 失效 → expired；从未确认过 → unverified（会话已导入，只是还没
    # 确认登录，公开搜索仍可尝试）。
    if was_connected:
        _set_state(platform, status="expired", verified=False,
                   safe_error_code="login_required",
                   safe_message="会话已失效，请到账号设置重新同步",
                   browser_backend=backend)
        return {
            "success": True,
            "platform": platform,
            "verified": False,
            "status": "expired",
            "safe_error_code": "login_required",
            "safe_message": "会话已失效，请到账号设置重新同步",
        }
    _set_state(platform, status="unverified", verified=False,
               safe_error_code="login_not_verified",
               safe_message="会话已导入，但尚未确认账号登录。"
                           "你仍可以尝试搜索；如搜索需要登录，再重新同步。",
               browser_backend=backend)
    return {
        "success": True,
        "platform": platform,
        "verified": False,
        "status": "unverified",
        "safe_error_code": "login_not_verified",
        "safe_message": "会话已导入，但尚未确认账号登录。"
                        "你仍可以尝试搜索；如搜索需要登录，再重新同步。",
    }


async def _pong_with_profile(
    platform: str, context, metrics: Optional[Dict[str, int]] = None,
) -> str:
    """Verify the profile session with the platform's own client.

    Three-state verdict: "verified" = 真实确认登录；
    "not_logged_in" = 平台检查明确返回未登录；"unavailable" = 网络错误、
    超时、403 风控、导航失败或客户端技术错误 —— 无法得出登录结论，
    绝不能当作"明确未登录"。返回 False/True 的旧式布尔调用点由
    verify_platform 兼容处理。

    页面策略：
    - xhs 优先 HTTP，无法确认时复核同一 profile 的个人入口；风控直接停止；
    - bilibili 纯 HTTP pong，零页面、零导航；
    - douyin 需要页面读 localStorage：只创建并导航一次（同一页面）；
    - zhihu 已有非空 d_c0 → 零页面直接 HTTP pong；缺 d_c0 → 创建同一
      页面，官网 + 搜索页各导航一次刷新 Cookie。
    - 验证页面使用轻量加载（domcontentloaded + image/media/font/
      analytics 拦截）；小红书缺少签名 cookie 时可能先初始化官网。
    - metrics（可选）：记录 navigation_ms（导航阶段耗时，整数毫秒）。
    """
    urls = PLATFORM_COOKIE_URLS.get(platform)
    if not urls:
        return "unavailable"
    if platform == "xhs":
        # XHS HTTP signing needs browser-managed a1.  The helper may perform
        # one official-homepage initialization and always returns the latest
        # cookies, so the client is never built from stale pre-navigation data.
        cookie_dict = await _ensure_xhs_signing_cookies(context)
        if not cookie_dict:
            return "unavailable"
        cookie_str = "; ".join(
            f"{name}={value}" for name, value in cookie_dict.items())
    else:
        try:
            cookies = await context.cookies(urls)
        except Exception:
            # cookie 读取本身失败（浏览器/context 技术问题）→ 无法验证
            return "unavailable"
        if not cookies:
            return "not_logged_in"
        cookie_dict = {c["name"]: c["value"] for c in cookies}
        cookie_str = "; ".join(f"{c['name']}={c['value']}" for c in cookies)
    light_routes_installed = False

    async def _install_light_routes() -> None:
        nonlocal light_routes_installed
        if not light_routes_installed:
            from tools.light_page import install_light_page_routes
            await install_light_page_routes(context)
            light_routes_installed = True

    async def _goto_light(page, url: str) -> None:
        from tools.light_page import light_goto_kwargs
        await page.goto(url, **light_goto_kwargs())

    if platform == "xhs":
        try:
            from media_platform.xhs.client import XiaoHongShuClient
            from media_platform.xhs.exception import XhsRateLimitError
            client = XiaoHongShuClient(
                proxy=None, headers={
                    "User-Agent": _UA, "Cookie": cookie_str,
                }, playwright_page=None, cookie_dict=cookie_dict)
            # raise_on_error=True：平台 pong 不再吞异常 —— 网络错误/超时/
            # 403 风控/接口异常会传播到这里被 except 归为 unavailable；
            # 只有 200 + 明确无登录（success=false）才返回 False →
            # not_logged_in。默认 False 的 console/login 行为保持不变。
            # 461/471 平台风控（验证码/访问限制）→ 独立 verdict
            # "rate_limited"，绝不是"明确未登录"也不是笼统的"无法验证"。
            try:
                verdict = "verified" if bool(await client.pong(raise_on_error=True)) else "not_logged_in"
            except XhsRateLimitError:
                return "rate_limited"
            except Exception:
                verdict = "unavailable"
            if verdict == "verified":
                return verdict
            # HTTP and the browser can disagree. Confirm using the same "Me"
            # control used by scan login, in this exact profile, once only.
            page = None
            try:
                await _install_light_routes()
                page = await context.new_page()
                await _goto_light(page, PLATFORM_HOME_URLS["xhs"])
                await page.wait_for_timeout(1000)
                client.playwright_page = page
                if await client.browser_login_confirmed():
                    return "verified"
            except Exception:
                pass
            finally:
                if page is not None:
                    try:
                        await page.close()
                    except Exception:
                        pass
            return verdict
        except XhsRateLimitError:
            return "rate_limited"
        except Exception:
            return "unavailable"
    if platform == "douyin":
        # DouYinClient.pong 依赖 playwright_page 读 localStorage（快路径），
        # 绝不能传 None —— 从该 profile context 创建真实页面并打开官网，
        # 让页面带上刚导入的 Cookie；导航失败 = 无法访问官方站点确认登录
        # → unavailable（不是"明确未登录"）。全程只创建/导航
        # 这一个页面，使用轻量加载。
        page = None
        try:
            from media_platform.douyin.client import DouYinClient
            await _install_light_routes()
            page = await context.new_page()
        except Exception:
            return "unavailable"
        try:
            try:
                _t_nav = time.perf_counter()
                await _goto_light(page, PLATFORM_HOME_URLS[platform])
                if metrics is not None:
                    metrics["navigation_ms"] = int(
                        (time.perf_counter() - _t_nav) * 1000)
            except Exception:
                return "unavailable"
            client = DouYinClient(
                proxy=None, headers={
                    "User-Agent": _UA, "Cookie": cookie_str,
                }, playwright_page=page, cookie_dict=cookie_dict)
            try:
                # douyin pong 本身不吞异常（localStorage 快路径除外）：
                # cookies 读取失败会直接传播 → unavailable。
                return "verified" if bool(await client.pong(browser_context=context)) else "not_logged_in"
            except Exception:
                return "unavailable"
        finally:
            try:
                await page.close()
            except Exception:
                pass
    if platform == "bilibili":
        try:
            from media_platform.bilibili.client import BilibiliClient
            client = BilibiliClient(
                proxy=None, headers={
                    "User-Agent": _UA, "Cookie": cookie_str,
                }, playwright_page=None, cookie_dict=cookie_dict)
            # raise_on_error=True：403/-412 风控/5xx/网络异常传播 →
            # unavailable；DataFetchError(-101) 平台明确未登录码 → False →
            # not_logged_in；isLogin=false → not_logged_in。
            return "verified" if bool(await client.pong(raise_on_error=True)) else "not_logged_in"
        except Exception:
            return "unavailable"
    if platform == "zhihu":
        # 分级验证（）：
        #   - 平台登录标记检查：profile Cookie 中已有非空 d_c0 → 纯 HTTP
        #     pong 直接验证，零页面、零导航；
        #   - 缺少 d_c0（往往只在真实访问知乎页面后由浏览器生成）→ 才创建
        #     一个页面，官网 + 搜索页导航刷新 Cookie（顺序保持联调结论）。
        # 官网导航失败 → unavailable；搜索页导航失败 → 继续；导航后仍无
        # d_c0 → pong 返回 False → not_logged_in；绝不抛出内部异常。
        page = None
        try:
            from media_platform.zhihu.client import ZhiHuClient
            has_dc0 = any(
                c.get("name") == "d_c0" and c.get("value") for c in cookies)
            use_dict, use_str = cookie_dict, cookie_str
            if not has_dc0:
                try:
                    await _install_light_routes()
                    page = await context.new_page()
                except Exception:
                    return "unavailable"
                try:
                    _t_nav = time.perf_counter()
                    try:
                        await _goto_light(page, PLATFORM_HOME_URLS[platform])
                    except Exception:
                        return "unavailable"
                    try:
                        await _goto_light(
                            page,
                            "https://www.zhihu.com/search?q=python"
                            "&search_source=Guess&utm_content=search_hot"
                            "&type=content")
                    except Exception:
                        pass
                    if metrics is not None:
                        metrics["navigation_ms"] = int(
                            (time.perf_counter() - _t_nav) * 1000)
                    refreshed = await context.cookies(urls)
                    use_dict = {c["name"]: c["value"] for c in refreshed}
                    use_str = "; ".join(
                        f"{c['name']}={c['value']}" for c in refreshed)
                except Exception:
                    return "unavailable"
            client = ZhiHuClient(
                proxy=None, headers={
                    "User-Agent": _UA, "cookie": use_str,
                }, playwright_page=page, cookie_dict=use_dict)
            # raise_on_error=True：ForbiddenError（403 风控/验证码）、
            # DataFetchError、超时等传播 → unavailable；200 响应但无
            # uid/name（明确无用户）→ not_logged_in。
            return "verified" if bool(await client.pong(raise_on_error=True)) else "not_logged_in"
        except Exception:
            return "unavailable"
        finally:
            if page is not None:
                try:
                    await page.close()
                except Exception:
                    pass
    return "unavailable"


_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/128.0.0.0 Safari/537.36"
)


async def delete_platform_session(platform: str) -> Dict[str, Any]:
    """Delete the platform's profile directory (browser_data only)."""
    if platform not in PLATFORM_PROFILE_DIRS:
        raise PlatformError(f"不支持的平台: {platform}")

    async with _profile_lock(platform):
        target = _resolve_profile_path(platform)
        if target.is_dir():
            shutil.rmtree(target)
        _set_state(platform, status="disconnected", verified=False,
                   display_name=None, last_verified_at=None,
                   safe_error_code=None, safe_message=None)
        # Round 16：清除账号 → 内存会话快照一并清除。
        await clear_session_snapshot(platform)
        return {
            "success": True,
            "platform": platform,
            "safe_error_code": None,
            "safe_message": "登录状态已清除",
        }
