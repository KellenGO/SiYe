# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler/blob/main/media_platform/douyin/client.py
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

import copy
import json
import urllib.parse
from typing import Any, Dict, Optional

from playwright.async_api import BrowserContext

from base.base_crawler import AbstractApiClient
from base.base_platform_client import ReusableHttpClientMixin
from tools import utils
from var import request_keyword_var

from .exception import *
from .field import *
from .help import *


class DouYinClient(ReusableHttpClientMixin, AbstractApiClient):

    def __init__(
        self,
        timeout=60,  # If the crawl media option is turned on, Douyin’s short videos will require a longer timeout.
        proxy=None,
        *,
        headers: Dict,
        playwright_page: Optional[Page],
        cookie_dict: Dict,
        reuse_http_client: bool = False,
    ):
        self.proxy = proxy
        self.timeout = timeout
        self.headers = headers
        self.reuse_http_client = reuse_http_client
        self._init_http_client_state()
        self._host = "https://www.douyin.com"
        self.cookie_urls = [
            "https://douyin.com",
            self._host,
            "https://creator.douyin.com",
            "https://douhot.douyin.com",
            "https://live.douyin.com",
        ]
        self.playwright_page = playwright_page
        self.cookie_dict = cookie_dict
        # ── 搜索响应诊断（排障用；只记"形状"，绝不记任何凭据值）──
        # 抖音"搜不到内容"时最需要知道的两件事：请求带没带页面里的 xmst
        # （msToken 的来源，见 __process_req_params），以及返回体是什么形状。
        self._last_xmst_present = False
        self._last_xmst_len = 0
        self.last_search_diag: Dict = {}

    async def __process_req_params(
        self,
        uri: str,
        params: Optional[Dict] = None,
        headers: Optional[Dict] = None,
        request_method="GET",
    ):

        if not params:
            return
        headers = headers or self.headers
        local_storage: Dict = await self.playwright_page.evaluate("() => window.localStorage")  # type: ignore
        # 只记"有/无 + 长度"，绝不记值：日志脱敏会丢弃含 token 字样的行，
        # 所以这里统一用 xmst 表达（它就是页面里 msToken 的来源键）。
        _xmst = local_storage.get("xmst")
        self._last_xmst_present = bool(_xmst)
        self._last_xmst_len = len(_xmst) if isinstance(_xmst, str) else 0
        common_params = {
            "device_platform": "webapp",
            "aid": "6383",
            "channel": "channel_pc_web",
            "version_code": "190600",
            "version_name": "19.6.0",
            "update_version_code": "170400",
            "pc_client_type": "1",
            "cookie_enabled": "true",
            "browser_language": "zh-CN",
            "browser_platform": "MacIntel",
            "browser_name": "Chrome",
            "browser_version": "125.0.0.0",
            "browser_online": "true",
            "engine_name": "Blink",
            "os_name": "Mac OS",
            "os_version": "10.15.7",
            "cpu_core_num": "8",
            "device_memory": "8",
            "engine_version": "109.0",
            "platform": "PC",
            "screen_width": "2560",
            "screen_height": "1440",
            'effective_type': '4g',
            "round_trip_time": "50",
            "webid": get_web_id(),
            "msToken": local_storage.get("xmst"),
        }
        params.update(common_params)
        query_string = urllib.parse.urlencode(params)

        # 20240927 a-bogus update (JS version)
        post_data = {}
        if request_method == "POST":
            post_data = params

        if "/v1/web/general/search" not in uri:
            a_bogus = await get_a_bogus(uri, query_string, post_data, headers["User-Agent"], self.playwright_page)
            params["a_bogus"] = a_bogus

    async def request(self, method, url, **kwargs):
        # Check whether the proxy has expired before each request

        # 复用 / 独立生命周期由 ReusableHttpClientMixin._send 统一处理。
        response = await self._send(method, url, **kwargs)
        from aggregate_search.pagination import check_search_http_status
        check_search_http_status(response.status_code)
        try:
            if response.text == "" or response.text == "blocked":
                utils.logger.error(f"request params incrr, response.text: {response.text}")
                raise Exception("account blocked")
            return response.json()
        except Exception as e:
            raise DataFetchError(f"{e}, {response.text}")

    async def get(self, uri: str, params: Optional[Dict] = None, headers: Optional[Dict] = None):
        """
        GET请求
        """
        await self.__process_req_params(uri, params, headers)
        headers = headers or self.headers
        return await self.request(method="GET", url=f"{self._host}{uri}", params=params, headers=headers)

    async def post(self, uri: str, data: dict, headers: Optional[Dict] = None):
        await self.__process_req_params(uri, data, headers, request_method="POST")
        headers = headers or self.headers
        return await self.request(method="POST", url=f"{self._host}{uri}", data=data, headers=headers)

    async def pong(
        self,
        *,
        raise_on_error: bool = False,
        browser_context: Optional[BrowserContext] = None,
    ) -> bool:
        """探测登录态。

        签名与其它三个平台保持一致（``*, raise_on_error, browser_context``），
        这样调用方不必按平台分支。douyin 的探测本身不主动抛异常
        （localStorage 只是快路径，失败一律回退到 Cookie 校验），
        所以 ``raise_on_error`` 在这里只作为接口占位。

        注意：``raise_on_error`` 以外的关键字参数是**必须**的 ——
        ``browser_context`` 缺失时无法校验 Cookie，直接返回 False。
        """
        if browser_context is None:
            return False
        # localStorage 校验只是快路径：page 可能为 None（账号同步场景只传
        # context），或页面未加载完 —— 任何失败都回退到 context Cookie 校验，
        # 绝不在这里抛异常把整个验证打挂。
        try:
            if self.playwright_page is not None:
                local_storage = await self.playwright_page.evaluate(
                    "() => window.localStorage")
                if local_storage and local_storage.get("HasUserLogin", "") == "1":
                    return True
        except Exception:
            pass

        _, cookie_dict = await utils.convert_browser_context_cookies(
            browser_context,
            urls=self.cookie_urls,
        )
        return cookie_dict.get("LOGIN_STATUS") == "1"

    async def search_info_by_keyword(
        self,
        keyword: str,
        offset: int = 0,
        search_channel: SearchChannelType = SearchChannelType.GENERAL,
        sort_type: SearchSortType = SearchSortType.GENERAL,
        publish_time: PublishTimeType = PublishTimeType.UNLIMITED,
        search_id: str = "",
    ):
        """
        DouYin Web Search API
        :param keyword:
        :param offset:
        :param search_channel:
        :param sort_type:
        :param publish_time: ·
        :param search_id: ·
        :return:
        """
        query_params = {
            'search_channel': search_channel.value,
            'enable_history': '1',
            'keyword': keyword,
            'search_source': 'tab_search',
            'query_correct_type': '1',
            'is_filter_search': '0',
            'from_group_id': '7378810571505847586',
            'offset': offset,
            'count': '15',
            'need_filter_settings': '1',
            'list_type': 'multi',
            'search_id': search_id,
        }
        if sort_type.value != SearchSortType.GENERAL.value or publish_time.value != PublishTimeType.UNLIMITED.value:
            query_params["filter_selected"] = json.dumps({"sort_type": str(sort_type.value), "publish_time": str(publish_time.value)})
            query_params["is_filter_search"] = 1
            query_params["search_source"] = "tab_search"
        referer_url = f"https://www.douyin.com/search/{keyword}?aid=f594bbd9-a0e2-4651-9319-ebe3cb6298c1&type=general"
        headers = copy.copy(self.headers)
        headers["Referer"] = urllib.parse.quote(referer_url, safe=':/')
        response = await self.get("/aweme/v1/web/general/search/single/", query_params, headers=headers)
        self._record_search_diag(response, offset)
        return response

    def _record_search_diag(self, response: Any, offset: int) -> None:
        """记下这次搜索响应的**形状**（排障用，不含任何凭据值）。

        抖音"搜不到内容"时，这一行要能回答：请求带没带页面里的 xmst、
        平台返回的是空列表还是错误码、有没有 logid（风控判定用得到）。
        """
        if not isinstance(response, dict):
            self.last_search_diag = {"shape": type(response).__name__}
            utils.logger.warning("[DouYin] search-resp 非字典响应: %s", type(response).__name__)
            return
        raw = response.get("data")
        diag = {
            "offset": offset,
            "status_code": response.get("status_code"),
            "data_len": len(raw) if isinstance(raw, list) else -1,
            "has_more": response.get("has_more"),
            "cursor_present": "cursor" in response,
            "logid_present": bool((response.get("extra") or {}).get("logid"))
            if isinstance(response.get("extra"), dict) else False,
            "xmst_present": self._last_xmst_present,
            "xmst_len": self._last_xmst_len,
        }
        self.last_search_diag = diag
        utils.logger.info(
            "[DouYin] search-resp offset=%s status_code=%s data_len=%s has_more=%s "
            "cursor_present=%s logid_present=%s xmst_present=%s xmst_len=%s",
            diag["offset"], diag["status_code"], diag["data_len"], diag["has_more"],
            diag["cursor_present"], diag["logid_present"], diag["xmst_present"],
            diag["xmst_len"])

    async def get_collected_awemes(self, cursor: int = 0, count: int = 20) -> Dict:
        """Return the current account's collected videos (read only)."""
        return await self.post("/aweme/v1/web/aweme/listcollection/", {
            "cursor": str(max(cursor, 0)),
            "count": str(min(max(count, 1), 20)),
        })
