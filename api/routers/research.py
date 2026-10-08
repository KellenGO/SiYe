# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
# This file is part of MediaCrawler project.
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

"""Local configuration and explicit research task lifecycle."""

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from ..services.research_config import research_config
from ..services.research_jobs import research_jobs
from ..services.spaces_store import SpacesStore, SpaceMissing, get_spaces_store

research_router = APIRouter(prefix="/api/research", tags=["research"])


def get_research_jobs():
    return research_jobs


def get_research_config():
    return research_config


async def local_request(request: Request):
    # Credential APIs and paid actions must not be callable by arbitrary websites.
    if request.url.hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise HTTPException(403, "研究接口仅允许四野本机页面访问")
    origin = request.headers.get("origin")
    if origin and origin not in {str(request.base_url).rstrip("/"), "http://localhost:5173", "http://127.0.0.1:5173"}:
        raise HTTPException(403, "研究接口仅允许四野本机页面访问")
    if request.headers.get("sec-fetch-site") == "cross-site" and origin not in {"http://localhost:5173", "http://127.0.0.1:5173"}:
        raise HTTPException(403, "研究接口仅允许四野本机页面访问")


research_router.dependencies.append(Depends(local_request))


class ConfigInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    protocol: Literal["openai", "anthropic"] = "openai"
    base_url: str = Field(max_length=500)
    model: str = Field(min_length=1, max_length=200)
    api_key: str | None = Field(default=None, max_length=4096)


class WebInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    web_enabled: bool = False


class TaskInput(WebInput):
    space_id: int
    question: str = Field(default="", max_length=2000)
    conversation_id: str | None = Field(default=None, max_length=64)


def translate(error):
    raise HTTPException(404 if isinstance(error, SpaceMissing) else 400, str(error)) from None


@research_router.get("/config")
def config_get(config=Depends(get_research_config)):
    try:
        return config.public()
    except ValueError as error:
        translate(error)


@research_router.put("/config")
async def config_save(payload: ConfigInput, config=Depends(get_research_config), manager=Depends(get_research_jobs)):
    if manager.active:
        raise HTTPException(409, "研究正在运行或等待确认，请先完成或取消后修改 AI 配置")
    try:
        return config.save(payload.base_url, payload.model, payload.api_key, payload.protocol)
    except ValueError as error:
        translate(error)


@research_router.delete("/config")
async def config_delete(config=Depends(get_research_config), manager=Depends(get_research_jobs)):
    if manager.active:
        raise HTTPException(409, "研究正在运行或等待确认，请先完成或取消后修改 AI 配置")
    return config.delete()


@research_router.post("/connection-test")
async def connection_test(payload: WebInput, manager=Depends(get_research_jobs)):
    try:
        return await manager.probe(payload.web_enabled)
    except ValueError as error:
        translate(error)


@research_router.get("/spaces/{space_id}/preference")
def preference_get(space_id: int, store: SpacesStore = Depends(get_spaces_store), config=Depends(get_research_config)):
    try:
        store.get_space(space_id)
        return {"web_enabled": config.preference(space_id)}
    except ValueError as error:
        translate(error)


@research_router.put("/spaces/{space_id}/preference")
def preference_save(space_id: int, payload: WebInput, store: SpacesStore = Depends(get_spaces_store), config=Depends(get_research_config)):
    try:
        store.get_space(space_id)
        return config.set_preference(space_id, payload.web_enabled)
    except ValueError as error:
        translate(error)


@research_router.post("/jobs", status_code=201)
async def job_create(payload: TaskInput, store: SpacesStore = Depends(get_spaces_store), manager=Depends(get_research_jobs)):
    try:
        return await manager.create(store.get_space(payload.space_id), payload.question, payload.web_enabled, payload.conversation_id)
    except ValueError as error:
        translate(error)


@research_router.get("/spaces/{space_id}/latest")
def latest(space_id: int, store: SpacesStore = Depends(get_spaces_store), manager=Depends(get_research_jobs)):
    try:
        store.get_space(space_id)
        return manager.latest(space_id, store)
    except ValueError as error:
        translate(error)


@research_router.get("/spaces/{space_id}/conversations")
def conversations(space_id: int, store: SpacesStore = Depends(get_spaces_store), manager=Depends(get_research_jobs)):
    try:
        store.get_space(space_id)
        return manager.conversations(space_id)
    except ValueError as error:
        translate(error)


@research_router.get("/spaces/{space_id}/conversations/{conversation_id}")
def conversation(space_id: int, conversation_id: str, store: SpacesStore = Depends(get_spaces_store), manager=Depends(get_research_jobs)):
    try:
        store.get_space(space_id)
        return [manager.public(job["job_id"], store, job=job) for job in manager.conversation_jobs(space_id, conversation_id)]
    except ValueError as error:
        translate(error)


@research_router.get("/jobs/{job_id}")
def job_get(job_id: str, store: SpacesStore = Depends(get_spaces_store), manager=Depends(get_research_jobs)):
    try:
        return manager.public(job_id, store)
    except ValueError as error:
        translate(error)


@research_router.post("/jobs/{job_id}/{action}")
async def job_action(job_id: str, action: str, store: SpacesStore = Depends(get_spaces_store), manager=Depends(get_research_jobs)):
    try:
        job = manager.get(job_id)
        space = store.get_space(job["space_id"])
        if action == "cancel":
            return await manager.cancel(job_id)
        if space["archived"]:
            raise ValueError("空间已归档，请先继续研究")
        if action == "retry":
            return await manager.retry(job_id)
        if action == "generate":
            return await manager.generate(job_id)
        raise HTTPException(404, "研究操作不存在")
    except ValueError as error:
        translate(error)
