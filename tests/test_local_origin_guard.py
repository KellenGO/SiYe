# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

"""本机来源守卫的回归测试。

背景：API 只监听 127.0.0.1 不等于只有本机能调用。任意网页都能向本机端口发
cross-site simple request（不触发 CORS 预检），而 DNS rebinding 更让攻击者
页面与 API 变成同源、连响应都能读。所以服务端必须自己判断来源。

这里锁三件事：
1. 所有 /api 路由（健康检查除外）都挂了守卫 —— 漏一个就前功尽弃；
2. 攻击形态（Host 为外部域名、外部 Origin、cross-site）一律 403；
3. 正常形态（本机页面、dev server、扩展 Service Worker 不带 Origin）放行。
"""

from __future__ import annotations

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from api.main import app

LOOPBACK = "127.0.0.1:8080"

#: 只由一个原因豁免：启动器与托盘需要在后端起来前探测存活。
GUARD_EXEMPT = {"/api/health"}


def _api_routes():
    for route in app.routes:
        if isinstance(route, APIRoute) and route.path.startswith("/api"):
            yield route


def _dependency_names(route: APIRoute) -> set[str]:
    return {getattr(dep.call, "__name__", str(dep.call)) for dep in route.dependant.dependencies}


def test_every_api_route_is_guarded():
    """漏挂守卫的路由会让整套校验失去意义，所以逐个断言。"""
    unguarded = [
        (sorted(route.methods), route.path)
        for route in _api_routes()
        if route.path not in GUARD_EXEMPT and "local_request" not in _dependency_names(route)
    ]
    assert unguarded == [], f"这些 /api 路由没有挂本机来源守卫: {unguarded}"


@pytest.mark.parametrize(
    "headers",
    [
        pytest.param({"Host": "evil.example"}, id="dns-rebinding-host"),
        pytest.param({"Host": "evil.example", "Origin": "http://evil.example"}, id="dns-rebinding-origin"),
        pytest.param({"Host": LOOPBACK, "Origin": "https://evil.example"}, id="cross-site-origin"),
        pytest.param({"Host": LOOPBACK, "Sec-Fetch-Site": "cross-site"}, id="cross-site-fetch-site"),
        pytest.param({"Host": "evil.example", "Origin": "http://evil.example", "Sec-Fetch-Site": "cross-site"}, id="all-signals"),
    ],
)
def test_attack_shapes_are_rejected(headers):
    """攻击形态必须在进入业务逻辑前被挡下（用只读接口断言，不触发写路径）。"""
    with TestClient(app) as client:
        response = client.get("/api/library/stats", headers=headers)
    assert response.status_code == 403, f"{headers} 应当被拒绝，实际 {response.status_code}"


@pytest.mark.parametrize(
    "headers",
    [
        pytest.param({"Host": LOOPBACK}, id="same-origin-page"),
        pytest.param({"Host": "localhost:8080"}, id="localhost"),
        pytest.param({"Host": LOOPBACK, "Origin": "http://127.0.0.1:8080"}, id="matching-origin"),
        pytest.param({"Host": LOOPBACK, "Origin": "http://localhost:5173"}, id="vite-dev-server"),
        pytest.param({"Host": LOOPBACK, "Sec-Fetch-Site": "same-origin"}, id="same-origin"),
        pytest.param({"Host": LOOPBACK, "Sec-Fetch-Site": "none"}, id="address-bar-navigation"),
    ],
)
def test_legitimate_shapes_are_allowed(headers):
    with TestClient(app) as client:
        response = client.get("/api/library/stats", headers=headers)
    assert response.status_code == 200, f"{headers} 应当放行，实际 {response.status_code}"


def test_extension_service_worker_without_origin_is_allowed():
    """扩展 Service Worker 的请求不带 Origin，不能因此被拒。"""
    with TestClient(app) as client:
        response = client.get("/api/library/stats", headers={"Host": LOOPBACK})
    assert response.status_code == 200


def test_health_endpoint_stays_reachable():
    """健康检查必须保持无守卫：启动器要在后端就绪前探测它。"""
    with TestClient(app) as client:
        response = client.get("/api/health", headers={"Host": "evil.example"})
    assert response.status_code == 200


def test_guard_covers_the_destructive_session_endpoint():
    """实证过的路径：清除登录态会 rmtree profile，必须校验来源。

    只断言守卫已挂上，不真的调用 —— 那会删掉本机 browser_data。
    """
    routes = [r for r in _api_routes() if r.path == "/api/search/accounts/{platform}/session"]
    assert routes, "清除登录态的接口不存在了？文档与前端都依赖它"
    for route in routes:
        assert "local_request" in _dependency_names(route)
