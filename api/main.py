# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler/blob/main/api/main.py
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#
# 声明：本代码仅供学习和研究目的使用。使用者应遵守以下原则：
# 1. 不得用于任何商业用途。
# 2. 使用时应遵守目标平台的使用条款和robots.txt规则。
# 3. 不得进行大规模爬取或对平台造成运营干扰。
# 4. 应合理控制请求频率，避免给目标平台带来不必要的负担。
# 5. 不得用于任何非法或不当的用途。
#
# 详细许可条款请参阅项目根目录下的LICENSE文件。
# 使用本代码即表示您同意遵守上述原则和LICENSE中的所有条款。

"""
SiYe WebUI API Server
Start command: uvicorn api.main:app --port 8080 --reload
Or: python -m api.main
"""
import uvicorn
from pathlib import Path
from fastapi import FastAPI
from fastapi import HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from base.app_version import APP_VERSION
from base.runtime_paths import resource_path

from .routers.search import search_router
from .routers.library import library_router
from .schemas.search import HealthResponse
from .services.environment_health import build_health_response
from .services.search_job_manager import search_job_manager
from .services.favorites_job_manager import favorites_job_manager

app = FastAPI(
    title="SiYe WebUI API",
    description="API for controlling SiYe from WebUI",
    version=APP_VERSION
)


@app.on_event("startup")
async def _prepare_library_storage():
    """Merge legacy per-folder databases before the API starts serving data."""
    import asyncio
    from .services.library_migration import migrate_legacy_libraries

    await asyncio.to_thread(migrate_legacy_libraries)

# Production frontend build directory. It is intentionally kept outside the
# API package so the static server cannot expose arbitrary repository files.
WEBUI_DIR = resource_path("webui", "dist")


@app.on_event("shutdown")
async def _shutdown_cleanup():
    """Stop aggregate search and login workers on shutdown."""
    from .routers.search import _cleanup_login_on_shutdown
    from .services.accounts import cancel_verify_tasks

    await search_job_manager.cleanup()
    await favorites_job_manager.cleanup()
    await _cleanup_login_on_shutdown()
    await cancel_verify_tasks()

# CORS configuration - allow frontend dev server access
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",  # Vite dev server
        "http://localhost:3000",  # Backup port
        "http://127.0.0.1:5173",
        "http://127.0.0.1:3000",
    ],
    allow_origin_regex=r"chrome-extension://.*",  # browser extension SW → local API
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register routers
app.include_router(search_router)  # search router includes its own /api/search prefix
app.include_router(library_router)  # local bookmark library, /api/library prefix


@app.get("/api/health", response_model=HealthResponse)
async def health_check():
    return await build_health_response()


def _frontend_file(path: str) -> Path | None:
    """Resolve a requested build file without allowing path traversal."""
    candidate = (WEBUI_DIR / path).resolve()
    try:
        candidate.relative_to(WEBUI_DIR.resolve())
    except ValueError:
        return None
    return candidate if candidate.is_file() else None


def _serve_frontend(path: str = ""):
    """Serve build files and fall back to index.html for SPA routes only."""
    if path == "api" or path.startswith("api/"):
        raise HTTPException(status_code=404, detail="Not Found")
    if ".." in Path(path).parts:
        raise HTTPException(status_code=404, detail="Not Found")

    index_path = _frontend_file("index.html")
    requested_file = _frontend_file(path) if path else index_path
    if requested_file:
        return FileResponse(requested_file)

    # Missing assets must not be mistaken for an SPA route. Plain paths such
    # as /accounts remain eligible for the React entry point.
    if path in {"assets", "logos", "static"} or path.startswith(("assets/", "logos/", "static/")):
        raise HTTPException(status_code=404, detail="Static file not found")
    if Path(path).suffix:
        raise HTTPException(status_code=404, detail="Static file not found")
    if index_path:
        return FileResponse(index_path)
    if path:
        raise HTTPException(status_code=404, detail="WebUI build not found")
    return {
        "message": "SiYe WebUI API",
        "version": APP_VERSION,
        "docs": "/docs",
        "note": "WebUI not found, please build it first: cd webui && npm run build",
    }


@app.get("/")
async def serve_frontend():
    return _serve_frontend()


@app.get("/{path:path}", include_in_schema=False)
async def serve_frontend_path(path: str):
    return _serve_frontend(path)


if __name__ == "__main__":
    from base.server_port import resolve_port

    uvicorn.run(app, host="127.0.0.1", port=resolve_port())
