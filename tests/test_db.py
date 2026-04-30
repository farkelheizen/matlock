from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from matlock.db import (
    UNKNOWN_PROJECT,
    delete_tasks_for_file,
    get_connection,
    get_file,
    get_files_needing_parsing,
    get_tasks_for_file,
    init_db,
    mark_file_deleted,
    replace_file_projects,
    replace_projects,
    replace_super_projects,
    set_needs_parsing,
    upsert_file,
    upsert_task,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def conn():
    """In-memory DB, initialised and ready for use."""
    c = get_connection(":memory:")
    init_db(c)
    yield c
    c.close()


@pytest.fixture()
def file_conn(tmp_path: Path):
    """File-backed DB for tests that need connection-isolation."""
    db_path = tmp_path / "matlock.db"
    c = get_connection(db_path)
    init_db(c)
    yield c
    c.close()


def _file_row(file_path: str = "Notes/foo.md", **overrides) -> dict:
    base = {
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
        "needs_parsing": 1,
    }
    base.update(overrides)
    return base


def _task_row(task_id: str = "t1", file_path: str = "Notes/foo.md", **overrides) -> dict:
    base = {
        "task_id": task_id,
        "file_path": file_path,
        "parent_task_id": None,
        "created_date": None,
        "due_date": None,
        "est_comp_date": None,
        "act_comp_date": None,
        "checked": 0,
        "task_text": "Do the thing",
        "overflow": 0,
        "headers": "[]",
        "attributes": "{}",
        "errors": "[]",
        "twin_index": 0,
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# P2-S1 — get_connection() + PRAGMAs
# ---------------------------------------------------------------------------


def test_pragma_journal_mode_is_wal(file_conn):
    row = file_conn.execute("PRAGMA journal_mode").fetchone()
    assert row[0] == "wal"


def test_pragma_foreign_keys_on(file_conn):
    row = file_conn.execute("PRAGMA foreign_keys").fetchone()
    assert row[0] == 1


def test_pragma_synchronous_normal(file_conn):
    # NORMAL = 1
    row = file_conn.execute("PRAGMA synchronous").fetchone()
    assert row[0] == 1


def test_row_factory_is_sqlite_row(conn):
    upsert_file(conn, _file_row())
    conn.commit()
    row = get_file(conn, "Notes/foo.md")
    assert isinstance(row, sqlite3.Row)
    assert row["file_path"] == "Notes/foo.md"


def test_memory_connection_accepted():
    c = get_connection(":memory:")
    assert c is not None
    c.close()


# ---------------------------------------------------------------------------
# P2-S2 — init_db() schema
# ---------------------------------------------------------------------------


def test_all_six_tables_created(conn):
    tables = {
        row["name"]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    assert tables == {"file", "task", "super_project", "project", "file_project", "daily_metric"}


def test_init_db_idempotent(conn):
    # Calling init_db a second time must not raise
    init_db(conn)
    tables = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()
    assert len(tables) == 6


def test_daily_metric_project_id_nullable(conn):
    """project_id accepts NULL (used for the unassigned-tasks row by rollup stage)."""
    conn.execute(
        "INSERT INTO daily_metric (metric_date, project_id) VALUES (?, ?)",
        ("2026-01-01", None),
    )
    conn.commit()
    row = conn.execute(
        "SELECT * FROM daily_metric WHERE project_id IS NULL"
    ).fetchone()
    assert row is not None
    assert row["project_id"] is None


def test_daily_metric_unknown_sentinel_accepted(conn):
    conn.execute(
        "INSERT INTO daily_metric (metric_date, project_id) VALUES (?, ?)",
        ("2026-01-01", UNKNOWN_PROJECT),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM daily_metric").fetchone()
    assert row["project_id"] == UNKNOWN_PROJECT


def test_daily_metric_composite_pk_enforced(conn):
    conn.execute(
        "INSERT INTO daily_metric (metric_date, project_id) VALUES (?, ?)",
        ("2026-01-01", "proj1"),
    )
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO daily_metric (metric_date, project_id) VALUES (?, ?)",
            ("2026-01-01", "proj1"),
        )
        conn.commit()


# ---------------------------------------------------------------------------
# P2-S3 — file table helpers
# ---------------------------------------------------------------------------


def test_upsert_and_get_file(conn):
    upsert_file(conn, _file_row())
    conn.commit()
    row = get_file(conn, "Notes/foo.md")
    assert row is not None
    assert row["sha256"] == "abc123"


def test_upsert_file_updates_existing(conn):
    upsert_file(conn, _file_row())
    conn.commit()
    upsert_file(conn, _file_row(sha256="new_hash"))
    conn.commit()
    assert get_file(conn, "Notes/foo.md")["sha256"] == "new_hash"


def test_get_file_returns_none_for_missing(conn):
    assert get_file(conn, "nonexistent.md") is None


def test_get_files_needing_parsing(conn):
    upsert_file(conn, _file_row("a.md", needs_parsing=1))
    upsert_file(conn, _file_row("b.md", needs_parsing=0))
    upsert_file(conn, _file_row("c.md", needs_parsing=1, deleted=1))
    conn.commit()
    results = get_files_needing_parsing(conn)
    paths = {r["file_path"] for r in results}
    assert paths == {"a.md"}


def test_mark_file_deleted(conn):
    upsert_file(conn, _file_row())
    conn.commit()
    mark_file_deleted(conn, "Notes/foo.md")
    conn.commit()
    assert get_file(conn, "Notes/foo.md")["deleted"] == 1


def test_mark_file_deleted_excluded_from_needs_parsing(conn):
    upsert_file(conn, _file_row(needs_parsing=1))
    conn.commit()
    mark_file_deleted(conn, "Notes/foo.md")
    conn.commit()
    assert get_files_needing_parsing(conn) == []


def test_set_needs_parsing_to_zero(conn):
    upsert_file(conn, _file_row(needs_parsing=1))
    conn.commit()
    set_needs_parsing(conn, "Notes/foo.md", 0)
    conn.commit()
    assert get_file(conn, "Notes/foo.md")["needs_parsing"] == 0


def test_set_needs_parsing_does_not_touch_other_columns(conn):
    upsert_file(conn, _file_row(sha256="original"))
    conn.commit()
    set_needs_parsing(conn, "Notes/foo.md", 0)
    conn.commit()
    assert get_file(conn, "Notes/foo.md")["sha256"] == "original"


# ---------------------------------------------------------------------------
# P2-S4 — task table helpers
# ---------------------------------------------------------------------------


def test_upsert_and_get_tasks(conn):
    upsert_file(conn, _file_row())
    upsert_task(conn, _task_row("t1"))
    upsert_task(conn, _task_row("t2", task_text="Another task"))
    conn.commit()
    tasks = get_tasks_for_file(conn, "Notes/foo.md")
    assert len(tasks) == 2
    ids = {t["task_id"] for t in tasks}
    assert ids == {"t1", "t2"}


def test_upsert_task_replaces_existing(conn):
    upsert_file(conn, _file_row())
    upsert_task(conn, _task_row("t1", task_text="Original"))
    conn.commit()
    upsert_task(conn, _task_row("t1", task_text="Updated"))
    conn.commit()
    tasks = get_tasks_for_file(conn, "Notes/foo.md")
    assert len(tasks) == 1
    assert tasks[0]["task_text"] == "Updated"


def test_get_tasks_for_file_returns_empty_list(conn):
    upsert_file(conn, _file_row())
    conn.commit()
    assert get_tasks_for_file(conn, "Notes/foo.md") == []


def test_delete_tasks_for_file(conn):
    upsert_file(conn, _file_row())
    upsert_task(conn, _task_row("t1"))
    upsert_task(conn, _task_row("t2"))
    conn.commit()
    delete_tasks_for_file(conn, "Notes/foo.md")
    conn.commit()
    assert get_tasks_for_file(conn, "Notes/foo.md") == []


def test_delete_tasks_only_affects_target_file(conn):
    upsert_file(conn, _file_row("a.md"))
    upsert_file(conn, _file_row("b.md"))
    upsert_task(conn, _task_row("t1", file_path="a.md"))
    upsert_task(conn, _task_row("t2", file_path="b.md"))
    conn.commit()
    delete_tasks_for_file(conn, "a.md")
    conn.commit()
    assert get_tasks_for_file(conn, "a.md") == []
    assert len(get_tasks_for_file(conn, "b.md")) == 1


def test_task_fk_rejects_unknown_file(conn):
    with pytest.raises(sqlite3.IntegrityError):
        upsert_task(conn, _task_row("t1", file_path="nonexistent.md"))
        conn.commit()


# ---------------------------------------------------------------------------
# P2-S5 — project table helpers
# ---------------------------------------------------------------------------


def _sp(id_: str, title: str = "Title", priority: str = "high") -> dict:
    return {"super_project_id": id_, "title": title, "priority": priority}


def _proj(id_: str, sp_id: str | None = None) -> dict:
    return {
        "project_id": id_,
        "super_project_id": sp_id,
        "title": f"Project {id_}",
        "home_file": None,
        "priority": None,
        "status": None,
        "start_date": None,
        "due_date": None,
    }


def test_replace_super_projects(conn):
    replace_super_projects(conn, [_sp("sp1"), _sp("sp2")])
    conn.commit()
    rows = conn.execute("SELECT * FROM super_project").fetchall()
    assert len(rows) == 2

    replace_super_projects(conn, [_sp("sp1")])
    conn.commit()
    rows = conn.execute("SELECT * FROM super_project").fetchall()
    assert len(rows) == 1
    assert rows[0]["super_project_id"] == "sp1"


def test_replace_projects(conn):
    replace_super_projects(conn, [_sp("sp1")])
    replace_projects(conn, [_proj("p1", "sp1"), _proj("p2", "sp1")])
    conn.commit()
    assert len(conn.execute("SELECT * FROM project").fetchall()) == 2

    replace_projects(conn, [_proj("p1", "sp1")])
    conn.commit()
    rows = conn.execute("SELECT * FROM project").fetchall()
    assert len(rows) == 1
    assert rows[0]["project_id"] == "p1"


def test_replace_file_projects(conn):
    upsert_file(conn, _file_row("a.md"))
    upsert_file(conn, _file_row("b.md"))
    replace_super_projects(conn, [_sp("sp1")])
    replace_projects(conn, [_proj("p1", "sp1"), _proj("p2", "sp1")])
    conn.commit()

    replace_file_projects(conn, [
        {"file_path": "a.md", "project_id": "p1"},
        {"file_path": "b.md", "project_id": "p1"},
    ])
    conn.commit()
    assert len(conn.execute("SELECT * FROM file_project").fetchall()) == 2

    replace_file_projects(conn, [{"file_path": "a.md", "project_id": "p1"}])
    conn.commit()
    rows = conn.execute("SELECT * FROM file_project").fetchall()
    assert len(rows) == 1
    assert rows[0]["file_path"] == "a.md"


def test_replace_super_projects_empty_list(conn):
    replace_super_projects(conn, [_sp("sp1")])
    conn.commit()
    replace_super_projects(conn, [])
    conn.commit()
    assert conn.execute("SELECT * FROM super_project").fetchall() == []
