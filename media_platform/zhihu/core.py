# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler/blob/main/media_platform/zhihu/core.py
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
import asyncio
import os
from typing import Dict, List, Optional

from playwright.async_api import (
    BrowserContext,
    BrowserType,
    Page,
    Playwright,
    async_playwright,
)

import config
from constant import zhihu as constant
from base.base_crawler import AbstractCrawler
from base.runtime_paths import resource_path, writable_path
from model.m_zhihu import ZhihuContent
from tools import utils
from tools.browser_launcher import (
    BrowserUnavailableError, resolve_playwright_browser,
)
from tools.cdp_browser import CDPBrowserManager
from var import crawler_type_var, source_keyword_var

from .client import ZhiHuClient
from .exception import DataFetchError
from .help import ZhihuExtractor, judge_zhihu_url
from .login import ZhiHuLogin


class ZhihuCrawler(AbstractCrawler):
    context_page: Page
    zhihu_client: ZhiHuClient
    browser_context: BrowserContext
    cdp_manager: Optional[CDPBrowserManager]

    def __init__(self) -> None:
        super().__init__()
        self.index_url = "https://www.zhihu.com"
        self.cookie_urls = [self.index_url]
        # self.user_agent = utils.get_user_agent()
        self.user_agent = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
        self._extractor = ZhihuExtractor()
        self.cdp_manager = None

    async def start(self) -> None:
        """
        Start the crawler
        Returns:

        """
        async with async_playwright() as playwright:
            # Choose launch mode based on configuration
            if config.ENABLE_CDP_MODE:
                utils.logger.info("[ZhihuCrawler] Launching browser in CDP mode")
                self.browser_context = await self.launch_browser_with_cdp(
                    playwright,
                    None,
                    self.user_agent,
                    headless=config.CDP_HEADLESS,
                )
            else:
                utils.logger.info("[ZhihuCrawler] Launching browser in standard mode")
                # Launch a browser context.
                chromium = playwright.chromium
                self.browser_context = await self.launch_browser(
                    chromium, None, self.user_agent, headless=config.HEADLESS
                )
                # stealth.min.js is a js script to prevent the website from detecting the crawler.
                await self.browser_context.add_init_script(
                    path=str(resource_path("libs", "stealth.min.js")))

            self.context_page = await self.browser_context.new_page()
            await self.context_page.goto(self.index_url, wait_until="domcontentloaded")

            # Create a client to interact with the zhihu website.
            self.zhihu_client = await self.create_zhihu_client(None)
            if not await self.zhihu_client.pong():
                if self._login_fail_fast():
                    from base.exceptions import LoginRequiredError
                    raise LoginRequiredError(platform="zhihu", message="知乎登录状态已失效，请前往账号设置重新登录")
                login_obj = ZhiHuLogin(
                    login_type=config.LOGIN_TYPE,
                    login_phone="",  # input your phone number
                    browser_context=self.browser_context,
                    context_page=self.context_page,
                    cookie_str=config.COOKIES,
                )
                await login_obj.begin()
                await self.zhihu_client.update_cookies(
                    browser_context=self.browser_context,
                    urls=self.cookie_urls,
                )

            # Zhihu's search API requires opening the search page first to access cookies, homepage alone won't work
            utils.logger.info(
                "[ZhihuCrawler.start] Zhihu navigating to search page to get search page cookies, this process takes about 5 seconds"
            )
            await self.context_page.goto(
                f"{self.index_url}/search?q=python&search_source=Guess&utm_content=search_hot&type=content"
            )
            await asyncio.sleep(5)
            await self.zhihu_client.update_cookies(
                browser_context=self.browser_context,
                urls=self.cookie_urls,
            )

            crawler_type_var.set(config.CRAWLER_TYPE)
            if config.CRAWLER_TYPE == "search":
                await self.search()
            elif config.CRAWLER_TYPE == "favorites":
                await self.fetch_favorites()
            else:
                pass

            utils.logger.info("[ZhihuCrawler.start] Zhihu Crawler finished ...")

    async def search(self) -> None:
        """Search Zhihu content and send native results to the sink."""
        utils.logger.info("[ZhihuCrawler.search] Begin search zhihu keywords")
        zhihu_limit_count = 20  # zhihu limit page fixed value
        if config.CRAWLER_MAX_NOTES_COUNT < zhihu_limit_count:
            config.CRAWLER_MAX_NOTES_COUNT = zhihu_limit_count
        max_notes = min(config.CRAWLER_MAX_NOTES_COUNT, self._result_limit())
        start_page = config.START_PAGE
        for keyword in config.KEYWORDS.split(","):
            source_keyword_var.set(keyword)
            utils.logger.info(
                f"[ZhihuCrawler.search] Current search keyword: {keyword}"
            )
            page = 1
            while (
                page - start_page + 1
            ) * zhihu_limit_count <= max_notes:
                if page < start_page:
                    utils.logger.info(f"[ZhihuCrawler.search] Skip page {page}")
                    page += 1
                    continue

                try:
                    utils.logger.info(
                        f"[ZhihuCrawler.search] search zhihu keyword: {keyword}, page: {page}"
                    )
                    content_list: List[ZhihuContent] = (
                        await self.zhihu_client.get_note_by_keyword(
                            keyword=keyword,
                            page=page,
                        )
                    )
                    utils.logger.info(
                        f"[ZhihuCrawler.search] Search contents :{content_list}"
                    )
                    if not content_list:
                        utils.logger.info("No more content!")
                        break

                    # ── aggregate-search hook: convert to raw-ish dicts ──
                    # ZhihuContent stores masked nicknames, but the original
                    # search API response had public names. We can't recover
                    # them here; the worker will re-fetch raw data if needed.
                    # For now, convert ZhihuContent → dict for result_sink.
                    raw_dicts = [
                        c.model_dump() if hasattr(c, "model_dump") else c
                        for c in content_list
                    ]
                    self._result_sink_call(raw_dicts)

                    # Sleep after page navigation
                    await asyncio.sleep(config.CRAWLER_MAX_SLEEP_SEC)
                    utils.logger.info(f"[ZhihuCrawler.search] Sleeping for {config.CRAWLER_MAX_SLEEP_SEC} seconds after page {page-1}")

                    page += 1

                except DataFetchError:
                    if self._strict_errors():
                        raise
                    utils.logger.error("[ZhihuCrawler.search] Search content error")
                    return

    async def fetch_favorites(self) -> None:
        """Fetch recent items across the current user's Zhihu collections."""
        sync = (getattr(self, "runtime_options", None) and self.runtime_options.extra.get("favorites_sync"))
        if sync:
            return await sync(self.zhihu_client)
        me = await self.zhihu_client.get_current_user_info()
        token = me.get("url_token") if isinstance(me, dict) else None
        if not token:
            from base.exceptions import LoginRequiredError
            raise LoginRequiredError(platform="zhihu", message="知乎登录状态已失效")
        collections_response = await self.zhihu_client.get_user_collections(str(token))
        collections = collections_response.get("data", []) if isinstance(collections_response, dict) else []
        remaining = self._result_limit()
        seen: set[str] = set()
        detail_rows = []
        for collection in collections if isinstance(collections, list) else []:
            if remaining <= 0 or not isinstance(collection, dict):
                break
            collection_id = collection.get("id")
            if collection_id is None:
                continue
            offset = 0
            while remaining > 0:
                response = await self.zhihu_client.get_collection_items(
                    str(collection_id), offset, min(remaining, 20))
                rows = response.get("data", []) if isinstance(response, dict) else []
                batch = []
                for row in rows if isinstance(rows, list) else []:
                    content = row.get("content") if isinstance(row, dict) else None
                    if not isinstance(content, dict):
                        continue
                    key = f"{content.get('type')}:{content.get('id')}"
                    if not content.get("id"):
                        continue
                    item = dict(content)
                    item["_collection_name"] = str(collection.get("title") or "默认收藏夹")
                    if key in seen:
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
                paging = response.get("paging", {}) if isinstance(response, dict) else {}
                if not rows or (isinstance(paging, dict) and paging.get("is_end")):
                    break
                offset += len(rows)
                await asyncio.sleep(config.CRAWLER_MAX_SLEEP_SEC)

        from aggregate_search.favorite_metrics import enrich_favorites
        await enrich_favorites("zhihu", self.zhihu_client, detail_rows, self._result_sink_call)

    async def create_zhihu_client(self, httpx_proxy: Optional[str]) -> ZhiHuClient:
        """Create zhihu client"""
        utils.logger.info(
            "[ZhihuCrawler.create_zhihu_client] Begin create zhihu API client ..."
        )
        cookie_str, cookie_dict = await utils.convert_browser_context_cookies(
            self.browser_context,
            urls=self.cookie_urls,
        )
        zhihu_client_obj = ZhiHuClient(
            proxy=httpx_proxy,
            headers={
                "accept": "*/*",
                "accept-language": "zh-CN,zh;q=0.9",
                "cookie": cookie_str,
                "priority": "u=1, i",
                "referer": "https://www.zhihu.com/search?q=python&time_interval=a_year&type=content",
                "user-agent": self.user_agent,
                "x-api-version": "3.0.91",
                "x-app-za": "OS=Web",
                "x-requested-with": "fetch",
                "x-zse-93": "101_3_3.0",
            },
            playwright_page=self.context_page,
            cookie_dict=cookie_dict,
        )
        return zhihu_client_obj

    async def launch_browser(
        self,
        chromium: BrowserType,
        playwright_proxy: Optional[Dict],
        user_agent: Optional[str],
        headless: bool = True,
    ) -> BrowserContext:
        """Launch browser and create browser context"""
        utils.logger.info(
            "[ZhihuCrawler.launch_browser] Begin create browser context ..."
        )
        # Resolve browser: CUSTOM_BROWSER_PATH > Chrome > Edge > bundled Chromium.
        # Never unconditionally use channel="chrome".
        executable_path, channel, backend = resolve_playwright_browser()
        if backend == "playwright-chromium":
            bundled_path = getattr(chromium, "executable_path", None)
            if not bundled_path or not os.path.isfile(bundled_path):
                raise BrowserUnavailableError(
                    "没有找到可用的浏览器，请安装 Chrome 或 Edge 后重试")
            executable_path = bundled_path
        utils.logger.info(
            f"[ZhihuCrawler.launch_browser] Using browser backend: {backend}")
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
            browser = await chromium.launch(  # type: ignore
                headless=headless, proxy=playwright_proxy,
                executable_path=executable_path, channel=channel)
            browser_context = await browser.new_context(
                viewport={"width": 1920, "height": 1080}, user_agent=user_agent
            )
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
            utils.logger.info(f"[ZhihuCrawler] CDP browser info: {browser_info}")

            return browser_context

        except Exception as e:
            utils.logger.error(f"[ZhihuCrawler] CDP mode launch failed, falling back to standard mode: {e}")
            # Fall back to standard mode
            chromium = playwright.chromium
            return await self.launch_browser(
                chromium, playwright_proxy, user_agent, headless
            )

    async def close(self):
        """Close browser context"""
        # Special handling if using CDP mode
        if self.cdp_manager:
            await self.cdp_manager.cleanup()
            self.cdp_manager = None
        else:
            await self.browser_context.close()
        utils.logger.info("[ZhihuCrawler.close] Browser context closed ...")
