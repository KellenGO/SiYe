"""Read at most two favorite pages and report only structural diagnostics.

Uses the existing Douyin profile; never writes the archive or platform favorites.
"""
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config
from aggregate_search.worker import _cleanup_crawler
from base.browser_runtime import configure_browser_temp
from base.crawler_runtime import CrawlerRuntimeOptions
from media_platform.douyin.core import DouYinCrawler
from tools import utils


async def main():
    configure_browser_temp()
    utils.logger.disabled = True
    config.PLATFORM = "dy"
    config.CRAWLER_TYPE = "favorites"
    config.ENABLE_CDP_MODE = False
    config.HEADLESS = True
    config.SAVE_LOGIN_STATE = True
    config.ENABLE_IP_PROXY = False
    crawler = DouYinCrawler()

    async def probe(client):
        page_observations = []
        if "--self-page" in sys.argv:
            def observe(response):
                from urllib.parse import urlparse
                path = urlparse(response.url).path
                if path in ("/aweme/v1/web/user/profile/self/", "/aweme/v1/web/aweme/listcollection/"):
                    page_observations.append({"endpoint": "identity" if "profile/self" in path else "collection", "http_status": response.status})
            client.playwright_page.on("response", observe)
            await client.playwright_page.goto("https://www.douyin.com/user/self?showTab=collection", wait_until="domcontentloaded", timeout=30000)
            await client.playwright_page.wait_for_timeout(5000)
        info = {} if "--skip-identity" in sys.argv or "--page-only" in sys.argv else await client.get("/aweme/v1/web/user/profile/self/", {"aid": "6383"})
        user = info.get("user") if isinstance(info, dict) else None
        report = {
            "identity_status": info.get("status_code") if isinstance(info, dict) else None,
            "identity_present": isinstance(user, dict) and bool(user.get("sec_uid") or user.get("uid")),
            "identity_user_object": isinstance(user, dict),
            "page_markers": await client.playwright_page.evaluate("""() => ({
                login_marker: localStorage.getItem('HasUserLogin') === '1',
                self_redirected: /^\\/user\\/(?!self(?:[/?#]|$))[^/?#]+/.test(location.pathname),
                self_link: Array.from(document.querySelectorAll('a[href]')).some(a =>
                    /\\/user\\/(self|MS4wLj)/.test(a.getAttribute('href') || '') &&
                    /^(我|我的|个人主页)$/.test((a.innerText || '').trim()))
            })"""),
            "pages": [],
            "page_observations": page_observations,
        }
        if "--page-only" in sys.argv or "--identity-only" in sys.argv:
            print(json.dumps(report))
            return
        cursor = 0
        seen_ids = set()
        for _ in range(2 if report["identity_present"] and report["identity_status"] == 0 else 1):
            result = await client.get_collected_awemes(cursor, 20)
            rows = result.get("aweme_list") if isinstance(result, dict) else None
            more = result.get("has_more") if isinstance(result, dict) else None
            next_cursor = result.get("cursor") if isinstance(result, dict) else None
            if next_cursor is None and isinstance(result, dict):
                next_cursor = result.get("max_cursor")
            try:
                next_cursor = int(next_cursor)
            except (TypeError, ValueError):
                next_cursor = None
            ids = [str(row.get("aweme_id")) for row in rows if isinstance(row, dict) and row.get("aweme_id")] if isinstance(rows, list) else []
            report["pages"].append({
                "status": result.get("status_code") if isinstance(result, dict) else None,
                "list_valid": isinstance(rows, list),
                "count": len(rows) if isinstance(rows, list) else None,
                "ids_present": isinstance(rows, list) and all(isinstance(row, dict) and bool(row.get("aweme_id")) for row in rows),
                "has_more": more if more in (True, False, 0, 1) else None,
                "cursor_advances": next_cursor is not None and next_cursor != cursor,
                "ids_unique": len(ids) == len(set(ids)) and not seen_ids.intersection(ids),
            })
            if not isinstance(result, dict) or result.get("status_code") != 0 or not isinstance(rows, list) or more not in (True, False, 0, 1) or not more or next_cursor is None or next_cursor == cursor:
                break
            seen_ids.update(ids)
            cursor = next_cursor
            await asyncio.sleep(2)
        print(json.dumps(report))

    crawler.runtime_options = CrawlerRuntimeOptions(
        login_policy="fail_fast", headless=True, reuse_http_client=True,
        light_page=True, strict_errors=True, extra={"favorites_sync": probe})
    try:
        await asyncio.wait_for(crawler.start(), 90)
    except Exception as exc:
        print(json.dumps({"probe": "failed", "error_type": type(exc).__name__,
                          "http_status": getattr(exc, "http_status", None),
                          "platform_code": getattr(exc, "platform_code", None),
                          "safe_code": getattr(exc, "safe_code", None)}))
        return 1
    finally:
        await _cleanup_crawler(crawler)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
