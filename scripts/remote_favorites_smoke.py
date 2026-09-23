"""跨平台收藏页浏览器验收：平台页签、一次 100 条 + 显示更多、增量/重新同步全部。

Run after building webui. All external requests are blocked; no real profile is used.
"""
from __future__ import annotations

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
from api.routers.search import search_router
from api.routers.library import library_router
from api.services.library_store import LibraryStore, get_library_store
from api.services.remote_favorites_store import RemoteFavoritesStore
import api.services.remote_favorites_store as remote_module


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *_):
        pass


def main():
    (ROOT / "build").mkdir(exist_ok=True)
    with TemporaryDirectory(prefix="remote-smoke-", dir=ROOT / "build") as temp:
        store = RemoteFavoritesStore(Path(temp) / "library.db")
        remote_module._store = store
        library = LibraryStore(store.db_path)
        # 130 条 B站 + 10 条小红书：够验证"先渲染 100 条、点显示更多再展开"和平台页签过滤。
        rows = [{"platform": "bilibili", "content_id": f"BV{i}", "content_type": "video",
                 "title": f"同步条目 {i}", "author": "测试作者", "url": f"https://www.bilibili.com/video/BV{i}"}
                for i in range(130)]
        rows += [{"platform": "xhs", "content_id": f"note{i}", "content_type": "note",
                  "title": f"小红书条目 {i}", "author": "测试作者", "url": f"https://www.xiaohongshu.com/explore/{i}"}
                 for i in range(10)]
        # 直接落库（不走平台）：页面读的就是这份本地缓存。
        store.save_platform("bilibili", rows[:130], status="succeeded")
        store.save_platform("xhs", rows[130:], status="succeeded")
        library.add_item(rows[0], note="本地备注必须保留")
        app = FastAPI()
        app.include_router(search_router)
        app.include_router(library_router)
        app.dependency_overrides[get_library_store] = lambda: library
        client = TestClient(app)
        server = ThreadingHTTPServer(("127.0.0.1", 0), partial(QuietHandler, directory=str(ROOT / "webui/dist")))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        origin = f"http://127.0.0.1:{server.server_port}"
        errors, sync_requests = [], []

        def route_request(route):
            request = route.request
            url = urlparse(request.url)
            if not request.url.startswith(origin + "/"):
                return route.abort()
            if not url.path.startswith("/api/"):
                return route.continue_()
            if url.path == "/api/search/favorites/jobs" and request.method == "POST":
                sync_requests.append(request.post_data_json or {})
                # 不真的跑同步：返回一份已完成的空任务，页面保留本地缓存即可。
                return route.fulfill(status=201, json={
                    "job_id": "smoke", "overall": "completed", "created_at": "2026-09-21T00:00:00Z",
                    "completed_at": "2026-09-21T00:00:00Z", "platforms": {}, "results": []})
            # 本机缓存相关一律交给真实后端（同一份临时 SQLite），页面读到的才是落地数据
            if url.path.startswith("/api/library/") or url.path.startswith("/api/search/favorites/"):
                response = client.request(request.method, url.path + ("?" + url.query if url.query else ""),
                                          content=request.post_data, headers={"content-type": "application/json"})
                return route.fulfill(status=response.status_code, body=response.content, content_type="application/json")
            if url.path == "/api/health":
                data = {"status": "ok", "environment_status": "ok", "version": "smoke", "version_match": True}
            elif url.path == "/api/search/accounts":
                data = {"accounts": []}
            else:
                data = {}
            route.fulfill(json=data)

        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(channel="msedge", headless=True)
                context = browser.new_context(viewport={"width": 1440, "height": 960},
                                              permissions=["clipboard-read", "clipboard-write"])
                context.add_init_script("localStorage.setItem('mediacrawler_license_accepted','true'); localStorage.setItem('siye_onboarding_preference_v1','completed')")
                context.route("**/*", route_request)
                page = context.new_page()
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(origin + "/#/favorites/remote")
                # 打开页面只读本机缓存，不发同步请求
                expect(page.get_by_text("已保存 140 条收藏", exact=False)).to_be_visible()
                assert sync_requests == []

                # 平台页签直接点着切（V0.3 的用法）
                page.get_by_role("tab", name="B站").click()
                expect(page.get_by_text("已显示 100 / 130 条")).to_be_visible()
                assert page.locator("article.result-row").count() == 100
                # 一次只渲染 100 条，点「显示更多」把剩下的铺开
                page.get_by_role("button", name="显示更多", exact=True).click()
                expect(page.get_by_role("button", name="显示更多", exact=True)).to_have_count(0)
                assert page.locator("article.result-row").count() == 130
                page.get_by_role("tab", name="小红书").click()
                expect(page.get_by_text("小红书条目 3", exact=False).first).to_be_visible()
                assert page.locator("article.result-row").count() == 10

                # 一键同步 = 增量；重新同步全部是另一个显式动作
                page.get_by_role("tab", name="全部").click()
                page.screenshot(path=str(ROOT / "build/review-v1-remote-favorites.png"), full_page=False)
                # 勾选后按钮的 accessible name 会变成「小红书 ✓」，所以按 class 定位
                xhs_choice = page.locator(".platform-choice").filter(has_text="小红书")
                if xhs_choice.get_attribute("aria-pressed") == "false":
                    xhs_choice.click()
                page.get_by_role("button", name="同步所选平台 / 继续", exact=True).click()
                page.wait_for_timeout(400)
                assert sync_requests[-1]["sync_mode"] == "auto", sync_requests[-1]
                # 同步按钮会把勾选清掉，重新扫之前先确认还勾着
                if page.locator(".platform-choice").filter(has_text="小红书").get_attribute("aria-pressed") == "false":
                    page.locator(".platform-choice").filter(has_text="小红书").click()
                page.get_by_role("button", name="重新同步全部", exact=True).click()
                page.wait_for_timeout(400)
                assert sync_requests[-1]["sync_mode"] == "full", sync_requests[-1]

                # 结果区仍是平台页签 + 排序下拉，不是归档筛选器
                assert page.get_by_role("tablist", name="结果平台").count() == 1
                assert page.get_by_label("归档平台").count() == 0
                assert page.get_by_role("button", name="完整核对 B站", exact=True).count() == 0
                assert errors == [], errors
                page.screenshot(path=str(ROOT / "build/remote-favorites-smoke.png"), full_page=True)
                browser.close()
        finally:
            server.shutdown()
            server.server_close()
            client.close()
    print(json.dumps({"passed": ["local-cache-only", "platform-tabs", "show-more", "incremental-sync", "full-rescan", "no-archive-toolbar"]}))


if __name__ == "__main__":
    raise SystemExit(main())
