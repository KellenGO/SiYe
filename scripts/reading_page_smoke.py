# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
# This file is part of MediaCrawler project.
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

"""Execute official-page extraction in isolated Edge with fully mocked sites."""

import asyncio
import json
import sys
from contextlib import asynccontextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from playwright.async_api import async_playwright
from api.services import reading_browser
from api.services.research_diagnostics import AcquisitionError


async def main():
    identity = "7692362378063272891"
    url = f"https://www.douyin.com/video/{identity}"
    state = {"aweme_id": identity, "desc": "目标正文", "video": {"width": 90, "height": 160,
        "play_addr": {"url_list": ["https://v.douyincdn.com/video"]}}}
    source = (ROOT / "libs/reading_douyin.js").read_text(encoding="utf-8")
    requests = []
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(channel="msedge", headless=True)
        context = await browser.new_context()
        async def route(request):
            requests.append(request.request.url)
            await request.fulfill(status=200, content_type="text/html", body="<html></html>")
        await context.route("**/*", route)
        page = await context.new_page()
        await page.goto(url)

        # Real JavaScript handles named stores, encoded render data and numeric precision.
        await page.evaluate("value => {window._ROUTER_DATA = {recommend:{aweme_id:'43',desc:'其它视频'},current:value}}", state)
        value = await page.evaluate(source, {"identity": identity, "kind": "video"})
        assert value["desc"] == "目标正文" and value["video"]["play_addr"]["url_list"]
        await page.evaluate("() => {delete window._ROUTER_DATA}")
        await page.evaluate("value => {const s=document.createElement('script');s.id='RENDER_DATA';s.textContent=encodeURIComponent(JSON.stringify({detail:value}));document.body.append(s)}", state)
        assert (await page.evaluate(source, {"identity": identity, "kind": "video"}))["aweme_id"] == identity
        await page.evaluate("() => document.querySelector('#RENDER_DATA').remove()")
        await page.evaluate("value => {window.__INITIAL_STATE__={aweme_id:Number(value),desc:'不能接受精度丢失'}}", identity)
        assert await page.evaluate(source, {"identity": identity, "kind": "video"}) is None
        await page.evaluate("() => {delete window.__INITIAL_STATE__}")
        gallery = {"awemeId": identity, "desc": "图集", "images": [{"urlList": ["https://p3.douyinpic.com/image"]}]}
        await page.evaluate("value => {window.__NEXT_DATA__={detail:value}}", gallery)
        assert (await page.evaluate(source, {"identity": identity, "kind": "note"}))["images"][0]["url_list"]
        await page.evaluate("() => {delete window.__NEXT_DATA__}")
        # DOM fallback requires current canonical/title and exactly one current player.
        await page.set_content(f'<link rel="canonical" href="{url}"><title>第2集 | 目标正文 - 抖音</title>'
            '<section data-e2e="video-detail"><p data-e2e="detail-video-info">目标正文</p><video src="https://v.douyincdn.com/video"></video></section>')
        assert (await page.evaluate(source, {"identity": identity, "kind": "video"}))["desc"] == "目标正文"
        await page.evaluate("() => document.querySelector('link').href='https://www.douyin.com/video/43'")
        assert await page.evaluate(source, {"identity": identity, "kind": "video"}) is None
        await context.close()

        original = reading_browser.reading_page
        response_state = state
        html = '<script>setTimeout(()=>fetch("https://www-hj.douyin.com/aweme/v1/web/aweme/detail/?aweme_id=' + identity + '"),150)</script>'
        @asynccontextmanager
        async def official(*args):
            ctx = await browser.new_context()
            async def respond(route):
                if "aweme/detail/" in route.request.url:
                    body = json.dumps({"aweme_detail": response_state})
                    await route.fulfill(status=200, headers={"content-type": "application/json",
                        "access-control-allow-origin": "*"}, body=body)
                else:
                    await route.fulfill(status=200, content_type="text/html; charset=utf-8", body=html)
            await ctx.route("**/*", respond)
            try:
                yield await ctx.new_page()
            finally:
                await ctx.close()
        reading_browser.reading_page = official
        try:
            value = await reading_browser.douyin_page_detail({}, identity, "video", url)
            assert value["blocks"][0]["text"] == "目标正文" and value["media"]
            html = '<script>setTimeout(()=>{const x=new XMLHttpRequest();x.open("GET","https://www-hj.douyin.com/aweme/v1/web/aweme/detail/");x.responseType="json";x.onload=()=>{window.originalXhrDone=x.status};x.send()},150)</script>'
            value = await reading_browser.douyin_page_detail({}, identity, "video", url)
            assert value["media"]
            original_timeout = reading_browser.PAGE_TIMEOUT
            reading_browser.PAGE_TIMEOUT = 0.8
            try:
                for bad in ({**state, "aweme_id": "43"}, {**state, "unused": "x" * (1024 * 1024)}):
                    response_state = bad
                    html = '<script>setTimeout(()=>fetch("https://www-hj.douyin.com/aweme/v1/web/aweme/detail/"),50)</script>'
                    try:
                        await reading_browser.douyin_page_detail({}, identity, "video", url)
                        raise AssertionError("mismatched or oversized detail must be ignored")
                    except TimeoutError:
                        pass
            finally:
                response_state = state
                reading_browser.PAGE_TIMEOUT = original_timeout
            html = '<script>setTimeout(()=>{window._ROUTER_DATA=' + json.dumps(state) + '},200)</script>'
            value = await reading_browser.douyin_page_detail({}, identity, "video", url)
            assert value["media"]
            html = '<title>安全验证</title>'
            try:
                await reading_browser.douyin_page_detail({}, identity, "video", url)
                raise AssertionError("challenge should stop extraction")
            except AcquisitionError as error:
                assert error.safe_code == "rate_limited"
            html = '<script>setTimeout(()=>{const s=document.createElement("script");s.id="js-initialData";s.textContent=JSON.stringify({initialState:{entities:{answers:{"42":{id:"42",content:"<p>延迟正文</p>"}}}}});document.body.append(s)},200)</script>'
            async with official() as page:
                value = await reading_browser.zhihu_page_detail(page, "answer", "42", "https://www.zhihu.com/answer/42")
                assert value["blocks"][0]["text"] == "延迟正文"
        finally:
            reading_browser.reading_page = original
            await browser.close()
    print("PASS: real JS current-ID stores/render data, safe numeric IDs, gallery, strict DOM, alternate response origin, delayed state, challenge and Zhihu body")


if __name__ == "__main__":
    asyncio.run(main())
