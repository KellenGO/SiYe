"""Claude SDK adapter. No filesystem or shell capabilities are exposed."""

import asyncio
import json
import re
from datetime import datetime, timezone

from .research_config import agent_cli_path
from .research_documents import CHUNK_SIZE, MaterialAccess, ReadLoopGuard, RESEARCH_INSTRUCTIONS, SourceReferenceError, ToolInputError, ToolActivity, answer_document, model_external, read_material_section, research_context, result_document
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
    read_guard = ReadLoopGuard()

    def text_result(value):
        return {"content": [{"type": "text", "text": json.dumps(value, ensure_ascii=False)}]}

    @tool("manifest", "列出本次空间的来源、分段数和已读取情况", {})
    async def manifest(_):
        value = {"space": access.model_coverage(), "external": model_external(external.values())}
        read_guard.observe("manifest", {}, value)
        return text_result(value)

    @tool("read_material", "key 使用 S1/W1 等公开标识，按 body/comments/subtitles 读取；网页只有 body，index 从 0 开始，缺口不重试", {"key": str, "section": str, "index": int})
    async def read_material(args):
        try:
            if read_guard.exhausted:
                raise ToolInputError(read_guard.hint)
            value = read_material_section(access, external, web_texts, args["key"], args["section"], args["index"])
        except (KeyError, ValueError, TypeError) as error:
            value = {"error": str(error) if isinstance(error, ToolInputError) else "读取参数无效，使用公开 key、有效 section 和整数 index"}
        read_guard.observe("read_material", args, value)
        return {**text_result(value), **({"is_error": True} if "error" in value else {})}

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
            value = {**model_external([external[identity]])[0], "text": text[:CHUNK_SIZE]}
            read_guard.observe("read_webpage", args, value)
            return text_result(value)
        except Exception:
            web_errors.append("外部网页未能读取")
            value = {"error": "网页无法读取、地址受限或内容过大；不要声称已读取正文"}
            read_guard.observe("read_webpage", args, value)
            return {**text_result(value), "is_error": True}

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
        if not probe and read_guard.exhausted:
            return {"continue_": False, "stopReason": read_guard.hint,
                    "hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                    "permissionDecisionReason": read_guard.hint}}
        if name not in allowed:
            identity = activity.start("denied", "工具未开放")
            activity.finish(identity, "denied", "工具未开放", "此任务没有该工具的权限", failed=True)
            read_guard.observe(name, data.get("tool_input", {}), {"error": "此研究任务未开放该工具"})
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
            summary = "工具执行失败" if failed else "工具执行完成"
            if name == "mcp__research__read_material" and isinstance(response, dict):
                try:
                    value = json.loads(response["content"][0]["text"])
                    summary = (f"{value['title'][:200]} · {value['section']} 第 {value['index'] + 1} 段 · {len(value['text'])} 字符"
                        if "text" in value else value.get("error") or value.get("reason") or value.get("hint") or summary)
                except (KeyError, IndexError, TypeError, ValueError):
                    pass
            activity.finish(identity, name, name.removeprefix("mcp__research__"), summary, failed)
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
                json.dumps({"external": model_external(external.values())}, ensure_ascii=False)}}
        if read_guard.exhausted:
            return {"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": read_guard.hint}}
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
            if read_guard.exhausted and output is None and not answer.strip():
                raise ValueError("AI 重复工具调用未取得新证据，任务已停止；请缩小问题后重试")
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
