"""Regression for a three-source chat with collection gaps and fully read samples."""

import copy
import json

import pytest

from api.services.research_documents import (
    MaterialAccess, ReadLoopGuard, SourceReferenceError, answer_document, read_material_section,
    research_context, result_document,
)
from api.services.research_materials import component, material


def sources():
    rows = [material({"key": f"xhs|{index}", "result": {"platform": "xhs", "title": f"苏州资料{index}",
        "content_type": "video" if index == 3 else "note", "snippet": "摘要不能替代正文",
        "url": f"https://www.xiaohongshu.com/explore/{index}"}}) for index in range(1, 4)]
    rows[0].update(body=component("ok", text="清晨游园"), comments=component("restricted", reason="平台限制评论"))
    rows[1].update(body=component("failed", reason="正文获取失败"), comments=component("failed", reason="评论获取失败"))
    rows[2].update(body=component("ok", text="视频介绍语"), comments=component("ok",
        reason="最多取得 50 条评论", entries=[{"id": str(index), "text": "评论样本" * 40} for index in range(50)]),
        subtitles=component("failed", reason="平台无字幕且本地转写不可用"))
    rows[2]["comments"]["truncated"] = True
    return rows


def read(access, key, section, index=0):
    return read_material_section(access, {}, {}, key, section, index)


def submission(refs=None, text="清晨游园，当前评论样本提供补充反馈。"):
    return {"sections": [{"kind": "space", "title": "建议", "paragraphs": [{"text": text, "sources": refs or ["S1", "S3"]}]}]}


def test_collection_and_reading_are_independent_and_duplicate_reads_are_bounded():
    access = MaterialAccess(sources())
    assert access.model_coverage()[2]["sections"]["comments"]["chunks"] == 2
    assert "complete" not in access.model_coverage()[2]["sections"]["comments"]
    assert "truncated" not in access.model_coverage()[2]["sections"]["comments"]
    assert "xhs|" not in research_context({"space_name": "苏州", "description": ""}, access)
    for key, section, state in [("S1", "comments", "restricted"), ("S2", "body", "failed"), ("S2", "comments", "failed"), ("S3", "subtitles", "failed")]:
        value = read(access, key, section)
        assert value["available"] is False and value["state"] == state and value["chunks"] == 0 and value["reason"]
        assert "error" not in value
    assert all(row["read_chunks"] == 0 for row in access.coverage())
    assert read(access, "S2", "body", 4)["available"] is False
    read(access, "S1", "body")
    read(access, "S3", "body")
    read(access, "S3", "comments")
    section = access.coverage()[2]["sections"]["comments"]
    assert section["collection_truncated"] and not section["reading_complete"]
    read(access, "S3", "comments", 1)
    section = access.model_coverage()[2]["sections"]["comments"]
    assert section["count"] == 50 and section["read_chunks"] == section["chunks"] == 2
    assert section["collection_truncated"] and section["reading_complete"]
    duplicate = read(access, "S3", "comments", 1)
    assert duplicate["already_read"] and "text" not in duplicate
    assert access.coverage()[2]["sections"]["comments"]["read_chunks"] == 2
    assert not access.coverage()[1]["sections"]["body"]["reading_complete"]
    for doc in (answer_document("建议 [S1:body]，当前评论样本 [S3:comments]，视频讲述与 S2 正文不可用。", False,
                               sources(), coverage=access.coverage()),
                result_document(submission(), sources(), [], access.coverage(), False)):
        encoded = json.dumps(doc, ensure_ascii=False)
        assert "已读完当前 50 条样本" in encoded and "平台获取不完整" in encoded
        assert "href" in encoded and "xhs|" not in encoded


@pytest.mark.parametrize("ref", ["S3:comments", "S3:subtitles", "S2", "S2:body", "S99", "S3 评论", "S3:comment", "s3", "S3：comments"])
@pytest.mark.parametrize("structured", [False, True])
def test_unread_sections_and_pseudo_citations_cannot_bypass_validation(ref, structured):
    access = MaterialAccess(sources())
    read(access, "S1", "body")
    read(access, "S3", "body")
    with pytest.raises(SourceReferenceError):
        if structured:
            result_document(submission(["S1"], f"声称评论或字幕观点 [{ref}]"), sources(), [], access.coverage(), False)
        else:
            answer_document(f"声称评论或字幕观点 [{ref}]", False, sources(), coverage=access.coverage())


def test_public_and_legacy_structured_refs_share_validation_and_render_links():
    access = MaterialAccess(sources())
    read(access, "S1", "body")
    read(access, "S3", "comments")
    for refs in (["S1", "S3"], ["S1:body", "S3:comments"], ["xhs|1", "xhs|3"]):
        doc = result_document(submission(refs), sources(), [], access.coverage(), False)
        links = [node for block in doc["content"] for node in block.get("content", []) if node.get("marks")]
        assert len(links) == 2 and all(node["text"].startswith("[S") for node in links)
    with pytest.raises(SourceReferenceError):
        answer_document("伪引用【S1】", False, sources(), coverage=access.coverage())
    with pytest.raises(SourceReferenceError):
        result_document(submission(["S3:subtitles"]), sources(), [], access.coverage(), False)
    invalid_title = submission(["S1"])
    invalid_title["sections"][0]["title"] = "评论观点 [S3 评论]"
    with pytest.raises(SourceReferenceError):
        result_document(invalid_title, sources(), [], access.coverage(), False)


def test_read_guard_limits_stagnant_calls_and_new_evidence_resets_it():
    guard = ReadLoopGuard()
    for index in range(5):
        guard.observe("read_material", {"key": f"S{index}"}, {"available": False})
    assert not guard.exhausted
    guard.observe("read_material", {"key": "S6"}, {"text": "新证据"})
    assert not guard.exhausted and guard.stagnant == 0
    result = {"available": False}
    for _ in range(3):
        guard.observe("read_material", {"key": "S2"}, result)
    assert guard.exhausted and "停止工具重试" in result["hint"]
    guard = ReadLoopGuard()
    for index in range(6):
        guard.observe("denied", {"key": f"S{index}"}, {"error": "invalid"})
    assert guard.exhausted


@pytest.mark.parametrize("key,section,index", [("S4", "body", 0), ("S1", "cookie", 0), ("S1", "body", 1), ("S1", "body", -1), ("S1", "body", True), ([], "body", 0), ("S1", [], 0)])
def test_invalid_read_arguments_still_fail_without_counting_reads(key, section, index):
    access = MaterialAccess(sources())
    with pytest.raises(ValueError):
        read(access, key, section, index)
    assert all(row["read_chunks"] == 0 for row in access.coverage())


def test_public_web_ids_and_body_citations_distinguish_summary_from_body():
    access = MaterialAccess([])
    external = {"web|1": {"id": "web|1", "citation": "W1", "title": "网页", "url": "https://example.com",
        "level": "仅搜索摘要", "chunks": 0, "read_chunks": [], "fetched_at": "now"}}
    assert not read_material_section(access, external, {}, "W1", "body", 0)["available"]
    answer_document("摘要信息 [W1]", True, external=list(external.values()))
    with pytest.raises(SourceReferenceError):
        answer_document("正文信息 [W1:body]", True, external=list(external.values()))
    external["web|1"]["chunks"] = 1
    read_material_section(access, external, {"web|1": "正文"}, "W1", "body", 0)
    doc = result_document({"sections": [{"kind": "web", "paragraphs": [{"text": "正文 [W1:body]", "sources": ["W1:body"]}]}]},
                          [], list(external.values()), [], True)
    assert "href" in json.dumps(doc)


def call(name, args, identity):
    return {"id": identity, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}


@pytest.mark.asyncio
@pytest.mark.parametrize("protocol", ["openai", "anthropic"])
async def test_three_source_chat_succeeds_with_public_ids_in_both_adapters(monkeypatch, tmp_path, protocol):
    from api.services import research_agent, research_openai
    values, events = [], []
    actions = [("manifest", {}), ("read_material", {"key": "S1", "section": "body", "index": 0}),
        ("read_material", {"key": "S2", "section": "body", "index": 0}),
        ("read_material", {"key": "S2", "section": "comments", "index": 0}),
        ("read_material", {"key": "S3", "section": "body", "index": 0}),
        ("read_material", {"key": "S3", "section": "subtitles", "index": 0}),
        ("read_material", {"key": "S3", "section": "comments", "index": 0}),
        ("read_material", {"key": "S3", "section": "comments", "index": 1})]
    data = {"protocol": protocol, "materials": sources(), "mode": "analyze", "workdir": str(tmp_path),
        "base_url": "https://example.com/v1", "model": "test", "space_name": "苏州", "description": "", "web_enabled": False}
    if protocol == "openai":
        replies = [{"choices": [{"message": {"tool_calls": [call(name, args, str(index))]}}]} for index, (name, args) in enumerate(actions)]
        replies.append({"choices": [{"message": {"tool_calls": [call("submit_result", submission(), "submit")]}}]})
        async def completion(client, payload, messages, tools):
            if messages[-1]["role"] == "tool":
                values.append(json.loads(messages[-1]["content"]))
            return replies.pop(0)
        monkeypatch.setattr(research_openai, "completion", completion)
    else:
        import claude_agent_sdk as sdk
        functions = {}
        def tool(name, description, schema):
            def wrap(function):
                functions[name] = function
                return function
            return wrap
        monkeypatch.setattr(sdk, "tool", tool)
        monkeypatch.setattr(sdk, "create_sdk_mcp_server", lambda *args, **kwargs: {})
        class Client:
            def __init__(self, options):
                pass
            async def __aenter__(self):
                return self
            async def __aexit__(self, *args):
                pass
            async def query(self, prompt):
                assert "xhs|" not in prompt
            async def receive_response(self):
                for name, args in actions:
                    value = await functions[name](args)
                    assert not value.get("is_error")
                    values.append(json.loads(value["content"][0]["text"]))
                yield sdk.ResultMessage("success", 1, 1, False, 1, "test", result="清晨游园 [S1:body]。当前评论样本 [S3:comments]；S2 正文及视频原话不可用。")
        monkeypatch.setattr(sdk, "ClaudeSDKClient", Client)
    result = await research_agent.run_agent(data, events.append)
    assert all("error" not in value for value in values)
    gaps = [value for value in values if value.get("available") is False]
    assert len(gaps) == 3 and all(value["chunks"] == 0 for value in gaps)
    section = result["coverage"][2]["sections"]["comments"]
    assert section["reading_complete"] and section["collection_truncated"] and section["count"] == 50
    assert "已读完当前 50 条样本" in json.dumps(result["document"], ensure_ascii=False)
    if protocol == "openai":
        submits = [event for event in events if event.get("tool") == "submit_result"]
        assert len(submits) == 2 and submits[-1]["status"] == "completed"


@pytest.mark.asyncio
async def test_batched_reads_and_multiple_submits_finish_once(monkeypatch):
    from api.services import research_openai
    calls = [call("submit_result", submission(), "submit1"), call("read_material", {"key": "S1", "section": "body", "index": 0}, "body"),
        call("read_material", {"key": "S3", "section": "comments", "index": 0}, "comments"), call("submit_result", submission(), "submit2")]
    async def completion(*args):
        return {"choices": [{"message": {"tool_calls": calls}}]}
    monkeypatch.setattr(research_openai, "completion", completion)
    events = []
    output = await research_openai.run_openai({"materials": sources(), "base_url": "https://example.com", "model": "test", "space_name": "苏州", "description": ""}, events.append)
    assert output["coverage"][0]["read_chunks"] == 1
    assert sum(event.get("tool") == "submit_result" and event.get("status") == "completed" for event in events) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_submit", [True, False])
async def test_retries_are_bounded_then_normal_answer_retains_read_evidence(monkeypatch, bad_submit):
    from api.services import research_openai
    captured = []
    async def completion(client, payload, messages, tools):
        captured.append(copy.deepcopy((messages, tools)))
        index = len(captured)
        if index == 1:
            calls = [call("read_material", {"key": "S1", "section": "body", "index": 0}, "read")]
        elif not tools:
            return {"choices": [{"message": {"content": "建议清晨游园 [S1:body]。其余资料有缺口。"}}]}
        elif bad_submit:
            calls = [call("submit_result", {"paragraph": "invalid"}, str(index))]
        else:
            calls = [call("read_material", {"key": "S2", "section": "body", "index": 0}, str(index))]
        return {"choices": [{"message": {"tool_calls": calls}}]}
    monkeypatch.setattr(research_openai, "completion", completion)
    output = await research_openai.run_openai({"materials": sources(), "base_url": "https://example.com", "model": "test", "space_name": "苏州", "description": ""}, lambda _: None)
    assert len(captured) == (4 if bad_submit else 5)
    assert captured[-1][1] == [] and output["coverage"][0]["read_chunks"] == 1
    assert "href" in json.dumps(output["document"])
