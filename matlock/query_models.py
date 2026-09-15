from __future__ import annotations

import json
import re
from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ProjectRecord(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    project_id: str
    super_project_id: str | None = None
    title: str | None = None
    home_file: str | None = None
    priority: str | None = None
    status: str | None = None
    start_date: str | None = None
    due_date: str | None = None


class SuperProjectRecord(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    super_project_id: str
    title: str | None = None
    priority: str | None = None


class TaskRecord(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    task_id: str
    file_path: str
    parent_task_id: str | None = None
    created_date: str | None = None
    due_date: str | None = None
    est_comp_date: str | None = None
    act_comp_date: str | None = None
    checked: bool = False
    task_text: str | None = None
    overflow: bool = False
    headers: list[str] = Field(default_factory=list)
    attributes: dict[str, Any] = Field(default_factory=dict)
    errors: list[str] = Field(default_factory=list)
    twin_index: int = 0
    project_ids: list[str] = Field(default_factory=list)

    @field_validator("checked", "overflow", mode="before")
    @classmethod
    def _coerce_bool(cls, value: Any) -> Any:
        if value in (0, 1):
            return bool(value)
        return value

    @field_validator("headers", mode="before")
    @classmethod
    def _parse_headers(cls, value: Any) -> list[str]:
        return cls._parse_json_list(value, field_name="headers")

    @field_validator("attributes", mode="before")
    @classmethod
    def _parse_attributes(cls, value: Any) -> dict[str, Any]:
        return cls._parse_json_object(value, field_name="attributes")

    @field_validator("errors", mode="before")
    @classmethod
    def _parse_errors(cls, value: Any) -> list[str]:
        return cls._parse_json_list(value, field_name="errors")

    @staticmethod
    def _parse_json_list(value: Any, *, field_name: str) -> list[str]:
        if value is None:
            return []
        if isinstance(value, list):
            return [str(item) for item in value]
        if isinstance(value, str):
            try:
                parsed = json.loads(value)
            except json.JSONDecodeError as exc:  # pragma: no cover - guard against bad legacy rows
                raise ValueError(f"Invalid JSON in {field_name}: {value!r}") from exc
            if isinstance(parsed, list):
                return [str(item) for item in parsed]
            return [str(parsed)]
        raise ValueError(f"Invalid JSON in {field_name}: {value!r}")

    @staticmethod
    def _parse_json_object(value: Any, *, field_name: str) -> dict[str, Any]:
        if value is None:
            return {}
        if isinstance(value, dict):
            return value
        if isinstance(value, str):
            try:
                parsed = json.loads(value)
            except json.JSONDecodeError as exc:  # pragma: no cover - guard against bad legacy rows
                raise ValueError(f"Invalid JSON in {field_name}: {value!r}") from exc
            if isinstance(parsed, dict):
                return parsed
            raise ValueError(f"Invalid JSON in {field_name}: expected object, got {type(parsed).__name__}")
        raise ValueError(f"Invalid JSON in {field_name}: {value!r}")


class DatePredicate(BaseModel):
    field: str = "due_date"
    operator: str = "="
    value: date


class QueryResponseStats(BaseModel):
    model_config = ConfigDict(extra="forbid")

    total_matches: int = Field(ge=0)
    returned_matches: int = Field(ge=0)
    limit: int | None = Field(default=None, ge=1)
    offset: int = Field(default=0, ge=0)


class TaskQueryResponse(QueryResponseStats):
    results: list[TaskRecord] = Field(default_factory=list)


class ProjectQueryResponse(QueryResponseStats):
    results: list[ProjectRecord] = Field(default_factory=list)


class TaskQueryFilters(BaseModel):
    due_date: list[DatePredicate] = Field(default_factory=list)
    est_comp_date: list[DatePredicate] = Field(default_factory=list)
    act_comp_date: list[DatePredicate] = Field(default_factory=list)
    checked: bool | None = None
    task_text: str | None = None
    headers: str | None = None
    attributes: str | None = None
    project_ids: list[str] = Field(default_factory=list)
    super_project_ids: list[str] = Field(default_factory=list)


AttributeFilterOperator = Literal["eq", "neq", "gt", "gte", "lt", "lte", "in", "contains"]

_ATTRIBUTE_FILTER_OPERATORS: set[str] = {"eq", "neq", "gt", "gte", "lt", "lte", "in", "contains"}


class AttributeFilterPredicate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str
    operator: AttributeFilterOperator = "eq"
    value: Any


def parse_date_predicate(raw: str, field: str = "due_date") -> DatePredicate:
    """Parse a date comparison token like ``>=2026-01-01`` or ``2026-01-01``."""
    if raw is None or raw == "":
        raise ValueError("Date predicate cannot be empty")

    match = re.fullmatch(r"(>=|<=|=|>|<)?(\d{4}-\d{2}-\d{2})", raw)
    if match is None:
        raise ValueError(f"Invalid date predicate: {raw!r}; expected YYYY-MM-DD or =/</>/<=/>=/<=YYYY-MM-DD")

    operator, date_value = match.groups()
    if operator is None:
        operator = "="
    try:
        parsed_date = date.fromisoformat(date_value)
    except ValueError as exc:  # pragma: no cover - standard library validation
        raise ValueError(f"Invalid date value: {date_value!r}") from exc

    return DatePredicate(field=field, operator=operator, value=parsed_date)


def _coerce_attribute_token(token: str) -> Any:
    """Coerce one raw attribute-filter value token to match likely JSON storage types."""
    lowered = token.lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    if lowered == "null":
        return None
    try:
        return int(token)
    except ValueError:
        pass
    try:
        return float(token)
    except ValueError:
        pass
    return token


def parse_attribute_filter_predicate(raw: str) -> AttributeFilterPredicate:
    """Parse a colon-delimited ``path:operator:value`` attribute-filter token.

    Bare paths without a leading ``$`` are auto-prefixed with ``$.``. The
    ``in`` operator accepts a comma-separated value list.
    """
    if raw is None or raw == "":
        raise ValueError("Attribute filter cannot be empty")

    parts = raw.split(":", 2)
    if len(parts) != 3:
        raise ValueError(
            f"Invalid attribute filter: {raw!r}; expected PATH:OPERATOR:VALUE"
        )
    path, operator, raw_value = parts
    if not path:
        raise ValueError(f"Invalid attribute filter: {raw!r}; path cannot be empty")
    if operator not in _ATTRIBUTE_FILTER_OPERATORS:
        raise ValueError(
            f"Invalid attribute filter operator: {operator!r}; "
            f"expected one of {sorted(_ATTRIBUTE_FILTER_OPERATORS)}"
        )
    if not path.startswith("$"):
        path = f"$.{path}"

    if operator == "in":
        value: Any = [_coerce_attribute_token(token) for token in raw_value.split(",")]
    else:
        value = _coerce_attribute_token(raw_value)

    return AttributeFilterPredicate(path=path, operator=operator, value=value)

