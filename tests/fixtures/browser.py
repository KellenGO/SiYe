# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
# This file is part of MediaCrawler project.
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

"""测试共用的假 Playwright 对象。

原先 9 个测试文件各自抄了一份 `_FakeCtx` / `_FakePW` / `_FakePage`，差异只在
几个可参数化的点上（要不要预设 page、close 后 cookies 是否抛错、cookies 是否
按 navigated 切换）。这里收成一个超集替身，每个差异变成构造参数 ——
新写测试不要再抄一份。

刻意**不合并**的东西：各测试里带业务行为的假件（`_FakeDouYinClient`、
`_FakeCrawler`、`_FakeHttpClient` 等）—— 那些编码的是被测逻辑，不是浏览器替身。
"""

from __future__ import annotations

from typing import Any, List, Optional


class FakePage:
    """最小的假 page。

    - ``goto`` 记录调用参数到 ``goto_args``（list of ``(url, kwargs)``），
      便于断言"导航到哪个地址"；
    - 若绑定了 context，``goto`` 会把 ``context.navigated`` 置 True
      （xhs 会话恢复逻辑据此判断"页面已加载过"）；
    - ``evaluate`` 的返回值可用 ``evaluate_result`` 指定（抖音测试要读 UA）。
    """

    def __init__(self, context: Optional["FakeBrowserContext"] = None, *,
                 evaluate_result: Any = None, visible: bool = False) -> None:
        self.context = context
        #: 访问过的 URL（只要地址）
        self.goto_urls: List[str] = []
        #: (url, kwargs) 形式，便于断言带参数的导航
        self.goto_args: List[Any] = []
        self.evaluate_result = evaluate_result
        self.visible = visible
        self.closed = False

    async def goto(self, url: str, **kwargs: Any) -> None:
        self.goto_urls.append(url)
        self.goto_args.append((url, kwargs))
        if self.context is not None:
            self.context.navigated = True

    async def evaluate(self, script: Any, *args: Any) -> Any:
        return self.evaluate_result

    async def add_init_script(self, *args: Any, **kwargs: Any) -> None:
        return None

    async def is_visible(self, *args: Any, **kwargs: Any) -> bool:
        return self.visible

    async def wait_for_timeout(self, _milliseconds: Any) -> None:
        return None

    async def close(self) -> None:
        self.closed = True


class FakePlaywright:
    """假 Playwright 对象。

    既是 ``async with`` 的上下文管理器（非 CDP 分支会这么用它），
    也有 ``chromium`` 属性（``launch_browser`` 被替换后该值本身无意义）与
    幂等的 ``stop()``。
    """

    chromium = object()

    def __init__(self) -> None:
        #: 调过几次 stop()（有测试断言"只停一次"）
        self.stop_count = 0
        #: 是否停过（另一种断言写法）
        self.stopped = False

    async def __aenter__(self) -> "FakePlaywright":
        return self

    async def __aexit__(self, *args: Any) -> bool:
        return False

    async def stop(self) -> None:
        self.stop_count += 1
        self.stopped = True


class FakeBrowserContext:
    """假 BrowserContext，覆盖 9 个测试原先各自的变体。

    参数：
    - ``existing``：cookies() 返回的既有 cookie 列表；
    - ``page``：预设的 page（不传则每次 ``new_page()`` 现造一个并记进 ``pages``）；
    - ``closed``：初始就处于"已关闭"状态；
    - ``initialized_cookies``：与 ``existing`` 不同时，导航后（``navigated``）返回这一组
      —— 用来模拟"页面初始化后 cookie 变了"（xhs 会话恢复）；
    - ``strict_closed``：为 True 时，``closed`` 状态下读 cookies 抛
      ``RuntimeError``，复刻真实 Playwright 在 context 关闭后的行为。
    """

    def __init__(self, *, existing: Optional[list] = None,
                 page: Optional[FakePage] = None,
                 closed: bool = False,
                 initialized_cookies: Optional[list] = None,
                 strict_closed: bool = False) -> None:
        self.existing = list(existing or [])
        self.initialized_cookies = (None if initialized_cookies is None
                                    else list(initialized_cookies))
        self.page = page
        self.closed = closed
        self.strict_closed = strict_closed
        self.navigated = False
        self.pages: List[FakePage] = []
        self.added: List[Any] = []
        self.cleared_domains: List[Any] = []
        self.close_count = 0

    async def cookies(self, urls: Any = None) -> list:
        if self.strict_closed and self.closed:
            raise RuntimeError("Target page, context or browser has been closed")
        if self.initialized_cookies is not None and self.navigated:
            return list(self.initialized_cookies)
        return list(self.existing)

    async def add_cookies(self, mapped: Any) -> None:
        self.added.extend(mapped)

    async def clear_cookies(self, *, name: Any = None, domain: Any = None,
                            path: Any = None) -> None:
        self.cleared_domains.append(domain)

    async def new_page(self, *args: Any, **kwargs: Any) -> FakePage:
        if self.page is not None:
            return self.page
        page = FakePage(self)
        self.pages.append(page)
        return page

    async def route(self, pattern: Any, handler: Any) -> None:
        return None

    async def add_init_script(self, *args: Any, **kwargs: Any) -> None:
        return None

    async def close(self) -> None:
        self.close_count += 1
        self.closed = True


#: 抖音相关测试里假 page 的默认 evaluate 返回值（读 UA 的那个分支）。
DOUYIN_TEST_UA = "Mozilla/5.0 (test UA)"


def douyin_test_context(**kwargs: Any) -> FakeBrowserContext:
    """抖音测试用的上下文：预设一个 evaluate 返回测试 UA 的 page。"""
    return FakeBrowserContext(page=FakePage(evaluate_result=DOUYIN_TEST_UA), **kwargs)
