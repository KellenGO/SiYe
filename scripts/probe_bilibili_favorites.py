"""Read at most two Bilibili favorite pages; print only non-content diagnostics.

Uses the existing logged-in application profile. Does not write the archive or
change platform favorites. A successful probe alone does not verify reorder,
move or re-collection semantics and must not enable incremental early stopping.
"""
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config
from aggregate_search.favorites_sync import BilibiliFavoritesSource
from aggregate_search.worker import _cleanup_crawler
from base.crawler_runtime import CrawlerRuntimeOptions
from media_platform.bilibili.core import BilibiliCrawler
from tools import utils


async def main():
    utils.logger.disabled = True
    config.PLATFORM = "bili"
    config.CRAWLER_TYPE = "favorites"
    config.ENABLE_CDP_MODE = False
    config.HEADLESS = True
    config.SAVE_LOGIN_STATE = True
    config.ENABLE_IP_PROXY = False
    crawler = BilibiliCrawler()

    async def probe(client):
        source = BilibiliFavoritesSource(client)
        account = await source.identity()
        folders = await source.folders(account)
        report = {"folders_valid": True, "folder_count": len(folders), "pages_checked": 0,
                  "incremental_verified": False}
        folder = next((f for f in folders if f["count"]), None)
        stamps = []
        if folder:
            for number in range(1, min(2, (folder["count"] + 19) // 20) + 1):
                page = await source.page(folder, number)
                report["pages_checked"] += 1
                stamps.extend(stamp for _, stamp in page.identities)
        report["favorite_times_available"] = bool(stamps) and all(isinstance(stamp, int) for stamp in stamps)
        report["sample_descending"] = bool(report["favorite_times_available"] and stamps == sorted(stamps, reverse=True))
        print(json.dumps(report))

    crawler.runtime_options = CrawlerRuntimeOptions(
        login_policy="fail_fast", headless=True, reuse_http_client=True,
        light_page=True, strict_errors=True, extra={"favorites_sync": probe})
    try:
        await asyncio.wait_for(crawler.start(), 90)
    except Exception as exc:
        print(json.dumps({"probe": "failed", "error_type": type(exc).__name__, "incremental_verified": False}))
        return 1
    finally:
        await _cleanup_crawler(crawler)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
