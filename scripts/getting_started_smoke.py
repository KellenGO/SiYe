"""Check onboarding and diagnostic feedback against isolated, simulated services.

Build webui first. No real platform requests or user profiles are used.
"""
from __future__ import annotations

import argparse
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
from api.services.library_store import LibraryStore, get_library_store


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def do_GET(self):
        try:
            super().do_GET()
        except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
            pass  # Navigation can cancel an in-flight static asset request.


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--channel", default="msedge")
    args = parser.parse_args()
    dist = ROOT / "webui" / "dist"
    if not (dist / "index.html").is_file():
        raise SystemExit("Build webui first")
    output = ROOT / "build" / "getting-started-review"
    output.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(QuietHandler, directory=str(dist)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    origin = f"http://127.0.0.1:{server.server_port}"
    current = None
    offline = False
    mutations = []
    errors = []
    date = "2026-09-15T12:00:00Z"
    result = dict(platform="xhs", content_id="tutorial", title="剪辑入门 PRIVATE_RESULT", content_type="note",
                  url="https://www.xiaohongshu.com/explore/tutorial", author="PRIVATE_AUTHOR", snippet="学习剪辑的练习步骤",
                  published_at=date, cover_url=None, metrics={"liked_count": 12}, rank=1)
    account = dict(platform="xhs", profile_exists=True, status="connected", verified=True,
                   display_name="PRIVATE_ACCOUNT", last_verified_at=date, safe_error_code=None,
                   safe_message=None, browser_backend="msedge")
    health = dict(status="ok", environment_status="ok", backend_available=True,
                  version="0.2.2", api_version="0.2.2", web_version="0.2.2", version_match=True,
                  browser_available=True, browser_backend="msedge", redis_required=False, redis_available=None)

    try:
        with TemporaryDirectory(prefix="guide-", dir=output) as temp, sync_playwright() as pw:
            store = LibraryStore(Path(temp) / "library.db")
            app = FastAPI()
            app.include_router(library_router)
            app.dependency_overrides[get_library_store] = lambda: store
            with TestClient(app) as client:
                def route_request(route):
                    nonlocal current
                    request = route.request
                    path = urlparse(request.url).path
                    if not request.url.startswith(origin + "/"):
                        route.abort()
                        return
                    if not path.startswith("/api/"):
                        route.continue_()
                        return
                    if offline:
                        route.abort()
                        return
                    if request.method != "GET":
                        mutations.append((request.method, path))
                    if path.startswith("/api/library/"):
                        response = client.request(request.method, path, content=request.post_data,
                                                  headers={"content-type": "application/json"})
                        route.fulfill(status=response.status_code, body=response.content, content_type="application/json")
                        return
                    data, status = {}, 200
                    if path == "/api/health":
                        data = health
                    elif path == "/api/search/accounts":
                        data = {"accounts": [account]}
                    elif path == "/api/search/favorites/jobs/latest":
                        data, status = {"detail": "no snapshot"}, 404
                    elif path == "/api/search/jobs" and request.method == "POST":
                        current = dict(job_id="guide-search", overall="completed", keyword="PRIVATE_QUERY", created_at=date,
                            completed_at=date, total_ms=1200, hydration_status="completed", results=[result],
                            platforms={"xhs": dict(status="succeeded", result_count=1, error_summary=None,
                                timings=dict(first_result_ms=400, total_ms=1200, spawn_ms=5), fetched_at=date)})
                        data, status = current, 201
                    elif path.startswith("/api/search/jobs/"):
                        data = current
                    route.fulfill(status=status, json=data)

                browser = pw.chromium.launch(**({} if args.channel == "chromium" else {"channel": args.channel}))
                context = browser.new_context(viewport={"width": 1440, "height": 1050}, locale="zh-CN",
                                              permissions=["clipboard-read", "clipboard-write"])
                context.add_init_script(f"if (location.origin === {json.dumps(origin)}) localStorage.setItem('mediacrawler_language', 'zh-CN')")
                context.route("**/*", route_request)
                context.on("page", lambda page: page.on("pageerror", lambda error: errors.append(str(error))))
                page = context.new_page()
                page.goto(origin)
                guide = page.get_by_role("region", name="新手引导")
                expect(guide).to_have_count(0)
                confirm = json.loads((ROOT / "webui/src/i18n/locales/zh-CN/license.json").read_text(encoding="utf-8"))["confirm"]
                page.get_by_role("button", name=confirm, exact=True).click()
                expect(guide).to_be_visible()
                page.screenshot(path=output / "welcome.png", full_page=True)
                guide.get_by_role("button", name="这次跳过", exact=True).click()
                page.reload()
                expect(guide).to_have_count(0)
                page.close()

                page = context.new_page()
                page.goto(origin)
                guide = page.get_by_role("region", name="新手引导")
                expect(guide).to_be_visible()
                guide.get_by_role("checkbox", name="以后不再自动提示").check()
                guide.get_by_role("button", name="关闭引导", exact=True).click()
                page.close()
                page = context.new_page()
                page.goto(origin + "/#/help")
                guide = page.get_by_role("region", name="新手引导")
                expect(guide).to_have_count(0)
                page.get_by_role("button", name="重新开始新手引导").click()
                expect(page).to_have_url(origin + "/#/settings/accounts")
                expect(guide.get_by_role("heading", name="连接平台", exact=True)).to_be_visible()
                guide.get_by_role("button", name="下一步", exact=True).click()
                expect(page).to_have_url(origin + "/#/search")
                expect(guide.get_by_text("带 ✓ 的平台会参与搜索", exact=False)).to_be_visible()
                search = page.locator("form input[type='text']")
                xhs_button = page.get_by_role("button", name="小红书", exact=True)
                douyin_button = page.get_by_role("button", name="抖音", exact=True)
                bilibili_button = page.get_by_role("button", name="B站", exact=True)
                zhihu_button = page.get_by_role("button", name="知乎", exact=True)
                for button in (douyin_button, bilibili_button, zhihu_button):
                    button.click()
                    expect(button).to_have_attribute("aria-pressed", "false")
                expect(xhs_button).to_have_attribute("aria-pressed", "true")

                before_search = len([item for item in mutations if item[1] == "/api/search/jobs"])
                search.focus()
                page.get_by_role("button", name="效率工作流", exact=True).click()
                expect(search).to_have_value("效率工作流")
                expect(search).to_be_focused()
                expect(page.get_by_role("button", name="效率工作流", exact=True)).to_have_count(0)
                assert len([item for item in mutations if item[1] == "/api/search/jobs"]) == before_search
                expect(xhs_button).to_have_attribute("aria-pressed", "true")
                for button in (douyin_button, bilibili_button, zhihu_button):
                    expect(button).to_have_attribute("aria-pressed", "false")

                search.fill("剪辑入门")
                assert before_search == 0, "Tutorial navigation and recommendation pick must not start a search"
                search.press("Enter")
                expect(page.get_by_role("button", name="收藏 剪辑入门 PRIVATE_RESULT", exact=True)).to_be_visible()

                douyin_button.click()
                xhs_button.click()
                expect(douyin_button).to_have_attribute("aria-pressed", "true")
                expect(xhs_button).to_have_attribute("aria-pressed", "false")
                search.fill("临时关键词")
                search.focus()
                history_pick = page.get_by_title(re.compile("填入「剪辑入门」"))
                expect(history_pick).to_be_visible()
                expect(page.get_by_text("上次：小", exact=False)).to_be_visible()
                history_pick.click()
                expect(search).to_have_value("剪辑入门")
                expect(search).to_be_focused()
                assert len([item for item in mutations if item[1] == "/api/search/jobs"]) == 1
                expect(douyin_button).to_have_attribute("aria-pressed", "true")
                expect(xhs_button).to_have_attribute("aria-pressed", "false")
                assert json.loads(page.evaluate("localStorage.getItem('aggregate_search_platform_pref')")) == ["douyin"]

                page.get_by_role("button", name="收藏 剪辑入门 PRIVATE_RESULT", exact=True).click()
                expect(page.get_by_role("button", name="移出收藏 剪辑入门 PRIVATE_RESULT", exact=True)).to_be_visible()
                guide.get_by_role("button", name="下一步", exact=True).click()
                expect(page).to_have_url(origin + "/#/favorites/local")
                expect(page.get_by_role("button", name="移出收藏 剪辑入门 PRIVATE_RESULT", exact=True)).to_be_visible()
                page.reload()
                expect(guide.get_by_role("heading", name="收藏与整理", exact=True)).to_be_visible()
                page.set_viewport_size({"width": 390, "height": 844})
                page.evaluate("localStorage.setItem('mediacrawler_theme', 'dark')")
                page.reload()
                expect(guide.get_by_role("heading", name="收藏与整理", exact=True)).to_be_visible()
                expect(page.locator("body")).to_have_css("background-color", "rgb(16, 18, 24)")
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), "Mobile overflow"
                page.screenshot(path=output / "mobile-tutorial.png", full_page=True)
                guide.get_by_role("button", name="下一步", exact=True).click()
                expect(page).to_have_url(origin + "/#/help")
                guide.get_by_role("button", name="完成教程", exact=True).click()
                expect(guide).to_have_count(0)
                page.reload()
                expect(guide).to_have_count(0)

                page.set_viewport_size({"width": 1440, "height": 1050})
                before_diagnostics = len(mutations)
                page.get_by_role("button", name="复制诊断信息", exact=True).click()
                report = page.get_by_label("诊断报告预览")
                expect(report).to_be_visible()
                assert "PRIVATE" not in report.input_value()
                assert json.loads(report.input_value())["latest_search"]["platforms"]["xhs"]["total_ms"] == 1200
                expect(page.get_by_text("诊断信息已复制。", exact=False)).to_be_visible()
                assert json.loads(page.evaluate("navigator.clipboard.readText()")) == json.loads(report.input_value())
                assert len(mutations) == before_diagnostics, "Diagnostics must not mutate local or platform state"
                with page.expect_download() as download:
                    page.get_by_role("button", name="下载诊断文件", exact=True).click()
                downloaded = Path(download.value.path()).read_text(encoding="utf-8")
                assert downloaded == report.input_value()
                page.screenshot(path=output / "diagnostics.png", full_page=True)
                page.evaluate("Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText: () => Promise.reject(new Error('denied')) } })")
                page.get_by_role("button", name="复制诊断信息", exact=True).click()
                expect(page.get_by_text("报告已生成，但浏览器未允许复制。", exact=False)).to_be_visible()
                offline = True
                page.get_by_role("button", name="复制诊断信息", exact=True).click()
                expect(report).to_have_value(re.compile('"health": \\{\\s+"state": "unavailable"'))
                assert json.loads(report.input_value())["ui_version"] == "0.2.2"
                assert len([item for item in mutations if item[1] == "/api/search/jobs"]) == 1
                assert not errors, errors
                context.close()
                browser.close()
            print("PASS: consent, skip, never again, restart, keyword picks preserve platforms, tutorial with search/save, reload, mobile, diagnostics copy/download/offline; no real platform requests")
    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    main()
