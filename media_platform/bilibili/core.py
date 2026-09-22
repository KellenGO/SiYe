# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler/blob/main/media_platform/bilibili/core.py
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

# -*- coding: utf-8 -*-
# @Author  : relakkes@gmail.com
# @Time    : 2023/12/2 18:44
# @Desc    : Bilibili Crawler

import asyncio
import os
from typing import Dict, List, Optional
from datetime import datetime, timedelta

from playwright.async_api import (
    BrowserContext,
    BrowserType,
    Page,
    Playwright,
    async_playwright,
)
from playwright._impl._errors import TargetClosedError

import config
from base.base_crawler import AbstractCrawler
from base.runtime_paths import resource_path, writable_path
from tools import utils
from tools.browser_launcher import (
    BrowserUnavailableError, resolve_playwright_browser,
)
from tools.cdp_browser import CDPBrowserManager
from var import crawler_type_var, source_keyword_var

from .client import BilibiliClient
from .exception import DataFetchError
from .field import SearchOrderType
from .login import BilibiliLogin


class BilibiliCrawler(AbstractCrawler):
    context_page: Page
    bili_client: BilibiliClient
    browser_context: BrowserContext
    cdp_manager: Optional[CDPBrowserManager]

    def __init__(self):
        super().__init__()
        self.index_url = "https://www.bilibili.com"
        self.cookie_urls = [self.index_url]
        self.user_agent = utils.get_user_agent()
        self.cdp_manager = None

    async def start(self):
        self._begin_phase_timing()
        async with async_playwright() as playwright:
            # Choose launch mode based on configuration
            if config.ENABLE_CDP_MODE:
                utils.logger.info("[BilibiliCrawler] Launching browser using CDP mode")
                self.browser_context = await self.launch_browser_with_cdp(
                    playwright,
                    None,
                    self.user_agent,
                    headless=config.CDP_HEADLESS,
                )
            else:
                utils.logger.info("[BilibiliCrawler] Launching browser using standard mode")
                # Launch a browser context.
                chromium = playwright.chromium
                self.browser_context = await self.launch_browser(chromium, None, self.user_agent, headless=config.HEADLESS)
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

            # Create a client to interact with the xiaohongshu website.
            self.bili_client = await self.create_bilibili_client(None)
            if self.runtime_options and self.runtime_options.extra.get("favorites_sync"):
                from aggregate_search.favorites_sync import REQUEST_TIMEOUT
                self.bili_client.favorites_sync = True
                # 客户端层也压住单请求超时，别让一次请求挂死整轮同步。
                self.bili_client.timeout = REQUEST_TIMEOUT
            if not await self.bili_client.pong():
                if self._login_fail_fast():
                    from base.exceptions import LoginRequiredError
                    raise LoginRequiredError(platform="bilibili", message="B站登录状态已失效，请前往账号设置重新登录")
                login_obj = BilibiliLogin(
                    login_type=config.LOGIN_TYPE,
                    login_phone="",  # your phone number
                    browser_context=self.browser_context,
                    context_page=self.context_page,
                    cookie_str=config.COOKIES,
                )
                await login_obj.begin()
                await self.bili_client.update_cookies(
                    browser_context=self.browser_context,
                    urls=self.cookie_urls,
                )
            self._report_metric("preflight")

            crawler_type_var.set(config.CRAWLER_TYPE)
            if config.CRAWLER_TYPE == "search":
                await self.search()
            elif config.CRAWLER_TYPE == "favorites":
                return await self.fetch_favorites()
            else:
                pass
            utils.logger.info("[BilibiliCrawler.start] Bilibili Crawler finished ...")

    async def search(self):
        """
        search bilibili video
        """
        await self.search_by_keywords()

    async def fetch_favorites(self) -> None:
        """Fetch recent items across the current user's created folders."""
        sync = (getattr(self, "runtime_options", None) and self.runtime_options.extra.get("favorites_sync"))
        if sync:
            return await sync(self.bili_client)
        nav = await self.bili_client.get("/x/web-interface/nav", enable_params_sign=False)
        mid = nav.get("mid") if isinstance(nav, dict) else None
        if not mid:
            from base.exceptions import LoginRequiredError
            raise LoginRequiredError(platform="bilibili", message="B站登录状态已失效")
        folders_response = await self.bili_client.get_created_favorite_folders(int(mid))
        folders = folders_response.get("list", []) if isinstance(folders_response, dict) else []
        remaining = self._result_limit()
        seen: set[str] = set()
        detail_rows = []
        for folder in folders if isinstance(folders, list) else []:
            if remaining <= 0 or not isinstance(folder, dict):
                break
            media_id = folder.get("id") or folder.get("media_id")
            if not media_id:
                continue
            page = 1
            while remaining > 0:
                response = await self.bili_client.get_favorite_folder_contents(
                    int(media_id), page, min(remaining, 20))
                medias = response.get("medias", []) if isinstance(response, dict) else []
                batch = []
                for media in medias if isinstance(medias, list) else []:
                    if not isinstance(media, dict):
                        continue
                    key = str(media.get("bvid") or media.get("id") or "")
                    if not key:
                        continue
                    item = dict(media)
                    item["_collection_name"] = str(folder.get("title") or "默认收藏夹")
                    if key in seen:
                        # Emit folder membership as an update; the worker merges
                        # it into the already collected public result.
                        self._result_sink_call([item])
                        continue
                    seen.add(key)
                    batch.append(item)
                    if len(batch) >= remaining:
                        break
                if batch:
                    self._result_sink_call(batch)
                    detail_rows.extend(batch)
                    remaining -= len(batch)
                has_more = bool(response.get("has_more")) or (
                    isinstance(medias, list) and len(medias) >= min(remaining + len(batch), 20))
                if not has_more or not medias:
                    break
                page += 1
                await asyncio.sleep(config.CRAWLER_MAX_SLEEP_SEC)

        from aggregate_search.favorite_metrics import enrich_favorites
        await enrich_favorites("bilibili", self.bili_client, detail_rows, self._result_sink_call)

    async def search_by_keywords(self):
        """
        search bilibili video with keywords in normal mode
        :return:
        """
        from aggregate_search.pagination import current_pagination
        pagination = current_pagination.get()
        if pagination is not None:
            await pagination.run(self.bili_client)
            return
        utils.logger.info("[BilibiliCrawler.search_by_keywords] Begin search bilibli keywords")
        bili_limit_count = 20  # bilibili limit page fixed value
        if config.CRAWLER_MAX_NOTES_COUNT < bili_limit_count:
            config.CRAWLER_MAX_NOTES_COUNT = bili_limit_count
        max_notes = min(config.CRAWLER_MAX_NOTES_COUNT, self._result_limit())
        start_page = config.START_PAGE  # start page number
        for keyword in config.KEYWORDS.split(","):
            source_keyword_var.set(keyword)
            utils.logger.info(f"[BilibiliCrawler.search_by_keywords] Current search keyword: {keyword}")
            page = 1
            remaining = max_notes
            _search_api_reported = False
            while remaining > 0 and (page - start_page + 1) * bili_limit_count <= config.CRAWLER_MAX_NOTES_COUNT + bili_limit_count:
                if page < start_page:
                    utils.logger.info(f"[BilibiliCrawler.search_by_keywords] Skip page: {page}")
                    page += 1
                    continue

                utils.logger.info(f"[BilibiliCrawler.search_by_keywords] search bilibili keyword: {keyword}, page: {page}")
                videos_res = await self.bili_client.search_video_by_keyword(
                    keyword=keyword,
                    page=page,
                    page_size=bili_limit_count,
                    order=SearchOrderType.DEFAULT,
                    pubtime_begin_s=0,  # Publish date start timestamp
                    pubtime_end_s=0,  # Publish date end timestamp
                )
                if not _search_api_reported:
                    self._report_metric("search_api")
                    _search_api_reported = True
                video_list: List[Dict] = videos_res.get("result")

                if not video_list:
                    utils.logger.info(f"[BilibiliCrawler.search_by_keywords] No more videos for '{keyword}', moving to next keyword.")
                    break

                # Aggregate search uses the lightweight list response directly.
                videos_to_fetch = video_list[:remaining]
                self._result_sink_call(videos_to_fetch)
                remaining -= len(videos_to_fetch)
                page += 1
                if remaining > 0:
                    await asyncio.sleep(config.CRAWLER_MAX_SLEEP_SEC)
                continue
    async def create_bilibili_client(self, httpx_proxy: Optional[str]) -> BilibiliClient:
        """
        create bilibili client
        :param httpx_proxy: httpx proxy
        :return: bilibili client
        """
        utils.logger.info("[BilibiliCrawler.create_bilibili_client] Begin create bilibili API client ...")
        cookie_str, cookie_dict = await utils.convert_browser_context_cookies(
            self.browser_context,
            urls=self.cookie_urls,
        )
        bilibili_client_obj = BilibiliClient(
            proxy=httpx_proxy,
            headers={
                "User-Agent": self.user_agent,
                "Cookie": cookie_str,
                "Origin": "https://www.bilibili.com",
                "Referer": "https://www.bilibili.com",
                "Content-Type": "application/json;charset=UTF-8",
            },
            playwright_page=self.context_page,
            cookie_dict=cookie_dict,
        )
        return bilibili_client_obj

    async def create_bilibili_client_from_snapshot(
        self, cookie_dict: Dict[str, str],
    ) -> BilibiliClient:
        """Round 16 fast path：从内存会话快照构造 client，无浏览器（page=None）。
        WBI Key 走 /x/web-interface/nav HTTP 路径；Cookie 语义与浏览器路径一致。"""
        cookie_str = "; ".join(f"{k}={v}" for k, v in cookie_dict.items())
        return BilibiliClient(
            proxy=None,
            headers={
                "User-Agent": self.user_agent,
                "Cookie": cookie_str,
                "Origin": "https://www.bilibili.com",
                "Referer": "https://www.bilibili.com",
                "Content-Type": "application/json;charset=UTF-8",
            },
            playwright_page=None,
            cookie_dict=dict(cookie_dict),
            reuse_http_client=self._reuse_http_client(),
        )

    async def launch_browser(
        self,
        chromium: BrowserType,
        playwright_proxy: Optional[Dict],
        user_agent: Optional[str],
        headless: bool = True,
    ) -> BrowserContext:
        """
        launch browser and create browser context
        :param chromium: chromium browser
        :param playwright_proxy: playwright proxy
        :param user_agent: user agent
        :param headless: headless mode
        :return: browser context
        """
        utils.logger.info("[BilibiliCrawler.launch_browser] Begin create browser context ...")
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
            f"[BilibiliCrawler.launch_browser] Using browser backend: {backend}")
        launch_kwargs: Dict = {
            "accept_downloads": True,
            "headless": headless,
            "proxy": playwright_proxy,  # type: ignore
            "viewport": {"width": 1920, "height": 1080},
            "user_agent": user_agent,
        }
        if executable_path:
            launch_kwargs["executable_path"] = executable_path
        elif channel:
            launch_kwargs["channel"] = channel

        if config.SAVE_LOGIN_STATE:
            # feat issue #14
            # we will save login state to avoid login every time
            user_data_dir = str(writable_path(
                "browser_data", config.USER_DATA_DIR % config.PLATFORM))
            browser_context = await chromium.launch_persistent_context(
                user_data_dir=user_data_dir,
                **launch_kwargs,
            )
            return browser_context
        else:
            # type: ignore
            browser = await chromium.launch(
                headless=headless, proxy=playwright_proxy,
                executable_path=executable_path, channel=channel)
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
        Launch browser using CDP mode
        """
        try:
            self.cdp_manager = CDPBrowserManager()
            browser_context = await self.cdp_manager.launch_and_connect(
                playwright=playwright,
                playwright_proxy=playwright_proxy,
                user_agent=user_agent,
                headless=headless,
            )

            # Display browser information
            browser_info = await self.cdp_manager.get_browser_info()
            utils.logger.info(f"[BilibiliCrawler] CDP browser info: {browser_info}")

            return browser_context

        except Exception as e:
            utils.logger.error(f"[BilibiliCrawler] CDP mode launch failed, fallback to standard mode: {e}")
            # Fallback to standard mode
            chromium = playwright.chromium
            return await self.launch_browser(chromium, playwright_proxy, user_agent, headless)

    async def close(self):
        """Close browser context"""
        try:
            # If using CDP mode, special handling is required
            if self.cdp_manager:
                await self.cdp_manager.cleanup()
                self.cdp_manager = None
            elif self.browser_context:
                await self.browser_context.close()
            utils.logger.info("[BilibiliCrawler.close] Browser context closed ...")
        except TargetClosedError:
            utils.logger.warning("[BilibiliCrawler.close] Browser context was already closed.")
        except Exception as e:
            utils.logger.error(f"[BilibiliCrawler.close] An error occurred during close: {e}")
