from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from aggregate_search.adapters import BilibiliAdapter, ZhihuAdapter
from aggregate_search.models import WorkerRequest
from api.schemas.favorites import FavoritesJobRequest
from api.services.favorites_job_manager import _Job
from media_platform.bilibili.core import BilibiliCrawler
from media_platform.douyin.core import DouYinCrawler
from media_platform.xhs.core import XiaoHongShuCrawler
from media_platform.zhihu.core import ZhihuCrawler


def test_favorites_request_contract_is_bounded_and_unique():
    assert FavoritesJobRequest().limit_per_platform == 20
    # 每个平台的目标总量可以到 100（分页逐页读取，不是单次请求 100 条）
    assert FavoritesJobRequest(limit_per_platform=100).limit_per_platform == 100
    assert WorkerRequest(job_id="j", mode="favorites", platform="xhs").mode == "favorites"
    with pytest.raises(ValidationError):
        FavoritesJobRequest(platforms=["xhs", "xhs"])
    with pytest.raises(ValidationError):
        FavoritesJobRequest(limit_per_platform=101)


def test_folder_metadata_survives_bilibili_and_zhihu_adaptation():
    bili = BilibiliAdapter().adapt([{
        "bvid": "BV1test", "title": "视频", "cover": "https://i0.hdslb.com/a.jpg",
        "upper": {"name": "UP"},
        # 收藏夹列表的真实形状：收藏数在 cnt_info.collect（不是详情接口的 favorite）。
        "cnt_info": {"play": 12, "danmaku": 3, "collect": 5},
        "_collection_name": "稍后学习",
    }])[0]
    assert bili.author == "UP"
    assert bili.metrics == {"view_count": 12, "danmaku_count": 3, "collect_count": 5}
    assert bili.collection_names == ["稍后学习"]

    zhihu = ZhihuAdapter().adapt([{
        "id": 8, "type": "answer", "question": {"id": 7}, "title": "回答",
        "author": {"name": "答主"}, "_collection_name": "产品思考",
    }])[0]
    assert zhihu.url.endswith("/question/7/answer/8")
    assert zhihu.collection_names == ["产品思考"]


def test_job_snapshot_does_not_regress_metrics_saved_locally():
    """本次同步只拿到列表字段时，内存结果不能覆盖库里那份完整快照。"""
    from api.services.favorites_job_manager import _merge_result

    previous = BilibiliAdapter().adapt([{"bvid": "BV1", "title": "一"}])[0]
    previous.metrics = {"view_count": 100, "coin_count": 2}
    previous.metrics_status = "complete"
    previous.metrics_updated_at = 123.0
    current = previous.model_copy(update={"metrics": {"view_count": 110},
                                          "metrics_status": "failed",
                                          "metrics_updated_at": None})

    merged = _merge_result(previous, current)
    assert merged.metrics == {"view_count": 110, "coin_count": 2}
    assert merged.metrics_status == "complete"
    assert merged.metrics_updated_at == 123.0


def test_favorites_job_interleaves_platforms_and_preserves_partial_results():
    job = _Job(FavoritesJobRequest(platforms=["xhs", "bilibili"], limit_per_platform=2))
    job.items["xhs"] = [BilibiliAdapter().adapt([{"bvid": "x", "title": "占位"}])[0].model_copy(update={"platform": "xhs"})]
    job.items["bilibili"] = BilibiliAdapter().adapt([
        {"bvid": "b1", "title": "一"}, {"bvid": "b2", "title": "二"}])
    job.platforms["xhs"].status = "succeeded"
    job.platforms["bilibili"].status = "failed"
    assert job.response().overall == "running"
    from datetime import datetime, timezone
    job.completed_at = datetime.now(timezone.utc)
    response = job.response()
    assert response.overall == "partial"
    assert [item.content_id for item in response.results] == ["x", "b1", "b2"]


@pytest.mark.asyncio
async def test_xhs_and_douyin_favorites_are_bounded():
    xhs_batches = []

    class XhsPage:
        """Stand-in for the loaded SPA page that carries the account id."""

        async def evaluate(self, _script):
            return "self-user-id"

    xhs = SimpleNamespace(
        xhs_client=SimpleNamespace(get_collected_notes=lambda *_: None),
        context_page=XhsPage(),
        _result_limit=lambda: 2,
        _result_sink_call=xhs_batches.append,
    )
    seen_params = []

    async def xhs_page(cursor, num, user_id):
        seen_params.append((cursor, num, user_id))
        return {"notes": [{"note_id": "1"}, {"note_id": "2"}, {"note_id": "3"}],
                "has_more": True, "cursor": "next"}

    xhs.xhs_client.get_collected_notes = xhs_page
    await XiaoHongShuCrawler.fetch_favorites(xhs)
    assert [item["note_id"] for item in xhs_batches[0]] == ["1", "2"]
    # v2 collect/page 必须带上当前账号自己的 user_id，否则平台回 -9109。
    assert seen_params == [("", 2, "self-user-id")]

    dy_batches = []
    dy = SimpleNamespace(
        dy_client=SimpleNamespace(), _result_limit=lambda: 1,
        _result_sink_call=dy_batches.append,
    )

    async def dy_page(*_):
        return {"aweme_list": [{"aweme_id": "a"}, {"aweme_id": "b"}], "has_more": 1, "cursor": 10}
    dy.dy_client.get_collected_awemes = dy_page
    await DouYinCrawler.fetch_favorites(dy)
    assert [item["aweme_id"] for item in dy_batches[0]] == ["a"]


@pytest.mark.asyncio
async def test_xhs_favorites_without_account_id_fails_loudly():
    """没有账号 id 时必须给出明确错误，而不是静默返回空列表。"""
    from media_platform.xhs.exception import DataFetchError

    class NoStatePage:
        async def evaluate(self, _script):
            return ""

    xhs = SimpleNamespace(
        xhs_client=SimpleNamespace(), context_page=NoStatePage(),
        _result_limit=lambda: 5, _result_sink_call=lambda *_: None,
    )
    with pytest.raises(DataFetchError):
        await XiaoHongShuCrawler.fetch_favorites(xhs)


def test_xhs_collect_feed_cover_and_chinese_counts_are_normalized():
    """收藏列表的封面藏在 cover.info_list，互动数是「10万」这种中文单位。"""
    from aggregate_search.adapters import XhsAdapter

    result = XhsAdapter().adapt([{
        "note_id": "n1",
        "display_title": "标题",
        "type": "video",
        "cover": {
            "width": 2880,
            "height": 3839,
            "info_list": [
                {"image_scene": "WB_PRV",
                 "url": "http://sns-webpic-qc.xhscdn.com/a.jpg"},
            ],
        },
        "interact_info": {"liked": True, "liked_count": "10万"},
        "user": {"user_id": "u1", "nickname": "作者"},
    }])[0]

    assert result.cover_url == "http://sns-webpic-qc.xhscdn.com/a.jpg"
    assert result.metrics == {"like_count": 100000}
    assert result.content_id == "n1"
    assert result.content_type == "video"
    assert result.title == "标题"
    assert result.author == "作者"
    assert result.url == "https://www.xiaohongshu.com/explore/n1"


@pytest.mark.parametrize("raw,expected", [
    ("10万", 100000), ("10万+", 100000), ("1.2万", 12000), ("1亿", 100000000),
    ("1180", 1180), (220, 220), ("", 0), ("点赞", 0), (None, 0),
])
def test_xhs_count_parsing_covers_chinese_units(raw, expected):
    from aggregate_search.adapters import XhsAdapter

    assert XhsAdapter._parse_count(raw) == expected


@pytest.mark.asyncio
async def test_latest_favorites_snapshot_can_be_restored(tmp_path, monkeypatch):
    """页面重新进入时应能取回上一次结果，不必重新同步。"""
    from api.services.favorites_job_manager import FavoritesJobManager
    from api.services.remote_favorites_store import RemoteFavoritesStore

    store = RemoteFavoritesStore(tmp_path / "library.db")
    monkeypatch.setattr("api.services.favorites_job_manager.get_remote_favorites_store", lambda: store)

    manager = FavoritesJobManager()
    assert await manager.latest() is None

    job = _Job(FavoritesJobRequest(platforms=["xhs"], limit_per_platform=1))
    job.platforms["xhs"].status = "succeeded"
    job.platforms["xhs"].result_count = 1
    manager._recent = job

    restored = await manager.latest()
    assert restored is not None
    assert restored.job_id == job.job_id
    assert restored.platforms["xhs"].status == "succeeded"


@pytest.mark.asyncio
async def test_latest_snapshot_deduplicates_legacy_and_account_archives(tmp_path, monkeypatch):
    """同一内容在旧 default 与账号归档中各有一份时，页面只能收到一条。"""
    from api.services.favorites_job_manager import FavoritesJobManager
    from api.services.remote_favorites_store import RemoteFavoritesStore

    store = RemoteFavoritesStore(tmp_path / "library.db")
    monkeypatch.setattr("api.services.favorites_job_manager.get_remote_favorites_store", lambda: store)
    legacy = {"platform": "bilibili", "content_id": "BV1", "content_type": "video",
              "title": "历史快照", "author": "UP", "url": "https://example.test/BV1"}
    current = {**legacy, "title": "账号快照"}
    store.save_platform("bilibili", [legacy], status="succeeded")
    store.begin_scan("bilibili", "uid:1", [{"id": "f", "name": "收藏夹"}], "full")
    store.save_sync_page("bilibili", "uid:1", 1, "f", "收藏夹", 1, "page-1", [current], True, 20)

    restored = await FavoritesJobManager().latest()

    assert restored is not None
    assert [(item.platform, item.content_id) for item in restored.results] == [("bilibili", "BV1")]
    assert restored.results[0].title == "账号快照"


@pytest.mark.asyncio
async def test_folder_fetchers_flatten_and_report_duplicate_membership(monkeypatch):
    from unittest.mock import AsyncMock
    monkeypatch.setattr("aggregate_search.favorite_metrics.enrich_favorites", AsyncMock())
    bili_batches = []

    class BiliClient:
        async def get(self, *_args, **_kwargs): return {"mid": 1}
        async def get_created_favorite_folders(self, _mid):
            return {"list": [{"id": 10, "title": "A"}, {"id": 11, "title": "B"}]}
        async def get_favorite_folder_contents(self, media_id, *_):
            return {"medias": [{"bvid": "same", "title": "one"},
                                {"bvid": str(media_id), "title": "two"}], "has_more": False}

    bili = SimpleNamespace(bili_client=BiliClient(), _result_limit=lambda: 3,
                           _result_sink_call=bili_batches.append)
    await BilibiliCrawler.fetch_favorites(bili)
    flattened = [item for batch in bili_batches for item in batch]
    assert [item["bvid"] for item in flattened] == ["same", "10", "same", "11"]
    assert [item["_collection_name"] for item in flattened] == ["A", "A", "B", "B"]

    zh_batches = []

    class ZhClient:
        async def get_current_user_info(self): return {"url_token": "me"}
        async def get_user_collections(self, _token):
            return {"data": [{"id": "c", "title": "精选"}]}
        async def get_collection_items(self, *_):
            return {"data": [{"content": {"type": "article", "id": 9, "title": "文章"}}],
                    "paging": {"is_end": True}}

    zh = SimpleNamespace(zhihu_client=ZhClient(), _result_limit=lambda: 5,
                         _result_sink_call=zh_batches.append)
    await ZhihuCrawler.fetch_favorites(zh)
    assert zh_batches[0][0]["_collection_name"] == "精选"


@pytest.mark.asyncio
@pytest.mark.parametrize("platform", ["xhs", "douyin"])
async def test_hundred_favorites_use_bounded_pages(platform, monkeypatch):
    from unittest.mock import AsyncMock
    import config
    monkeypatch.setattr(config, "CRAWLER_MAX_SLEEP_SEC", 0)
    monkeypatch.setattr("aggregate_search.favorite_metrics.enrich_favorites", AsyncMock())
    counts = []
    received = []
    async def fetch(cursor, count, *_):
        offset = int(cursor or 0)
        counts.append(count)
        rows = [{"note_id": str(n), "aweme_id": str(n)} for n in range(offset, offset + count)]
        return {"notes": rows, "aweme_list": rows, "has_more": True,
                "cursor": str(offset + count) if platform == "xhs" else offset + count}
    crawler = SimpleNamespace(
        _result_limit=lambda: 100, _result_sink_call=received.extend,
        context_page=SimpleNamespace(evaluate=AsyncMock(return_value="self-user")),
        xhs_client=SimpleNamespace(get_collected_notes=fetch),
        dy_client=SimpleNamespace(get_collected_awemes=fetch),
    )
    if platform == "xhs":
        await XiaoHongShuCrawler.fetch_favorites(crawler)
    else:
        await DouYinCrawler.fetch_favorites(crawler)
    assert len(received) == 100
    assert len({row["note_id"] for row in received}) == 100
    assert max(counts) <= (30 if platform == "xhs" else 20)
