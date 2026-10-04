"""Claude SDK adapter. No filesystem or shell capabilities are exposed."""

import asyncio
import json
import re
from datetime import datetime, timezone

from .research_config import agent_cli_path
from .research_documents import CHUNK_SIZE, MaterialAccess, RESEARCH_INSTRUCTIONS, SourceReferenceError, ToolActivity, answer_document, research_context, result_document
from .research_web import page_text, public_get, public_target

async def run_agent(payload, emit):
    if payload.get("protocol", "anthropic") == "openai":
        from .research_openai import run_openai
        return await run_openai(payload, emit)
    from claude_agent_sdk import (AssistantMessage, TextBlock, ClaudeAgentOptions, ClaudeSDKClient, HookMatcher, PermissionResultAllow,
                                  PermissionResultDeny, ResultMessage, SystemMessage, create_sdk_mcp_server, tool)

    probe = payload.get("mode") == "probe"
    web_enabled = bool(payload.get("web_enabled"))
    access = MaterialAccess(payload.get("materials", []))
    external, web_texts, observed = {}, {}, set()
    web_errors = []
    search_called, fetch_called, probe_called = False, False, False
    activity = ToolActivity(emit)
    active_tools = {}

    def text_result(value):
        return {"content": [{"type": "text", "text": json.dumps(value, ensure_ascii=False)}]}

    @tool("manifest", "列出本次空间的来源、分段数和已读取情况", {})
    async def manifest(_):
        return text_result({"space": access.coverage(), "external": list(external.values())})

    @tool("read_material", "按 section 读取资料的一段：body/comments/subtitles；网页只有 body，index 从 0 开始", {"key": str, "section": str, "index": int})
    async def read_material(args):
        try:
            key, section, index = args["key"], args["section"], args["index"]
            if key in web_texts:
                if section != "body" or type(index) is not int or index < 0 or index * CHUNK_SIZE >= len(web_texts[key]):
                    raise ValueError("网页分段不存在")
                external[key].setdefault("read_chunks", [])
                if index not in external[key]["read_chunks"]:
                    external[key]["read_chunks"].append(index)
                external[key]["level"] = "网页正文（部分已读）" if len(external[key]["read_chunks"]) < external[key]["chunks"] else "网页正文"
                return text_result({"key": key, "citation": external[key]["citation"], "section": section, "index": index,
                    "text": web_texts[key][index * CHUNK_SIZE:(index + 1) * CHUNK_SIZE]})
            text = access.chunk(key, section, index)
            citation = access.citations[key]
            return text_result({"key": key, "citation": citation, "section": section, "index": index, "text": text})
        except (KeyError, ValueError, TypeError):
            return {**text_result({"error": "资料分段不可用，请检查 key、section、index 和获取缺口"}), "is_error": True}

    @tool("read_webpage", "联网开启时，读取公开网页并返回外部来源 id 与第一段；其余通过 read_material 读取", {"url": str})
    async def read_webpage(args):
        nonlocal fetch_called
        if not web_enabled:
            return {**text_result({"error": "本任务禁止联网"}), "is_error": True}
        try:
            url, html = await public_get(args["url"])
            title, text = page_text(html)
            if not text.strip():
                raise ValueError("网页没有可读取正文")
            identity = next((key for key, value in external.items() if value["url"] == url), f"web|{len(external) + 1}")
            external[identity] = {"id": identity, "citation": f"W{identity.split('|')[1]}", "url": url, "title": title,
                "level": "网页正文（部分已读）" if len(text) > CHUNK_SIZE else "网页正文",
                "fetched_at": datetime.now(timezone.utc).isoformat(), "chunks": (len(text) + CHUNK_SIZE - 1) // CHUNK_SIZE,
                "read_chunks": [0]}
            web_texts[identity] = text
            fetch_called = True
            emit({"type": "progress", "phase": "web", "message": "正在读取外部网页"})
            return text_result({**external[identity], "text": text[:CHUNK_SIZE]})
        except Exception:
            web_errors.append("外部网页未能读取")
            return {**text_result({"error": "网页无法读取、地址受限或内容过大；不要声称已读取正文"}), "is_error": True}

    @tool("check_connection", "连接测试：调用此工具确认 SDK 工具循环可用", {})
    async def check_connection(_):
        nonlocal probe_called
        probe_called = True
        return text_result({"ok": True})

    names = ["manifest", "read_material"] if not probe else ["check_connection"]
    functions = [manifest, read_material] if not probe else [check_connection]
    if web_enabled:
        names.append("read_webpage")
        functions.append(read_webpage)
    allowed = {f"mcp__research__{name}" for name in names}
    if web_enabled:
        allowed.add("WebSearch")
    async def guard(data, _tool_id, _context):
        name = data.get("tool_name", "")
        if name not in allowed:
            identity = activity.start("denied", "工具未开放")
            activity.finish(identity, "denied", "工具未开放", "此任务没有该工具的权限", failed=True)
            return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                    "permissionDecisionReason": "此研究任务未开放该工具"}}
        active_tools[_tool_id] = activity.start(name, name.removeprefix("mcp__research__"))
        return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "allow"}}

    async def permission(name, arguments, _context):
        if name in allowed:
            return PermissionResultAllow(updated_input=arguments)
        return PermissionResultDeny(message="此研究任务未开放该工具")

    async def post_tool(data, _tool_id, _context):
        nonlocal search_called
        name = data.get("tool_name", "")
        response = data.get("tool_response")
        failed = isinstance(response, dict) and bool(response.get("is_error") or response.get("error"))
        identity = active_tools.pop(_tool_id, None)
        if identity:
            activity.finish(identity, name, name.removeprefix("mcp__research__"), "工具执行失败" if failed else "工具执行完成", failed)
        if data.get("tool_name") == "WebSearch":
            response = data.get("tool_response")
            if isinstance(response, dict) and (response.get("is_error") or response.get("error")):
                web_errors.append("联网搜索失败")
            else:
                search_called = True
                # Search results are evidence of discovery, not of reading a page.
                encoded = json.dumps(response, ensure_ascii=False)
                for url in dict.fromkeys(re.findall(r'https?://[^\s"<>\\]+', encoded)):
                    url = url.rstrip(")],.;")
                    try:
                        await public_target(url)
                    except Exception:
                        continue
                    if any(row["url"] == url for row in external.values()):
                        continue
                    identity = f"web|{len(external) + 1}"
                    external[identity] = {"id": identity, "citation": f"W{identity.split('|')[1]}", "title": urlsplit_title(url), "url": url,
                        "level": "仅搜索摘要", "fetched_at": datetime.now(timezone.utc).isoformat(), "chunks": 0, "read_chunks": []}
        if name == "WebSearch":
            return {"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext":
                json.dumps({"external": list(external.values())}, ensure_ascii=False)}}
        return {}

    options = ClaudeAgentOptions(
        cli_path=str(agent_cli_path()), cwd=payload["workdir"],
        tools=["WebSearch"] if web_enabled else [], allowed_tools=list(allowed),
        setting_sources=[], settings="{}",
        strict_mcp_config=True, skills=[], plugins=[],
        system_prompt="" if probe else RESEARCH_INSTRUCTIONS, model=payload["model"],
        max_turns=80 if not probe else 8,
        mcp_servers={"research": create_sdk_mcp_server("research", tools=functions)},
        hooks={"PreToolUse": [HookMatcher(hooks=[guard])], "PostToolUse": [HookMatcher(hooks=[post_tool])]},
        can_use_tool=permission, permission_mode="default", stderr=lambda _: None,
        output_format=None,
        env={"ANTHROPIC_BASE_URL": payload["base_url"], "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1"},
    )
    if probe:
        prompt = "调用 check_connection 后返回 OK。"
        if web_enabled:
            prompt += "必须再用 WebSearch 搜索 Anthropic 官方文档，并调用 read_webpage 读取 https://example.com，然后返回 OK。"
    else:
        prompt = research_context(payload, access)
    async with ClaudeSDKClient(options=options) as client:
        for attempt in range(1 if probe else 2):
            output, answer = None, ""
            await client.query(prompt)
            async for message in client.receive_response():
                if isinstance(message, SystemMessage) and message.subtype == "init":
                    observed.update(message.data.get("tools", []))
                    if web_enabled and "WebSearch" not in observed:
                        raise ValueError("当前服务未提供联网搜索，请关闭联网后重新生成")
                    emit({"type": "progress", "phase": "analyzing", "message": "AI 正在阅读并整理资料"})
                if isinstance(message, ResultMessage):
                    if message.is_error or message.subtype != "success":
                        raise ValueError("AI 请求失败或达到轮次限制，请检查服务配置后重试")
                    output = message.structured_output
                    answer = message.result or answer
                    emit({"type": "usage", "usage": message.usage, "cost_usd": message.total_cost_usd})
                elif isinstance(message, AssistantMessage):
                    answer = "\n".join(block.text for block in message.content if isinstance(block, TextBlock))
                    if any(not isinstance(block, TextBlock) for block in message.content):
                        activity.commentary(answer)
            if probe:
                if not probe_called or (web_enabled and not (search_called and fetch_called)):
                    raise ValueError("SDK 工具或联网测试未通过，不能确认服务兼容")
                return {"connection_ok": True, "web_ok": web_enabled}
            coverage = access.coverage()
            for row in external.values():
                if row["chunks"]:
                    row["level"] = "网页正文（部分已读）" if len(row["read_chunks"]) < row["chunks"] else "网页正文"
            try:
                document = (result_document(output, list(access.materials.values()), list(external.values()), coverage, web_enabled)
                    if output is not None else answer_document(answer, web_enabled, list(access.materials.values()), list(external.values()), coverage))
                return {"document": document, "coverage": coverage, "external_sources": list(external.values()), "web_errors": web_errors}
            except SourceReferenceError as error:
                if attempt:
                    raise
                prompt = str(error)


def urlsplit_title(url):
    from urllib.parse import urlsplit
    return urlsplit(url).hostname or "外部网页"
