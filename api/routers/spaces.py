"""Local research spaces, independent of favorite and watch-later records."""

from fastapi import APIRouter, Depends, HTTPException

from ..schemas.spaces import ActiveSpaceInput, SpaceArchiveInput, SpaceInfoInput, SpaceItemsInput, SpaceKeysInput, SpaceNoteInput
from ..services.spaces_store import SpaceConflict, SpaceMissing, SpacesStore, get_spaces_store

spaces_router = APIRouter(prefix="/api/spaces", tags=["spaces"])


def _call(operation, *args):
    try:
        return operation(*args)
    except SpaceMissing as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except SpaceConflict as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except (ValueError, TypeError) as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@spaces_router.get("")
def list_spaces(store: SpacesStore = Depends(get_spaces_store)):
    return store.list_spaces()


@spaces_router.post("", status_code=201)
def create_space(payload: SpaceInfoInput, store: SpacesStore = Depends(get_spaces_store)):
    return _call(store.create_space, payload.name, payload.description)


@spaces_router.put("/active")
def set_active(payload: ActiveSpaceInput, store: SpacesStore = Depends(get_spaces_store)):
    return _call(store.set_active, payload.active_space_id)


@spaces_router.get("/{space_id}")
def get_space(space_id: int, store: SpacesStore = Depends(get_spaces_store)):
    return _call(store.get_space, space_id)


@spaces_router.patch("/{space_id}")
def update_info(space_id: int, payload: SpaceInfoInput, store: SpacesStore = Depends(get_spaces_store)):
    return _call(store.update_info, space_id, payload.name, payload.description)


@spaces_router.put("/{space_id}/archive")
async def archive_space(space_id: int, payload: SpaceArchiveInput, store: SpacesStore = Depends(get_spaces_store)):
    if payload.archived:
        from ..services.research_jobs import research_jobs
        await research_jobs.cancel_space(space_id)
    return _call(store.set_archived, space_id, payload.archived)


@spaces_router.delete("/{space_id}")
async def delete_space(space_id: int, store: SpacesStore = Depends(get_spaces_store)):
    from ..services.research_jobs import research_jobs
    from ..services.research_config import research_config
    await research_jobs.cancel_space(space_id)
    result = _call(store.delete_space, space_id)
    research_config.remove_preference(space_id)
    return result


@spaces_router.post("/{space_id}/items")
def add_items(space_id: int, payload: SpaceItemsInput, store: SpacesStore = Depends(get_spaces_store)):
    return _call(store.add_items, space_id, payload.results)


@spaces_router.delete("/{space_id}/items")
def remove_items(space_id: int, payload: SpaceKeysInput, store: SpacesStore = Depends(get_spaces_store)):
    return _call(store.remove_items, space_id, [key.model_dump() for key in payload.keys])


@spaces_router.put("/{space_id}/note")
def save_note(space_id: int, payload: SpaceNoteInput, store: SpacesStore = Depends(get_spaces_store)):
    return _call(store.save_note, space_id, payload.document, payload.format_version, payload.revision)
