# -*- coding: utf-8 -*-
"""Best-effort, post-search description hydration.

This module deliberately uses existing lightweight detail clients. It never
starts a browser and it is independent from the resident search workers.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import time
from typing import Dict, Optional
from urllib.parse import parse_qs, urlsplit

from aggregate_search.hydration import hydrate_results, metric_candidates
from aggregate_search.adapters.xhs import XhsAdapter
from aggregate_search.models import UnifiedSearchResult, clean_snippet
from .accounts import ensure_session_snapshot, get_session_snapshot

logger = logging.getLogger(__name__)


def _configure_hydration_debug_logging() -> None:
    """Install an opt-in handler because uvicorn may filter module loggers.

    The handler is intentionally scoped to this diagnostic module and writes
    only the already-sanitized messages emitted below. Normal startup keeps
    the existing logging configuration unchanged.
    """
    if os.environ.get("MC_HYDRATION_DEBUG") != "1":
        return

    logger.setLevel(logging.DEBUG)
    for handler in logger.handlers:
        if getattr(handler, "_mc_hydration_debug", False):
            return

    handler = logging.StreamHandler()
    handler._mc_hydration_debug = True  # type: ignore[attr-defined]
    handler.setLevel(logging.DEBUG)
    handler.setFormatter(logging.Formatter(
        "%(asctime)s %(name)s %(levelname)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    ))
    logger.addHandler(handler)
    # Prevent uvicorn/root logger filtering or duplicate propagation from
    # hiding the diagnostic line or printing it twice.
    logger.propagate = False
    logger.debug("[XHS hydration] diagnostic logger enabled")


_configure_hydration_debug_logging()


def _bool_text(value: bool) -> str:
    return "true" if value else "false"


def _safe_source(value: str) -> str:
    source = value if isinstance(value, str) else ""
    if re.fullmatch(r"[A-Za-z0-9_-]{1,40}", source):
        return source
    return "[redacted]"


def _safe_exception_message(exc: BaseException) -> str:
    """Keep useful exception context while removing URL/body credentials."""
    message = str(exc)
    message = re.sub(r"https?://[^\s]+", "[URL]", message)
    message = re.sub(
        r"(?i)(xsec[_-]?token|cookie|authorization|access[_-]?token|refresh[_-]?token)"
        r"\s*[:=]\s*[^\s,;}]+'?",
        r"\1=[REDACTED]",
        message,
    )
    return message[:160] or "[empty]"


def _safe_business_msg(value: object) -> str:
    if not isinstance(value, str) or not value:
        return "[none]"
    return _safe_exception_message(Exception(value))


def extract_xhs_snippet(detail: object) -> Optional[str]:
    """Extract description from the known XHS detail response shapes.

    ``get_note_by_id`` normally unwraps ``data.items[0].note_card`` to the
    note-card dict, while fixtures and compatible clients may return one of
    those outer shapes directly. Values are cleaned only after a candidate
    field is found.
    """
    if not isinstance(detail, dict):
        return None
    candidates = [detail.get("desc"), detail.get("description")]
    note_card = detail.get("note_card")
    if isinstance(note_card, dict):
        candidates.extend([note_card.get("desc"), note_card.get("description")])
    note = detail.get("note")
    if isinstance(note, dict):
        candidates.extend([note.get("desc"), note.get("description")])
    data = detail.get("data")
    if isinstance(data, dict):
        candidates.extend([data.get("desc"), data.get("description")])
        items = data.get("items")
        if isinstance(items, list) and items:
            first = items[0]
            if isinstance(first, dict):
                nested_card = first.get("note_card")
                if isinstance(nested_card, dict):
                    candidates.extend([
                        nested_card.get("desc"),
                        nested_card.get("description"),
                    ])
    for candidate in candidates:
        snippet = clean_snippet(candidate)
        if snippet:
            return snippet
    return None


class ResultHydrator:
    """Reuse at most one HTTP client per supported platform for one job."""

    def __init__(self) -> None:
        self._clients: Dict[str, object] = {}
        self._metric_details: Dict[tuple, dict] = {}
        self._metric_stopped: set[str] = set()
        self.metric_errors: Dict[str, dict] = {}

    async def hydrate_metrics(self, results, cancel_event, on_update):
        from aggregate_search.favorite_metrics import enrich_favorites

        candidates = metric_candidates(results)

        async def platform_batch(platform):
            selected = [r for r in candidates if r.platform == platform]
            if not selected or cancel_event.is_set():
                return
            unsupported = [r for r in selected if platform == "bilibili"
                           and urlsplit(r.url).path.startswith(("/cheese/", "/bangumi/"))]
            for result in unsupported:
                result.metrics_status = "unavailable"
                result.metrics_updated_at = time.time()
                on_update(result)
            selected = [r for r in selected if r not in unsupported]
            if not selected:
                return
            for result in selected:
                result.metrics_status = "pending"
                on_update(result)
            try:
                if platform == "bilibili":
                    client = await self._get_bilibili()
                else:
                    snapshot = get_session_snapshot("zhihu") or await ensure_session_snapshot("zhihu")
                    if not snapshot or not snapshot.get("d_c0"):
                        raise RuntimeError("missing_session")
                    client = await self._get_zhihu(snapshot)
                by_key = {}
                rows = []
                for result in selected:
                    row = {"id": result.content_id, "type": result.content_type}
                    if platform == "bilibili" and result.content_id.upper().startswith("BV"):
                        row["bvid"] = result.content_id
                    rows.append(row)
                    by_key[(result.content_type, result.content_id)] = result

                # Keep successful raw details in memory to reuse their descriptions.
                details = self._metric_details

                class DetailClient:
                    async def get_video_info(self, **kwargs):
                        value = await client.get_video_info(**kwargs)
                        if isinstance(value, dict):
                            details[(platform, "video", str(kwargs.get("bvid") or kwargs.get("aid")))] = value.get("View", value)
                        return value

                    async def get(self, uri, params):
                        value = await client.get(uri, params)
                        if isinstance(value, dict):
                            kind = uri.split("/")[-2][:-1]
                            details[(platform, kind, uri.split("/")[-1])] = value
                        return value

                def receive(batch):
                    if cancel_event.is_set():
                        raise asyncio.CancelledError()
                    for row in batch:
                        result = by_key[(row["type"], row["id"])]
                        counters = row.get("_favorite_metrics", {})
                        if row.get("_metrics_cached"):
                            counters = {k: v for k, v in counters.items() if k not in result.metrics}
                        result.metrics.update(counters)
                        result.metrics_status = row["_metrics_status"]
                        result.metrics_updated_at = row.get("_metrics_updated_at")
                        result.metrics_approximate = sorted(
                            (set(result.metrics_approximate) - counters.keys()) |
                            {k for k in row.get("_metrics_approximate", []) if k in counters})
                        on_update(result)

                await enrich_favorites(platform, DetailClient(), rows, receive)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._metric_stopped.add(platform)
                self.metric_errors[platform] = {"type": type(exc).__name__,
                    "http_status": getattr(exc, "http_status", None),
                    "platform_code": getattr(exc, "platform_code", None)}
                logger.warning("search metric hydration stopped for %s: %s", platform, type(exc).__name__)
            finally:
                for result in selected:
                    if result.metrics_status == "pending":
                        result.metrics_status = "failed"
                        if not cancel_event.is_set():
                            on_update(result)

        await asyncio.gather(*(platform_batch(p) for p in ("bilibili", "zhihu")))

    async def hydrate(self, results, cancel_event: asyncio.Event):
        try:
            return await hydrate_results(
                results,
                self.fetch_snippet,
                cancel_event=cancel_event,
            )
        finally:
            await self.close()

    async def close(self) -> None:
        clients = list(self._clients.values())
        self._clients.clear()
        for client in clients:
            close = getattr(client, "aclose", None) or getattr(client, "close", None)
            if close is not None:
                try:
                    await close()
                except Exception:
                    pass

    async def fetch_snippet(self, result: UnifiedSearchResult) -> Optional[str]:
        detail = self._metric_details.get((result.platform, result.content_type, result.content_id))
        if detail is not None:
            return clean_snippet(detail.get("desc") or detail.get("description") or detail.get("excerpt") or detail.get("content"))
        if result.platform in self._metric_stopped:
            return None
        if result.platform == "bilibili":
            return await self._fetch_bilibili(result)
        if result.platform == "xhs":
            return await self._fetch_xhs(result)
        if result.platform == "zhihu":
            return await self._fetch_zhihu(result)
        # Douyin's detail endpoint requires the browser signing/page path;
        # do not launch a browser from the post-search task in V1.
        return None

    async def _fetch_bilibili(self, result: UnifiedSearchResult) -> Optional[str]:
        client = await self._get_bilibili()
        value = result.content_id
        detail = await client.get_video_info(
            bvid=value if value.upper().startswith("BV") else None,
            aid=int(value) if value.isdigit() else None,
        )
        if not isinstance(detail, dict):
            return None
        detail = detail.get("View", detail)
        return clean_snippet(detail.get("desc") or detail.get("description"))

    async def _fetch_xhs(self, result: UnifiedSearchResult) -> Optional[str]:
        query = parse_qs(urlsplit(result.url).query)
        token = (query.get("xsec_token") or [""])[0]
        source = (query.get("xsec_source") or ["pc_search"])[0]
        raw_snapshot = get_session_snapshot("xhs")
        has_snapshot = raw_snapshot is not None
        cookie_present = bool(raw_snapshot)
        source_value = _safe_source(source)
        diagnostic_prefix = (
            "[XHS hydration] note_id=%s has_xsec_token=%s xsec_source=%s "
            "has_session_snapshot=%s snapshot_cookie_present=%s"
        )
        if not token:
            logger.debug(
                diagnostic_prefix + " client_created=false request_started=false "
                "request_status=skipped exception_type=missing_xsec_token",
                result.content_id, _bool_text(False), source_value,
                _bool_text(has_snapshot), _bool_text(cookie_present),
            )
            return None
        # Browser search can succeed without an API snapshot.  Recover the
        # existing persistent profile once before constructing the HTTP client;
        # an empty snapshot cannot satisfy XHS signing because it lacks a1.
        snapshot = raw_snapshot
        if snapshot is None:
            snapshot = await ensure_session_snapshot("xhs")
        has_snapshot = snapshot is not None
        cookie_present = bool(snapshot)
        if snapshot is None:
            logger.debug(
                diagnostic_prefix + " client_created=false request_started=false "
                "request_status=snapshot_restore_failed exception_type=none",
                result.content_id, _bool_text(False), source_value,
                _bool_text(False), _bool_text(False),
            )
            return None
        try:
            client = await self._get_xhs(snapshot)
        except Exception as exc:
            logger.debug(
                diagnostic_prefix + " client_created=false request_started=false "
                "request_status=client_create_exception exception_type=%s "
                "exception_message=%s",
                result.content_id, _bool_text(True), source_value,
                _bool_text(has_snapshot), _bool_text(cookie_present),
                type(exc).__name__, _safe_exception_message(exc),
            )
            raise
        logger.debug(
            diagnostic_prefix + " client_created=%s request_started=false",
            result.content_id, _bool_text(True), source_value,
            _bool_text(has_snapshot), _bool_text(cookie_present),
            _bool_text(client is not None),
        )
        try:
            logger.debug(
                diagnostic_prefix + " client_created=true request_started=true",
                result.content_id, _bool_text(True), source_value,
                _bool_text(has_snapshot), _bool_text(cookie_present),
            )
            detail = await client.get_note_by_id(result.content_id, source, token)
        except Exception as exc:
            logger.debug(
                diagnostic_prefix + " client_created=true request_started=true "
                "request_status=exception exception_type=%s exception_message=%s "
                "http_status=%s business_code=%s business_msg=%s",
                result.content_id, _bool_text(True), source_value,
                _bool_text(has_snapshot), _bool_text(cookie_present),
                type(exc).__name__, _safe_exception_message(exc),
                getattr(client, "last_response_status", None),
                getattr(client, "last_business_code", None),
                _safe_business_msg(getattr(client, "last_business_msg", None)),
            )
            raise
        if isinstance(detail, dict):
            response_keys = sorted(str(key) for key in detail.keys())[:20]
        else:
            response_keys = [f"<payload:{type(detail).__name__}>"]
        snippet = extract_xhs_snippet(detail)
        if result.content_type == "video" and result.duration_seconds is None and isinstance(detail, dict):
            candidates = [detail, detail.get("note_card"), detail.get("note")]
            data = detail.get("data")
            if isinstance(data, dict):
                candidates.append(data)
                items = data.get("items")
                if isinstance(items, list) and items and isinstance(items[0], dict):
                    candidates.append(items[0].get("note_card"))
            adapter = XhsAdapter()
            for candidate in candidates:
                if isinstance(candidate, dict):
                    duration = adapter._extract_duration(candidate)
                    if duration is not None:
                        result.duration_seconds = duration
                        break
        logger.debug(
            diagnostic_prefix + " client_created=true request_started=true "
            "request_status=success http_status=%s business_code=%s "
            "business_msg=%s response_top_level_keys=%s "
            "extracted_snippet_length=%s",
            result.content_id, _bool_text(True), source_value,
            _bool_text(has_snapshot), _bool_text(cookie_present),
            getattr(client, "last_response_status", None),
            getattr(client, "last_business_code", None),
            _safe_business_msg(getattr(client, "last_business_msg", None)),
            response_keys, len(snippet or ""),
        )
        return snippet

    async def _fetch_zhihu(self, result: UnifiedSearchResult) -> Optional[str]:
        snapshot = get_session_snapshot("zhihu")
        if not snapshot or not snapshot.get("d_c0"):
            return None
        client = await self._get_zhihu(snapshot)
        if result.content_type == "answer":
            parts = [part for part in urlsplit(result.url).path.split("/") if part]
            if len(parts) < 4 or parts[-2] != "answer":
                return None
            detail = await client.get_answer_info(parts[-3], parts[-1])
        elif result.content_type == "article":
            detail = await client.get_article_info(result.content_id)
        elif result.content_type == "zvideo":
            detail = await client.get_video_info(result.content_id)
        else:
            return None
        if detail is None:
            return None
        return clean_snippet(detail.desc or detail.content_text)

    async def _get_bilibili(self):
        if "bilibili" not in self._clients:
            from media_platform.bilibili.client import BilibiliClient

            snapshot = get_session_snapshot("bilibili") or {}
            cookie = "; ".join(f"{key}={value}" for key, value in snapshot.items())
            self._clients["bilibili"] = BilibiliClient(
                timeout=8,
                proxy=None,
                headers={
                    "User-Agent": "Mozilla/5.0",
                    "Cookie": cookie,
                    "Origin": "https://www.bilibili.com",
                    "Referer": "https://www.bilibili.com",
                    "Content-Type": "application/json;charset=UTF-8",
                },
                playwright_page=None,
                cookie_dict=snapshot,
                reuse_http_client=True,
            )
        return self._clients["bilibili"]

    async def _get_xhs(self, snapshot):
        if "xhs" not in self._clients:
            from media_platform.xhs.client import XiaoHongShuClient

            cookie = "; ".join(f"{key}={value}" for key, value in snapshot.items())
            self._clients["xhs"] = XiaoHongShuClient(
                timeout=8,
                proxy=None,
                headers={
                    "accept": "application/json, text/plain, */*",
                    "accept-language": "zh-CN,zh;q=0.9",
                    "content-type": "application/json;charset=UTF-8",
                    "origin": "https://www.xiaohongshu.com",
                    "referer": "https://www.xiaohongshu.com/",
                    "user-agent": "Mozilla/5.0",
                    "Cookie": cookie,
                },
                playwright_page=None,
                cookie_dict=dict(snapshot),
                reuse_http_client=True,
            )
        return self._clients["xhs"]

    async def _get_zhihu(self, snapshot):
        if "zhihu" not in self._clients:
            from media_platform.zhihu.client import ZhiHuClient

            cookie = "; ".join(f"{key}={value}" for key, value in snapshot.items())
            self._clients["zhihu"] = ZhiHuClient(
                timeout=8,
                proxy=None,
                headers={
                    "accept": "*/*",
                    "accept-language": "zh-CN,zh;q=0.9",
                    "cookie": cookie,
                    "referer": "https://www.zhihu.com/",
                    "user-agent": "Mozilla/5.0",
                    "x-api-version": "3.0.91",
                    "x-app-za": "OS=Web",
                    "x-requested-with": "fetch",
                    "x-zse-93": "101_3_3.0",
                },
                playwright_page=None,
                cookie_dict=dict(snapshot),
                reuse_http_client=True,
            )
        return self._clients["zhihu"]
