# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
# This file is part of MediaCrawler project.
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

"""Classify Douyin search responses without loading the crawler."""

from typing import Any, Dict, Optional

from .exception import DataFetchError

# 抖音搜索响应中已知的风控/验证码 status_code（其余非零码一律按未知处理，
# 绝不当成正常空结果，也不猜测成登录失效）。
DOUYIN_RATE_LIMIT_STATUS_CODES = frozenset({21111, 21004, -20})


def _safe_status_code(value: Any) -> Optional[int]:
    """把 status_code 安全地规范化为整数。

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
            safe_message="抖音搜索暂时不可用，请稍后重试")
    status_code = _safe_status_code(posts_res.get("status_code"))
    data = posts_res.get("data")
    if status_code == 0:
        if isinstance(data, list):
            # 明确成功响应：空列表才是正常 empty；有数据正常继续。
            return "empty" if not data else None
        raise DataFetchError(
            "抖音搜索返回了异常响应", stage="search_list", platform_code=0,
            safe_message="抖音搜索暂时不可用，请稍后重试")
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
        safe_message="抖音搜索暂时不可用，请稍后重试")
