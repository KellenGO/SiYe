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
from aggregate_search.adapters.douyin import DouyinAdapter
from aggregate_search.adapters.xhs import XhsAdapter
from aggregate_search.adapters.zhihu import ZhihuAdapter
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
    article["metrics"] = {"view_count": 12000, "like_count": 12}
    article["metrics_approximate"] = ["view_count"]
    video["duration_seconds"] = 3723
    group = {**note, "grouped_sources": [note, video]}
    job = dict(job_id="library-smoke", overall="completed", keyword="研究素材",
               created_at=fetched, completed_at=fetched, total_ms=100,
               hydration_status="completed", results=[group, article],
               platforms={p: dict(status="succeeded", result_count=1, error_summary=None,
                                  fetched_at=fetched, cache_hit=False)
                          for p in ("xhs", "bilibili", "zhihu")})
    serving_job = True
    api_writes = []
    history_views = []
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
        elif url.path == "/api/history/views" and route.request.method == "POST":
            history_views.append(route.request.post_data_json["result"])
            route.fulfill(json={"ok": True})
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
                                          permissions=["clipboard-read", "clipboard-write"], has_touch=True)
            context.add_init_script("localStorage.setItem('mediacrawler_license_accepted', 'true'); localStorage.setItem('siye_onboarding_preference_v1', 'completed')")
            context.route("**/*", route_request)
            page = context.new_page()
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(origin + "/#/search")
            expect(page.get_by_role("button", name="选择收藏平台 研究素材图文", exact=True)).to_be_visible()
            expect(page.locator(".local-content-card")).to_have_count(2)
            expect(page.locator(".local-content-primary-metric")).to_have_text(["12", "≈12k"])
            expect(page.locator(".local-content-duration")).to_have_count(0)
            expect(page.locator(".local-content-primary-metric svg.lucide-eye")).to_have_count(1)
            expect(page.locator(".local-content-primary-metric svg.lucide-thumbs-up")).to_have_count(1)
            expect(page.locator(".local-content-primary-metric").last).to_have_attribute("aria-label", "阅读 约 12,000")
            if args.screenshots:
                page.screenshot(path=str(ROOT / "build/content-grid-search.png"), full_page=True)
            page.set_viewport_size({"width": 390, "height": 844})
            if args.screenshots:
                page.screenshot(path=str(ROOT / "build/content-grid-search-mobile.png"), full_page=True)
            page.get_by_role("button", name="选择收藏平台 研究素材图文", exact=True).tap()
            mobile_menu = page.get_by_role("group", name="选择收藏来源", exact=True)
            bounds = mobile_menu.bounding_box()
            assert bounds and bounds["x"] >= 0 and bounds["x"] + bounds["width"] <= 390
            page.keyboard.press("Escape")
            # 网格收藏直接打开居中归属面板，不增高卡片；关闭后恢复焦点。
            before = page.locator(".local-content-grid").bounding_box()
            page.get_by_role("button", name="收藏 独立文章", exact=True).tap()
            membership = page.get_by_role("dialog", name="编辑收藏夹归属 独立文章", exact=True)
            expect(membership).to_be_visible()
            expect(page.locator(".local-content-grid .bookmark-note")).to_have_count(0)
            after = page.locator(".local-content-grid").bounding_box()
            assert before and after and abs(before["height"] - after["height"]) < 1
            box = membership.bounding_box()
            assert box and abs(box["x"] + box["width"] / 2 - 195) < 2
            if args.screenshots:
                page.screenshot(path=str(ROOT / "build/grid-save-membership-mobile.png"))
            membership.get_by_role("checkbox", name="默认收藏夹", exact=True).click()
            expect(membership.get_by_role("checkbox", name="默认收藏夹", exact=True)).not_to_be_checked()
            membership.get_by_role("button", name="完成", exact=True).focus()
            page.keyboard.press("Shift+Tab")
            expect(membership.get_by_role("checkbox", name="默认收藏夹", exact=True)).to_be_focused()
            page.keyboard.press("Escape")
            expect(membership).to_have_count(0)
            expect(page.get_by_role("button", name="取消收藏 独立文章", exact=True)).to_be_focused()
            page.get_by_role("button", name="取消收藏 独立文章", exact=True).click()
            page.locator(".confirm-card").get_by_role("button", name="取消收藏", exact=True).click()
            expect(page.get_by_role("button", name="收藏 独立文章", exact=True)).to_be_visible()
            page.get_by_role("button", name="选择收藏平台 研究素材图文", exact=True).click()
            page.get_by_role("button", name="全部加入收藏 研究素材图文", exact=True).click()
            batch_membership = page.get_by_role("dialog", name="编辑收藏夹归属 研究素材图文", exact=True)
            expect(batch_membership).to_be_visible()
            batch_membership.get_by_role("checkbox", name="默认收藏夹", exact=True).click()
            expect(batch_membership.get_by_role("checkbox", name="默认收藏夹", exact=True)).not_to_be_checked()
            assert not store.get_item("xhs", "old-note")["in_default"]
            assert not store.get_item("bilibili", "new-video")["in_default"]
            page.set_viewport_size({"width": 1280, "height": 900})
            if args.screenshots:
                page.screenshot(path=str(ROOT / "build/grid-save-membership-desktop.png"))
            page.locator(".membership-overlay").click(position={"x": 4, "y": 4})
            expect(batch_membership).to_have_count(0)
            page.get_by_role("button", name="选择收藏平台 研究素材图文", exact=True).click()
            page.get_by_role("group", name="选择收藏来源", exact=True).get_by_role("button", name="取消收藏 研究素材图文", exact=True).last.click()
            page.locator(".confirm-card").get_by_role("button", name="取消收藏", exact=True).click()
            page.keyboard.press("Escape")
            page.set_viewport_size({"width": 390, "height": 844})
            page.get_by_role("button", name="列表", exact=True).tap()
            expect(page.locator(".result-row")).to_have_count(2)
            expect(page.locator(".result-cover .local-content-cover")).to_have_count(2)
            expect(page.locator(".result-platform").first).to_have_text("2 个平台版本")
            expect(page.locator(".result-platform").first.locator(".pd")).to_have_count(2)
            expect(page.locator(".result-metric").filter(has_text="≈12k")).to_have_attribute("aria-label", "阅读 约 12,000")
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            if args.screenshots:
                page.screenshot(path=str(ROOT / "build/content-list-search-mobile.png"), full_page=True)
            page.get_by_role("button", name="查看内容信息：研究素材图文", exact=True).tap()
            mobile_drawer = page.get_by_role("dialog", name="内容信息", exact=True)
            expect(mobile_drawer.locator(".local-content-detail")).to_have_count(2)
            expect(mobile_drawer.get_by_role("button", name="编辑归属")).to_have_count(0)
            if args.screenshots:
                page.screenshot(path=str(ROOT / "build/content-detail-search-mobile.png"))
            mobile_drawer.get_by_role("button", name="关闭内容详情").tap()
            page.set_viewport_size({"width": 1280, "height": 900})
            page.get_by_role("button", name="展开完整内容", exact=True).first.click()
            expect(page.get_by_text("2 个平台的内容版本", exact=True)).to_be_visible()
            page.get_by_role("button", name="网格", exact=True).click()
            page.get_by_role("button", name="列表", exact=True).click()
            expect(page.get_by_text("2 个平台的内容版本", exact=True)).to_be_visible()
            expect(page.get_by_role("button", name="收起完整内容", exact=True).first).to_have_attribute("aria-expanded", "true")
            with page.expect_popup() as original:
                page.locator(".result-expanded-sources a").filter(has_text="研究素材视频").click()
            original.value.close()
            assert history_views[-1]["content_id"] == "new-video"
            with page.expect_popup() as original:
                page.locator("a.result-title").filter(has_text="研究素材图文").click()
            original.value.close()
            assert history_views[-1]["content_id"] == "old-note"
            page.get_by_role("button", name="收起完整内容", exact=True).first.click()
            if args.screenshots:
                page.screenshot(path=str(ROOT / "build/content-list-search-desktop.png"), full_page=True)
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
            assert store.get_item("bilibili", "new-video")["result"]["duration_seconds"] == 3723
            page.keyboard.press("Escape")
            page.get_by_role("button", name="查看内容信息：研究素材图文", exact=True).click()
            drawer = page.get_by_role("dialog", name="内容信息", exact=True)
            expect(drawer.locator(".local-content-detail")).to_have_count(2)
            expect(drawer.locator(".local-content-duration")).to_have_text("1:02:03")
            expect(drawer.get_by_role("heading", name="研究素材图文", exact=True)).to_be_visible()
            assert store.get_item("xhs", "old-note") is None
            expect(drawer).to_contain_text("研究素材摘要")
            expect(drawer).to_contain_text("12")
            if args.screenshots:
                page.screenshot(path=str(ROOT / "build/result-unsaved-detail.png"))
            page.keyboard.press("Escape")
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
            page.get_by_role("combobox", name="排序方式").select_option("latest")
            page.get_by_role("button", name="网格", exact=True).click()
            expect(page.get_by_role("checkbox", name="选择 研究素材视频", exact=True)).to_be_checked()
            expect(page.get_by_label("结果内关键词")).to_have_value("视频")
            expect(page.get_by_role("combobox", name="排序方式")).to_have_value("latest")
            expect(page.locator(".local-content-card")).to_have_count(1)
            page.get_by_role("button", name="列表", exact=True).click()
            expect(page.locator(".result-row")).to_have_count(1)
            expect(page.get_by_role("checkbox", name="选择 研究素材视频", exact=True)).to_be_checked()
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
            page.locator('[data-local-folder="all"]').click()
            expect(page.get_by_role("button", name="网格", exact=True)).to_have_attribute("aria-pressed", "true")
            page.get_by_role("button", name="查看内容信息：研究素材视频", exact=True).click()
            expect(page.get_by_role("dialog", name="内容信息").get_by_role("button", name="取消收藏 研究素材视频", exact=True)).to_be_visible()
            page.get_by_role("button", name="添加备注 研究素材视频", exact=True).click()
            page.get_by_label("备注 研究素材视频", exact=True).fill("稍后整理，保留原文")
            page.get_by_role("button", name="保存备注", exact=True).click()
            expect(page.get_by_role("dialog", name="内容信息").get_by_text("稍后整理，保留原文", exact=True)).to_be_visible()
            serving_job = False
            page.reload()
            page.locator('[data-local-folder="all"]').click()
            expect(page.get_by_role("button", name="网格", exact=True)).to_have_attribute("aria-pressed", "true")
            page.get_by_role("button", name="查看内容信息：研究素材视频", exact=True).click()
            expect(page.get_by_role("dialog", name="内容信息").get_by_text("稍后整理，保留原文", exact=True)).to_be_visible()
            page.get_by_role("button", name="编辑备注 研究素材视频", exact=True).click()
            page.get_by_label("备注 研究素材视频", exact=True).fill("取消后不应保留")
            page.get_by_role("button", name="取消编辑", exact=True).click()
            expect(page.get_by_role("dialog", name="内容信息").get_by_text("稍后整理，保留原文", exact=True)).to_be_visible()
            assert store.get_item("bilibili", "new-video")["note"] == "稍后整理，保留原文"
            if args.screenshots:
                page.screenshot(path=str(ROOT / "build/result-bookmarks-reading.png"), full_page=True)
            for width in (390, 1024, 1440):
                page.set_viewport_size({"width": width, "height": 900})
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), f"Overflow at {width}px"
            page.get_by_role("button", name="关闭内容详情").click()
            # Saving failures are API failures now, not localStorage quota errors.
            serving_job = True
            page.goto(origin + "/#/search")
            fail_write = True
            page.get_by_role("button", name="收藏 独立文章", exact=True).click()
            expect(page.get_by_text("测试收藏写入失败", exact=True)).to_be_visible()
            expect(page.get_by_role("button", name="收藏 独立文章", exact=True)).to_have_attribute("aria-pressed", "false")
            assert store.get_item("zhihu", "article") is None
            fail_write = False
            job["hydration_status"] = "running"
            page.reload()
            page.get_by_role("tab", name="B站").click()
            page.get_by_role("button", name="网格", exact=True).click()
            page.get_by_role("button", name="列表", exact=True).click()
            expect(page.get_by_role("tab", name="B站")).to_have_attribute("aria-selected", "true")
            page.get_by_role("button", name="查看内容信息：研究素材视频", exact=True).click()
            live_drawer = page.get_by_role("dialog", name="内容信息", exact=True)
            expect(live_drawer.locator(".local-content-metrics dd")).to_have_text("12")
            video["metrics"] = {"like_count": 42}
            job["hydration_status"] = "completed"
            expect(live_drawer.locator(".local-content-metrics dd")).to_have_text("42", timeout=10000)
            assert store.get_item("bilibili", "new-video")["result"]["metrics"]["like_count"] == 12
            page.keyboard.press("Escape")
            page.get_by_role("button", name="网格", exact=True).click()
            blank = source("douyin", "untitled", "", "short_video", "javascript:alert(1)", 0)
            blank["cover_url"] = "https://broken.siye.invalid/cover.jpg"
            blank["metrics"] = {}
            job["results"].append(blank)
            page.reload()
            blank_card = page.locator(".local-content-card").filter(has_text="无标题内容")
            expect(blank_card).to_contain_text("封面暂不可用")
            expect(blank_card.locator(".local-content-primary-metric")).to_have_count(0)
            expect(blank_card.get_by_role("link")).to_have_count(0)
            blank_card.get_by_role("button", name="查看内容信息：无标题内容", exact=True).click()
            blank_drawer = page.get_by_role("dialog", name="内容信息", exact=True)
            expect(blank_drawer.get_by_role("heading", name="无标题内容", exact=True)).to_be_visible()
            expect(blank_drawer).to_contain_text("暂无互动数据")
            expect(blank_drawer.get_by_role("link")).to_have_count(0)
            page.keyboard.press("Escape")
            blank["duration_seconds"] = 94
            blank["metrics"] = {"view_count": 0, "like_count": 99}
            page.reload()
            expect(blank_card.locator(".local-content-primary-metric")).to_have_text("0")
            expect(blank_card.locator(".local-content-duration")).to_have_text("01:34")
            page.set_viewport_size({"width": 390, "height": 844})
            if args.screenshots:
                page.screenshot(path=str(ROOT / "build/content-duration-mobile.png"), full_page=True)
            rich = source("bilibili", "rich", "长标题与多指标内容用于检查窄屏阅读和操作区域" * 12,
                          "video", "https://www.bilibili.com/video/rich", 1)
            rich.update(cover_url='data:image/svg+xml,<svg xmlns="http://www.w3.org/2000/svg" width="320" height="180"><rect width="320" height="180" fill="teal"/><circle cx="200" cy="80" r="50" fill="gold"/></svg>',
                        metrics={"view_count": 1200000, "like_count": 15400, "coin_count": 0,
                                 "comment_count": 1000000000, "collect_count": 80, "share_count": 5},
                        metrics_approximate=["view_count"], duration_seconds=94)
            job["results"].append(rich)
            duration_samples = [
                (DouyinAdapter(), {"aweme_id": "duration-dy", "desc": "抖音时长", "video": {"duration": 94500}}),
                (XhsAdapter(), {"id": "duration-xhs", "note_card": {"type": "video", "display_title": "小红书时长", "video": {"capa": {"duration": 94}}}}),
                (ZhihuAdapter(), {"id": "12345", "type": "zvideo", "title": "知乎时长", "video": {"duration": 94.5}}),
            ]
            for adapter, raw in duration_samples:
                adapted = adapter.adapt([raw])[0].model_dump(mode="json")
                adapted["cover_url"] = rich["cover_url"]
                job["results"].append(adapted)
            page.reload()
            for dark in (False, True):
                page.evaluate("dark => document.documentElement.classList.toggle('dark', dark)", dark)
                for width in (1280, 390, 320):
                    page.set_viewport_size({"width": width, "height": 900})
                    for view in ("列表", "网格"):
                        page.get_by_role("button", name=view, exact=True).click()
                        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), (dark, width, view)
                        for title in ("抖音时长", "小红书时长", "知乎时长"):
                            card = page.locator(".result-row" if view == "列表" else ".local-content-card").filter(has_text=title)
                            expect(card.locator(".local-content-duration")).to_have_text("01:34")
                        for duration in page.locator(".local-content-duration").all():
                            geometry = duration.evaluate("""el => {
                                const cover = el.closest('.local-content-cover').getBoundingClientRect();
                                const time = el.getBoundingClientRect();
                                const labels = el.previousElementSibling.getBoundingClientRect();
                                return {right: cover.right - time.right, bottom: cover.bottom - time.bottom,
                                    overlap: labels.right > time.left, left: time.left - cover.left};
                            }""")
                            assert 0 <= geometry["right"] <= 7 and 0 <= geometry["bottom"] <= 5, geometry
                            assert not geometry["overlap"] and geometry["left"] >= 0, geometry
                        for badges in page.locator(".local-content-badges").all():
                            assert badges.evaluate("el => parseFloat(getComputedStyle(el).paddingTop) <= 6")
                        if view == "列表":
                            rich_row = page.locator(".result-row").filter(has_text=rich["title"])
                            expect(rich_row.locator(".result-metric")).to_have_text(["≈1.2M", "15.4k", "0", "1B", "80", "5"])
                            expect(rich_row.locator(".result-cover img")).to_be_visible()
                            expect(rich_row.locator(".local-content-duration")).to_have_text("01:34")
                            blank_row = page.locator(".result-row").filter(has_text="无标题内容")
                            expect(blank_row).to_contain_text("封面暂不可用")
                            expect(blank_row.locator(".result-metric").first).to_have_attribute("aria-label", "播放 0")
                            rich_row.get_by_role("button", name="展开完整内容", exact=True).click()
                            assert rich_row.locator("h2").evaluate("el => el.scrollHeight <= el.clientHeight + 1")
                            rich_row.get_by_role("button", name="收起完整内容", exact=True).click()
                            buttons = rich_row.locator(".row-actions > button, .row-actions > .relative > button")
                            minimum = 44 if width <= 700 or page.evaluate("matchMedia('(pointer: coarse)').matches") else 32
                            for box in buttons.evaluate_all("els => els.map(el => { const r = el.getBoundingClientRect(); return {x:r.x, right:r.right, width:r.width, height:r.height}; })"):
                                assert box["width"] >= minimum and box["height"] >= minimum
                                assert box["x"] >= 0 and box["right"] <= width
                        if args.screenshots:
                            page.screenshot(path=str(ROOT / "build" / f"continuity-{view}-{width}-{'dark' if dark else 'light'}.png"), full_page=True)
            assert not errors, errors
            assert not api_writes, api_writes
            browser.close()
    finally:
        if client is not None:
            client.close()
        server.shutdown()
        server.server_close()
    print(json.dumps({"result": "passed", "checks": ["list/grid state preservation", "original list expansion and history", "independent page preferences", "grid and touch details", "live detail metrics", "missing title and broken cover", "group filters", "highlight", "per-source bookmarks",
        "SQLite bookmark and note reload", "export disclosure", "selected CSV/Markdown export", "clipboard",
        "narrow layout", "API write failure", "no platform traffic", "no page errors"]}))


if __name__ == "__main__":
    raise SystemExit(main())
