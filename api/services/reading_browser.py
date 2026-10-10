# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
# This file is part of MediaCrawler project.
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

"""Bounded current-item reads from the user's official browser session."""

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from urllib.parse import urlsplit

from .accounts import platform_session_context
from .reading_content import detail_from_page, reading_detail
from .acquisition_diagnostics import AcquisitionError
from tools.light_page import install_light_page_routes
from base.runtime_paths import resource_path

logger = logging.getLogger(__name__)
PAGE_TIMEOUT = 45
ZHIHU_BODY_TIMEOUT = 8
MAX_RESPONSE_BYTES = 1024 * 1024
PAGE_SIGNALS = """() => {
    const visible = el => !!el && el.getClientRects().length > 0;
    const challenge = [...document.querySelectorAll('[id*="captcha"], [class*="captcha"], [id*="verify-bar"], [class*="VerifyModal"]')].slice(0, 12).some(visible)
        || /安全验证|访问异常|访问受限|请求过于频繁/.test(document.title);
    const login = [...document.querySelectorAll('input[type="password"], input[autocomplete="one-time-code"], [class*="SignFlow"] input')].slice(0, 12).some(visible);
    return {challenge, login};
}"""


@asynccontextmanager
async def reading_page(platform, snapshot):
    async with platform_session_context(platform, snapshot) as context:
        await install_light_page_routes(context)
        page = await context.new_page()
        yield page


def page_status(response):
    status = response.status if response is not None else 200
    if status == 401:
        raise AcquisitionError("session_expired", status)
    if status in {403, 412, 429, 461, 471}:
        raise AcquisitionError("rate_limited", status)
    if status != 200:
        raise AcquisitionError("restricted" if status in {404, 410} else "detail_api_failed", status)


async def check_page_signals(page):
    signals = await page.evaluate(PAGE_SIGNALS)
    if isinstance(signals, dict):
        if signals.get("challenge"):
            raise AcquisitionError("rate_limited")
        if signals.get("login"):
            raise AcquisitionError("login_required")


async def zhihu_page_detail(page, kind, identity, url):
    page_status(await page.goto(url, wait_until="domcontentloaded", timeout=20000))
    deadline = asyncio.get_running_loop().time() + ZHIHU_BODY_TIMEOUT
    while True:
        try:
            reading_reference_url = urlsplit(page.url)
            if reading_reference_url.hostname not in {"www.zhihu.com", "zhihu.com", "zhuanlan.zhihu.com"}:
                raise AcquisitionError("rate_limited")
            detail = await asyncio.to_thread(detail_from_page, await page.content(), kind, identity)
            value = await asyncio.to_thread(reading_detail, detail, kind, identity)
            logger.info("reader platform=zhihu stage=page_state outcome=ok")
            return value
        except (ValueError, KeyError, TypeError):
            await check_page_signals(page)
            if asyncio.get_running_loop().time() >= deadline:
                raise AcquisitionError("malformed_response") from None
            await asyncio.sleep(0.4)


def douyin_detail_response_url(url):
    try:
        parsed = urlsplit(url)
        return (parsed.scheme == "https" and not parsed.username and not parsed.password and
                parsed.port in {None, 443} and parsed.hostname in {"www.douyin.com", "www-hj.douyin.com"} and
                parsed.path == "/aweme/v1/web/aweme/detail/")
    except ValueError:
        return False


def current_douyin_route(url, identity):
    try:
        parsed = urlsplit(url)
        return (parsed.scheme == "https" and parsed.hostname in {"www.douyin.com", "douyin.com"} and
                parsed.port in {None, 443} and not parsed.username and not parsed.password and
                parsed.path.rstrip("/") in {f"/video/{identity}", f"/note/{identity}"})
    except ValueError:
        return False


async def douyin_page_detail(snapshot, identity, kind, url):
    from .reading_platforms import platform_detail
    script = resource_path("libs", "reading_douyin.js").read_text(encoding="utf-8")
    observer = resource_path("libs", "reading_douyin_response.js").read_text(encoding="utf-8")
    async with reading_page("douyin", snapshot) as page:
        await page.add_init_script(script=f"({observer})({json.dumps(identity)});")
        captured = best = None
        tasks = set()

        async def capture(response):
            nonlocal captured, best
            try:
                if (response.status != 200 or response.request.method != "GET" or
                        not douyin_detail_response_url(response.request.url) or
                        not douyin_detail_response_url(response.url) or not current_douyin_route(page.url, identity)):
                    return
                length = response.headers.get("content-length")
                if length is not None and (not length.isdigit() or int(length) > MAX_RESPONSE_BYTES):
                    return
                # Unknown-size bodies are read by the bounded in-page streaming observer.
                if length is None:
                    return
                body = await response.body()
                if len(body) > MAX_RESPONSE_BYTES:
                    return
                detail = json.loads(body).get("aweme_detail")
                value = await asyncio.to_thread(platform_detail, "douyin", kind, identity, detail)
                if current_douyin_route(page.url, identity) and (value["blocks"] or value["media"]):
                    best = value
                    if value["media"] or any(row["type"] == "image" for row in value["blocks"]):
                        captured = value
            except (ValueError, TypeError, KeyError):
                return
            except Exception:
                logger.info("reader platform=douyin stage=response outcome=unavailable")

        def observed(response):
            task = asyncio.create_task(capture(response))
            tasks.add(task)
            task.add_done_callback(tasks.discard)

        page.on("response", observed)
        try:
            async with asyncio.timeout(PAGE_TIMEOUT):
                page_status(await page.goto(url, wait_until="domcontentloaded", timeout=20000))
                while True:
                    if captured is not None and current_douyin_route(page.url, identity):
                        logger.info("reader platform=douyin stage=response outcome=ok")
                        return captured
                    await check_page_signals(page)
                    if current_douyin_route(page.url, identity):
                        detail = await page.evaluate(script, {"identity": identity, "kind": kind})
                        if isinstance(detail, dict):
                            try:
                                value = await asyncio.to_thread(platform_detail, "douyin", kind, identity, detail)
                                if value["blocks"] or value["media"]:
                                    best = value
                                    if value["media"] or any(row["type"] == "image" for row in value["blocks"]):
                                        logger.info("reader platform=douyin stage=page_state outcome=ok")
                                        return value
                            except (ValueError, TypeError, KeyError):
                                pass
                    await asyncio.sleep(0.45)
        except TimeoutError:
            if best is not None and current_douyin_route(page.url, identity):
                logger.info("reader platform=douyin stage=page_state outcome=metadata_only")
                return best
            raise
        finally:
            page.remove_listener("response", observed)
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
