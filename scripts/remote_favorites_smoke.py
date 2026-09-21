"""Exercise remote archive pagination and decisions against a temporary SQLite DB.

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
        rows = [{"platform": "bilibili", "content_id": f"BV{i}", "content_type": "video",
                 "title": f"同步条目 {i}", "author": "测试作者", "url": f"https://www.bilibili.com/video/BV{i}"}
                for i in range(61)]
        scan = store.begin_scan("bilibili:1", [{"id": "10"}], "full")
        for offset in range(0, 61, 20):
            store.save_sync_page("bilibili:1", scan, "10", "收藏夹", offset // 20 + 1,
                                 str(offset), rows[offset:offset + 20], offset == 60)
        store.finish_scan("bilibili:1", scan, True)
        scan = store.begin_scan("bilibili:1", [{"id": "10"}], "full")
        for offset in range(0, 59, 20):
            store.save_sync_page("bilibili:1", scan, "10", "收藏夹", offset // 20 + 1,
                                 str(offset), rows[offset:min(offset + 20, 59)], offset == 40)
        store.finish_scan("bilibili:1", scan, True)
        library.add_item(rows[60], note="本地备注必须保留")
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
            url = urlparse(route.request.url)
            if not route.request.url.startswith(origin + "/"):
                route.abort()
            elif url.path == "/api/search/favorites/jobs" and route.request.method == "POST":
                sync_requests.append(route.request.post_data_json)
                response = client.get("/api/search/favorites/jobs/latest?summary=true")
                route.fulfill(status=201, json=response.json())
            elif url.path.startswith(("/api/search/favorites/", "/api/library/")):
                response = client.request(route.request.method, url.path + ("?" + url.query if url.query else ""),
                                          content=route.request.post_data, headers={"content-type": "application/json"})
                route.fulfill(status=response.status_code, body=response.content, content_type="application/json")
            elif url.path.startswith("/api/"):
                route.fulfill(json={"accounts": [], "status": "ok"})
            else:
                route.continue_()

        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(channel="msedge", headless=True)
                context = browser.new_context(viewport={"width": 1440, "height": 960})
                context.add_init_script("localStorage.setItem('mediacrawler_license_accepted','true')")
                context.route("**/*", route_request)
                page = context.new_page()
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(origin + "/#/favorites/remote")
                expect(page.get_by_text("共 61 条 · 第 1 页 · 每页 50 条")).to_be_visible()
                assert sync_requests == []
                page.get_by_role("button", name="下一页", exact=True).click()
                expect(page.get_by_text("共 61 条 · 第 2 页 · 每页 50 条")).to_be_visible()
                page.get_by_role("button", name="待确认 2 条", exact=True).click()
                expect(page.get_by_text("共 2 条 · 第 1 页 · 每页 50 条")).to_be_visible()
                expect(page.get_by_label("选择本页待确认条目")).not_to_be_checked()
                page.get_by_label("选择本页待确认条目").check()
                page.get_by_role("button", name="移除选中条目", exact=True).click()
                dialog = page.get_by_role("dialog")
                expect(dialog.get_by_text("只移除同步归档及远端归属", exact=False)).to_be_visible()
                dialog.get_by_role("button", name="取消", exact=True).click()
                assert store.archive_page()["total"] == 61
                page.get_by_role("button", name="移除选中条目", exact=True).click()
                page.get_by_role("dialog").get_by_role("button", name="从跨平台收藏移除", exact=True).click()
                expect(page.get_by_text("共 0 条 · 第 1 页 · 每页 50 条")).to_be_visible()
                assert store.archive_page()["total"] == 59
                assert library.get_item("bilibili", "BV60")["note"] == "本地备注必须保留"
                page.get_by_role("button", name="完整核对 B站", exact=True).click()
                expect(page.get_by_role("button", name="完整核对 B站", exact=True)).to_be_enabled()
                assert sync_requests[-1]["platforms"] == ["bilibili"]
                assert sync_requests[-1]["sync_mode"] == "full"
                assert errors == [], errors
                page.screenshot(path=str(ROOT / "build/remote-favorites-smoke.png"), full_page=True)
                browser.close()
        finally:
            server.shutdown()
            server.server_close()
            client.close()
            remote_module._store = None
    print(json.dumps({"passed": ["pagination", "no-auto-sync", "pending-list", "default-unselected", "cancel-remove", "batch-remove", "local-note-preserved", "single-platform-full"], "page_errors": errors}))


if __name__ == "__main__":
    main()
