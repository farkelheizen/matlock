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
        vectors: list[list[float]] = []
        for text in texts:
            lowered = text.lower()
            if "semantic" in lowered:
                vectors.append([0.0, 1.0, 0.0])
            else:
                vectors.append([1.0, 0.0, 0.0])
        return vectors


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


def _seed_file(
    conn,
    base_dir: Path,
    *,
    file_path: str,
    frontmatter: dict,
    body: str,
    project_id: str,
    super_project_id: str,
    chunks: list[str],
    vectors: list[list[float]],
) -> None:
    abs_path = base_dir / file_path
    abs_path.parent.mkdir(parents=True, exist_ok=True)
    abs_path.write_text(body, encoding="utf-8")
    upsert_file(
        conn,
        {
            "file_path": file_path,
            "sha256": f"sha-{file_path}",
            "file_ext": ".md",
            "created": 1723200000,
            "modified": 1723286400,
            "modified_date": "2026-08-10T00:00:00Z",
            "deleted": 0,
            "length": len(body.encode("utf-8")),
            "word_count": len(body.split()),
            "meta_data": json.dumps(frontmatter),
            "is_generated": 0,
            "needs_parsing": 0,
        },
    )
    conn.execute(
        "INSERT OR REPLACE INTO super_project (super_project_id, title, priority) VALUES (?, ?, ?)",
        (super_project_id, super_project_id, "High"),
    )
    conn.execute(
        "INSERT OR REPLACE INTO project (project_id, super_project_id, title, home_file, priority, status, start_date, due_date)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (project_id, super_project_id, project_id, None, "High", "In Progress", None, None),
    )
    conn.execute(
        "INSERT OR REPLACE INTO file_project (file_path, project_id) VALUES (?, ?)",
        (file_path, project_id),
    )
    replace_search_chunks(
        conn,
        file_path,
        [
            {
                "chunk_id": f"{file_path}#{index:04d}",
                "chunk_index": index,
                "content": content,
            }
            for index, content in enumerate(chunks)
        ],
    )
    for index, vector in enumerate(vectors):
        upsert_search_vector(
            conn,
            {
                "chunk_id": f"{file_path}#{index:04d}",
                "embedding": json.dumps(vector).encode("utf-8"),
                "embedding_model": "fake-query-model",
                "embedding_dim": 3,
            },
        )
    conn.commit()


def _seed_dataset(conn, tmp_path: Path) -> MatlockConfig:
    base_dir = tmp_path / "vault"
    base_dir.mkdir()
    _seed_file(
        conn,
        base_dir,
        file_path="Notes/alpha.md",
        frontmatter={"status": "active", "priority": 3, "tags": ["refactor", "db"], "author": "Alice"},
        body="# Alpha\n\nplanning kickoff\n\ndatabase refactor performance optimization\n\nfollow up benchmarks\n",
        project_id="proj-alpha",
        super_project_id="super-platform",
        chunks=[
            "planning kickoff",
            "database refactor performance optimization",
            "follow up benchmarks",
        ],
        vectors=[[0.1, 0.0, 0.0], [1.0, 0.0, 0.0], [0.7, 0.0, 0.0]],
    )
    _seed_file(
        conn,
        base_dir,
        file_path="Notes/beta.md",
        frontmatter={"status": "draft", "priority": 1, "tags": "ops", "author": "Bob"},
        body="# Beta\n\noperations runbook\n\ndatabase maintenance\n",
        project_id="proj-beta",
        super_project_id="super-platform",
        chunks=["operations runbook", "database maintenance"],
        vectors=[[0.2, 0.2, 0.0], [0.4, 0.1, 0.0]],
    )
    _seed_file(
        conn,
        base_dir,
        file_path="Notes/gamma.md",
        frontmatter={"status": "active", "priority": 2, "tags": ["design"], "author": "Cara"},
        body="# Gamma\n\nsemantic search experiment\n\noptimization semantic pipeline\n",
        project_id="proj-gamma",
        super_project_id="super-research",
        chunks=["semantic search experiment", "optimization semantic pipeline"],
        vectors=[[0.0, 1.0, 0.0], [0.0, 0.9, 0.0]],
    )
    return _config(base_dir, tmp_path)


def test_metadata_only_file_granularity_returns_filtered_files(tmp_path: Path, conn) -> None:
    config = _seed_dataset(conn, tmp_path)
    engine = SearchQueryEngine(config, conn, embedding_provider=FakeQueryEmbeddingProvider())

    response = engine.execute(
        {
            "filters": {"metadata": [{"path": "$.status", "operator": "eq", "value": "active"}]},
                "output": {"granularity": "file", "include_content": False, "limit": 10},
        }
    )

    assert response.status == "success"
    assert response.stats.search_mode_executed == "metadata_only"
    assert [result.file_path for result in response.results] == ["Notes/alpha.md", "Notes/gamma.md"]
    assert response.results[0].file_details is not None
    assert response.results[0].file_details.content == ""


def test_fts_only_returns_ranked_chunk_matches(tmp_path: Path, conn) -> None:
    config = _seed_dataset(conn, tmp_path)
    engine = SearchQueryEngine(config, conn, embedding_provider=FakeQueryEmbeddingProvider())

    response = engine.execute(
        {
            "query": "database optimization",
            "search_mode": "fts_only",
            "output": {"granularity": "chunk", "limit": 10},
        }
    )

    assert response.status == "success"
    assert response.results[0].file_path == "Notes/alpha.md"
    assert response.results[0].chunk_details is not None
    assert response.results[0].chunk_details.chunk_id == "Notes/alpha.md#0001"
    assert response.results[0].score_breakdown.fts_rank == 1


def test_vector_only_uses_embedding_similarity(tmp_path: Path, conn) -> None:
    config = _seed_dataset(conn, tmp_path)
    engine = SearchQueryEngine(config, conn, embedding_provider=FakeQueryEmbeddingProvider())

    response = engine.execute(
        {
            "query": "semantic",
            "search_mode": "vector_only",
            "output": {"granularity": "chunk", "limit": 2},
        }
    )

    assert response.status == "success"
    assert response.stats.search_mode_executed == "vector_only"
    assert response.results[0].file_path == "Notes/gamma.md"
    assert response.results[0].score_breakdown.vector_similarity == pytest.approx(1.0)


def test_hybrid_combines_fts_and_vector_rankings(tmp_path: Path, conn) -> None:
    config = _seed_dataset(conn, tmp_path)
    engine = SearchQueryEngine(config, conn, embedding_provider=FakeQueryEmbeddingProvider())

    response = engine.execute(
        {
            "query": "database optimization",
            "search_mode": "hybrid",
            "output": {"granularity": "chunk", "limit": 10},
        }
    )

    assert response.status == "success"
    assert response.results[0].file_path == "Notes/alpha.md"
    assert response.results[0].score_breakdown.fts_rank == 1
    assert response.results[0].score_breakdown.vector_similarity is not None
    assert response.results[0].score_breakdown.rrf_score == pytest.approx(response.results[0].score)


def test_metadata_query_handles_millisecond_epoch_timestamps(tmp_path: Path, conn) -> None:
    config = _seed_dataset(conn, tmp_path)

    conn.execute(
        "UPDATE file SET created = ?, modified = ? WHERE file_path = ?",
        (1716824777000, 1717545576000, "Notes/alpha.md"),
    )
    conn.commit()

    engine = SearchQueryEngine(config, conn, embedding_provider=FakeQueryEmbeddingProvider())
    response = engine.execute(
        {
            "filters": {
                "project_id": ["proj-alpha"],
                "project_match_mode": "exact",
            },
            "output": {"granularity": "file", "include_content": False, "limit": 1},
        }
    )

    assert response.status == "success"
    assert len(response.results) == 1
    assert response.results[0].created.startswith("2024-")
    assert response.results[0].modified.startswith("2024-")


def test_project_filters_require_exact_match_mode(tmp_path: Path, conn) -> None:
    config = _seed_dataset(conn, tmp_path)
    engine = SearchQueryEngine(config, conn, embedding_provider=FakeQueryEmbeddingProvider())

    with pytest.raises(ValueError, match="project_match_mode"):
        engine.execute(
            {
                "filters": {
                    "project_id": ["proj-alpha"],
                    "project_match_mode": "hybrid",
                },
            }
        )

    response = engine.execute(
        {
            "filters": {
                "project_id": ["proj-gamma"],
                "project_match_mode": "exact",
            },
                "output": {"granularity": "file", "include_content": False},
        }
    )
    assert [result.file_path for result in response.results] == ["Notes/gamma.md"]