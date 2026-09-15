"""Search-independent JSON-path SQL filter helpers.

Shared by `matlock/db.py` (task attribute filtering) and
`matlock/search/sql_filters.py` (file metadata filtering) so both build
`json_extract`/`json_each` predicates from a single implementation.
"""

from __future__ import annotations

from typing import Any, Literal

JsonPathOperator = Literal["eq", "neq", "gt", "gte", "lt", "lte", "in", "contains"]

_SCALAR_OPERATORS = {"eq", "neq", "gt", "gte", "lt", "lte"}

_SQL_OPERATORS = {
    "eq": "=",
    "neq": "!=",
    "gt": ">",
    "gte": ">=",
    "lt": "<",
    "lte": "<=",
}


def build_json_path_filter_clause(
    *,
    path: str,
    operator: str,
    value: Any,
    json_column: str,
) -> tuple[str, list[Any]]:
    """Compile one JSON-path predicate against `json_column` into parameterized SQL."""

    if operator in _SCALAR_OPERATORS:
        sql_operator = _SQL_OPERATORS[operator]
        return (
            f"json_extract({json_column}, ?) {sql_operator} ?",
            [path, _coerce_scalar_value(value)],
        )

    values = _coerce_collection_values(value)
    return (
        "EXISTS ("
        " SELECT 1"
        " FROM json_each("
        "   CASE"
        f"     WHEN json_type({json_column}, ?) = 'array' THEN json_extract({json_column}, ?)"
        f"     ELSE json_array(json_extract({json_column}, ?))"
        "   END"
        " ) AS json_path_value"
        f" WHERE json_path_value.value IN ({_placeholders(len(values))})"
        ")",
        [path, path, path, *values],
    )


def _placeholders(count: int) -> str:
    return ", ".join("?" for _ in range(count))


def _coerce_scalar_value(value: Any) -> Any:
    if isinstance(value, list):
        if not value:
            return None
        return value[0]
    return value


def _coerce_collection_values(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    return [value]


__all__ = ["build_json_path_filter_clause", "JsonPathOperator"]
