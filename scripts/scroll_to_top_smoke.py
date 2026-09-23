"""Check back-to-top on long search and history lists with simulated responses."""

import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import expect, sync_playwright


ROOT = Path(__file__).resolve().parents[1]


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


results = [
    {"platform": "xhs", "content_id": str(i), "title": f"测试结果 {i}",
     "url": f"https://www.xiaohongshu.com/explore/{i}", "author": "测试",
     "snippet": "模拟列表内容", "content_type": "note", "metrics": {}, "rank": 0}
    for i in range(60)
]
job = {
    "job_id": "mock-search", "keyword": "测试", "overall": "completed",
    "created_at": "2026-09-23T00:00:00Z", "completed_at": "2026-09-23T00:00:00Z",
    "hydration_status": "completed",
    "platforms": {"xhs": {"status": "succeeded", "result_count": len(results)}},
    "results": results,
}
views = {"items": [
    {"id": i, "key": f"xhs|{i}", "result": result,
     "first_viewed_at": "2026-09-23T00:00:00Z", "last_viewed_at": "2026-09-23T00:00:00Z",
     "view_count": 1}
    for i, result in enumerate(results)
]}


def main():
    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(QuietHandler, directory=str(ROOT / "webui/dist")))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    origin = f"http://127.0.0.1:{server.server_port}"

    def route_request(route):
        request = route.request
        url = urlsplit(request.url)
        if not request.url.startswith(origin + "/"):
            return route.abort()
        if url.path in ("/api/search/jobs/current", "/api/search/jobs/mock-search"):
            return route.fulfill(json=job)
        if url.path == "/api/history/views":
            return route.fulfill(json=views)
        if url.path == "/api/search/accounts":
            return route.fulfill(json={"accounts": []})
        if url.path == "/api/health":
            return route.fulfill(json={"status": "ok", "environment_status": "ok"})
        if url.path.startswith("/api/"):
            return route.fulfill(status=404, json={"detail": "mock unavailable"})
        return route.continue_()

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel="msedge", headless=True)
            context = browser.new_context(viewport={"width": 1280, "height": 900}, reduced_motion="reduce")
            context.add_init_script(
                "localStorage.setItem('mediacrawler_license_accepted','true');"
                "localStorage.setItem('siye_onboarding_preference_v1','completed');"
                "sessionStorage.setItem('aggregate_search_job_id','mock-search')"
            )
            context.route("**/*", route_request)
            page = context.new_page()
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))

            for route in ("search", "history"):
                page.goto(f"{origin}/#/{route}")
                expect(page.locator("article.result-row")).to_have_count(60)
                button = page.get_by_role("button", name="返回顶部", include_hidden=True)
                expect(button).to_be_hidden()
                page.evaluate("window.scrollTo(0, 120)")
                expect(button).to_be_hidden()
                page.evaluate("window.scrollTo(0, 200)")
                expect(button).to_be_visible()
                button.click()
                page.wait_for_function("window.scrollY < 5")
                expect(button).to_be_hidden()

                page.set_viewport_size({"width": 390, "height": 844})
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                page.evaluate("window.scrollTo(0, 200)")
                expect(button).to_be_visible()
                (ROOT / "build").mkdir(exist_ok=True)
                page.screenshot(path=str(ROOT / f"build/back-to-top-{route}-mobile.png"))
                button.click()
                page.wait_for_function("window.scrollY < 5")
                page.set_viewport_size({"width": 1280, "height": 900})

            assert not errors, errors
            browser.close()
    finally:
        server.shutdown()
        server.server_close()
    print("PASS search/history back-to-top at desktop and 390px")


if __name__ == "__main__":
    main()
