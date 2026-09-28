"""Exercise the built favorites UI against an isolated SQLite library.

No real accounts, user library, or platform requests are accessed.
Run after npm run build with the existing Playwright/Edge installation.
"""

import json
import argparse
import re
import sys
import threading
from io import BytesIO
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fastapi import FastAPI
from fastapi.testclient import TestClient
from playwright.sync_api import expect, sync_playwright
from PIL import Image

from api.routers.library import library_router
from api.services.library_store import LibraryStore, get_library_store


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--web-root", type=Path, default=ROOT / "webui" / "dist")
    args = parser.parse_args()
    with TemporaryDirectory(prefix="siye-review-") as temporary:
        store = LibraryStore(Path(temporary) / "library.db")
        app = FastAPI()
        app.include_router(library_router)
        app.dependency_overrides[get_library_store] = lambda: store
        client = TestClient(app)
        results = [
            {"platform": "xhs", "content_id": "a", "title": "图文收藏测试", "url": "https://www.xiaohongshu.com/explore/a", "cover_url": "https://covers.siye.invalid/a.svg"},
            {"platform": "bilibili", "content_id": "b", "title": "视频收藏测试", "url": "https://www.bilibili.com/video/BVtest", "content_type": "video"},
        ]
        legacy = {"version": 1, "items": [{"result": r, "note": "旧备注", "savedAt": "2026-09-01T00:00:00Z"} for r in results]}
        snapshot = {"job_id": "saved", "overall": "completed", "created_at": "2026-09-01T00:00:00Z", "completed_at": "2026-09-01T00:00:00Z", "results": results, "platforms": {"xhs": {"status": "succeeded", "result_count": 1}, "bilibili": {"status": "succeeded", "result_count": 1}}}
        server = ThreadingHTTPServer(("127.0.0.1", 0), partial(QuietHandler, directory=str(args.web_root.resolve())))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        origin = f"http://127.0.0.1:{server.server_port}"
        errors = []
        writes = []
        failures = {"note": False, "sync": True, "cancel": True, "verified": False}
        login_polls = []

        def route_request(route):
            req = route.request
            url = urlsplit(req.url)
            if url.netloc == "sns-webpic-qc.xhscdn.com":
                route.fulfill(status=403, body="expired")
            elif url.netloc in ("covers.siye.invalid", "sns-img-qc.xhscdn.com"):
                route.fulfill(content_type="image/svg+xml", body='<svg xmlns="http://www.w3.org/2000/svg" width="640" height="360"><rect width="640" height="360" fill="#7c8cff"/><circle cx="488" cy="92" r="130" fill="#37c9bd" opacity=".72"/></svg>')
            elif not req.url.startswith(origin + "/"):
                route.abort()
            elif url.path.startswith("/api/library/"):
                if failures["note"] and req.method == "PATCH" and "/items/" in url.path:
                    route.fulfill(status=503, json={"detail": "测试磁盘写入失败"})
                    return
                if req.method != "GET":
                    writes.append((req.method, url.path))
                response = client.request(req.method, url.path + ("?" + url.query if url.query else ""), content=req.post_data, headers={"content-type": "application/json"})
                route.fulfill(status=response.status_code, body=response.content, content_type="application/json")
            elif url.path == "/api/search/favorites/jobs/latest":
                route.fulfill(json=snapshot)
            elif url.path == "/api/search/favorites/jobs" and req.method == "POST":
                if failures["sync"]:
                    route.fulfill(status=503, json={"detail": "测试同步失败"})
                else:
                    snapshot.update(job_id="active", overall="running", completed_at=None)
                    route.fulfill(status=201, json=snapshot)
            elif url.path == "/api/search/favorites/jobs/active/cancel":
                if failures["cancel"]:
                    route.fulfill(status=503, json={"detail": "测试取消失败"})
                else:
                    snapshot.update(overall="partial", completed_at="2026-09-14T00:00:00Z")
                    snapshot["platforms"]["xhs"].update(status="cancelled", error_summary="同步已取消")
                    route.fulfill(json=snapshot)
            elif url.path == "/api/search/favorites/jobs/active":
                route.fulfill(json=snapshot)
            elif url.path == "/api/health":
                route.fulfill(json={"status": "ok", "environment_status": "ok", "backend_available": True})
            elif url.path == "/api/search/accounts":
                route.fulfill(json={"accounts": [{"platform": "xhs", "status": "connected" if failures["verified"] else "unverified", "verified": failures["verified"],
                    "profile_exists": True, "display_name": None, "last_verified_at": None,
                    "safe_error_code": None, "safe_message": None, "browser_backend": "edge",
                    "diagnostic": {"platform": "xhs", "search_available": False, "search_mode": "unavailable",
                        "account_state": "unverified", "snippet_available": True, "hydration_available": True,
                        "fallback_active": True, "limitation_code": "login_required", "user_message": "历史搜索要求登录",
                        "recommended_action": None, "checked_at": None}}]})
            elif url.path == "/api/search/login" and req.method == "POST":
                route.fulfill(json={"job_id": "scan-test", "platform": "xhs", "status": "running", "message": "测试等待扫码"})
            elif url.path == "/api/search/login/scan-test":
                login_polls.append(1)
                route.fulfill(json={"job_id": "scan-test", "platform": "xhs", "status": "succeeded", "message": "测试扫码已完成"})
            elif url.path == "/api/search/accounts/xhs/verify":
                route.fulfill(json={"success": True, "verified": True, "platform": "xhs"})
            elif url.path.startswith("/api/"):
                route.fulfill(status=404, json={"detail": "测试环境无此数据"})
            else:
                route.continue_()

        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(channel="msedge", headless=True)
                context = browser.new_context(viewport={"width": 1440, "height": 1000})
                context.add_init_script("localStorage.setItem('mediacrawler_license_accepted','true'); localStorage.setItem('siye_onboarding_preference_v1','completed'); localStorage.setItem('aggregate_search_bookmarks_v1'," + json.dumps(json.dumps(legacy)) + ");")
                context.route("**/*", route_request)
                page = context.new_page()
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(origin + "/#/favorites/local")
                page.get_by_role("button", name="迁移到本机收藏库", exact=True).click()
                assert store.stats()["total"] == 2
                expect(page.locator(".library-side")).to_have_count(0)
                expect(page.get_by_role("button", name="列表", exact=True)).to_have_count(0)
                page.locator('[data-local-folder="all"]').click()
                expect(page.locator(".local-content-card")).to_have_count(2)
                page.get_by_role("button", name="稍后再看 图文收藏测试", exact=True).click()
                expect(page.get_by_role("button", name="取消稍后再看 图文收藏测试", exact=True)).to_be_visible()
                assert store.get_item("xhs", "a")["in_default"] is True
                assert store.get_item("xhs", "a")["watch_later"] is True
                page.get_by_role("button", name="返回收藏夹", exact=True).click()
                page.get_by_role("button", name="新建收藏夹", exact=True).click()
                page.get_by_role("textbox", name="名称").fill("跨平台学习")
                page.get_by_role("textbox", name="简介").fill("跨平台内容整理")
                page.get_by_role("button", name="创建", exact=True).click()
                expect(page.locator(".local-folder-open").filter(has_text="跨平台学习")).to_be_visible()
                page.locator('[data-local-folder="all"]').click()
                page.get_by_role("button", name="批量管理", exact=True).click()
                page.get_by_role("checkbox", name="选择当前全部结果").check()
                page.get_by_label("加入收藏夹", exact=True).select_option(label="跨平台学习")
                expect(page.get_by_role("checkbox", name="选择当前全部结果")).not_to_be_checked()
                assert store.list_collections()[0]["item_count"] == 2
                page.get_by_role("button", name="查看内容信息：图文收藏测试", exact=True).click()
                drawer = page.get_by_role("dialog", name="内容信息", exact=True)
                drawer.get_by_role("button", name="编辑备注 图文收藏测试", exact=True).click()
                field = drawer.get_by_role("textbox", name="备注 图文收藏测试", exact=True)
                field.fill("新的学习备注")
                drawer.get_by_role("button", name="保存备注", exact=True).click()
                expect(drawer.get_by_text("新的学习备注", exact=True)).to_be_visible()
                expect(drawer.get_by_role("button", name="关闭内容详情")).to_be_enabled()
                page.keyboard.press("Escape")
                expect(drawer).to_have_count(0)
                page.get_by_role("button", name="返回收藏夹", exact=True).click()
                page.locator(".local-folder-open").filter(has_text="跨平台学习").click()
                expect(page.get_by_role("button", name="新建收藏夹", exact=True)).to_have_count(0)
                page.get_by_role("button", name="返回收藏夹", exact=True).click()
                page.get_by_role("button", name="新建收藏夹", exact=True).click()
                long_name = "超长收藏夹LongFolder" * 4
                page.get_by_role("textbox", name="名称").fill(long_name)
                page.get_by_role("button", name="创建", exact=True).click()
                expect(page.locator(".local-folder-card")).to_have_count(5)
                (ROOT / "build").mkdir(exist_ok=True)
                expect(page.locator(".local-folder-browser")).to_be_visible()
                expect(page.locator(".local-folder-card")).to_have_count(5)
                expect(page.locator(".local-folder-card").filter(has_text="跨平台学习").locator("img")).to_be_visible()
                expect(page.locator(".local-folder-browser").get_by_role("button", name="更多操作：跨平台学习")).to_have_count(0)
                page.screenshot(path=str(ROOT / "build" / "review-local-folder-grid.png"), full_page=True)
                original_order = [item["name"] for item in store.list_collections()]
                page.get_by_role("button", name="整理顺序", exact=True).click()
                page.get_by_role("button", name=f"后移收藏夹：{original_order[0]}", exact=True).click()
                expect(page.locator(".local-folder-section").last.locator(".local-folder-name").first).to_have_text(original_order[1])
                assert [item["name"] for item in store.list_collections()] == original_order[::-1]
                page.get_by_role("button", name=f"前移收藏夹：{original_order[0]}", exact=True).click()
                expect(page.locator(".local-folder-section").last.locator(".local-folder-name").first).to_have_text(original_order[0])
                page.get_by_role("button", name="完成排序", exact=True).click()
                page.locator(".local-folder-open").filter(has_text="跨平台学习").click()
                expect(page.get_by_role("button", name="返回收藏夹", exact=True)).to_be_visible()
                expect(page.get_by_text("视频收藏测试", exact=True)).to_be_visible()
                expect(page.locator(".library-side")).to_have_count(0)
                expect(page.locator(".local-content-card")).to_have_count(2)
                expect(page.locator(".local-content-card").filter(has_text="视频收藏测试")).to_contain_text("暂无封面")
                page.get_by_role("button", name="列表", exact=True).click()
                expect(page.locator(".result-row")).to_have_count(2)
                expect(page.locator(".result-list .bookmark-note")).to_have_count(0)
                page.get_by_role("button", name="查看内容信息：图文收藏测试", exact=True).click()
                page.get_by_role("button", name="编辑备注 图文收藏测试", exact=True).click()
                draft = page.get_by_role("textbox", name="备注 图文收藏测试", exact=True)
                draft.fill("切换后保留的草稿")
                expect(draft).to_have_value("切换后保留的草稿")
                page.get_by_role("button", name="取消编辑", exact=True).click()
                page.get_by_role("button", name="关闭内容详情").click()
                page.get_by_role("button", name="网格", exact=True).click()
                page.get_by_role("button", name="列表", exact=True).click()
                page.set_viewport_size({"width": 390, "height": 844})
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                page.screenshot(path=str(ROOT / "build/content-list-local-mobile.png"), full_page=True)
                page.get_by_role("button", name="网格", exact=True).click()
                page.set_viewport_size({"width": 1440, "height": 1000})
                trigger = page.get_by_role("button", name="查看内容信息：图文收藏测试", exact=True)
                trigger.press("Enter")
                drawer = page.get_by_role("dialog", name="内容信息", exact=True)
                expect(drawer).to_be_visible()
                expect(drawer.get_by_role("button", name="关闭内容详情")).to_be_focused()
                assert page.locator("#root").evaluate("el => el.inert")
                drawer.get_by_role("heading", name="图文收藏测试").click()
                expect(drawer).to_be_visible()
                page.screenshot(path=str(ROOT / "build" / "review-local-content-drawer.png"), full_page=True)
                page.mouse.click(30, 300)
                expect(drawer).to_have_count(0)
                expect(trigger).to_be_focused()
                assert not page.locator("#root").evaluate("el => el.inert")
                trigger.click()
                drawer.get_by_role("button", name="编辑备注 图文收藏测试", exact=True).click()
                note_field = drawer.get_by_role("textbox", name="备注 图文收藏测试", exact=True)
                note_field.fill("抽屉未保存的备注")
                page.once("dialog", lambda dialog: dialog.dismiss())
                page.mouse.click(30, 300)
                expect(drawer).to_be_visible()
                expect(note_field).to_have_value("抽屉未保存的备注")
                failures["note"] = True
                drawer.get_by_role("button", name="保存备注", exact=True).click()
                expect(drawer.get_by_role("alert")).to_contain_text("备注未保存，请重试")
                expect(note_field).to_have_value("抽屉未保存的备注")
                failures["note"] = False
                note_field.fill("新的学习备注")
                drawer.get_by_role("button", name="取消编辑", exact=True).click()
                drawer.get_by_role("button", name="编辑归属", exact=True).click()
                membership = drawer.get_by_role("dialog", name="编辑收藏夹归属 图文收藏测试", exact=True)
                folder_summary = drawer.get_by_role("list", name="所在收藏夹", exact=True)
                expect(folder_summary).to_contain_text("全部收藏")
                expect(folder_summary).to_contain_text("默认收藏夹")
                expect(folder_summary).to_contain_text("跨平台学习")
                expect(folder_summary).not_to_contain_text("稍后再看")
                membership.get_by_role("checkbox", name=long_name, exact=True).click()
                expect(folder_summary).to_contain_text(long_name)
                expect(membership.get_by_role("checkbox", name=long_name, exact=True)).to_be_checked()
                membership.get_by_role("checkbox", name=long_name, exact=True).click()
                expect(folder_summary).not_to_contain_text(long_name)
                membership.get_by_role("button", name="完成", exact=True).focus()
                expect(membership.get_by_role("button", name="完成", exact=True)).to_be_focused()
                for width in (1440, 390):
                    page.set_viewport_size({"width": width, "height": 1000})
                    bounds = membership.bounding_box()
                    assert bounds and bounds["width"] >= min(350, width - 32)
                    assert abs(bounds["x"] + bounds["width"] / 2 - width / 2) < 2
                    assert abs(bounds["y"] + bounds["height"] / 2 - 500) < 2
                    assert membership.evaluate("el => el.scrollWidth <= el.clientWidth")
                    page.screenshot(path=str(ROOT / "build" / f"review-membership-centered-{width}.png"))
                page.keyboard.press("Shift+Tab")
                expect(membership.get_by_role("checkbox").last).to_be_focused()
                page.keyboard.press("Tab")
                expect(membership.get_by_role("button", name="完成", exact=True)).to_be_focused()
                page.set_viewport_size({"width": 1440, "height": 1000})
                page.mouse.click(30, 300)
                expect(membership).to_have_count(0)
                expect(drawer).to_be_visible()
                expect(drawer.get_by_role("button", name="编辑归属", exact=True)).to_be_focused()
                drawer.get_by_role("button", name="编辑归属", exact=True).click()
                page.keyboard.press("Escape")
                expect(drawer).to_be_visible()
                expect(drawer.locator(".membership-card")).to_have_count(0)
                drawer.get_by_role("button", name="取消收藏 图文收藏测试", exact=True).click()
                expect(drawer.locator(".confirm-card")).to_be_visible()
                page.keyboard.press("Escape")
                expect(drawer.locator(".confirm-card")).to_have_count(0)
                expect(drawer).to_be_visible()
                page.keyboard.press("Escape")
                expect(drawer).to_have_count(0)
                expect(trigger).to_be_focused()
                page.get_by_role("textbox", name="结果内关键词").fill("视频")
                expect(page.locator(".local-content-card")).to_have_count(1)
                page.get_by_role("textbox", name="结果内关键词").fill("")
                page.get_by_role("button", name="批量管理", exact=True).click()
                page.get_by_role("checkbox", name="选择当前全部结果").check()
                expect(page.get_by_role("button", name="导出 CSV", exact=True)).to_be_enabled()
                expect(page.get_by_label("加入收藏夹", exact=True)).to_be_visible()
                page.get_by_role("button", name="收起", exact=True).click()
                page.screenshot(path=str(ROOT / "build" / "review-local-content-grid.png"), full_page=True)
                more = page.get_by_role("button", name="更多操作：跨平台学习")
                expect(more).to_be_visible()
                closed_bounds = more.bounding_box()
                more.click()
                menu = page.locator(".library-folder-menu")
                expect(menu).to_be_visible()
                assert more.bounding_box() == closed_bounds, "sidebar menu must not move its trigger"
                assert menu.get_by_role("button").count() == 2
                expect(menu).not_to_contain_text("夹内内容仍会保留")
                page.keyboard.press("Tab")
                expect(menu.get_by_role("button", name="编辑信息")).to_be_focused()
                page.screenshot(path=str(ROOT / "build" / "review-local-folder-menu.png"), full_page=True)
                page.keyboard.press("Escape")
                expect(menu).to_have_count(0)
                assert more.evaluate("el => document.activeElement === el")
                more.press("Enter")
                menu.get_by_role("button", name="编辑信息").click()
                info = page.get_by_role("dialog", name="编辑收藏夹信息")
                expect(info).to_be_visible()
                expect(info.get_by_label("名称")).to_be_focused()
                info.get_by_label("名称").fill("跨平台学习改名")
                info.get_by_label("简介").fill("整理跨平台的学习资料")
                image = BytesIO()
                Image.new("RGB", (640, 360), "#2579b7").save(image, format="PNG")
                with page.expect_file_chooser() as chooser:
                    info.get_by_role("button", name="点击更换收藏夹封面").click()
                chooser.value.set_files({"name": "folder-cover.png", "mimeType": "image/png", "buffer": image.getvalue()})
                expect(info.locator(".folder-info-cover img")).to_have_attribute("src", re.compile(r"^data:image/png;base64,"))
                page.screenshot(path=str(ROOT / "build" / "review-local-folder-edit-card.png"), full_page=True)
                info.get_by_role("button", name="保存", exact=True).click()
                expect(info).to_have_count(0)
                expect(page.locator(".local-folder-detail-title h2")).to_have_text("跨平台学习改名")
                expect(page.locator(".local-folder-detail-description")).to_have_text("整理跨平台的学习资料")
                assert store.get_collection_cover(store.list_collections()[0]["id"]) is not None
                page.get_by_role("button", name="更多操作：跨平台学习改名").click()
                page.locator(".library-folder-menu").get_by_role("button", name="编辑信息").click()
                info = page.get_by_role("dialog", name="编辑收藏夹信息")
                info.get_by_label("名称").fill("跨平台学习")
                info.get_by_role("button", name="保存", exact=True).click()
                expect(page.locator(".local-folder-detail-title h2")).to_have_text("跨平台学习")
                page.screenshot(path=str(ROOT / "build" / "review-local-folder-detail.png"), full_page=True)
                page.set_viewport_size({"width": 320, "height": 700})
                expect(page.locator(".library-folder-grip")).to_have_count(0)
                more = page.get_by_role("button", name="更多操作：跨平台学习")
                assert more.evaluate("el => el.getBoundingClientRect().width >= 40")
                more.click()
                menu_bounds = page.locator(".library-folder-menu").bounding_box()
                assert menu_bounds is not None and menu_bounds["x"] >= 0 and menu_bounds["x"] + menu_bounds["width"] <= 320
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), "detail menu overflow at 320px"
                page.screenshot(path=str(ROOT / "build" / "review-local-folder-menu-320.png"), full_page=True)
                page.locator(".library-folder-menu").get_by_role("button", name="编辑信息").click()
                edit_bounds = page.locator(".folder-info-card").bounding_box()
                assert edit_bounds is not None and edit_bounds["x"] >= 0 and edit_bounds["x"] + edit_bounds["width"] <= 320
                page.keyboard.press("Escape")
                expect(page.locator(".folder-info-card")).to_have_count(0)
                page.set_viewport_size({"width": 1440, "height": 1000})
                page.get_by_role("button", name="返回收藏夹", exact=True).click()
                expect(page.locator(".local-folder-browser")).to_be_visible()
                page.set_viewport_size({"width": 320, "height": 700})
                page.locator(".local-folder-open").filter(has_text=long_name).click()
                long_title = page.locator(".local-folder-detail-title h2")
                assert long_title.get_attribute("title") == long_name
                assert long_title.evaluate("el => el.scrollWidth <= el.clientWidth"), "long icon-folder title should wrap"
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), "long folder detail overflow at 320px"
                page.get_by_role("button", name="返回收藏夹", exact=True).click()
                page.set_viewport_size({"width": 390, "height": 844})
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), "local icon grid overflow at 390px"
                page.screenshot(path=str(ROOT / "build" / "review-local-folder-grid-mobile.png"), full_page=True)
                page.reload()
                expect(page.locator(".local-folder-browser")).to_be_visible()
                custom_card = page.locator(".local-folder-open").filter(has_text="跨平台学习")
                expect(custom_card.locator("img")).to_have_attribute("src", re.compile(r"^/api/library/collections/\d+/cover\?v="))
                custom_card.click()
                expect(page.locator(".local-folder-detail-description")).to_have_text("整理跨平台的学习资料")
                mobile_trigger = page.get_by_role("button", name="查看内容信息：视频收藏测试", exact=True)
                expect(page.locator("[data-sonner-toast][data-visible='true']")).to_have_count(0, timeout=10000)
                mobile_trigger.click()
                drawer = page.get_by_role("dialog", name="内容信息", exact=True)
                expect(drawer).to_contain_text("暂无封面")
                bounds = drawer.bounding_box()
                assert bounds and bounds["x"] >= 0 and bounds["x"] + bounds["width"] <= 390 and bounds["y"] == 0
                page.screenshot(path=str(ROOT / "build" / "review-local-content-mobile.png"))
                drawer.get_by_role("button", name="关闭内容详情").click()
                expect(mobile_trigger).to_be_focused()
                page.get_by_role("button", name="返回收藏夹", exact=True).click()
                page.locator('[data-local-folder="all"]').click()
                for width in (1024, 390):
                    page.set_viewport_size({"width": width, "height": 844})
                    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), f"local layout overflow at {width}"
                page.locator("[data-sonner-toast]").evaluate_all("els => els.forEach(el => el.remove())")
                page.get_by_role("combobox", name="切换主题", exact=True).click()
                page.get_by_role("option", name="Dark", exact=True).click()
                page.wait_for_function("getComputedStyle(document.body).backgroundColor === 'rgb(16, 18, 24)'")
                page.get_by_role("button", name="查看内容信息：图文收藏测试", exact=True).click()
                page.get_by_role("button", name="编辑归属", exact=True).first.click()
                page.screenshot(path=str(ROOT / "build" / "review-favorites-local-mobile-dark.png"), full_page=True)
                page.locator(".membership-card").get_by_role("button", name="完成", exact=True).click()
                page.set_viewport_size({"width": 1440, "height": 1000})
                page.reload()
                page.locator('[data-local-folder="all"]').click()
                page.get_by_role("button", name="查看内容信息：图文收藏测试", exact=True).click()
                expect(page.get_by_role("dialog", name="内容信息").get_by_text("新的学习备注", exact=True)).to_be_visible()
                page.keyboard.press("Escape")
                page.get_by_role("button", name="返回收藏夹", exact=True).click()
                expect(page.get_by_role("button", name="迁移到本机收藏库", exact=True)).to_have_count(0)
                page.locator(".local-folder-open").filter(has_text="跨平台学习").click()
                page.get_by_role("button", name="更多操作：跨平台学习").click()
                page.locator(".library-folder-menu").get_by_role("button", name="删除", exact=True).click()
                expect(page.locator(".local-folder-browser")).to_be_visible()
                expect(page.locator(".local-folder-open").filter(has_text="跨平台学习")).to_have_count(0)
                assert store.stats()["total"] == 2
                page.locator('[data-local-folder="all"]').click()
                # ── 取消收藏 = 从本机移除，先弹居中的确认框 ──
                page.get_by_role("combobox", name="切换主题", exact=True).click()
                page.get_by_role("option", name="Light", exact=True).click()
                page.get_by_role("button", name="取消收藏 视频收藏测试", exact=True).click()
                dialog = page.locator(".confirm-card")
                expect(dialog).to_be_visible()
                expect(dialog).to_contain_text("是否要取消收藏")
                expect(dialog).to_contain_text("编辑归属")
                box = dialog.bounding_box()
                assert box is not None and box["y"] > 200, "确认框要在屏幕正中，不是顶部提示"
                assert box["width"] >= 440, "确认框要放得下正文，别挤成一条窄柱"
                page.screenshot(path=str(ROOT / "build" / "review-unsave-confirm.png"), full_page=True)
                dialog.get_by_role("button", name="取消", exact=True).click()
                expect(dialog).to_have_count(0)
                assert store.get_item("bilibili", "b") is not None
                page.get_by_role("button", name="取消收藏 视频收藏测试", exact=True).click()
                page.locator(".confirm-card").get_by_role("button", name="取消收藏", exact=True).click()
                expect(page.locator(".confirm-card")).to_have_count(0)
                assert store.get_item("bilibili", "b") is None
                assert store.stats()["total"] == 1
                # ── 勾了「下次不再提示」之后直接取消，不再弹框 ──
                page.get_by_role("button", name="取消收藏 图文收藏测试", exact=True).click()
                dialog = page.locator(".confirm-card")
                expect(dialog).to_be_visible()
                dialog.get_by_role("checkbox", name="下次不再提示").check()
                dialog.get_by_role("button", name="取消收藏", exact=True).click()
                expect(page.locator(".confirm-card")).to_have_count(0)
                assert store.get_item("xhs", "a") is None
                assert store.stats()["total"] == 0
                page.get_by_role("button", name="跨平台收藏", exact=True).click()
                expect(page.get_by_text("视频收藏测试", exact=True)).to_be_visible()
                page.get_by_role("button", name="同步所选平台 / 继续", exact=True).click()
                expect(page.get_by_role("alert")).to_contain_text("测试同步失败")
                expect(page.get_by_text("视频收藏测试", exact=True)).to_be_visible()
                failures["sync"] = False
                page.get_by_role("button", name="同步所选平台 / 继续", exact=True).click()
                expect(page.get_by_role("button", name="正在同步 · 取消", exact=True)).to_be_enabled()
                expect(page.get_by_role("button", name="正在同步 · 取消", exact=True).locator(".spinner")).to_be_visible()
                page.screenshot(path=str(ROOT / "build" / "review-cancel-button.png"), full_page=True)
                page.get_by_role("button", name="正在同步 · 取消", exact=True).click()
                expect(page.get_by_role("alert")).to_contain_text("测试取消失败")
                failures["cancel"] = False
                page.get_by_role("button", name="正在同步 · 取消", exact=True).click()
                expect(page.get_by_role("button", name="正在同步 · 取消", exact=True)).to_have_count(0)
                expect(page.get_by_role("button", name="同步所选平台 / 继续", exact=True)).to_be_enabled()
                expect(page.get_by_text("视频收藏测试", exact=True)).to_be_visible()
                page.set_viewport_size({"width": 390, "height": 844})
                page.screenshot(path=str(ROOT / "build" / "review-favorites-mobile.png"), full_page=True)
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                page.set_viewport_size({"width": 1440, "height": 1000})
                page.goto(origin + "/#/settings/accounts")
                failures["verified"] = True
                page.reload()
                expect(page.get_by_text("已就绪，直接去搜索即可。", exact=True)).to_be_visible()
                expect(page.get_by_text("暂时无法搜索", exact=True)).to_have_count(0)
                expect(page.get_by_text("历史搜索要求登录", exact=True)).to_have_count(0)
                page.screenshot(path=str(ROOT / "build" / "review-account-wording.png"), full_page=True)
                page.set_viewport_size({"width": 320, "height": 844})
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), "settings navigation overflow at 320px"
                expect(page.locator(".settings-nav button")).to_have_count(3)
                page.screenshot(path=str(ROOT / "build" / "review-settings-mobile-320.png"), full_page=True)
                page.set_viewport_size({"width": 1440, "height": 1000})
                page.get_by_role("button", name="扫码登录", exact=True).click()
                expect(page.get_by_text("测试等待扫码", exact=True)).to_be_visible()
                page.evaluate("location.hash = '#/'")
                # Playwright keeps servicing the intercepted local task polling.
                page.wait_for_timeout(2200)
                assert login_polls, "scan login polling stopped when leaving accounts"
                page.evaluate("location.hash = '#/settings/accounts'")
                expect(page.get_by_text("测试扫码已完成", exact=True)).to_be_visible()

                # ── 500 条真实上限：首屏只渲染 100 条，继续时每次再放 100 条 ──
                bulk = [
                    {
                        "result": {
                            "platform": "xhs",
                            "content_id": f"boundary-{index:03d}",
                            "title": f"边界收藏 {index:03d}",
                            "url": f"https://www.xiaohongshu.com/explore/boundary-{index:03d}",
                        }
                    }
                    for index in range(500)
                ]
                bulk[0]["result"].update(cover_url="https://sns-webpic-qc.xhscdn.com/202609091523/0123456789abcdef0123456789abcdef/spectrum/testcover!nc_n_webp_mw_1", content_type="note")
                bulk[1]["result"].update(platform="zhihu", content_type="answer", snippet="这是已保存的问题摘要，用来展示知乎主题封面。")
                bulk[2]["result"].update(platform="zhihu", content_type="answer", cover_url="https://broken.siye.invalid/image.jpg")
                assert store.add_items(bulk) == {"added": 500, "updated": 0, "skipped": 0}
                page.evaluate("location.hash = '#/favorites/local'")
                page.locator(".local-folder-open").filter(has_text="全部收藏").click()
                expect(page.locator(".local-content-card")).to_have_count(100)
                page.get_by_role("button", name="显示更多", exact=True).click()
                expect(page.locator(".local-content-card")).to_have_count(200)
                page.get_by_role("button", name="列表", exact=True).click()
                expect(page.locator(".result-row")).to_have_count(200)
                expect(page.locator(".library-batch-bar")).to_contain_text("已显示 200 / 500 条")
                expect(page.locator(".library-side")).to_have_count(0)
                page.screenshot(path=str(ROOT / "build/content-list-local-desktop.png"))
                page.get_by_role("button", name="批量管理", exact=True).click()
                page.get_by_role("checkbox", name="选择当前全部结果", exact=True).check()
                page.get_by_role("button", name="网格", exact=True).click()
                expect(page.get_by_role("checkbox", name="选择当前全部结果", exact=True)).to_be_checked()
                expect(page.locator(".local-content-card")).to_have_count(200)
                page.get_by_role("button", name="列表", exact=True).click()
                with page.expect_download() as exported:
                    page.get_by_role("button", name="导出 CSV", exact=True).click()
                csv_text = Path(exported.value.path()).read_text(encoding="utf-8-sig")
                assert all(f"边界收藏 {index:03d}" in csv_text for index in range(500)), "grid export omitted undisplayed results"
                page.get_by_role("button", name="收起", exact=True).click()
                page.get_by_role("button", name="网格", exact=True).click()
                page.get_by_role("textbox", name="结果内关键词").fill("边界收藏 00")
                recovered_cover = page.get_by_role("button", name="查看内容信息：边界收藏 000", exact=True).locator("img")
                expect(recovered_cover).to_have_attribute("src", "https://sns-img-qc.xhscdn.com/spectrum/testcover")
                expect(recovered_cover).to_be_visible()
                assert recovered_cover.evaluate("el => el.complete && el.naturalWidth > 0")
                for number in (1, 2):
                    topic = page.get_by_role("button", name=f"查看内容信息：边界收藏 {number:03d}", exact=True).locator(".local-content-topic")
                    expect(topic).to_contain_text("知乎 · 回答")
                    expect(topic).to_contain_text(f"边界收藏 {number:03d}")
                page.screenshot(path=str(ROOT / "build" / "review-content-cover-fallbacks.png"), full_page=True)
                assert not errors, errors
                browser.close()
        finally:
            server.shutdown()
            client.close()
        print("favorites UI: list/grid selection and pagination, note drafts, built-in folders, independent watch-later, folders, notes, cache, cancel, responsive layout PASS")


if __name__ == "__main__":
    raise SystemExit(main())
