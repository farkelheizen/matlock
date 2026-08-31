from __future__ import annotations

from pathlib import Path

import sqlite3

import pytest

from matlock.db import (
    clear_file_search_state,
    get_connection,
    get_file,
    get_files_needing_search_indexing,
    get_search_chunks_for_file,
    init_db,
    mark_file_deleted,
    mark_file_search_indexed,
    replace_search_chunks,
    upsert_file,
    upsert_search_vector,
)


@pytest.fixture()
def conn():
    db = get_connection(":memory:")
    init_db(db)
    yield db
    db.close()


def _file_row(file_path: str = "Notes/foo.md", **overrides) -> dict:
    row = {
        "file_path": file_path,
        "sha256": "abc123",
        "file_ext": ".md",
        "created": 1000,
        "modified": 2000,
        "modified_date": "2026-01-01",
        "deleted": 0,
        "length": 512,
        "word_count": 80,
        "meta_data": "{}",
        "is_generated": 0,
        "needs_parsing": 0,
    }
    row.update(overrides)
    return row


def _legacy_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE file (
            file_path TEXT PRIMARY KEY,
            sha256 TEXT,
            file_ext TEXT,
            created INTEGER,
            modified INTEGER,
            modified_date TEXT,
            deleted INTEGER DEFAULT 0,
            length INTEGER,
            word_count INTEGER,
            meta_data TEXT,
            is_generated INTEGER DEFAULT 0,
            needs_parsing INTEGER DEFAULT 1
        );

        CREATE TABLE task (
            task_id TEXT PRIMARY KEY,
            file_path TEXT REFERENCES file(file_path),
            parent_task_id TEXT,
            created_date TEXT,
            due_date TEXT,
            est_comp_date TEXT,
            act_comp_date TEXT,
            checked INTEGER,
            task_text TEXT,
            overflow INTEGER,
            headers TEXT,
            attributes TEXT,
            errors TEXT,
            twin_index INTEGER
        );

        CREATE TABLE super_project (
            super_project_id TEXT PRIMARY KEY,
            title TEXT,
            priority TEXT
        );

        CREATE TABLE project (
            project_id TEXT PRIMARY KEY,
            super_project_id TEXT REFERENCES super_project(super_project_id),
            title TEXT,
            home_file TEXT,
            priority TEXT,
            start_date TEXT,
            due_date TEXT
        );

        CREATE TABLE file_project (
            file_path TEXT REFERENCES file(file_path),
            project_id TEXT REFERENCES project(project_id),
            PRIMARY KEY (file_path, project_id)
        );

        CREATE TABLE daily_metric (
            metric_date TEXT NOT NULL,
            project_id TEXT,
            tasks_created_count INTEGER,
            tasks_created_minutes INTEGER,
            tasks_completed_count INTEGER,
            tasks_completed_minutes INTEGER,
            tasks_due_tomorrow_count INTEGER,
            tasks_due_tomorrow_minutes INTEGER,
            tasks_past_due_count INTEGER,
            tasks_past_due_minutes INTEGER,
            tasks_future_due_count INTEGER,
            tasks_future_due_minutes INTEGER,
            files_created_count INTEGER,
            files_modified_count INTEGER,
            files_deleted_count INTEGER,
            PRIMARY KEY (metric_date, project_id)
        );

        CREATE TABLE file_touch (
            file_path TEXT NOT NULL,
            touch_date TEXT NOT NULL,
            event_type TEXT NOT NULL,
            modified INTEGER,
            PRIMARY KEY (file_path, touch_date, event_type)
        );

        CREATE TABLE daily_task (
            task_id TEXT NOT NULL,
            event_date TEXT NOT NULL,
            event_type TEXT NOT NULL,
            task_text TEXT,
            file_path TEXT,
            attributes TEXT,
            PRIMARY KEY (task_id, event_date, event_type)
        );
        """
    )


def test_init_db_creates_search_schema(conn):
    file_columns = {
        row[1] for row in conn.execute("PRAGMA table_info(file)").fetchall()
    }
    assert {
        "search_indexed_at",
        "search_index_hash",
        "has_secrets",
        "secret_detection_error",
    }.issubset(file_columns)

    tables = {
        row["name"]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    }
    assert {"search_chunks", "search_fts", "search_vec"}.issubset(tables)

    triggers = {
        row["name"]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'trigger'"
        ).fetchall()
    }
    assert {
        "search_chunks_ai",
        "search_chunks_ad",
        "search_chunks_au",
        "file_search_cleanup_on_soft_delete",
        "file_search_reset_on_hash_change",
    }.issubset(triggers)


def test_init_db_migrates_legacy_database(tmp_path: Path):
    db_path = tmp_path / "legacy.db"
    conn = get_connection(db_path)
    _legacy_schema(conn)
    conn.commit()

    init_db(conn)

    file_columns = {
        row[1] for row in conn.execute("PRAGMA table_info(file)").fetchall()
    }
    project_columns = {
        row[1] for row in conn.execute("PRAGMA table_info(project)").fetchall()
    }
    tables = {
        row["name"]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    }

    assert {
        "deleted_date",
        "search_indexed_at",
        "search_index_hash",
        "has_secrets",
        "secret_detection_error",
    }.issubset(file_columns)
    assert "status" in project_columns
    assert {"search_chunks", "search_fts", "search_vec"}.issubset(tables)
    conn.close()


def test_get_files_needing_search_indexing_filters_by_freshness(conn):
    upsert_file(conn, _file_row("stale-null.md"))
    upsert_file(conn, _file_row("stale-hash.md"))
    upsert_file(conn, _file_row("fresh.md"))
    upsert_file(conn, _file_row("deleted.md", deleted=1))
    upsert_file(conn, _file_row("generated.md", is_generated=1))

    mark_file_search_indexed(conn, "stale-hash.md", "2026-01-02T00:00:00Z", "old-hash")
    mark_file_search_indexed(conn, "fresh.md", "2026-01-02T00:00:00Z")
    conn.commit()

    paths = {
        row["file_path"] for row in get_files_needing_search_indexing(conn)
    }
    assert paths == {"stale-null.md", "stale-hash.md"}


def test_replace_search_chunks_keeps_fts_in_sync(conn):
    upsert_file(conn, _file_row())
    replace_search_chunks(
        conn,
        "Notes/foo.md",
        [
            {
                "chunk_id": "Notes/foo.md#0",
                "chunk_index": 0,
                "content": "alpha beta",
            },
            {
                "chunk_id": "Notes/foo.md#1",
                "chunk_index": 1,
                "content": "delta epsilon",
            },
        ],
    )
    conn.commit()

    alpha_hits = conn.execute(
        "SELECT sc.chunk_id"
        " FROM search_fts sf"
        " JOIN search_chunks sc ON sc.search_rowid = sf.rowid"
        " WHERE search_fts MATCH ?",
        ("alpha",),
    ).fetchall()
    assert [row["chunk_id"] for row in alpha_hits] == ["Notes/foo.md#0"]

    replace_search_chunks(
        conn,
        "Notes/foo.md",
        [
            {
                "chunk_id": "Notes/foo.md#0",
                "chunk_index": 0,
                "content": "gamma theta",
            }
        ],
    )
    conn.commit()

    alpha_hits = conn.execute(
        "SELECT sc.chunk_id"
        " FROM search_fts sf"
        " JOIN search_chunks sc ON sc.search_rowid = sf.rowid"
        " WHERE search_fts MATCH ?",
        ("alpha",),
    ).fetchall()
    gamma_hits = conn.execute(
        "SELECT sc.chunk_id"
        " FROM search_fts sf"
        " JOIN search_chunks sc ON sc.search_rowid = sf.rowid"
        " WHERE search_fts MATCH ?",
        ("gamma",),
    ).fetchall()

    assert alpha_hits == []
    assert [row["chunk_id"] for row in gamma_hits] == ["Notes/foo.md#0"]


def test_clear_file_search_state_removes_chunks_vectors_and_freshness(conn):
    upsert_file(conn, _file_row())
    replace_search_chunks(
        conn,
        "Notes/foo.md",
        [{"chunk_id": "Notes/foo.md#0", "chunk_index": 0, "content": "alpha"}],
    )
    upsert_search_vector(
        conn,
        {
            "chunk_id": "Notes/foo.md#0",
            "embedding": b"abc",
            "embedding_model": "mini",
            "embedding_dim": 3,
        },
    )
    mark_file_search_indexed(conn, "Notes/foo.md", "2026-01-02T00:00:00Z")
    conn.commit()

    clear_file_search_state(conn, "Notes/foo.md")
    conn.commit()

    row = get_file(conn, "Notes/foo.md")
    assert row["search_indexed_at"] is None
    assert row["search_index_hash"] is None
    assert get_search_chunks_for_file(conn, "Notes/foo.md") == []
    assert conn.execute("SELECT * FROM search_vec").fetchall() == []


def test_mark_file_deleted_cleans_search_rows(conn):
    upsert_file(conn, _file_row())
    replace_search_chunks(
        conn,
        "Notes/foo.md",
        [{"chunk_id": "Notes/foo.md#0", "chunk_index": 0, "content": "alpha"}],
    )
    upsert_search_vector(
        conn,
        {
            "chunk_id": "Notes/foo.md#0",
            "embedding": b"abc",
            "embedding_model": "mini",
            "embedding_dim": 3,
        },
    )
    mark_file_search_indexed(conn, "Notes/foo.md", "2026-01-02T00:00:00Z")
    conn.commit()

    mark_file_deleted(conn, "Notes/foo.md", deleted_date="2026-01-03")
    conn.commit()

    row = get_file(conn, "Notes/foo.md")
    alpha_hits = conn.execute(
        "SELECT rowid FROM search_fts WHERE search_fts MATCH ?",
        ("alpha",),
    ).fetchall()

    assert row["deleted"] == 1
    assert row["search_indexed_at"] is None
    assert row["search_index_hash"] is None
    assert get_search_chunks_for_file(conn, "Notes/foo.md") == []
    assert conn.execute("SELECT * FROM search_vec").fetchall() == []
    assert alpha_hits == []


def test_upsert_file_clears_search_freshness_when_hash_changes(conn):
    upsert_file(conn, _file_row())
    mark_file_search_indexed(conn, "Notes/foo.md", "2026-01-02T00:00:00Z")
    conn.commit()

    upsert_file(conn, _file_row(sha256="def456", modified=3000))
    conn.commit()

    row = get_file(conn, "Notes/foo.md")
    assert row["search_indexed_at"] is None
    assert row["search_index_hash"] is None