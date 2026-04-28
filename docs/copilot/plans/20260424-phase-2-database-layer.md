# 20260424-phase-2 Plan: Database Layer

> Date: 4/24/2026
> Owner: Copilot
> Branch: feat/phase-2-database-layer
> Related docs: `docs/matlock-data-model.md`, `docs/matlock-configuration.md`, `docs/roadmap/index.md`

## Problem Summary

No persistent storage exists yet. All subsequent pipeline stages (sync, parse, map-projects, rollup, report) read from and write exclusively to a SQLite database. Without this layer, no stage can be built or tested against real state.

The database must be initialized idempotently (safe to run `CREATE TABLE IF NOT EXISTS` on an already-configured DB), enforce WAL mode and foreign keys on every connection, and provide the CRUD helpers that stages will call rather than writing raw SQL inline.

## Goal

Implement `matlock/db.py`: a `get_connection()` factory that applies all required PRAGMAs, `init_db()` that creates all 6 tables idempotently, and per-table upsert/select helpers for the `file` and `task` tables (the only two tables written by Phases 3 and 4). Write comprehensive tests.

## Scope

- **In scope:**
  - `matlock/db.py` — `get_connection()`, `init_db()`, upsert/select helpers for `file` and `task` tables, bulk-insert helper for `super_project`, `project`, and `file_project` tables (needed by Phase 5 but trivial to provide now)
  - `tests/test_db.py` — full test coverage using `tmp_path` SQLite files
  - Update `matlock/__init__.py` to export `get_connection` and `init_db`

- **Out of scope:**
  - CLI integration — no `matlock` command reads the DB yet
  - `daily_metric` write helpers — deferred to Phase 6 (rollup)
  - Report-read queries — deferred to Phase 7
  - Migration tooling — the schema is append-only for now; no versioning needed at this stage

## Constraints / Requirements

- `get_connection(db_path)` accepts a `str | Path` and returns a `sqlite3.Connection` with row factory set to `sqlite3.Row` and all four PRAGMAs applied: `journal_mode=WAL`, `synchronous=NORMAL`, `busy_timeout=5000`, `foreign_keys=ON`.
- `init_db(conn)` creates all 6 tables using `CREATE TABLE IF NOT EXISTS` — safe to call on an existing database.
- Upsert helpers use `INSERT OR REPLACE` (full-row replacement) for `file`, `super_project`, `project`, and `file_project`. The `task` table uses `INSERT OR REPLACE` as well — tasks are always re-derived from source files.
- All helpers accept a `sqlite3.Connection` (not a path) — callers manage connection lifetime.
- No ORM, no SQLAlchemy, no third-party DB library.
- Foreign key constraints must be satisfied in test fixtures (insert `file` rows before `task` rows, etc.).

---

## Implementation Plan

| Step ID | Status | Goal | Planned Changes | Test Coverage |
|---|---|---|---|---|
| P2-S1 | Completed | `get_connection()` + PRAGMAs | `matlock/db.py` — connection factory with all 4 PRAGMAs | `test_db.py`: PRAGMA values verified on fresh connection |
| P2-S2 | Completed | `init_db()` — schema creation | `matlock/db.py` — `CREATE TABLE IF NOT EXISTS` for all 6 tables | `test_db.py`: all tables present after `init_db()`; idempotency (call twice) |
| P2-S3 | Completed | `file` table helpers | `matlock/db.py` — `upsert_file()`, `get_file()`, `get_files_needing_parsing()`, `mark_file_deleted()`, `set_needs_parsing()` | `test_db.py`: insert, update, retrieve, soft-delete, needs_parsing flag |
| P2-S4 | Completed | `task` table helpers | `matlock/db.py` — `upsert_task()`, `get_tasks_for_file()`, `delete_tasks_for_file()` | `test_db.py`: insert/replace, retrieve by file, bulk-delete |
| P2-S5 | Completed | Project table helpers | `matlock/db.py` — `replace_super_projects()`, `replace_projects()`, `replace_file_projects()` | `test_db.py`: full replace round-trip |
| P2-S6 | Completed | Exports + regression | `matlock/__init__.py` — export `get_connection`, `init_db` | Full suite: 132 passed |

Status values: `Not Started` | `In Progress` | `Completed` | `Blocked`

---

## Step Details

### P2-S1 — `get_connection()` and PRAGMAs

**Files (expected):**
- `matlock/db.py` *(new)*

**Implementation notes:**
- Signature: `get_connection(db_path: str | Path) -> sqlite3.Connection`
- Set `conn.row_factory = sqlite3.Row` so callers can access columns by name.
- Execute all 4 PRAGMAs immediately after opening; `journal_mode=WAL` returns the active mode as a result row — no action needed beyond executing it.
- Do **not** catch exceptions here; let them propagate so callers know the path is bad.

**Definition of done:**
- A test opens a connection to a `tmp_path` DB and verifies `PRAGMA journal_mode` returns `wal`, `PRAGMA foreign_keys` returns `1`, and `PRAGMA synchronous` returns `1` (NORMAL).

---

### P2-S2 — `init_db()` Schema Creation

**Files (expected):**
- `matlock/db.py`

**Schema to implement (all 6 tables from `docs/matlock-data-model.md`):**

```sql
CREATE TABLE IF NOT EXISTS file (
    file_path     TEXT PRIMARY KEY,
    sha256        TEXT,
    file_ext      TEXT,
    created       INTEGER,
    modified      INTEGER,
    modified_date TEXT,
    deleted       INTEGER DEFAULT 0,
    length        INTEGER,
    word_count    INTEGER,
    meta_data     TEXT,
    is_generated  INTEGER DEFAULT 0,
    needs_parsing INTEGER DEFAULT 1
);

CREATE TABLE IF NOT EXISTS task (
    task_id        TEXT PRIMARY KEY,
    file_path      TEXT REFERENCES file(file_path),
    parent_task_id TEXT,
    created_date   TEXT,
    due_date       TEXT,
    est_comp_date  TEXT,
    act_comp_date  TEXT,
    checked        INTEGER,
    task_text      TEXT,
    overflow       INTEGER,
    headers        TEXT,
    attributes     TEXT,
    errors         TEXT,
    twin_index     INTEGER
);

CREATE TABLE IF NOT EXISTS super_project (
    super_project_id TEXT PRIMARY KEY,
    title            TEXT,
    priority         TEXT
);

CREATE TABLE IF NOT EXISTS project (
    project_id       TEXT PRIMARY KEY,
    super_project_id TEXT REFERENCES super_project(super_project_id),
    title            TEXT,
    home_file        TEXT,
    priority         TEXT,
    start_date       TEXT,
    due_date         TEXT
);

CREATE TABLE IF NOT EXISTS file_project (
    file_path  TEXT REFERENCES file(file_path),
    project_id TEXT REFERENCES project(project_id),
    PRIMARY KEY (file_path, project_id)
);

CREATE TABLE IF NOT EXISTS daily_metric (
    metric_date                TEXT    NOT NULL,
    project_id                 TEXT    NOT NULL,  -- use "__UNKNOWN__" for unassigned tasks
    tasks_created_count        INTEGER,
    tasks_created_minutes      INTEGER,
    tasks_completed_count      INTEGER,
    tasks_completed_minutes    INTEGER,
    tasks_due_tomorrow_count   INTEGER,
    tasks_due_tomorrow_minutes INTEGER,
    tasks_past_due_count       INTEGER,
    tasks_past_due_minutes     INTEGER,
    tasks_future_due_count     INTEGER,
    tasks_future_due_minutes   INTEGER,
    files_created_count        INTEGER,
    files_modified_count       INTEGER,
    files_deleted_count        INTEGER,
    PRIMARY KEY (metric_date, project_id)
);
```

**Definition of done:**
- After `init_db(conn)`, querying `sqlite_master` returns all 6 table names.
- Calling `init_db(conn)` a second time on the same DB raises no error and leaves the schema unchanged.

---

### P2-S3 — `file` Table Helpers

**Files (expected):**
- `matlock/db.py`

**Functions:**

| Function | Signature | Notes |
|:---------|:----------|:------|
| `upsert_file` | `(conn, file_dict: dict) -> None` | `INSERT OR REPLACE INTO file` with all columns |
| `get_file` | `(conn, file_path: str) -> sqlite3.Row \| None` | Fetch single row by primary key |
| `get_files_needing_parsing` | `(conn) -> list[sqlite3.Row]` | `WHERE needs_parsing = 1 AND deleted = 0` |
| `mark_file_deleted` | `(conn, file_path: str) -> None` | `UPDATE file SET deleted = 1 WHERE file_path = ?` |
| `set_needs_parsing` | `(conn, file_path: str, value: int) -> None` | `UPDATE file SET needs_parsing = ? WHERE file_path = ?` |

**Implementation notes:**
- `upsert_file` accepts a plain dict with keys matching column names; caller is responsible for providing all required fields.
- These helpers do not commit — callers control transactions.

**Definition of done:**
- Insert a file row, verify `get_file()` returns it.
- Update the same row via `upsert_file()`, verify the change.
- `mark_file_deleted()` sets `deleted = 1`; row is excluded from `get_files_needing_parsing()`.
- `set_needs_parsing()` updates only the `needs_parsing` flag without touching other columns.

---

### P2-S4 — `task` Table Helpers

**Files (expected):**
- `matlock/db.py`

**Functions:**

| Function | Signature | Notes |
|:---------|:----------|:------|
| `upsert_task` | `(conn, task_dict: dict) -> None` | `INSERT OR REPLACE INTO task` |
| `get_tasks_for_file` | `(conn, file_path: str) -> list[sqlite3.Row]` | All tasks belonging to a file |
| `delete_tasks_for_file` | `(conn, file_path: str) -> None` | `DELETE FROM task WHERE file_path = ?` |

**Implementation notes:**
- JSON-serialisable fields (`headers`, `attributes`, `errors`) are stored as JSON strings; serialisation is the caller's responsibility.
- `delete_tasks_for_file` is called before re-parsing a file so stale tasks are removed.

**Definition of done:**
- Insert a `file` row, then two `task` rows for that file.
- `get_tasks_for_file()` returns both.
- After `delete_tasks_for_file()`, `get_tasks_for_file()` returns `[]`.

---

### P2-S5 — Project Table Helpers

**Files (expected):**
- `matlock/db.py`

**Functions:**

| Function | Signature | Notes |
|:---------|:----------|:------|
| `replace_super_projects` | `(conn, rows: list[dict]) -> None` | Delete-all then insert; called by `map-projects` stage |
| `replace_projects` | `(conn, rows: list[dict]) -> None` | Delete-all then insert |
| `replace_file_projects` | `(conn, rows: list[dict]) -> None` | Delete-all then insert |

**Implementation notes:**
- These use a delete-then-insert pattern (not `INSERT OR REPLACE`) because the full list from `config.yaml` is the authoritative set. Stale rows must be removed.
- `replace_super_projects` must run before `replace_projects` (FK dependency). Callers should sequence them correctly; these helpers do not enforce ordering between each other.
- `replace_file_projects` depends on both `file` and `project` rows existing.

**Definition of done:**
- Insert 2 super_projects, then call `replace_super_projects` with 1 row — verify only 1 remains.
- Same pattern for projects and file_projects.

---

### P2-S6 — Exports and Regression

**Files (expected):**
- `matlock/__init__.py`

**Implementation notes:**
- Add `from .db import get_connection, init_db` to the public API.
- Update `__all__` accordingly.

**Definition of done:**
- `from matlock import get_connection, init_db` works.
- `poetry run pytest` passes with no regressions (104+ tests).

---

## Acceptance Criteria

- `from matlock.db import get_connection, init_db` imports cleanly.
- `get_connection(path)` returns a `sqlite3.Connection` with all 4 PRAGMAs active.
- `init_db(conn)` is idempotent — safe to call on a new or existing DB.
- All 6 tables created correctly; foreign key constraints enforced (`foreign_keys=ON`).
- All helper functions tested in `tests/test_db.py`.
- Full suite passes with zero regressions.

## Risks / Notes

- **`daily_metric` PRIMARY KEY is composite `(metric_date, project_id)`**: `project_id` is nullable (NULL for unassigned tasks). SQLite treats `NULL != NULL` in unique constraints, which could allow duplicate `(date, NULL)` rows with a standard `UNIQUE` constraint. Using `PRIMARY KEY (metric_date, project_id)` with a NOT NULL constraint on `project_id` avoids this — but the spec allows NULL for unassigned tasks. This is a design question (see Q1 below).
- **`INSERT OR REPLACE` semantics**: This deletes the old row and inserts a new one, which means any columns not provided will revert to their defaults. All helpers must ensure all columns are passed.
- **Transaction management**: Helpers do not commit. The calling stage is responsible for wrapping operations in a transaction and calling `conn.commit()`. This needs to be documented clearly in the module docstring.

## Validation Plan

Run tests in this order:
1. Focused tests: `poetry run pytest tests/test_db.py -v`
2. Regression tests: `poetry run pytest tests/test_matlock_config.py -v`
3. Full suite: `poetry run pytest`

Record results:
- Focused: pass — 28 passed, 0 failures
- Regression: pass — 21 passed (`test_matlock_config.py`), 0 failures
- Full suite: pass — 132 passed, 0 failures, 1 deprecation warning (pytimeparse, unrelated)

---

## Design Decisions (Resolved)

**D1 — `daily_metric` unassigned bucket**: Use `"__UNKNOWN__"` as the sentinel string for tasks with no project. `project_id` is `NOT NULL` in the DDL. The rollup stage must use this sentinel consistently when writing unassigned-task metrics.

**D2 — `file_project` PRIMARY KEY**: Composite `PRIMARY KEY (file_path, project_id)` as a table-level constraint. Not two single-column PKs.

**D3 — `set_needs_parsing` helper**: Add a dedicated `set_needs_parsing(conn, file_path: str, value: int) -> None` helper alongside `upsert_file()`. The sync stage uses this to flip the flag without re-supplying all columns.

**D4 — `":memory:"` support**: `get_connection` accepts `":memory:"` as a valid path for lightweight in-process test isolation. Tests that need true connection-isolation use `tmp_path`.
