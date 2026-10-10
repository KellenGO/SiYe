# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
# This file is part of MediaCrawler project.
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

"""Local, read-only content detail requests."""

import asyncio
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from ..services.reading import ReadingError, reading_service
from ..services.reading_media import reading_media


async def local_reading_request(request: Request):
    origin = request.headers.get("origin")
    dev_origins = {"http://localhost:5173", "http://127.0.0.1:5173"}
    if request.url.hostname not in {"localhost", "127.0.0.1", "::1"} or (
        origin and origin not in {str(request.base_url).rstrip("/"), *dev_origins}
    ) or request.headers.get("sec-fetch-site") == "cross-site" and origin not in dev_origins:
        raise HTTPException(403, "阅读接口仅允许四野本机页面访问")


reading_router = APIRouter(prefix="/api/reading", tags=["reading"], dependencies=[Depends(local_reading_request)])


class ReadingInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    platform: Literal["zhihu", "xhs", "douyin", "bilibili"]
    content_type: Literal["answer", "article", "note", "video"]
    content_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9]+$")
    url: str = Field(min_length=1, max_length=2000)
    refresh: bool = False


def get_reading_service():
    return reading_service


@reading_router.post("/detail")
async def read_detail(payload: ReadingInput, request: Request, service=Depends(get_reading_service)):
    task = asyncio.create_task(service.read(payload.content_type, payload.content_id, payload.url, payload.refresh, platform=payload.platform))
    try:
        while not task.done():
            await asyncio.wait({task}, timeout=0.25)
            if not task.done() and await request.is_disconnected():
                raise HTTPException(499, "阅读请求已关闭")
        return await task
    except ReadingError as error:
        raise HTTPException(409 if error.code == "busy" else 503, {"code": error.code, "message": str(error)}) from None
    except ValueError:
        raise HTTPException(422, "内容类型、编号或原文链接不匹配") from None
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


@reading_router.post("/comments")
async def read_comments(payload: ReadingInput, request: Request, service=Depends(get_reading_service)):
    task = asyncio.create_task(service.comments(payload.content_type, payload.content_id, payload.url, platform=payload.platform))
    try:
        while not task.done():
            await asyncio.wait({task}, timeout=0.25)
            if not task.done() and await request.is_disconnected():
                raise HTTPException(499, "评论请求已关闭")
        return await task
    except ReadingError as error:
        raise HTTPException(409 if error.code == "busy" else 503, {"code": error.code, "message": str(error).replace("正文", "评论")}) from None
    except ValueError:
        raise HTTPException(422, "内容类型、编号或原文链接不匹配") from None
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


@reading_router.api_route("/media/{token}", methods=["GET", "HEAD"])
async def read_media(token: str, request: Request):
    return await reading_media.stream(token, request.headers.get("range"), request.method)
