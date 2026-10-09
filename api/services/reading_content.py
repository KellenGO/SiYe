# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
# This file is part of MediaCrawler project.
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

"""Turn Zhihu HTML into inert, bounded reading blocks."""

import json
import re
from urllib.parse import urlsplit, urlunsplit

from parsel import Selector

MAX_HTML_CHARS = 2 * 1024 * 1024
MAX_TEXT_CHARS = 256000
MAX_BLOCKS = 4000
LIMITED_NOTICE = "原文标记了付费或截断状态，当前只展示平台返回的可访问内容。"
IGNORED = {"script", "style", "noscript", "template", "form", "input", "button", "svg"}
EMBEDDED = {"iframe", "video", "audio", "object", "embed"}
LIMITED_CLASSES = {"richcontent-inner--collapsed", "paid-content", "paywall", "content-truncated"}


def reading_reference(kind: str, identity: str, url: str) -> str:
    """Only canonical platform routes matching the requested identity are fetched."""
    if kind not in {"answer", "article"} or not re.fullmatch(r"[0-9]{1,30}", identity):
        raise ValueError("当前仅支持知乎回答和文章")
    try:
        parsed = urlsplit(url)
        if parsed.scheme != "https" or parsed.username or parsed.password or parsed.port not in {None, 443}:
            raise ValueError
        if kind == "answer" and parsed.hostname in {"www.zhihu.com", "zhihu.com"}:
            match = re.fullmatch(r"/(?:question/([0-9]+)/)?answer/([0-9]+)/?", parsed.path)
            if match and match[2] == identity:
                question = f"question/{match[1]}/" if match[1] else ""
                return f"https://www.zhihu.com/{question}answer/{identity}"
        if kind == "article" and parsed.hostname == "zhuanlan.zhihu.com" and parsed.path.rstrip("/") == f"/p/{identity}":
            return f"https://zhuanlan.zhihu.com/p/{identity}"
    except ValueError:
        pass
    raise ValueError("内容编号与知乎原文链接不一致")


def reading_image(raw: str) -> str | None:
    try:
        parsed = urlsplit("https:" + raw if raw.startswith("//") else raw)
        host = (parsed.hostname or "").lower()
        if not (host == "zhimg.com" or host.endswith(".zhimg.com")):
            return None
        if parsed.username or parsed.password or parsed.scheme not in {"http", "https"}:
            return None
        if parsed.port not in {None, 80 if parsed.scheme == "http" else 443}:
            return None
        return urlunsplit(("https", host, parsed.path, parsed.query, ""))
    except ValueError:
        return None


def detail_from_page(page_html: str, kind: str, identity: str) -> dict:
    if len(page_html) > MAX_HTML_CHARS:
        raise ValueError("page too large")
    raw = Selector(text=page_html).xpath("//script[@id='js-initialData']/text()").get()
    data = json.loads(raw or "{}")
    entities = data.get("initialState", {}).get("entities", {})
    rows = entities.get("answers" if kind == "answer" else "articles", {})
    detail = rows.get(identity)
    if not isinstance(detail, dict) or str(detail.get("id")) != identity:
        raise ValueError("matching detail missing")
    return detail


def truthy(value) -> bool:
    return value is True or value == 1 or isinstance(value, str) and value.lower() in {"true", "1", "yes"}


def reading_detail(detail: dict, kind: str, identity: str) -> dict:
    if not isinstance(detail, dict) or str(detail.get("id")) != identity:
        raise ValueError("detail identity mismatch")
    denied = any(detail.get(key) is False or detail.get(key) == 0 or detail.get(key) in ("false", "0")
                 for key in ("is_visible", "is_public", "can_read") if key in detail)
    if denied or any(truthy(detail.get(key)) for key in ("is_deleted", "is_hidden")):
        raise PermissionError("restricted")
    content = detail.get("content")
    if not isinstance(content, str) or len(content) > MAX_HTML_CHARS:
        raise ValueError("body missing or too large")
    value = parse_reading_html(content)
    flags = ("is_paid", "is_premium", "is_truncated", "truncated", "content_truncated", "is_limited", "has_more_content", "is_content_hidden")
    paid_info = detail.get("paid_info") if isinstance(detail.get("paid_info"), dict) else {}
    if any(truthy(row.get(key)) for row in (detail, paid_info) for key in flags):
        value["limited"] = True
        value["notices"].append(LIMITED_NOTICE)
    if not value["blocks"] and not value["limited"]:
        raise ValueError("empty body")
    return {"content_id": identity, "content_type": kind, **value,
            "notices": list(dict.fromkeys(value["notices"]))}


def parse_reading_html(content: str) -> dict:
    if len(content) > MAX_HTML_CHARS:
        raise ValueError("body too large")
    blocks, notices = [], []
    limited, used = False, 0

    def emit(kind, text="", **extra):
        nonlocal limited, used
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        if kind != "code":
            text = re.sub(r"[\t\f \u00a0]+", " ", text).strip()
        if not text and kind not in {"image", "unsupported"}:
            return
        if len(blocks) >= MAX_BLOCKS or used >= MAX_TEXT_CHARS:
            limited = True
            return
        text = text[:MAX_TEXT_CHARS - used]
        if text or kind in {"image", "unsupported"}:
            blocks.append({"type": kind, "text": text, **extra})
            used += len(text)

    def image(node):
        for field in ("data-original", "data-actualsrc", "data-src", "src"):
            url = reading_image(node.get(field) or "")
            if url:
                emit("image", url=url, text=(node.get("alt") or "")[:200])
                return
        emit("unsupported", "此图片暂不可用，请在原文中查看。")

    def restricted(node):
        nonlocal limited
        marker = bool(set((node.get("class") or "").lower().split()) & LIMITED_CLASSES)
        marker |= any(node.get(key) is not None and node.get(key).lower() not in {"false", "0", "no"}
                      for key in ("data-paid", "data-paywall", "data-truncated"))
        if marker:
            limited = True
            notices.append(LIMITED_NOTICE)
        return marker

    def inline(node, kind, **extra):
        buffer = [node.text or ""]

        def flush():
            emit(kind, "".join(buffer), **extra)
            buffer.clear()

        def child(current):
            tag = str(current.tag).lower()
            if tag in IGNORED or restricted(current):
                return
            if tag == "img":
                flush()
                image(current)
            elif tag in EMBEDDED or tag == "table" or tag == "math" or current.get("data-tex") is not None:
                flush()
                emit("unsupported", "原文中的表格、公式或嵌入媒体请在原平台查看。")
            elif tag == "br":
                buffer.append("\n")
            else:
                buffer.append(current.text or "")
                for nested in current:
                    child(nested)
                    buffer.append(nested.tail or "")
        for current in node:
            child(current)
            buffer.append(current.tail or "")
        flush()

    def visit(node, quote=False):
        if len(blocks) >= MAX_BLOCKS or used >= MAX_TEXT_CHARS:
            return
        tag = str(node.tag).lower()
        if tag in IGNORED or restricted(node):
            return
        if tag == "img":
            image(node)
        elif tag in EMBEDDED or tag in {"table", "math"} or node.get("data-tex") is not None:
            emit("unsupported", "原文中的表格、公式或嵌入媒体请在原平台查看。")
        elif tag == "pre":
            emit("code", "".join(node.itertext()))
        elif re.fullmatch(r"h[1-6]", tag):
            inline(node, "heading", level=int(tag[1]))
        elif tag in {"p", "li"}:
            inline(node, "quote" if quote else "list-item" if tag == "li" else "paragraph")
        else:
            emit("quote" if quote or tag == "blockquote" else "paragraph", node.text or "")
            for child in node:
                visit(child, quote or tag == "blockquote")
                emit("quote" if quote else "paragraph", child.tail or "")

    root = Selector(text="<div>" + content + "</div>").xpath("//body/div")[0].root
    visit(root)
    if used >= MAX_TEXT_CHARS or len(blocks) >= MAX_BLOCKS:
        limited = True
        notices.append("正文较长，当前保留前部内容，请在原文中继续阅读。")
    return {"blocks": blocks, "limited": limited, "notices": list(dict.fromkeys(notices))}
