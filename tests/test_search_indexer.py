from __future__ import annotations

import json
from pathlib import Path

import pytest

from matlock.config import MatlockConfig
from matlock.db import (
    get_connection,
    get_file,
    get_search_chunks_for_file,
    init_db,
    mark_file_search_indexed,
    replace_search_chunks,
    upsert_file,
    upsert_search_vector,
)
from matlock.search.indexer import run_search_indexing


class FakeEmbeddingProvider:
    def __init__(self, dimensions: int = 3):
        self.model_name = "fake-model"
        self.dimensions = dimensions
        self.calls: list[list[str]] = []

    def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        return [
            [float(index), float(len(text.split())), 1.0]
            for index, text in enumerate(texts)
        ]


@pytest.fixture()
def conn():
    db = get_connection(":memory:")
    init_db(db)
    yield db
    db.close()


def _config(base_dir: Path, tmp_path: Path, **search_overrides) -> MatlockConfig:
    search = {
        "indexing": {"batch_size": 2},
        "chunking": {
            "strategy": "fixed_token",
            "chunk_size": 10,
            "chunk_overlap": 2,
            "inject_frontmatter": True,
            "frontmatter_template": "[Project: {db.project_id} | File: {sys.file_name} | Status: {fm.status}]",
        },
        "embedding": {
            "provider": "fastembed",
            "model_name": "mini",
            "dimensions": 3,
        },
    }
    for key, value in search_overrides.items():
        section, field = key.split("__", 1)
        search[section][field] = value
    return MatlockConfig(
        base_directory=base_dir,
        db_path=tmp_path / "matlock.db",
        output_directory=base_dir / "_output",
        search=search,
    )


def _seed_file(conn, base_dir: Path, file_path: str, content: str, **overrides) -> None:
    abs_path = base_dir / file_path
    abs_path.parent.mkdir(parents=True, exist_ok=True)
    abs_path.write_text(content, encoding="utf-8")
    row = {
        "file_path": file_path,
        "sha256": overrides.pop("sha256", f"sha-{file_path}"),
        "file_ext": ".md",
        "created": 1000,
        "modified": 2000,
        "modified_date": "2026-01-01",
        "deleted": 0,
        "length": len(content.encode("utf-8")),
        "word_count": 0,
        "meta_data": json.dumps(overrides.pop("meta_data", {})),
        "is_generated": 0,
        "needs_parsing": 0,
    }
    row.update(overrides)
    upsert_file(conn, row)


def _link_project(conn, file_path: str, project_id: str = "p1", super_project_id: str = "sp1"):
    conn.execute(
        "INSERT OR REPLACE INTO super_project (super_project_id, title, priority) VALUES (?, ?, ?)",
        (super_project_id, "Super", "High"),
    )
    conn.execute(
        "INSERT OR REPLACE INTO project (project_id, super_project_id, title, home_file, priority, status, start_date, due_date)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (project_id, super_project_id, "Project", None, "High", "In Progress", None, None),
    )
    conn.execute(
        "INSERT OR REPLACE INTO file_project (file_path, project_id) VALUES (?, ?)",
        (file_path, project_id),
    )


def test_run_search_indexing_writes_chunks_vectors_and_frontmatter_context(tmp_path: Path, conn):
    vault = tmp_path / "vault"
    vault.mkdir()
    config = _config(vault, tmp_path)
    provider = FakeEmbeddingProvider()
    content = """---
status: active
---
# Topic

alpha beta gamma delta epsilon zeta eta theta iota kappa lambda
"""
    _seed_file(conn, vault, "Notes/note.md", content)
    _link_project(conn, "Notes/note.md")

    result = run_search_indexing(
        config,
        conn,
        embedding_provider=provider,
        indexed_at="2026-08-09T10:00:00Z",
    )

    chunks = get_search_chunks_for_file(conn, "Notes/note.md")
    vectors = conn.execute("SELECT * FROM search_vec ORDER BY chunk_id").fetchall()
    file_row = get_file(conn, "Notes/note.md")

    assert result.indexed_files == 1
    assert result.chunks_written == len(chunks)
    assert result.vectors_written == len(vectors)
    assert len(chunks) >= 2
    assert chunks[0]["content"].startswith("[Project: p1 | File: note.md | Status: active]\n\n# Topic")
    assert file_row["search_indexed_at"] == "2026-08-09T10:00:00Z"
    assert file_row["search_index_hash"] == "sha-Notes/note.md"
    assert provider.calls and len(provider.calls[0]) == len(chunks)


def test_force_index_override_reindexes_fresh_file(tmp_path: Path, conn):
    vault = tmp_path / "vault"
    vault.mkdir()
    config = _config(vault, tmp_path)
    provider = FakeEmbeddingProvider()
    content = """---
matlock:
  search:
    force_index: true
---
alpha beta gamma delta epsilon
"""
    _seed_file(
        conn,
        vault,
        "Notes/forced.md",
        content,
        meta_data={"matlock": {"search": {"force_index": True}}},
        sha256="same-hash",
    )
    replace_search_chunks(
        conn,
        "Notes/forced.md",
        [{"chunk_id": "Notes/forced.md#0000", "chunk_index": 0, "content": "old chunk"}],
    )
    upsert_search_vector(
        conn,
        {
            "chunk_id": "Notes/forced.md#0000",
            "embedding": b"[0,0,0]",
            "embedding_model": "old-model",
            "embedding_dim": 3,
        },
    )
    mark_file_search_indexed(conn, "Notes/forced.md", "2026-08-01T00:00:00Z", "same-hash")
    conn.commit()

    result = run_search_indexing(
        config,
        conn,
        embedding_provider=provider,
        indexed_at="2026-08-09T11:00:00Z",
    )

    chunks = get_search_chunks_for_file(conn, "Notes/forced.md")
    vector = conn.execute("SELECT * FROM search_vec WHERE chunk_id = ?", ("Notes/forced.md#0000",)).fetchone()

    assert result.indexed_files == 1
    assert chunks[0]["content"].endswith("alpha beta gamma delta epsilon")
    assert vector["embedding_model"] == "fake-model"
    assert provider.calls == [[chunks[0]["content"]]]


def test_exclude_override_clears_existing_rows_and_marks_file_fresh(tmp_path: Path, conn):
    vault = tmp_path / "vault"
    vault.mkdir()
    config = _config(vault, tmp_path)
    content = """---
matlock:
  search:
    exclude: true
---
alpha beta gamma
"""
    _seed_file(conn, vault, "Notes/excluded.md", content, sha256="exclude-hash")
    replace_search_chunks(
        conn,
        "Notes/excluded.md",
        [{"chunk_id": "Notes/excluded.md#0000", "chunk_index": 0, "content": "old chunk"}],
    )
    upsert_search_vector(
        conn,
        {
            "chunk_id": "Notes/excluded.md#0000",
            "embedding": b"[1,1,1]",
            "embedding_model": "old-model",
            "embedding_dim": 3,
        },
    )
    conn.commit()

    result = run_search_indexing(config, conn, indexed_at="2026-08-09T12:00:00Z")

    file_row = get_file(conn, "Notes/excluded.md")
    assert result.excluded_files == 1
    assert get_search_chunks_for_file(conn, "Notes/excluded.md") == []
    assert conn.execute("SELECT * FROM search_vec").fetchall() == []
    assert file_row["search_indexed_at"] == "2026-08-09T12:00:00Z"
    assert file_row["search_index_hash"] == "exclude-hash"


def test_orphan_cleanup_purges_missing_file_rows(tmp_path: Path, conn):
    vault = tmp_path / "vault"
    vault.mkdir()
    config = _config(vault, tmp_path)
    _seed_file(conn, vault, "Notes/orphaned.md", "alpha beta gamma", sha256="orphaned-hash")
    replace_search_chunks(
        conn,
        "Notes/orphaned.md",
        [{"chunk_id": "Notes/orphaned.md#0000", "chunk_index": 0, "content": "alpha beta gamma"}],
    )
    upsert_search_vector(
        conn,
        {
            "chunk_id": "Notes/orphaned.md#0000",
            "embedding": b"[1,2,3]",
            "embedding_model": "old-model",
            "embedding_dim": 3,
        },
    )
    mark_file_search_indexed(conn, "Notes/orphaned.md", "2026-08-01T00:00:00Z", "orphaned-hash")
    conn.commit()
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.execute("DELETE FROM file WHERE file_path = ?", ("Notes/orphaned.md",))
    conn.commit()
    conn.execute("PRAGMA foreign_keys = ON")

    result = run_search_indexing(config, conn, indexed_at="2026-08-09T13:00:00Z")

    assert result.orphaned_files_purged == 1
    assert get_search_chunks_for_file(conn, "Notes/orphaned.md") == []
    assert conn.execute("SELECT * FROM search_vec").fetchall() == []
    assert get_file(conn, "Notes/orphaned.md") is None