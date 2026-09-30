"""Real local API + browser: hold later results until the first card is visible.

Uses fake workers and a temporary library; never contacts social platforms.
Build webui before running this script.
"""
import asyncio
import json
import os
import socket
import sys
import threading
import time
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from urllib.parse import urlparse
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    from fastapi import FastAPI
    from fastapi.staticfiles import StaticFiles
    from playwright.sync_api import sync_playwright, expect
    import uvicorn

    (ROOT / "build").mkdir(exist_ok=True)
    with TemporaryDirectory(prefix="first-result-", dir=ROOT / "build") as temp:
        with patch.dict(os.environ, {"SIYE_DATA_DIR": temp}):
            from api.routers import search
            from api.routers.library import library_router
            from api.services.library_store import LibraryStore, get_library_store
            from api.services import search_job_manager as sjm
            from aggregate_search.models import UnifiedSearchResult

            manager = sjm.SearchJobManager()
            first_gate, rest_gate = asyncio.Event(), asyncio.Event()
            waiting = threading.Event()
            errors = []

            async def worker(job, platform):
                job.set_platform_status(platform, "running")
                def emit(content_id, title):
                    job.add_result(platform, UnifiedSearchResult(
                        platform=platform, content_id=content_id, title=title,
                        url="https://example.test/content"))
                if platform == "xhs":
                    await first_gate.wait()
                    emit("first", "首条已经可以阅读")
                await rest_gate.wait()
                emit(platform + "-later", "随后补到的旅行攻略" if platform == "xhs" else "另一平台返回的硬件评测")
                job.set_platform_status(platform, "succeeded")

            app = FastAPI()
            app.include_router(search.search_router)
            app.include_router(library_router)
            store = LibraryStore(Path(temp) / "library.db")
            app.dependency_overrides[get_library_store] = lambda: store

            @app.middleware("http")
            async def observe_wait(request, call_next):
                if request.query_params.get("wait_seconds") == "15":
                    waiting.set()
                return await call_next(request)

            @app.post("/__test/release/{stage}")
            async def release(stage: str):
                if stage == "reset":
                    first_gate.clear()
                    rest_gate.clear()
                    waiting.clear()
                    return {"ok": True}
                (first_gate if stage == "first" else rest_gate).set()
                return {"ok": True}

            @app.on_event("shutdown")
            async def shutdown():
                await manager.cleanup()

            app.mount("/", StaticFiles(directory=ROOT / "webui/dist", html=True))
            sock = socket.socket()
            sock.bind(("127.0.0.1", 0))
            origin = f"http://127.0.0.1:{sock.getsockname()[1]}"
            server = uvicorn.Server(uvicorn.Config(app, log_level="error"))

            def route_request(route):
                url = urlparse(route.request.url)
                if not route.request.url.startswith(origin + "/"):
                    route.abort()
                elif url.path.startswith(("/api/search/jobs", "/api/library/")):
                    route.continue_()
                elif url.path.startswith("/api/"):
                    data = {"accounts": []} if url.path == "/api/search/accounts" else {}
                    if url.path == "/api/health":
                        data = {"status": "ok", "environment_status": "ok"}
                    route.fulfill(json=data)
                else:
                    route.continue_()

            with patch.object(search, "search_job_manager", manager), \
                 patch.object(manager, "_run_worker", worker), \
                 patch.object(sjm, "search_login_block", lambda p: None), \
                 patch.object(sjm, "hydration_candidates", lambda rows: []), \
                 patch.object(sjm, "metric_candidates", lambda rows: []):
                thread = threading.Thread(target=lambda: server.run(sockets=[sock]), daemon=True)
                thread.start()
                try:
                    deadline = time.monotonic() + 10
                    while not server.started and time.monotonic() < deadline:
                        time.sleep(0.02)
                    assert server.started, "local API did not start"
                    with sync_playwright() as p:
                        browser = p.chromium.launch(channel="msedge")
                        context = browser.new_context(viewport={"width": 1280, "height": 900})
                        context.add_init_script("""
                            localStorage.setItem('mediacrawler_license_accepted', 'true');
                            localStorage.setItem('siye_onboarding_preference_v1', 'completed');
                            localStorage.setItem('aggregate_search_platform_pref', '["xhs","douyin"]');
                        """)
                        context.route("**/*", route_request)
                        page = context.new_page()
                        page.on("pageerror", lambda error: errors.append(str(error)))
                        page.goto(origin + "/#/search")
                        timings = []
                        for attempt in range(2):
                            if attempt:
                                page.get_by_role("tab", name="抖音").click()
                                page.get_by_role("textbox", name="结果内关键词").fill("不会命中的旧筛选")
                                expect(page.locator(".local-content-card")).to_have_count(0)
                                with urlopen(Request(origin + "/__test/release/reset", method="POST")) as response:
                                    assert response.status == 200
                            page.locator(".search-box input").fill(f"即时展示测试{attempt}")
                            page.locator(".search-box button[type=submit]").click()
                            deadline = time.monotonic() + 5
                            while not waiting.is_set() and time.monotonic() < deadline:
                                page.wait_for_timeout(20)
                            assert waiting.is_set(), "frontend never opened the change-wait request"
                            started = time.perf_counter()
                            with urlopen(Request(origin + "/__test/release/first", method="POST")) as response:
                                assert response.status == 200
                            expect(page.locator(".local-content-card")).to_have_count(1)
                            expect(page.get_by_role("button", name="查看内容信息：首条已经可以阅读", exact=True)).to_be_visible()
                            timings.append(round((time.perf_counter() - started) * 1000))
                            assert not rest_gate.is_set()
                            assert not manager._active_job.task.done()
                            assert all(s.status == "running" for s in manager._active_job.platforms_state.values())
                            with urlopen(Request(origin + "/__test/release/rest", method="POST")) as response:
                                assert response.status == 200
                            expect(page.locator(".local-content-card")).to_have_count(3)
                        assert not errors, errors
                        browser.close()
                        print(json.dumps({"result": "passed", "first_item_to_visible_ms": timings,
                            "checks": ["first card visible while both platforms run",
                                       "later items append", "new keyword resets tab/filter",
                                       "real API change wait", "no platform traffic"]}))
                finally:
                    server.should_exit = True
                    thread.join(timeout=20)
                    sock.close()


if __name__ == "__main__":
    main()
