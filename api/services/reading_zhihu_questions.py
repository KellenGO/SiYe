"""Question-specific answer discovery using the already-working search path."""

import asyncio
import re
from urllib.parse import parse_qs, urlsplit

from aggregate_search.adapters.zhihu import ZhihuAdapter, search_reading_payload
from aggregate_search.models import clean_title
from .result_hydration import ResultHydrator


def question_reference(identity, url):
    if not re.fullmatch(r"[0-9]{1,30}", identity):
        raise ValueError("问题编号无效")
    try:
        parsed = urlsplit(url)
        if (parsed.scheme == "https" and parsed.hostname in {"zhihu.com", "www.zhihu.com"}
                and not parsed.username and not parsed.password and parsed.port in {None, 443}
                and parsed.path.rstrip("/") == f"/question/{identity}"):
            return f"https://www.zhihu.com/question/{identity}"
    except ValueError:
        pass
    raise ValueError("问题编号与原文链接不匹配")


def result_question(result):
    if result.platform != "zhihu" or result.content_type != "answer":
        return None
    parsed = urlsplit(result.url)
    match = re.fullmatch(r"/question/([0-9]{1,30})/answer/([0-9]{1,30})/?", parsed.path)
    if not match or match[2] != result.content_id:
        return None
    question_reference(match[1], f"{parsed.scheme}://{parsed.netloc}/question/{match[1]}")
    if result.question and result.question.id != match[1]:
        return None
    return {"id": match[1], "title": clean_title(result.question.title if result.question else result.title),
            "url": f"https://www.zhihu.com/question/{match[1]}"}


async def fetch_question_page(snapshot, title, offset):
    from aggregate_search.worker import _ZHIHU_USER_AGENT
    hydrator = ResultHydrator()
    try:
        client = await hydrator._get_zhihu(snapshot)
        client.default_headers.update({"user-agent": _ZHIHU_USER_AGENT, "priority": "u=1, i",
            "referer": "https://www.zhihu.com/search?q=python&time_interval=a_year&type=content"})
        return await asyncio.wait_for(client.get("/api/v4/search_v3", {
            "gk_version": "gz-gaokao", "t": "general", "q": title, "correction": 1,
            "offset": offset, "limit": 20, "filter_fields": "", "lc_idx": offset,
            "show_all_topics": 0, "search_source": "Filter", "sort": "upvoted_count",
        }), timeout=15)
    finally:
        await hydrator.close()


def question_page(raw, identity, offset):
    if not isinstance(raw, dict) or not isinstance(raw.get("data"), list):
        raise ValueError("回答列表响应无效")
    adapter = ZhihuAdapter()
    answers, bodies = [], []
    for row in raw["data"][:20]:
        item = row.get("object") if isinstance(row, dict) else None
        if not isinstance(item, dict) or item.get("type") != "answer":
            continue
        question = item.get("question")
        answer_id = str(item.get("id") or "")
        if (not isinstance(question, dict) or str(question.get("id")) != identity
                or not re.fullmatch(r"[0-9]{1,30}", answer_id)):
            continue
        body = search_reading_payload(item)
        if body:
            bodies.append(body)
        answers.extend(adapter.adapt([item]))
    paging = raw.get("paging")
    if not isinstance(paging, dict) or not isinstance(paging.get("is_end"), bool):
        raise ValueError("回答分页响应无效")
    next_offset = offset + 20
    # 只取偏移，不把平台返回的地址当成下一次请求 URL。
    if not paging["is_end"] and isinstance(paging.get("next"), str):
        parsed = urlsplit(paging["next"])
        values = parse_qs(parsed.query).get("offset", [])
        if values and re.fullmatch(r"[0-9]{1,8}", values[0]) and int(values[0]) > offset:
            next_offset = int(values[0])
    return answers, bodies, next_offset, paging["is_end"]
