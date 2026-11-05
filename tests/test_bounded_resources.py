# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

"""有界性与"失败可见"的回归测试。

这里锁的是 2026-11-05 审查里找到的几类"没有上限"与"失败被吞掉"的问题：
研究 payload 体积、正文缓存、历史列表、外部 URL 校验、转写 stdout 容错。
"""

from __future__ import annotations

import asyncio
import json

import pytest

from api.services import research_jobs as jobs_module
from api.services import watch_history_store
from aggregate_search.adapters.bilibili import BilibiliAdapter


# ── 研究 payload 体积闸门 ───────────────────────────────────────────────

def _payload(materials, chars):
    return {
        "mode": "analyze",
        "materials": [
            {"key": f"k{i}", "title": "t", "sections": {k: "字" * chars
             for k in ("body", "comments", "transcript", "evidence")}}
            for i in range(materials)
        ],
        "space_name": "空间",
        "question": "问题",
        "evidence_cache": {"x": "y" * 200000},
        "conversation": [{"role": "user", "content": "c" * 2000}] * 30,
    }


def test_small_payload_is_not_touched():
    payload = {"mode": "analyze", "materials": [{"key": "k", "sections": {"body": "短"}}],
               "space_name": "空间", "question": "问题"}
    out, trimmed = jobs_module.fit_payload(payload)
    assert trimmed is False
    assert out is payload  # 没超限时原样返回，不做无谓的深拷贝


def test_oversized_payload_is_trimmed_below_the_worker_readline_limit():
    """子进程用 readline(16MB) 读一行 JSON；超限会被截断成"研究服务未能完成"。"""
    payload = _payload(20, 256000)
    assert jobs_module._payload_size(payload) > jobs_module.MAX_PAYLOAD_BYTES

    out, trimmed = jobs_module.fit_payload(payload)
    assert trimmed is True
    size = jobs_module._payload_size(out)
    assert size <= jobs_module.MAX_PAYLOAD_BYTES
    # 必须显著小于子进程上限，给 JSON 转义留余量。
    assert size < 16 * 1024 * 1024
    # 结构还在：资料条数不因截断而丢失。
    assert len(out["materials"]) == len(payload["materials"])


def test_trimming_prefers_shrinking_bodies_before_dropping_history():
    payload = _payload(10, 256000)
    out, _ = jobs_module.fit_payload(payload)
    if len(out.get("conversation") or []) == len(payload["conversation"]):
        # 只靠压缩正文就够了，正文必须被压短。
        assert len(out["materials"][0]["sections"]["body"]) <= jobs_module.TRIMMED_SECTION_CHARS


def test_trimming_does_not_mutate_the_caller_payload():
    payload = _payload(20, 256000)
    before = json.dumps(payload, ensure_ascii=False)
    jobs_module.fit_payload(payload)
    assert json.dumps(payload, ensure_ascii=False) == before


# ── 正文缓存必须有界 ───────────────────────────────────────────────────

def test_search_body_cache_is_bounded():
    from api.services.reading import ReadingService, MAX_SEARCH_BODIES

    service = ReadingService()
    for index in range(MAX_SEARCH_BODIES * 3):
        service._remember_search_body(("zhihu", 0, "answer", str(index)), {"blocks": []})
    assert len(service.search_bodies) == MAX_SEARCH_BODIES
    # 最新写入的必须还在（LRU 淘汰最旧的）。
    assert ("zhihu", 0, "answer", str(MAX_SEARCH_BODIES * 3 - 1)) in service.search_bodies


def test_question_groups_are_bounded():
    from api.services.reading import ReadingService, MAX_QUESTION_GROUPS

    service = ReadingService()
    for index in range(MAX_QUESTION_GROUPS * 3):
        service._question_state(0, str(index), "标题")
    assert len(service.questions) == MAX_QUESTION_GROUPS
    assert (0, str(MAX_QUESTION_GROUPS * 3 - 1)) in service.questions


# ── 观看历史列表必须有 LIMIT ───────────────────────────────────────────

def test_list_views_applies_a_hard_limit_when_caller_omits_one(tmp_path):
    store = watch_history_store.ViewHistoryStore(tmp_path / "history.db")

    for index in range(30):
        store.record_view({
            "platform": "xhs", "content_id": str(index), "content_type": "note",
            "title": f"标题{index}", "author": "作者",
            "url": f"https://example.com/{index}", "cover_url": None,
            "metrics": {}, "rank": index,
        })
    everything = store.list_views()
    assert len(everything["items"]) <= watch_history_store.MAX_LIST_LIMIT
    assert everything["total"] == 30  # total 仍是真实总数，分页不受影响

    capped = store.list_views(limit=watch_history_store.MAX_LIST_LIMIT * 10)
    assert len(capped["items"]) <= watch_history_store.MAX_LIST_LIMIT


# ── 外部 URL 只认主机，不认前缀 ────────────────────────────────────────

@pytest.mark.parametrize("url", [
    "https://www.bilibili.com.evil.net/video/BV1xx",
    "https://evil.net/?u=https://www.bilibili.com/video/BV1xx",
    "http://www.bilibili.com/video/BV1xx",
    "javascript:alert(1)",
    "",
    None,
])
def test_bilibili_rejects_hosts_that_only_look_like_bilibili(url):
    assert BilibiliAdapter._allowed_bilibili_url(url) is False


@pytest.mark.parametrize("url", [
    "https://www.bilibili.com/video/BV1xx",
    "https://bilibili.com/video/BV1xx",
    "https://m.bilibili.com/video/BV1xx",
])
def test_bilibili_accepts_real_hosts(url):
    assert BilibiliAdapter._allowed_bilibili_url(url) is True


# ── 转写 stdout 混入非 JSON 时不能丢掉已识别分片 ───────────────────────

class _FakeStdout:
    def __init__(self, lines):
        self._lines = list(lines)

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._lines:
            raise StopAsyncIteration
        return self._lines.pop(0)


class _FakeProc:
    def __init__(self, lines):
        self.stdout = _FakeStdout(lines)
        self.returncode = 0

    async def wait(self):
        return 0


@pytest.mark.asyncio
async def test_transcription_skips_garbage_lines_instead_of_failing():
    from api.services.research_transcription import read_asr_output

    good = json.dumps({"type": "segment",
                       "entry": {"start": 0.0, "end": 3.0, "text": "识别出来的字幕"},
                       "duration": 10.0}, ensure_ascii=False).encode() + b"\n"
    proc = _FakeProc([
        b"not json at all\n",          # 混入的半行
        b"\xff\xfe broken utf8\n",     # 非法字节
        good,
        b'{"type": "done", "result": {"ok": true}}\n',
    ])
    partial = []
    progress = []
    result = await read_asr_output(proc, progress.append, partial)
    # 关键点：脏行被跳过，已经识别出的分片仍在。
    assert partial, "遇到非 JSON 行不应该丢掉已识别分片"
    assert result is not None
