from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from matlock.search.models import SearchDateRange, SearchFilters, SearchMetadataFilter
from matlock.sql_filters import build_json_path_filter_clause


def build_file_filter_clause(
    filters: SearchFilters,
    *,
    file_alias: str = "f",
) -> tuple[str, list[Any]]:
    """Compile request filters into a SQLite WHERE fragment and parameters."""

    clauses: list[str] = []
    params: list[Any] = []

    created_clause, created_params = _build_date_range_clause(
        column=f"{file_alias}.created",
        date_range=filters.created,
    )
    if created_clause:
        clauses.append(created_clause)
        params.extend(created_params)

    modified_clause, modified_params = _build_date_range_clause(
        column=f"{file_alias}.modified",
        date_range=filters.modified,
    )
    if modified_clause:
        clauses.append(modified_clause)
        params.extend(modified_params)

    for column_name, predicates in (
        ("created", filters.created_predicates),
        ("modified", filters.modified_predicates),
    ):
        predicate_clause, predicate_params = _build_date_predicates_clause(
            column=f"{file_alias}.{column_name}",
            predicates=predicates,
        )
        if predicate_clause:
            clauses.append(predicate_clause)
            params.extend(predicate_params)

    if filters.project_id:
        if filters.project_match_mode != "exact":
            raise ValueError(
                "project_match_mode must be 'exact' when project_id filters are provided"
            )
        project_clause, project_params = _build_exists_clause(
            sql=(
                "SELECT 1"
                " FROM file_project fp"
                f" WHERE fp.file_path = {file_alias}.file_path"
                f"   AND LOWER(fp.project_id) IN ({_placeholders(len(filters.project_id))})"
            ),
            params=[value.lower() for value in filters.project_id],
        )
        clauses.append(project_clause)
        params.extend(project_params)

    if filters.super_project_id:
        if filters.project_match_mode != "exact":
            raise ValueError(
                "project_match_mode must be 'exact' when super_project_id filters are provided"
            )
        super_project_clause, super_project_params = _build_exists_clause(
            sql=(
                "SELECT 1"
                " FROM file_project fp"
                " JOIN project p ON p.project_id = fp.project_id"
                f" WHERE fp.file_path = {file_alias}.file_path"
                f"   AND LOWER(p.super_project_id) IN ({_placeholders(len(filters.super_project_id))})"
            ),
            params=[value.lower() for value in filters.super_project_id],
        )
        clauses.append(super_project_clause)
        params.extend(super_project_params)

    if filters.file_paths:
        file_path_clause = " OR ".join(
            f"substr({file_alias}.file_path, 1, length(?)) = ?" for _ in filters.file_paths
        )
        clauses.append(f"({file_path_clause})")
        for value in filters.file_paths:
            normalized = value.lstrip("/")
            params.extend([normalized, normalized])

    if filters.file_exts:
        clauses.append(
            "(" + " OR ".join(
                f"LOWER({file_alias}.file_ext) = LOWER(?)" for _ in filters.file_exts
            ) + ")"
        )
        params.extend(filters.file_exts)

    for metadata_filter in filters.metadata:
        metadata_clause, metadata_params = build_metadata_filter_clause(
            metadata_filter,
            json_column=f"{file_alias}.meta_data",
        )
        clauses.append(metadata_clause)
        params.extend(metadata_params)

    if not clauses:
        return "", []

    return " AND ".join(f"({clause})" for clause in clauses), params


def build_metadata_filter_clause(
    metadata_filter: SearchMetadataFilter,
    *,
    json_column: str = "f.meta_data",
) -> tuple[str, list[Any]]:
    """Compile one metadata filter into a SQLite predicate."""

    return build_json_path_filter_clause(
        path=metadata_filter.path,
        operator=metadata_filter.operator,
        value=metadata_filter.value,
        json_column=json_column,
    )


def _build_date_range_clause(
    *,
    column: str,
    date_range: SearchDateRange | None,
) -> tuple[str, list[Any]]:
    if date_range is None:
        return "", []

    clauses: list[str] = []
    params: list[Any] = []
    if date_range.min is not None:
        clauses.append(f"datetime({column}, 'unixepoch') >= datetime(?)")
        params.append(date_range.min)
    if date_range.max is not None:
        clauses.append(f"datetime({column}, 'unixepoch') <= datetime(?)")
        params.append(date_range.max)
    return " AND ".join(clauses), params


def _build_date_predicates_clause(
    *,
    column: str,
    predicates: Sequence[object],
) -> tuple[str, list[Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    for predicate in predicates:
        operator = predicate.operator
        clauses.append(f"date(datetime({column}, 'unixepoch')) {operator} ?")
        params.append(predicate.value)
    return " AND ".join(clauses), params


def _build_exists_clause(sql: str, params: Sequence[Any]) -> tuple[str, list[Any]]:
    return f"EXISTS ({sql})", list(params)


def _placeholders(count: int) -> str:
    return ", ".join("?" for _ in range(count))


__all__ = ["build_file_filter_clause", "build_metadata_filter_clause"]