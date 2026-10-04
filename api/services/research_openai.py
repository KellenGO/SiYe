"""OpenAI Chat Completions tool loop restricted to the selected research sources."""

import json
import os
from datetime import datetime, timezone

import httpx

from .research_documents import CHUNK_SIZE, MaterialAccess, RESEARCH_INSTRUCTIONS, RESULT_SCHEMA, SourceReferenceError, ToolActivity, answer_document, public_url, research_context, result_document
from .research_web import page_text, public_get, search_public


def function(name, description, properties=None, required=None):
    return {"type": "function", "function": {"name": name, "description": description,
        "parameters": {"type": "object", "properties": properties or {}, "required": required or []}}}


async def completion(client, payload, messages, tools):
    try:
        async with client.stream("POST", payload["base_url"].rstrip("/") + "/chat/completions",
                headers={"Authorization": "Bearer " + os.environ["SIYE_RESEARCH_API_KEY"]},
                json={"model": payload["model"], "messages": messages, "tools": tools,
                      "tool_choice": "auto", "stream": False}) as response:
            if response.status_code in {401, 403}:
                raise ValueError("AI 服务拒绝访问，请检查 API Key 和模型权限")
            if response.status_code == 429:
                raise ValueError("AI 服务限流或余额不足，请稍后重试或检查账户")
            if response.status_code >= 400:
                raise ValueError("AI 请求失败，请检查基础地址、模型及工具调用兼容性")
            chunks, size = [], 0
            async for chunk in response.aiter_bytes():
                size += len(chunk)
                if size > 2 * 1024 * 1024:
                    raise ValueError("AI 响应过大，请缩小研究范围")
                chunks.append(chunk)
            data = json.loads(b"".join(chunks))
            if not isinstance(data, dict):
                raise ValueError("AI 服务未返回有效响应")
            return data
    except (httpx.HTTPError, KeyError, json.JSONDecodeError):
        raise ValueError("AI 服务未返回有效响应，请检查配置或稍后重试") from None


async def run_openai(payload, emit):
    probe = payload.get("mode") == "probe"
    web = bool(payload.get("web_enabled"))
    access = MaterialAccess(payload.get("materials", []))
    external, texts, web_errors = {}, {}, []
    checked, searched, fetched = False, False, False
    activity = ToolActivity(emit)
    tools = [function("check_connection", "验证工具调用后再回复 OK")] if probe else [
        function("manifest", "列出选定空间资料及外部来源、分段数、读取情况"),
        function("read_material", "读取选定资料或已获取网页的一个分段",
                 {"key": {"type": "string"}, "index": {"type": "integer"}}, ["key", "index"]),
        {"type": "function", "function": {"name": "submit_result", "description": "完成来源读取后提交研究结论",
                                              "parameters": RESULT_SCHEMA}}]
    if web:
        tools += [function("search_web", "搜索公开网页，只返回搜索摘要",
                           {"query": {"type": "string"}}, ["query"]),
                  function("read_webpage", "读取公开网页正文，不读取登录网页、音视频或本机文件",
                           {"url": {"type": "string"}}, ["url"])]
    allowed = {row["function"]["name"] for row in tools}
    question = "调用 check_connection，然后回复 OK。" if probe else research_context(payload, access)
    if probe and web:
        question += "先用 search_web 搜索 OpenAI 官方文档，再用 read_webpage 读取 https://example.com。"
    messages = ([] if probe else [{"role": "system", "content": RESEARCH_INSTRUCTIONS}])
    messages.append({"role": "user", "content": question})
    citation_repaired = False
    usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}

    def finish(output=None, text=None):
        for row in external.values():
            if row["chunks"]:
                row["level"] = "网页正文（部分已读）" if len(row["read_chunks"]) < row["chunks"] else "网页正文"
        coverage = access.coverage()
        document = (answer_document(text, web, list(access.materials.values()), list(external.values()), coverage) if text is not None else
                    result_document(output, list(access.materials.values()), list(external.values()), coverage, web))
        return {"document": document, "coverage": coverage, "external_sources": list(external.values()), "web_errors": web_errors}

    async with httpx.AsyncClient(timeout=120, trust_env=False, follow_redirects=False) as client:
        for _ in range(8 if probe else 80):
            emit({"type": "progress", "phase": "analyzing", "message": "正在等待模型回复"})
            response = await completion(client, payload, messages, tools)
            reported = response.get("usage") or {}
            if not isinstance(reported, dict):
                reported = {}
            for key in usage:
                count = reported.get(key, 0)
                if type(count) is int and count >= 0:
                    usage[key] += count
            emit({"type": "usage", "usage": usage.copy(), "cost_usd": None})
            try:
                choice = response["choices"][0]
                message = choice["message"]
                if choice.get("finish_reason") in {"length", "content_filter"}:
                    raise ValueError("AI 回答被截断或服务限制，请缩小研究范围后重试")
                if not isinstance(message, dict):
                    raise KeyError("message")
            except (KeyError, IndexError, TypeError):
                raise ValueError("AI 服务未返回有效的聊天响应") from None
            # Preserve DeepSeek reasoning_content and Gemini thought signatures exactly.
            message = {key: value for key, value in message.items() if key in {
                "role", "content", "tool_calls", "reasoning_content", "extra_content"}}
            message["role"] = "assistant"
            messages.append(message)
            calls = message.get("tool_calls") or []
            if not isinstance(calls, list) or len(calls) > 32:
                raise ValueError("AI 返回了无效的工具调用")
            if not calls:
                if probe:
                    if checked and (not web or (searched and fetched)):
                        return {"connection_ok": True, "web_ok": web}
                    raise ValueError("AI 工具或联网测试未通过，不能确认服务兼容")
                text = message.get("content") or ""
                try:
                    encoded = text.strip()
                    if encoded.startswith("```json") or encoded.startswith("```\n"):
                        encoded = encoded.split("\n", 1)[1].rsplit("```", 1)[0]
                    output = json.loads(encoded)
                except (ValueError, AttributeError, IndexError):
                    output = None
                try:
                    return finish(output) if isinstance(output, dict) and "sections" in output else finish(text=text)
                except SourceReferenceError as error:
                    if citation_repaired:
                        raise
                    citation_repaired = True
                    messages.append({"role": "user", "content": str(error)})
                    continue
            activity.commentary(message.get("content"))
            for call in calls:
                declaration = call.get("function") if isinstance(call, dict) else None
                if not isinstance(declaration, dict) or not isinstance(declaration.get("name"), str):
                    raise ValueError("AI 返回了无效的工具调用")
                name = declaration["name"]
                identity = call.get("id") if isinstance(call, dict) else None
                if not isinstance(identity, str) or not identity:
                    raise ValueError("AI 工具调用缺少标识")
                result = None
                tool_name = name if name in allowed else "denied"
                label, summary = tool_name, "工具执行完成"
                step = activity.start(tool_name, label)
                try:
                    if name not in allowed:
                        raise ValueError("本次研究没有开放此工具")
                    args = json.loads(call["function"]["arguments"])
                    if not isinstance(args, dict):
                        raise ValueError("工具参数无效")
                    if name == "check_connection":
                        checked = True
                        result = {"ok": True}
                    elif name == "manifest":
                        summary = f"{len(access.materials)} 条空间资料，{len(external)} 条外部来源"
                        result = {"space": access.coverage(), "external": list(external.values())}
                    elif name == "read_material":
                        key, index = args["key"], args["index"]
                        if key in texts:
                            if type(index) is not int or index < 0 or index * CHUNK_SIZE >= len(texts[key]):
                                raise ValueError("网页分段不存在")
                            if index not in external[key]["read_chunks"]:
                                external[key]["read_chunks"].append(index)
                            external[key]["level"] = "网页正文（部分已读）" if len(external[key]["read_chunks"]) < external[key]["chunks"] else "网页正文"
                            text = texts[key][index * CHUNK_SIZE:(index + 1) * CHUNK_SIZE]
                        else:
                            text = access.chunk(key, index)
                        title = (external.get(key) or access.materials.get(key) or {}).get("title", "资料")
                        summary = f"{title[:200]} · 第 {index + 1} 段 · {len(text)} 字符"
                        citation = external[key]["citation"] if key in external else access.citations[key]
                        result = {"key": key, "citation": citation, "index": index, "text": text}
                    elif name == "search_web":
                        try:
                            rows = await search_public(args["query"])
                            for row in rows:
                                key = next((key for key, value in external.items() if value["url"] == row["url"]), f"web|{len(external) + 1}")
                                if key not in external:
                                    external[key] = {"id": key, "citation": f"W{key.split('|')[1]}", "url": row["url"], "title": row["title"], "level": "仅搜索摘要",
                                        "fetched_at": datetime.now(timezone.utc).isoformat(), "chunks": 0, "read_chunks": []}
                                row["id"] = key
                                row["citation"] = external[key]["citation"]
                                row["level"] = "仅搜索摘要"
                            searched = True
                            summary = f"取得 {len(rows)} 条公开搜索结果"
                            result = rows
                        except Exception:
                            web_errors.append("公开搜索未能完成")
                            raise ValueError("公开搜索暂不可用，不要声称已检索来源") from None
                    elif name == "read_webpage":
                        try:
                            url, html = await public_get(args["url"])
                            title, text = page_text(html)
                            if not text.strip():
                                raise ValueError("网页没有正文")
                            key = next((key for key, value in external.items() if value["url"] == url), f"web|{len(external) + 1}")
                            external[key] = {"id": key, "citation": f"W{key.split('|')[1]}", "url": url, "title": title[:200],
                                "level": "网页正文（部分已读）" if len(text) > CHUNK_SIZE else "网页正文",
                                "fetched_at": datetime.now(timezone.utc).isoformat(), "chunks": (len(text) + CHUNK_SIZE - 1) // CHUNK_SIZE,
                                "read_chunks": [0]}
                            texts[key] = text
                            fetched = True
                            summary = f"{title[:200]} · {public_url(url)} · {len(text)} 字符"
                            result = {**external[key], "text": text[:CHUNK_SIZE]}
                        except Exception:
                            web_errors.append("外部网页未能读取")
                            raise ValueError("网页受限、没有正文或内容过大，不要声称已读取") from None
                    elif name == "submit_result":
                        if len(calls) != 1:
                            raise ValueError("完成资料读取后，再单独提交结论")
                        output = finish(args)
                        activity.finish(step, tool_name, label, "结果已提交")
                        return output
                except (ValueError, KeyError, TypeError):
                    # Never reflect provider arguments, source text or credentials in logs/errors.
                    result = {"error": "工具调用未成功，请检查参数、可用来源或读取缺口后重试"}
                    summary = result["error"]
                activity.finish(step, tool_name, label, summary, isinstance(result, dict) and "error" in result)
                messages.append({"role": "tool", "tool_call_id": identity, "content": json.dumps(result, ensure_ascii=False)})
    raise ValueError("AI 达到工具调用轮次上限，请缩小研究范围后重试")
