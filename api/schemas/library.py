"""本地收藏库的请求模型。

收藏内容用 ``result`` 字典承载（与前端 UnifiedSearchResult 对齐），
后端只按白名单字段持久化，避免把内部字段写进库。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, field_validator


class LibraryKey(BaseModel):
    """(platform, content_id) 唯一键。"""

    platform: str = Field(min_length=1)
    content_id: str = Field(min_length=1)


class LibraryItemInput(BaseModel):
    result: Dict[str, Any]
    note: str = ""
    fetched_at: Optional[str] = None
    collection_ids: List[int] = Field(default_factory=list)
    in_default: Optional[bool] = None
    watch_later: bool = False


class LibraryItemsBatchInput(BaseModel):
    entries: List[LibraryItemInput] = Field(default_factory=list)
    # 批量加入的收藏夹（作用于本批全部条目）
    collection_ids: List[int] = Field(default_factory=list)


class LibraryNotePatch(BaseModel):
    note: str = ""


class LibraryKeysInput(BaseModel):
    keys: List[LibraryKey] = Field(default_factory=list)

    @field_validator("keys")
    @classmethod
    def not_empty(cls, value: List[LibraryKey]) -> List[LibraryKey]:
        if not value:
            raise ValueError("keys 不能为空")
        return value


class CollectionCreate(BaseModel):
    name: str = Field(min_length=1, max_length=60)


class CollectionRename(BaseModel):
    name: str = Field(min_length=1, max_length=60)


class CollectionBatchInput(BaseModel):
    keys: List[LibraryKey] = Field(default_factory=list)

    @field_validator("keys")
    @classmethod
    def not_empty(cls, value: List[LibraryKey]) -> List[LibraryKey]:
        if not value:
            raise ValueError("keys 不能为空")
        return value


class LibraryImportInput(BaseModel):
    """导入备份 / 迁移旧 localStorage 数据。

    兼容 v1（旧浏览器收藏）、v2（旧本机库）与 v3（内置收藏夹）结构。
    """

    payload: Dict[str, Any]
