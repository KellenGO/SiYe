# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
# This file is part of MediaCrawler project.
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

"""Verify the built reader with a mocked platform and a temporary library."""

import json
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
    job = dict(job_id="reader-smoke", keyword="阅读", overall="completed", hydration_status="completed",
               created_at="2026-10-10T00:00:00Z", results=[source, article], platforms={"zhihu": {
                   "status": "succeeded", "result_count": 2, "error_summary": None, "cache_hit": False}})
    mode, reads, views, errors, pending = "ok", [], [], [], []

    class Reader:
        async def read(self, kind, identity, url, refresh):
            reads.append((identity, refresh))
            if mode == "fail":
                raise ReadingError("busy")
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

            def route_request(route):
                parsed = urlparse(route.request.url)
                if parsed.hostname == "pic.zhimg.com":
                    route.fulfill(content_type="image/svg+xml", body='<svg xmlns="http://www.w3.org/2000/svg" width="640" height="240"><rect width="640" height="240" fill="#e5eff8"/><path d="M180 60h280v120H180z" fill="#9fb8cc"/></svg>')
                elif not route.request.url.startswith(origin + "/"):
                    route.abort()
                elif parsed.path.startswith("/api/library/") or parsed.path == "/api/reading/detail":
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
            expect(page.locator(".local-content-card")).to_have_count(2)
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
            assert len(context.pages) == 1 and len(views) == 1 and views[0]["content_id"] == "42"
            expect(drawer.get_by_role("link", name="在原平台打开", exact=True)).to_have_attribute("href", source["url"])
            drawer.get_by_role("button", name="增大正文字号").click()
            assert drawer.locator(".reader-body").evaluate("el => getComputedStyle(el).fontSize") == "19px"
            mode = "fail"
            drawer.get_by_role("button", name="重新读取正文").click()
            expect(drawer.get_by_role("alert")).to_contain_text("正在进行")
            expect(drawer.get_by_text("真正的正文，保留完整段落与图文顺序。", exact=True)).to_be_visible()
            mode = "ok"
            drawer.get_by_role("button", name="重试读取", exact=True).click()
            expect(drawer.get_by_role("alert")).to_have_count(0)
            assert len(views) == 1
            page.screenshot(path=str(output / "desktop-light.png"))
            for width in (390, 320):
                page.set_viewport_size({"width": width, "height": 844})
                assert drawer.evaluate("el => el.scrollWidth <= el.clientWidth")
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                page.screenshot(path=str(output / f"mobile-{width}.png"))
            page.evaluate("document.documentElement.classList.add('dark'); document.documentElement.dataset.accent = 'pine'")
            page.screenshot(path=str(output / "mobile-dark.png"))
            page.keyboard.press("Escape")
            expect(drawer).to_have_count(0)
            expect(title).to_be_focused()
            page.set_viewport_size({"width": 1440, "height": 1000})
            mode = "pending"
            title.click()
            expect(page.get_by_role("status").filter(has_text="正在读取知乎正文")).to_be_visible()
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
            assert not errors, errors
            browser.close()
            print(json.dumps({"passed": True, "reading_requests": len(reads), "history_views": len(views), "screenshots": str(output)}, ensure_ascii=False))
    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    main()
