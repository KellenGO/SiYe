"""Local management page APIs for the official optional AI extension."""
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict
from .research import local_request
from ..services.extensions import extensions
from ..services.research_jobs import research_jobs

extensions_router = APIRouter(prefix="/api/extensions", tags=["extensions"], dependencies=[Depends(local_request)])
def get_extensions():
    return extensions
class EnableInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool
def translate(error):
    raise HTTPException(409, str(error)) from None
@extensions_router.get("/ai")
def status(manager=Depends(get_extensions)):
    return manager.status()
@extensions_router.get("/ai/catalog")
async def catalog(manager=Depends(get_extensions)):
    return await manager.catalog()
@extensions_router.post("/ai/install")
async def install(manager=Depends(get_extensions)):
    try:
        return await manager.start_install(research_jobs.cleanup)
    except ValueError as error:
        translate(error)
@extensions_router.post("/ai/cancel")
async def cancel(manager=Depends(get_extensions)):
    return await manager.cancel_install()
@extensions_router.put("/ai/enabled")
async def enabled(payload: EnableInput, manager=Depends(get_extensions)):
    try:
        return await manager.set_enabled(payload.enabled, research_jobs.cleanup)
    except ValueError as error:
        translate(error)
@extensions_router.delete("/ai")
async def uninstall(clear_data: bool = Query(False), manager=Depends(get_extensions)):
    try:
        return await manager.uninstall(research_jobs.cleanup, clear_data)
    except ValueError as error:
        translate(error)
@extensions_router.get("/ai/assets/ui.js")
def asset(manager=Depends(get_extensions)):
    try:
        manager.require_enabled()
    except ValueError as error:
        raise HTTPException(409, str(error)) from None
    return FileResponse(manager.installed_root() / "assets/ui.js", media_type="text/javascript", headers={"Cache-Control": "no-store"})
