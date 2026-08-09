from __future__ import annotations

import json

import pytest

from matlock.db import get_connection, init_db, upsert_file
from matlock.search.models import SearchFilters
from matlock.search.sql_filters import build_file_filter_clause, build_metadata_filter_clause


@pytest.fixture()
def conn() -> object:
    db = get_connection(":memory:")
    init_db(db)
    rows = [
        ("Notes/alpha.md", {"status": "active", "priority": 3, "tags": ["refactor", "db"], "author": "Alice"}),
        ("Notes/beta.md", {"status": "draft", "priority": 1, "tags": "ops", "author": "Bob"}),
        ("Notes/gamma.md", {"status": "active", "priority": 2, "tags": ["design"], "author": "Cara"}),
    ]
    for file_path, meta_data in rows:
        upsert_file(
            db,
            {
                "file_path": file_path,
                "sha256": f"sha-{file_path}",
                "file_ext": ".md",
                "created": 1723200000,
                "modified": 1723286400,
                "modified_date": "2026-08-10T00:00:00Z",
                "deleted": 0,
                "length": 10,
                "word_count": 2,
                "meta_data": json.dumps(meta_data),
                "is_generated": 0,
                "needs_parsing": 0,
            },
        )
    db.commit()
    yield db
    db.close()


def _matching_paths(conn, filters: SearchFilters) -> list[str]:
    clause, params = build_file_filter_clause(filters)
    sql = "SELECT f.file_path FROM file f WHERE f.deleted = 0 AND f.is_generated = 0"
    if clause:
        sql += f" AND {clause}"
    sql += " ORDER BY f.file_path"
    return [row["file_path"] for row in conn.execute(sql, params).fetchall()]


def test_contains_and_in_compile_to_json_each() -> None:
    contains_clause, _ = build_metadata_filter_clause(
        SearchFilters.model_validate(
            {"metadata": [{"path": "$.tags", "operator": "contains", "value": "db"}]}
        ).metadata[0]
    )
    in_clause, _ = build_metadata_filter_clause(
        SearchFilters.model_validate(
            {"metadata": [{"path": "$.author", "operator": "in", "value": ["Alice", "Cara"]}]}
        ).metadata[0]
    )

    assert "json_each" in contains_clause
    assert "json_each" in in_clause


def test_contains_matches_array_and_scalar_metadata(conn) -> None:
    tags_filters = SearchFilters.model_validate(
        {"metadata": [{"path": "$.tags", "operator": "contains", "value": "db"}]}
    )
    status_filters = SearchFilters.model_validate(
        {"metadata": [{"path": "$.status", "operator": "contains", "value": "draft"}]}
    )

    assert _matching_paths(conn, tags_filters) == ["Notes/alpha.md"]
    assert _matching_paths(conn, status_filters) == ["Notes/beta.md"]


def test_in_matches_scalar_and_array_metadata(conn) -> None:
    author_filters = SearchFilters.model_validate(
        {"metadata": [{"path": "$.author", "operator": "in", "value": ["Alice", "Cara"]}]}
    )
    tags_filters = SearchFilters.model_validate(
        {"metadata": [{"path": "$.tags", "operator": "in", "value": ["db", "design"]}]}
    )

    assert _matching_paths(conn, author_filters) == ["Notes/alpha.md", "Notes/gamma.md"]
    assert _matching_paths(conn, tags_filters) == ["Notes/alpha.md", "Notes/gamma.md"]


def test_numeric_and_not_equal_filters_compose_with_and_semantics(conn) -> None:
    filters = SearchFilters.model_validate(
        {
            "metadata": [
                {"path": "$.priority", "operator": "gte", "value": 2},
                {"path": "$.status", "operator": "neq", "value": "draft"},
            ]
        }
    )

    assert _matching_paths(conn, filters) == ["Notes/alpha.md", "Notes/gamma.md"]