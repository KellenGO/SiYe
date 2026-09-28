"""Duration uses public snapshots; old records need no database migration."""
import pytest

from aggregate_search.adapters.bilibili import BilibiliAdapter
from aggregate_search.adapters.douyin import DouyinAdapter
from aggregate_search.adapters.xhs import XhsAdapter
from aggregate_search.adapters.zhihu import ZhihuAdapter
from aggregate_search.models import GroupedSource
from api.services.favorite_snapshot import decode_metrics, encode_metrics, merge_snapshot
from api.services.library_store import LibraryStore
from api.services.remote_favorites_store import RemoteFavoritesStore
from api.services.watch_history_store import ViewHistoryStore


@pytest.mark.parametrize("raw, expected", [(94, 94), ("01:34", 94), ("1:02:03", 3723), ("65:01", 3901), ("90", 90), (0, None), (-1, None), (True, None), ("1:99", None), ("bad", None), (None, None)])
def test_bilibili_duration_shapes(raw, expected):
    for item in ({"bvid": "BVtest", "title": "Video", "duration": raw}, {"View": {"bvid": "BVtest", "title": "Video", "duration": raw}}):
        result = BilibiliAdapter().adapt([item])[0]
        assert result.duration_seconds == expected
        assert GroupedSource.from_result(result).duration_seconds == expected


def test_snapshot_duration_is_optional_and_not_a_counter():
    assert decode_metrics('{"like_count": 12}')["duration_seconds"] is None
    for value in (None, -1, 0, True, "94", 1.5):
        assert decode_metrics(encode_metrics({"duration_seconds": value}))["duration_seconds"] is None
    snapshot = {"duration_seconds": 94, "metrics": {"view_count": 10}}
    assert decode_metrics(encode_metrics(snapshot))["duration_seconds"] == 94
    assert merge_snapshot(snapshot, {"duration_seconds": None})["duration_seconds"] == 94
    assert merge_snapshot(snapshot, {"duration_seconds": 100})["duration_seconds"] == 100


@pytest.mark.parametrize("adapter, raw", [
    (BilibiliAdapter(), {"bvid": "BVtest", "title": "Video", "duration": "01:34"}),
    (DouyinAdapter(), {"aweme_id": "dy-test", "desc": "Video", "video": {"duration": 94000}}),
    (XhsAdapter(), {"note_id": "xhs-test", "title": "Video", "type": "video", "video": {"capa": {"duration": 94}}}),
    (ZhihuAdapter(), {"id": "123", "title": "Video", "type": "zvideo", "video": {"duration": 94.8}}),
])
def test_duration_survives_stores_and_backup(tmp_path, adapter, raw):
    result = adapter.adapt([raw])[0].model_dump()
    platform, content_id = result["platform"], result["content_id"]
    library = LibraryStore(tmp_path / "library.db")
    library.add_item(result)
    assert library.get_item(platform, content_id)["result"]["duration_seconds"] == 94
    restored = LibraryStore(tmp_path / "restored.db")
    restored.import_payload(library.export_payload())
    assert restored.get_item(platform, content_id)["result"]["duration_seconds"] == 94
    history = ViewHistoryStore(tmp_path / "history.db")
    assert history.record_view(result)["result"]["duration_seconds"] == 94
    remote = RemoteFavoritesStore(tmp_path / "remote.db")
    remote.save_platform(platform, [result], status="succeeded")
    assert remote.load()["results"][0]["duration_seconds"] == 94


# Field units follow the native schemas, not the magnitude of the number:
# https://github.com/yt-dlp/yt-dlp/blob/master/yt_dlp/extractor/tiktok.py
# https://github.com/yt-dlp/yt-dlp/blob/master/yt_dlp/extractor/zhihu.py
# https://pkg.go.dev/github.com/Sen-platotech/xiaohongshu-auto-ops-assistant/xiaohongshu
@pytest.mark.parametrize("adapter, raw, expected", [
    (DouyinAdapter(), {"aweme_id": "1", "video": {"duration": "94500"}}, 94),
    (DouyinAdapter(), {"aweme_id": "1", "video": {"duration": 500}}, 1),
    (DouyinAdapter(), {"aweme_id": "1", "video": {"download_addr": {"duration": 3723000}}}, 3723),
    (DouyinAdapter(), {"aweme_id": "1", "music": {"duration": 94}}, None),
    (DouyinAdapter(), {"aweme_id": "1", "images": [{}], "video": {"duration": 94000}}, None),
    (DouyinAdapter(), {"aweme_id": "1", "image_post_info": {"images": [{}]}, "video": {"duration": 94000}}, None),
    (XhsAdapter(), {"id": "1", "note_card": {"type": "video", "video": {"capa": {"duration": "94"}}}}, 94),
    (XhsAdapter(), {"note_id": "1", "type": "video", "video": {"media": {"video": {"duration": 94}}}}, 94),
    (XhsAdapter(), {"note_id": "1", "type": "video", "video": {"media": {"stream": {"h264": [None, {"duration": "94500"}]}}}}, 94),
    (XhsAdapter(), {"id": "1", "note_card": {"type": "video", "video": None}, "video": {"capa": {"duration": 94}}}, 94),
    (XhsAdapter(), {"note_id": "1", "type": "normal", "video": {"capa": {"duration": 94}}}, None),
    (ZhihuAdapter(), {"id": "1", "type": "zvideo", "video": {"duration": "146.333"}}, 146),
    (ZhihuAdapter(), {"id": "1", "type": "answer", "video": {"duration": 94}}, None),
    (ZhihuAdapter(), {"id": "1", "type": "article", "video": {"duration": 94}}, None),
])
def test_other_platform_duration_units_and_content_types(adapter, raw, expected):
    result = adapter.adapt([raw])[0]
    assert result.duration_seconds == expected
    assert GroupedSource.from_result(result).duration_seconds == expected


@pytest.mark.parametrize("value", [None, True, False, 0, -1, "", "bad", "01:34", "NaN", "Infinity", float("nan"), float("inf"), 10**400, {}, []])
def test_invalid_duration_does_not_break_result(value):
    samples = [
        (DouyinAdapter(), {"aweme_id": "1", "video": {"duration": value}}),
        (XhsAdapter(), {"note_id": "1", "type": "video", "video": {"capa": {"duration": value}}}),
        (ZhihuAdapter(), {"id": "1", "type": "zvideo", "video": {"duration": value}}),
    ]
    for adapter, raw in samples:
        assert adapter.adapt([raw])[0].duration_seconds is None


@pytest.mark.parametrize("video", [None, [], "broken", {}, {"capa": [], "media": []}, {"media": {"video": [], "stream": {"h264": "bad"}}}])
def test_missing_or_malformed_video_shapes(video):
    for adapter, raw in [
        (DouyinAdapter(), {"aweme_id": "1", "video": video}),
        (XhsAdapter(), {"note_id": "1", "type": "video", "video": video}),
        (ZhihuAdapter(), {"id": "1", "type": "zvideo", "video": video}),
    ]:
        assert adapter.adapt([raw])[0].duration_seconds is None
