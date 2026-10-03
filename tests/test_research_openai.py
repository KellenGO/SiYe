"""OpenAI-compatible provider semantics, bounded HTTP and restricted tool use."""

import copy
import json

import httpx
import pytest

from api.services import research_openai as module
from api.services.research_materials import material, component


def source():
    row = material({"key": "xhs|one", "result": {"platform": "xhs", "title": "攻略", "snippet": "简介",
        "content_type": "note", "url": "https://www.xiaohongshu.com/explore/one"}})
    row["body"] = component("ok", text="完整正文")
    return row


def payload(**extra):
    return {"mode": "analyze", "protocol": "openai", "base_url": "https://example.com/v1", "model": "model",
        "space_name": "攻略", "description": "", "materials": [source()], "web_enabled": False, **extra}


def result(ref="xhs|one"):
    return {"sections": [{"kind": "space", "title": "发现", "paragraphs": [{"text": "结论", "sources": [ref]}]}]}


def response(name=None, args=None, **extra):
    message = {"role": "assistant", "content": "OK" if name is None else None, **extra}
    if name:
        message["tool_calls"] = [{"id": "call-" + name, "type": "function",
            "function": {"name": name, "arguments": json.dumps(args or {})}}]
    return {"choices": [{"finish_reason": "tool_calls" if name else "stop", "message": message}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12}}


def test_ordinary_answer_keeps_basic_markdown_and_treats_html_as_text():
    from api.services.research_documents import answer_document
    from api.services.space_notes import validate_note
    document = answer_document("## 计划\n- **第一天**\n- 第二天\n2. 出发\n3. 返回\n<script>alert(1)</script>", False)
    validate_note(document)
    blocks = document["content"][2:]
    assert [row["type"] for row in blocks] == ["heading", "bulletList", "orderedList", "paragraph"]
    assert len(blocks[1]["content"]) == 2 and blocks[2]["attrs"]["start"] == 2
    assert blocks[1]["content"][0]["content"][0]["content"][0]["marks"] == [{"type": "bold"}]
    assert blocks[-1]["content"][0]["text"] == "<script>alert(1)</script>"
    validate_note(answer_document("**\n" + "9" * 5000 + ". 序号", False))


@pytest.mark.asyncio
async def test_native_loop_preserves_provider_metadata_and_source_coverage(monkeypatch):
    first = response("read_material", {"key": "xhs|one", "index": 0}, reasoning_content="provider-private-thinking")
    first["choices"][0]["message"]["tool_calls"][0]["extra_content"] = {"google": {"thought_signature": "opaque-signature"}}
    replies = [first, response("submit_result", result())]
    captured, events = [], []
    async def completion(client, data, messages, tools):
        captured.append(copy.deepcopy(messages))
        assert {row["function"]["name"] for row in tools} == {"manifest", "read_material", "submit_result"}
        return replies.pop(0)
    monkeypatch.setattr(module, "completion", completion)
    output = await module.run_openai(payload(conversation=[{"question": "以前", "answer": "上下文"}]), events.append)
    assert output["coverage"][0]["complete"]
    assert captured[1][1] == first["choices"][0]["message"]
    assert json.loads(captured[0][0]["content"])["conversation"][0]["answer"] == "上下文"
    assert all(row["role"] != "system" for row in captured[0])
    assert "provider-private-thinking" not in json.dumps(events)
    assert "opaque-signature" not in json.dumps(events)
    assert [event for event in events if event["type"] == "usage"][-1]["usage"]["total_tokens"] == 24
    activity = [event for event in events if event["type"] == "activity" and event["tool"] == "read_material"]
    assert [event["status"] for event in activity] == ["running", "completed"]
    assert activity[0]["id"] == activity[1]["id"] and "攻略" in activity[1]["summary"]


@pytest.mark.asyncio
async def test_ordinary_reply_and_public_commentary_work_without_a_system_prompt(monkeypatch):
    replies = [response("manifest", content="先查看你选入的资料。", reasoning_content="private-thinking"),
               response(content="可以，你可以直接告诉我想了解什么。", reasoning_content="private-thinking")]
    events = []
    async def completion(client, data, messages, tools):
        assert not any(row["role"] == "system" for row in messages)
        assert "围绕空间主题整理研究笔记" not in messages[0]["content"]
        return replies.pop(0)
    monkeypatch.setattr(module, "completion", completion)
    output = await module.run_openai(payload(question="你好"), events.append)
    assert "可以，你可以直接告诉我" in json.dumps(output["document"], ensure_ascii=False)
    assert not output["coverage"][0]["complete"]
    assert any(event.get("kind") == "commentary" and event["message"] == "先查看你选入的资料。" for event in events)
    assert "private-thinking" not in json.dumps(events)


@pytest.mark.asyncio
async def test_invalid_tool_activity_ends_with_failure(monkeypatch):
    replies = [response("read_material", {"key": "other", "index": 0}), response(content="没有读取成功。")]
    events = []
    async def completion(*args):
        return replies.pop(0)
    monkeypatch.setattr(module, "completion", completion)
    await module.run_openai(payload(), events.append)
    activity = [event for event in events if event["type"] == "activity"]
    assert [event["status"] for event in activity] == ["running", "failed"]


@pytest.mark.asyncio
async def test_native_loop_denies_files_network_and_other_spaces(monkeypatch):
    replies = [response("Bash", {"command": "private"}), response("read_webpage", {"url": "http://127.0.0.1"}),
        response("read_material", {"key": "other-space", "index": 0}), response("read_material", {"key": "xhs|one", "index": False}),
        response("read_material", {"key": "xhs|one", "index": 0}), response("submit_result", result())]
    captured = []
    async def completion(client, data, messages, tools):
        captured.append(copy.deepcopy(messages))
        return replies.pop(0)
    async def forbidden(*args):
        pytest.fail("Disabled web capability was executed")
    monkeypatch.setattr(module, "completion", completion)
    monkeypatch.setattr(module, "public_get", forbidden)
    output = await module.run_openai(payload(), lambda event: None)
    errors = [json.loads(row["content"]) for row in captured[-1] if row["role"] == "tool"]
    assert all("error" in row for row in errors[:4]) and output["coverage"][0]["complete"]


@pytest.mark.asyncio
async def test_native_loop_rejects_unread_final_citations(monkeypatch):
    async def completion(*args):
        return {"choices": [{"message": {"content": json.dumps(result())}}]}
    monkeypatch.setattr(module, "completion", completion)
    with pytest.raises(ValueError, match="未读取"):
        await module.run_openai(payload(), lambda event: None)


@pytest.mark.asyncio
async def test_native_probe_requires_real_tool_and_web_calls(monkeypatch):
    replies = [response("check_connection"), response()]
    async def completion(*args):
        return replies.pop(0)
    monkeypatch.setattr(module, "completion", completion)
    assert (await module.run_openai(payload(mode="probe", materials=[]), lambda event: None))["connection_ok"]
    replies += [response("check_connection"), response()]
    with pytest.raises(ValueError, match="联网测试"):
        await module.run_openai(payload(mode="probe", web_enabled=True, materials=[]), lambda event: None)


@pytest.mark.asyncio
async def test_native_web_sources_record_search_and_partial_reads(monkeypatch):
    replies = [response("read_material", {"key": "xhs|one", "index": 0}), response("search_web", {"query": "路线"}),
        response("read_webpage", {"url": "https://example.org"}),
        response("submit_result", {"sections": [*result()["sections"], {"kind": "web", "title": "补充", "paragraphs": [{"text": "外部", "sources": ["web|1"]}]}]})]
    async def completion(*args):
        return replies.pop(0)
    async def search(*args):
        return [{"title": "公开网页", "url": "https://example.org", "snippet": "摘要"}]
    async def read(*args):
        return "https://example.org", "<title>正文</title><body>" + "正文" * 5000 + "</body>"
    monkeypatch.setattr(module, "completion", completion)
    monkeypatch.setattr(module, "search_public", search)
    monkeypatch.setattr(module, "public_get", read)
    output = await module.run_openai(payload(web_enabled=True), lambda event: None)
    assert output["external_sources"][0]["level"] == "网页正文（部分已读）"
    assert output["external_sources"][0]["read_chunks"] == [0]


@pytest.mark.asyncio
@pytest.mark.parametrize("base", ["https://api.deepseek.com", "https://generativelanguage.googleapis.com/v1beta/openai"])
async def test_provider_http_endpoint_and_authentication(monkeypatch, base):
    monkeypatch.setenv("SIYE_RESEARCH_API_KEY", "isolated-test-key")
    def handler(request):
        assert str(request.url) == base + "/chat/completions"
        assert request.headers["Authorization"] == "Bearer isolated-test-key"
        assert json.loads(request.content)["stream"] is False
        return httpx.Response(200, json=response())
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        assert "choices" in await module.completion(client, payload(base_url=base), [], [])


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [401, 403, 429, 400, 500])
async def test_provider_errors_do_not_expose_secrets(monkeypatch, status):
    monkeypatch.setenv("SIYE_RESEARCH_API_KEY", "private-key")
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(status, text="private-provider-error"))) as client:
        with pytest.raises(ValueError) as error:
            await module.completion(client, payload(), [], [])
        assert "private" not in str(error.value)


@pytest.mark.asyncio
async def test_provider_response_size_is_bounded(monkeypatch):
    monkeypatch.setenv("SIYE_RESEARCH_API_KEY", "isolated-key")
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, content=b"x" * (2 * 1024 * 1024 + 1)))) as client:
        with pytest.raises(ValueError, match="响应过大"):
            await module.completion(client, payload(), [], [])


@pytest.mark.asyncio
async def test_public_search_discards_private_and_broken_targets(monkeypatch):
    from api.services import research_web as web
    async def get(*args):
        return "https://html.duckduckgo.com/html/", '''<div class="result"><a class="result__a" href="http://127.0.0.1/private">Private</a></div>
            <div class="result"><a class="result__a" href="https://missing.example">Broken</a></div>
            <div class="result"><a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.org%2Farticle">Public</a><span class="result__snippet">Search snippet</span></div>'''
    async def target(url):
        if "127.0.0.1" in url:
            raise ValueError("private")
        if "missing" in url:
            raise OSError("missing")
        return url, "example.org"
    monkeypatch.setattr(web, "public_get", get)
    monkeypatch.setattr(web, "public_target", target)
    assert await web.search_public("路线") == [{"url": "https://example.org/article", "title": "Public", "snippet": "Search snippet"}]
