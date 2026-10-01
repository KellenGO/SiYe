"""Validate the small, versioned rich-text document supported by research spaces."""

import json
from urllib.parse import urlsplit

MAX_NOTE_LENGTH = 50_000
MAX_NOTE_BYTES = 2 * 1024 * 1024
FONT_SIZES = {f"{size}px" for size in (14, 16, 18, 20, 24, 28, 32)}
EMPTY_NOTE = {"type": "doc", "content": [{"type": "paragraph"}]}
BLOCKS = {"paragraph", "heading", "bulletList", "orderedList", "taskList"}


def validate_note(document: dict, format_version: int = 1) -> str:
    if format_version != 1:
        raise ValueError("不支持的笔记格式版本")
    encoded = json.dumps(document, ensure_ascii=False, allow_nan=False)
    if len(encoded.encode("utf-8")) > MAX_NOTE_BYTES:
        raise ValueError("笔记格式数据不能超过 2 MB")
    count = 0

    def visit(node, depth=0):
        nonlocal count
        if depth > 32 or not isinstance(node, dict) or set(node) - {"type", "attrs", "content", "text", "marks"}:
            raise ValueError("笔记结构无效")
        kind = node.get("type")
        allowed = {"doc": BLOCKS, "paragraph": {"text", "hardBreak"}, "heading": {"text", "hardBreak"},
                   "bulletList": {"listItem"}, "orderedList": {"listItem"}, "taskList": {"taskItem"},
                   "listItem": BLOCKS, "taskItem": BLOCKS, "text": set(), "hardBreak": set()}
        if not isinstance(kind, str) or kind not in allowed:
            raise ValueError("笔记包含不支持的内容")
        attrs = node.get("attrs", {})
        if not isinstance(attrs, dict):
            raise ValueError("笔记属性无效")
        if kind == "heading":
            if set(attrs) != {"level"} or type(attrs["level"]) is not int or attrs["level"] not in (1, 2, 3):
                raise ValueError("标题级别无效")
        elif kind == "orderedList":
            if set(attrs) - {"start", "type"} or type(attrs.get("start", 1)) is not int or not 1 <= attrs.get("start", 1) <= 100_000 or attrs.get("type") not in (None, "1"):
                raise ValueError("编号列表属性无效")
        elif kind == "taskItem":
            if set(attrs) != {"checked"} or type(attrs["checked"]) is not bool:
                raise ValueError("勾选清单属性无效")
        elif attrs:
            raise ValueError("笔记包含不支持的属性")
        if kind == "text":
            if not isinstance(node.get("text"), str) or not node["text"]:
                raise ValueError("笔记文字无效")
            count += len(node["text"])
        elif "text" in node:
            raise ValueError("笔记文字结构无效")
        marks = node.get("marks", [])
        if not isinstance(marks, list) or (marks and kind != "text"):
            raise ValueError("笔记文字样式无效")
        seen = set()
        for mark in marks:
            if not isinstance(mark, dict) or set(mark) - {"type", "attrs"}:
                raise ValueError("笔记文字样式无效")
            name, values = mark.get("type"), mark.get("attrs", {})
            if not isinstance(name, str) or name in seen or not isinstance(values, dict):
                raise ValueError("笔记文字样式无效")
            seen.add(name)
            if name in {"bold", "italic", "underline"} and not values:
                continue
            if name == "textStyle" and set(values) == {"fontSize"} and isinstance(values["fontSize"], str) and values["fontSize"] in FONT_SIZES:
                continue
            if name == "link" and not set(values) - {"href", "target", "rel", "class"}:
                if not isinstance(values.get("href"), str):
                    raise ValueError("笔记链接无效")
                url = urlsplit(values.get("href", ""))
                if url.scheme in {"http", "https"} and url.netloc and not url.username and not url.password and values.get("target") in (None, "_blank") and values.get("class") is None and values.get("rel") in (None, "noopener noreferrer nofollow", "noopener noreferrer"):
                    continue
            raise ValueError("笔记文字样式或链接无效")
        children = node.get("content", [])
        if not isinstance(children, list):
            raise ValueError("笔记内容无效")
        if kind in {"doc", "bulletList", "orderedList", "taskList", "listItem", "taskItem"} and not children:
            raise ValueError("笔记段落无效")
        if kind in {"listItem", "taskItem"} and (not isinstance(children[0], dict) or children[0].get("type") != "paragraph"):
            raise ValueError("列表项必须以段落开始")
        for child in children:
            if not isinstance(child, dict) or child.get("type") not in allowed[kind]:
                raise ValueError("笔记段落结构无效")
            visit(child, depth + 1)

    if not isinstance(document, dict) or document.get("type") != "doc":
        raise ValueError("笔记必须是文档")
    visit(document)
    if count > MAX_NOTE_LENGTH:
        raise ValueError("笔记正文不能超过 50000 字")
    return encoded
