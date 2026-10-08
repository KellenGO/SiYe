# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
# This file is part of MediaCrawler project.
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

"""Pure Chrome cookie validation and mapping for extension imports."""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional


# Platform -> official cookie domains (subdomain match allowed).
PLATFORM_COOKIE_DOMAINS: Dict[str, tuple] = {
    "xhs": ("xiaohongshu.com",),
    "douyin": ("douyin.com",),
    "bilibili": ("bilibili.com",),
    "zhihu": ("zhihu.com",),
}


# Cookie wire contract: the extension sends raw Chrome-cookies-API fields
# exactly once; the backend performs the single Chrome -> Playwright mapping.
COOKIE_FORMAT_CHROME_V1 = "chrome-v1"


CHROME_V1_FIELDS = frozenset({
    "name", "value", "domain", "path", "expirationDate",
    "httpOnly", "secure", "sameSite", "session", "storeId",
})


# Playwright-style sameSite values are NOT part of the chrome-v1 contract.
PLAYWRIGHT_SAME_SITE_VALUES = frozenset({"Lax", "Strict", "None"})


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


class CookieDomainRejectedError(Exception):
    def __init__(self, message: str = "Cookie 域名不在该平台允许的官方域名内",
                 diagnostics: Optional[Dict[str, Any]] = None):
        self.safe_code = "cookie_domain_rejected"
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
