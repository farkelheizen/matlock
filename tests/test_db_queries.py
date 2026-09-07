from __future__ import annotations

import sqlite3

from matlock.db import (
    fetch_active_tasks,
    fetch_projects,
    fetch_super_projects,
    get_connection,
    init_db,
)


def _insert_task(conn: sqlite3.Connection, *, task_id: str, file_path: str, task_text: str, checked: int = 0, due_date: str | None = None, est_comp_date: str | None = None, act_comp_date: str | None = None, headers: str = '[]', attributes: str = '{}', errors: str = '[]', overflow: int = 0) -> None:
    conn.execute(
        "INSERT INTO task (task_id, file_path, parent_task_id, created_date, due_date, est_comp_date, act_comp_date, checked, task_text, overflow, headers, attributes, errors, twin_index) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (task_id, file_path, None, "2026-01-01", due_date, est_comp_date, act_comp_date, checked, task_text, overflow, headers, attributes, errors, 0),
    )


def test_fetch_projects_returns_ordered_rows_and_literal_like_matches() -> None:
    conn = get_connection(":memory:")
    init_db(conn)
    conn.execute("INSERT INTO project (project_id, super_project_id, title, home_file, priority, status, start_date, due_date) VALUES (?, ?, ?, ?, ?, ?, ?, ?)", ("proj-b", None, "Roadmap: 100%", "notes/b.md", "P2", "Active", "2026-01-01", None))
    conn.execute("INSERT INTO project (project_id, super_project_id, title, home_file, priority, status, start_date, due_date) VALUES (?, ?, ?, ?, ?, ?, ?, ?)", ("proj-a", None, "Alpha", "notes/a.md", "P1", "Active", "2026-01-02", "2026-03-02"))
    conn.commit()

    projects = fetch_projects(conn)
    assert [project.project_id for project in projects] == ["proj-a", "proj-b"]

    filtered = fetch_projects(conn, term="100%")
    assert [project.project_id for project in filtered] == ["proj-b"]

    filtered = fetch_projects(conn, term="alpha")
    assert [project.project_id for project in filtered] == ["proj-a"]

    conn.close()


def test_fetch_super_projects_returns_all_rows_sorted() -> None:
    conn = get_connection(":memory:")
    init_db(conn)
    conn.execute("INSERT INTO super_project (super_project_id, title, priority) VALUES (?, ?, ?)", ("sp-b", "Beta", "P2"))
    conn.execute("INSERT INTO super_project (super_project_id, title, priority) VALUES (?, ?, ?)", ("sp-a", "Alpha", "P1"))
    conn.commit()

    rows = fetch_super_projects(conn)
    assert [row.super_project_id for row in rows] == ["sp-a", "sp-b"]

    conn.close()


def test_fetch_active_tasks_filters_deleted_and_generated_files_and_decodes_json() -> None:
    conn = get_connection(":memory:")
    init_db(conn)

    conn.execute("INSERT INTO file (file_path, sha256, file_ext, created, modified, modified_date, deleted, length, word_count, meta_data, is_generated, needs_parsing) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", ("notes/live.md", "hash-1", ".md", 1, 1, "2026-01-01", 0, 1, 1, "{}", 0, 0))
    conn.execute("INSERT INTO file (file_path, sha256, file_ext, created, modified, modified_date, deleted, length, word_count, meta_data, is_generated, needs_parsing) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", ("notes/deleted.md", "hash-2", ".md", 1, 1, "2026-01-01", 1, 1, 1, "{}", 0, 0))
    conn.execute("INSERT INTO file (file_path, sha256, file_ext, created, modified, modified_date, deleted, length, word_count, meta_data, is_generated, needs_parsing) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", ("notes/generated.md", "hash-3", ".md", 1, 1, "2026-01-01", 0, 1, 1, "{}", 1, 0))
    conn.execute("INSERT INTO project (project_id, super_project_id, title, home_file, priority, status, start_date, due_date) VALUES (?, ?, ?, ?, ?, ?, ?, ?)", ("p-1", None, "Alpha", "notes/live.md", "P1", "Active", "2026-01-01", None))
    conn.execute("INSERT INTO project (project_id, super_project_id, title, home_file, priority, status, start_date, due_date) VALUES (?, ?, ?, ?, ?, ?, ?, ?)", ("p-2", None, "Beta", "notes/other.md", "P2", "Active", "2026-01-01", None))
    conn.execute("INSERT INTO file_project (file_path, project_id) VALUES (?, ?)", ("notes/live.md", "p-1"))
    conn.execute("INSERT INTO file_project (file_path, project_id) VALUES (?, ?)", ("notes/live.md", "p-2"))

    _insert_task(conn, task_id="t-1", file_path="notes/live.md", task_text="Alpha task", checked=1, due_date="2026-03-02", headers='["one"]', attributes='{"owner": "ops"}', errors='["warn"]', overflow=1)
    _insert_task(conn, task_id="t-2", file_path="notes/live.md", task_text="Beta task", checked=0, due_date="2026-03-04")
    _insert_task(conn, task_id="t-3", file_path="notes/deleted.md", task_text="Hidden", checked=0)
    _insert_task(conn, task_id="t-4", file_path="notes/generated.md", task_text="Hidden too", checked=0)
    conn.commit()

    rows = fetch_active_tasks(conn)
    assert [row.task_id for row in rows] == ["t-1", "t-2"]
    assert rows[0].headers == ["one"]
    assert rows[0].attributes == {"owner": "ops"}
    assert rows[0].errors == ["warn"]
    assert rows[0].project_ids == ["p-1", "p-2"]
    assert rows[0].checked is True
    assert rows[0].overflow is True

    filtered = fetch_active_tasks(conn, filters={
        "due_date": [{"field": "due_date", "operator": ">=", "value": "2026-03-01"}],
        "checked": True,
        "task_text": "alpha",
        "project_ids": ["p-1"],
    })
    assert [row.task_id for row in filtered] == ["t-1"]

    conn.close()


def test_fetch_active_tasks_applies_duplicate_and_date_filters() -> None:
    conn = get_connection(":memory:")
    init_db(conn)

    conn.execute("INSERT INTO file (file_path, sha256, file_ext, created, modified, modified_date, deleted, length, word_count, meta_data, is_generated, needs_parsing) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", ("notes/a.md", "hash-a", ".md", 1, 1, "2026-01-01", 0, 1, 1, "{}", 0, 0))
    conn.execute("INSERT INTO file (file_path, sha256, file_ext, created, modified, modified_date, deleted, length, word_count, meta_data, is_generated, needs_parsing) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", ("notes/b.md", "hash-b", ".md", 1, 1, "2026-01-01", 0, 1, 1, "{}", 0, 0))
    conn.execute("INSERT INTO project (project_id, super_project_id, title, home_file, priority, status, start_date, due_date) VALUES (?, ?, ?, ?, ?, ?, ?, ?)", ("p-1", None, "Alpha", "notes/a.md", "P1", "Active", "2026-01-01", None))
    conn.execute("INSERT INTO project (project_id, super_project_id, title, home_file, priority, status, start_date, due_date) VALUES (?, ?, ?, ?, ?, ?, ?, ?)", ("p-2", None, "Beta", "notes/b.md", "P2", "Active", "2026-01-01", None))
    conn.execute("INSERT INTO file_project (file_path, project_id) VALUES (?, ?)", ("notes/a.md", "p-1"))
    conn.execute("INSERT INTO file_project (file_path, project_id) VALUES (?, ?)", ("notes/a.md", "p-2"))
    conn.execute("INSERT INTO file_project (file_path, project_id) VALUES (?, ?)", ("notes/b.md", "p-2"))

    _insert_task(conn, task_id="t-1", file_path="notes/a.md", task_text="Alpha task", checked=1, due_date="2026-03-02", headers='["ops"]', attributes='{"owner": "ops"}')
    _insert_task(conn, task_id="t-2", file_path="notes/b.md", task_text="Beta task", checked=0, due_date="2026-03-04", headers='["ops"]', attributes='{"owner": "ops"}')
    conn.commit()

    rows = fetch_active_tasks(conn, filters={
        "due_date": [{"field": "due_date", "operator": ">=", "value": "2026-03-03"}, {"field": "due_date", "operator": "<", "value": "2026-03-05"}],
        "project_ids": ["p-2"],
    })
    assert [row.task_id for row in rows] == ["t-2"]

    conn.close()
