# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
# This file is part of MediaCrawler project.
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

"""Verify the built reader with a mocked platform and a temporary library."""

import json
import re
import sys
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import urlparse

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fastapi import FastAPI
from fastapi.testclient import TestClient
from api.routers.library import library_router
from api.routers.reading import get_reading_service, reading_router
from api.services.library_store import LibraryStore, get_library_store
from api.services.reading import ReadingError
from api.services.reading_content import reading_detail
from api.services.reading_media import reading_media
from api.services.reading_platforms import platform_detail


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


def main():
    dist = ROOT / "webui" / "dist"
    if not (dist / "index.html").is_file():
        raise SystemExit("Build webui first: npm run build")
    output = ROOT / "build" / "reading-ui"
    output.mkdir(parents=True, exist_ok=True)
    source = dict(platform="zhihu", content_id="42", content_type="answer", title="为什么读书需要慢下来？",
                  author="阅读测试作者", snippet="这里只是搜索摘要，不是正文。", url="https://www.zhihu.com/question/1/answer/42",
                  cover_url=None, metrics={"like_count": 128}, rank=0, grouped_sources=None)
    article = {**source, "content_id": "43", "content_type": "article", "title": "一篇独立的文章", "url": "https://zhuanlan.zhihu.com/p/43"}
    note_id, bvid = "abcdef0123456789abcdef01", "BV1234567890"
    note = {**source, "platform": "xhs", "content_id": note_id, "content_type": "note", "title": "小红书图文阅读", "url": f"https://www.xiaohongshu.com/explore/{note_id}?xsec_token=fixture"}
    douyin = {**source, "platform": "douyin", "content_id": "123", "content_type": "video", "title": "抖音视频阅读", "url": "https://www.douyin.com/video/123"}
    bilibili = {**source, "platform": "bilibili", "content_id": bvid, "content_type": "video", "title": "B站视频阅读", "url": f"https://www.bilibili.com/video/{bvid}"}
    job = dict(job_id="reader-smoke", keyword="阅读", overall="completed", hydration_status="completed",
               created_at="2026-10-10T00:00:00Z", results=[source, article, note, douyin, bilibili], platforms={platform: {
                   "status": "succeeded", "result_count": 2 if platform == "zhihu" else 1, "error_summary": None, "cache_hit": False} for platform in ("zhihu", "xhs", "douyin", "bilibili")})
    mode, reads, views, errors, pending, media_requests = "ok", [], [], [], [], []
    comment_reads = []

    class Reader:
        async def comments(self, kind, identity, url, platform="zhihu"):
            comment_reads.append(identity)
            return {"platform": platform, "content_type": kind, "content_id": identity,
                    "entries": [{"id": "comment-1", "parent_id": None, "text": "站内评论测试正文", "author": "评论作者", "like_count": 8},
                                {"id": "reply-1", "parent_id": "comment-1", "text": "站内回复测试正文", "author": "回复作者"}]
                               + [{"id": f"discussion-{i}", "parent_id": None, "author": f"阅读者 {i + 1}",
                                   "text": "阅读之后，也想看看其他人的理解。保留不同角度，回到原文再做判断。", "like_count": i + 2} for i in range(18)],
                    "limited": True, "sort": "热门", "notice": ""}

        async def read(self, kind, identity, url, refresh, platform="zhihu"):
            reads.append((identity, refresh))
            if mode == "fail":
                raise ReadingError("busy")
            if platform != "zhihu":
                if platform == "xhs":
                    value = platform_detail(platform, kind, identity, {"note_id": identity, "type": "normal", "desc": "图文正文\n保留原始换行。", "image_list": [
                        {"url_default": "https://sns-webpic-qc.xhscdn.com/one"}, {"url_default": "https://sns-webpic-qc.xhscdn.com/two"}]})
                elif platform == "douyin":
                    value = platform_detail(platform, kind, identity, {"aweme_id": identity, "desc": "抖音视频正文", "video": {
                        "width": 90, "height": 160, "play_addr": {"url_list": ["https://v.douyincdn.com/video"]}}})
                else:
                    value = platform_detail(platform, kind, identity, {"bvid": identity, "aid": 12, "cid": 34, "desc": ""},
                        {"durl": [{"url": "https://upos.bilivideo.com/one"}, {"url": "https://upos.bilivideo.com/two"}]})
                for block in value["blocks"]:
                    if block["type"] == "image":
                        block["url"] = reading_media.register(platform, block["url"], "image")
                for part in value["media"]:
                    part["url"] = reading_media.register(platform, part["url"], "video")
                return value
            body = '''<h2>给阅读留一点时间</h2><p>真正的正文，保留完整段落与图文顺序。</p>
                <p>先阅读，再记录。<img src="https://pic.zhimg.com/reader-fixture.svg" alt="阅读示意图">图片后面的正文。</p>
                <blockquote><p>慢一点，才能看清楚。</p></blockquote><pre>note = "阅读"\nprint(note)</pre>
                <ul><li>记住问题</li><li>核对原文</li></ul><iframe src="https://evil.test"></iframe>'''
            return reading_detail({"id": identity, "content": body, "is_truncated": identity == "43"}, kind, identity)

    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(QuietHandler, directory=str(dist)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    origin = f"http://127.0.0.1:{server.server_port}"
    try:
        with TemporaryDirectory(prefix="reader-smoke-", dir=output) as temp, sync_playwright() as p:
            store = LibraryStore(Path(temp) / "library.db")
            app = FastAPI()
            app.include_router(library_router)
            app.include_router(reading_router)
            app.dependency_overrides[get_library_store] = lambda: store
            app.dependency_overrides[get_reading_service] = lambda: Reader()
            client = TestClient(app, base_url=origin)
            browser = p.chromium.launch(channel="msedge")
            context = browser.new_context(viewport={"width": 1440, "height": 1000})
            context.add_init_script("localStorage.setItem('mediacrawler_license_accepted','true'); localStorage.setItem('siye_onboarding_preference_v1','completed')")
            fixture_page = context.new_page()
            fixture_page.goto("data:text/html,<body>Video fixture</body>")
            video_bytes = bytes(fixture_page.evaluate("""async () => {
                const canvas = document.createElement('canvas'); canvas.width = 160; canvas.height = 90;
                document.body.append(canvas); canvas.getContext('2d').fillRect(0,0,160,90);
                await new Promise(resolve => requestAnimationFrame(resolve));
                const stream = canvas.captureStream(12), chunks = [];
                const recorder = new MediaRecorder(stream, {mimeType:'video/webm;codecs=vp8'});
                const done = new Promise(resolve => recorder.onstop = resolve);
                recorder.ondataavailable = event => chunks.push(event.data);
                let frame = 0;
                const timer = setInterval(() => {const ctx=canvas.getContext('2d'); ctx.fillStyle=frame++%2?'#6799ba':'#a4c0d4'; ctx.fillRect(0,0,160,90)}, 80);
                recorder.start(100);
                for (let warmup=0; warmup<80 && chunks.reduce((n,b)=>n+b.size,0)<2000; warmup++) await new Promise(resolve => setTimeout(resolve,100));
                await new Promise(resolve => setTimeout(resolve,1800)); recorder.stop(); await done;
                clearInterval(timer); stream.getTracks().forEach(track => track.stop());
                return Array.from(new Uint8Array(await new Blob(chunks).arrayBuffer()));
            }"""))
            assert video_bytes, "Edge did not encode the test video"
            (output / "fixture.webm").write_bytes(video_bytes)
            fixture_page.close()
            from io import BytesIO
            from PIL import Image
            image_output = BytesIO()
            Image.new("RGB", (640, 240), "#a4c0d4").save(image_output, "PNG")

            def route_request(route):
                parsed = urlparse(route.request.url)
                if parsed.hostname == "pic.zhimg.com":
                    route.fulfill(content_type="image/svg+xml", body='<svg xmlns="http://www.w3.org/2000/svg" width="640" height="240"><rect width="640" height="240" fill="#e5eff8"/><path d="M180 60h280v120H180z" fill="#9fb8cc"/></svg>')
                elif not route.request.url.startswith(origin + "/"):
                    route.abort()
                elif parsed.path.startswith("/api/reading/media/"):
                    _, _, kind = reading_media.resolve(parsed.path.rsplit("/", 1)[-1])
                    media_requests.append((kind, route.request.headers.get("range")))
                    data = image_output.getvalue() if kind == "image" else video_bytes
                    headers = {"Content-Type": "image/png" if kind == "image" else "video/webm", "Accept-Ranges": "bytes"}
                    if mode == "media-fail":
                        route.fulfill(status=502, body="unavailable")
                    elif kind == "video" and route.request.headers.get("range"):
                        start, end = route.request.headers["range"].removeprefix("bytes=").split("-")
                        start, end = int(start or 0), min(int(end) if end else len(data)-1, len(data)-1)
                        headers["Content-Range"] = f"bytes {start}-{end}/{len(data)}"
                        route.fulfill(status=206, body=data[start:end+1], headers=headers)
                    else:
                        route.fulfill(body=data, headers=headers)
                elif parsed.path.startswith("/api/library/") or parsed.path in {"/api/reading/detail", "/api/reading/comments"}:
                    if parsed.path == "/api/reading/detail" and mode == "pending":
                        pending.append(route)
                        return
                    response = client.request(route.request.method, parsed.path,
                                              content=route.request.post_data, headers={"content-type": "application/json"})
                    route.fulfill(status=response.status_code, body=response.content, content_type="application/json")
                elif parsed.path == "/api/history/views" and route.request.method == "POST":
                    views.append(route.request.post_data_json["result"])
                    route.fulfill(json={"ok": True})
                elif parsed.path.startswith("/api/"):
                    data = job if parsed.path.startswith("/api/search/jobs/") else {"accounts": []} if parsed.path == "/api/search/accounts" else {"status": "ok", "environment_status": "ok"} if parsed.path == "/api/health" else {}
                    route.fulfill(json=data)
                else:
                    route.continue_()

            context.route("**/*", route_request)
            page = context.new_page()
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(origin + "/#/search")
            expect(page.locator(".local-content-card")).to_have_count(5)
            page.get_by_role("button", name="列表", exact=True).click()
            title = page.get_by_role("button", name=source["title"], exact=True)
            expect(title).to_be_visible()
            title.click()
            drawer = page.get_by_role("dialog", name="内容阅读", exact=True)
            expect(drawer).to_be_visible()
            expect(drawer.get_by_text("真正的正文，保留完整段落与图文顺序。", exact=True)).to_be_visible()
            expect(drawer.get_by_text(source["snippet"], exact=True)).to_have_count(0)
            expect(drawer.locator(".reader-image img")).to_be_visible()
            expect(drawer.locator("iframe, script")).to_have_count(0)
            assert not comment_reads, "Comments must load only when requested"
            assert drawer.bounding_box()["width"] == 760
            expect(drawer.get_by_role("region", name="评论", exact=True)).to_have_count(0)
            drawer.locator(".reader-body").evaluate("el => window.beforeCommentsBody = el")
            page.screenshot(path=str(output / "comments-collapsed.png"))
            drawer.get_by_role("button", name="展开评论", exact=True).click()
            expect(drawer.get_by_text("站内评论测试正文", exact=True)).to_be_visible()
            assert drawer.bounding_box()["width"] == 1120
            assert drawer.locator(".reader-body").evaluate("el => el === window.beforeCommentsBody")
            expect(drawer.locator(".reader-comment.is-reply")).to_have_count(1)
            thread = drawer.get_by_role("article", name="评论作者的评论", exact=True)
            expect(thread.get_by_text("站内回复测试正文", exact=True)).to_be_visible()
            expect(thread.get_by_role("group", name="回复 评论作者", exact=True)).to_be_visible()
            expect(thread.get_by_label("8 赞", exact=True)).to_be_visible()
            left = drawer.locator(".reading-content-column")
            right = drawer.locator(".reader-comments")
            left_box, right_box = left.bounding_box(), right.bounding_box()
            assert right_box["x"] >= left_box["x"] + left_box["width"] - 1
            assert abs(left_box["width"] - right_box["width"]) <= 2
            assert left.locator(".local-content-actions").count() == 1
            scroll_before = drawer.evaluate("el => el.scrollTop")
            right.evaluate("el => el.scrollTop = 100")
            assert right.evaluate("el => el.scrollTop") > 0 and drawer.evaluate("el => el.scrollTop") == scroll_before
            right.evaluate("el => el.scrollTop = 0")
            drawer.get_by_role("button", name="收起评论", exact=True).click()
            expect(right).to_be_hidden()
            assert drawer.bounding_box()["width"] == 760
            drawer.get_by_role("button", name="展开评论", exact=True).click()
            expect(drawer.get_by_text("站内评论测试正文", exact=True)).to_be_visible()
            assert comment_reads == ["42"], "Reopening must keep loaded comments without another request"
            drawer.get_by_role("button", name="全屏查看", exact=True).click()
            expect(drawer.get_by_text("站内评论测试正文", exact=True)).to_be_visible()
            assert comment_reads == ["42"], "Fullscreen must preserve comments without another request"
            drawer.get_by_role("button", name="退出全屏", exact=True).click()
            assert len(context.pages) == 1 and len(views) == 1 and views[0]["content_id"] == "42"
            expect(drawer.get_by_role("link", name="在原平台打开", exact=True)).to_have_attribute("href", source["url"])
            drawer.get_by_role("button", name="增大正文字号").click()
            assert drawer.locator(".reader-body").evaluate("el => getComputedStyle(el).fontSize") == "19px"
            mode = "fail"
            drawer.get_by_role("button", name="重新读取内容").click()
            expect(drawer.get_by_role("alert")).to_contain_text("正在进行")
            expect(drawer.get_by_text("真正的正文，保留完整段落与图文顺序。", exact=True)).to_be_visible()
            mode = "ok"
            drawer.get_by_role("button", name="重试读取", exact=True).click()
            expect(drawer.get_by_role("alert")).to_have_count(0)
            assert len(views) == 1
            page.screenshot(path=str(output / "desktop-light.png"))
            before_fullscreen = (len(reads), len(views))
            drawer.locator(".reader-body").evaluate("el => { window.readerBody = el; }")
            drawer.get_by_role("button", name="全屏查看", exact=True).click()
            expect(drawer.get_by_role("button", name="退出全屏", exact=True)).to_have_attribute("aria-pressed", "true")
            assert drawer.bounding_box() == {"x": 0, "y": 0, "width": 1440, "height": 1000}
            assert drawer.locator(".reader-body").evaluate("el => el === window.readerBody && getComputedStyle(el).fontSize === '19px'")
            assert before_fullscreen == (len(reads), len(views))
            page.screenshot(path=str(output / "fullscreen-light.png"))
            for width in (390, 320):
                page.set_viewport_size({"width": width, "height": 844})
                assert drawer.evaluate("el => el.scrollWidth <= el.clientWidth")
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                assert drawer.bounding_box() == {"x": 0, "y": 0, "width": width, "height": 844}
                assert right.bounding_box()["y"] >= left.bounding_box()["y"] + left.bounding_box()["height"]
                expect(drawer.get_by_role("button", name="退出全屏", exact=True)).to_be_in_viewport()
                page.screenshot(path=str(output / f"mobile-{width}.png"))
            page.evaluate("document.documentElement.classList.add('dark'); document.documentElement.dataset.accent = 'pine'")
            page.screenshot(path=str(output / "mobile-dark.png"))
            page.keyboard.press("Escape")
            expect(drawer.get_by_role("button", name="全屏查看", exact=True)).to_be_focused()
            assert drawer.locator(".reader-body").evaluate("el => el === window.readerBody")
            page.keyboard.press("Escape")
            expect(drawer).to_have_count(0)
            expect(title).to_be_focused()
            page.set_viewport_size({"width": 1440, "height": 1000})
            mode = "pending"
            title.click()
            expect(page.get_by_role("status").filter(has_text="正在读取知乎内容")).to_be_visible()
            page.keyboard.press("Escape")
            expect(drawer).to_have_count(0)
            assert len(views) == 1 and pending
            for request in pending:
                request.abort()
            pending.clear()
            mode = "ok"
            page.get_by_role("button", name=article["title"], exact=True).click()
            expect(drawer.get_by_text("当前正文可能不完整，请结合原文查看。", exact=True)).to_be_visible()
            assert len(views) == 2 and views[-1]["content_id"] == "43"
            page.keyboard.press("Escape")
            mode = "fail"
            title.click()
            expect(drawer.get_by_role("alert")).to_contain_text("正在进行")
            expect(drawer.locator(".reader-body")).to_have_count(0)
            assert len(views) == 2
            mode = "ok"
            drawer.get_by_role("button", name="重试读取", exact=True).click()
            expect(drawer.locator(".reader-body")).to_be_visible()
            assert len(views) == 3
            page.keyboard.press("Escape")
            page.get_by_role("button", name="网格", exact=True).click()
            page.get_by_role("button", name=f"查看内容信息：{source['title']}", exact=True).click()
            expect(drawer.locator(".reader-body")).to_be_visible()
            page.keyboard.press("Escape")
            page.get_by_role("button", name="列表", exact=True).click()
            page.get_by_role("button", name=note["title"], exact=True).click()
            expect(drawer.locator(".reader-image img")).to_have_count(2)
            expect(drawer.get_by_text("图文正文\n保留原始换行。", exact=True)).to_be_visible()
            drawer.locator(".reader-image img").first.scroll_into_view_if_needed()
            page.wait_for_function("(() => { const img = document.querySelector('.reader-image img'); return img?.complete && img.naturalWidth > 0; })()")
            page.screenshot(path=str(output / "xhs-gallery.png"))
            page.keyboard.press("Escape")
            page.get_by_role("button", name=douyin["title"], exact=True).click()
            video = drawer.locator("video")
            expect(video).to_have_count(1)
            expect(video).to_have_attribute("src", re.compile(r"/api/reading/media/"))
            assert not media_requests or media_requests[-1][0] == "image", "video should not preload"
            playback = video.evaluate("async el => {try {await el.play(); return {ok:true}} catch(e) {return {ok:false,error:e.name,code:el.error?.code,src:el.getAttribute('src')}}}")
            assert playback["ok"], {"playback": playback, "fixture_bytes": len(video_bytes), "media_requests": media_requests}
            page.wait_for_function("document.querySelector('.reader-video video').currentTime > 0.15")
            video.evaluate("el => { el.pause(); el.currentTime = 0.5; }")
            page.wait_for_function("!document.querySelector('.reader-video video').seeking")
            assert video.evaluate("el => el.currentTime") >= 0.49
            video.evaluate("el => { window.fullscreenVideo = el; }")
            before_fullscreen = (len(reads), len(views))
            drawer.get_by_role("button", name="全屏查看", exact=True).click()
            assert drawer.bounding_box() == {"x": 0, "y": 0, "width": 1440, "height": 1000}
            assert video.evaluate("el => el === window.fullscreenVideo && el.paused && el.currentTime >= 0.49")
            page.screenshot(path=str(output / "fullscreen-video.png"))
            drawer.get_by_role("button", name="退出全屏", exact=True).click()
            assert video.evaluate("el => el === window.fullscreenVideo && el.paused && el.currentTime >= 0.49")
            video.evaluate("el => el.play()")
            page.wait_for_function("document.querySelector('.reader-video video').currentTime > 0.7")
            drawer.get_by_role("button", name="全屏查看", exact=True).click()
            assert video.evaluate("el => el === window.fullscreenVideo && !el.paused && el.currentTime > 0.7")
            page.keyboard.press("Escape")
            assert video.evaluate("el => el === window.fullscreenVideo && !el.paused && el.currentTime > 0.7")
            video.evaluate("el => el.pause()")
            assert before_fullscreen == (len(reads), len(views))
            for width in (390, 320):
                page.set_viewport_size({"width": width, "height": 844})
                assert drawer.evaluate("el => el.scrollWidth <= el.clientWidth")
            page.screenshot(path=str(output / "douyin-mobile.png"))
            video.evaluate("el => { window.closedReaderVideo = el; }")
            page.keyboard.press("Escape")
            assert page.evaluate("window.closedReaderVideo.paused && !window.closedReaderVideo.getAttribute('src')")
            page.set_viewport_size({"width": 1440, "height": 1000})
            history_before_video = len(views)
            mode = "media-fail"
            page.get_by_role("button", name=bilibili["title"], exact=True).click()
            drawer.locator("video").evaluate("el => el.play().catch(() => {})")
            expect(drawer.get_by_role("alert")).to_contain_text("视频加载失败")
            assert len(views) == history_before_video
            page.keyboard.press("Escape")
            mode = "ok"
            page.get_by_role("button", name=bilibili["title"], exact=True).click()
            expect(drawer.get_by_role("button", name="第 1 段", exact=True)).to_have_attribute("aria-pressed", "true")
            assert len(views) == history_before_video
            drawer.get_by_role("button", name="第 2 段", exact=True).click()
            expect(drawer.get_by_role("button", name="第 2 段", exact=True)).to_have_attribute("aria-pressed", "true")
            video = drawer.locator("video")
            video.evaluate("el => el.play()")
            page.wait_for_function("document.querySelector('.reader-video video').currentTime > 0.15")
            assert len(views) == history_before_video + 1
            page.screenshot(path=str(output / "bilibili-player.png"))
            page.keyboard.press("Escape")
            mode = "media-fail"
            page.get_by_role("button", name=douyin["title"], exact=True).click()
            drawer.locator("video").evaluate("el => el.play().catch(() => {})")
            expect(drawer.get_by_role("alert")).to_contain_text("视频加载失败")
            expect(drawer.get_by_text("抖音视频正文", exact=True)).to_be_visible()
            assert any(kind == "video" and header for kind, header in media_requests)
            assert not errors, errors
            browser.close()
            print(json.dumps({"passed": True, "reading_requests": len(reads), "history_views": len(views), "screenshots": str(output)}, ensure_ascii=False))
    finally:
        reading_media.clear()
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    main()
