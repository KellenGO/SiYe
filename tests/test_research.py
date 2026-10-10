# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
# This file is part of MediaCrawler project.
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

"""Isolated configuration, source coverage, lifecycle and agent permission tests."""

import asyncio
import json
import os
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.routers.research import research_router, get_research_config, get_research_jobs, require_research_extension
from api.services.research_config import ResearchConfig, protect_key
from api.services.research_jobs import ResearchJobs, task_environment
from api.services.research_materials import material, normalize_comments, component, subtitle_entries, MaterialCollector
from api.services.public_network import public_target, page_text
from api.services.spaces_store import SpacesStore, get_spaces_store


def cipher(value, decrypt=False):
    return value.removeprefix("encrypted:") if decrypt else "encrypted:" + value


@pytest.fixture
def config(tmp_path):
    return ResearchConfig(tmp_path / "ai.json", cipher=cipher)


def source(identity="one", platform="xhs", text="正文"):
    domains = {"xhs": "www.xiaohongshu.com", "bilibili": "www.bilibili.com", "douyin": "www.douyin.com", "zhihu": "www.zhihu.com"}
    return {"key": f"{platform}|{identity}", "result": {"platform": platform, "content_id": identity,
        "title": "攻略", "snippet": text, "content_type": "note", "url": f"https://{domains[platform]}/{identity}?xsec_token=private"}}


def test_config_masks_key_keeps_preference_and_restarts(config):
    config.save("https://example.com", "test-model", "sk-private")
    assert "sk-private" not in json.dumps(config.public())
    assert config.credentials()["api_key"] == "sk-private"
    config.set_preference(1, True)
    config.save("https://other.example", "other")
    reopened = ResearchConfig(config.path, cipher)
    assert reopened.preference(1) and not reopened.preference(2)
    assert reopened.credentials()["api_key"] == "sk-private"
    reopened.delete()
    assert reopened.preference(1) and not reopened.public()["has_key"]
    reopened.remove_preference(1)
    assert not reopened.preference(1)


@pytest.mark.skipif(os.name != "nt", reason="Windows DPAPI")
def test_dpapi_roundtrip_is_not_plaintext():
    encrypted = protect_key("unit-test-secret")
    assert "unit-test-secret" not in encrypted
    assert protect_key(encrypted, decrypt=True) == "unit-test-secret"


def test_key_configuration_rejects_credentials_in_url(config):
    with pytest.raises(ValueError):
        config.save("https://user:password@example.com", "model", "secret")




def test_comments_cap_dedup_reply_identity_and_subtitles():
    rows = [{"id": str(index), "content": f"观点{index}", "sub_comments": [
        {"id": f"reply{index}", "content": "补充", "user_info": {"nickname": "读者"}}]} for index in range(60)]
    entries = normalize_comments(rows + rows, "xhs")
    assert len(entries) == 50 and len({row["id"] for row in entries}) == 50
    assert entries[1]["parent_id"] == "0" and entries[1]["author"] == "读者"
    assert subtitle_entries({"body": [{"from": 12, "to": 14, "content": "<b>字幕</b>"}]}) == [{"text": "字幕", "start": 12, "end": 14}]






def test_reference_link_survives_editor_default_attributes():
    from api.services.space_notes import validate_note
    link = {"type": "doc", "content": [{"type": "paragraph", "content": [{"type": "text", "text": "来源", "marks": [
        {"type": "link", "attrs": {"href": "https://example.com", "target": "_blank", "rel": "noopener noreferrer", "class": None, "title": None}}]}]}]}
    validate_note(link)
    link["content"][0]["content"][0]["marks"][0]["attrs"]["title"] = {"unexpected": True}
    with pytest.raises(ValueError):
        validate_note(link)


@pytest.mark.asyncio
@pytest.mark.parametrize("url", ["http://127.0.0.1", "http://[::1]", "http://localhost", "http://169.254.169.254", "file:///etc/passwd", "https://u:p@example.com", "http://192.168.1.1", "http://example.com:8080"])
async def test_web_rejects_private_targets(url):
    with pytest.raises(ValueError):
        await public_target(url)


@pytest.mark.asyncio
async def test_web_rejects_mixed_public_private_dns(monkeypatch):
    async def resolve(*args, **kwargs):
        return [(2, 1, 6, "", ("93.184.216.34", 443)), (2, 1, 6, "", ("127.0.0.1", 443))]
    monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", resolve)
    with pytest.raises(ValueError):
        await public_target("https://example.com")
    assert page_text("<html><head><title>T</title></head><body><script>secret</script><p>正文</p></body></html>") == ("T", "正文")


@pytest.mark.asyncio
async def test_jobs_gap_confirmation_retry_cancel_and_snapshot(config, monkeypatch):
    from api.services import research_jobs as module
    monkeypatch.setattr(module, "get_session_snapshot", lambda _: {"a1": "private"})
    manager = ResearchJobs(config)
    monkeypatch.setattr(manager, "require_runtime", lambda: None)
    config.save("https://example.com", "model", "secret")
    async def process(job, payload, credentials=None):
        if payload["mode"] == "collect":
            job["materials"] = [material(row) for row in payload["items"]]
            return {"collected": True}
        return {"document": {"type": "doc", "content": [{"type": "paragraph"}]}, "coverage": []}
    monkeypatch.setattr(manager, "process", process)
    snapshot = {"id": 1, "archived": False, "items": [source()], "name": "攻略", "description": ""}
    job = await manager.create(snapshot, "路线", False)
    identity = job["job_id"]
    await manager.tasks[identity]
    assert manager.get(identity)["status"] == "awaiting_sources"
    with pytest.raises(ValueError, match="已有"):
        await manager.create(snapshot, "", True)
    snapshot["items"].append(source("two"))
    assert len(manager.get(identity)["snapshot"]["items"]) == 1
    await manager.generate(identity)
    await manager.tasks[identity]
    assert manager.get(identity)["status"] == "ready" and manager.active is None
    next_job = await manager.create(snapshot, "", True)
    await manager.tasks[next_job["job_id"]]
    await manager.cancel_space(1)
    assert manager.get(next_job["job_id"])["status"] == "cancelled" and manager.active is None


def test_task_environment_does_not_inherit_claude_settings(monkeypatch, tmp_path):
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "subscription-secret")
    monkeypatch.setenv("CLAUDE_CODE_USE_BEDROCK", "1")
    env = task_environment(tmp_path, {"api_key": "own-key", "base_url": "https://example.com"})
    assert "ANTHROPIC_AUTH_TOKEN" not in env and "CLAUDE_CODE_USE_BEDROCK" not in env
    assert env["ANTHROPIC_API_KEY"] == "own-key"


def test_routes_configuration_origin_and_preferences(config, tmp_path):
    app = FastAPI()
    app.include_router(research_router)
    app.dependency_overrides[require_research_extension] = lambda: None
    store = SpacesStore(tmp_path / "library.db")
    space_id = store.create_space("攻略")["id"]
    app.dependency_overrides[get_research_config] = lambda: config
    app.dependency_overrides[get_spaces_store] = lambda: store
    with TestClient(app, base_url="http://127.0.0.1") as client:
        assert client.put("/api/research/config", json={"base_url": "https://example.com", "model": "m", "api_key": "secret"}).status_code == 200
        assert "secret" not in client.get("/api/research/config").text
        assert client.get("/api/research/config", headers={"Origin": "https://evil.example"}).status_code == 403
        assert client.get(f"/api/research/spaces/{space_id}/preference").json() == {"web_enabled": False}
        assert client.put(f"/api/research/spaces/{space_id}/preference", json={"web_enabled": True}).json() == {"web_enabled": True}
        assert client.get("/api/research/spaces/999/preference").status_code == 404






@pytest.mark.asyncio
async def test_partial_component_failure_does_not_replace_success(monkeypatch):
    collector = MaterialCollector({"xhs": {"a1": "session"}})
    class Client:
        async def get_note_by_id(self, *args):
            return {"desc": "完整正文"}
    async def client(_):
        return Client()
    async def comments(*args):
        raise TimeoutError()
    monkeypatch.setattr(collector, "client", client)
    monkeypatch.setattr(collector, "comments", comments)
    previous = material(source())
    previous["body"] = component("ok", text="已成功的正文")
    current = await collector.collect(source(), previous)
    assert current["body"]["text"] == "已成功的正文"
    assert current["comments"]["state"] == "failed"


@pytest.mark.asyncio
async def test_later_comment_page_failure_keeps_read_entries(monkeypatch):
    collector = MaterialCollector()
    calls = 0
    async def request(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise TimeoutError()
        return {"comments": [{"id": "first", "content": "已获取的评论"}], "cursor": "next", "has_more": True}
    class Client:
        async def get(self, *args, **kwargs):
            pass
    monkeypatch.setattr(collector, "request", request)
    result = await collector.comments(Client(), source()["result"], None)
    assert result["state"] == "failed" and result["truncated"]
    assert result["entries"][0]["text"] == "已获取的评论"


@pytest.mark.asyncio
async def test_collection_failure_accounts_for_every_selected_source(config, monkeypatch):
    from api.services import research_jobs as module
    monkeypatch.setattr(module, "get_session_snapshot", lambda _: {"a1": "test"})
    manager = ResearchJobs(config)
    monkeypatch.setattr(manager, "require_runtime", lambda: None)
    async def process(*args, **kwargs):
        raise TimeoutError()
    monkeypatch.setattr(manager, "process", process)
    job = await manager.create({"id": 1, "archived": False, "items": [source(), source("two")], "name": "主题", "description": ""}, "", False)
    await manager.tasks[job["job_id"]]
    failed = manager.get(job["job_id"])
    assert failed["status"] == "failed" and len(failed["materials"]) == 2
    assert all(row["body"]["state"] == "failed" for row in failed["materials"])
    assert manager.active is None


@pytest.mark.asyncio
async def test_web_rechecks_redirect_destinations(monkeypatch):
    import httpx
    from api.services import public_network as module
    original = httpx.AsyncClient
    calls = []
    async def target(url):
        calls.append(url)
        if "127.0.0.1" in url:
            raise ValueError("private")
        return "https://93.184.216.34/", "example.com"
    async def redirect(request):
        assert request.headers["Host"] == "example.com"
        return httpx.Response(302, headers={"location": "http://127.0.0.1/secret"})
    monkeypatch.setattr(module, "public_target", target)
    monkeypatch.setattr(module.httpx, "AsyncClient", lambda **kwargs: original(transport=httpx.MockTransport(redirect), **kwargs))
    with pytest.raises(ValueError, match="private"):
        await module.public_get("https://example.com")
    assert calls == ["https://example.com", "http://127.0.0.1/secret"]




@pytest.mark.parametrize("host", ["evil.example", "localhost.evil.example", "127.0.0.1.evil.example"])
def test_matching_untrusted_host_and_origin_cannot_change_credentials(config, host):
    config.save("https://original.example", "original", "secret")
    app = FastAPI()
    app.include_router(research_router)
    app.dependency_overrides[require_research_extension] = lambda: None
    app.dependency_overrides[get_research_config] = lambda: config
    with TestClient(app, base_url=f"http://{host}:8080") as client:
        response = client.put("/api/research/config", headers={"Origin": f"http://{host}:8080", "Sec-Fetch-Site": "same-origin"},
            json={"base_url": "https://evil.example", "model": "changed"})
        assert response.status_code == 403
        assert client.post("/api/research/connection-test", json={}).status_code == 403
    assert config.credentials() == {"protocol": "openai", "base_url": "https://original.example", "model": "original", "api_key": "secret"}


@pytest.mark.parametrize("url", ["http://localhost:8080", "http://127.0.0.1:8080", "http://[::1]:8080"])
def test_local_hosts_and_development_origin_remain_supported(config, url):
    app = FastAPI()
    app.include_router(research_router)
    app.dependency_overrides[require_research_extension] = lambda: None
    app.dependency_overrides[get_research_config] = lambda: config
    with TestClient(app, base_url="http://127.0.0.1:8080", headers={"Host": urlsplit(url).netloc}) as client:
        assert client.get("/api/research/config", headers={"Origin": url}).status_code == 200
        assert client.get("/api/research/config", headers={"Origin": "http://localhost:5173"}).status_code == 200
        assert client.get("/api/research/config", headers={"Origin": "http://evil.example:8080"}).status_code == 403


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [PermissionError("missing session"), RuntimeError("client failed")])
async def test_retry_client_failure_keeps_partial_evidence(monkeypatch, error):
    collector = MaterialCollector()
    async def client(_):
        raise error
    monkeypatch.setattr(collector, "client", client)
    previous = material(source())
    previous["body"] = component("ok", text="已读取正文")
    previous["comments"] = component("failed", entries=[{"id": "one", "text": "部分评论"}])
    previous["subtitles"] = component("failed", entries=[{"start": 0, "text": "部分字幕"}])
    result = await collector.collect(source(), previous)
    assert result["body"]["text"] == "已读取正文"
    for name in ("comments", "subtitles"):
        assert result[name]["entries"] == previous[name]["entries"] and result[name]["entries"]
        assert result[name]["truncated"] and result[name]["reason"]


def test_poll_response_omits_evidence_but_preserves_counts(config):
    manager = ResearchJobs(config)
    row = material(source(text="private snippet"))
    row["body"] = component("ok", text="private body" * 10000)
    row["comments"] = component("failed", entries=[{"id": "1", "text": "private comment"}], reason="读取失败")
    manager.jobs["job"] = {"space_id": 1, "snapshot": {"items": [source()]}, "materials": [row], "elapsed": 0}
    response = manager.public("job")
    encoded = json.dumps(response)
    assert "private" not in encoded and len(encoded) < 2000
    assert response["materials"][0]["comments"]["count"] == 1 and response["total_materials"] == 1
    response["materials"][0]["body"]["state"] = "failed"
    assert row["body"]["state"] == "ok"


def test_finished_jobs_are_bounded_and_active_job_is_preserved(config, monkeypatch):
    from api.services import research_jobs as module
    manager = ResearchJobs(config)
    manager.jobs["active"] = {"status": "awaiting_sources"}
    manager.active = "active"
    for index in range(20):
        manager.jobs[str(index)] = {"status": "ready", "evidence": "内容"}
    manager.prune()
    assert list(manager.jobs) == ["active", *map(str, range(10, 20))]
    monkeypatch.setattr(module, "MAX_RETAINED_BYTES", 70)
    manager.prune()
    assert list(manager.jobs) == ["active", "19"]


@pytest.mark.asyncio
async def test_history_survives_restart_pruning_and_reuses_materials(config, monkeypatch):
    manager = ResearchJobs(config)
    monkeypatch.setattr(manager, "require_runtime", lambda: None)
    config.save("https://example.com", "model", "private-api-key")
    async def process(job, payload, credentials=None):
        assert payload["mode"] == "analyze"
        return {"document": {"type": "doc", "content": [{"type": "paragraph", "content": [{"type": "text", "text": "已完成"}]}]}}
    monkeypatch.setattr(manager, "process", process)
    snapshot = {"id": 1, "archived": False, "items": [source()], "name": "攻略", "description": ""}
    for index in range(12):
        row = {"job_id": f"turn-{index}", "conversation_id": "saved", "space_id": 1, "created_at": index,
               "snapshot": snapshot, "question": f"问题{index}", "status": "ready", "phase": "ready",
               "research_focus": "研究主题", "materials": [material(source())], "elapsed": 0,
               "web_enabled": False, "document": {"type": "doc", "content": [{"type": "paragraph"}]},
               "worker_credentials": {"api_key": "private-api-key"}}
        manager.jobs[row["job_id"]] = row
        manager.persist(row["job_id"])
    manager.prune()
    assert len(manager.jobs) == 10
    assert len(manager.conversation_jobs(1, "saved")) == 12
    assert "private-api-key" not in "".join(path.read_text(encoding="utf-8") for path in manager.history_store.root.glob("*.json"))
    restarted = ResearchJobs(config)
    monkeypatch.setattr(restarted, "require_runtime", lambda: None)
    monkeypatch.setattr(restarted, "process", process)
    assert restarted.conversations(1)[0]["turns"] == 12
    assert restarted.latest(1)["question"] == "问题11"
    assert restarted.get("turn-0")["status"] == "ready"
    with pytest.raises(ValueError, match="不属于"):
        restarted.conversation_jobs(2, "saved")
    followup = await restarted.create(snapshot, "继续研究", False, "saved")
    await restarted.tasks[followup["job_id"]]
    assert restarted.get(followup["job_id"])["research_focus"] == "研究主题"
    assert len(restarted.get(followup["job_id"])["history"]) == 3
    restarted.remove_space(1)
    assert ResearchJobs(config).conversations(1) == []


def test_interrupted_and_corrupt_history_does_not_lock_restart(config):
    manager = ResearchJobs(config)
    row = {"job_id": "interrupted", "space_id": 1, "snapshot": {"name": "主题", "items": [], "note_document": "private-note"},
           "question": "问题", "status": "analyzing", "materials": [], "document": None}
    manager.history_store.save(row)
    assert "private-note" not in manager.history_store.path("interrupted").read_text(encoding="utf-8")
    (manager.history_store.root / "corrupt.json").write_text("{", encoding="utf-8")
    restarted = ResearchJobs(config)
    assert restarted.active is None
    assert len(restarted.conversations(1)) == 1
    assert restarted.get("interrupted")["status"] == "failed"
    assert "重启" in restarted.get("interrupted")["error"]
    with pytest.raises(ValueError):
        restarted.get("../research-ai")


@pytest.mark.asyncio
async def test_conversation_followups_reuse_sources_and_limit_context(config, monkeypatch):
    from api.services import research_jobs as module
    monkeypatch.setattr(module, "get_session_snapshot", lambda _: {"a1": "private"})
    manager = ResearchJobs(config)
    monkeypatch.setattr(manager, "require_runtime", lambda: None)
    config.save("https://example.com", "model", "secret")
    requests = []
    async def process(job, payload, credentials=None):
        requests.append(payload)
        if payload["mode"] == "collect":
            job["materials"] = [material(row) for row in payload["items"]]
            return {}
        return {"document": {"type": "doc", "content": [{"type": "paragraph", "content": [{"type": "text", "text": "answer" * 2000}]}]}, "coverage": []}
    monkeypatch.setattr(manager, "process", process)
    snapshot = {"id": 1, "archived": False, "items": [source()], "name": "攻略", "description": ""}
    first = await manager.create(snapshot, "路线", False)
    identity = first["job_id"]
    await manager.tasks[identity]
    with pytest.raises(ValueError, match="完成"):
        await manager.create(snapshot, "追问", False, first["conversation_id"])
    await manager.generate(identity)
    await manager.tasks[identity]
    snapshot["items"].append(source("two"))
    for index in range(5):
        followup = await manager.create(snapshot, f"追问{index}", index == 0, first["conversation_id"])
        assert followup["status"] == "analyzing" and followup["conversation_id"] == identity
        assert "history" not in followup
        await manager.tasks[followup["job_id"]]
        internal = manager.get(followup["job_id"])
        assert len(internal["snapshot"]["items"]) == 1
        assert 1 <= len(internal["history"]) <= 3
        assert all(len(row["answer"]) <= 3003 and row["answer_truncated"] for row in internal["history"])
        assert internal["research_focus"] == "路线"
    assert sum(row["mode"] == "collect" for row in requests) == 1
    assert requests[-1]["conversation"][-1]["question"] == "追问3"
    assert requests[-1]["research_focus"] == "路线"
    groups = manager.conversations(1)
    assert len(groups) == 1 and groups[0]["turns"] == 6
    with pytest.raises(ValueError, match="不属于"):
        await manager.create({**snapshot, "id": 2}, "别的空间", False, identity)
    with pytest.raises(ValueError, match="不存在"):
        await manager.create(snapshot, "未知会话", False, "unknown")
    fresh = await manager.create(snapshot, "新会话", False)
    assert fresh["conversation_id"] != identity and fresh["status"] == "collecting"
    await manager.tasks[fresh["job_id"]]
    assert len(manager.get(fresh["job_id"])["snapshot"]["items"]) == 2
    assert manager.get(fresh["job_id"])["history"] == []
    await manager.cancel(fresh["job_id"])


def test_conversation_routes_isolate_spaces_and_lock_configuration(config, tmp_path):
    app = FastAPI()
    app.include_router(research_router)
    app.dependency_overrides[require_research_extension] = lambda: None
    store = SpacesStore(tmp_path / "library.db")
    first = store.create_space("第一空间")["id"]
    second = store.create_space("第二空间")["id"]
    manager = ResearchJobs(config)
    manager.jobs["one"] = {"job_id": "one", "conversation_id": "conversation", "space_id": first,
        "question": "路线", "status": "ready", "snapshot": {"items": [], "name": "主题"}, "materials": [], "history": [{"answer": "private"}]}
    app.dependency_overrides[get_research_config] = lambda: config
    app.dependency_overrides[get_research_jobs] = lambda: manager
    app.dependency_overrides[get_spaces_store] = lambda: store
    with TestClient(app, base_url="http://127.0.0.1") as client:
        response = client.get(f"/api/research/spaces/{first}/conversations")
        assert response.json()[0]["id"] == "conversation"
        assert client.get(f"/api/research/spaces/{second}/conversations").json() == []
        assert "private" not in client.get(f"/api/research/spaces/{first}/conversations/conversation").text
        assert client.get(f"/api/research/spaces/{second}/conversations/conversation").status_code == 400
        manager.active = "one"
        assert client.put("/api/research/config", json={"base_url": "https://example.com", "model": "m", "api_key": "secret"}).status_code == 409
        assert client.delete("/api/research/config").status_code == 409
        manager.active = None
        assert client.put("/api/research/config", json={"base_url": "https://example.com", "model": "m", "api_key": "secret"}).status_code == 200



def test_new_and_legacy_protocol_configuration(config):
    assert config.public()["protocol"] == "openai"
    config.save("https://api.deepseek.com", "deepseek-flash", "secret")
    assert config.credentials()["protocol"] == "openai"
    data = json.loads(config.path.read_text(encoding="utf-8"))
    data.pop("protocol")
    config.path.write_text(json.dumps(data), encoding="utf-8")
    assert config.credentials()["protocol"] == "anthropic", "Old credentials must keep their original protocol"
    config.save("https://generativelanguage.googleapis.com/v1beta/openai", "gemini-3.8-flash", "gemini-key", "openai")
    assert config.public()["protocol"] == "openai"
    with pytest.raises(ValueError):
        config.save("https://api.deepseek.com/chat/completions", "model", "secret")
    with pytest.raises(ValueError):
        config.save("https://example.com", "model", "secret", "unsupported")


def test_openai_runtime_does_not_require_claude_sdk(config, monkeypatch):
    from api.services import research_jobs as module
    config.save("https://api.deepseek.com", "model", "secret")
    monkeypatch.setattr(module, "runtime_status", lambda: {"sdk_available": False, "cli_available": False})
    ResearchJobs(config).require_runtime()
    config.save("https://api.anthropic.com", "model", "secret", "anthropic")
    with pytest.raises(ValueError, match="运行环境"):
        ResearchJobs(config).require_runtime()


@pytest.mark.asyncio
async def test_cancel_updates_running_tool_records(config, monkeypatch):
    manager = ResearchJobs(config)
    config.save("https://example.com", "model", "isolated-key")
    manager.jobs["job"] = {"job_id": "job", "space_id": 1, "status": "analyzing", "materials": [], "history": [],
        "snapshot": {"name": "主题", "description": "", "items": []}, "question": "你好", "web_enabled": False,
        "activity": [{"id": "step-1", "tool": "read_material", "status": "running", "message": "read_material"}]}
    started = asyncio.Event()
    async def process(*args):
        started.set()
        await asyncio.Event().wait()
    monkeypatch.setattr(manager, "process", process)
    manager.active = "job"
    manager.tasks["job"] = asyncio.create_task(manager.analyze("job"))
    await started.wait()
    await manager.cancel("job")
    assert manager.jobs["job"]["activity"][0]["status"] == "cancelled"
    assert manager.active is None


@pytest.mark.parametrize("byte_limit", [32 * 1024 * 1024, 1])
def test_loading_history_obeys_retention_limits(config, monkeypatch, byte_limit):
    from api.services import research_jobs as module
    monkeypatch.setattr(module, "MAX_RETAINED_BYTES", byte_limit)
    manager = ResearchJobs(config)
    manager.jobs["active"] = {"status": "awaiting_sources"}
    manager.active = "active"
    for index in range(25):
        identity = f"saved-{index}"
        row = {"job_id": identity, "space_id": 1, "created_at": index,
               "snapshot": {"name": "主题", "items": []}, "question": "问题",
               "status": "ready", "materials": [], "document": None}
        manager.history_store.save(row)
        assert manager.get(identity)["job_id"] == identity
        expected = module.MAX_FINISHED_JOBS if byte_limit > 1 else 1
        assert len(manager.jobs) <= expected + 1
        assert "active" in manager.jobs
    assert len(manager.history_store.records()) == 25
    assert manager.get("saved-0")["job_id"] == "saved-0"
    assert "saved-0" in manager.jobs


@pytest.mark.asyncio
@pytest.mark.parametrize("url", ["/api/research/spaces/1/conversations", "/api/research/spaces/1/conversations/saved"])
async def test_history_reads_do_not_block_other_requests(url):
    import threading
    from httpx import ASGITransport, AsyncClient

    entered, release = threading.Event(), threading.Event()
    class Store:
        def get_space(self, _):
            return {"id": 1}
    class Manager:
        def read(self, *_):
            entered.set()
            release.wait(1)
            return []
        conversations = read
        conversation_jobs = read
    app = FastAPI()
    app.include_router(research_router)
    app.dependency_overrides[require_research_extension] = lambda: None
    app.dependency_overrides[get_spaces_store] = Store
    app.dependency_overrides[get_research_jobs] = Manager
    @app.get("/ping")
    async def ping():
        return {"alive": True}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://localhost:8080") as client:
        pending = asyncio.create_task(client.get(url))
        try:
            assert await asyncio.to_thread(entered.wait, 2)
            assert (await client.get("/ping")).json() == {"alive": True}
            assert not pending.done(), "history I/O blocked the event loop"
        finally:
            release.set()
            response = await pending
        assert response.status_code == 200 and response.json() == []
