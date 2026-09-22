# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler/blob/main/media_platform/xhs/core.py
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
import time
from typing import Dict, List, Optional

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

from .client import XiaoHongShuClient
from .exception import DataFetchError
from .field import SearchSortType
from .help import get_search_id
from .login import XiaoHongShuLogin


async def _resolve_self_user_id(context_page: Page) -> str:
    """Read the logged-in account's own user id from the loaded page.

    The v2 collected-notes endpoint requires ``user_id``, and neither the
    cookies nor the signed client expose it. The SPA keeps the current profile
    in ``window.__INITIAL_STATE__.user.userInfo``; the sidebar profile link is
    used as a fallback. Returns ``""`` when neither is available.
    """
    try:
        resolved = await context_page.evaluate("""() => {
            try {
                const u = window.__INITIAL_STATE__ &&
                          window.__INITIAL_STATE__.user;
                const node = u && u.userInfo;
                const info = node && node.value !== undefined
                    ? node.value : node;
                if (info && typeof info === 'object') {
                    const id = info.userId || info.user_id || '';
                    if (id) return String(id);
                }
            } catch (e) { /* fall through to the DOM probe */ }
            try {
                const links = document.querySelectorAll(
                    'a[href*="/user/profile/"]');
                for (const link of links) {
                    const href = link.getAttribute('href') || '';
                    const parts = href.split('/').filter(Boolean);
                    const at = parts.indexOf('profile');
                    if (at !== -1 && parts[at + 1]) return parts[at + 1];
                }
            } catch (e) { return ''; }
            return '';
        }""")
    except Exception:
        return ""
    return resolved if isinstance(resolved, str) else ""


class XiaoHongShuCrawler(AbstractCrawler):
    context_page: Page
    xhs_client: XiaoHongShuClient
    browser_context: BrowserContext
    cdp_manager: Optional[CDPBrowserManager]

    def __init__(self) -> None:
        super().__init__()
        self.index_url = "https://www.rednote.com" if config.XHS_INTERNATIONAL else "https://www.xiaohongshu.com"
        self.cookie_urls = [self.index_url]
        # self.user_agent = utils.get_user_agent()
        self.user_agent = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
        self.cdp_manager = None

    async def start(self) -> None:
        self._begin_phase_timing()
        async with async_playwright() as playwright:
            # Choose launch mode based on configuration
            if config.ENABLE_CDP_MODE:
                utils.logger.info("[XiaoHongShuCrawler] Launching browser using CDP mode")
                self.browser_context = await self.launch_browser_with_cdp(
                    playwright,
                    None,
                    self.user_agent,
                    headless=config.CDP_HEADLESS,
                )
            else:
                utils.logger.info("[XiaoHongShuCrawler] Launching browser using standard mode")
                # Launch a browser context.
                chromium = playwright.chromium
                self.browser_context = await self.launch_browser(
                    chromium,
                    None,
                    self.user_agent,
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

            # Create a client to interact with the Xiaohongshu website.
            self.xhs_client = await self.create_xhs_client(None)
            if not await self.xhs_client.pong():
                if self._login_fail_fast():
                    from base.exceptions import LoginRequiredError
                    raise LoginRequiredError(platform="xhs", message="小红书登录状态已失效，请前往账号设置重新登录")
                login_obj = XiaoHongShuLogin(
                    login_type=config.LOGIN_TYPE,
                    login_phone="",  # input your phone number
                    browser_context=self.browser_context,
                    context_page=self.context_page,
                    cookie_str=config.COOKIES,
                )
                await login_obj.begin()
                await self.xhs_client.update_cookies(
                    browser_context=self.browser_context,
                    urls=self.cookie_urls,
                )
            self._report_metric("preflight")

            crawler_type_var.set(config.CRAWLER_TYPE)
            if config.CRAWLER_TYPE == "search":
                await self.search()
            elif config.CRAWLER_TYPE == "favorites":
                await self.fetch_favorites()
            else:
                pass

            utils.logger.info("[XiaoHongShuCrawler.start] Xhs Crawler finished ...")

    async def search(self) -> None:
        """Search notes through the lightweight list API used by aggregation."""
        from aggregate_search.pagination import current_pagination
        pagination = current_pagination.get()
        if pagination is not None:
            await pagination.run(self.xhs_client)
            return
        utils.logger.info("[XiaoHongShuCrawler.search] Begin search Xiaohongshu keywords")
        xhs_limit_count = 20  # Xiaohongshu limit page fixed value
        if config.CRAWLER_MAX_NOTES_COUNT < xhs_limit_count:
            config.CRAWLER_MAX_NOTES_COUNT = xhs_limit_count
        # Respect runtime result_limit
        max_notes = min(config.CRAWLER_MAX_NOTES_COUNT, self._result_limit())
        start_page = config.START_PAGE
        remaining = max_notes  # track how many more items we need
        _search_api_reported = False
        for keyword in config.KEYWORDS.split(","):
            source_keyword_var.set(keyword)
            utils.logger.info(f"[XiaoHongShuCrawler.search] Current search keyword: {keyword}")
            page = 1
            search_id = get_search_id()
            source_offset = 0  # 已处理页累计条目数（原始搜索列表序号基准）
            # Repeated content IDs do not consume the result limit; reset the
            # set for each keyword because searches are independent.
            seen_light_content_ids: set = set()
            while remaining > 0 and (page - start_page + 1) * xhs_limit_count <= config.CRAWLER_MAX_NOTES_COUNT + xhs_limit_count:
                if page < start_page:
                    utils.logger.info(f"[XiaoHongShuCrawler.search] Skip page {page}")
                    page += 1
                    continue

                try:
                    utils.logger.info(f"[XiaoHongShuCrawler.search] search Xiaohongshu keyword: {keyword}, page: {page}")
                    _req_start = time.perf_counter()
                    notes_res = await self.xhs_client.get_note_by_keyword(
                        keyword=keyword,
                        search_id=search_id,
                        page=page,
                        sort=(SearchSortType(config.SORT_TYPE) if config.SORT_TYPE != "" else SearchSortType.GENERAL),
                    )
                    item_count = len((notes_res or {}).get("items", {}) or {})
                    utils.logger.info(
                        f"[XiaoHongShuCrawler.search] page {page} got {item_count} items "
                        f"in {(time.perf_counter() - _req_start) * 1000:.0f}ms"
                    )
                    if not _search_api_reported:
                        self._report_metric("search_api")
                        _search_api_reported = True
                    if not notes_res:
                        utils.logger.info("[XiaoHongShuCrawler.search] No response!")
                        break

                    current_items = notes_res.get("items", {})
                    if not current_items:
                        utils.logger.info("[XiaoHongShuCrawler.search] No items in this page!")
                        break

                    light_items = []
                    for idx, post_item in enumerate(current_items):
                        if len(light_items) >= remaining:
                            break
                        if post_item.get("model_type") in ("rec_query", "hot_query"):
                            continue
                        card = post_item.get("note_card")
                        content_id = post_item.get("id")
                        if content_id is None and isinstance(card, dict):
                            content_id = card.get("note_id")
                        if content_id is None:
                            content_id = post_item.get("note_id")
                        if not content_id or content_id in seen_light_content_ids:
                            continue
                        seen_light_content_ids.add(content_id)
                        item = dict(post_item)
                        item["source_index"] = source_offset + idx
                        light_items.append(item)
                    if light_items:
                        self._result_sink_call(light_items)
                    remaining -= len(light_items)
                    source_offset += len(current_items)
                    page += 1
                    utils.logger.info(
                        f"[XiaoHongShuCrawler.search] light-list page "
                        f"{page - 1}: {len(light_items)} items"
                    )
                    if remaining > 0 and notes_res.get("has_more", False):
                        await asyncio.sleep(config.CRAWLER_MAX_SLEEP_SEC)

                    if not notes_res.get("has_more", False):
                        utils.logger.info("[XiaoHongShuCrawler.search] No more pages!")
                        break
                except DataFetchError:
                    if self._strict_errors():
                        raise
                    utils.logger.error("[XiaoHongShuCrawler.search] Search request failed")
                    break

    async def fetch_favorites(self) -> None:
        """Fetch a bounded slice of the logged-in account's collections."""
        sync = (getattr(self, "runtime_options", None) and self.runtime_options.extra.get("favorites_sync"))
        if sync:
            return await sync(self.xhs_client)
        remaining = self._result_limit()
        detail_rows = []
        user_id = await _resolve_self_user_id(self.context_page)
        if not user_id:
            raise DataFetchError(
                "无法识别当前小红书账号，请前往账号设置重新同步登录状态")
        cursor = ""
        while remaining > 0:
            response = await self.xhs_client.get_collected_notes(
                cursor, min(remaining, 30), user_id)
            # v2 collect/page 的列表字段是 ``notes``（旧版 v1 用的是 ``items``）。
            items = response.get("notes") if isinstance(response, dict) else None
            if not isinstance(items, list) or not items:
                break
            self._result_sink_call(items[:remaining])
            detail_rows.extend(items[:remaining])
            remaining -= len(items[:remaining])
            if not response.get("has_more"):
                break
            next_cursor = response.get("cursor") or response.get("next_cursor")
            if not isinstance(next_cursor, str) or next_cursor == cursor:
                break
            cursor = next_cursor
            if remaining > 0:
                await asyncio.sleep(config.CRAWLER_MAX_SLEEP_SEC)

        from aggregate_search.favorite_metrics import enrich_favorites
        await enrich_favorites("xhs", self.xhs_client, detail_rows, self._result_sink_call)

    async def create_xhs_client(self, httpx_proxy: Optional[str]) -> XiaoHongShuClient:
        """Create Xiaohongshu client"""
        utils.logger.info("[XiaoHongShuCrawler.create_xhs_client] Begin create Xiaohongshu API client ...")
        cookie_str, cookie_dict = await utils.convert_browser_context_cookies(
            self.browser_context,
            urls=self.cookie_urls,
        )
        xhs_client_obj = XiaoHongShuClient(
            proxy=httpx_proxy,
            headers={
                "accept": "application/json, text/plain, */*",
                "accept-language": "zh-CN,zh;q=0.9",
                "cache-control": "no-cache",
                "content-type": "application/json;charset=UTF-8",
                "origin": self.index_url,
                "pragma": "no-cache",
                "priority": "u=1, i",
                "referer": f"{self.index_url}/",
                "sec-ch-ua": '"Chromium";v="136", "Google Chrome";v="136", "Not.A/Brand";v="99"',
                "sec-ch-ua-mobile": "?0",
                "sec-ch-ua-platform": '"Windows"',
                "sec-fetch-dest": "empty",
                "sec-fetch-mode": "cors",
                "sec-fetch-site": "same-site",
                "user-agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/137.0.0.0 Safari/537.36",
                "Cookie": cookie_str,
            },
            playwright_page=self.context_page,
            cookie_dict=cookie_dict,
            reuse_http_client=self._reuse_http_client(),
        )
        return xhs_client_obj

    async def create_xhs_client_from_snapshot(
        self, cookie_dict: Dict[str, str],
    ) -> XiaoHongShuClient:
        """Construct a client from an in-memory session snapshot."""
        cookie_str = "; ".join(f"{k}={v}" for k, v in cookie_dict.items())
        return XiaoHongShuClient(
            proxy=None,
            headers={
                "accept": "application/json, text/plain, */*",
                "accept-language": "zh-CN,zh;q=0.9",
                "cache-control": "no-cache",
                "content-type": "application/json;charset=UTF-8",
                "origin": self.index_url,
                "pragma": "no-cache",
                "priority": "u=1, i",
                "referer": f"{self.index_url}/",
                "sec-ch-ua": '"Chromium";v="136", "Google Chrome";v="136", "Not.A/Brand";v="99"',
                "sec-ch-ua-mobile": "?0",
                "sec-ch-ua-platform": '"Windows"',
                "sec-fetch-dest": "empty",
                "sec-fetch-mode": "cors",
                "sec-fetch-site": "same-site",
                "user-agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/137.0.0.0 Safari/537.36",
                "Cookie": cookie_str,
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
        """Launch browser and create browser context"""
        utils.logger.info("[XiaoHongShuCrawler.launch_browser] Begin create browser context ...")
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
            f"[XiaoHongShuCrawler.launch_browser] Using browser backend: {backend}")
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
        """Launch browser using CDP mode"""
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
            utils.logger.info(f"[XiaoHongShuCrawler] CDP browser info: {browser_info}")

            return browser_context

        except Exception as e:
            utils.logger.error(f"[XiaoHongShuCrawler] CDP mode launch failed, falling back to standard mode: {e}")
            # Fall back to standard mode
            chromium = playwright.chromium
            return await self.launch_browser(chromium, playwright_proxy, user_agent, headless)

    async def close(self):
        """Close browser context"""
        # Special handling if using CDP mode
        if self.cdp_manager:
            await self.cdp_manager.cleanup()
            self.cdp_manager = None
        else:
            await self.browser_context.close()
        utils.logger.info("[XiaoHongShuCrawler.close] Browser context closed ...")
