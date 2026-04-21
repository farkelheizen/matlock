from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ParsedTask(BaseModel):
    checked: bool
    task_text: str
    overflow: bool = False
    headers: list[str]
    attributes: dict[str, Any]
    errors: list[str] = Field(default_factory=list)
    parent_task_id: str | None
    twin_index: int = 0
    task_id: str


class ParsedDocument(BaseModel):
    meta_data: dict[str, Any]
    tasks: list[ParsedTask]
    file_path: str | None = None
    created: int | None = None
    modified: int | None = None
    length: int | None = None
    word_count: int | None = None
    sha256: str | None = None
