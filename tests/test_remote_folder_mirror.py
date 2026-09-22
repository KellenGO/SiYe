"""Read-only remote-folder mirror: identity, rename and pagination contracts."""

from api.services.remote_favorites_store import RemoteFavoritesStore


def _item(kind: str, content_id: str, title: str, cover: str | None = None):
    return {"platform": "zhihu", "content_type": kind, "content_id": content_id,
            "title": title, "author": "author", "url": "https://example.test/item",
            "cover_url": cover, "metrics": {}}


def test_zhihu_typed_identity_keeps_folder_memberships_separate(tmp_path):
    store = RemoteFavoritesStore(tmp_path / "library.db")
    account, folder = "zhihu:stable-user", "42"
    store.observe_folders("zhihu", account, [{"id": folder, "name": "阅读"}])
    scan = store.begin_scan("zhihu", account, [{"id": folder, "name": "阅读"}], "full")
    rows = [_item("answer", "7", "回答"), _item("article", "7", "文章", "https://cover.test/a.jpg")]
    store.save_sync_page("zhihu", account, scan, folder, "阅读", 1, "fingerprint", rows, True, 20,
                         [("answer:7", ""), ("article:7", "")])
    store.complete_folder(account, scan, folder, reconcile=True)

    page = store.folder_archive_page(account, folder, limit=10)
    assert page["total"] == 2
    assert {row["content_type"] for row in page["items"]} == {"answer", "article"}
    summary = store.folders_page(platform="zhihu", account=account)
    assert summary[0]["item_count"] == 2
    assert summary[0]["cover_url"] == "https://cover.test/a.jpg"


def test_folder_rename_and_not_found_keep_read_only_mirror(tmp_path):
    store = RemoteFavoritesStore(tmp_path / "library.db")
    account = "bilibili:1"
    store.observe_folders("bilibili", account, [{"id": "old", "name": "旧名字"}])
    store.observe_folders("bilibili", account, [{"id": "old", "name": "新名字"}])
    assert store.folders_page(platform="bilibili", account=account)[0]["name"] == "新名字"
    # A later full directory that omits it must not delete the durable folder row.
    scan = store.begin_scan("bilibili", account, [], "full")
    store.finish_scan(account, scan, reconcile=True)
    row = store.folders_page(platform="bilibili", account=account)[0]
    assert row["observed_state"] == "not_found"
