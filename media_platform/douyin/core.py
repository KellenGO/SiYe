# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler/blob/main/media_platform/douyin/core.py
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

import asyncio
import os
from typing import Any, Dict, List, Optional

from playwright.async_api import (
    BrowserContext,
    BrowserType,
    Page,
    Playwright,
    async_playwright,
)

import config
from base.base_crawler import AbstractCrawler
from base.runtime_paths import resource_path, writable_path
from tools import utils
from tools.browser_launcher import (
    BrowserUnavailableError, resolve_playwright_browser,
)
from tools.cdp_browser import CDPBrowserManager
from var import crawler_type_var, source_keyword_var

from .client import DouYinClient
from .exception import DataFetchError
from .field import PublishTimeType
from .login import DouYinLogin

# 抖音搜索响应中已知的风控/验证码 status_code（其余非零码一律按未知处理，
# 绝不当成正常空结果，也不猜测成登录失效）。
DOUYIN_RATE_LIMIT_STATUS_CODES = frozenset({21111, 21004, -20})


def _safe_status_code(value: Any) -> Optional[int]:
    """把 status_code 安全地规范化为整数（Round 11 边界）。

    - bool 一律拒绝（bool 是 int 子类，True/False 绝不是合法状态码）；
    - 整数原样返回；
    - 字符串去除首尾空白后若全为数字 → 转 int（"21111" 不得绕过分类）；
    - 其余类型/非数字字符串 → None（未知，绝不误判为已知风控码）。
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.isdigit():
            return int(stripped)
    return None


def _classify_douyin_search_response(posts_res: Dict) -> Optional[str]:
    """分类抖音搜索接口响应（生产函数，仅看安全字段）。

    返回 None=正常结果可继续；"empty"=明确成功响应中的空列表（正常停止
    翻页）；其余情况抛 ``DataFetchError``（带 stage/platform_code/
    safe_message 安全 metadata，绝不含响应体、URL 参数、Cookie、header
    或 traceback）：
    - status_code 为已知风控码或 status_msg（casefold 后）含风控特征
      → 风控/限流；
    - 其他非零 status_code / 非法类型 / 异常响应形状 → 未知错误（failed
      语义，由 worker 按 metadata 分类，默认 failed、绝不猜测登录失效）。
    """
    if not isinstance(posts_res, dict):
        raise DataFetchError(
            "抖音搜索返回了无法识别的响应", stage="search_list",
            safe_message="抖音搜索接口暂时不可用，请稍后重试")
    status_code = _safe_status_code(posts_res.get("status_code"))
    data = posts_res.get("data")
    if status_code == 0:
        if isinstance(data, list):
            # 明确成功响应：空列表才是正常 empty；有数据正常继续。
            return "empty" if not data else None
        raise DataFetchError(
            "抖音搜索返回了异常响应", stage="search_list", platform_code=0,
            safe_message="抖音搜索接口暂时不可用，请稍后重试")
    # casefold：大小写不敏感匹配风控特征（"CAPTCHA required" / "Verify
    # now" 等英文文案大小写不一）。status_code 已规范化 —— bool/非法
    # 字符串为 None，不匹配任何已知码。
    status_msg = str(posts_res.get("status_msg", "") or "").casefold()
    if status_code in DOUYIN_RATE_LIMIT_STATUS_CODES or any(
            kw in status_msg for kw in ("verify", "captcha", "block",
                                        "风控", "验证码", "受限")):
        raise DataFetchError(
            "抖音搜索请求被风控拦截", stage="search_list",
            platform_code=status_code,
            safe_message="抖音搜索请求被平台风控拦截，请稍后重试")
    raise DataFetchError(
        "抖音搜索接口返回错误", stage="search_list",
        platform_code=status_code,
        safe_message="抖音搜索接口暂时不可用，请稍后重试")


class DouYinCrawler(AbstractCrawler):
    context_page: Page
    dy_client: DouYinClient
    browser_context: BrowserContext
    cdp_manager: Optional[CDPBrowserManager]

    def __init__(self) -> None:
        super().__init__()
        self.index_url = "https://www.douyin.com"
        self.cookie_urls = [
            "https://douyin.com",
            self.index_url,
            "https://creator.douyin.com",
            "https://douhot.douyin.com",
            "https://live.douyin.com",
        ]
        self.cdp_manager = None
        # 公开搜索（未登录也继续搜）的开关：见 start() —— 一旦走了这条路，
        # 平台对匿名搜索常返回空列表，此时必须报"登录态失效"而不是"无结果"。
        self.anonymous_public_search = False

    async def start(self) -> None:
        self._begin_phase_timing()
        async with async_playwright() as playwright:
            # Select startup mode based on configuration
            if config.ENABLE_CDP_MODE:
                utils.logger.info("[DouYinCrawler] 使用CDP模式启动浏览器")
                self.browser_context = await self.launch_browser_with_cdp(
                    playwright,
                    None,
                    None,
                    headless=config.CDP_HEADLESS,
                )
            else:
                utils.logger.info("[DouYinCrawler] 使用标准模式启动浏览器")
                # Launch a browser context.
                chromium = playwright.chromium
                self.browser_context = await self.launch_browser(
                    chromium,
                    None,
                    user_agent=None,
                    headless=config.HEADLESS,
                )
                # stealth.min.js is a js script to prevent the website from detecting the crawler.
                await self.browser_context.add_init_script(
                    path=str(resource_path("libs", "stealth.min.js")))
            self._report_metric("browser_launch")
            await self._apply_light_page()

            self.context_page = await self.browser_context.new_page()
            if self._light_page():
                from tools.light_page import light_goto_kwargs
                await self.context_page.goto(self.index_url, **light_goto_kwargs())
            else:
                await self.context_page.goto(self.index_url)
            self._report_metric("navigation")

            self.dy_client = await self.create_douyin_client(None)
            pong_ok = await self.dy_client.pong(browser_context=self.browser_context)
            if not pong_ok:
                # pong 未确认登录：默认行为不变（fail_fast 抛错 / 交互式扫码），
                # 但聚合搜索可开启 allow_public_search —— 跳过登录门禁直接
                # 尝试公开搜索（搜索 API 不登录也可能返回结果）。
                self.anonymous_public_search = bool(self._allow_public_search())
                if self._login_fail_fast() and not self._allow_public_search():
                    from base.exceptions import LoginRequiredError
                    raise LoginRequiredError(platform="douyin", message="抖音登录状态已失效，请前往账号设置重新登录")
                if not self._login_fail_fast() and not self._allow_public_search():
                    login_obj = DouYinLogin(
                        login_type=config.LOGIN_TYPE,
                        login_phone="",  # you phone number
                        browser_context=self.browser_context,
                        context_page=self.context_page,
                        cookie_str=config.COOKIES,
                    )
                    await login_obj.begin()
                    await self.dy_client.update_cookies(
                        browser_context=self.browser_context,
                        urls=self.cookie_urls,
                    )
            self._report_metric("preflight")
            # 排障一行：抖音"搜不到内容"时先要知道登录态到底通没通、
            # 是不是走了匿名公开搜索（匿名搜索常只拿回空列表）。
            utils.logger.info("[DouYin] preflight pong_ok=%s anonymous_public_search=%s",
                              pong_ok, self.anonymous_public_search)
            crawler_type_var.set(config.CRAWLER_TYPE)
            if config.CRAWLER_TYPE == "search":
                await self.search()
            elif config.CRAWLER_TYPE == "favorites":
                await self.fetch_favorites()

            utils.logger.info("[DouYinCrawler.start] Douyin Crawler finished ...")

    async def search(self) -> None:
        from aggregate_search.pagination import current_pagination
        pagination = current_pagination.get()
        if pagination is not None:
            await pagination.run(self.dy_client)
            return
        utils.logger.info("[DouYinCrawler.search] Begin search douyin keywords")
        dy_limit_count = 10  # douyin limit page fixed value
        if config.CRAWLER_MAX_NOTES_COUNT < dy_limit_count:
            config.CRAWLER_MAX_NOTES_COUNT = dy_limit_count
        max_notes = min(config.CRAWLER_MAX_NOTES_COUNT, self._result_limit())
        start_page = config.START_PAGE  # start page number
        for keyword in config.KEYWORDS.split(","):
            source_keyword_var.set(keyword)
            utils.logger.info(f"[DouYinCrawler.search] Current keyword: {keyword}")
            aweme_list: List[str] = []
            page = 0
            dy_search_id = ""
            remaining = max_notes
            emitted = 0
            _search_api_reported = False
            while remaining > 0 and (page - start_page + 1) * dy_limit_count <= config.CRAWLER_MAX_NOTES_COUNT + dy_limit_count:
                if page < start_page:
                    utils.logger.info(f"[DouYinCrawler.search] Skip {page}")
                    page += 1
                    continue
                try:
                    utils.logger.info(f"[DouYinCrawler.search] search douyin keyword: {keyword}, page: {page}")
                    posts_res = await self.dy_client.search_info_by_keyword(
                        keyword=keyword,
                        offset=page * dy_limit_count - dy_limit_count,
                        publish_time=PublishTimeType(config.PUBLISH_TIME_TYPE),
                        search_id=dy_search_id,
                    )
                    # 只有明确成功响应中的空列表才是正常 empty；status_code
                    # 非零 / 错误字段 / 异常形状必须抛 DataFetchError（带
                    # 安全 metadata），绝不能误报为"没有结果"。
                    if _classify_douyin_search_response(posts_res) == "empty":
                        utils.logger.info(f"[DouYinCrawler.search] search douyin keyword: {keyword}, page: {page} is empty")
                        break
                except DataFetchError:
                    if self._strict_errors():
                        raise
                    utils.logger.error(f"[DouYinCrawler.search] search douyin keyword: {keyword} failed")
                    break
                if not _search_api_reported:
                    self._report_metric("search_api")
                    _search_api_reported = True

                page += 1
                if "data" not in posts_res:
                    utils.logger.error(f"[DouYinCrawler.search] search douyin keyword: {keyword} failed，账号也许被风控了。")
                    break
                dy_search_id = posts_res.get("extra", {}).get("logid", "")
                page_aweme_list = []
                page_aweme_data: List[Dict] = []
                for post_item in posts_res.get("data"):
                    if remaining <= 0:
                        break
                    try:
                        aweme_info: Dict = (post_item.get("aweme_info") or post_item.get("aweme_mix_info", {}).get("mix_items")[0])
                    except TypeError:
                        continue
                    aweme_list.append(aweme_info.get("aweme_id", ""))
                    page_aweme_list.append(aweme_info.get("aweme_id", ""))
                    page_aweme_data.append(aweme_info)
                    remaining -= 1

                # ── aggregate-search hook: push native results to sink ──
                self._result_sink_call(page_aweme_data)
                emitted += len(page_aweme_data)

                # Sleep after each page navigation（已达 limit 时无需再等下一页）
                if remaining > 0:
                    await asyncio.sleep(config.CRAWLER_MAX_SLEEP_SEC)
                    utils.logger.info(f"[DouYinCrawler.search] Sleeping for {config.CRAWLER_MAX_SLEEP_SEC} seconds after page {page-1}")
            utils.logger.info(f"[DouYinCrawler.search] keyword:{keyword}, aweme_list:{aweme_list}")
            if emitted == 0 and self.anonymous_public_search:
                # 不登录也继续搜（allow_public_search）时，匿名请求常常只拿回空列表。
                # 这时报"无结果"是骗人的 —— 用户看到 0 条却不知道要去重新登录。
                from base.exceptions import LoginRequiredError
                raise LoginRequiredError(
                    platform="douyin",
                    message="抖音未登录或登录态失效，公开搜索没有返回内容，请前往账号设置重新登录")

    async def fetch_favorites(self) -> None:
        """Fetch a bounded slice of the logged-in account's collected videos."""
        remaining = self._result_limit()
        cursor = 0
        while remaining > 0:
            response = await self.dy_client.get_collected_awemes(cursor, min(remaining, 20))
            items = response.get("aweme_list", []) if isinstance(response, dict) else []
            if not isinstance(items, list) or not items:
                break
            self._result_sink_call(items[:remaining])
            remaining -= len(items[:remaining])
            if not response.get("has_more"):
                break
            next_cursor = response.get("cursor") or response.get("max_cursor")
            try:
                next_cursor = int(next_cursor)
            except (TypeError, ValueError):
                break
            if next_cursor == cursor:
                break
            cursor = next_cursor
            if remaining > 0:
                await asyncio.sleep(config.CRAWLER_MAX_SLEEP_SEC)

    async def create_douyin_client(self, httpx_proxy: Optional[str]) -> DouYinClient:
        """Create douyin client"""
        cookie_str, cookie_dict = await utils.convert_browser_context_cookies(
            self.browser_context,
            urls=self.cookie_urls,
        )  # type: ignore
        douyin_client = DouYinClient(
            proxy=httpx_proxy,
            headers={
                "User-Agent": await self.context_page.evaluate("() => navigator.userAgent"),
                "Cookie": cookie_str,
                "Host": "www.douyin.com",
                "Origin": "https://www.douyin.com/",
                "Referer": "https://www.douyin.com/",
                "Content-Type": "application/json;charset=UTF-8",
            },
            playwright_page=self.context_page,
            cookie_dict=cookie_dict,
            reuse_http_client=self._reuse_http_client(),
        )
        return douyin_client

    async def launch_browser(
        self,
        chromium: BrowserType,
        playwright_proxy: Optional[Dict],
        user_agent: Optional[str],
        headless: bool = True,
    ) -> BrowserContext:
        """Launch browser and create browser context"""
        # Resolve browser: CUSTOM_BROWSER_PATH > Chrome > Edge > bundled Chromium.
        # Never unconditionally use channel="chrome".
        executable_path, channel, backend = resolve_playwright_browser()
        if backend == "playwright-chromium":
            # Bundled Chromium is the fallback — verify it is actually installed.
            bundled_path = getattr(chromium, "executable_path", None)
            if not bundled_path or not os.path.isfile(bundled_path):
                raise BrowserUnavailableError(
                    "没有找到可用的浏览器，请安装 Chrome 或 Edge 后重试")
            executable_path = bundled_path
        utils.logger.info(
            f"[DouYinCrawler.launch_browser] Using browser backend: {backend}")
        launch_kwargs: Dict = {
            "accept_downloads": True,
            "headless": headless,
            "proxy": playwright_proxy,  # type: ignore
            "viewport": {
                "width": 1920,
                "height": 1080
            },
            "user_agent": user_agent,
        }
        if executable_path:
            launch_kwargs["executable_path"] = executable_path
        elif channel:
            launch_kwargs["channel"] = channel

        if config.SAVE_LOGIN_STATE:
            user_data_dir = str(writable_path(
                "browser_data", config.USER_DATA_DIR % config.PLATFORM))
            browser_context = await chromium.launch_persistent_context(
                user_data_dir=user_data_dir,
                **launch_kwargs,
            )  # type: ignore
            return browser_context
        else:
            browser = await chromium.launch(
                headless=headless, proxy=playwright_proxy,
                executable_path=executable_path, channel=channel)  # type: ignore
            browser_context = await browser.new_context(viewport={"width": 1920, "height": 1080}, user_agent=user_agent)
            return browser_context

    async def launch_browser_with_cdp(
        self,
        playwright: Playwright,
        playwright_proxy: Optional[Dict],
        user_agent: Optional[str],
        headless: bool = True,
    ) -> BrowserContext:
        """
        使用CDP模式启动浏览器
        """
        try:
            self.cdp_manager = CDPBrowserManager()
            browser_context = await self.cdp_manager.launch_and_connect(
                playwright=playwright,
                playwright_proxy=playwright_proxy,
                user_agent=user_agent,
                headless=headless,
            )

            # Add anti-detection script
            await self.cdp_manager.add_stealth_script()

            # Show browser information
            browser_info = await self.cdp_manager.get_browser_info()
            utils.logger.info(f"[DouYinCrawler] CDP浏览器信息: {browser_info}")

            return browser_context

        except Exception as e:
            utils.logger.error(f"[DouYinCrawler] CDP模式启动失败，回退到标准模式: {e}")
            # Fall back to standard mode
            chromium = playwright.chromium
            return await self.launch_browser(chromium, playwright_proxy, user_agent, headless)

    async def close(self) -> None:
        """Close browser context"""
        # If you use CDP mode, special processing is required
        if self.cdp_manager:
            await self.cdp_manager.cleanup()
            self.cdp_manager = None
        else:
            await self.browser_context.close()
        utils.logger.info("[DouYinCrawler.close] Browser context closed ...")
