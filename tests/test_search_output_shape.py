from __future__ import annotations

import json
from pathlib import Path

import pytest

from matlock.config import MatlockConfig
from matlock.db import get_connection, init_db, replace_search_chunks, upsert_file, upsert_search_vector
from matlock.search.query_engine import SearchQueryEngine


class FakeQueryEmbeddingProvider:
    model_name = "fake-query-model"
    dimensions = 3

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [[1.0, 0.0, 0.0] for _ in texts]


@pytest.fixture()
def conn() -> object:
    db = get_connection(":memory:")
    init_db(db)
    yield db
    db.close()


def _config(base_dir: Path, tmp_path: Path) -> MatlockConfig:
    return MatlockConfig(
        base_directory=base_dir,
        db_path=tmp_path / "matlock.db",
        output_directory=base_dir / "_output",
        search={
            "embedding": {
                "provider": "fastembed",
                "model_name": "mini",
                "dimensions": 3,
            }
        },
    )


def _seed_alpha(conn, base_dir: Path) -> None:
    file_path = "Notes/alpha.md"
    body = "# Alpha\n\nplanning kickoff\n\ndatabase refactor performance optimization\n\nfollow up benchmarks\n"
    abs_path = base_dir / file_path
    abs_path.parent.mkdir(parents=True, exist_ok=True)
    abs_path.write_text(body, encoding="utf-8")
    upsert_file(
        conn,
        {
            "file_path": file_path,
            "sha256": "sha-alpha",
            "file_ext": ".md",
            "created": 1723200000,
            "modified": 1723286400,
            "modified_date": "2026-08-10T00:00:00Z",
            "deleted": 0,
            "length": len(body.encode("utf-8")),
            "word_count": len(body.split()),
            "meta_data": json.dumps({"status": "active"}),
            "is_generated": 0,
            "needs_parsing": 0,
        },
    )
    conn.execute(
        "INSERT OR REPLACE INTO project (project_id, super_project_id, title, home_file, priority, status, start_date, due_date)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        ("proj-alpha", None, "Alpha", None, "High", "In Progress", None, None),
    )
    conn.execute(
        "INSERT OR REPLACE INTO file_project (file_path, project_id) VALUES (?, ?)",
        (file_path, "proj-alpha"),
    )
    replace_search_chunks(
        conn,
        file_path,
        [
            {"chunk_id": "Notes/alpha.md#0000", "chunk_index": 0, "content": "planning kickoff"},
            {"chunk_id": "Notes/alpha.md#0001", "chunk_index": 1, "content": "database refactor performance optimization"},
            {"chunk_id": "Notes/alpha.md#0002", "chunk_index": 2, "content": "follow up benchmarks"},
        ],
    )
    upsert_search_vector(
        conn,
        {
            "chunk_id": "Notes/alpha.md#0000",
            "embedding": b"[0.1, 0.0, 0.0]",
            "embedding_model": "fake-query-model",
            "embedding_dim": 3,
        },
    )
    upsert_search_vector(
        conn,
        {
            "chunk_id": "Notes/alpha.md#0001",
            "embedding": b"[1.0, 0.0, 0.0]",
            "embedding_model": "fake-query-model",
            "embedding_dim": 3,
        },
    )
    upsert_search_vector(
        conn,
        {
            "chunk_id": "Notes/alpha.md#0002",
            "embedding": b"[0.7, 0.0, 0.0]",
            "embedding_model": "fake-query-model",
            "embedding_dim": 3,
        },
    )
    conn.commit()


def test_chunk_granularity_includes_adjacent_chunks_and_stats(tmp_path: Path, conn) -> None:
    base_dir = tmp_path / "vault"
    base_dir.mkdir()
    _seed_alpha(conn, base_dir)
    engine = SearchQueryEngine(_config(base_dir, tmp_path), conn, embedding_provider=FakeQueryEmbeddingProvider())

    response = engine.execute(
        {
            "query": "database optimization",
            "search_mode": "fts_only",
            "output": {"granularity": "chunk", "surrounding_chunks": 1, "limit": 1},
        }
    )

    result = response.results[0]
    assert response.stats.total_matches == 1
    assert response.stats.returned_matches == 1
    assert result.chunk_details is not None
    assert result.chunk_details.total_chunks == 3
    assert result.chunk_details.before == ["planning kickoff"]
    assert result.chunk_details.after == ["follow up benchmarks"]


def test_file_granularity_returns_file_details_without_chunk_block(tmp_path: Path, conn) -> None:
    base_dir = tmp_path / "vault"
    base_dir.mkdir()
    _seed_alpha(conn, base_dir)
    engine = SearchQueryEngine(_config(base_dir, tmp_path), conn, embedding_provider=FakeQueryEmbeddingProvider())

    response = engine.execute(
        {
            "query": "database",
            "search_mode": "fts_only",
                "output": {"granularity": "file", "include_content": False, "limit": 1},
        }
    )

    result = response.results[0]
    assert result.chunk_details is None
    assert result.file_details is not None
    assert result.file_details.total_matching_chunks == 1
    assert result.file_details.content == ""


def test_chunk_granularity_honors_include_content_false(tmp_path: Path, conn) -> None:
    base_dir = tmp_path / "vault"
    base_dir.mkdir()
    _seed_alpha(conn, base_dir)
    engine = SearchQueryEngine(_config(base_dir, tmp_path), conn, embedding_provider=FakeQueryEmbeddingProvider())

    response = engine.execute(
        {
            "query": "database",
            "search_mode": "fts_only",
                "output": {"granularity": "chunk", "surrounding_chunks": 1, "include_content": False, "limit": 1},
        }
    )

    result = response.results[0]
    assert result.chunk_details is not None
    assert result.chunk_details.content is None
    assert result.chunk_details.before == []
    assert result.chunk_details.after == []