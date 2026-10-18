"""Small browser adapters over account profiles and existing platform clients."""

from urllib.parse import urlencode, urlsplit
from contextlib import AsyncExitStack

from .accounts import platform_session_context, PLATFORM_HOME_URLS, PLATFORM_COOKIE_URLS
from .acquisition_diagnostics import AcquisitionError, classify, diagnostic


async def browser_json(page, url, headers):
    from playwright.async_api import Error
    try:
        result = await page.evaluate("""async ({url, headers}) => {
        const response = await fetch(url, {headers, credentials: 'include'});
        if (!response.ok) return {status: response.status};
        const text = await response.text();
        if (text.length > 2 * 1024 * 1024) return {status: response.status};
        try { return {status: response.status, data: JSON.parse(text)}; }
        catch { return {status: response.status}; }
        }""", {"url": url, "headers": headers})
    except Error:
        raise AcquisitionError("browser_request_failed") from None
    status = result.get("status") if isinstance(result, dict) else None
    if status != 200:
        raise AcquisitionError("session_expired" if status == 401 else
            "rate_limited" if status in {412, 429, 461, 471} else "restricted" if status == 403 else "malformed_response", status)
    if not isinstance(result.get("data"), dict):
        raise AcquisitionError("malformed_response", status)
    return result["data"]


class ResearchBrowserProvider:
    def __init__(self, stack, sessions):
        self.stack, self.sessions = stack, sessions
        self.pages = {}
        self.page_errors = {}
        self.xhs_attempted = False
        self.xhs_outcome = None
        self.xhs_error = None
        self.xhs_presence = {}

    async def page(self, platform, client, *, navigate=True):
        if platform in self.page_errors:
            raise self.page_errors[platform]
        if platform not in self.pages:
            from tools.light_page import install_light_page_routes, light_goto_kwargs
            owner, attached = AsyncExitStack(), False
            try:
                context = await owner.enter_async_context(platform_session_context(platform, self.sessions.get(platform)))
                await install_light_page_routes(context)
                page = await context.new_page()
                response = await page.goto(PLATFORM_HOME_URLS[platform], **light_goto_kwargs()) if navigate else None
                if response is not None and response.status in {401, 403, 429, 461, 471}:
                    raise AcquisitionError("session_expired" if response.status == 401 else
                        "restricted" if response.status == 403 else "rate_limited", response.status)
                cookies = {row["name"]: row["value"] for row in await context.cookies(PLATFORM_COOKIE_URLS[platform])}
                if platform == "xhs" and not cookies.get("web_session"):
                    raise AcquisitionError("login_required")
                await client.update_cookies(context)
                client.playwright_page = page
                if getattr(client, "headers", None) is not None:
                    client.headers["User-Agent"] = await page.evaluate("() => navigator.userAgent")
                self.pages[platform] = page
                self.stack.push_async_callback(owner.aclose)
                attached = True
            except Exception as error:
                code, status = classify(error, "session")
                self.page_errors[platform] = AcquisitionError(code, status)
                raise self.page_errors[platform] from None
            finally:
                if not attached:
                    await owner.aclose()
        return self.pages[platform]

    async def xhs_comments(self, client, params):
        self.xhs_presence = {"a1_present": bool(client.cookie_dict.get("a1")), "xsec_token_present": bool(params.get("xsec_token"))}
        uri = "/api/sns/web/v2/comment/page"
        if self.xhs_outcome == "succeeded":
            return await self.xhs_browser_request(client, uri, params)
        try:
            return await client.get(uri, params)
        except Exception as error:
            if self.xhs_attempted or classify(error)[0] not in {"rate_limited", "restricted"}:
                raise
            self.xhs_attempted = True
            self.xhs_error = classify(error)
            try:
                result = await self.xhs_browser_request(client, uri, params)
                self.xhs_outcome = "succeeded"
                return result
            except Exception:
                self.xhs_outcome = "failed"
                raise

    async def xhs_browser_request(self, client, uri, params):
        page = await self.page("xhs", client)
        headers = await client._pre_headers(uri, params=params)
        headers = {key: value for key, value in headers.items() if key.lower() in
            {"x-s", "x-t", "x-s-common", "x-b3-traceid"}}
        response = await browser_json(page, "https://edith.xiaohongshu.com" + uri + "?" + client._build_query_string(params), headers)
        if not response.get("success"):
            raise AcquisitionError("restricted")
        if not isinstance(response.get("data"), dict):
            raise AcquisitionError("malformed_response")
        return response["data"]

    async def xhs_replies(self, client, params):
        uri = "/api/sns/web/v2/comment/sub/page"
        if self.xhs_outcome == "succeeded":
            return await self.xhs_browser_request(client, uri, params)
        return await client.get(uri, params)

    async def bilibili_player(self, client, uri, params):
        if uri not in {"/x/player/wbi/v2", "/x/player/wbi/playurl"}:
            raise AcquisitionError("internal_failure")
        page = await self.page("bilibili", client)
        signed = await client.pre_request_data(dict(params))
        response = await browser_json(page, "https://api.bilibili.com" + uri + "?" + urlencode(signed), {})
        if response.get("code") != 0:
            raise AcquisitionError("session_expired" if response.get("code") == -101 else
                "rate_limited" if response.get("code") in {-352, -412} else "restricted")
        if not isinstance(response.get("data"), dict):
            raise AcquisitionError("malformed_response")
        return response["data"]

    def comment_diagnostic(self, result, platform):
        if platform == "xhs":
            result.setdefault("diagnostics", diagnostic(platform, "comments", "comment_api"))
            result["diagnostics"].update(self.xhs_presence)
        if platform == "xhs" and self.xhs_attempted:
            code, status = self.xhs_error
            result["diagnostics"] = {**result.get("diagnostics", {}), **diagnostic(platform, "comments", "comment_api",
                provider="browser", fallback_attempted=True,
                fallback_outcome=self.xhs_outcome)}
            result["diagnostics"]["primary_safe_error_code"] = code
            if status is not None:
                result["diagnostics"]["primary_http_status"] = status

    async def douyin_detail(self, client, source):
        from .reading_browser import douyin_page_detail
        from .reading_platforms import platform_reference
        identity, kind = str(source["content_id"]), source["content_type"]
        url = platform_reference("douyin", kind, identity, source["url"])
        return await douyin_page_detail(self.sessions.get("douyin"), identity, kind, url,
                                       page=client.playwright_page, raw=True)

    async def zhihu_detail(self, client, source):
        from .reading_browser import zhihu_page_detail
        from .reading_content import reading_reference
        kind, identity = source["content_type"], str(source["content_id"])
        if not identity.isdigit() or kind not in {"answer", "article", "zvideo"}:
            raise AcquisitionError("malformed_response")
        if kind in {"answer", "article"}:
            url = reading_reference(kind, identity, source["url"])
            page = await self.page("zhihu", client, navigate=False)
            return await zhihu_page_detail(page, kind, identity, url)
        page = await self.page("zhihu", client, navigate=False)
        url = "https://www.zhihu.com/zvideo/" + identity
        extract = client._extractor.extract_zvideo_content_from_html
        response = await page.goto(url, wait_until="domcontentloaded", timeout=20000)
        if response is not None and response.status in {401, 403, 429}:
            raise AcquisitionError("session_expired" if response.status == 401 else "restricted", response.status)
        detail = extract(await page.content())
        if detail is None:
            raise AcquisitionError("restricted")
        return detail

    async def zhihu_comments(self, client, uri, params):
        page = await self.page("zhihu", client)
        if urlsplit(page.url).hostname != "www.zhihu.com":
            response = await page.goto(PLATFORM_HOME_URLS["zhihu"], wait_until="domcontentloaded", timeout=20000)
            if response is not None and response.status in {401, 403, 429}:
                raise AcquisitionError("session_expired" if response.status == 401 else "restricted", response.status)
        headers = await client._pre_headers(uri + "?" + urlencode(params))
        headers = {key: value for key, value in headers.items() if key.lower() in
            {"x-zst-81", "x-zse-96", "x-zse-93", "x-app-za", "x-api-version"}}
        return await browser_json(page, "https://www.zhihu.com" + uri + "?" + urlencode(params), headers)
