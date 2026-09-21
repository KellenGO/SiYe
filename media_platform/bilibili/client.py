# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler/blob/main/media_platform/bilibili/client.py
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
# @Desc    : bilibili request client
import asyncio
import json
from typing import Any, Dict, Optional, Tuple, Union
from urllib.parse import urlencode

from playwright.async_api import BrowserContext, Page

from base.base_crawler import AbstractApiClient
from base.base_platform_client import ReusableHttpClientMixin
from tools import utils

from .exception import DataFetchError
from .field import CommentOrderType, SearchOrderType
from .help import BilibiliSign

# Error metadata stage labels (safe, fixed text — used for classification
# and user-facing messages; never URLs/cookies/headers/response bodies).
_STAGE_LABELS = {
    "search_list": "搜索",
    "video_detail": "视频详情",
    "login_check": "登录检查",
    "request": "接口",
}


def _stage_for_uri(uri: str) -> str:
    """Derive the API stage from the request URI (no params are inspected)."""
    if "/x/web-interface/wbi/search/type" in uri:
        return "search_list"
    if "/x/web-interface/view/detail" in uri:
        return "video_detail"
    if "/x/web-interface/nav" in uri:
        return "login_check"
    return "request"


def _safe_bili_error_message(message: Optional[str], code: Any,
                             http_status: Optional[int],
                             stage: str) -> str:
    """Fixed / length-bounded safe error text for user display.

    Never includes URL query params, cookies, headers, or the response body.
    Falls back to the platform message only when it is short plain text.
    """
    label = _STAGE_LABELS.get(stage, "接口")
    if code == -412:
        return f"B站{label}请求受限（code -412），请稍后重试"
    if code == -352:
        return f"B站{label}触发验证码或风控，请稍后重试"
    if code == -101:
        return "B站登录状态失效，请前往账号设置重新同步"
    if http_status is not None and http_status >= 500:
        return f"B站{label}接口暂时不可用（HTTP {http_status}），请稍后重试"
    if http_status == 403:
        return f"B站{label}请求被拒绝，请稍后重试"
    if isinstance(message, str) and message and len(message) <= 40:
        return f"B站{label}请求失败：{message}"
    return f"B站{label}请求失败，请稍后重试"


class BilibiliClient(ReusableHttpClientMixin, AbstractApiClient):

    def __init__(
        self,
        timeout=60,  # For media crawling, Bilibili long videos need a longer timeout
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
        self._init_http_client_state()
        self._host = "https://api.bilibili.com"
        self.cookie_urls = ["https://www.bilibili.com"]
        self.playwright_page = playwright_page
        self.cookie_dict = cookie_dict

    async def request(self, method, url, **kwargs) -> Any:
        # Check if proxy has expired before each request

        stage = _stage_for_uri(url)
        response = await self._send(method, url, **kwargs)
        # HTTP 5xx / transient failures: exactly ONE bounded retry, then a
        # safe "temporarily unavailable" error — never unlimited retries,
        # never fast retry loops that bypass platform limits.
        from aggregate_search.pagination import allow_client_retry, check_search_http_status
        if getattr(self, "favorites_sync", False) and response.status_code in (403, 429, 461, 471):
            from base.exceptions import RateLimitError
            raise RateLimitError("bilibili", "B站请求受限，请稍后重试")
        if getattr(self, "favorites_sync", False) and response.status_code >= 500:
            raise DataFetchError("Bilibili unavailable", stage=stage,
                http_status=response.status_code,
                safe_message=_safe_bili_error_message(None, None, response.status_code, stage))
        check_search_http_status(response.status_code)
        if response.status_code >= 500 and allow_client_retry() and not getattr(self, "favorites_sync", False):
            utils.logger.warning(
                f"[BilibiliClient.request] HTTP {response.status_code} for "
                f"{url} (stage={stage}), retrying once after 1.5s")
            await asyncio.sleep(1.5)
            response = await self._send(method, url, **kwargs)
        try:
            data: Dict = response.json()
        except json.JSONDecodeError:
            utils.logger.error(f"[BilibiliClient.request] Failed to decode JSON from response. status_code: {response.status_code}, response_text: {response.text}")
            raise DataFetchError(
                "Failed to decode JSON from response",
                stage=stage, http_status=response.status_code,
                safe_message=_safe_bili_error_message(
                    None, None, response.status_code, stage))
        if data.get("code") != 0:
            code = data.get("code")
            raise DataFetchError(
                data.get("message", "unkonw error"),
                stage=stage, http_status=response.status_code,
                platform_code=code,
                safe_message=_safe_bili_error_message(
                    data.get("message"), code, response.status_code, stage))
        else:
            return data.get("data", {})

    async def pre_request_data(self, req_data: Dict) -> Dict:
        """
        Send request to sign request parameters
        Need to get wbi_img_urls parameter from localStorage, value as follows:
        https://i0.hdslb.com/bfs/wbi/7cd084941338484aae1ad9425b84077c.png-https://i0.hdslb.com/bfs/wbi/4932caff0ff746eab6f01bf08b70ac45.png
        :param req_data:
        :return:
        """
        if not req_data:
            return {}
        img_key, sub_key = await self.get_wbi_keys()
        return BilibiliSign(img_key, sub_key).sign(req_data)

    async def get_wbi_keys(self) -> Tuple[str, str]:
        """
        Get the latest img_key and sub_key
        :return:
        """
        # Round 16 fast path：page=None 时跳过 localStorage，直接走 HTTP
        # /x/web-interface/nav（与浏览器路径的 HTTP fallback 同一实现）。
        if self.playwright_page is not None:
            try:
                local_storage = await self.playwright_page.evaluate("() => window.localStorage")
                wbi_img_urls = local_storage.get("wbi_img_urls", "")
                if not wbi_img_urls:
                    img_url_from_storage = local_storage.get("wbi_img_url")
                    sub_url_from_storage = local_storage.get("wbi_sub_url")
                    if img_url_from_storage and sub_url_from_storage:
                        wbi_img_urls = f"{img_url_from_storage}-{sub_url_from_storage}"
                if wbi_img_urls and "-" in wbi_img_urls:
                    img_url, sub_url = wbi_img_urls.split("-")
                    img_key = img_url.rsplit('/', 1)[1].split('.')[0]
                    sub_key = sub_url.rsplit('/', 1)[1].split('.')[0]
                    return img_key, sub_key
            except Exception:
                pass
        resp = await self.request(method="GET", url=self._host + "/x/web-interface/nav")
        img_url: str = resp['wbi_img']['img_url']
        sub_url: str = resp['wbi_img']['sub_url']
        img_key = img_url.rsplit('/', 1)[1].split('.')[0]
        sub_key = sub_url.rsplit('/', 1)[1].split('.')[0]
        return img_key, sub_key

    async def get(self, uri: str, params=None, enable_params_sign: bool = True) -> Dict:
        final_uri = uri
        if enable_params_sign:
            params = await self.pre_request_data(params)
        if isinstance(params, dict):
            final_uri = (f"{uri}?"
                         f"{urlencode(params)}")
        return await self.request(method="GET", url=f"{self._host}{final_uri}", headers=self.headers)

    async def post(self, uri: str, data: dict) -> Dict:
        data = await self.pre_request_data(data)
        json_str = json.dumps(data, separators=(',', ':'), ensure_ascii=False)
        return await self.request(method="POST", url=f"{self._host}{uri}", data=json_str, headers=self.headers)

    async def pong(
        self,
        raise_on_error: bool = False,
        browser_context: object = None,
    ) -> bool:
        """get a note to check if login state is ok
        Args:
            browser_context: 为与其它平台统一签名而接受，本平台不使用
                （登录态直接查 /x/web-interface/nav）。
            raise_on_error: True 时异常不再吞掉 —— 网络错误/超时/403/-412
                风控等向上传播（供账号验证 probe 区分"明确未登录"与"无法
                验证"）；DataFetchError(platform_code=-101) 是平台明确返回
                的"未登录"码 → 仍返回 False。False（默认）保持 console/
                login 模块的原始行为：任何异常都返回 False。
        """
        utils.logger.info("[BilibiliClient.pong] Begin pong bilibili...")
        ping_flag = False
        try:
            check_login_uri = "/x/web-interface/nav"
            response = await self.get(check_login_uri)
            if response.get("isLogin"):
                utils.logger.info("[BilibiliClient.pong] Use cache login state get web interface successfull!")
                ping_flag = True
        except DataFetchError as e:
            utils.logger.error(f"[BilibiliClient.pong] Pong bilibili failed: {e}, and try to login again...")
            if raise_on_error and getattr(e, "platform_code", None) != -101:
                # -101 = 平台明确未登录码 → False（not_logged_in 语义）；
                # 其余平台错误（403/-412/5xx/风控）→ 传播（unavailable 语义）。
                raise
            ping_flag = False
        except Exception as e:
            utils.logger.error(f"[BilibiliClient.pong] Pong bilibili failed: {e}, and try to login again...")
            if raise_on_error:
                raise
            ping_flag = False
        return ping_flag

    async def search_video_by_keyword(
        self,
        keyword: str,
        page: int = 1,
        page_size: int = 20,
        order: SearchOrderType = SearchOrderType.DEFAULT,
        pubtime_begin_s: int = 0,
        pubtime_end_s: int = 0,
    ) -> Dict:
        """
        Bilibili web search api
        :param keyword: Search keyword
        :param page: Page number for pagination
        :param page_size: Number of items per page
        :param order: Sort order for search results, default is comprehensive sorting
        :param pubtime_begin_s: Publish time start timestamp
        :param pubtime_end_s: Publish time end timestamp
        :return:
        """
        uri = "/x/web-interface/wbi/search/type"
        post_data = {
            "search_type": "video",
            "keyword": keyword,
            "page": page,
            "page_size": page_size,
            "order": order.value,
            "pubtime_begin_s": pubtime_begin_s,
            "pubtime_end_s": pubtime_end_s
        }
        return await self.get(uri, post_data)

    async def get_created_favorite_folders(self, up_mid: int) -> Dict:
        return await self.get(
            "/x/v3/fav/folder/created/list-all",
            {"up_mid": up_mid},
            enable_params_sign=False,
        )

    async def get_favorite_folder_contents(
        self, media_id: int, page: int = 1, page_size: int = 20,
    ) -> Dict:
        return await self.get(
            "/x/v3/fav/resource/list",
            {"media_id": media_id, "pn": page, "ps": min(max(page_size, 1), 20),
             "order": "mtime", "platform": "web"},
            enable_params_sign=False,
        )

    async def get_video_info(self, aid: Union[int, None] = None, bvid: Union[str, None] = None) -> Dict:
        """
        Bilibli web video detail api, choose one parameter between aid and bvid
        :param aid: Video aid
        :param bvid: Video bvid
        :return:
        """
        if not aid and not bvid:
            raise ValueError("Please provide at least one parameter: aid or bvid")

        uri = "/x/web-interface/view/detail"
        params = dict()
        if aid:
            params.update({"aid": aid})
        else:
            params.update({"bvid": bvid})
        return await self.get(uri, params, enable_params_sign=False)
