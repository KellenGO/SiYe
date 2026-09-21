"""Exercise exploration UI against isolated mocked APIs; no platform traffic."""
import json
import sys
import threading
import time
from copy import deepcopy
from datetime import datetime, timezone
from functools import partial
from http.server import ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import urlparse

from fastapi import FastAPI
from fastapi.testclient import TestClient
from playwright.sync_api import expect, sync_playwright
from result_library_smoke import ROOT, QuietHandler

sys.path.insert(0, str(ROOT))
from api.routers.library import library_router  # noqa: E402
from api.services.library_store import LibraryStore, get_library_store  # noqa: E402


def main():
    fetched = datetime.now(timezone.utc).isoformat()
    def note(content_id, title, platform="xhs"):
        return dict(platform=platform, content_id=content_id, title=title, content_type="note",
            url=(f"https://www.xiaohongshu.com/explore/{content_id}" if platform == "xhs" else f"https://www.bilibili.com/video/{content_id}"), author="测试作者",
            snippet="用于验证分页的素材摘要", published_at=None, cover_url=None, metrics={}, rank=0)
    old = note("old", "第一批收藏素材")
    new = note("new", "第二批新素材")
    version = note("video", old["title"], "bilibili")
    platforms = {p: dict(status="succeeded", result_count=1, error_summary=None,
                         fetched_at=fetched, cache_hit=False) for p in ("xhs", "bilibili")}
    first = dict(job_id="round-1", keyword="素材", overall="completed", created_at=fetched,
        completed_at=fetched, hydration_status="completed", total_ms=100, platforms=platforms, results=[old])
    first["exploration"] = dict(id="topic", round=1, max_per_platform=100, new_sources=1, new_contents=1,
        page_requests=2, duplicates=0, platforms={p: dict(collected=int(p == "xhs"), has_more=True) for p in platforms},
        previous_batches=[])
    def batch(job, number):
        return {**{key: deepcopy(job[key]) for key in ("job_id", "overall", "completed_at", "platforms", "results")}, "number": number}
    second = deepcopy(first)
    second.update(job_id="round-2", results=[new])
    prior = batch(first, 1)
    prior["results"] = [{**old, "grouped_sources": [old, version]}]
    second["exploration"].update(round=2, new_sources=2, new_contents=1, previous_batches=[prior],
        platforms={"xhs": dict(collected=2, has_more=True), "bilibili": dict(collected=1, has_more=False)})
    third = deepcopy(second)
    third.update(job_id="round-3", results=[])
    third["exploration"].update(round=3, new_sources=0, new_contents=0, previous_batches=[prior, batch(second, 2)],
        platforms={"xhs": dict(collected=2, has_more=False), "bilibili": dict(collected=1, has_more=False)})
    current = deepcopy(first)
    posts, errors = [], []
    # 收藏走的是后端 SQLite（不再是 localStorage），所以 /api/library 要转发给真实的 store
    store = None
    client = None
    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(QuietHandler, directory=str(ROOT / "webui/dist")))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    origin = f"http://127.0.0.1:{server.server_port}"

    def wait_for_posts(count: int, timeout: float = 5.0) -> None:
        """等 mock 真的收到第 count 个搜索请求。

        「换一批」按钮变 disabled 早于请求抵达 mock，点完立刻读 posts[-1] 会读到上一条，
        断言就会拿错请求（曾经因此误判成行为变更）。
        """
        deadline = time.time() + timeout
        while len(posts) < count and time.time() < deadline:
            # 必须用 Playwright 的等待：time.sleep 会阻塞同步 API 的事件循环，
            # 期间到达的请求不会被 route 回调处理，posts 永远涨不上去。
            page.wait_for_timeout(50)
        assert len(posts) >= count, f"只等到 {len(posts)} 个搜索请求，期望第 {count} 个"

    def route_request(route):
        nonlocal current
        request = route.request
        path = urlparse(request.url).path
        if not request.url.startswith(origin + "/"):
            return route.abort()
        if not path.startswith("/api/"):
            return route.continue_()
        if path.startswith("/api/library/"):
            parsed = urlparse(request.url)
            response = client.request(
                request.method,
                path + ("?" + parsed.query if parsed.query else ""),
                content=request.post_data,
                headers={"content-type": "application/json"},
            )
            return route.fulfill(status=response.status_code, body=response.content, content_type="application/json")
        if path == "/api/search/jobs" and request.method == "POST":
            req = request.post_data_json
            posts.append(req)
            if req.get("continue_from") == "round-1":
                current = deepcopy(second)
            elif req.get("continue_from") == "round-2":
                current = deepcopy(third)
            else:
                current = deepcopy(first)
                current["job_id"] = "refreshed"
            return route.fulfill(json=current, status=201)
        if path.startswith("/api/search/jobs/"):
            data = current
        elif path == "/api/health":
            data = {"status": "ok", "environment_status": "ok", "version": "smoke", "version_match": True}
        elif path == "/api/search/accounts":
            data = {"accounts": []}
        else:
            data = {}
        route.fulfill(json=data)

    try:
        (ROOT / "build").mkdir(exist_ok=True)
        with TemporaryDirectory(prefix="search-exploration-", dir=ROOT / "build") as temp, sync_playwright() as p:
            store = LibraryStore(Path(temp) / "library.db")
            app = FastAPI()
            app.include_router(library_router)
            app.dependency_overrides[get_library_store] = lambda: store
            client = TestClient(app)
            browser = p.chromium.launch(channel="msedge")
            context = browser.new_context(viewport={"width": 1280, "height": 900})
            context.add_init_script("localStorage.setItem('mediacrawler_license_accepted', 'true')")
            context.route("**/*", route_request)
            page = context.new_page()
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(origin)
            expect(page.get_by_role("button", name="换一批", exact=True)).to_be_enabled()
            expect(page.get_by_role("button", name="刷新结果", exact=True)).to_have_count(0)
            page.get_by_role("button", name="收藏 第一批收藏素材", exact=True).click()
            page.get_by_role("button", name="换一批", exact=True).click()
            wait_for_posts(1)
            expect(page.get_by_role("button", name="收藏 第二批新素材", exact=True)).to_be_visible()
            assert len(posts) == 1 and posts[0]["continue_from"] == "round-1"
            assert posts[0]["bypass_cache"] is True and posts[0]["limit_per_platform"] == 20
            assert "pagination" not in posts[0]
            page.locator("summary").filter(has_text="更多").click()
            page.get_by_label("查看轮次", exact=True).select_option("1")
            expect(page.get_by_role("button", name="选择收藏平台 第一批收藏素材", exact=True)).to_be_visible()
            expect(page.get_by_role("button", name="收藏 第二批新素材", exact=True)).to_have_count(0)
            # 收藏落库（SQLite），不再是 localStorage：直接查 store
            assert store.stats()["total"] == 1
            assert store.get_item("xhs", "old") is not None
            page.reload()
            expect(page.get_by_role("button", name="收藏 第二批新素材", exact=True)).to_be_visible()
            page.locator("summary").filter(has_text="更多").click()
            page.get_by_label("查看轮次", exact=True).select_option("1")
            expect(page.get_by_role("button", name="选择收藏平台 第一批收藏素材", exact=True)).to_be_visible()
            page.get_by_label("查看轮次", exact=True).select_option("2")
            page.locator("summary").filter(has_text="更多").click()
            # 切回轮次 2 后视图应该是第二轮的内容，再从这里继续换一批
            expect(page.get_by_role("button", name="收藏 第二批新素材", exact=True)).to_be_visible()
            page.get_by_role("button", name="换一批", exact=True).click()
            wait_for_posts(2)
            expect(page.get_by_role("button", name="换一批", exact=True)).to_be_disabled()
            assert posts[-1]["continue_from"] == "round-2" and posts[-1]["platforms"] == ["xhs"], posts[-1]
            page.locator("summary").filter(has_text="更多").click()
            page.get_by_label("查看轮次", exact=True).select_option("2")
            expect(page.get_by_role("button", name="收藏 第二批新素材", exact=True)).to_be_visible()
            page.get_by_role("button", name="刷新结果", exact=True).click()
            wait_for_posts(3)
            expect(page.get_by_role("button", name="取消收藏 第一批收藏素材", exact=True)).to_be_visible()
            assert "continue_from" not in posts[-1] and posts[-1]["bypass_cache"] is True
            assert set(posts[-1]["platforms"]) == {"xhs", "bilibili"}
            for width in (390, 320):
                page.set_viewport_size({"width": width, "height": 844})
                assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
            page.set_viewport_size({"width": 1280, "height": 900})
            page.screenshot(path=str(ROOT / "build/search-exploration-desktop.png"), full_page=True)
            assert errors == [], errors
            context.close()
            browser.close()
        print(json.dumps({"passed": True, "search_posts": len(posts), "checks": [
            "next_batch", "distinct_results", "old_round_versions", "bookmarks_preserved", "reload_recovery",
            "exhaustion", "refresh_from_start", "collapsed_actions", "mobile_320_390"]}))
    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    raise SystemExit(main())
