"""Research space request contracts."""

from typing import Any

from pydantic import BaseModel, Field

from .library import LibraryKey


class SpaceInfoInput(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    description: str = Field(default="", max_length=200)


class ActiveSpaceInput(BaseModel):
    active_space_id: int | None = Field(default=None, ge=1)


class SpaceArchiveInput(BaseModel):
    archived: bool


class SpaceItemsInput(BaseModel):
    results: list[dict[str, Any]] = Field(min_length=1, max_length=500)


class SpaceKeysInput(BaseModel):
    keys: list[LibraryKey] = Field(min_length=1, max_length=500)


class SpaceNoteInput(BaseModel):
    document: dict[str, Any]
    format_version: int = 1
    revision: int = Field(ge=0)
