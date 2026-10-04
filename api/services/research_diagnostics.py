"""Fixed acquisition diagnostics; never copy exception messages or request data."""

import asyncio
import httpx
from tenacity import RetryError

REASONS = {
    "login_required": "需要本机登录会话，请先检查平台账号",
    "session_expired": "平台登录会话已失效，请重新登录",
    "rate_limited": "平台触发验证码或访问限制，请稍后重试",
    "restricted": "平台拒绝访问，请在平台页面检查访问权限",
    "timeout": "读取超时，可重试",
    "malformed_response": "平台返回空响应或异常结构，已有资料保留",
    "invalid_identity": "资料标识与原帖链接不一致，请重新加入资料",
    "detail_api_failed": "平台详情接口未能完成，已有资料保留",
    "comment_api_failed": "平台评论接口未能完成，已有资料保留",
    "internal_failure": "资料获取内部错误，已有资料保留",
    "browser_request_failed": "浏览器会话请求未能完成，请检查平台页面后重试",
}


class AcquisitionError(Exception):
    def __init__(self, code, http_status=None):
        self.safe_code = code if code in REASONS else "internal_failure"
        self.http_status = http_status
        super().__init__(self.safe_code)


def classify(error, stage=""):
    if isinstance(error, RetryError):
        error = error.last_attempt.exception()
    status = getattr(error, "http_status", None)
    if isinstance(error, httpx.HTTPStatusError):
        status = error.response.status_code
    if not isinstance(status, int) or isinstance(status, bool) or not 100 <= status <= 599:
        status = None
    code = getattr(error, "safe_code", None)
    platform_code = getattr(error, "platform_code", None)
    if not isinstance(platform_code, int) or isinstance(platform_code, bool):
        platform_code = None
    if not isinstance(code, str):
        code = None
    name = type(error).__name__.lower()
    if code not in REASONS:
        if status == 401 or platform_code == -101:
            code = "session_expired"
        elif status in {412, 429, 461, 471} or platform_code in {-352, -412, 21111, 21004, -20} or "rate" in name or "block" in name:
            code = "rate_limited"
        elif status == 403 or "forbidden" in name:
            code = "restricted"
        elif isinstance(error, PermissionError) or "login" in name:
            code = "login_required"
        elif isinstance(error, (asyncio.TimeoutError, httpx.TimeoutException)) or "timeout" in name:
            code = "timeout"
        elif isinstance(error, (ValueError, KeyError, TypeError)):
            code = "malformed_response"
        elif isinstance(error, httpx.RequestError):
            code = "comment_api_failed" if stage == "comment_api" else "detail_api_failed"
        else:
            code = "internal_failure"
    return code, status if isinstance(status, int) and not isinstance(status, bool) and 100 <= status <= 599 else None


def diagnostic(platform, component, stage, *, code=None, http_status=None,
               provider="session_api", fallback_attempted=False, fallback_outcome=None):
    # All inputs are code-owned labels or bounded numeric metadata.
    result = {"platform": platform, "component": component, "stage": stage, "provider": provider,
              "fallback_attempted": bool(fallback_attempted)}
    if code in REASONS:
        result["safe_error_code"] = code
    if isinstance(http_status, int) and 100 <= http_status <= 599:
        result["http_status"] = http_status
    if fallback_outcome in {"succeeded", "failed", "unavailable"}:
        result["fallback_outcome"] = fallback_outcome
    return result
