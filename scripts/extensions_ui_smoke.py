"""Verify real extension ZIP installation and UI lifecycle in temporary storage."""

import argparse
import asyncio
import hashlib
import json
import sys
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import urlparse

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient
from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


def main(package):
    from api.routers import extensions as extension_routes
    from api.routers.research import research_router, get_research_config, get_research_jobs
    from api.routers.spaces import spaces_router
    from api.services import extensions as extension_module
    from api.services.extensions import Extensions, ASSET_NAME
    from api.services.research_config import ResearchConfig
    from api.services.research_jobs import ResearchJobs
    from api.services.spaces_store import SpacesStore, get_spaces_store

    data = Path(package).read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    real_client = httpx.AsyncClient
    slow = True

    class ArchiveStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            for offset in range(0, len(data), 1024 * 1024):
                if slow:
                    await asyncio.sleep(.12)
                yield data[offset:offset + 1024 * 1024]

    async def provider(request):
        if request.url.host == "api.github.com":
            return httpx.Response(200, json={"tag_name": "v0.1.0", "assets": [{"name": ASSET_NAME, "size": len(data), "digest": "sha256:" + digest,
                "browser_download_url": "https://github.com/KellenGO/SiYe-AI/releases/download/v0.1.0/" + ASSET_NAME}]})
        return httpx.Response(200, stream=ArchiveStream())

    extension_module.httpx.AsyncClient = lambda **kwargs: real_client(transport=httpx.MockTransport(provider), **kwargs)
    output = ROOT / "build/extensions-ui"
    output.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(QuietHandler, directory=str(ROOT / "webui/dist")))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    origin = f"http://127.0.0.1:{server.server_port}"
    try:
        with TemporaryDirectory(prefix="extensions-ui-", dir=output) as temporary:
            root = Path(temporary)
            manager = Extensions(root / "extensions")
            extension_module.extensions = manager
            store = SpacesStore(root / "library.db")
            space = store.create_space("插件验收空间")
            identity = space["id"]
            store.add_items(identity, [{"platform": "xhs", "content_id": "fixture", "title": "保留的空间资料", "content_type": "note", "url": "https://www.xiaohongshu.com/explore/fixture", "snippet": "本地验收", "metrics": {}, "rank": 1}])
            note = {"type": "doc", "content": [{"type": "paragraph", "content": [{"type": "text", "text": "卸载后仍保留这份手写笔记"}]}]}
            store.save_note(identity, note, 1, 0)
            config = ResearchConfig(root / "data/research-ai.json", cipher=lambda value, decrypt=False: value)
            config.save("https://example.com", "fixture-model", "isolated-key")
            jobs = ResearchJobs(config)
            jobs.history_store.root.mkdir(parents=True)
            saved = jobs.history_store.root / "saved.json"
            saved.write_text("{}")
            extension_routes.research_jobs = jobs
            app = FastAPI()
            app.include_router(extension_routes.extensions_router)
            app.include_router(research_router)
            app.include_router(spaces_router)
            app.dependency_overrides[extension_routes.get_extensions] = lambda: manager
            app.dependency_overrides[get_spaces_store] = lambda: store
            app.dependency_overrides[get_research_config] = lambda: config
            app.dependency_overrides[get_research_jobs] = lambda: jobs
            with TestClient(app, base_url="http://127.0.0.1") as client, sync_playwright() as playwright:
                browser = playwright.chromium.launch(channel="msedge", headless=True)
                context = browser.new_context(viewport={"width": 1440, "height": 960})
                context.add_init_script("localStorage.setItem('mediacrawler_license_accepted','true'); localStorage.setItem('siye_onboarding_preference_v1','completed')")
                def route_request(route):
                    request = route.request
                    parsed = urlparse(request.url)
                    if not request.url.startswith(origin + "/"):
                        route.abort()
                    elif parsed.path.startswith(("/api/extensions", "/api/research", "/api/spaces")):
                        response = client.request(request.method, parsed.path + ("?" + parsed.query if parsed.query else ""), content=request.post_data, headers={"content-type": "application/json"})
                        body = response.content
                        if parsed.path == "/api/extensions/ai/assets/ui.js" and response.status_code == 200:
                            body += b';const OriginalPanel=SiYeAI.Panel; SiYeAI.Panel=(props)=>{if("editor" in props || "session" in props) throw new Error("private editor leaked to plugin"); return SiYeAIHost.react.createElement(OriginalPanel,props);};'
                        route.fulfill(status=response.status_code, body=body, headers={"content-type": response.headers.get("content-type", "application/json")})
                    elif parsed.path.startswith("/api/"):
                        route.fulfill(json={"accounts": []} if parsed.path == "/api/search/accounts" else {"status": "ok", "environment_status": "ok"} if parsed.path == "/api/health" else {})
                    else:
                        route.continue_()
                context.route("**/*", route_request)
                errors = []
                page = context.new_page()
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(origin + "/#/settings/extensions")
                expect(page.locator(".extension-state")).to_have_text("未安装")
                assert page.locator(".research-settings").count() == 0
                page.get_by_role("button", name="下载并安装", exact=True).click()
                expect(page.locator(".extension-progress")).to_contain_text("正在下载", timeout=15000)
                page.get_by_role("button", name="取消", exact=True).click()
                expect(page.locator(".extension-progress")).to_have_count(0, timeout=15000)
                assert not manager.status()["installed"]
                assert not list(manager.root.glob("install-*"))
                slow = False
                page.get_by_role("button", name="下载并安装", exact=True).click()
                expect(page.locator(".extension-state")).to_have_text("已关闭", timeout=90000)
                expect(page.get_by_role("switch")).not_to_be_checked()
                assert manager.status()["installed"] and not manager.status()["enabled"]
                page.screenshot(path=str(output / "installed-off.png"), full_page=True)
                page.get_by_role("switch").click()
                expect(page.locator(".research-settings")).to_be_visible(timeout=15000)
                assert config.credentials()["api_key"] == "isolated-key"
                page.screenshot(path=str(output / "enabled-settings.png"), full_page=True)
                workspace = context.new_page()
                workspace.on("pageerror", lambda error: errors.append(str(error)))
                workspace.goto(origin + f"/#/spaces/{identity}")
                expect(workspace.locator(".space-ai-trigger")).to_be_visible(timeout=15000)
                workspace.locator(".space-ai-trigger").click()
                expect(workspace.locator(".space-research-dialog[open]")).to_be_visible(timeout=15000)
                page.get_by_role("switch").click()
                expect(workspace.locator(".space-ai-trigger")).to_have_count(0, timeout=15000)
                expect(workspace.locator(".space-research-dialog[open]")).to_have_count(0)
                assert not workspace.evaluate("document.body.classList.contains('has-ai-sidebar')")
                page.get_by_role("button", name="卸载", exact=True).click()
                expect(page.locator(".extension-state")).to_have_text("未安装", timeout=15000)
                assert config.path.exists() and saved.exists()
                assert store.get_space(identity)["note_document"] == note
                assert len(store.get_space(identity)["items"]) == 1
                page.set_viewport_size({"width": 390, "height": 844})
                page.screenshot(path=str(output / "uninstalled-mobile.png"), full_page=True)
                assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
                assert not errors, errors
                browser.close()
                print("PASS: real ZIP download/cancel/install, default-off, plugin settings and panel, disable cleanup, uninstall preserves credentials/history/notes, 390px layout")
    finally:
        extension_module.httpx.AsyncClient = real_client
        server.shutdown()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--package", required=True)
    main(parser.parse_args().package)
