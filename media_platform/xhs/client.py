# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler/blob/main/media_platform/xhs/client.py
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

import json
import re
from typing import Any, Dict, Optional, Union
from urllib.parse import quote

from playwright.async_api import BrowserContext, Page
from tenacity import retry, stop_after_attempt, wait_fixed, retry_if_not_exception_type, retry_if_exception
from aggregate_search.pagination import allow_client_retry, check_search_http_status

import config
from base.base_crawler import AbstractApiClient
from base.base_platform_client import ReusableHttpClientMixin
from tools import utils

from .exception import (
    DataFetchError, IPBlockError, NoteNotFoundError, XhsRateLimitError,
)
from .field import SearchNoteType, SearchSortType
from .help import get_search_id
from .playwright_sign import sign_with_xhshow


def _safe_debug_message(value: Any) -> Optional[str]:
    if not isinstance(value, str) or not value:
        return None
    message = re.sub(r"https?://[^\s]+", "[URL]", value)
    message = re.sub(
        r"(?i)(xsec[_-]?token|cookie|authorization|access[_-]?token|refresh[_-]?token)"
        r"\s*[:=]\s*[^\s,;}]+'?",
        r"\1=[REDACTED]",
        message,
    )
    return message[:120]


class XiaoHongShuClient(ReusableHttpClientMixin, AbstractApiClient):

    def __init__(
        self,
        timeout=60,  # If media crawling is enabled, Xiaohongshu long videos need longer timeout
        proxy=None,
        *,
        headers: Dict[str, str],
        playwright_page: Page,
        cookie_dict: Dict[str, str],
        reuse_http_client: bool = False,
    ):
        self.proxy = proxy
        self.timeout = timeout
        self.headers = headers
        self.reuse_http_client = reuse_http_client
        # 复用的 httpx client（懒创建；代理变化时安全关闭并重建）。
        self._init_http_client_state()
        if config.XHS_INTERNATIONAL:
            self._host = "https://webapi.rednote.com"
            self._domain = "https://www.rednote.com"
        else:
            self._host = "https://edith.xiaohongshu.com"
            self._domain = "https://www.xiaohongshu.com"
        self.cookie_urls = [self._domain]
        self.IP_ERROR_STR = "Network connection error, please check network settings or restart"
        self.IP_ERROR_CODE = 300012
        self.NOTE_NOT_FOUND_CODE = -510000
        self.NOTE_ABNORMAL_CODE = -510001
        self.playwright_page = playwright_page
        self.cookie_dict = cookie_dict
        # Diagnostic-only response metadata for aggregate hydration. Never
        # store response bodies, URLs, cookies, or tokens here.
        self.last_response_status: Optional[int] = None
        self.last_business_code: Any = None
        self.last_business_msg: Optional[str] = None

    async def _pre_headers(self, url: str, params: Optional[Dict] = None, payload: Optional[Dict] = None) -> Dict:
        """请求头参数签名 (使用 xhshow 纯算法)

        Args:
            url: 请求 URI path
            params: GET 请求参数
            payload: POST 请求参数

        Returns:
            Dict: 签名后的请求头参数
        """
        if params is not None:
            data = params
            method = "GET"
        elif payload is not None:
            data = payload
            method = "POST"
        else:
            raise ValueError("params or payload is required")

        # 使用 xhshow 纯算法生成签名
        signs = sign_with_xhshow(
            uri=url,
            data=data,
            cookie_str=self.headers.get("Cookie", ""),
            method=method,
        )

        headers = {
            "X-S": signs["x-s"],
            "X-T": signs["x-t"],
            "x-S-Common": signs["x-s-common"],
            "X-B3-Traceid": signs["x-b3-traceid"],
        }
        self.headers.update(headers)
        return self.headers

    # 461/471 是平台风控（验证码/访问限制）—— XhsRateLimitError
    # 必须被排除在重试之外（只发 1 次请求，不重复触发风控）；NoteNotFoundError
    # 原语义保持不重试。其余网络/临时错误仍按原样重试 3 次。
    @retry(stop=stop_after_attempt(3), wait=wait_fixed(1),
           retry=retry_if_not_exception_type(
                (NoteNotFoundError, XhsRateLimitError, IPBlockError)) & retry_if_exception(
                    lambda error: allow_client_retry(error) and getattr(error, "http_status", None) not in {401, 403, 429}))
    async def request(self, method, url, **kwargs) -> Union[str, Any]:
        """
        Wrapper for httpx common request method, processes request response
        Args:
            method: Request method
            url: Request URL
            **kwargs: Other request parameters, such as headers, body, etc.

        Returns:

        """
        # Check if proxy is expired before each request

        # return response.text
        return_response = kwargs.pop("return_response", False)
        # 复用 / 独立生命周期由 ReusableHttpClientMixin._send 统一处理。
        response = await self._send(method, url, **kwargs)

        check_search_http_status(response.status_code)

        # Keep only safe response metadata for the hydration diagnostic log.
        self.last_response_status = response.status_code
        self.last_business_code = None
        self.last_business_msg = None

        if response.status_code == 471 or response.status_code == 461:
            # 平台风控/验证码挑战 —— 立即抛专用异常，不读取
            # Verifyuuid/Verifytype，不记录 response 对象或 body，日志只写
            # 固定文案与状态码。XhsRateLimitError 被重试条件排除 → 只发 1 次。
            utils.logger.error(
                f"[XiaoHongShuClient.request] xhs rate-limited, "
                f"http_status={response.status_code}")
            raise XhsRateLimitError(http_status=response.status_code)

        if response.status_code in {401, 403, 429}:
            error = DataFetchError("小红书接口拒绝访问")
            error.http_status = response.status_code
            error.safe_code = "session_expired" if response.status_code == 401 else "rate_limited" if response.status_code == 429 else "restricted"
            raise error

        if return_response:
            return response.text
        try:
            data: Dict = response.json()
            if not isinstance(data, dict) or "success" not in data:
                raise ValueError("response")
        except ValueError:
            error = DataFetchError("小红书接口返回异常结构")
            error.http_status = response.status_code
            error.safe_code = "malformed_response"
            raise error from None
        self.last_business_code = data.get("code")
        msg = data.get("msg")
        self.last_business_msg = _safe_debug_message(msg)
        if data["success"]:
            return data.get("data", data.get("success", {}))
        elif data["code"] == self.IP_ERROR_CODE:
            raise IPBlockError(self.IP_ERROR_STR)
        elif data["code"] in (self.NOTE_NOT_FOUND_CODE, self.NOTE_ABNORMAL_CODE):
            raise NoteNotFoundError(f"Note not found or abnormal, code: {data['code']}")
        else:
            error = DataFetchError("小红书接口请求失败")
            error.http_status = response.status_code
            raise error

    @staticmethod
    def _build_query_string(params: Dict) -> str:
        """Build URL query string with encoding matching browser behavior (commas not encoded)"""
        parts = []
        for key, value in params.items():
            value_str = str(value) if value is not None else ""
            parts.append(f"{key}={quote(value_str, safe=',')}")
        return "&".join(parts)

    async def get(self, uri: str, params: Optional[Dict] = None) -> Dict:
        """
        GET request, signs request headers
        Args:
            uri: Request route
            params: Request parameters

        Returns:

        """
        headers = await self._pre_headers(uri, params)
        # Build URL manually to ensure query string encoding matches the sign string
        # (httpx's default params encoding differs from browser/XHS frontend behavior)
        if params:
            full_url = f"{self._host}{uri}?{self._build_query_string(params)}"
        else:
            full_url = f"{self._host}{uri}"

        return await self.request(
            method="GET", url=full_url, headers=headers
        )

    async def post(self, uri: str, data: dict, **kwargs) -> Dict:
        """
        POST request, signs request headers
        Args:
            uri: Request route
            data: Request body parameters

        Returns:

        """
        headers = await self._pre_headers(uri, payload=data)
        json_str = json.dumps(data, separators=(",", ":"), ensure_ascii=False)
        return await self.request(
            method="POST",
            url=f"{self._host}{uri}",
            data=json_str,
            headers=headers,
            **kwargs,
        )

    async def query_self(self) -> Optional[Dict]:
        """
        Query self user info to check login state
        Returns:
            Dict: User info if logged in, None otherwise
        """
        uri = "/api/sns/web/v1/user/selfinfo"
        headers = await self._pre_headers(uri, params={})
        # 与其它请求共用同一套 httpx 生命周期（ReusableHttpClientMixin._send）——
        # 未启用复用时它同样是"每次请求新建 client"，行为不变。
        response = await self._send("GET", f"{self._host}{uri}", headers=headers)
        # Round 17.2: 461/471 是平台风控（验证码/访问限制）—— 抛专用
        # 异常（不读取 Verifyuuid/Verifytype、不记录 body/URL）。
        if response.status_code in (461, 471):
            raise XhsRateLimitError(http_status=response.status_code)
        if response.status_code == 200:
            return response.json()
        return None

    async def browser_login_confirmed(self) -> bool:
        """Use the same personal-profile control as the interactive login flow."""
        if self.playwright_page is None:
            return False
        try:
            return await self.playwright_page.is_visible(
                "xpath=//a[contains(@href, '/user/profile/')]//span[text()='我']",
                timeout=500,
            )
        except Exception:
            return False
    async def pong(
        self,
        raise_on_error: bool = False,
        browser_context: object = None,
    ) -> bool:

        """
        Check if login state is still valid by querying self user info
        Args:
            browser_context: 为与其它平台统一签名而接受，本平台不使用
                （登录态直接查自己的接口，不需要浏览器上下文）。
            raise_on_error: True 时异常不再吞掉 —— 网络错误/超时/风控/接口
                异常向上传播（供账号验证 probe 区分"明确未登录"与"无法验证"）；
                False（默认）保持 console/login 模块的原始行为：任何异常
                都返回 False。
        Returns:
            bool: True if logged in, False otherwise
        """
        utils.logger.info("[XiaoHongShuClient.pong] Begin to check login state...")
        ping_flag = False
        try:
            self_info: Dict = await self.query_self()
            if raise_on_error and self_info is None:
                # query_self 仅在 HTTP 200 时返回响应 —— None 表示接口异常
                # 响应（403/5xx 等），绝不能被误判为"明确未登录"。
                raise DataFetchError("selfinfo 接口未返回有效响应")
            # A missing success flag is an unknown response, not logout.
            result = (self_info or {}).get("data", {}).get("result", {})
            success = result.get("success") if isinstance(result, dict) else None
            if success is True:
                ping_flag = True
            elif success is False:
                ping_flag = False
            elif raise_on_error:
                raise DataFetchError("selfinfo 响应无法确认登录状态")
        except Exception as e:
            utils.logger.error(
                f"[XiaoHongShuClient.pong] Check login state failed: {e}, and try to login again..."
            )
            if isinstance(e, XhsRateLimitError):
                if raise_on_error:
                    raise
                return False
            if await self.browser_login_confirmed():
                return True
            if raise_on_error:
                raise
            ping_flag = False
        if not ping_flag and await self.browser_login_confirmed():
            return True
        utils.logger.info(f"[XiaoHongShuClient.pong] Login state result: {ping_flag}")
        return ping_flag

    async def get_note_by_keyword(
        self,
        keyword: str,
        search_id: str = get_search_id(),
        page: int = 1,
        page_size: int = 20,
        sort: SearchSortType = SearchSortType.GENERAL,
        note_type: SearchNoteType = SearchNoteType.ALL,
    ) -> Dict:
        """
        Search notes by keyword
        Args:
            keyword: Keyword parameter
            page: Page number
            page_size: Page data length
            sort: Search result sorting specification
            note_type: Type of note to search

        Returns:

        """
        uri = "/api/sns/web/v1/search/notes"
        data = {
            "keyword": keyword,
            "page": page,
            "page_size": page_size,
            "search_id": search_id,
            "sort": sort.value,
            "note_type": note_type.value,
            # Round 17.1: 缺少 image_formats 时真实搜索响应里的 note_card.cover
            # 与 image_list[] 只有 height/width 没有任何图片 URL；与详情接口
            # 相同的 image_formats 参数让封面字段携带真实图片 URL（url_pre /
            # url_default / image_list[].info_list[].url）。
            "image_formats": ["jpg", "webp", "avif"],
        }
        return await self.post(uri, data)

    async def get_collected_notes(
        self, cursor: str = "", num: int = 20, user_id: str = "",
    ) -> Dict:
        """Return the current account's collected-note feed (read only).

        Round 18: the legacy ``v1`` path was retired by the platform — it now
        answers ``404 page not found`` with an HTML body, which surfaces as a
        ``JSONDecodeError``. ``v2`` is the live endpoint and additionally
        requires the logged-in account's own ``user_id`` (without it the
        platform answers ``-9109 参数错误``).
        """
        return await self.get("/api/sns/web/v2/note/collect/page", {
            "cursor": cursor,
            "num": min(max(num, 1), 30),
            "user_id": user_id,
            "image_formats": "jpg,webp,avif",
        })

    async def get_note_by_id(
        self,
        note_id: str,
        xsec_source: str,
        xsec_token: str,
    ) -> Dict:
        """
        Get note detail API
        Args:
            note_id: Note ID
            xsec_source: Channel source
            xsec_token: Token returned from search keyword result list

        Returns:

        """
        if xsec_source == "":
            xsec_source = "pc_search"

        data = {
            "source_note_id": note_id,
            "image_formats": ["jpg", "webp", "avif"],
            "extra": {"need_body_topic": 1},
            "xsec_source": xsec_source,
            "xsec_token": xsec_token,
        }
        uri = "/api/sns/web/v1/feed"
        res = await self.post(uri, data)
        if res and res.get("items"):
            res_dict: Dict = res["items"][0]["note_card"]
            return res_dict
        # When crawling frequently, some notes may have results while others don't
        utils.logger.error(
            f"[XiaoHongShuClient.get_note_by_id] get note id:{note_id} empty and res:{res}"
        )
        return dict()
