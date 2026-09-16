# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

"""FastAPI router for the local bookmark library (收藏库 / 收藏夹).

设计要点：
- 收藏内容存在本机稳定数据目录的 SQLite，
  不再依赖浏览器 localStorage，清缓存/换浏览器都不会丢。
- 一条内容只存一份，可以同时属于默认收藏夹、稍后再看和多个自建收藏夹。
- 删除收藏夹默认保留其中的内容（只解除归属）；取消收藏是独立接口，避免误删。
- 这些接口只做本地读写，不访问任何平台；路由定义为同步函数，由 FastAPI 放进线程池，
  避免 SQLite 的同步 I/O 阻塞事件循环。
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from ..schemas.library import (
    CollectionBatchInput,
    CollectionCreate,
    CollectionRename,
    LibraryImportInput,
    LibraryItemInput,
    LibraryItemsBatchInput,
    LibraryKeysInput,
    LibraryNotePatch,
)
from ..services.library_store import LibraryStore, get_library_store

library_router = APIRouter(prefix="/api/library", tags=["library"])


def _bad_request(error: ValueError) -> HTTPException:
    return HTTPException(status_code=400, detail=str(error))


# ── 概览 ────────────────────────────────────────────────────────────────


@library_router.get("/stats")
def library_stats(store: LibraryStore = Depends(get_library_store)) -> Dict[str, Any]:
    """收藏总量、内置收藏夹数量、自建收藏夹数量及数据库位置。"""
    return store.stats()


# ── 条目 ────────────────────────────────────────────────────────────────


@library_router.get("/items")
def list_items(
    collection_id: Optional[int] = Query(default=None),
    unclassified: bool = Query(default=False, description="只看未分类（不属于任何收藏夹）"),
    system_collection: Optional[str] = Query(default=None, pattern="^(default|watch_later)$"),
    platform: Optional[str] = Query(default=None),
    q: Optional[str] = Query(default=None, description="在标题/作者/摘要中搜索"),
    limit: Optional[int] = Query(default=None, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    store: LibraryStore = Depends(get_library_store),
) -> Dict[str, Any]:
    """列出收藏条目，可按收藏夹、未分类、平台、关键词过滤。"""
    return store.list_items(
        collection_id=collection_id,
        only_unclassified=unclassified,
        system_collection=system_collection,
        platform=platform,
        query=q,
        limit=limit,
        offset=offset,
    )


@library_router.post("/items", status_code=201)
def add_item(
    payload: LibraryItemInput,
    store: LibraryStore = Depends(get_library_store),
) -> Dict[str, Any]:
    """收藏一条内容（重复收藏会更新快照，保留首次时间与已有备注）。"""
    try:
        return store.add_item(
            payload.result,
            note=payload.note,
            fetched_at=payload.fetched_at,
            collection_ids=payload.collection_ids,
            in_default=payload.in_default,
            watch_later=payload.watch_later,
        )
    except ValueError as error:
        raise _bad_request(error)


@library_router.post("/items/batch")
def add_items(
    payload: LibraryItemsBatchInput,
    store: LibraryStore = Depends(get_library_store),
) -> Dict[str, Any]:
    """批量收藏（迁移、同步结果一键加入收藏夹都走这里）。"""
    entries = [
        {
            "result": entry.result,
            "note": entry.note,
            "fetched_at": entry.fetched_at,
            "collection_ids": entry.collection_ids or payload.collection_ids,
            "in_default": entry.in_default,
            "watch_later": entry.watch_later,
        }
        for entry in payload.entries
    ]
    stats = store.add_items(entries)
    return {**stats, "total_requested": len(entries)}


@library_router.patch("/items/{platform}/{content_id}")
def update_note(
    platform: str,
    content_id: str,
    payload: LibraryNotePatch,
    store: LibraryStore = Depends(get_library_store),
) -> Dict[str, Any]:
    """更新某条收藏的备注。"""
    if not store.set_note(platform, content_id, payload.note):
        raise HTTPException(status_code=404, detail="收藏不存在")
    item = store.get_item(platform, content_id)
    assert item is not None
    return item


@library_router.delete("/items")
def remove_items(
    payload: LibraryKeysInput,
    store: LibraryStore = Depends(get_library_store),
) -> Dict[str, Any]:
    """取消收藏（独立操作，与「移出收藏夹」区分）。"""
    removed = store.remove_items([(key.platform, key.content_id) for key in payload.keys])
    return {"removed": removed}


@library_router.put("/system-collections/{collection}/items")
def add_items_to_system_collection(
    collection: str,
    payload: LibraryItemsBatchInput,
    store: LibraryStore = Depends(get_library_store),
) -> Dict[str, Any]:
    """幂等加入默认收藏夹或稍后再看；不存在的内容同时落库。"""
    entries = [
        {
            "result": entry.result,
            "note": entry.note,
            "fetched_at": entry.fetched_at,
            "collection_ids": entry.collection_ids,
        }
        for entry in payload.entries
    ]
    try:
        return store.add_items_to_system_collection(entries, collection)
    except ValueError as error:
        raise _bad_request(error)


@library_router.delete("/system-collections/{collection}/items")
def remove_items_from_system_collection(
    collection: str,
    payload: LibraryKeysInput,
    store: LibraryStore = Depends(get_library_store),
) -> Dict[str, Any]:
    """只解除内置收藏夹归属；内容本体和其他归属保持不变。"""
    try:
        return store.remove_items_from_system_collection(
            [(key.platform, key.content_id) for key in payload.keys], collection
        )
    except ValueError as error:
        raise _bad_request(error)


# ── 收藏夹 ──────────────────────────────────────────────────────────────


@library_router.get("/collections")
def list_collections(store: LibraryStore = Depends(get_library_store)) -> Dict[str, Any]:
    """列出收藏夹及其条目数。"""
    collections = store.list_collections()
    return {"collections": collections, "total": len(collections)}


@library_router.post("/collections", status_code=201)
def create_collection(
    payload: CollectionCreate,
    store: LibraryStore = Depends(get_library_store),
) -> Dict[str, Any]:
    """新建收藏夹。"""
    try:
        return store.create_collection(payload.name)
    except ValueError as error:
        raise _bad_request(error)


@library_router.patch("/collections/{collection_id}")
def rename_collection(
    collection_id: int,
    payload: CollectionRename,
    store: LibraryStore = Depends(get_library_store),
) -> Dict[str, Any]:
    """重命名收藏夹。"""
    try:
        return store.rename_collection(collection_id, payload.name)
    except ValueError as error:
        raise _bad_request(error)


@library_router.delete("/collections/{collection_id}")
def delete_collection(
    collection_id: int,
    store: LibraryStore = Depends(get_library_store),
) -> Dict[str, Any]:
    """删除收藏夹，其中的内容默认保留（只解除归属）。"""
    try:
        return store.delete_collection(collection_id)
    except ValueError as error:
        raise _bad_request(error)


@library_router.post("/collections/{collection_id}/items")
def add_items_to_collection(
    collection_id: int,
    payload: CollectionBatchInput,
    store: LibraryStore = Depends(get_library_store),
) -> Dict[str, Any]:
    """批量把收藏加入某个收藏夹（一条内容可属于多个收藏夹）。"""
    try:
        return store.add_items_to_collection(
            [(key.platform, key.content_id) for key in payload.keys], collection_id
        )
    except ValueError as error:
        raise _bad_request(error)


@library_router.delete("/collections/{collection_id}/items")
def remove_items_from_collection(
    collection_id: int,
    payload: CollectionBatchInput,
    store: LibraryStore = Depends(get_library_store),
) -> Dict[str, Any]:
    """批量把收藏移出某个收藏夹（不取消收藏）。"""
    return store.remove_items_from_collection(
        [(key.platform, key.content_id) for key in payload.keys], collection_id
    )


# ── 导入 / 导出 ─────────────────────────────────────────────────────────


@library_router.get("/export")
def export_library(store: LibraryStore = Depends(get_library_store)) -> Dict[str, Any]:
    """导出可移植 JSON（等价于原来的「备份全部收藏」）。"""
    return store.export_payload()


@library_router.post("/import")
def import_library(
    payload: LibraryImportInput,
    store: LibraryStore = Depends(get_library_store),
) -> Dict[str, Any]:
    """导入备份或迁移旧浏览器收藏：只合并新增，不删除现有内容。"""
    try:
        return store.import_payload(payload.payload)
    except ValueError as error:
        raise _bad_request(error)
