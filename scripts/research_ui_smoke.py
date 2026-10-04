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
from api.services.research_documents import MaterialAccess, answer_document
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
            store.add_items(first, [{"platform": "xhs", "content_id": identity, "title": title, "content_type": "video",
                "url": f"https://www.xiaohongshu.com/explore/{identity}", "snippet": "清单简介", "metrics": {}, "rank": index}
                for index, (identity, title) in enumerate((("two", "苏州三日游"), ("three", "苏州景点评级")), 2)])
            config = ResearchConfig(Path(temp) / "ai.json", cipher=lambda value, decrypt=False: value)
            config.save("https://example.com", "test-model", "isolated-key")
            manager = jobs_module.ResearchJobs(config)
            manager.require_runtime = lambda: None
            jobs_module.get_session_snapshot = lambda _: {"a1": "isolated-session"}
            async def process(job, payload, credentials=None):
                if payload["mode"] == "collect":
                    job["materials"] = sorted((material(row) for row in payload["items"]),
                        key=lambda row: ("xhs|one", "xhs|two", "xhs|three").index(row["key"]))
                    for row in job["materials"]:
                        row.update(body=component("ok", text="完整正文"), comments=component("failed", reason="测试评论读取失败"))
                        row["subtitles"] = component("ok", entries=[{"start": 0, "end": 522, "text": "视频讲述"}])
                        row["subtitles"]["metadata"] = {"source": "native", "duration": 522}
                        if row["key"] == "xhs|two":
                            row.update(body=component("failed", reason="正文获取失败"), comments=component("failed", reason="评论获取失败"), subtitles=component("not_applicable"))
                        if row["key"] == "xhs|three":
                            row.update(comments=component("ok", reason="最多取得 50 条评论", entries=[{"id": str(index), "text": "评论样本" * 40} for index in range(50)]),
                                subtitles=component("failed", reason="平台无字幕且本地转写不可用"))
                            row["comments"]["truncated"] = True
                    return {}
                conversation_inputs.append(payload.get("conversation", []))
                job["activity"] = [{"id": "step-1", "tool": "manifest", "message": "manifest", "status": "completed", "summary": "3 条空间资料"},
                    {"id": "step-2", "tool": "assistant", "kind": "commentary", "message": "我先查看攻略中的路线和交通信息。", "status": "completed"},
                    {"id": "step-3", "tool": "read_material", "message": "read_material", "status": "running"}]
                await asyncio.sleep(1.2)
                job["activity"][-1].update(status="completed", summary="苏州攻略 · 第 1 段 · 128 字符")
                job["materials"][0]["subtitles"]["metadata"] = {"source": "local_asr", "duration": 87}
                access = MaterialAccess(job["materials"])
                for row in access.manifest():
                    for index in range(row["sections"]["body"]["chunks"]):
                        access.chunk(row["key"], "body", index)
                for index in range(access.sections["xhs|three"]["comments"]["chunks"]):
                    access.chunk("S3", "comments", index)
                return {"document": answer_document("AI生成的苏州研究结果 [S1:body]。当前评论样本 [S3:comments]；S2 正文和 S3 视频评级不可用。", job["web_enabled"], job["materials"], [], access.coverage()),
                        "coverage": access.coverage(), "external_sources": [], "web_errors": []}
            manager.process = process
            tested_models = []
            conversation_inputs = []
            async def probe(web_enabled):
                tested_models.append(config.credentials()["model"])
                return {"connection_ok": True, "web_ok": web_enabled}
            manager.probe = probe
            fail_append = False
            app = FastAPI()
            app.include_router(spaces_router)
            app.include_router(research_router)
            app.dependency_overrides[get_spaces_store] = lambda: store
            app.dependency_overrides[get_research_config] = lambda: config
            app.dependency_overrides[get_research_jobs] = lambda: manager
            with TestClient(app, base_url="http://127.0.0.1") as client:
                def route_request(route):
                    request = route.request
                    path = urlparse(request.url).path
                    if not request.url.startswith(origin + "/"):
                        route.abort()
                    elif path.startswith(("/api/spaces", "/api/research")):
                        if fail_append and path.endswith("/note") and request.method == "PUT":
                            route.fulfill(status=503, json={"detail": "测试保存失败"})
                            return
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
                page.on("dialog", lambda event: event.accept())
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                def open_note():
                    edge = page.locator(".space-edge-right:visible")
                    if "is-open" not in edge.get_attribute("class"):
                        edge.get_by_role("button", name="打开笔记", exact=True).click()
                    if page.viewport_size["width"] > 1000 and not edge.get_by_role("button", name="取消固定研究笔记", exact=True).count():
                        edge.get_by_role("button", name="固定研究笔记", exact=True).click()
                    return page.get_by_role("textbox", name="空间笔记编辑器", exact=True)
                def open_panel():
                    panel = page.locator(".space-research:visible")
                    if not panel.count():
                        page.get_by_role("button", name="打开研究助手", exact=True).click()
                    expect(panel.get_by_label("允许联网补充", exact=True)).to_be_enabled()
                    expect(panel.get_by_role("button", name="新会话", exact=True)).to_be_enabled()
                    assert not panel.get_by_label("模型名称", exact=True).count(), "Configuration still appears inside the assistant"
                    return panel
                def last_turn(panel):
                    return panel.locator(".research-chat-turn").last
                def choose_history(panel, title):
                    toggle = panel.get_by_role("button", name="会话历史", exact=True)
                    if toggle.get_attribute("aria-expanded") != "true":
                        toggle.click()
                    sessions = page.get_by_role("complementary", name="会话历史", exact=True)
                    sessions.get_by_label("搜索会话", exact=True).fill(title)
                    sessions.locator(".research-session-list button").filter(has_text=title).click()
                    expect(sessions).to_be_visible()
                    sessions.get_by_role("button", name="关闭会话列表", exact=True).click()
                page.goto(origin + "/#/settings/ai")
                settings = page.locator(".research-settings")
                settings.get_by_label("模型名称", exact=True).fill("new-model")
                settings.get_by_role("button", name="保存并测试连接", exact=True).click()
                expect(settings.get_by_text("连接与工具调用测试通过", exact=True)).to_be_visible()
                assert tested_models == ["new-model"], "Connection test ignored current inputs"
                settings.get_by_label("服务商", exact=True).select_option("gemini")
                expect(settings.get_by_label("服务基础地址（Base URL）", exact=True)).to_have_value("https://generativelanguage.googleapis.com/v1beta/openai")
                expect(settings.get_by_role("button", name="保存配置", exact=True)).to_be_disabled()
                settings.get_by_label("API Key", exact=True).fill("isolated-gemini-key")
                settings.get_by_role("button", name="保存配置", exact=True).click()
                expect(settings.get_by_text("配置已保存", exact=True)).to_be_visible()
                assert config.credentials()["protocol"] == "openai"
                settings.get_by_label("服务商", exact=True).select_option("deepseek")
                settings.get_by_label("API Key", exact=True).fill("isolated-deepseek-key")
                settings.get_by_role("button", name="保存配置", exact=True).click()
                expect(settings.get_by_text("配置已保存", exact=True)).to_be_visible()
                page.screenshot(path=str(ROOT / "build/research-settings.png"), full_page=True)
                page.goto(origin + f"/#/spaces/{first}")
                full_width = page.locator(".app-main-inner").bounding_box()["width"]
                panel = open_panel()
                assert page.locator(".app-main-inner").bounding_box()["width"] <= full_width - 450, "Sidebar did not compress the main content"
                assert page.locator(".app-header").bounding_box()["width"] <= full_width - 450, "Sidebar did not compress the header"
                bounds = page.locator(".space-research-dialog").bounding_box()
                assert bounds["y"] == 0 and bounds["height"] == 1000, "Docked assistant leaves a gap at the top"
                assert not page.locator(".space-research-dialog").evaluate("element => element.matches(':modal')"), "Desktop assistant blocks the main content"
                panel.get_by_role("button", name="会话历史", exact=True).click()
                sessions = page.get_by_role("complementary", name="会话历史", exact=True)
                assert sessions.bounding_box()["x"] >= panel.bounding_box()["x"] + panel.bounding_box()["width"] - 1, "Docked history covers the chat composer"
                panel.get_by_role("button", name="新会话", exact=True).click()
                expect(sessions).to_be_visible()
                assert page.locator(".space-research-dialog").bounding_box()["width"] < 1000, "New conversation unexpectedly expanded the sidebar"
                sessions.get_by_role("button", name="关闭会话列表", exact=True).click()
                resize = panel.locator("xpath=..").get_by_role("separator", name="调整 AI 侧栏宽度", exact=True)
                box = resize.bounding_box()
                width_before = panel.bounding_box()["width"]
                page.mouse.move(box["x"] + 3, box["y"] + 100)
                page.mouse.down()
                page.mouse.move(box["x"] - 140, box["y"] + 100, steps=8)
                page.mouse.up()
                assert panel.bounding_box()["width"] >= width_before + 135, "Drag did not resize the sidebar"
                assert page.locator(".app-main-inner").bounding_box()["width"] <= full_width - width_before - 135, "Dragging did not resize the main content"
                resize.focus()
                page.keyboard.press("ArrowRight")
                assert panel.bounding_box()["width"] < width_before + 140
                panel.get_by_role("button", name="展开 AI 工作区", exact=True).click()
                bounds = page.locator(".space-research-dialog").bounding_box()
                assert bounds == {"x": 0, "y": 0, "width": 1440, "height": 1000}, "Expanded workspace does not fill the viewport"
                assert page.evaluate("document.documentElement.scrollHeight === innerHeight"), "Hidden main content leaves scroll space below fullscreen"
                assert not page.locator(".space-research-dialog").evaluate("element => element.matches(':modal')"), "Desktop expansion introduces a modal backdrop"
                sessions = page.get_by_role("complementary", name="会话历史", exact=True)
                assert sessions.bounding_box()["x"] >= panel.bounding_box()["x"] + panel.bounding_box()["width"] - 1, "Sessions cover the expanded chat"
                sessions.get_by_role("button", name="新会话", exact=True).click()
                expect(sessions).to_be_visible()
                expect(panel.get_by_label("给 AI 发送消息", exact=True)).to_be_focused()
                panel.get_by_role("button", name="新会话", exact=True).click()
                expect(sessions).to_be_visible()
                assert page.locator(".space-research-dialog").bounding_box() == bounds, "New conversation changed the layout"
                page.screenshot(path=str(ROOT / "build/research-new-fullscreen.png"), full_page=True)
                box = resize.bounding_box()
                page.mouse.move(box["x"] + 3, box["y"] + 100)
                page.mouse.down()
                page.mouse.move(box["x"] + 603, box["y"] + 100, steps=12)
                page.mouse.up()
                assert page.locator(".space-research-dialog").bounding_box()["width"] == 840, "Fullscreen workspace cannot be resized back to a dock"
                assert page.locator(".app-main-inner").bounding_box()["width"] == 600, "Main content did not reclaim the dragged width"
                expect(sessions).to_be_visible()
                page.screenshot(path=str(ROOT / "build/research-docked-history.png"), full_page=True)
                panel.get_by_role("button", name="展开 AI 工作区", exact=True).click()
                panel.get_by_role("button", name="收起为侧栏", exact=True).click()
                sessions.get_by_role("button", name="关闭会话列表", exact=True).click()
                assert not page.locator(".space-research-dialog").evaluate("element => element.matches(':modal')")
                assert not panel.locator(".space-research-steps").count(), "Old workflow still replaces the chat"
                web = panel.get_by_label("允许联网补充", exact=True)
                expect(web).not_to_be_checked()
                note = open_note()
                note.fill("生成前的手写笔记")
                panel.get_by_label("给 AI 发送消息", exact=True).fill("苏州三天旅行怎么安排？")
                panel.get_by_role("button", name="发送消息", exact=True).click()
                expect(panel.get_by_text("等待检查资料", exact=True)).to_be_visible()
                expect(panel.locator(".research-user-message")).to_contain_text("苏州三天旅行怎么安排？")
                if panel.locator(".research-tool-trace").get_attribute("open") is None:
                    panel.locator(".research-tool-trace > summary").click()
                if panel.locator(".research-material-details").get_attribute("open") is None:
                    panel.locator(".research-material-details > summary").click()
                expect(panel.get_by_text("测试评论读取失败", exact=False)).to_be_visible()
                expect(panel.locator(".space-research-tags").first).to_contain_text("08:42 · 平台原生")
                web.check()
                expect(last_turn(panel).locator(".research-message-label")).to_contain_text("仅空间资料")
                panel.get_by_role("button", name="继续分析", exact=True).click()
                note.press("Control+End")
                note.press_sequentially("，生成期间继续写")
                expect(last_turn(panel).get_by_role("button", name="追加到笔记", exact=True)).to_be_visible(timeout=15000)
                expect(last_turn(panel).locator(".space-research-preview a").first).to_have_attribute("href", "https://www.xiaohongshu.com/explore/one")
                expect(last_turn(panel).locator(".space-research-preview")).to_contain_text("S1 评论：测试评论读取失败")
                expect(last_turn(panel).locator(".space-research-preview")).to_contain_text("字幕已读 0/1")
                expect(last_turn(panel).locator(".space-research-tags").first).to_contain_text("01:27 · 本地 AI 转写", timeout=10000)
                reading = last_turn(panel).locator(".space-research-materials article")
                expect(reading.nth(1)).to_contain_text("正文 未取得可读内容")
                expect(reading.nth(2)).to_contain_text("AI 已读完当前 50 条样本（2/2 段）")
                expect(reading.nth(2)).to_contain_text("平台未完整获取")
                expect(last_turn(panel).locator(".space-research-preview a").filter(has_text="[S3:comments]").first).to_have_attribute("href", "https://www.xiaohongshu.com/explore/three")
                expect(note).not_to_contain_text("AI生成的苏州研究结果")
                expect(page.locator(".space-note-status")).to_contain_text("已保存到本机")
                fail_append = True
                last_turn(panel).get_by_role("button", name="追加到笔记", exact=True).click()
                expect(last_turn(panel).get_by_role("button", name="重试保存笔记", exact=True)).to_be_enabled()
                expect(panel.get_by_role("alert")).to_contain_text("保存失败")
                assert "AI生成的苏州研究结果" not in json.dumps(store.get_space(first)["note_document"], ensure_ascii=False)
                fail_append = False
                page.reload()
                panel = open_panel()
                assert panel.bounding_box()["width"] >= 670, "Saved sidebar width was lost after reload"
                note = open_note()
                expect(note).not_to_contain_text("AI生成的苏州研究结果")
                last_turn(panel).get_by_role("button", name="追加到笔记", exact=True).click()
                expect(note).to_contain_text("生成期间继续写")
                expect(note).to_contain_text("AI生成的苏州研究结果")
                expect(page.locator(".space-note-status")).to_contain_text("已保存到本机")
                first_conversation = list(manager.jobs.values())[-1]["conversation_id"]
                panel.get_by_label("给 AI 发送消息", exact=True).fill("交通预算是多少？")
                panel.get_by_role("button", name="发送消息", exact=True).click()
                expect(last_turn(panel).get_by_text("正在分析资料", exact=True)).to_be_visible()
                assert list(manager.jobs.values())[-1]["conversation_id"] == first_conversation
                expect(panel.get_by_role("button", name="新会话", exact=True)).to_be_disabled()
                expect(last_turn(panel).get_by_role("button", name="追加到笔记", exact=True)).to_be_visible(timeout=15000)
                expect(panel.locator(".research-chat-turn")).to_have_count(2)
                fail_append = True
                last_turn(panel).get_by_role("button", name="追加到笔记", exact=True).click()
                expect(last_turn(panel).get_by_role("button", name="重试保存笔记", exact=True)).to_be_enabled()
                fail_append = False
                last_turn(panel).get_by_role("button", name="重试保存笔记", exact=True).click()
                expect(last_turn(panel).get_by_role("button", name="已追加到笔记", exact=True)).to_be_disabled()
                assert json.dumps(store.get_space(first)["note_document"], ensure_ascii=False).count("AI生成的苏州研究结果") == 2, "Retry inserted the same result twice"
                assert conversation_inputs[0] == [] and conversation_inputs[1][0]["answer"], "Follow-up lost prior answers"
                assert page.locator(".space-note-content").bounding_box()["height"] >= 300
                expect(panel.get_by_label("工具调用过程", exact=True).last).to_be_visible()
                expect(panel.get_by_text("我先查看攻略中的路线和交通信息。", exact=True).last).to_be_visible()
                panel.locator(".research-tool-step > summary").last.click()
                expect(panel.get_by_text("苏州攻略 · 第 1 段 · 128 字符", exact=True).last).to_be_visible()
                page.screenshot(path=str(ROOT / "build/research-desktop.png"), full_page=True)
                page.locator(".space-research-dialog").screenshot(path=str(ROOT / "build/research-chat.png"))
                panel.get_by_role("button", name="展开 AI 工作区", exact=True).click()
                page.screenshot(path=str(ROOT / "build/research-expanded.png"), full_page=True)
                panel.get_by_role("button", name="收起为侧栏", exact=True).click()
                page.get_by_role("complementary", name="会话历史", exact=True).get_by_role("button", name="关闭会话列表", exact=True).click()
                panel.get_by_role("button", name="会话历史", exact=True).click()
                page.screenshot(path=str(ROOT / "build/research-sessions.png"), full_page=True)
                page.get_by_role("complementary", name="会话历史", exact=True).get_by_role("button", name="关闭会话列表", exact=True).click()
                panel.get_by_role("button", name="会话历史", exact=True).click()
                sessions = page.get_by_role("complementary", name="会话历史", exact=True)
                sessions.get_by_label("搜索会话", exact=True).fill("苏州")
                sessions.get_by_role("button", name="新会话", exact=True).click()
                expect(sessions).to_be_visible()
                expect(sessions.get_by_label("搜索会话", exact=True)).to_have_value("苏州")
                expect(panel.get_by_label("给 AI 发送消息", exact=True)).to_have_value("")
                expect(panel.locator(".research-chat-turn")).to_have_count(0)
                sessions.get_by_role("button", name="关闭会话列表", exact=True).click()
                panel.get_by_role("button", name="总结这些资料的关键结论", exact=True).click()
                expect(panel.get_by_label("给 AI 发送消息", exact=True)).to_have_value("总结这些资料的关键结论")
                panel.get_by_role("button", name="发送消息", exact=True).click()
                expect(panel.get_by_text("等待检查资料", exact=True)).to_be_visible()
                panel.get_by_role("button", name="停止回答", exact=True).click()
                expect(panel.get_by_text("研究已取消", exact=True)).to_be_visible()
                assert list(manager.jobs.values())[-1]["conversation_id"] != first_conversation
                panel.get_by_label("给 AI 发送消息", exact=True).fill("重新开始")
                panel.get_by_role("button", name="发送消息", exact=True).click()
                expect(panel.get_by_text("等待检查资料", exact=True)).to_be_visible()
                expect(panel.locator(".research-chat-turn")).to_have_count(1)
                panel.get_by_role("button", name="停止回答", exact=True).click()
                expect(panel.get_by_text("研究已取消", exact=True)).to_be_visible()
                choose_history(panel, "苏州三天旅行")
                expect(panel.locator(".research-chat-turn")).to_have_count(2)
                expect(panel.locator(".research-chat-turn").first).to_contain_text("AI生成的苏州研究结果")
                expect(last_turn(panel).get_by_role("button", name="已追加到笔记", exact=True)).to_be_disabled()
                page.reload()
                panel = open_panel()
                choose_history(panel, "苏州三天旅行")
                expect(panel.get_by_label("允许联网补充", exact=True)).to_be_checked()
                expect(last_turn(panel).get_by_role("button", name="已追加到笔记", exact=True)).to_be_disabled()
                page.goto(origin + f"/#/spaces/{second}")
                panel = open_panel()
                expect(panel.get_by_label("允许联网补充", exact=True)).not_to_be_checked()
                page.goto(origin + f"/#/spaces/{first}")
                panel = open_panel()
                page.set_viewport_size({"width": 390, "height": 844})
                expect(panel).to_be_visible()
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                page.screenshot(path=str(ROOT / "build/research-mobile.png"), full_page=True)
                assert page.locator(".space-research-dialog").evaluate("element => element.matches(':modal')"), "Resizing removed the dialog from the top layer"
                bounds = panel.bounding_box()
                assert bounds["width"] >= 380 and bounds["height"] >= 830
                assert panel.locator(".research-input-card").bounding_box()["width"] >= bounds["width"] - 40, "Global page footer styles narrowed the composer"
                footer = panel.locator(".space-research-footer").bounding_box()
                assert footer["y"] + footer["height"] <= 845
                page.set_viewport_size({"width": 320, "height": 640})
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                assert panel.locator(".space-research-footer").bounding_box()["y"] + panel.locator(".space-research-footer").bounding_box()["height"] <= 641
                panel.get_by_role("button", name="新会话", exact=True).focus()
                page.keyboard.press("Tab")
                assert page.locator(".space-research-dialog").evaluate("element => element.contains(document.activeElement)")
                page.keyboard.press("Escape")
                expect(page.locator(".space-research-dialog")).not_to_be_visible()
                expect(open_note()).to_be_visible()
                assert not errors, errors
                context.close()
                browser.close()
                print("PASS: three-source collection/reading coverage and section links, persistent history, fullscreen/dock resizing, provider settings, source review, save recovery, per-turn append, tool steps and mobile")
    finally:
        server.shutdown()


if __name__ == "__main__":
    main()
