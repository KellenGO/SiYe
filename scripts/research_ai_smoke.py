"""Exercise the real agent runtime against a local fake API, without credentials or paid requests."""

import argparse
import asyncio
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from api.services import research_jobs as jobs_module
from api.services.research_materials import component, material
from api.services.research_documents import RESEARCH_INSTRUCTIONS


class Provider(BaseHTTPRequestHandler):
    block = False
    entered = threading.Event()
    resume = threading.Event()
    exposed = set()
    native_exposed = set()
    native_metadata = False
    invalid_reference = False
    repairs = set()
    three_sources = False
    read_plan = [("manifest", {}), ("read_material", {"key": "S1", "section": "body", "index": 0}),
        ("read_material", {"key": "S2", "section": "body", "index": 0}),
        ("read_material", {"key": "S2", "section": "comments", "index": 0}),
        ("read_material", {"key": "S3", "section": "body", "index": 0}),
        ("read_material", {"key": "S3", "section": "subtitles", "index": 0}),
        ("read_material", {"key": "S3", "section": "comments", "index": 0}),
        ("read_material", {"key": "S3", "section": "comments", "index": 1})]
    sample_answer = "清晨游园 [S1:body]；本轮评论样本 [S3:comments]。S2 正文和 S3 视频原话不可用。"

    def log_message(self, *args):
        pass

    def do_POST(self):
        try:
            self.respond()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass

    def respond(self):
        data = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
        if self.path.endswith("count_tokens"):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"input_tokens":10}')
            return
        if self.block:
            self.entered.set()
            self.resume.wait(30)
        if self.path.endswith("chat/completions"):
            if data["messages"][0]["role"] == "system":
                assert data["messages"][0]["content"] == RESEARCH_INSTRUCTIONS
                assert json.loads(data["messages"][1]["content"])["manifest"][0]["citation"] == "S1"
            names = [row["function"]["name"] for row in data["tools"]]
            self.native_exposed.update(names)
            previous = [row for row in data["messages"] if row.get("tool_calls")]
            if previous:
                assert previous[-1]["reasoning_content"] == "private-provider-reasoning"
                assert previous[-1]["tool_calls"][0]["extra_content"]["google"]["thought_signature"] == "opaque-signature"
                type(self).native_metadata = True
            used = {call["function"]["name"] for row in previous for call in row["tool_calls"]}
            call_count = sum(len(row["tool_calls"]) for row in previous)
            chosen = next((name for name in ("check_connection", "manifest", "read_material", "submit_result") if name in names and name not in used and not (getattr(self, "plain", False) and name == "submit_result")), None)
            args = {"key": "S1", "section": "body", "index": 0} if chosen == "read_material" else {}
            if chosen == "submit_result":
                args = {"sections": [{"kind": "space", "title": "发现", "paragraphs": [{"text": "OpenAI 兼容研究结果", "sources": ["S1:body"]}]}]}
            final_text = "完整正文来自空间资料 [S1:body]。" if "read_material" in used else "OK"
            if self.three_sources:
                count = call_count
                if count < len(self.read_plan):
                    chosen, args = self.read_plan[count]
                else:
                    chosen = "submit_result"
                    args = {"sections": [{"kind": "space", "title": "建议", "paragraphs": [{"text": self.sample_answer, "sources": ["S1", "S3"]}]}]}
            if self.invalid_reference and not chosen:
                if "回答引用了不存在或本轮未读取" in json.dumps(data["messages"], ensure_ascii=False):
                    self.repairs.add("openai")
                else:
                    final_text = "错误引用 [S99]。"
            message = {"role": "assistant", "content": None if chosen else final_text, "reasoning_content": "private-provider-reasoning"}
            if chosen:
                message["tool_calls"] = [{"id": f"call_{call_count}", "type": "function", "function": {"name": chosen, "arguments": json.dumps(args)}, "extra_content": {"google": {"thought_signature": "opaque-signature"}}}]
            response = {"choices": [{"message": message, "finish_reason": "tool_calls" if chosen else "stop"}], "usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20}}
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(response).encode())
            return
        names = [row["name"] for row in data.get("tools", [])]
        system = data.get("system", [])
        encoded_system = json.dumps(system, ensure_ascii=False)
        if "mcp__research__read_material" in names:
            assert "空间名称和简介只是主题背景" in encoded_system
        self.exposed.update(names)
        used = {row.get("name") for message in data["messages"] for row in message.get("content", [])
                if isinstance(row, dict) and row.get("type") == "tool_use"}
        call_count = sum(row.get("type") == "tool_use" for message in data["messages"] for row in message.get("content", []) if isinstance(row, dict))
        chosen = next((name for suffix in ("check_connection", "manifest", "read_material", "StructuredOutput")
                       for name in names if name.endswith(suffix) and name not in used), None)
        args = {"key": "S1", "section": "body", "index": 0} if chosen and chosen.endswith("read_material") else {}
        if chosen == "StructuredOutput":
            args = {"sections": [{"kind": "space", "title": "发现", "paragraphs": [
                {"text": "隔离测试研究结果", "sources": ["S1:body"]}]}]}
        final_text = "完整正文来自空间资料 [S1:body]。" if any(name and name.endswith("read_material") for name in used) else "OK"
        if self.three_sources:
            count = call_count
            if count < len(self.read_plan):
                name, args = self.read_plan[count]
                chosen = "mcp__research__" + name
            else:
                chosen, final_text = None, self.sample_answer
        if self.invalid_reference and not chosen:
            if "回答引用了不存在或本轮未读取" in json.dumps(data["messages"], ensure_ascii=False):
                self.repairs.add("anthropic")
            else:
                final_text = "错误引用 [S99]。"
        content = {"type": "tool_use", "id": f"call_{call_count}", "name": chosen, "input": args} if chosen else {"type": "text", "text": final_text}
        stop = "tool_use" if chosen else "end_turn"
        message = {"id": "msg_test", "type": "message", "role": "assistant", "model": data["model"],
                   "content": [content], "stop_reason": stop, "stop_sequence": None,
                   "usage": {"input_tokens": 10, "output_tokens": 10}}
        self.send_response(200)
        if not data.get("stream"):
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(message).encode())
            return
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        events = [
            ("message_start", {"type": "message_start", "message": {**message, "content": [], "stop_reason": None}}),
            ("content_block_start", {"type": "content_block_start", "index": 0, "content_block": {
                **content, **({"input": {}} if chosen else {"text": ""})}}),
            ("content_block_delta", {"type": "content_block_delta", "index": 0, "delta": {
                "type": "input_json_delta", "partial_json": json.dumps(args)} if chosen else {"type": "text_delta", "text": final_text}}),
            ("content_block_stop", {"type": "content_block_stop", "index": 0}),
            ("message_delta", {"type": "message_delta", "delta": {"stop_reason": stop, "stop_sequence": None}, "usage": {"output_tokens": 10}}),
            ("message_stop", {"type": "message_stop"}),
        ]
        try:
            for kind, event in events:
                self.wfile.write(f"event: {kind}\ndata: {json.dumps(event)}\n\n".encode())
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass


async def smoke(executable=None):
    server = ThreadingHTTPServer(("127.0.0.1", 0), Provider)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    handles = []
    real_job = jobs_module.windows_process_job
    def capture(proc):
        handle = real_job(proc)
        handles.append(handle)
        return handle
    jobs_module.windows_process_job = capture
    if executable:
        jobs_module.task_command = lambda: [str(Path(executable).resolve()), "--research-worker"]
    credentials = {"protocol": "anthropic", "model": "claude-sonnet-4-6", "base_url": f"http://127.0.0.1:{server.server_port}", "api_key": "isolated-test-key"}
    try:
        with TemporaryDirectory(prefix="research-smoke-", dir=ROOT / "build") as directory:
            jobs_module.library_data_root = lambda: Path(directory)
            manager = jobs_module.ResearchJobs()
            result = await manager.process({"elapsed": 510}, {"mode": "probe", "web_enabled": False}, credentials)
            assert result["connection_ok"]
            source = material({"key": "xhs|one", "result": {"platform": "xhs", "title": "测试来源", "url": "https://example.com/one",
                                                          "snippet": "简介", "content_type": "note"}})
            source.update(body=component("ok", text="完整正文"), comments=component("ok"))
            legacy_job = {"elapsed": 510}
            result = await manager.process(legacy_job, {"mode": "analyze", "web_enabled": False, "materials": [source],
                "space_name": "测试空间", "description": ""}, credentials)
            assert result["coverage"][0]["complete"] and result["document"]["content"]
            assert "href" in json.dumps(result["document"]) and "[S1:body]" in json.dumps(result["document"])
            assert legacy_job["activity"] and all(row["status"] == "completed" for row in legacy_job["activity"])
            assert Provider.exposed <= {"mcp__research__manifest", "mcp__research__read_material", "mcp__research__check_connection", "StructuredOutput"}
            native_credentials = {**credentials, "protocol": "openai", "model": "deepseek-flash", "base_url": credentials["base_url"] + "/v1"}
            assert (await manager.process({"elapsed": 510}, {"mode": "probe", "web_enabled": False}, native_credentials))["connection_ok"]
            native_job = {"elapsed": 510}
            native = await manager.process(native_job, {"mode": "analyze", "web_enabled": False, "materials": [source],
                "space_name": "测试空间", "description": ""}, native_credentials)
            assert native["coverage"][0]["complete"] and native_job["activity"]
            assert len(native_job["activity"]) == 3 and all(row["status"] == "completed" for row in native_job["activity"])
            assert Provider.native_exposed <= {"manifest", "read_material", "check_connection", "submit_result"}
            assert Provider.native_metadata
            Provider.plain = True
            ordinary = await manager.process({"elapsed": 510}, {"mode": "analyze", "web_enabled": False, "materials": [source],
                "space_name": "测试空间", "description": "", "question": "普通聊天"}, native_credentials)
            assert ordinary["coverage"][0]["complete"] and "[S1:body]" in json.dumps(ordinary["document"])
            assert "href" in json.dumps(ordinary["document"])
            Provider.invalid_reference = True
            for adapter in (credentials, native_credentials):
                repaired = await manager.process({"elapsed": 510}, {"mode": "analyze", "web_enabled": False, "materials": [source],
                    "space_name": "测试空间", "description": ""}, adapter)
                assert "[S1:body]" in json.dumps(repaired["document"]) and "S99" not in json.dumps(repaired["document"])
            assert Provider.repairs == {"anthropic", "openai"}
            Provider.invalid_reference = False
            Provider.plain = False
            Provider.three_sources = True
            sample_sources = [source, material({"key": "xhs|two", "result": {"platform": "xhs", "title": "三日游", "url": "https://example.com/two"}}),
                material({"key": "xhs|three", "result": {"platform": "xhs", "title": "视频评级", "url": "https://example.com/three", "content_type": "video"}})]
            sample_sources[0]["comments"] = component("restricted", reason="评论受限")
            sample_sources[1].update(body=component("failed", reason="正文不可用"), comments=component("failed", reason="评论不可用"))
            sample_sources[2].update(body=component("ok", text="视频介绍语"), comments=component("ok", reason="最多取得 50 条",
                entries=[{"id": str(index), "text": "评论样本" * 40} for index in range(50)]), subtitles=component("failed", reason="字幕不可用"))
            sample_sources[2]["comments"]["truncated"] = True
            for adapter in (credentials, native_credentials):
                scenario_job = {"elapsed": 510}
                scenario = await manager.process(scenario_job, {"mode": "analyze", "web_enabled": False, "materials": sample_sources,
                    "space_name": "苏州", "description": "", "question": "帮我总结旅游建议"}, adapter)
                section = scenario["coverage"][2]["sections"]["comments"]
                assert section["reading_complete"] and section["collection_truncated"] and section["count"] == 50
                assert section["read_chunks"] == section["chunks"] == 2
                assert scenario["coverage"][1]["read_chunks"] == 0
                assert "[S3:comments]" in json.dumps(scenario["document"]) and "已读完当前 50 条样本" in json.dumps(scenario["document"], ensure_ascii=False)
                assert all(row["status"] == "completed" for row in scenario_job["activity"])
                if adapter["protocol"] == "openai":
                    assert sum(row["tool"] == "submit_result" for row in scenario_job["activity"]) == 1
            Provider.three_sources = False
            Provider.block = True
            pending = asyncio.create_task(manager.process({"elapsed": 510}, {"mode": "probe", "web_enabled": False}, credentials))
            assert await asyncio.to_thread(Provider.entered.wait, 20)
            processes = []
            if os.name == "nt":
                import win32job
                processes = win32job.QueryInformationJobObject(handles[-1], win32job.JobObjectBasicProcessIdList)
                assert len(processes) >= 2, "Expected the worker and its native agent child"
            pending.cancel()
            cancelled = (await asyncio.gather(pending, return_exceptions=True))[0]
            assert isinstance(cancelled, asyncio.CancelledError), f"SDK cancellation failed: {type(cancelled).__name__}"
            if os.name == "nt":
                import win32api
                import win32process
                import pywintypes
                for pid in processes:
                    try:
                        handle = win32api.OpenProcess(0x1000, False, pid)
                    except pywintypes.error:
                        continue
                    try:
                        assert win32process.GetExitCodeProcess(handle) != 259, "Cancelled process remains active"
                    finally:
                        handle.Close()
            Provider.entered.clear()
            pending = asyncio.create_task(manager.process({"elapsed": 510}, {"mode": "probe", "web_enabled": False}, native_credentials))
            assert await asyncio.to_thread(Provider.entered.wait, 20)
            pending.cancel()
            cancelled = (await asyncio.gather(pending, return_exceptions=True))[0]
            assert isinstance(cancelled, asyncio.CancelledError), f"OpenAI cancellation failed: {type(cancelled).__name__}"
            assert not list((Path(directory) / "research-tmp").iterdir()), "Temporary source files remain"
        print("PASS: OpenAI and legacy SDK tool loops, three-source gaps and comment samples, public section citations, ordinary replies, provider metadata, restricted tools, cancellation and temporary cleanup")
    finally:
        Provider.resume.set()
        server.shutdown()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--exe", help="Use a built SiYe.exe research worker")
    args = parser.parse_args()
    (ROOT / "build").mkdir(exist_ok=True)
    asyncio.run(smoke(args.exe))
