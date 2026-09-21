"""Exercise the built result library UI with mocked APIs and an isolated browser.

Run after `cd webui && npm run build`: python scripts/result_library_smoke.py
Uses the project's existing Playwright dependency and system Edge by default.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import sys
import threading
from datetime import datetime, timedelta, timezone
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
from api.services.library_store import LibraryStore, get_library_store
BOOKMARKS_KEY = "aggregate_search_bookmarks_v1"


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, format, *args):
        pass


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--channel", default="msedge", help="Browser channel, or chromium")
    parser.add_argument("--screenshots", action="store_true", help="Save UI review images under build/")
    args = parser.parse_args()
    dist = ROOT / "webui" / "dist"
    if not (dist / "index.html").is_file():
        raise SystemExit("Build webui first: npm run build")
    now = datetime.now(timezone.utc)
    fetched = now.isoformat()

    def source(platform, content_id, title, kind, url, age):
        return dict(platform=platform, content_id=content_id, title=title,
                    content_type=kind, url=url, author="测试作者", snippet="研究素材摘要",
                    published_at=(now - timedelta(days=age)).isoformat(), cover_url=None,
                    metrics={"like_count": 12}, rank=1, grouped_sources=None)

    note = source("xhs", "old-note", "研究素材图文", "note", "https://www.xiaohongshu.com/explore/old-note", 20)
    video = source("bilibili", "new-video", "研究素材视频", "video", "https://www.bilibili.com/video/new-video", 1)
    article = source("zhihu", "article", "独立文章", "article", "https://zhuanlan.zhihu.com/p/article", 2)
    group = {**note, "grouped_sources": [note, video]}
    job = dict(job_id="library-smoke", overall="completed", keyword="研究素材",
               created_at=fetched, completed_at=fetched, total_ms=100,
               hydration_status="completed", results=[group, article],
               platforms={p: dict(status="succeeded", result_count=1, error_summary=None,
                                  fetched_at=fetched, cache_hit=False)
                          for p in ("xhs", "bilibili", "zhihu")})
    serving_job = True
    api_writes = []
    fail_write = False
    client = None
    errors = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(QuietHandler, directory=str(dist)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    origin = f"http://127.0.0.1:{server.server_port}"

    def route_request(route):
        url = urlparse(route.request.url)
        if not route.request.url.startswith(origin + "/"):
            route.abort()
        elif url.path.startswith("/api/library/"):
            if fail_write and route.request.method != "GET":
                route.fulfill(status=503, json={"detail": "测试收藏写入失败"})
                return
            response = client.request(route.request.method, url.path + ("?" + url.query if url.query else ""),
                content=route.request.post_data, headers={"content-type": "application/json"})
            route.fulfill(status=response.status_code, body=response.content, content_type="application/json")
        elif url.path.startswith("/api/"):
            if route.request.method != "GET":
                api_writes.append((route.request.method, url.path))
            if url.path == "/api/health":
                data = {"status": "ok", "environment_status": "ok"}
            elif url.path == "/api/search/accounts":
                data = {"accounts": []}
            elif url.path == "/api/search/favorites/jobs/latest":
                data = None
            elif url.path.startswith("/api/search/jobs/"):
                data = job if serving_job else None
            else:
                data = {}
            route.fulfill(body=json.dumps(data), content_type="application/json")
        else:
            route.continue_()

    try:
        (ROOT / "build").mkdir(exist_ok=True)
        with TemporaryDirectory(prefix="result-library-", dir=ROOT / "build") as temp, sync_playwright() as p:
            store = LibraryStore(Path(temp) / "library.db")
            app = FastAPI()
            app.include_router(library_router)
            app.dependency_overrides[get_library_store] = lambda: store
            client = TestClient(app)
            browser = p.chromium.launch(**({} if args.channel == "chromium" else {"channel": args.channel}))
            context = browser.new_context(viewport={"width": 1280, "height": 900},
                                          permissions=["clipboard-read", "clipboard-write"])
            context.add_init_script("localStorage.setItem('mediacrawler_license_accepted', 'true')")
            context.route("**/*", route_request)
            page = context.new_page()
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(origin + "/#/search")
            expect(page.get_by_role("button", name="选择收藏平台 研究素材图文", exact=True)).to_be_visible()
            expect(page.get_by_role("button", name="导出 CSV", exact=True)).to_have_count(0)
            expect(page.get_by_role("checkbox", name="选择当前全部结果", exact=True)).to_have_count(0)
            page.get_by_role("button", name="导出 / 复制", exact=True).click()
            page.get_by_label("结果内关键词").fill("不存在的内容")
            expect(page.get_by_role("button", name="导出 CSV", exact=True)).to_be_disabled()
            page.get_by_label("结果内关键词").fill("视频")
            expect(page.get_by_role("checkbox", name="选择 研究素材视频", exact=True)).to_be_visible()
            expect(page.locator("mark")).to_have_text(["视频"])
            page.get_by_role("button", name="清除筛选", exact=True).click()
            page.get_by_role("button", name="收起导出", exact=True).click()
            expect(page.get_by_role("checkbox", name="选择当前全部结果", exact=True)).to_have_count(0)
            page.get_by_role("button", name="选择收藏平台 研究素材图文", exact=True).click()
            popup = page.get_by_role("group", name="选择收藏来源", exact=True)
            # 这个按钮在 224px 宽的下拉里，被压窄就会一个字一行（历史回归）
            group_button = popup.get_by_role("button", name="全部加入收藏 研究素材图文", exact=True)
            group_box = group_button.bounding_box()
            assert group_box is not None and group_box["height"] < 44, "「全部加入收藏」按钮被压成了竖排文字"
            assert group_box["width"] >= 90, "「全部加入收藏」按钮宽度不足"
            if args.screenshots:
                page.screenshot(path=str(ROOT / "build/result-bookmark-menu.png"), full_page=True)
            popup.get_by_role("button", name="收藏 研究素材视频", exact=True).click()
            expect(popup.get_by_role("button", name="取消收藏 研究素材视频", exact=True)).to_be_visible()
            assert store.get_item("bilibili", "new-video") is not None
            page.keyboard.press("Escape")
            page.get_by_role("button", name="展开完整内容", exact=True).click()
            expect(page.get_by_text("2 个平台的内容版本", exact=True)).to_be_visible()
            page.get_by_role("button", name="收起完整内容", exact=True).click()
            page.get_by_role("button", name="选择收藏平台 研究素材图文", exact=True).click()
            popup.get_by_role("button", name="收藏 研究素材图文", exact=True).click()
            popup.get_by_role("button", name="取消收藏 研究素材图文", exact=True).first.click()
            # 取消收藏会先弹居中的确认框（见 docs/features/favorites-library.md）
            page.locator(".confirm-card").get_by_role("button", name="取消收藏", exact=True).click()
            expect(page.locator(".confirm-card")).to_have_count(0)
            expect(popup.get_by_role("button", name="收藏 研究素材图文", exact=True)).to_be_visible()
            assert store.get_item("xhs", "old-note") is None
            popup.get_by_role("button", name="全部加入收藏 研究素材图文", exact=True).click()
            expect(popup.get_by_role("button", name="取消收藏 研究素材图文", exact=True)).to_have_count(2)
            page.keyboard.press("Escape")
            assert page.locator("a button, a input, a textarea").count() == 0
            # Export and clipboard still operate on selected search sources.
            page.get_by_role("button", name="导出 / 复制", exact=True).click()
            page.get_by_label("结果内关键词").fill("视频")
            page.get_by_role("checkbox", name="选择 研究素材视频", exact=True).check()
            for label, filename in (("导出 CSV", "selected.csv"), ("导出 Markdown", "selected.md")):
                with page.expect_download() as download:
                    page.get_by_role("button", name=label, exact=True).click()
                path = Path(temp) / filename
                download.value.save_as(path)
                text = path.read_text(encoding="utf-8-sig")
                assert video["url"] in text and note["url"] not in text
                if filename.endswith(".csv"):
                    rows = list(csv.DictReader(io.StringIO(text)))
                    assert len(rows) == 1 and rows[0]["标题"] == video["title"]
            page.get_by_role("button", name="复制链接", exact=True).click()
            expect(page.get_by_text("原文链接已复制", exact=True)).to_be_visible()
            assert page.evaluate("navigator.clipboard.readText()") == video["url"]
            page.goto(origin + "/#/favorites/local")
            expect(page.get_by_role("heading", name="留住值得再看的内容", exact=True)).to_be_visible()
            expect(page.get_by_role("button", name="取消收藏 研究素材视频", exact=True)).to_be_visible()
            page.get_by_role("button", name="添加备注 研究素材视频", exact=True).click()
            page.get_by_label("备注 研究素材视频", exact=True).fill("稍后整理，保留原文")
            page.get_by_role("button", name="保存备注", exact=True).click()
            expect(page.get_by_text("稍后整理，保留原文", exact=True)).to_be_visible()
            serving_job = False
            page.reload()
            expect(page.get_by_text("稍后整理，保留原文", exact=True)).to_be_visible()
            page.get_by_role("button", name="编辑备注 研究素材视频", exact=True).click()
            page.get_by_label("备注 研究素材视频", exact=True).fill("取消后不应保留")
            page.get_by_role("button", name="取消编辑", exact=True).click()
            expect(page.get_by_text("稍后整理，保留原文", exact=True)).to_be_visible()
            assert store.get_item("bilibili", "new-video")["note"] == "稍后整理，保留原文"
            if args.screenshots:
                page.screenshot(path=str(ROOT / "build/result-bookmarks-reading.png"), full_page=True)
            for width in (390, 1024, 1440):
                page.set_viewport_size({"width": width, "height": 900})
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), f"Overflow at {width}px"
            # Saving failures are API failures now, not localStorage quota errors.
            serving_job = True
            page.goto(origin + "/#/search")
            fail_write = True
            page.get_by_role("button", name="收藏 独立文章", exact=True).click()
            expect(page.get_by_text("测试收藏写入失败", exact=True)).to_be_visible()
            expect(page.get_by_role("button", name="收藏 独立文章", exact=True)).to_have_attribute("aria-pressed", "false")
            assert store.get_item("zhihu", "article") is None
            assert not errors, errors
            assert not api_writes, api_writes
            browser.close()
    finally:
        if client is not None:
            client.close()
        server.shutdown()
        server.server_close()
    print(json.dumps({"result": "passed", "checks": ["group filters", "highlight", "per-source bookmarks",
        "SQLite bookmark and note reload", "export disclosure", "selected CSV/Markdown export", "clipboard",
        "narrow layout", "API write failure", "no platform traffic", "no page errors"]}))


if __name__ == "__main__":
    raise SystemExit(main())
