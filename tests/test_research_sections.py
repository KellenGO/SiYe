"""Section retrieval separates acquired content from actual evidence reads."""

import json

import pytest

from api.services.research_documents import CHUNK_SIZE, MAX_SECTION_CHARS, MaterialAccess, answer_document, result_document
from api.services.research_materials import component, material


def evidence():
    row = material({"key": "bilibili|one", "result": {"platform": "bilibili", "title": "视频",
        "url": "https://www.bilibili.com/video/one", "content_type": "video", "snippet": "简介不是正文"}})
    row["body"] = component("ok", text="正文证据")
    row["comments"] = component("ok", entries=[{"id": "c1", "text": "高赞观点" * 4000, "like_count": 100, "reply_count": 3}])
    row["subtitles"] = component("ok", entries=[{"start": i, "end": i + 1, "text": "讲述内容" * 400} for i in range(20)])
    return row


def test_sections_are_discoverable_and_exact_reads_are_independent():
    row = evidence()
    access = MaterialAccess([row])
    manifest = access.manifest()[0]
    assert set(manifest["sections"]) == {"body", "comments", "subtitles"}
    assert manifest["sections"]["body"]["chunks"] == 1
    assert manifest["sections"]["comments"]["count"] == 1
    assert manifest["sections"]["subtitles"]["count"] == 20
    assert access.chunk(row["key"], "body", 0) == "正文证据"
    access.chunk(row["key"], "body", 0)
    coverage = access.coverage()[0]
    assert coverage["read_chunks"] == 1 and not coverage["complete"]
    assert coverage["sections"]["comments"]["read_chunks"] == 0
    assert coverage["sections"]["subtitles"]["read_indices"] == []
    comments = []
    for index in range(manifest["sections"]["comments"]["chunks"]):
        chunk = access.chunk(row["key"], "comments", index)
        assert len(chunk) <= CHUNK_SIZE and "讲述内容" not in chunk
        comments.extend(json.loads(chunk))
    assert "".join(part["text"] for part in comments) == row["comments"]["entries"][0]["text"]
    assert all(part["like_count"] == 100 and part["id"] == "c1" for part in comments)
    for index in range(manifest["sections"]["subtitles"]["chunks"]):
        chunk = access.chunk(row["key"], "subtitles", index)
        assert len(chunk) <= CHUNK_SIZE and "高赞观点" not in chunk
        assert all(part["end"] == part["start"] + 1 for part in json.loads(chunk))
    assert access.coverage()[0]["complete"]
    # A new AI turn cannot inherit previous read accounting.
    assert MaterialAccess([row]).coverage()[0]["read_chunks"] == 0


@pytest.mark.parametrize("key,section,index", [
    ("other", "body", 0), ([], "body", 0), ("bilibili|one", "cookie", 0),
    ("bilibili|one", "subtitles", True), ("bilibili|one", "body", -1), ("bilibili|one", "body", 1)])
def test_invalid_sections_never_count_as_reads(key, section, index):
    access = MaterialAccess([evidence()])
    with pytest.raises(ValueError):
        access.chunk(key, section, index)
    assert access.coverage()[0]["read_chunks"] == 0


def test_absent_sections_do_not_turn_manifest_or_snippet_into_evidence():
    row = evidence()
    row["body"] = component("missing")
    access = MaterialAccess([row])
    with pytest.raises(ValueError):
        access.chunk(row["key"], "body", 0)
    with pytest.raises(ValueError, match="未读取"):
        answer_document("简介证据 [S1]", False, [row], coverage=access.coverage())


def test_cited_body_does_not_claim_comments_or_subtitles_were_read():
    row = evidence()
    access = MaterialAccess([row])
    access.chunk(row["key"], "body", 0)
    coverage = access.coverage()
    ordinary = answer_document("正文显示证据 [S1]", False, [row], coverage=coverage)
    structured = result_document({"sections": [{"kind": "space", "paragraphs": [
        {"text": "正文显示证据", "sources": [row["key"]]}]}]}, [row], [], coverage, False)
    for doc in (ordinary, structured):
        encoded = json.dumps(doc, ensure_ascii=False)
        assert "正文已读" not in encoded and "评论已读" not in encoded and "字幕已读" not in encoded
        assert "正文显示证据" in encoded and "href" in encoded
    assert coverage[0]["sections"]["body"]["read_chunks"] == 1
    assert coverage[0]["sections"]["comments"]["read_chunks"] == 0
    assert coverage[0]["sections"]["subtitles"]["read_chunks"] == 0


def test_oversized_section_is_bounded_and_manifest_reports_truncation():
    row = evidence()
    row["body"]["text"] = "a" * (MAX_SECTION_CHARS + 100)
    access = MaterialAccess([row])
    section = access.manifest()[0]["sections"]["body"]
    assert section["truncated"] and section["reason"]
    assert sum(len(access.chunk(row["key"], "body", i)) for i in range(section["chunks"])) == MAX_SECTION_CHARS


def test_long_single_comment_keeps_prefix_and_escaped_chunks_remain_valid():
    row = evidence()
    row["comments"]["entries"] = [{"id": "one", "author": "\x00" * 200, "text": "\x01" * (MAX_SECTION_CHARS + 1)}]
    access = MaterialAccess([row])
    section = access.manifest()[0]["sections"]["comments"]
    assert section["truncated"] and section["chunks"] > 0
    texts = []
    for index in range(section["chunks"]):
        chunk = access.chunk(row["key"], "comments", index)
        assert len(chunk) <= CHUNK_SIZE
        texts.extend(part["text"] for part in json.loads(chunk))
    assert len("".join(texts)) == MAX_SECTION_CHARS
