# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler/blob/main/media_platform/zhihu/client.py
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
import json
from typing import Any, Dict, List, Optional, Union
from urllib.parse import urlencode

from httpx import Response
from playwright.async_api import BrowserContext, Page
from tenacity import retry, stop_after_attempt, wait_fixed, retry_if_exception
from aggregate_search.pagination import allow_client_retry, check_search_http_status

import config
from base.base_crawler import AbstractApiClient
from base.base_platform_client import ReusableHttpClientMixin
from constant import zhihu as zhihu_constant
from model.m_zhihu import ZhihuContent
from tools import utils

from .exception import DataFetchError, ForbiddenError
from .field import SearchSort, SearchTime, SearchType
from .help import ZhihuExtractor, sign


class ZhiHuClient(ReusableHttpClientMixin, AbstractApiClient):
    #: zhihu 用 default_headers + 小写 cookie 键（其余三家是 headers/Cookie）
    _cookie_header_bag = "default_headers"
    _cookie_header_name = "cookie"

    def __init__(
        self,
        timeout=10,
        proxy=None,
        *,
        headers: Dict[str, str],
        playwright_page: Page,
        cookie_dict: Dict[str, str],
        reuse_http_client: bool = False,
    ):
        self.proxy = proxy
        self.timeout = timeout
        self.default_headers = headers
        self.reuse_http_client = reuse_http_client
        self._init_http_client_state()
        self.cookie_urls = ["https://www.zhihu.com"]
        self.cookie_dict = cookie_dict
        self._extractor = ZhihuExtractor()

    async def _pre_headers(self, url: str) -> Dict:
        """
        Sign request headers
        Args:
            url: Request URL with query parameters
        Returns:

        """
        d_c0 = self.cookie_dict.get("d_c0")
        if not d_c0:
            raise Exception("d_c0 not found in cookies")
        sign_res = sign(url, self.default_headers["cookie"])
        headers = self.default_headers.copy()
        headers['x-zst-81'] = sign_res["x-zst-81"]
        headers['x-zse-96'] = sign_res["x-zse-96"]
        return headers

    @retry(stop=stop_after_attempt(3), wait=wait_fixed(1), retry=retry_if_exception(allow_client_retry))
    async def request(self, method, url, **kwargs) -> Union[str, Any]:
        """
        Wrapper for httpx common request method with response handling
        Args:
            method: Request method
            url: Request URL
            **kwargs: Other request parameters such as headers, body, etc.

        Returns:

        """
        # Check if proxy is expired before each request

        # return response.text
        return_response = kwargs.pop('return_response', False)

        # 复用 / 独立生命周期由 ReusableHttpClientMixin._send 统一处理。
        response = await self._send(method, url, **kwargs)

        check_search_http_status(response.status_code)
        if response.status_code != 200:
            utils.logger.error(f"[ZhiHuClient.request] Requset Url: {url}, Request error: {response.text}")
            if response.status_code == 403:
                # 403 = 风控/验证码拦截（不一定是未登录），固定安全文案，
                # 绝不含响应体。
                exc = ForbiddenError(response.text)
                exc.safe_message = "知乎暂时拒绝访问（可能需要验证），请稍后重试"
                raise exc
            elif response.status_code == 404:  # Content without comments also returns 404
                return {}

            exc = DataFetchError(response.text)
            exc.safe_message = "知乎暂时无法访问，请稍后重试"
            raise exc

        if return_response:
            return response.text
        try:
            data: Dict = response.json()
            if data.get("error"):
                utils.logger.error(f"[ZhiHuClient.request] Request error: {data}")
                exc = DataFetchError(data.get("error", {}).get("message"))
                exc.safe_message = "知乎暂时无法访问，请稍后重试"
                raise exc
            return data
        except json.JSONDecodeError:
            utils.logger.error(f"[ZhiHuClient.request] Request error: {response.text}")
            exc = DataFetchError(response.text)
            exc.safe_message = "知乎暂时无法访问，请稍后重试"
            raise exc

    async def get(self, uri: str, params=None, **kwargs) -> Union[Response, Dict, str]:
        """
        GET request with header signing
        Args:
            uri: Request URI
            params: Request parameters

        Returns:

        """
        final_uri = uri
        if isinstance(params, dict):
            final_uri += '?' + urlencode(params)
        headers = await self._pre_headers(final_uri)
        base_url = (zhihu_constant.ZHIHU_URL if "/p/" not in uri else zhihu_constant.ZHIHU_ZHUANLAN_URL)
        return await self.request(method="GET", url=base_url + final_uri, headers=headers, **kwargs)

    async def pong(
        self,
        raise_on_error: bool = False,
        browser_context: object = None,
    ) -> bool:
        """
        Check if login status is still valid
        Args:
            browser_context: 为与其它平台统一签名而接受，本平台不使用
                （登录态直接查 /api/v4/me）。
            raise_on_error: True 时异常不再吞掉 —— 网络错误/超时/403
                ForbiddenError/DataFetchError 等向上传播（供账号验证 probe
                区分"明确未登录"与"无法验证"）；False（默认）保持 console/
                login 模块的原始行为：任何异常都返回 False。
        Returns:

        """
        utils.logger.info("[ZhiHuClient.pong] Begin to pong zhihu...")
        ping_flag = False
        try:
            res = await self.get_current_user_info()
            if res.get("uid") and res.get("name"):
                ping_flag = True
                utils.logger.info("[ZhiHuClient.pong] Ping zhihu successfully")
            else:
                # 安全：绝不把响应体写进日志。
                utils.logger.error("[ZhiHuClient.pong] Ping zhihu failed: no uid/name in current-user response")
        except Exception as e:
            utils.logger.error(f"[ZhiHuClient.pong] Ping zhihu failed: {e}, and try to login again...")
            if raise_on_error:
                raise
            ping_flag = False
        return ping_flag

    async def get_current_user_info(self) -> Dict:
        """
        Get current logged-in user information
        Returns:

        """
        params = {"include": "email,is_active,is_bind_phone"}
        return await self.get("/api/v4/me", params)

    async def get_user_collections(self, url_token: str, offset: int = 0, limit: int = 20) -> Dict:
        return await self.get(f"/api/v4/people/{url_token}/collections", {
            "offset": max(offset, 0), "limit": min(max(limit, 1), 20),
            "include": "data[*].updated_time,answer_count,is_public",
        })

    async def get_collection_items(self, collection_id: str, offset: int = 0, limit: int = 20) -> Dict:
        return await self.get(f"/api/v4/collections/{collection_id}/items", {
            "offset": max(offset, 0), "limit": min(max(limit, 1), 20),
            "include": "data[*].content.excerpt,data[*].content.author,data[*].content.question",
        })

    async def get_note_by_keyword(
        self,
        keyword: str,
        page: int = 1,
        page_size: int = 20,
        sort: SearchSort = SearchSort.DEFAULT,
        note_type: SearchType = SearchType.DEFAULT,
        search_time: SearchTime = SearchTime.DEFAULT,
    ) -> List[ZhihuContent]:
        """
        Search by keyword
        Args:
            keyword: Search keyword
            page: Page number
            page_size: Page size
            sort: Sorting method
            note_type: Search result type
            search_time: Time range for search results

        Returns:

        """
        uri = "/api/v4/search_v3"
        params = {
            "gk_version": "gz-gaokao",
            "t": "general",
            "q": keyword,
            "correction": 1,
            "offset": (page - 1) * page_size,
            "limit": page_size,
            "filter_fields": "",
            "lc_idx": (page - 1) * page_size,
            "show_all_topics": 0,
            "search_source": "Filter",
            "time_interval": search_time.value,
            "sort": sort.value,
            "vertical": note_type.value,
        }
        search_res = await self.get(uri, params)
        utils.logger.info(f"[ZhiHuClient.get_note_by_keyword] Search result: {search_res}")
        return self._extractor.extract_contents_from_search(search_res)

    async def get_answer_info(
        self,
        question_id: str,
        answer_id: str,
    ) -> Optional[ZhihuContent]:
        """
        Get answer information
        Args:
            question_id:
            answer_id:

        Returns:

        """
        uri = f"/question/{question_id}/answer/{answer_id}"
        response_html = await self.get(uri, return_response=True)
        return self._extractor.extract_answer_content_from_html(response_html)

    async def get_article_info(self, article_id: str) -> Optional[ZhihuContent]:
        """
        Get article information
        Args:
            article_id:

        Returns:

        """
        uri = f"/p/{article_id}"
        response_html = await self.get(uri, return_response=True)
        return self._extractor.extract_article_content_from_html(response_html)

    async def get_video_info(self, video_id: str) -> Optional[ZhihuContent]:
        """
        Get video information
        Args:
            video_id:

        Returns:

        """
        uri = f"/zvideo/{video_id}"
        response_html = await self.get(uri, return_response=True)
        return self._extractor.extract_zvideo_content_from_html(response_html)
