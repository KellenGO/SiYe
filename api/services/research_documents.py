"""Trusted rich-text conversion and bounded source access for AI results."""

import json
import re
from datetime import datetime
from urllib.parse import urlsplit, urlunsplit

from .space_notes import validate_note

CHUNK_SIZE = 8000


class ToolActivity:
    """Public execution records, separate from provider reasoning and raw arguments."""
    def __init__(self, emit):
        self.emit = emit
        self.counter = 0

    def start(self, tool, message):
        self.counter += 1
        identity = f"step-{self.counter}"
        self.emit({"type": "activity", "id": identity, "tool": tool, "message": message, "status": "running"})
        return identity

    def finish(self, identity, tool, message, summary, failed=False):
        self.emit({"type": "activity", "id": identity, "tool": tool, "message": message,
                   "status": "failed" if failed else "completed", "summary": summary})

    def commentary(self, text):
        if isinstance(text, str) and text.strip():
            self.counter += 1
            self.emit({"type": "activity", "id": f"step-{self.counter}", "tool": "assistant",
                       "message": text[:4000], "status": "completed", "kind": "commentary"})


def answer_document(text, web_enabled):
    """Accept ordinary assistant text without imposing a research output schema."""
    if not isinstance(text, str) or not text.strip():
        raise ValueError("AI 未返回回答，请重试")
    content = [{"type": "heading", "attrs": {"level": 2}, "content": [{"type": "text", "text": "AI 回答"}]},
               {"type": "paragraph", "content": [{"type": "text", "text": f"生成于 {datetime.now().astimezone().strftime('%Y-%m-%d %H:%M')} · 联网补充：{'开启' if web_enabled else '关闭'}"}]}]
    def inline(value):
        nodes = []
        for part in re.split(r"(\*\*[^*\n]+\*\*)", value):
            if part:
                bold = bool(re.fullmatch(r"\*\*[^*\n]+\*\*", part))
                nodes.append({"type": "text", "text": part[2:-2] if bold else part,
                              **({"marks": [{"type": "bold"}]} if bold else {})})
        return nodes

    in_code = False
    for line in text.strip().splitlines():
        if line.startswith("```"):
            in_code = not in_code
            content.append({"type": "paragraph", "content": inline(line)})
            continue
        heading = None if in_code else re.match(r"^(#{1,6})\s+(.+)$", line)
        item = None if in_code else re.match(r"^\s*(?:([-*+])\s+|(\d+)\.\s+)(.+)$", line)
        if heading:
            content.append({"type": "heading", "attrs": {"level": min(len(heading[1]), 3)}, "content": inline(heading[2])})
        elif item:
            kind = "bulletList" if item[1] else "orderedList"
            node = {"type": "listItem", "content": [{"type": "paragraph", "content": inline(item[3])}]}
            if content[-1]["type"] == kind:
                content[-1]["content"].append(node)
            else:
                start = max(1, min(int(item[2][:6]), 100000)) if item[2] else 1
                content.append({"type": kind, **({"attrs": {"start": start}} if item[2] else {}), "content": [node]})
        else:
            content.append({"type": "paragraph", **({"content": inline(line)} if line else {})})
    document = {"type": "doc", "content": content}
    validate_note(document)
    return document


def public_url(url):
    parsed = urlsplit(url)
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))


class MaterialAccess:
    def __init__(self, materials):
        self.materials = {item["key"]: item for item in materials}
        self.read = {key: set() for key in self.materials}

    def encoded(self, key):
        item = self.materials[key]
        return json.dumps({"title": item["title"], "snippet": item.get("snippet", ""),
                           "body": item["body"], "comments": item["comments"],
                           "subtitles": item["subtitles"]}, ensure_ascii=False)

    def manifest(self):
        return [{"key": key, "title": item["title"], "platform": item["platform"],
                 "chunks": (len(self.encoded(key)) + CHUNK_SIZE - 1) // CHUNK_SIZE,
                 "url": item["url"]} for key, item in self.materials.items()]

    def chunk(self, key, index):
        if key not in self.materials or type(index) is not int:
            raise ValueError("资料不属于本次空间任务")
        encoded = self.encoded(key)
        if index < 0 or index * CHUNK_SIZE >= len(encoded):
            raise ValueError("资料分段不存在")
        self.read[key].add(index)
        return encoded[index * CHUNK_SIZE:(index + 1) * CHUNK_SIZE]

    def coverage(self):
        return [{**row, "read_chunks": len(self.read[row["key"]]),
                 "complete": len(self.read[row["key"]]) == row["chunks"]} for row in self.manifest()]


RESULT_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "sections": {"type": "array", "items": {"type": "object", "additionalProperties": False,
            "properties": {"kind": {"type": "string", "enum": ["space", "web", "gaps"]},
                "title": {"type": "string"}, "paragraphs": {"type": "array", "items": {
                    "type": "object", "additionalProperties": False,
                    "properties": {"text": {"type": "string"}, "sources": {"type": "array", "items": {"type": "string"}}},
                    "required": ["text", "sources"]}}}, "required": ["kind", "title", "paragraphs"]}}},
    "required": ["sections"]}


def result_document(result, materials, external, coverage, web_enabled):
    if not isinstance(result, dict) or not isinstance(result.get("sections"), list) or not result["sections"]:
        raise ValueError("AI 未返回有效的研究笔记，请重试")
    sources = {item["key"]: {"title": item["title"], "url": item["url"], "kind": "space"} for item in materials}
    sources.update({item["id"]: {**item, "kind": "web"} for item in external})
    read_sources = {row["key"] for row in coverage if row.get("read_chunks", 0) > 0}
    content = []

    def paragraph(text):
        return {"type": "paragraph", "content": [{"type": "text", "text": text}]}

    content.append({"type": "heading", "attrs": {"level": 2}, "content": [{"type": "text", "text": "AI 研究笔记"}]})
    content.append(paragraph(f"生成于 {datetime.now().astimezone().strftime('%Y-%m-%d %H:%M')} · 联网补充：{'开启' if web_enabled else '关闭'}"))
    for kind, label in (("space", "空间资料结论"), ("web", "联网补充"), ("gaps", "分歧与信息缺口")):
        if kind == "web" and not web_enabled:
            continue
        sections = [section for section in result["sections"] if isinstance(section, dict) and section.get("kind") == kind]
        if not sections:
            continue
        content.append({"type": "heading", "attrs": {"level": 2}, "content": [{"type": "text", "text": label}]})
        for section in sections:
            if section.get("title"):
                content.append({"type": "heading", "attrs": {"level": 3}, "content": [{"type": "text", "text": str(section["title"])}]})
            for row in section.get("paragraphs", []):
                if not isinstance(row, dict) or not isinstance(row.get("text"), str) or not row["text"].strip():
                    raise ValueError("AI 笔记段落无效")
                refs = row.get("sources", [])
                if not isinstance(refs, list) or any(not isinstance(ref, str) or ref not in sources for ref in refs):
                    raise ValueError("AI 返回了未读取来源的引用，请重试")
                if any(sources[ref]["kind"] == "space" and ref not in read_sources for ref in refs):
                    raise ValueError("AI 返回了未读取来源的引用，请重试")
                if any(sources[ref]["kind"] == "web" for ref in refs) and (not web_enabled or kind == "space"):
                    raise ValueError("AI 混淆了空间资料与外部来源，请重试")
                node = paragraph(row["text"])
                for ref in dict.fromkeys(refs):
                    node["content"].extend([{"type": "text", "text": " "}, {"type": "text", "text": f"[{sources[ref]['title']}]",
                        "marks": [{"type": "link", "attrs": {"href": sources[ref]["url"], "target": "_blank", "rel": "noopener noreferrer"}}]}])
                content.append(node)
    unread = [row["title"] for row in coverage if not row["complete"]]
    if unread:
        content.append(paragraph("未完整分析的空间资料：" + "、".join(unread)))
    labels = {"body": "正文", "comments": "评论", "subtitles": "字幕"}
    states = {"missing": "未提供可读内容", "restricted": "访问受限", "failed": "读取失败", "ok": "部分内容"}
    gaps = [f"{item['title']}：{labels[name]} {states.get(item[name]['state'], '读取未完成')}（{item[name].get('reason') or '见资料读取情况'}）"
            for item in materials for name in labels
            if item[name]["state"] not in {"ok", "not_applicable"} or item[name].get("truncated")]
    if gaps:
        content.append(paragraph("资料获取缺口：" + "；".join(gaps)))
    if web_enabled and not external:
        content.append(paragraph("联网补充未取得可引用的外部来源。"))
    if external:
        content.append(paragraph("外部来源读取记录：" + "；".join(f"{row['title']}（{row['level']}，{row['fetched_at']}）" for row in external)))
    document = {"type": "doc", "content": content}
    validate_note(document)
    return document
