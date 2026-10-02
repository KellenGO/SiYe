"""Verify the built research UI with temporary storage and an isolated fake worker."""

import asyncio
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
from api.routers.research import research_router, get_research_config, get_research_jobs
from api.routers.spaces import spaces_router
from api.services import research_jobs as jobs_module
from api.services.research_config import ResearchConfig
from api.services.research_documents import MaterialAccess, result_document
from api.services.research_materials import material, component
from api.services.spaces_store import SpacesStore, get_spaces_store


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


def main():
    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(QuietHandler, directory=str(ROOT / "webui/dist")))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    origin = f"http://127.0.0.1:{server.server_port}"
    (ROOT / "build").mkdir(exist_ok=True)
    try:
        with TemporaryDirectory(prefix="research-ui-", dir=ROOT / "build") as temp, sync_playwright() as playwright:
            store = SpacesStore(Path(temp) / "library.db")
            first = store.create_space("苏州研究")["id"]
            second = store.create_space("杭州研究")["id"]
            store.set_active(first)
            store.add_items(first, [{"platform": "xhs", "content_id": "one", "title": "苏州攻略", "content_type": "note",
                "url": "https://www.xiaohongshu.com/explore/one", "author": "测试作者", "snippet": "完整简介",
                "published_at": datetime.now(timezone.utc).isoformat(), "metrics": {}, "rank": 1}])
            config = ResearchConfig(Path(temp) / "ai.json", cipher=lambda value, decrypt=False: value)
            config.save("https://example.com", "test-model", "isolated-key")
            manager = jobs_module.ResearchJobs(config)
            manager.require_runtime = lambda: None
            jobs_module.get_session_snapshot = lambda _: {"a1": "isolated-session"}
            async def process(job, payload, credentials=None):
                if payload["mode"] == "collect":
                    job["materials"] = [material(row) for row in payload["items"]]
                    for row in job["materials"]:
                        row.update(body=component("ok", text="完整正文"), comments=component("failed", reason="测试评论读取失败"))
                    return {}
                await asyncio.sleep(1.2)
                access = MaterialAccess(job["materials"])
                for row in access.manifest():
                    for index in range(row["chunks"]):
                        access.chunk(row["key"], index)
                result = {"sections": [{"kind": "space", "title": "空间发现", "paragraphs": [
                    {"text": "AI生成的苏州研究结果", "sources": ["xhs|one"]}]}]}
                return {"document": result_document(result, job["materials"], [], access.coverage(), job["web_enabled"]),
                        "coverage": access.coverage(), "external_sources": [], "web_errors": []}
            manager.process = process
            app = FastAPI()
            app.include_router(spaces_router)
            app.include_router(research_router)
            app.dependency_overrides[get_spaces_store] = lambda: store
            app.dependency_overrides[get_research_config] = lambda: config
            app.dependency_overrides[get_research_jobs] = lambda: manager
            with TestClient(app) as client:
                def route_request(route):
                    request = route.request
                    path = urlparse(request.url).path
                    if not request.url.startswith(origin + "/"):
                        route.abort()
                    elif path.startswith(("/api/spaces", "/api/research")):
                        response = client.request(request.method, path, content=request.post_data, headers={"content-type": "application/json"})
                        if path.endswith("/note") and response.status_code >= 400:
                            raise AssertionError("Rich-note save rejected: " + request.post_data)
                        route.fulfill(status=response.status_code, body=response.content, content_type="application/json")
                    elif path.startswith("/api/"):
                        route.fulfill(json={"accounts": []} if path.endswith("accounts") else {})
                    else:
                        route.continue_()
                browser = playwright.chromium.launch(channel="msedge")
                context = browser.new_context(viewport={"width": 1440, "height": 1000}, locale="zh-CN")
                context.add_init_script("localStorage.setItem('mediacrawler_license_accepted','true');localStorage.setItem('siye_onboarding_preference_v1','completed');localStorage.setItem('mediacrawler_language','zh-CN')")
                context.route("**/*", route_request)
                page = context.new_page()
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                def open_panel():
                    edge = page.locator(".space-edge-right:visible")
                    if "is-open" not in edge.get_attribute("class"):
                        edge.get_by_role("button", name="打开笔记", exact=True).click()
                    if not edge.get_by_role("button", name="取消固定研究笔记", exact=True).count():
                        edge.get_by_role("button", name="固定研究笔记", exact=True).click()
                    panel = page.locator(".space-research:visible")
                    if not panel.get_attribute("open") == "":
                        panel.locator("summary").first.click()
                    expect(panel.get_by_label("允许联网补充", exact=True)).to_be_enabled()
                    return panel
                page.goto(origin + f"/#/spaces/{first}")
                panel = open_panel()
                web = panel.get_by_label("允许联网补充", exact=True)
                expect(web).not_to_be_checked()
                note = page.get_by_role("textbox", name="空间笔记编辑器", exact=True)
                note.fill("生成前的手写笔记")
                panel.get_by_role("button", name="生成研究笔记", exact=True).click()
                expect(panel.get_by_text("等待检查资料", exact=True)).to_be_visible()
                expect(panel.get_by_text("测试评论读取失败", exact=False)).to_be_visible()
                web.check()
                expect(panel.get_by_text("本次任务：联网补充关闭", exact=True)).to_be_visible()
                panel.get_by_role("button", name="使用这些资料开始分析", exact=True).click()
                note.press("Control+End")
                note.press_sequentially("，生成期间继续写")
                expect(panel.get_by_role("button", name="追加到笔记", exact=True)).to_be_visible(timeout=15000)
                expect(note).not_to_contain_text("AI生成的苏州研究结果")
                panel.get_by_role("button", name="追加到笔记", exact=True).click()
                expect(note).to_contain_text("生成期间继续写")
                expect(note).to_contain_text("AI生成的苏州研究结果")
                expect(page.locator(".space-note-status")).to_contain_text("已保存到本机")
                assert "AI生成的苏州研究结果" in json.dumps(store.get_space(first)["note_document"], ensure_ascii=False)
                assert page.locator(".space-note-content").bounding_box()["height"] >= 100, "AI panel leaves too little room for writing"
                expect(panel.get_by_role("button", name="已追加到笔记", exact=True)).to_be_disabled()
                expect(page.get_by_role("textbox", name="空间笔记编辑器", exact=True)).to_contain_text("AI生成的苏州研究结果")
                page.screenshot(path=str(ROOT / "build/research-desktop.png"), full_page=True)
                page.reload()
                panel = open_panel()
                expect(panel.get_by_label("允许联网补充", exact=True)).to_be_checked()
                expect(panel.get_by_role("button", name="已追加到笔记", exact=True)).to_be_disabled()
                page.goto(origin + f"/#/spaces/{second}")
                panel = open_panel()
                expect(panel.get_by_label("允许联网补充", exact=True)).not_to_be_checked()
                page.goto(origin + f"/#/spaces/{first}")
                panel = open_panel()
                page.set_viewport_size({"width": 390, "height": 844})
                expect(panel).to_be_visible()
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                page.screenshot(path=str(ROOT / "build/research-mobile.png"), full_page=True)
                assert not errors, errors
                context.close()
                browser.close()
                print("PASS: preview, missing components, frozen task preference, editing during generation, append once across reload, space-specific preference and mobile")
    finally:
        server.shutdown()


if __name__ == "__main__":
    main()
