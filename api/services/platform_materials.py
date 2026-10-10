"""Read selected platform posts without modifying saved search snapshots."""

import asyncio
import html
import math
import re
import time
from contextlib import AsyncExitStack
from urllib.parse import parse_qs, urlsplit

from .material_contract import MAX_SECTION_CHARS, public_url
from .public_network import public_get
from .result_hydration import ResultHydrator
from .research_transcription import TranscriptionService, asr_python
from .acquisition_diagnostics import REASONS, AcquisitionError, classify, diagnostic


def clean_text(value):
    if not isinstance(value, str):
        return ""
    from parsel import Selector
    return html.unescape(" ".join(Selector(text=f"<div>{value}</div>").xpath("//div//text()").getall())).strip()


def component(state="missing", text="", entries=None, reason=""):
    return {"state": state, "text": text, "entries": entries or [], "reason": reason, "truncated": False}


def bound_component(value):
    used = len(value["text"])
    limited = used > MAX_SECTION_CHARS
    value["text"] = value["text"][:MAX_SECTION_CHARS]
    entries = []
    for row in value["entries"]:
        text = row.get("text", "")
        if len(entries) >= 10000 or used + len(text) > MAX_SECTION_CHARS:
            limited = True
            remaining = max(0, MAX_SECTION_CHARS - used)
            if remaining and len(entries) < 10000:
                entries.append({**row, "text": text[:remaining]})
            break
        entries.append(row)
        used += len(text)
    value["entries"] = entries
    if limited:
        value.update(truncated=True, reason=(value.get("reason") + "；" if value.get("reason") else "") + "内容超过单项上限，保留前部内容")
    return value


def material(item):
    source = item["result"]
    return {"key": item["key"], "platform": source["platform"], "title": source["title"],
            "url": public_url(source["url"]), "snippet": source.get("snippet") or "",
            "body": component(), "comments": component(),
            "subtitles": component("missing" if source.get("content_type") in {"video", "zvideo"} else "not_applicable")}


def failure(error, platform="", section="", stage=""):
    code, status = classify(error, stage)
    value = component("restricted" if code in {"login_required", "session_expired", "rate_limited", "restricted"} else "failed",
                      reason=REASONS[code])
    value["diagnostics"] = diagnostic(platform, section, stage, code=code, http_status=status)
    return value


def normalize_comments(rows, platform, parent=None, limit=50):
    output, seen = [], set()

    def add(row, parent_id=None):
        if not isinstance(row, dict) or len(output) >= limit:
            return
        identity = str(row.get("id") or row.get("cid") or row.get("rpid") or "")
        text = clean_text(row.get("text") or (row.get("content", {}).get("message") if isinstance(row.get("content"), dict) else row.get("content")))
        if not identity or not text or identity in seen:
            return
        seen.add(identity)
        author = row.get("user_info") or row.get("user") or row.get("member") or row.get("author") or {}
        output.append({"id": identity, "parent_id": parent_id, "text": text,
                       "author": str(author.get("nickname") or author.get("uname") or author.get("name") or "")})
        for metric, fields in (("like_count", ("like_count", "digg_count", "like", "vote_count")),
                               ("reply_count", ("sub_comment_count", "reply_comment_total", "rcount", "child_comment_count"))):
            for field in fields:
                if row.get(field) is not None:
                    try:
                        output[-1][metric] = max(0, int(row[field]))
                    except (TypeError, ValueError, OverflowError):
                        pass
                    break
        children = row.get("sub_comments") or row.get("reply_comment") or row.get("replies") or row.get("child_comments") or []
        if isinstance(children, list):
            for child in children[:3]:
                add(child, identity)

    for row in rows:
        add(row, parent)
    return output


def subtitle_entries(data):
    if isinstance(data, dict):
        data = data.get("body") or data.get("subtitles") or []
    if not isinstance(data, list):
        return []
    entries = []
    for row in data:
        if not isinstance(row, dict):
            continue
        try:
            text = clean_text(row.get("content") or row.get("text"))
            start, end = float(row.get("from", row.get("start", 0))), float(row.get("to", row.get("end", 0)))
            if text and math.isfinite(start) and math.isfinite(end) and 0 <= start <= end:
                entries.append({"text": text, "start": start, "end": end})
        except (TypeError, ValueError):
            continue
    return entries


class MaterialCollector(ResultHydrator):
    def __init__(self, sessions=None, emit=lambda _: None, workdir=None, session_errors=None):
        super().__init__()
        self.sessions = sessions or {}
        self.session_errors = session_errors or {}
        self.last_request = 0.0
        self.playwright = None
        self.douyin_crawler = None
        self.browser_sessions = AsyncExitStack()
        from .platform_browser import ResearchBrowserProvider
        self.browser_provider = ResearchBrowserProvider(self.browser_sessions, self.sessions)
        self.fallback_diagnostics = {}
        self.douyin_error = None
        self.emit = emit
        self.transcription = TranscriptionService(temporary_root=workdir)

    async def request(self, method, *args, **kwargs):
        await asyncio.sleep(max(0, self.last_request + 1.0 - time.monotonic()))
        self.last_request = time.monotonic()
        return await asyncio.wait_for(method(*args, **kwargs), timeout=25)

    async def browser_fallback(self, platform, section, stage, method, *args):
        value = diagnostic(platform, section, stage, provider="browser", fallback_attempted=True, fallback_outcome="failed")
        self.fallback_diagnostics[section] = value
        response = await self.request(method, *args)
        value["fallback_outcome"] = "succeeded"
        return response

    async def douyin_client(self):
        if self.douyin_error is not None:
            raise self.douyin_error
        if "douyin" not in self._clients:
            from media_platform.douyin.core import DouYinCrawler
            from .accounts import platform_session_context
            from base.crawler_runtime import CrawlerRuntimeOptions
            from base.runtime_paths import resource_path

            crawler = self.douyin_crawler = DouYinCrawler()
            crawler.runtime_options = CrawlerRuntimeOptions(headless=True, reuse_http_client=True, login_policy="fail_fast")
            owner, attached = AsyncExitStack(), False
            try:
                crawler.browser_context = await owner.enter_async_context(
                    platform_session_context("douyin", self.sessions.get("douyin")))
                await crawler.browser_context.add_init_script(path=str(resource_path("libs", "stealth.min.js")))
                async def block_media(route):
                    if route.request.resource_type in {"image", "media", "font"}:
                        await route.abort()
                    else:
                        await route.continue_()
                await crawler.browser_context.route("**/*", block_media)
                crawler.context_page = await crawler.browser_context.new_page()
                await crawler.context_page.goto(crawler.index_url, wait_until="domcontentloaded", timeout=20000)
                client = await crawler.create_douyin_client(None)
                if not await client.pong(browser_context=crawler.browser_context):
                    await client.aclose()
                    raise PermissionError("session required")
                self._clients["douyin"] = client
                self.browser_sessions.push_async_callback(owner.aclose)
                attached = True
            except Exception as error:
                code, status = classify(error, "session")
                self.douyin_error = AcquisitionError(code, status)
                raise self.douyin_error from None
            finally:
                if not attached:
                    await owner.aclose()
        return self._clients["douyin"]

    async def client(self, platform):
        snapshot = self.sessions.get(platform) or {}
        if platform in self.session_errors:
            error = self.session_errors[platform]
            raise AcquisitionError(error.get("code"), error.get("http_status"))
        if platform == "bilibili":
            from media_platform.bilibili.client import BilibiliClient
            if platform not in self._clients:
                self._clients[platform] = BilibiliClient(timeout=15, proxy=None, headers={
                    "User-Agent": "Mozilla/5.0", "Cookie": "; ".join(f"{key}={value}" for key, value in snapshot.items()),
                    "Origin": "https://www.bilibili.com", "Referer": "https://www.bilibili.com"},
                    playwright_page=None, cookie_dict=snapshot, reuse_http_client=True)
            return self._clients[platform]
        if platform == "xhs":
            if not snapshot.get("a1"):
                raise PermissionError("session required")
            return await self._get_xhs(snapshot)
        if platform == "zhihu":
            if not snapshot.get("d_c0"):
                raise PermissionError("session required")
            return await self._get_zhihu(snapshot)
        return await self.douyin_client()

    async def collect(self, item, previous=None):
        self.fallback_diagnostics = {}
        result = material(item) if previous is None else previous
        source, platform = item["result"], item["result"]["platform"]
        try:
            client = await self.client(platform)
        except PermissionError as error:
            for name in ("body", "comments", "subtitles"):
                if result[name]["state"] not in {"ok", "not_applicable"}:
                    result[name].update(failure(error, platform, name, "session"),
                                        entries=result[name]["entries"], text=result[name]["text"])
                    result[name]["truncated"] = bool(result[name]["entries"] or result[name]["text"])
            return result
        except Exception as error:
            for name in ("body", "comments", "subtitles"):
                if result[name]["state"] not in {"ok", "not_applicable"}:
                    failed = failure(error, platform, name, "session")
                    result[name].update(state=failed["state"], reason=failed["reason"],
                                        diagnostics=failed["diagnostics"],
                                        truncated=bool(result[name]["entries"] or result[name]["text"]))
            return result
        detail = None
        try:
            if platform == "bilibili":
                identity = source["content_id"]
                response = await self.request(client.get_video_info, bvid=identity if identity.upper().startswith("BV") else None,
                                              aid=int(identity) if identity.isdigit() else None)
                detail = response.get("View", response)
                body = clean_text(detail.get("desc"))
            elif platform == "xhs":
                query = parse_qs(urlsplit(source["url"]).query)
                detail = await self.request(client.get_note_by_id, source["content_id"],
                                            (query.get("xsec_source") or ["pc_search"])[0], (query.get("xsec_token") or [""])[0])
                body = clean_text(detail.get("desc") or detail.get("description"))
            elif platform == "douyin":
                params = {"aweme_id": source["content_id"]}
                try:
                    response = await self.request(client.get, "/aweme/v1/web/aweme/detail/", dict(params))
                except Exception as error:
                    if classify(error)[0] not in {"malformed_response", "restricted"}:
                        raise
                    response = await self.browser_fallback(platform, "body", "detail_api", self.browser_provider.douyin_detail, client, params)
                    result["body"]["diagnostics"] = diagnostic(platform, "body", "detail_api", provider="browser",
                        fallback_attempted=True, fallback_outcome="succeeded")
                self.check_douyin(response)
                detail = response.get("aweme_detail")
                if not isinstance(detail, dict) or not detail.get("aweme_id"):
                    raise AcquisitionError("malformed_response")
                body = clean_text(detail.get("desc"))
            else:
                kind, identity = source.get("content_type"), source["content_id"]
                if kind not in {"answer", "article", "zvideo"} or not str(identity).isdigit():
                    raise AcquisitionError("invalid_identity")
                if kind == "answer":
                    parts = urlsplit(source["url"]).path.strip("/").split("/")
                    if len(parts) != 4 or parts[0] != "question" or parts[-2] != "answer" or parts[-1] != str(identity) or not parts[1].isdigit():
                        raise AcquisitionError("invalid_identity")
                    detail = await self.request(client.get_answer_info, parts[-3], identity)
                else:
                    detail = await self.request(client.get_article_info if kind == "article" else client.get_video_info, identity)
                if detail is None:
                    raise AcquisitionError("malformed_response")
                body = clean_text(detail.content_text or detail.desc) if detail else ""
            if result["body"]["state"] != "ok":
                diagnostics = result["body"].get("diagnostics")
                result["body"] = component("ok" if body else "missing", text=body, reason="" if body else "原帖未返回正文")
                result["body"]["diagnostics"] = diagnostics or diagnostic(platform, "body", "detail_api")
        except Exception as error:
            if result["body"]["state"] != "ok":
                result["body"] = failure(error, platform, "body", "detail_api")
                if platform == "zhihu" and classify(error)[0] in {"restricted", "malformed_response"}:
                    try:
                        detail = await self.browser_fallback(platform, "body", "detail_page", self.browser_provider.zhihu_detail, client, source)
                        body = clean_text(detail.content_text or detail.desc)
                        result["body"] = component("ok" if body else "missing", text=body)
                    except Exception as fallback_error:
                        result["body"] = failure(fallback_error, platform, "body", "detail_page")
                    result["body"]["diagnostics"] = {**result["body"].get("diagnostics", {}),
                        **diagnostic(platform, "body", "detail_page", provider="browser", fallback_attempted=True,
                            fallback_outcome="succeeded" if result["body"]["state"] == "ok" else "failed")}
        previous_components = {name: result[name] for name in ("comments", "subtitles")}
        if result["comments"]["state"] != "ok":
            try:
                result["comments"] = await self.comments(client, source, detail)
            except Exception as error:
                result["comments"] = failure(error, platform, "comments", "comment_api")
                self.browser_provider.comment_diagnostic(result["comments"], platform)
        if result["subtitles"]["state"] not in {"ok", "not_applicable"}:
            try:
                result["subtitles"] = await self.subtitles(client, source, detail)
            except Exception as error:
                result["subtitles"] = failure(error, platform, "subtitles", "native_subtitle")
            if result["subtitles"]["entries"]:
                ends = {}
                for row in result["subtitles"]["entries"]:
                    part = row.get("part", 1)
                    ends[part] = max(ends.get(part, 0), row.get("end", 0))
                result["subtitles"]["metadata"] = {**result["subtitles"].get("metadata", {}), "source": "native", "duration": sum(ends.values())}
            # Publish acquired body/comments before potentially slow or cancelled ASR.
            for name in ("body", "comments", "subtitles"):
                bound_component(result[name])
            self.emit({"type": "material", "material": result})
            if not result["subtitles"]["entries"] and not previous_components["subtitles"]["entries"]:
                native = dict(result["subtitles"])
                try:
                    result["subtitles"] = await self.transcribe_subtitles(client, source, detail, result)
                except Exception as error:
                    result["subtitles"] = failure(error)
                if result["subtitles"]["state"] != "ok":
                    if native["state"] in {"restricted", "failed"} and result["subtitles"]["state"] == "missing":
                        result["subtitles"]["state"] = native["state"]
                    result["subtitles"]["reason"] = (native.get("reason") or "未取得平台原生字幕") + "；" + result["subtitles"]["reason"]
        for name, previous_component in previous_components.items():
            current = result[name]
            if current["state"] in {"missing", "failed", "restricted"} and previous_component["entries"]:
                combined = previous_component["entries"] + current["entries"]
                seen, merged = set(), []
                for row in combined:
                    identity = row.get("id") if name == "comments" else (row.get("part"), row.get("start"), row.get("text"))
                    if identity not in seen:
                        seen.add(identity)
                        merged.append(row)
                current.update(entries=merged[:50] if name == "comments" else merged, truncated=True)
                if name == "subtitles" and "metadata" not in current and previous_component.get("metadata"):
                    current["metadata"] = previous_component["metadata"]
        for name in ("body", "comments", "subtitles"):
            bound_component(result[name])
            result[name].setdefault("diagnostics", diagnostic(platform, name,
                {"body": "detail_api", "comments": "comment_api", "subtitles": "native_subtitle"}[name]))
            result[name]["diagnostics"]["platform"] = platform
            if name in self.fallback_diagnostics:
                if name == "subtitles" and result[name]["diagnostics"].get("provider") == "local_asr":
                    result[name]["diagnostics"].update(native_fallback_attempted=True,
                        native_fallback_outcome=self.fallback_diagnostics[name]["fallback_outcome"])
                else:
                    result[name]["diagnostics"].update(self.fallback_diagnostics[name])
        return result

    async def transcribe_subtitles(self, client, source, detail, result):
        identity = f"{source['platform']}|{source['content_id']}|primary"
        cached = self.transcription.cached(identity)
        if cached:
            return self.primary_transcript(cached, source, detail)
        if not asr_python():
            return component("missing", reason="平台无可读字幕且本地转写不可用：未安装可选 faster-whisper 组件")
        if not isinstance(detail, dict):
            return component("missing", reason="当前详情未保留稳定音频资源，本地转写不可用")
        try:
            url = await self.audio_resource(client, source, detail)
        except Exception as error:
            failed = failure(error, source["platform"], "subtitles", "playurl")
            failed["reason"] = "平台媒体资源地址未能取得，本地转写不可用；" + failed["reason"]
            return failed
        if not url:
            return component("missing", reason="平台详情没有可安全复用的音轨或媒体流，本地转写不可用")
        def progress(message):
            result["subtitles"].update(reason=message)
            self.emit({"type": "material", "material": result})
            self.emit({"type": "progress", "phase": "transcribing", "key": result["key"], "message": f"{result['title'][:80]} · {message}"})
        transcript = await self.transcription.transcribe(identity, url, result["url"], progress)
        return self.primary_transcript(transcript, source, detail)

    @staticmethod
    def primary_transcript(transcript, source, detail):
        if transcript["state"] == "ok" and source["platform"] == "bilibili" and (
                not isinstance(detail, dict) or len(detail.get("pages") or []) > 1):
            transcript.update(truncated=True, reason="本地转写仅覆盖主分集，其余分集未转写或未能复核")
        return transcript

    async def audio_resource(self, client, source, detail):
        """Only known detail fields / the existing Bilibili client; no new crawler."""
        platform = source["platform"]
        video = detail.get("video") or {}
        rows = []
        if platform == "bilibili" and detail.get("aid") and detail.get("cid"):
            response = await self.bilibili_player(client, "/x/player/wbi/playurl", {
                "avid": detail["aid"], "cid": detail["cid"], "fnval": 16, "fnver": 0, "fourk": 0})
            rows = (response.get("dash") or {}).get("audio") or []
            rows = sorted((row for row in rows if isinstance(row, dict)), key=lambda row: row.get("bandwidth") or 0)
        elif platform == "xhs" and isinstance(video, dict):
            streams = ((video.get("media") or {}).get("stream") or {})
            rows = streams.get("audio") or streams.get("h264") or []
            rows = sorted((row for row in rows if isinstance(row, dict)), key=lambda row: row.get("avg_bitrate") or 0)
        elif platform == "douyin" and isinstance(video, dict):
            # music.play_url is background music, not the speech track of the post.
            variants = [row for row in video.get("bit_rate") or [] if isinstance(row, dict)
                        and isinstance(row.get("play_addr"), dict) and row.get("format") in {None, "mp4"}]
            variants.sort(key=lambda row: (bool(row.get("is_bytevc1")),
                row.get("bit_rate") if isinstance(row.get("bit_rate"), (int, float)) else math.inf))
            rows = [video.get("audio") or {}] + [row["play_addr"] for row in variants] + [video.get("play_addr") or {}]
        for row in rows:
            url = row.get("baseUrl") or row.get("base_url") or row.get("master_url") or next(iter(row.get("url_list") or []), None)
            if isinstance(url, str) and url:
                return "https:" + url if url.startswith("//") else url
        return None

    async def bilibili_player(self, client, uri, params):
        try:
            return await self.request(client.get, uri, params)
        except Exception as error:
            if classify(error)[0] not in {"rate_limited", "restricted"}:
                raise
            return await self.browser_fallback("bilibili", "subtitles", "playurl" if uri.endswith("playurl") else "native_subtitle",
                self.browser_provider.bilibili_player, client, uri, params)

    @staticmethod
    def check_douyin(response):
        if not isinstance(response, dict) or response.get("status_code") != 0:
            code = response.get("status_code") if isinstance(response, dict) else None
            raise AcquisitionError("rate_limited" if code in {21111, 21004, -20} else "malformed_response")

    async def comments(self, client, source, detail):
        platform, identity = source["platform"], source["content_id"]
        entries, seen = [], set()
        cursor, has_more, replies_incomplete = "", True, False
        reply_access_restricted = False
        sort = "热门" if platform in {"bilibili", "zhihu"} else "平台默认"
        for _ in range(5):
            try:
                if platform == "xhs":
                    token = (parse_qs(urlsplit(source["url"]).query).get("xsec_token") or [""])[0]
                    response = await self.request(self.browser_provider.xhs_comments, client, {
                        "note_id": identity, "cursor": cursor, "top_comment_id": "", "image_formats": "jpg,webp,avif", "xsec_token": token})
                    rows, next_cursor, has_more = response.get("comments"), response.get("cursor", ""), bool(response.get("has_more"))
                elif platform == "douyin":
                    response = await self.request(client.get, "/aweme/v1/web/comment/list/", {
                        "aweme_id": identity, "cursor": int(cursor or 0), "count": 20, "item_type": 0})
                    self.check_douyin(response)
                    rows, next_cursor, has_more = response.get("comments"), response.get("cursor", 0), bool(response.get("has_more"))
                elif platform == "bilibili":
                    if not isinstance(detail, dict) or not detail.get("aid"):
                        raise ValueError("missing video identity")
                    response = await self.request(client.get, "/x/v2/reply/main", {
                        "type": 1, "oid": detail["aid"], "mode": 3, "next": int(cursor or 0), "ps": 20}, enable_params_sign=False)
                    rows = response.get("replies")
                    paging = response.get("cursor") or {}
                    next_cursor, has_more = paging.get("next", 0), not paging.get("is_end", True)
                    if rows is None and not has_more:
                        rows = []
                else:
                    kind = source.get("content_type") or "answer"
                    uri = f"/api/v4/comment_v5/{kind}s/{identity}/root_comment"
                    params = {"order": "score", "offset": cursor, "limit": 20}
                    if platform in self.browser_provider.pages:
                        response = await self.browser_fallback(platform, "comments", "comment_api",
                            self.browser_provider.zhihu_comments, client, uri, params)
                    else:
                        try:
                            response = await self.request(client.get, uri, params)
                        except Exception as error:
                            if classify(error)[0] != "restricted":
                                raise
                            response = await self.browser_fallback(platform, "comments", "comment_api", self.browser_provider.zhihu_comments, client, uri, params)
                    rows = response.get("data")
                    paging = response.get("paging") or {}
                    next_cursor = (parse_qs(urlsplit(paging.get("next", "")).query).get("offset") or [""])[0]
                    has_more = not paging.get("is_end", True)
                if not isinstance(rows, list):
                    raise ValueError("invalid comments response")
                # Fetch a small reply page when the root response contains only a count.
                for row in rows[:5]:
                    if not isinstance(row, dict):
                        continue
                    root = str(row.get("id") or row.get("cid") or row.get("rpid") or "")
                    children = row.get("sub_comments") or row.get("reply_comment") or row.get("replies") or row.get("child_comments")
                    count = row.get("sub_comment_count") or row.get("reply_comment_total") or row.get("rcount") or row.get("child_comment_count") or 0
                    if not children and count and root and not reply_access_restricted:
                        try:
                            if platform == "xhs":
                                reply = await self.request(self.browser_provider.xhs_replies, client, {"note_id": identity,
                                    "root_comment_id": root, "xsec_token": token, "num": "3", "cursor": "", "image_formats": "jpg,webp,avif"})
                                row["sub_comments"] = reply.get("comments", [])
                            elif platform == "douyin":
                                reply = await self.request(client.get, "/aweme/v1/web/comment/list/reply/", {"comment_id": root,
                                    "cursor": 0, "count": 3, "item_type": 0, "item_id": identity})
                                self.check_douyin(reply)
                                row["reply_comment"] = reply.get("comments", [])
                            elif platform == "bilibili":
                                reply = await self.request(client.get, "/x/v2/reply/reply", {"type": 1, "oid": detail["aid"],
                                    "root": root, "pn": 1, "ps": 3}, enable_params_sign=False)
                                row["replies"] = reply.get("replies", [])
                            else:
                                method = self.browser_provider.zhihu_comments if platform in self.browser_provider.pages else client.get
                                args = (client,) if platform in self.browser_provider.pages else ()
                                reply = await self.request(method, *args, f"/api/v4/comment_v5/comment/{root}/child_comment", {"offset": "", "limit": 3, "order": "sort"})
                                row["child_comments"] = reply.get("data", [])
                        except Exception as error:
                            replies_incomplete = True
                            reply_access_restricted = classify(error)[0] in {"rate_limited", "restricted", "session_expired"}
                page = normalize_comments(rows, platform)
                for row in page:
                    if row["id"] not in seen and len(entries) < 50:
                        seen.add(row["id"])
                        entries.append(row)
                replies_incomplete |= any(int(row.get("sub_comment_count") or row.get("reply_comment_total") or row.get("rcount") or row.get("child_comment_count") or 0) > 3 for row in rows if isinstance(row, dict))
                if len(entries) >= 50 or not rows or not has_more or str(next_cursor) == str(cursor):
                    break
                cursor = next_cursor
            except Exception as error:
                if not entries:
                    raise
                result = failure(error, platform, "comments", "comment_api")
                result.update(entries=entries, sort=sort, truncated=True)
                self.browser_provider.comment_diagnostic(result, platform)
                return result
        result = component("ok", entries=entries)
        result.update(sort=sort, truncated=has_more or replies_incomplete or len(entries) >= 50)
        result["diagnostics"] = diagnostic(platform, "comments", "comment_api")
        self.browser_provider.comment_diagnostic(result, platform)
        return result

    async def subtitles(self, client, source, detail):
        if source["platform"] != "bilibili":
            candidates = []
            if isinstance(detail, dict):
                video = detail.get("video") or {}
                candidates = video.get("caption_list") or video.get("subtitle_list") or detail.get("subtitles") or []
            for row in candidates if isinstance(candidates, list) else []:
                url = row.get("url") or row.get("subtitle_url") if isinstance(row, dict) else None
                if isinstance(url, str):
                    data = await self.request(public_get, url if not url.startswith("//") else "https:" + url, json_response=True)
                    entries = subtitle_entries(data)
                    if entries:
                        return component("ok", entries=entries)
            return component("missing", reason="原帖未提供可读取的平台字幕")
        if not isinstance(detail, dict) or not detail.get("cid"):
            return component("failed", reason="视频详情缺少字幕所需标识")
        entries, incomplete = [], False
        pages = detail.get("pages") or [{"cid": detail["cid"], "page": 1}]
        for part in pages[:20]:
            try:
                response = await self.bilibili_player(client, "/x/player/wbi/v2", {"aid": detail["aid"], "cid": part["cid"]})
                tracks = (response.get("subtitle") or {}).get("subtitles") or []
                if not tracks:
                    incomplete = True
                    continue
                track = next((row for row in tracks if str(row.get("lan", "")).startswith("zh")), tracks[0])
                url = track.get("subtitle_url")
                if not url:
                    incomplete = True
                    continue
                data = await self.request(public_get, "https:" + url if url.startswith("//") else url, json_response=True)
                entries.extend({**row, "part": part.get("page", 1)} for row in subtitle_entries(data))
            except Exception as error:
                if not entries:
                    raise
                result = failure(error)
                result.update(entries=entries, truncated=True)
                return result
        result = component("ok" if entries else "missing", entries=entries, reason="部分分集没有可读取字幕" if incomplete else "")
        result["truncated"] = incomplete or len(pages) > 20
        return result

    async def close(self):
        try:
            await super().close()
        finally:
            await self.browser_sessions.aclose()
