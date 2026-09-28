"""Duration uses public snapshots; old records need no database migration."""
import pytest

from aggregate_search.adapters.bilibili import BilibiliAdapter
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


def test_duration_survives_stores_and_backup(tmp_path):
    result = BilibiliAdapter().adapt([{"bvid": "BVtest", "title": "Video", "duration": "01:34"}])[0].model_dump()
    library = LibraryStore(tmp_path / "library.db")
    library.add_item(result)
    assert library.get_item("bilibili", "BVtest")["result"]["duration_seconds"] == 94
    restored = LibraryStore(tmp_path / "restored.db")
    restored.import_payload(library.export_payload())
    assert restored.get_item("bilibili", "BVtest")["result"]["duration_seconds"] == 94
    history = ViewHistoryStore(tmp_path / "history.db")
    assert history.record_view(result)["result"]["duration_seconds"] == 94
    remote = RemoteFavoritesStore(tmp_path / "remote.db")
    remote.save_platform("bilibili", [result], status="succeeded")
    assert remote.load()["results"][0]["duration_seconds"] == 94
