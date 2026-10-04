"""Isolated research process, one command per process, safe JSON events only."""

import asyncio
import json
import logging
import os
import sys


def emit(event):
    sys.stdout.buffer.write((json.dumps(event, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8"))
    sys.stdout.buffer.flush()


async def run(payload):
    # Platform client diagnostics can contain raw responses; research only emits its own events.
    logging.disable(logging.CRITICAL)
    if payload["mode"] == "collect":
        from .research_materials import MaterialCollector
        collector = MaterialCollector(payload.get("sessions"), emit=emit, workdir=payload.get("workdir"),
                                      session_errors=payload.get("session_errors"))
        try:
            for item in payload["items"]:
                previous = payload.get("previous", {}).get(item["key"])
                result = await collector.collect(item, previous)
                emit({"type": "material", "material": result, "completed": True})
        finally:
            await collector.close()
        return {"collected": True}
    from .research_agent import run_agent
    return await run_agent(payload, emit)


def main():
    try:
        payload = json.loads(sys.stdin.buffer.readline(16 * 1024 * 1024))
        result = asyncio.run(run(payload))
        emit({"type": "done", "result": result})
    except Exception as error:
        # Never forward provider errors, tracebacks, request bodies, keys or cookies.
        message = str(error) if isinstance(error, ValueError) and str(error).startswith(("AI ", "SDK ", "当前服务")) else "研究服务未能完成，请检查配置或重试"
        emit({"type": "error", "message": message})
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
