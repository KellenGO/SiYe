"""Built UI + real local APIs, with mocked Zhihu and an isolated temporary library."""

import json
import os
import re
import sys
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from urllib.parse import urlparse

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


def main():
    with TemporaryDirectory(prefix=".tmp_zhihu_question_ui_", dir=ROOT) as temp:
        os.environ["SIYE_DATA_DIR"] = temp
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from aggregate_search.adapters.zhihu import ZhihuAdapter, search_reading_payload
        from api.routers.library import library_router
        from api.routers.reading import reading_router, get_reading_service
        from api.routers.spaces import spaces_router
        from api.services.library_store import LibraryStore, get_library_store
        from api.services.spaces_store import SpacesStore, get_spaces_store
        from api.services import reading
        from api.services.operation_coordinator import OperationCoordinator

        def raw(identity, votes, qid="1"):
            return {"id": identity, "type": "answer", "question": {"id": qid, "title": "你去过最离谱的景区是什么？"},
                    "author": {"name": f"作者{identity}"}, "voteup_count": votes, "comment_count": int(identity),
                    "visits_count": 112424515, "excerpt": f"回答{identity}摘要", "content": f"<p>回答{identity}的独立正文</p>"}
        low, high, more = raw("42", 4), raw("99", 5436), raw("100", 6000)
        adapter = ZhihuAdapter()
        source = adapter.adapt([low])[0]
        reader = reading.ReadingService()
        store = LibraryStore(Path(temp) / "library.db")
        spaces = SpacesStore(Path(temp) / "library.db")
        space = spaces.create_space("知乎验证空间", "")
        spaces.set_active(space["id"])
        app = FastAPI()
        for router in (library_router, reading_router, spaces_router):
            app.include_router(router)
        app.dependency_overrides[get_library_store] = lambda: store
        app.dependency_overrides[get_spaces_store] = lambda: spaces
        app.dependency_overrides[get_reading_service] = lambda: reader
        errors, views, requests, comments = [], [], [], []
        fail_page = [False]

        async def prepare(*args):
            pass
        async def snapshot(*args, **kwargs):
            return {"d_c0": "fixture"}
        async def fetch(snapshot, title, offset):
            requests.append(offset)
            if fail_page[0]:
                raise reading.ReadingError("rate_limited")
            values = [high, raw("80", 10000, "2")] if offset == 0 else [more, high]
            return {"data": [{"object": value} for value in values],
                    "paging": {"is_end": offset > 0, "next": "https://www.zhihu.com/api/v4/search_v3?offset=20"}}
        async def fetch_comments(kind, identity, url, platform):
            comments.append(identity)
            return {"platform": platform, "content_id": identity, "content_type": kind,
                    "entries": [{"id": f"c{identity}", "author": "评论作者", "text": f"回答{identity}的评论", "like_count": 1}],
                    "limited": False, "sort": "平台默认", "notice": ""}

        output = ROOT / "build" / "zhihu-questions-ui"
        output.mkdir(parents=True, exist_ok=True)
        server = ThreadingHTTPServer(("127.0.0.1", 0), partial(QuietHandler, directory=str(ROOT / "webui" / "dist")))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        origin = f"http://127.0.0.1:{server.server_port}"
        job = {"job_id": "zhihu-question-ui", "keyword": "小樽", "overall": "completed", "hydration_status": "completed",
               "created_at": "2026-10-11T00:00:00Z", "results": [source.model_dump()], "platforms": {"zhihu": {"status": "succeeded", "result_count": 1, "cache_hit": False, "error_summary": None}}}
        try:
            with patch.object(reading, "get_account_generation", lambda _: 7), patch.object(reading, "reading_service", reader), \
                 patch.object(reading, "operation_coordinator", OperationCoordinator()), patch.object(reading, "prepare_reading", prepare), \
                 patch.object(reading, "ensure_session_snapshot", snapshot), patch.object(reading, "fetch_question_page", fetch), \
                 patch.object(reading, "fetch_comments", fetch_comments), TestClient(app, base_url=origin) as client, sync_playwright() as pw:
                reader.remember_search_result(source, 7)
                reader.remember_search_body(search_reading_payload(low), 7)
                browser = pw.chromium.launch(channel="msedge", headless=True)
                context = browser.new_context(viewport={"width": 1440, "height": 1000})
                context.add_init_script("localStorage.setItem('mediacrawler_license_accepted','true'); localStorage.setItem('siye_onboarding_preference_v1','completed');")

                def route_request(route):
                    parsed = urlparse(route.request.url)
                    if not route.request.url.startswith(origin + "/"):
                        route.abort()
                    elif parsed.path.startswith(("/api/library/", "/api/reading/")) or parsed.path.startswith("/api/spaces"):
                        response = client.request(route.request.method, parsed.path + ("?" + parsed.query if parsed.query else ""),
                                                  content=route.request.post_data, headers={"content-type": "application/json"})
                        route.fulfill(status=response.status_code, body=response.content, content_type="application/json")
                    elif parsed.path == "/api/history/views" and route.request.method == "POST":
                        views.append(route.request.post_data_json["result"])
                        route.fulfill(json={"ok": True})
                    elif parsed.path.startswith("/api/"):
                        route.fulfill(json=job if parsed.path.startswith("/api/search/jobs/") else {"accounts": []} if parsed.path == "/api/search/accounts" else {"status": "ok", "environment_status": "ok"} if parsed.path == "/api/health" else {})
                    else:
                        route.continue_()

                context.route("**/*", route_request)
                page = context.new_page()
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(origin + "/#/search")
                expect(page.locator(".local-content-card")).to_have_count(1)
                expect(page.get_by_text("已获取 1 条回答", exact=True)).to_be_visible()
                page.get_by_role("button", name="选择回答收藏 你去过最离谱的景区是什么？", exact=True).click()
                drawer = page.get_by_role("dialog", name="内容阅读", exact=True)
                expect(drawer.locator(".local-content-detail")).to_have_count(2)
                expect(drawer.locator(".local-content-detail").first.get_by_text("作者99", exact=True)).to_be_visible()
                assert not views and not comments
                assert "112,424,515" not in drawer.inner_text()
                high_section = drawer.locator(".local-content-detail").filter(has=page.get_by_text("作者99", exact=True))
                high_section.get_by_role("button", name="收藏 你去过最离谱的景区是什么？", exact=True).click()
                membership = page.get_by_role("dialog", name=re.compile("收藏夹归属"))
                expect(membership).to_be_visible()
                membership.get_by_role("button", name="完成", exact=True).click()
                high_section.get_by_role("button", name="稍后再看 你去过最离谱的景区是什么？", exact=True).click()
                high_section.get_by_role("button", name="加入空间：你去过最离谱的景区是什么？", exact=True).click()
                high_section.get_by_role("button", name="阅读 作者99 的回答", exact=True).click()
                expect(high_section.get_by_text("回答99的独立正文", exact=True)).to_be_visible()
                expect(high_section.get_by_text("当前正文可能不完整，请结合原文查看。", exact=True)).to_have_count(0)
                high_section.get_by_role("button", name="展开评论", exact=True).click()
                expect(high_section.get_by_text("回答99的评论", exact=True)).to_be_visible()
                assert comments == ["99"] and [view["content_id"] for view in views] == ["99"]
                fail_page[0] = True
                drawer.get_by_role("button", name="加载更多回答", exact=True).click()
                expect(drawer.get_by_role("alert")).to_be_visible()
                expect(high_section.get_by_text("回答99的独立正文", exact=True)).to_be_visible()
                fail_page[0] = False
                drawer.get_by_role("button", name="重试加载回答", exact=True).click()
                expect(drawer.locator(".local-content-detail")).to_have_count(3)
                expect(drawer.locator(".local-content-detail").first.get_by_text("作者100", exact=True)).to_be_visible()
                assert requests == [0, 20, 20]
                page.screenshot(path=str(output / "desktop.png"))
                drawer.get_by_role("button", name="全屏查看", exact=True).click()
                expect(high_section.get_by_text("回答99的评论", exact=True)).to_be_visible()
                for width in (390, 320):
                    page.set_viewport_size({"width": width, "height": 844})
                    assert drawer.evaluate("el => el.scrollWidth <= el.clientWidth")
                    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                    page.screenshot(path=str(output / f"mobile-{width}.png"))
                page.keyboard.press("Escape")
                expect(drawer.get_by_role("button", name="全屏查看", exact=True)).to_be_visible()
                page.keyboard.press("Escape")
                expect(drawer).to_have_count(0)
                page.get_by_role("button", name="选择回答收藏 你去过最离谱的景区是什么？", exact=True).click()
                expect(drawer.locator(".local-content-detail")).to_have_count(3)
                expect(drawer.get_by_role("button", name="加载更多回答", exact=True)).to_have_count(0)
                assert requests == [0, 20, 20]
                page.keyboard.press("Escape")
                expect(drawer).to_have_count(0)
                saved = client.get("/api/library/items").json()["items"]
                assert [item["result"]["content_id"] for item in saved] == ["99"]
                assert [item["result"]["content_id"] for item in spaces.get_space(space["id"])["items"]] == ["99"]
                assert not errors, errors
                browser.close()
                print(json.dumps({"passed": True, "question_pages": len(requests), "saved_answer": "99", "comments": comments,
                                  "screenshots": str(output)}, ensure_ascii=False))
        finally:
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    main()
