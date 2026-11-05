"""Repository-wide pytest defaults for external-service tests."""

import os

import pytest

#: FastAPI 的 TestClient 默认把 Host 设为 `testserver`。本机来源守卫
#: （api/routers/local_guard.py）默认只放行回环主机名，所以测试里显式把
#: `testserver` 加进允许列表 —— 这是**测试进程**的放行名单，不改生产默认。
#: 新增攻击场景用例时应显式传 `Host:` / `Origin:` 头来断言 403。
os.environ.setdefault("MC_API_ALLOWED_HOSTS", "testserver")


def pytest_addoption(parser):
    parser.addoption(
        "--run-integration",
        action="store_true",
        default=False,
        help="run tests marked as requiring external services",
    )


def pytest_collection_modifyitems(config, items):
    if config.getoption("--run-integration"):
        return

    skip_integration = pytest.mark.skip(
        reason="integration test skipped; use --run-integration to enable it"
    )
    for item in items:
        if "integration" in item.keywords:
            item.add_marker(skip_integration)
