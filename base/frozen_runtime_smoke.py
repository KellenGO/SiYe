"""Small, network-free runtime checks used by the Windows build gate."""

from __future__ import annotations

import asyncio

from base.runtime_paths import resource_path


async def _browser_smoke() -> str:
    from playwright.async_api import async_playwright
    from tools.browser_launcher import resolve_playwright_browser

    executable_path, channel, backend = resolve_playwright_browser()
    async with async_playwright() as playwright:
        kwargs = {"headless": True}
        if executable_path:
            kwargs["executable_path"] = executable_path
        elif channel:
            kwargs["channel"] = channel
        browser = await playwright.chromium.launch(**kwargs)
        await browser.close()
    return backend


def run_frozen_runtime_smoke() -> None:
    if not resource_path("libs", "douyin.js").is_file():
        raise AssertionError("bundled signing resources are missing")

    import cv2  # noqa: F401
    import execjs
    from PIL import Image  # noqa: F401

    runtime = execjs.get()
    if "node" not in runtime.name.lower():
        raise AssertionError(f"unexpected packaged JS runtime: {runtime.name}")

    douyin = execjs.compile(
        resource_path("libs", "douyin.js").read_text(encoding="utf-8-sig"))
    if not douyin.call("sign_datail", "aweme_id=smoke", "Mozilla/5.0"):
        raise AssertionError("Douyin signing smoke failed")

    zhihu = execjs.compile(
        resource_path("libs", "zhihu.js").read_text(encoding="utf-8-sig"))
    signed = zhihu.call(
        "get_sign",
        "https://www.zhihu.com/api/v4/search_v3?t=general&q=smoke",
        "d_c0=smoke",
    )
    if not isinstance(signed, dict) or not signed.get("x-zse-96"):
        raise AssertionError("Zhihu signing smoke failed")

    from media_platform.bilibili.client import BilibiliClient  # noqa: F401
    from media_platform.douyin.client import DouYinClient  # noqa: F401
    from media_platform.xhs.client import XiaoHongShuClient  # noqa: F401
    from media_platform.zhihu.client import ZhiHuClient  # noqa: F401
    from media_platform.xhs.playwright_sign import sign_with_xhshow

    if not sign_with_xhshow(
        "/api/sns/web/v1/homefeed", {"source_note_id": "smoke"}, "a1=smoke"
    ).get("x-s"):
        raise AssertionError("XHS signing smoke failed")

    browser_backend = asyncio.run(_browser_smoke())
    print("frozen imports: PASS")
    print("native runtime: PASS")
    print("signing runtime: PASS")
    print(f"browser runtime: PASS ({browser_backend})")
