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
from base.app_version import APP_VERSION


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
                  version=APP_VERSION, api_version=APP_VERSION, web_version=APP_VERSION, version_match=True,
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
                    elif path == "/api/trending":
                        data = {"platforms": {
                            "xhs": {"status": "unavailable", "words": []},
                            "douyin": {"status": "ok", "words": [{"rank": 1, "word": "抖音测试热词"}]},
                            "bilibili": {"status": "ok", "words": [{"rank": 1, "word": "B站测试热词"}]},
                            "zhihu": {"status": "ok", "words": [{"rank": 1, "word": "知乎测试热词"}]},
                        }}
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

                # 右下角退出入口：教程不锁交互，用户滚到别处也得关得掉，
                # 而且它和「这次跳过」一样只影响当前标签页。
                page = context.new_page()
                page.goto(origin)
                guide = page.get_by_role("region", name="新手引导")
                expect(guide).to_be_visible()
                exit_button = page.get_by_role("button", name="退出教程", exact=True)
                expect(exit_button).to_be_visible()
                # 撑高页面后往下滚：卡片被顶出视口，退出入口必须还在原位。
                page.evaluate("document.body.style.paddingBottom = '2000px'")
                before_exit = exit_button.bounding_box()
                page.mouse.wheel(0, 600)
                page.wait_for_timeout(300)
                assert page.evaluate("scrollY") > 0, "页面向下滚动失败，退出入口这条没验到"
                card_box = guide.bounding_box()
                assert not card_box or card_box["y"] + card_box["height"] < 0, \
                    f"教程卡片还在视口里，这条没验到：{card_box}"
                after_exit = exit_button.bounding_box()
                assert abs(after_exit["y"] - before_exit["y"]) < 2, (before_exit, after_exit)
                exit_button.click()
                expect(guide).to_have_count(0)
                expect(page.locator(".guide-exit")).to_have_count(0)
                expect(page.locator(".guide-spotlight")).to_have_count(0)
                # 只影响当前标签页：刷新仍是关着的（session），但换个标签页还会出现。
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
                expect(guide.get_by_text("上手教程 · 1 / 7", exact=False)).to_be_visible()
                guide.get_by_role("button", name="下一步", exact=True).click()
                expect(page).to_have_url(origin + "/#/")
                expect(guide.get_by_role("heading", name="认识首页", exact=True)).to_be_visible()
                expect(guide.get_by_text("默认一个都不勾", exact=False)).to_be_visible()

                # 高亮框要严丝合缝套住「搜索范围」那一行（比目标外扩 6px），
                # 而且滚轮一动就跟着走 —— 这正是这一步存在的理由。
                expect(page.locator('[data-tour="search-scope"]')).to_be_visible()
                expect(page.locator(".guide-spotlight")).to_have_count(1)
                hint = page.get_by_text("搜索范围：想搜哪几个平台", exact=False)
                expect(hint).to_be_visible()

                def spotlight_offset():
                    """高亮框相对目标元素的偏移，四舍五入到整像素。"""
                    return page.evaluate(
                        """() => {
  const box = document.querySelector(".guide-spotlight").getBoundingClientRect();
  const row = document.querySelector('[data-tour="search-scope"]').getBoundingClientRect();
  return [Math.round(box.top - row.top), Math.round(box.left - row.left),
          Math.round(box.width - row.width), Math.round(box.height - row.height)];
}"""
                    )

                assert spotlight_offset() == [-6, -6, 12, 12], spotlight_offset()
                # 撑高页面，保证滚轮真的能滚 —— 否则「跟随滚动」这条会空过。
                page.evaluate("document.body.style.paddingBottom = '1500px'")
                page.mouse.wheel(0, 220)
                page.wait_for_timeout(300)
                assert page.evaluate("scrollY") > 0, "页面向下滚动失败，跟随滚动这条没验到"
                assert spotlight_offset() == [-6, -6, 12, 12], spotlight_offset()
                # 卡片（在页面顶部）已经滚出视口，提示气泡仍跟着高亮框在眼前。
                expect(hint).to_be_visible()
                # 退出入口是 fixed：卡片滚没了它也得在原处，用户才关得掉。
                exit_box = page.get_by_role("button", name="退出教程", exact=True).bounding_box()
                assert exit_box and 0 <= exit_box["y"] < page.evaluate("innerHeight"), exit_box
                page.evaluate("document.body.style.paddingBottom = ''")
                page.evaluate("window.scrollTo(0, 0)")
                page.wait_for_timeout(300)
                assert spotlight_offset() == [-6, -6, 12, 12], spotlight_offset()

                # 高亮层不锁交互：压暗层是 pointer-events: none，透过它照样点得到勾选框。
                # 哪天有人把它改成拦点击，这里的 click 会直接以 "intercepts pointer events" 失败。
                home_xhs = page.get_by_role("button", name="小红书", exact=True)
                home_xhs.click()
                expect(home_xhs).to_have_attribute("aria-pressed", "true")
                home_xhs.click()
                expect(home_xhs).to_have_attribute("aria-pressed", "false")

                # 复核截图 + 主题/缩放后的贴合：换主题、换视口都要重新测量。
                page.screenshot(path=output / "spotlight-light.png", full_page=True)
                page.evaluate("localStorage.setItem('mediacrawler_theme', 'dark')")
                page.reload()
                page.wait_for_timeout(400)
                assert spotlight_offset() == [-6, -6, 12, 12], spotlight_offset()
                page.screenshot(path=output / "spotlight-dark.png", full_page=True)
                page.set_viewport_size({"width": 390, "height": 844})
                page.wait_for_timeout(400)
                assert spotlight_offset() == [-6, -6, 12, 12], spotlight_offset()
                page.screenshot(path=output / "spotlight-mobile.png", full_page=True)
                page.evaluate("localStorage.setItem('mediacrawler_theme', 'light')")
                page.set_viewport_size({"width": 1440, "height": 1050})
                page.reload()
                page.wait_for_timeout(400)

                guide.get_by_role("button", name="下一步", exact=True).click()
                expect(page).to_have_url(origin + "/#/search")
                expect(guide.get_by_role("heading", name="搜索与结果", exact=True)).to_be_visible()
                search = page.locator("form input[type='text']")
                xhs_button = page.get_by_role("button", name="小红书", exact=True)
                douyin_button = page.get_by_role("button", name="抖音", exact=True)
                bilibili_button = page.get_by_role("button", name="B站", exact=True)
                zhihu_button = page.get_by_role("button", name="知乎", exact=True)
                # 平台按钮默认零勾选：教程不替用户选平台，教程正文也这么承诺。
                for button in (xhs_button, douyin_button, bilibili_button, zhihu_button):
                    expect(button).to_have_attribute("aria-pressed", "false")
                assert json.loads(page.evaluate("localStorage.getItem('aggregate_search_platform_pref')")) == []
                # 这里只勾小红书，作为"只搜一个平台"的样例。
                xhs_button.click()
                expect(xhs_button).to_have_attribute("aria-pressed", "true")
                for button in (douyin_button, bilibili_button, zhihu_button):
                    expect(button).to_have_attribute("aria-pressed", "false")
                assert json.loads(page.evaluate("localStorage.getItem('aggregate_search_platform_pref')")) == ["xhs"]

                # 勾选要按上一次的记忆恢复：刷新后仍是"只勾小红书"，不回退全选。
                page.reload()
                page.wait_for_timeout(300)
                expect(xhs_button).to_have_attribute("aria-pressed", "true")
                for button in (douyin_button, bilibili_button, zhihu_button):
                    expect(button).to_have_attribute("aria-pressed", "false")
                assert json.loads(page.evaluate("localStorage.getItem('aggregate_search_platform_pref')")) == ["xhs"]

                # 取消到零也是一种"上一次的选择"：零勾选必须落地，刷新后不能自己勾回来。
                xhs_button.click()
                expect(xhs_button).to_have_attribute("aria-pressed", "false")
                assert json.loads(page.evaluate("localStorage.getItem('aggregate_search_platform_pref')")) == []
                page.reload()
                page.wait_for_timeout(300)
                for button in (xhs_button, douyin_button, bilibili_button, zhihu_button):
                    expect(button).to_have_attribute("aria-pressed", "false")
                assert json.loads(page.evaluate("localStorage.getItem('aggregate_search_platform_pref')")) == []
                # 复原成"只勾小红书"，后面的历史词与收藏步骤按原样继续。
                xhs_button.click()
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
                # 点击历史词只填词、保留当前勾选：偏好仍是上一步勾选得到的 ["douyin"]，
                # 不随历史词回到其原平台 ["xhs"]。这条行为在"默认零勾选"改动下必须保持。
                assert json.loads(page.evaluate("localStorage.getItem('aggregate_search_platform_pref')")) == ["douyin"]

                page.get_by_role("button", name="收藏 剪辑入门 PRIVATE_RESULT", exact=True).click()
                expect(page.get_by_role("button", name="取消收藏 剪辑入门 PRIVATE_RESULT", exact=True)).to_be_visible()
                guide.get_by_role("button", name="下一步", exact=True).click()
                expect(page).to_have_url(origin + "/#/favorites/local")
                expect(guide.get_by_role("heading", name="收藏与整理", exact=True)).to_be_visible()
                expect(page.get_by_role("button", name="取消收藏 剪辑入门 PRIVATE_RESULT", exact=True)).to_be_visible()
                # 「回搜索结果」把教程一并带回搜索那一步，不只是改地址。
                guide.get_by_role("button", name="回搜索结果", exact=True).click()
                expect(page).to_have_url(origin + "/#/search")
                expect(guide.get_by_role("heading", name="搜索与结果", exact=True)).to_be_visible()
                guide.get_by_role("button", name="下一步", exact=True).click()
                expect(page).to_have_url(origin + "/#/favorites/local")
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
                expect(page).to_have_url(origin + "/#/history")
                expect(guide.get_by_role("heading", name="观看历史", exact=True)).to_be_visible()
                guide.get_by_role("button", name="下一步", exact=True).click()
                expect(page).to_have_url(origin + "/#/settings/appearance")
                expect(guide.get_by_role("heading", name="外观与个性化", exact=True)).to_be_visible()
                guide.get_by_role("button", name="下一步", exact=True).click()
                expect(page).to_have_url(origin + "/#/help")
                expect(guide.get_by_role("heading", name="帮助与反馈", exact=True)).to_be_visible()
                expect(guide.get_by_text("上手教程 · 7 / 7", exact=False)).to_be_visible()
                guide.get_by_role("button", name="完成教程", exact=True).click()
                expect(guide).to_have_count(0)
                page.reload()
                expect(guide).to_have_count(0)

                extension = page.locator(".help-extension-details")
                expect(extension).not_to_have_attribute("open", "")
                expect(page.get_by_text("不安装扩展也可以用四野内置扫码登录。", exact=True)).to_be_visible()
                expect(extension.get_by_text("开发者模式")).not_to_be_visible()
                page.screenshot(path=output / "help-extension-collapsed-mobile.png", full_page=True)
                summary = extension.locator("summary")
                summary.focus()
                summary.press("Enter")
                expect(extension).to_have_attribute("open", "")
                expect(extension.get_by_text("开发者模式")).to_be_visible()
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), "Help mobile overflow"
                page.screenshot(path=output / "help-extension-expanded-mobile.png", full_page=True)
                summary.press("Enter")
                expect(extension).not_to_have_attribute("open", "")

                page.goto(origin + "/#/")
                page.get_by_role("button", name="返回首页", exact=True).click()
                xhs_trending = page.get_by_role("tab", name="小红书 暂不可用")
                expect(xhs_trending).to_be_visible()
                expect(page.get_by_role("tab", name="抖音")).to_be_visible()
                expect(page.get_by_role("tab", name="B站")).to_be_visible()
                expect(page.get_by_role("tab", name="知乎")).to_be_visible()
                xhs_trending.focus()
                xhs_trending.press("Enter")
                expect(xhs_trending).to_have_attribute("aria-selected", "true")
                expect(page.get_by_role("tabpanel").get_by_text("目前暂未接入小红书热搜，其他平台仍可使用。", exact=True)).to_be_visible()
                page.screenshot(path=output / "trending-unavailable-mobile.png", full_page=True)
                page.get_by_role("tab", name="抖音").click()
                expect(page.get_by_text("抖音测试热词", exact=True)).to_be_visible()
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), "Trending mobile overflow"
                page.goto(origin + "/#/help")

                page.set_viewport_size({"width": 1440, "height": 1050})
                page.wait_for_timeout(500)
                page.screenshot(path=output / "help-v1.png", full_page=True)
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
                assert json.loads(report.input_value())["ui_version"] == APP_VERSION
                assert len([item for item in mutations if item[1] == "/api/search/jobs"]) == 1
                assert not errors, errors
                context.close()
                browser.close()
            print("PASS: consent, skip, never again, restart, zero platform pre-selection, "
                  "platform picks remembered across reload, zero picked stays zero, "
                  "keyword picks preserve platforms, seven tutorial steps (accounts/home/search/save/history/"
                  "appearance/help), spotlight tracks the search-scope row while scrolling without "
                  "blocking clicks, floating exit stays put, back-to-results, reload, mobile, "
                  "optional extension details, trending unavailable badge, diagnostics copy/download/offline; "
                  "no real platform requests")
    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    main()
