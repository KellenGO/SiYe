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


class Provider(BaseHTTPRequestHandler):
    block = False
    entered = threading.Event()
    resume = threading.Event()
    exposed = set()

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
        names = [row["name"] for row in data.get("tools", [])]
        self.exposed.update(names)
        used = {row.get("name") for message in data["messages"] for row in message.get("content", [])
                if isinstance(row, dict) and row.get("type") == "tool_use"}
        chosen = next((name for suffix in ("check_connection", "manifest", "read_material", "StructuredOutput")
                       for name in names if name.endswith(suffix) and name not in used), None)
        args = {"key": "xhs|one", "index": 0} if chosen and chosen.endswith("read_material") else {}
        if chosen == "StructuredOutput":
            args = {"sections": [{"kind": "space", "title": "发现", "paragraphs": [
                {"text": "隔离测试研究结果", "sources": ["xhs|one"]}]}]}
        content = {"type": "tool_use", "id": f"call_{len(used)}", "name": chosen, "input": args} if chosen else {"type": "text", "text": "OK"}
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
                "type": "input_json_delta", "partial_json": json.dumps(args)} if chosen else {"type": "text_delta", "text": "OK"}}),
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
    credentials = {"model": "claude-sonnet-4-6", "base_url": f"http://127.0.0.1:{server.server_port}", "api_key": "isolated-test-key"}
    try:
        with TemporaryDirectory(prefix="research-smoke-", dir=ROOT / "build") as directory:
            jobs_module.library_data_root = lambda: Path(directory)
            manager = jobs_module.ResearchJobs()
            result = await manager.process({"elapsed": 510}, {"mode": "probe", "web_enabled": False}, credentials)
            assert result["connection_ok"]
            source = material({"key": "xhs|one", "result": {"platform": "xhs", "title": "测试来源", "url": "https://example.com/one",
                                                          "snippet": "简介", "content_type": "note"}})
            source.update(body=component("ok", text="完整正文"), comments=component("ok"))
            result = await manager.process({"elapsed": 510}, {"mode": "analyze", "web_enabled": False, "materials": [source],
                "space_name": "测试空间", "description": ""}, credentials)
            assert result["coverage"][0]["complete"] and result["document"]["content"]
            assert Provider.exposed <= {"mcp__research__manifest", "mcp__research__read_material", "mcp__research__check_connection", "StructuredOutput"}
            Provider.block = True
            pending = asyncio.create_task(manager.process({"elapsed": 510}, {"mode": "probe", "web_enabled": False}, credentials))
            assert await asyncio.to_thread(Provider.entered.wait, 20)
            processes = []
            if os.name == "nt":
                import win32job
                processes = win32job.QueryInformationJobObject(handles[-1], win32job.JobObjectBasicProcessIdList)
                assert len(processes) >= 2, "Expected the worker and its native agent child"
            pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)
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
            assert not list((Path(directory) / "research-tmp").iterdir()), "Temporary source files remain"
        print("PASS: real SDK tool loop, structured note, tool restrictions, process-tree cancellation and temporary cleanup")
    finally:
        Provider.resume.set()
        server.shutdown()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--exe", help="Use a built SiYe.exe research worker")
    args = parser.parse_args()
    (ROOT / "build").mkdir(exist_ok=True)
    asyncio.run(smoke(args.exe))
