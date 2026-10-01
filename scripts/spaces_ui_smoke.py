"""Research spaces and rich notes in the built UI, with isolated SQLite and fake search."""

from __future__ import annotations

import json
import sys
import threading
from datetime import datetime, timezone
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import urlparse

from fastapi import FastAPI
from fastapi.testclient import TestClient
from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from api.routers.library import library_router
from api.routers.spaces import spaces_router
from api.services.library_store import LibraryStore, get_library_store
from api.services.spaces_store import SpacesStore, get_spaces_store


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *_args):
        pass


def main():
    dist = ROOT / "webui/dist"
    if not (dist / "index.html").exists():
        raise SystemExit("Build webui first")
    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(QuietHandler, directory=str(dist)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    origin = f"http://127.0.0.1:{server.server_port}"
    now = datetime.now(timezone.utc).isoformat()
    platforms = ("xhs", "douyin", "bilibili", "zhihu")
    domains = ("www.xiaohongshu.com", "www.douyin.com", "www.bilibili.com", "www.zhihu.com")
    sources = [{"platform": platform, "content_id": f"suzhou-{index}", "title": f"苏州攻略{index}", "content_type": "video" if index in (1, 2) else "note", "url": f"https://{domain}/explore/{index}", "author": "测试作者", "snippet": "苏州园林、街巷和美食的研究资料", "cover_url": None, "published_at": now, "metrics": {"like_count": 12}, "rank": 1, "duration_seconds": 120} for index, (platform, domain) in enumerate(zip(platforms, domains))]
    group = {**sources[0], "grouped_sources": [sources[0], sources[3]]}
    job = {"job_id": "spaces-smoke", "overall": "completed", "keyword": "苏州攻略", "created_at": now, "completed_at": now, "hydration_status": "completed", "results": [group, sources[1], sources[2]], "platforms": {platform: {"status": "succeeded", "result_count": 1, "error_summary": None} for platform in platforms}}
    fail_notes = False
    fail_items = False
    item_deletes = []
    errors = []
    (ROOT / "build").mkdir(exist_ok=True)
    try:
        with TemporaryDirectory(prefix="spaces-smoke-", dir=ROOT / "build") as temp, sync_playwright() as playwright:
            store = SpacesStore(Path(temp) / "library.db")
            library = LibraryStore(store.db_path)
            app = FastAPI()
            app.include_router(spaces_router)
            app.include_router(library_router)
            app.dependency_overrides[get_spaces_store] = lambda: store
            app.dependency_overrides[get_library_store] = lambda: library
            client = TestClient(app)

            def route_request(route):
                request = route.request
                path = urlparse(request.url).path
                if not request.url.startswith(origin + "/"):
                    route.abort()
                elif path.startswith(("/api/spaces", "/api/library")):
                    if path.endswith("/items") and request.method == "DELETE":
                        item_deletes.append(json.loads(request.post_data))
                        if fail_items:
                            route.fulfill(status=503, json={"detail": "测试移出失败，请重试"})
                            return
                    if fail_notes and path.endswith("/note"):
                        route.fulfill(status=503, json={"detail": "测试保存失败，草稿已保留"})
                        return
                    response = client.request(request.method, path, content=request.post_data, headers={"content-type": "application/json"})
                    route.fulfill(status=response.status_code, body=response.content, content_type="application/json")
                elif path.startswith("/api/"):
                    if path == "/api/search/accounts":
                        data = {"accounts": []}
                    elif path.startswith("/api/search/jobs/"):
                        data = job
                    elif path == "/api/health":
                        data = {"status": "ok", "environment_status": "ok"}
                    elif path.endswith("/latest"):
                        data = None
                    else:
                        data = {}
                    route.fulfill(json=data)
                else:
                    route.continue_()

            browser = playwright.chromium.launch(channel="msedge")
            context = browser.new_context(viewport={"width": 1440, "height": 1000}, locale="zh-CN")
            context.add_init_script("localStorage.setItem('mediacrawler_license_accepted', 'true'); localStorage.setItem('siye_onboarding_preference_v1', 'completed'); localStorage.setItem('mediacrawler_language', 'zh-CN')")
            context.route("**/*", route_request)
            page = context.new_page()
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(origin + "/#/search")
            expect(page.locator(".local-content-card")).to_have_count(3)
            left = page.locator(".space-edge-left")
            right = page.locator(".space-edge-right")
            def open_edge(edge, name, keyboard=False):
                if "is-open" not in edge.get_attribute("class"):
                    trigger = edge.get_by_role("button", name=name, exact=True)
                    expect(trigger).to_be_visible()
                    if keyboard or page.viewport_size["width"] <= 1000:
                        trigger.focus()
                        trigger.press("Enter")
                    else:
                        trigger.hover()
                expect(edge.locator(".space-edge-surface")).to_be_visible()
                expect(edge.locator(".space-edge-trigger")).not_to_be_visible()

            left_surface = left.locator(".space-edge-surface")
            expect(left_surface).not_to_be_visible()
            results_bounds = page.locator(".preview-container.search-shell").bounding_box()
            assert left.bounding_box()["width"] == 36
            left.get_by_role("button", name="当前空间", exact=True).hover()
            samples = left.evaluate("""async el => { const widths = []; for (let i = 0; i < 8; i++) { await new Promise(requestAnimationFrame); widths.push(el.getBoundingClientRect().width); } return widths; }""")
            assert any(36 < value < 220 for value in samples), samples
            expect(left_surface).to_be_visible()
            page.mouse.move(720, 90)
            page.wait_for_function("el => !el.classList.contains('is-open')", arg=left.element_handle())
            # Re-enter while the frame is shrinking; it reverses smoothly and still closes.
            page.mouse.move(12, 260)
            expect(left_surface).to_be_visible()
            page.mouse.move(720, 90)
            expect(left_surface).not_to_be_visible()
            page.emulate_media(reduced_motion="reduce")
            assert left.evaluate("el => getComputedStyle(el).transitionDuration") == "0s"
            open_edge(left, "当前空间", keyboard=True)
            expect(left.get_by_role("button", name="固定当前空间", exact=True)).to_be_focused()
            left.get_by_role("button", name="固定当前空间", exact=True).click()
            page.emulate_media(reduced_motion="no-preference")
            page.mouse.move(720, 90)
            expect(left_surface).to_be_visible()
            page.get_by_role("button", name="新建空间", exact=True).click()
            info = page.get_by_role("dialog", name="新建空间", exact=True)
            info.get_by_label("名称", exact=True).fill("苏州旅游攻略")
            info.get_by_label("简介", exact=True).fill("比较四个平台，准备三天的苏州行程")
            info.get_by_role("button", name="创建并启用", exact=True).click()
            expect(info).to_have_count(0)
            expect(page.get_by_role("combobox", name="当前空间", exact=True)).to_have_value("1")
            page.get_by_role("button", name="全部加入空间：苏州攻略0", exact=True).click()
            page.get_by_role("button", name="加入空间：苏州攻略1", exact=True).click()
            page.get_by_role("button", name="加入空间：苏州攻略2", exact=True).click()
            single_remove = page.get_by_role("button", name="移出空间：苏州攻略2", exact=True)
            expect(single_remove).to_be_enabled()
            expect(single_remove).to_have_attribute("aria-pressed", "true")
            expect(single_remove).to_have_text("")
            assert single_remove.locator("svg").count() == 1
            assert store.get_space(1)["item_count"] == 4
            assert library.stats()["total"] == 0
            # Clicking an added grouped card removes every displayed source in one request.
            grouped_remove = page.get_by_role("button", name="全部移出空间：苏州攻略0", exact=True)
            expect(grouped_remove).to_have_attribute("aria-pressed", "true")
            expect(grouped_remove).to_have_text("")
            grouped_remove.evaluate("el => { el.click(); el.click(); }")
            grouped_add = page.get_by_role("button", name="全部加入空间：苏州攻略0", exact=True)
            expect(grouped_add).to_be_enabled()
            expect(grouped_add).to_have_attribute("aria-pressed", "false")
            assert store.get_space(1)["item_count"] == 2
            assert len(item_deletes) == 1
            assert {key["platform"] for key in item_deletes[0]["keys"]} == {"xhs", "zhihu"}
            grouped_add.click()
            expect(grouped_remove).to_be_enabled()
            assert store.get_space(1)["item_count"] == 4

            # A failed removal stays selected and can retry; list view uses the same toggle.
            fail_items = True
            single_remove.click()
            expect(page.get_by_text("测试移出失败，请重试", exact=True)).to_be_visible()
            expect(single_remove).to_be_enabled()
            expect(single_remove).to_have_attribute("aria-pressed", "true")
            assert store.get_space(1)["item_count"] == 4
            fail_items = False
            single_remove.click()
            single_add = page.get_by_role("button", name="加入空间：苏州攻略2", exact=True)
            expect(single_add).to_be_enabled()
            assert store.get_space(1)["item_count"] == 3
            page.get_by_role("button", name="列表", exact=True).click()
            expect(single_add).to_have_text("")
            single_add.click()
            expect(single_remove).to_be_enabled()
            single_remove.click()
            expect(single_add).to_be_enabled()
            single_add.click()
            expect(single_remove).to_be_enabled()
            assert store.get_space(1)["item_count"] == 4
            page.get_by_role("button", name="网格", exact=True).click()

            # A source-level toggle removes only that source; the grouped button fills the gap.
            page.get_by_role("button", name="查看内容信息：苏州攻略0", exact=True).click()
            source_drawer = page.get_by_role("dialog", name="内容信息", exact=True)
            source_remove = source_drawer.get_by_role("button", name="移出空间：苏州攻略3", exact=True)
            expect(source_remove).to_have_text("")
            source_remove.click()
            expect(source_drawer.get_by_role("button", name="加入空间：苏州攻略3", exact=True)).to_be_enabled()
            assert store.get_space(1)["item_count"] == 3
            assert {item["result"]["platform"] for item in store.get_space(1)["items"]} == {"xhs", "douyin", "bilibili"}
            source_drawer.get_by_role("button", name="关闭内容详情", exact=True).click()
            expect(grouped_add).to_be_enabled()
            expect(grouped_add).to_have_attribute("aria-pressed", "false")
            grouped_add.click()
            expect(grouped_remove).to_be_enabled()
            assert store.get_space(1)["item_count"] == 4
            assert library.stats()["total"] == 0

            right.get_by_role("button", name="打开笔记", exact=True).hover()
            expect(right.locator(".space-edge-surface")).to_be_visible()
            page.mouse.move(720, 90)
            expect(right.locator(".space-edge-surface")).not_to_be_visible()
            open_edge(right, "打开笔记")
            right.get_by_role("button", name="固定研究笔记", exact=True).click()
            page.mouse.move(720, 90)
            expect(right.locator(".space-edge-surface")).to_be_visible()
            after = page.locator(".preview-container.search-shell").bounding_box()
            assert results_bounds and after
            assert all(abs(after[key] - results_bounds[key]) < 1 for key in ("x", "y", "width")), (results_bounds, after)
            # Both side tools leave the home search box in its original position.
            left.get_by_role("button", name="关闭当前空间面板", exact=True).click()
            page.get_by_role("button", name="返回首页", exact=True).click()
            home = page.locator(".home .search-zone")
            expect(home).to_be_visible()
            page.wait_for_timeout(250)
            home_bounds = home.bounding_box()
            right.get_by_role("button", name="关闭笔记面板", exact=True).click()
            assert home.bounding_box() == home_bounds
            for width in (1920, 1440, 390):
                page.set_viewport_size({"width": width, "height": 1000})
                page.wait_for_timeout(250)
                before = home.bounding_box()
                open_edge(left, "当前空间")
                left.get_by_role("button", name="固定当前空间", exact=True).click()
                open_edge(right, "打开笔记")
                note_modal = page.locator(".space-note-panel")
                expect(note_modal).to_be_visible()
                assert home.bounding_box() == before
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                if width > 1000:
                    right.get_by_role("button", name="固定研究笔记", exact=True).click()
                    for edge, side in ((left, "left"), (right, "right")):
                        # One border/frame replaces the small trigger, anchored at the window edge.
                        expect(edge.locator(".space-edge-trigger")).not_to_be_visible()
                        frame = edge.bounding_box()
                        surface = edge.locator(".space-edge-surface").bounding_box()
                        assert frame and surface
                        assert abs(frame["width"] - surface["width"] - 2) < 1
                        assert abs(frame["x"] - 8 if side == "left" else frame["x"] + frame["width"] - width + 8) < 1
                    page.screenshot(path=str(ROOT / f"build/spaces-search-edge-{width}.png"))
                    right.get_by_role("button", name="取消固定研究笔记", exact=True).click()
                    home.locator("input").first.click()
                    page.mouse.move(width / 2, 90)
                    expect(right.locator(".space-edge-surface")).not_to_be_visible()
                else:
                    note_modal.get_by_role("button", name="关闭笔记面板", exact=True).click()
                left.get_by_role("button", name="关闭当前空间面板", exact=True).click()
            page.set_viewport_size({"width": 1440, "height": 1000})
            open_edge(left, "当前空间")
            left.get_by_role("button", name="固定当前空间", exact=True).click()
            open_edge(right, "打开笔记")
            right.get_by_role("button", name="固定研究笔记", exact=True).click()
            left.get_by_role("button", name="关闭当前空间面板", exact=True).click()
            page.get_by_role("button", name="查看上次搜索（3 条）", exact=True).click()
            open_edge(left, "当前空间")
            left.get_by_role("button", name="固定当前空间", exact=True).click()
            note = page.get_by_role("textbox", name="空间笔记编辑器", exact=True)
            expect(note).to_be_visible()
            note.fill("苏州行程研究")
            note.press("Control+A")
            panel = page.locator(".space-note-panel")
            for format_name in ("加粗", "斜体", "下划线"):
                panel.get_by_role("button", name=format_name, exact=True).click()
            panel.get_by_role("combobox", name="字号", exact=True).select_option("24px")
            expect(note.locator("strong em u, strong u em, em strong u, em u strong, u strong em, u em strong")).to_have_count(1)
            expect(panel.get_by_text("已保存到本机", exact=True)).to_be_visible()
            saved = store.get_space(1)["note_document"]
            marks = saved["content"][0]["content"][0]["marks"]
            assert {mark["type"] for mark in marks} == {"bold", "italic", "underline", "textStyle"}
            assert next(mark["attrs"]["fontSize"] for mark in marks if mark["type"] == "textStyle") == "24px"

            # Editing focus keeps an unpinned panel open; Escape keeps the same DOM/editor.
            note.evaluate("el => { window.spaceNoteElement = el; }")
            right.get_by_role("button", name="取消固定研究笔记", exact=True).click()
            note.focus()
            page.mouse.move(720, 90)
            page.wait_for_timeout(300)
            expect(note).to_be_visible()
            note.press("Escape")
            expect(right.locator(".space-edge-surface")).not_to_be_visible()
            open_edge(right, "打开笔记")
            right.get_by_role("button", name="固定研究笔记", exact=True).click()
            expect(note).to_contain_text("苏州行程研究")
            assert note.evaluate("el => el === window.spaceNoteElement")

            # Detail shares the same editor; closing it preserves note text and history.
            page.locator(".local-content-open").first.click()
            drawer = page.get_by_role("dialog", name="内容信息", exact=True)
            expect(drawer.get_by_role("textbox", name="空间笔记编辑器", exact=True)).to_be_visible()
            drawer.get_by_role("textbox", name="空间笔记编辑器", exact=True).press("Control+End")
            drawer.get_by_role("textbox", name="空间笔记编辑器", exact=True).press("Enter")
            drawer.get_by_role("textbox", name="空间笔记编辑器", exact=True).press_sequentially("详情旁边写笔记")
            expect(drawer.get_by_text("已保存到本机", exact=True)).to_be_visible()
            drawer.get_by_role("textbox", name="空间笔记编辑器", exact=True).press("Escape")
            expect(drawer).to_have_count(0)
            expect(note).to_contain_text("详情旁边写笔记")

            # A failed autosave keeps the document; retry persists it.
            fail_notes = True
            note.press("Control+End")
            note.press("Enter")
            note.press_sequentially("失败保留测试")
            expect(page.get_by_text("测试保存失败，草稿已保留", exact=True)).to_be_visible()
            expect(note).to_contain_text("失败保留测试")
            fail_notes = False
            panel.get_by_role("button", name="重试", exact=True).click()
            expect(panel.get_by_text("已保存到本机", exact=True)).to_be_visible()

            page.get_by_role("link", name="查看资料", exact=True).click()
            expect(page.get_by_role("heading", name="苏州旅游攻略", exact=True).first).to_be_visible()
            expect(page.locator(".local-content-card")).to_have_count(4)
            assert page.locator(".content-card-actions button[aria-label^=\"移出空间：\"]").all_text_contents() == ["", "", "", ""]
            assert page.get_by_role("button", name="导出 / 复制", exact=True).count() == 0
            expect(page.get_by_role("textbox", name="空间笔记编辑器", exact=True)).to_contain_text("失败保留测试")
            page.screenshot(path=str(ROOT / "build/spaces-desktop.png"), full_page=True)
            note = page.get_by_role("textbox", name="空间笔记编辑器", exact=True)
            note.press("Control+End")
            note.press("Enter")
            panel = page.locator(".space-note-panel")
            panel.get_by_role("button", name="勾选清单", exact=True).click()
            note.press_sequentially("预约博物馆")
            note.locator('input[type="checkbox"]').check()
            expect(panel.get_by_text("已保存到本机", exact=True)).to_be_visible()
            page.reload()
            note = page.get_by_role("textbox", name="空间笔记编辑器", exact=True)
            expect(note).to_contain_text("预约博物馆")
            expect(note.locator('input[type="checkbox"]')).to_be_checked()
            expect(note.locator('span[style*="font-size: 24px"]')).not_to_have_count(0)
            page.get_by_role("button", name="归档保留", exact=True).click()
            expect(page.get_by_text("已归档 · 只读", exact=True)).to_be_visible()
            expect(note).to_have_attribute("contenteditable", "false")
            assert store.list_spaces()["active_space_id"] is None
            page.get_by_role("button", name="继续研究", exact=True).click()
            expect(note).to_have_attribute("contenteditable", "true")
            assert store.list_spaces()["active_space_id"] == 1

            # Narrow note panel remains in the viewport; close returns to material.
            page.set_viewport_size({"width": 390, "height": 844})
            panel = page.locator(".space-note-panel")
            expect(panel).to_be_visible()
            bounds = panel.bounding_box()
            assert bounds and bounds["x"] >= 0 and bounds["x"] + bounds["width"] <= 390
            page.screenshot(path=str(ROOT / "build/spaces-mobile-note.png"))
            panel.get_by_role("button", name="关闭笔记面板", exact=True).focus()
            page.keyboard.press("Shift+Tab")
            expect(note.locator('input[type="checkbox"]')).to_be_focused()
            note.press("Escape")
            expect(panel).to_have_count(0)
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            page.screenshot(path=str(ROOT / "build/spaces-mobile-material.png"), full_page=True)
            page.set_viewport_size({"width": 1440, "height": 1000})
            page.get_by_role("button", name="移出空间：苏州攻略2", exact=True).click()
            expect(page.locator(".local-content-card")).to_have_count(3)

            # A separate space has an independent note; creating it preserves the first.
            page.get_by_role("link", name="返回空间", exact=True).click()
            page.get_by_role("button", name="新建空间", exact=True).click()
            info = page.get_by_role("dialog", name="新建空间", exact=True)
            info.get_by_label("名称", exact=True).fill("杭州研究")
            info.get_by_role("button", name="创建并启用", exact=True).click()
            expect(info).to_have_count(0)
            assert store.list_spaces()["active_space_id"] == 2
            page.locator(".space-library-open").filter(has_text="杭州研究").click()
            note = page.get_by_role("textbox", name="空间笔记编辑器", exact=True)
            expect(note).to_have_text("")
            # Exercise headings, both list types, undo/redo, and supported paste.
            note.fill("格式验证")
            note.press("Control+A")
            panel = page.locator(".space-note-panel")
            panel.get_by_role("combobox", name="段落样式", exact=True).select_option("2")
            expect(note.locator("h2")).to_have_text("格式验证")
            panel.get_by_role("combobox", name="段落样式", exact=True).select_option("0")
            panel.get_by_role("button", name="项目列表", exact=True).click()
            expect(note.locator("ul:not([data-type='taskList'])")).to_have_count(1)
            panel.get_by_role("button", name="撤销", exact=True).click()
            expect(note.locator("ul:not([data-type='taskList'])")).to_have_count(0)
            panel.get_by_role("button", name="重做", exact=True).click()
            expect(note.locator("ul:not([data-type='taskList'])")).to_have_count(1)
            panel.get_by_role("button", name="编号列表", exact=True).click()
            expect(note.locator("ol")).to_have_count(1)
            note.press("Control+End")
            note.evaluate("""el => { const data = new DataTransfer(); data.setData('text/html', '<p><strong>保留加粗</strong><span style="font-size: 99px">不支持字号</span><img src="https://invalid.example/image"></p>'); el.dispatchEvent(new ClipboardEvent('paste', { clipboardData: data, bubbles: true, cancelable: true })); }""")
            expect(note).to_contain_text("保留加粗")
            assert note.locator("img, span[style*='99px']").count() == 0
            expect(page.get_by_text("已保存到本机", exact=True)).to_be_visible()
            note.press("Control+A")
            panel.get_by_role("combobox", name="段落样式", exact=True).select_option("0")
            note.fill("杭州的独立笔记")
            expect(page.get_by_text("已保存到本机", exact=True)).to_be_visible()
            assert "杭州的独立笔记" in json.dumps(store.get_space(2)["note_document"], ensure_ascii=False)
            assert "失败保留测试" in json.dumps(store.get_space(1)["note_document"], ensure_ascii=False)
            page.get_by_role("button", name="丢弃空间", exact=True).click()
            confirmation = page.get_by_role("dialog", name="确认丢弃这个空间？", exact=True)
            expect(confirmation.get_by_role("button", name="取消", exact=True)).to_be_focused()
            confirmation.get_by_role("button", name="丢弃空间", exact=True).click()
            expect(page.locator(".space-library-card")).to_have_count(1)
            assert store.list_spaces()["active_space_id"] is None
            assert store.get_space(1)["item_count"] == 3
            assert not errors, errors
            browser.close()
            print("PASS: spaces UI lifecycle, icon-only add/remove toggles, grouped and individual sources, failure retry, rich note persistence, retry, detail editor, mobile and independent drafts")
    finally:
        server.shutdown()


if __name__ == "__main__":
    main()
