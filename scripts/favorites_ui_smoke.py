"""Exercise the built favorites UI against an isolated SQLite library.

No real accounts, user library, or platform requests are accessed.
Run after npm run build with the existing Playwright/Edge installation.
"""

import json
import argparse
import sys
import threading
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
            if url.netloc == "covers.siye.invalid":
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
                expect(page.get_by_text("图文收藏测试", exact=True)).to_be_visible()
                assert store.stats()["total"] == 2
                expect(page.get_by_role("button", name="全部 2", exact=True)).to_be_visible()
                expect(page.get_by_role("button", name="默认收藏夹 2", exact=True)).to_be_visible()
                expect(page.get_by_role("button", name="稍后再看 0", exact=True)).to_be_visible()
                first_actions = page.locator(".result-row").first.locator(".row-actions button")
                assert first_actions.count() >= 2
                assert "收藏" in (first_actions.nth(0).get_attribute("aria-label") or "")
                assert "稍后再看" in (first_actions.nth(1).get_attribute("aria-label") or "")
                page.get_by_role("button", name="稍后再看 图文收藏测试", exact=True).click()
                expect(page.get_by_role("button", name="稍后再看 1", exact=True)).to_be_visible()
                assert store.get_item("xhs", "a")["in_default"] is True
                assert store.get_item("xhs", "a")["watch_later"] is True
                # 收藏信息条：归属面板只管收藏体系，稍后再看不在里面
                note_bar = page.locator(".saved-result-item").filter(has_text="图文收藏测试").locator(".bookmark-note")
                expect(note_bar).to_be_visible()
                note_bar.get_by_role("button", name="编辑归属", exact=True).click()
                inline_card = page.locator(".membership-card")
                expect(inline_card).to_be_visible()
                assert inline_card.get_by_role("checkbox", name="默认收藏夹").is_checked()
                assert inline_card.get_by_role("checkbox", name="稍后再看").count() == 0
                inline_card.get_by_role("button", name="完成", exact=True).click()
                expect(inline_card).to_have_count(0)
                page.get_by_role("button", name="新建收藏夹", exact=True).click()
                page.get_by_role("textbox", name="新收藏夹名称").fill("跨平台学习")
                page.get_by_role("button", name="创建收藏夹", exact=True).click()
                expect(page.get_by_role("button", name="跨平台学习 0", exact=True)).to_be_visible()
                page.get_by_role("button", name="批量管理", exact=True).click()
                page.get_by_role("checkbox", name="选择当前全部结果").check()
                page.get_by_label("加入收藏夹", exact=True).select_option(label="跨平台学习")
                expect(page.get_by_role("checkbox", name="选择当前全部结果")).not_to_be_checked()
                assert store.list_collections()[0]["item_count"] == 2
                page.get_by_role("button", name="跨平台学习 2", exact=True).click()
                expect(page.get_by_text("视频收藏测试", exact=True)).to_be_visible()
                page.get_by_role("button", name="编辑备注 图文收藏测试", exact=True).click()
                field = page.get_by_role("textbox", name="备注 图文收藏测试", exact=True)
                field.fill("新的学习备注")
                failures["note"] = True
                page.get_by_role("button", name="保存备注", exact=True).click()
                expect(page.get_by_text("测试磁盘写入失败", exact=True)).to_be_visible()
                expect(field).to_have_value("新的学习备注")
                assert store.get_item("xhs", "a")["note"] == "旧备注"
                failures["note"] = False
                page.get_by_role("button", name="保存备注", exact=True).click()
                expect(page.get_by_text("新的学习备注", exact=True)).to_be_visible()
                assert store.get_item("xhs", "a")["note"] == "新的学习备注"
                (ROOT / "build").mkdir(exist_ok=True)
                page.get_by_role("button", name="新建收藏夹", exact=True).click()
                long_name = "超长收藏夹LongFolder" * 4
                page.get_by_role("textbox", name="新收藏夹名称").fill(long_name)
                page.get_by_role("button", name="创建收藏夹", exact=True).click()
                saved_items = page.locator(".saved-result-item")
                assert saved_items.count() == 2
                assert saved_items.first.locator(":scope > .result-row").count() == 1
                assert saved_items.first.locator(":scope > .bookmark-note").count() == 1
                assert saved_items.first.locator(".result-number").evaluate(
                    "el => getComputedStyle(el, '::before').width",
                ) == "14px"
                assert saved_items.first.locator(".bookmark-note").evaluate(
                    "el => getComputedStyle(el).backgroundColor !== 'rgba(0, 0, 0, 0)'",
                )
                for width in (1440, 1024, 390):
                    page.set_viewport_size({"width": width, "height": 900})
                    expect(page.get_by_text(long_name, exact=True)).to_be_visible()
                    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                    if width == 390:
                        assert saved_items.first.locator(".result-number").evaluate(
                            "el => getComputedStyle(el).display",
                        ) == "none"
                    label = page.locator(".library-folder-name").filter(has_text=long_name)
                    assert label.evaluate("el => el.scrollWidth > el.clientWidth")
                    assert label.get_attribute("title") == long_name
                    page.get_by_role("button", name="编辑归属", exact=True).first.click()
                    card = page.locator(".membership-card")
                    expect(card).to_be_visible()
                    membership_label = card.locator(".membership-folder-name").filter(has_text=long_name)
                    expect(membership_label).to_be_visible()
                    assert membership_label.evaluate("el => el.scrollWidth > el.clientWidth")
                    assert membership_label.get_attribute("title") == long_name
                    assert card.evaluate("el => el.scrollWidth <= el.clientWidth")
                    assert card.evaluate("el => getComputedStyle(el).overflowY === 'auto'")
                    bounds = card.bounding_box()
                    assert bounds is not None
                    assert bounds["x"] >= 0 and bounds["x"] + bounds["width"] <= width
                    card.get_by_role("button", name="完成", exact=True).click()
                page.set_viewport_size({"width": 1440, "height": 1000})
                page.get_by_role("button", name="编辑归属", exact=True).first.click()
                page.screenshot(path=str(ROOT / "build" / "review-favorites-desktop.png"), full_page=True)
                page.locator(".membership-card").get_by_role("button", name="完成", exact=True).click()
                # 图标模式只浏览收藏夹；点击后仍回到原有纵向内容列表，返回时保留网格。
                page.get_by_role("button", name="全部 2", exact=True).click()
                page.get_by_role("button", name="图标", exact=True).click()
                expect(page.locator(".local-folder-browser")).to_be_visible()
                expect(page.locator(".local-folder-card")).to_have_count(5)
                expect(page.locator(".local-folder-card").filter(has_text="跨平台学习").locator("img")).to_be_visible()
                page.screenshot(path=str(ROOT / "build" / "review-local-folder-grid.png"), full_page=True)
                page.locator(".local-folder-open").filter(has_text="跨平台学习").click()
                expect(page.get_by_role("button", name="返回收藏夹", exact=True)).to_be_visible()
                expect(page.get_by_text("视频收藏测试", exact=True)).to_be_visible()
                page.screenshot(path=str(ROOT / "build" / "review-local-folder-detail.png"), full_page=True)
                page.get_by_role("button", name="返回收藏夹", exact=True).click()
                expect(page.locator(".local-folder-browser")).to_be_visible()
                page.set_viewport_size({"width": 390, "height": 844})
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), "local icon grid overflow at 390px"
                page.screenshot(path=str(ROOT / "build" / "review-local-folder-grid-mobile.png"), full_page=True)
                page.get_by_role("button", name="列表", exact=True).click()
                for width in (1024, 390):
                    page.set_viewport_size({"width": width, "height": 844})
                    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), f"local layout overflow at {width}"
                page.locator("[data-sonner-toast]").evaluate_all("els => els.forEach(el => el.remove())")
                page.get_by_role("combobox", name="切换主题", exact=True).click()
                page.get_by_role("option", name="Dark", exact=True).click()
                page.wait_for_function("getComputedStyle(document.body).backgroundColor === 'rgb(16, 18, 24)'")
                page.get_by_role("button", name="编辑归属", exact=True).first.click()
                page.screenshot(path=str(ROOT / "build" / "review-favorites-local-mobile-dark.png"), full_page=True)
                page.locator(".membership-card").get_by_role("button", name="完成", exact=True).click()
                page.set_viewport_size({"width": 1440, "height": 1000})
                page.reload()
                expect(page.get_by_text("新的学习备注", exact=True)).to_be_visible()
                expect(page.get_by_role("button", name="迁移到本机收藏库", exact=True)).to_have_count(0)
                page.get_by_role("button", name="删除收藏夹 跨平台学习", exact=True).click()
                expect(page.get_by_role("button", name="跨平台学习 2", exact=True)).to_have_count(0)
                assert store.stats()["total"] == 2
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
                page.get_by_role("button", name="全部 1", exact=True).click()
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
                page.get_by_role("button", name="扫码登录", exact=True).click()
                expect(page.get_by_text("测试等待扫码", exact=True)).to_be_visible()
                page.evaluate("location.hash = '#/'")
                # Playwright keeps servicing the intercepted local task polling.
                page.wait_for_timeout(2200)
                assert login_polls, "scan login polling stopped when leaving accounts"
                page.evaluate("location.hash = '#/settings/accounts'")
                expect(page.get_by_text("测试扫码已完成", exact=True)).to_be_visible()
                assert not errors, errors
                browser.close()
        finally:
            server.shutdown()
            client.close()
        print("favorites UI: built-in folders, independent watch-later, folders, notes, cache, cancel, responsive layout PASS")


if __name__ == "__main__":
    raise SystemExit(main())
