"""Spaces keep independent snapshots and lossless, conflict-checked rich notes."""

import json
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.routers.spaces import spaces_router
from api.services.library_store import LibraryStore
from api.services.space_notes import EMPTY_NOTE, validate_note
from api.services.spaces_store import SpaceConflict, SpacesStore, get_spaces_store


def result(content_id="one", platform="xhs"):
    domains = {"xhs": "www.xiaohongshu.com", "zhihu": "www.zhihu.com", "bilibili": "www.bilibili.com", "douyin": "www.douyin.com"}
    return {"platform": platform, "content_id": content_id, "title": "苏州攻略", "url": f"https://{domains[platform]}/{content_id}", "content_type": "video", "metrics": {"view_count": 3}, "duration_seconds": 60}


def document(text="攻略"):
    return {"type": "doc", "content": [{"type": "paragraph", "content": [{"type": "text", "text": text, "marks": [{"type": "bold"}, {"type": "italic"}, {"type": "underline"}, {"type": "textStyle", "attrs": {"fontSize": "24px"}}]}]}, {"type": "taskList", "content": [{"type": "taskItem", "attrs": {"checked": True}, "content": [{"type": "paragraph", "content": [{"type": "text", "text": "订酒店"}]}]}]}]}


@pytest.fixture
def store(tmp_path):
    return SpacesStore(tmp_path / "library.db")


@pytest.fixture
def client(store):
    app = FastAPI()
    app.include_router(spaces_router)
    app.dependency_overrides[get_spaces_store] = lambda: store
    return TestClient(app)


def test_create_restart_and_stop_collecting(store):
    first = store.create_space(" 苏州攻略 ", "四个平台一起看")
    second = store.create_space("选购比较")
    assert first["name"] == "苏州攻略"
    reopened = SpacesStore(store.db_path)
    assert reopened.list_spaces()["active_space_id"] == second["id"]
    reopened.set_active(first["id"])
    reopened.set_active(None)
    assert len(reopened.list_spaces()["spaces"]) == 2
    assert reopened.list_spaces()["active_space_id"] is None


def test_sources_independent_of_favorites_and_other_spaces(store):
    library = LibraryStore(store.db_path)
    first = store.create_space("苏州攻略")["id"]
    second = store.create_space("其他研究")["id"]
    store.add_items(first, [result(), result(platform="zhihu")])
    store.add_items(second, [result()])
    assert library.stats()["total"] == 0
    library.add_item(result())
    library.remove_items([{"platform": "xhs", "content_id": "one"}])
    assert store.get_space(first)["item_count"] == 2
    library.add_item(result())
    store.delete_space(first)
    assert library.stats()["total"] == 1
    assert store.get_space(second)["item_count"] == 1


def test_duplicate_add_preserves_order_and_snapshot(store):
    space_id = store.create_space("攻略")["id"]
    assert store.add_items(space_id, [result(), result()])["added"] == 1
    original = store.get_space(space_id)["items"][0]
    assert store.add_items(space_id, [{**result(), "title": "另一个标题"}])["added"] == 0
    assert store.get_space(space_id)["items"][0] == original
    store.add_items(space_id, [result("two")])
    assert [item["key"] for item in store.get_space(space_id)["items"]] == ["xhs|two", "xhs|one"]
    assert original["result"]["duration_seconds"] == 60


def test_concurrent_add_is_idempotent(store):
    space_id = store.create_space("攻略")["id"]
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: store.add_items(space_id, [result()]), range(4)))
    assert sum(item["added"] for item in results) == 1


def test_add_invalid_batch_and_capacity_are_atomic(store):
    space_id = store.create_space("攻略")["id"]
    with pytest.raises(ValueError):
        store.add_items(space_id, [result(), {**result("bad"), "url": "javascript:alert(1)"}])
    assert store.get_space(space_id)["item_count"] == 0
    store.add_items(space_id, [result(str(index)) for index in range(499)])
    with pytest.raises(ValueError, match="500"):
        store.add_items(space_id, [result("500"), result("501")])
    assert store.get_space(space_id)["item_count"] == 499


def test_snapshot_drops_private_fields(store):
    space_id = store.create_space("攻略")["id"]
    store.add_items(space_id, [{**result(), "cookie": "secret", "grouped_sources": [result()], "metrics": {"view_count": 0, "bad": 4}}])
    saved = store.get_space(space_id)["items"][0]["result"]
    assert "cookie" not in saved and "grouped_sources" not in saved
    assert saved["metrics"] == {"view_count": 0}


def test_remove_identity_with_slash(store):
    space_id = store.create_space("攻略")["id"]
    store.add_items(space_id, [result("answer/123")])
    store.remove_items(space_id, [{"platform": "xhs", "content_id": "answer/123"}])
    assert store.get_space(space_id)["item_count"] == 0


def test_archive_is_readonly_resume_preserves_everything(store):
    space_id = store.create_space("攻略")["id"]
    store.add_items(space_id, [result()])
    store.save_note(space_id, document(), 1, 0)
    store.set_archived(space_id, True)
    assert store.list_spaces()["active_space_id"] is None
    for operation in (lambda: store.set_active(space_id), lambda: store.add_items(space_id, [result("two")]),
                      lambda: store.remove_items(space_id, []), lambda: store.save_note(space_id, EMPTY_NOTE, 1, 1), lambda: store.update_info(space_id, "新名", "")):
        with pytest.raises(SpaceConflict):
            operation()
    resumed = store.set_archived(space_id, False)
    assert resumed["note_document"] == document()
    assert resumed["item_count"] == 1
    assert store.list_spaces()["active_space_id"] == space_id
    store.delete_space(space_id)
    assert store.list_spaces()["active_space_id"] is None


def test_note_roundtrip_and_stale_revision_rejected(store):
    space_id = store.create_space("攻略")["id"]
    assert store.save_note(space_id, document(), 1, 0)["note_revision"] == 1
    with pytest.raises(SpaceConflict):
        store.save_note(space_id, document("旧草稿"), 1, 0)
    assert SpacesStore(store.db_path).get_space(space_id)["note_document"] == document()


@pytest.mark.parametrize("bad", [
    {"type": "doc", "content": [{"type": "image", "attrs": {"src": "http://evil"}}]},
    {"type": "doc", "content": [{"type": "paragraph", "attrs": {"onclick": "alert(1)"}}]},
    {"type": "doc", "content": [{"type": "paragraph", "content": [{"type": "text", "text": "x", "marks": [{"type": "link", "attrs": {"href": "javascript:alert(1)"}}]}]}]},
    {"type": "doc", "content": [{"type": "heading", "attrs": {"level": 9}}]},
    {"type": "doc", "content": [{"type": "taskList", "content": [{"type": "taskItem", "attrs": {"checked": True}, "content": [False]}]}]},
    {"type": "doc", "content": [{"type": "paragraph", "content": [{"type": "text", "text": "x", "marks": [{"type": "textStyle", "attrs": {"fontSize": "999px"}}]}]}]},
])
def test_note_rejects_unsupported_structure(bad):
    with pytest.raises(ValueError):
        validate_note(bad)


def test_note_limits_and_versions():
    with pytest.raises(ValueError, match="50000"):
        validate_note(document("x" * 50_001))
    with pytest.raises(ValueError, match="2 MB"):
        validate_note({"type": "doc", "content": [{"type": "paragraph", "text": "x" * (2 * 1024 * 1024)}]})
    with pytest.raises(ValueError, match="版本"):
        validate_note(EMPTY_NOTE, 2)
    assert json.loads(validate_note(document())) == document()


def test_http_errors_and_full_flow(client):
    assert client.get("/api/spaces").json() == {"spaces": [], "active_space_id": None}
    assert client.get("/api/spaces/99").status_code == 404
    assert client.post("/api/spaces", json={"name": " "}).status_code == 400
    space_id = client.post("/api/spaces", json={"name": "攻略"}).json()["id"]
    path = f"/api/spaces/{space_id}"
    assert client.post(path + "/items", json={"results": [result()]}).status_code == 200
    assert client.put(path + "/note", json={"document": document(), "revision": 0}).status_code == 200
    assert client.put(path + "/note", json={"document": document(), "revision": 0}).status_code == 409
    assert client.put(path + "/note", json={"document": document(), "revision": -1}).status_code == 422
    assert client.put(path + "/archive", json={"archived": True}).status_code == 200
    assert client.post(path + "/items", json={"results": [result()]}).status_code == 409
    assert client.put(path + "/archive", json={"archived": False}).status_code == 200
    assert client.request("DELETE", path + "/items", json={"keys": [{"platform": "xhs", "content_id": "one"}]}).json()["removed"] == 1
    assert client.delete(path).status_code == 200
    assert client.get(path).status_code == 404
