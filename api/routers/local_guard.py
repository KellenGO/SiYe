# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

"""「只允许本机页面调用」的统一守卫。

背景：API 只监听 127.0.0.1，不等于只有本机能调用。
任意网站都可以向 `http://127.0.0.1:8080/api/...` 发 **simple request**
（例如 `Content-Type: text/plain`、`<form>` 提交），浏览器不会预检，
请求照发、副作用照发生 —— CORS 限制的是**读响应**，不是发请求。
更糟的是 DNS rebinding：攻击者把 `evil.example` 解析到 127.0.0.1 后，
页面与 API 就成了同源，连响应都能读。
所以服务端必须自己判断「这个请求是不是本机页面发起的」。

判断依据（任一不满足即 403）：

1. **Host 头**必须是回环地址。DNS rebinding 的核心特征是 Host 是攻击者
   域名，这一条直接挡掉；它比校验 `request.url.hostname` 可靠，因为后者
   在 rebinding 场景下同样来自攻击者域名。
2. **Origin 头**（浏览器对跨源请求总会带）必须缺失或等于本服务自身来源。
   缺失说明不是浏览器跨源请求（curl、扩展 SW、测试客户端）。
3. **Sec-Fetch-Site** 不能是 `cross-site`。

浏览器扩展的 Service Worker 请求不带 Origin，靠第 2 条放行。

测试与开发可用两个环境变量显式放宽：
- `MC_ALLOW_NONLOCAL_API=1`：跳过 Host 校验（等价于允许 rebinding，仅调试用）。
- `MC_API_ALLOWED_HOSTS`：逗号分隔的额外允许 Host（如 `testserver`）。
"""

from __future__ import annotations

import os

from fastapi import HTTPException, Request

#: 回环主机名。含 IPv6 的方括号与裸写两种形式。
LOOPBACK_HOSTNAMES = frozenset({"localhost", "127.0.0.1", "::1", "[::1]"})

#: 默认允许的来源（前端 dev server 直连后端时会是这些）。
ALLOWED_ORIGINS = frozenset({
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "http://localhost:3000",
    "http://127.0.0.1:3000",
})


def _split_host_header(value: str) -> str:
    """`127.0.0.1:8080` / `[::1]:8080` → 主机名（小写，去方括号）。"""
    host = (value or "").strip().lower()
    if not host:
        return ""
    if host.startswith("["):
        end = host.find("]")
        return host[1:end] if end != -1 else host
    # 裸 IPv6（没有端口）含有多个冒号，不能再 split。
    if host.count(":") > 1:
        return host
    return host.rsplit(":", 1)[0]


def _extra_allowed_hosts() -> set[str]:
    raw = os.environ.get("MC_API_ALLOWED_HOSTS", "")
    return {item.strip().lower() for item in raw.split(",") if item.strip()}


def _host_allowed(host: str) -> bool:
    if not host:
        # HTTP/1.0 或测试客户端可能不带 Host。既然端口只监听回环，允许缺失。
        return True
    if host in LOOPBACK_HOSTNAMES:
        return True
    return host in _extra_allowed_hosts()


def _origin_allowed(origin: str) -> bool:
    if not origin:
        return True
    if origin in ALLOWED_ORIGINS:
        return True
    try:
        hostname = _split_host_header(origin.split("://", 1)[-1].split("/", 1)[0])
    except (AttributeError, TypeError, ValueError):
        return False
    return hostname in LOOPBACK_HOSTNAMES


def allow_nonlocal_api() -> bool:
    """显式放行非本机来源（仅调试/隔离测试使用）。"""
    return os.environ.get("MC_ALLOW_NONLOCAL_API") == "1"


async def local_request(request: Request) -> None:
    """FastAPI 依赖：非本机页面发起的请求一律 403。"""
    if allow_nonlocal_api():
        return

    host = _split_host_header(request.headers.get("host", ""))
    if not _host_allowed(host):
        raise HTTPException(403, "四野接口仅允许本机页面访问")

    origin = request.headers.get("origin", "")
    if origin and not _origin_allowed(origin):
        raise HTTPException(403, "四野接口仅允许本机页面访问")

    if request.headers.get("sec-fetch-site") == "cross-site":
        raise HTTPException(403, "四野接口仅允许本机页面访问")
