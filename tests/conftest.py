# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler/blob/main/tests/conftest.py
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#
# 声明：本代码仅供学习和研究目的使用。使用者应遵守以下原则：
# 1. 不得用于任何商业用途。
# 2. 使用时应遵守目标平台的使用条款和robots.txt规则。
# 3. 不得进行大规模爬取或对平台造成运营干扰。
# 4. 应合理控制请求频率，避免给目标平台带来不必要的负担。
# 5. 不得用于任何非法或不当的用途。
#
# 详细许可条款请参阅项目根目录下的LICENSE文件。
# 使用本代码即表示您同意遵守上述原则和LICENSE中的所有条款。

"""
Pytest configuration and shared fixtures
"""

import pytest


@pytest.fixture(autouse=True)
def _isolate_library_data(monkeypatch, tmp_path):
    """No test may read or write the user's stable collection database."""
    monkeypatch.setenv("SIYE_DATA_DIR", str(tmp_path / "siye-data"))
    from api.services.library_store import reset_library_store
    from api.services.remote_favorites_store import reset_remote_favorites_store

    reset_library_store()
    reset_remote_favorites_store()
    yield
    reset_library_store()
    reset_remote_favorites_store()


@pytest.fixture(autouse=True)
def _default_oneshot_worker_mode():
    """默认以 one-shot 模式运行搜索 worker，避免既有测试泄漏常驻进程。

    需要测试 supervisor 常驻 worker 的用例请自行将
    api.services.search_job_manager.SEARCH_WORKER_MODE 置为 "supervisor"
    （并在用例结束后恢复）。
    """
    import api.services.search_job_manager as sjm

    prev = sjm.SEARCH_WORKER_MODE
    sjm.SEARCH_WORKER_MODE = "oneshot"
    yield
    sjm.SEARCH_WORKER_MODE = prev


@pytest.fixture(autouse=True)
def _default_search_login_precheck_allow(monkeypatch):
    """默认让"搜索前登录预检"放行，使既有用例与 CI/干净检出保持一致。

    预检（``accounts.search_login_block``）读取真实 ``browser_data`` 里的
    profile 判断平台能否搜索。CI 与干净检出上没有任何 profile，预检会把
    xhs / bilibili / zhihu 全部拦掉 —— 而本目录大量用例是"伪造 worker 后
    断言 worker 收到什么"的，它们假设搜索会真的启动 worker。

    需要验证预检本身的用例，请自行把真实函数装回去（见
    ``tests/test_search_login_precheck.py``）：

        import api.services.search_job_manager as sjm
        import api.services.accounts as accounts
        monkeypatch.setattr(sjm, "search_login_block", accounts.search_login_block)

    （``search_job_manager`` 是 ``from .accounts import search_login_block``
    按名导入的，所以必须 patch 它自己的模块属性。）
    """
    import api.services.search_job_manager as sjm

    monkeypatch.setattr(sjm, "search_login_block", lambda platform: None)
