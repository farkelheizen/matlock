"""
Matlock database layer.

Provides a connection factory and all schema/CRUD helpers used by
pipeline stages.  Helpers do NOT commit — callers are responsible for
transaction management (``conn.commit()`` / ``conn.rollback()``).

Sentinel: ``"__UNKNOWN__"`` is used as the ``project_id`` value in
``daily_metric`` rows that represent tasks with no project assignment.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

UNKNOWN_PROJECT = "__UNKNOWN__"

# ---------------------------------------------------------------------------
# Connection factory
# ---------------------------------------------------------------------------

_PRAGMAS = """
PRAGMA journal_mode = WAL;
PRAGMA synchronous  = NORMAL;
PRAGMA busy_timeout = 5000;
PRAGMA foreign_keys = ON;
"""


def get_connection(db_path: str | Path) -> sqlite3.Connection:
    """Open (or create) a SQLite database and apply required PRAGMAs.

    Accepts a filesystem path or the special string ``":memory:"`` for an
    in-process, in-memory database (useful for isolated unit tests).

    Returns a ``sqlite3.Connection`` with ``row_factory = sqlite3.Row``
    so all result rows support column-name access.
    """
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.executescript(_PRAGMAS)
    return conn


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_SCHEMA = """
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
    needs_parsing INTEGER DEFAULT 1,
    has_secrets   INTEGER,
    secret_detection_error TEXT
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
    status           TEXT,
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
    project_id                 TEXT,
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

CREATE TABLE IF NOT EXISTS file_touch (
    file_path  TEXT NOT NULL,
    touch_date TEXT NOT NULL,
    event_type TEXT NOT NULL,
    modified   INTEGER,
    PRIMARY KEY (file_path, touch_date, event_type)
);

CREATE TABLE IF NOT EXISTS daily_task (
    task_id    TEXT NOT NULL,
    event_date TEXT NOT NULL,
    event_type TEXT NOT NULL,
    task_text  TEXT,
    file_path  TEXT,
    attributes TEXT,
    PRIMARY KEY (task_id, event_date, event_type)
);
"""


_SEARCH_SCHEMA = """
CREATE TABLE IF NOT EXISTS search_chunks (
    search_rowid INTEGER PRIMARY KEY AUTOINCREMENT,
    chunk_id     TEXT NOT NULL UNIQUE,
    file_id      TEXT NOT NULL REFERENCES file(file_path) ON DELETE CASCADE,
    chunk_index  INTEGER NOT NULL CHECK (chunk_index >= 0),
    content      TEXT NOT NULL,
    UNIQUE (file_id, chunk_index)
);

CREATE TABLE IF NOT EXISTS search_vec (
    chunk_id        TEXT PRIMARY KEY REFERENCES search_chunks(chunk_id) ON DELETE CASCADE,
    embedding       BLOB,
    embedding_model TEXT,
    embedding_dim   INTEGER
);

CREATE VIRTUAL TABLE IF NOT EXISTS search_fts USING fts5(
    content,
    content='search_chunks',
    content_rowid='search_rowid',
    tokenize='porter'
);

CREATE TRIGGER IF NOT EXISTS search_chunks_ai
AFTER INSERT ON search_chunks
BEGIN
    INSERT INTO search_fts(rowid, content)
    VALUES (new.search_rowid, new.content);
END;

CREATE TRIGGER IF NOT EXISTS search_chunks_ad
AFTER DELETE ON search_chunks
BEGIN
    INSERT INTO search_fts(search_fts, rowid, content)
    VALUES ('delete', old.search_rowid, old.content);
END;

CREATE TRIGGER IF NOT EXISTS search_chunks_au
AFTER UPDATE ON search_chunks
BEGIN
    INSERT INTO search_fts(search_fts, rowid, content)
    VALUES ('delete', old.search_rowid, old.content);
    INSERT INTO search_fts(rowid, content)
    VALUES (new.search_rowid, new.content);
END;

CREATE TRIGGER IF NOT EXISTS file_search_cleanup_on_soft_delete
AFTER UPDATE OF deleted, is_generated ON file
WHEN new.deleted = 1 OR new.is_generated = 1
BEGIN
    DELETE FROM search_chunks WHERE file_id = new.file_path;
    UPDATE file
       SET search_indexed_at = NULL,
           search_index_hash = NULL
     WHERE file_path = new.file_path;
END;

CREATE TRIGGER IF NOT EXISTS file_search_reset_on_hash_change
AFTER UPDATE OF sha256 ON file
WHEN new.sha256 IS NOT old.sha256
BEGIN
    UPDATE file
       SET search_indexed_at = NULL,
           search_index_hash = NULL
     WHERE file_path = new.file_path;
END;
"""


def init_db(conn: sqlite3.Connection) -> None:
    """Create all six tables idempotently and apply any pending migrations.

    Safe to call on a new or already-initialised database.
    """
    conn.executescript(_SCHEMA)
    _migrate(conn)


def _migrate(conn: sqlite3.Connection) -> None:
    """Apply additive schema migrations to existing databases.

    Each migration is a no-op if the column/index already exists.
    """
    existing_project_cols = {
        row[1]
        for row in conn.execute("PRAGMA table_info(project)").fetchall()
    }
    if "status" not in existing_project_cols:
        conn.execute("ALTER TABLE project ADD COLUMN status TEXT")

    existing_file_cols = {
        row[1]
        for row in conn.execute("PRAGMA table_info(file)").fetchall()
    }
    if "deleted_date" not in existing_file_cols:
        conn.execute("ALTER TABLE file ADD COLUMN deleted_date TEXT")
    if "search_indexed_at" not in existing_file_cols:
        conn.execute("ALTER TABLE file ADD COLUMN search_indexed_at TEXT")
    if "search_index_hash" not in existing_file_cols:
        conn.execute("ALTER TABLE file ADD COLUMN search_index_hash TEXT")
    if "has_secrets" not in existing_file_cols:
        conn.execute("ALTER TABLE file ADD COLUMN has_secrets INTEGER")
    if "secret_detection_error" not in existing_file_cols:
        conn.execute("ALTER TABLE file ADD COLUMN secret_detection_error TEXT")

    conn.executescript(_SEARCH_SCHEMA)

    conn.commit()


# ---------------------------------------------------------------------------
# file table helpers
# ---------------------------------------------------------------------------

_FILE_COLUMNS = (
    "file_path",
    "sha256",
    "file_ext",
    "created",
    "modified",
    "modified_date",
    "deleted",
    "length",
    "word_count",
    "meta_data",
    "is_generated",
    "needs_parsing",
    "has_secrets",
    "secret_detection_error",
)

_UPSERT_FILE = (
    "INSERT INTO file ("
    + ", ".join(_FILE_COLUMNS)
    + ") VALUES ("
    + ", ".join(f":{c}" for c in _FILE_COLUMNS)
    + ") ON CONFLICT(file_path) DO UPDATE SET "
    + ", ".join(
        f"{column} = excluded.{column}"
        for column in _FILE_COLUMNS
        if column not in {"file_path", "has_secrets", "secret_detection_error"}
    )
    + ", deleted_date = CASE"
    + " WHEN excluded.deleted = 0 THEN NULL"
    + " ELSE file.deleted_date END"
    + ", search_indexed_at = CASE"
    + " WHEN excluded.deleted = 1 OR excluded.is_generated = 1 THEN NULL"
    + " WHEN file.sha256 IS excluded.sha256 THEN file.search_indexed_at"
    + " ELSE NULL END"
    + ", search_index_hash = CASE"
    + " WHEN excluded.deleted = 1 OR excluded.is_generated = 1 THEN NULL"
    + " WHEN file.sha256 IS excluded.sha256 THEN file.search_index_hash"
    + " ELSE NULL END"
    + ", has_secrets = CASE"
    + " WHEN excluded.deleted = 1 OR excluded.is_generated = 1 THEN NULL"
    + " WHEN file.sha256 IS excluded.sha256 THEN file.has_secrets"
    + " ELSE NULL END"
    + ", secret_detection_error = CASE"
    + " WHEN excluded.deleted = 1 OR excluded.is_generated = 1 THEN NULL"
    + " WHEN file.sha256 IS excluded.sha256 THEN file.secret_detection_error"
    + " ELSE NULL END"
)


def upsert_file(conn: sqlite3.Connection, file_dict: dict) -> None:
    """Insert a file row or update it while preserving valid scan state."""
    values = dict(file_dict)
    values.setdefault("has_secrets", None)
    values.setdefault("secret_detection_error", None)
    conn.execute(_UPSERT_FILE, values)


def get_file(conn: sqlite3.Connection, file_path: str) -> sqlite3.Row | None:
    """Return the ``file`` row for *file_path*, or ``None`` if not found."""
    row = conn.execute(
        "SELECT * FROM file WHERE file_path = ?", (file_path,)
    ).fetchone()
    return row


def get_files_needing_parsing(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Return all non-deleted files with ``needs_parsing = 1``."""
    return conn.execute(
        "SELECT * FROM file WHERE needs_parsing = 1 AND deleted = 0"
    ).fetchall()


def mark_file_deleted(
    conn: sqlite3.Connection,
    file_path: str,
    deleted_date: str | None = None,
) -> None:
    """Set ``deleted = 1`` (and optionally ``deleted_date``) for *file_path*."""
    import datetime as _dt
    date_val = deleted_date or _dt.date.today().isoformat()
    conn.execute(
        "UPDATE file SET deleted = 1, deleted_date = ?, has_secrets = NULL,"
        " secret_detection_error = NULL WHERE file_path = ?",
        (date_val, file_path),
    )


def set_file_secret_detection(
    conn: sqlite3.Connection,
    file_path: str,
    has_secrets: bool,
    secret_detection_error: str | None = None,
) -> None:
    """Persist a completed document secret-detection outcome."""
    conn.execute(
        "UPDATE file SET has_secrets = ?, secret_detection_error = ?"
        " WHERE file_path = ?",
        (1 if has_secrets else 0, secret_detection_error, file_path),
    )


def set_needs_parsing(
    conn: sqlite3.Connection, file_path: str, value: int
) -> None:
    """Set ``needs_parsing`` to *value* (0 or 1) for *file_path*."""
    conn.execute(
        "UPDATE file SET needs_parsing = ? WHERE file_path = ?",
        (value, file_path),
    )


def get_files_needing_search_indexing(
    conn: sqlite3.Connection,
) -> list[sqlite3.Row]:
    """Return active, non-generated files whose search index is stale."""
    return conn.execute(
        "SELECT * FROM file"
        " WHERE deleted = 0"
        "   AND is_generated = 0"
        "   AND ("
        "       search_indexed_at IS NULL"
        "       OR search_index_hash IS NULL"
        "       OR sha256 IS NULL"
        "       OR search_index_hash != sha256"
        "   )"
        " ORDER BY file_path"
    ).fetchall()


def mark_file_search_indexed(
    conn: sqlite3.Connection,
    file_path: str,
    indexed_at: str,
    search_index_hash: str | None = None,
) -> None:
    """Persist the latest successful search-index timestamp and hash."""
    conn.execute(
        "UPDATE file"
        " SET search_indexed_at = ?,"
        "     search_index_hash = COALESCE(?, sha256)"
        " WHERE file_path = ?",
        (indexed_at, search_index_hash, file_path),
    )


def clear_file_search_state(
    conn: sqlite3.Connection, file_path: str
) -> None:
    """Remove search rows for a file and clear its freshness markers."""
    delete_search_chunks_for_file(conn, file_path)
    conn.execute(
        "UPDATE file"
        " SET search_indexed_at = NULL, search_index_hash = NULL"
        " WHERE file_path = ?",
        (file_path,),
    )


_SEARCH_CHUNK_COLUMNS = (
    "chunk_id",
    "file_id",
    "chunk_index",
    "content",
)

_INSERT_SEARCH_CHUNK = (
    "INSERT INTO search_chunks ("
    + ", ".join(_SEARCH_CHUNK_COLUMNS)
    + ") VALUES ("
    + ", ".join(f":{column}" for column in _SEARCH_CHUNK_COLUMNS)
    + ")"
)


def replace_search_chunks(
    conn: sqlite3.Connection, file_path: str, rows: list[dict]
) -> None:
    """Replace all search chunks for a file with the provided chunk rows."""
    delete_search_chunks_for_file(conn, file_path)
    if not rows:
        return

    conn.executemany(
        _INSERT_SEARCH_CHUNK,
        [
            {
                "chunk_id": row["chunk_id"],
                "file_id": file_path,
                "chunk_index": row["chunk_index"],
                "content": row["content"],
            }
            for row in rows
        ],
    )


def delete_search_chunks_for_file(
    conn: sqlite3.Connection, file_path: str
) -> None:
    """Delete all search chunks for a file."""
    conn.execute("DELETE FROM search_chunks WHERE file_id = ?", (file_path,))


def get_search_chunks_for_file(
    conn: sqlite3.Connection, file_path: str
) -> list[sqlite3.Row]:
    """Return all search chunks for a file in chunk order."""
    return conn.execute(
        "SELECT * FROM search_chunks WHERE file_id = ? ORDER BY chunk_index",
        (file_path,),
    ).fetchall()


_UPSERT_SEARCH_VECTOR = (
    "INSERT INTO search_vec (chunk_id, embedding, embedding_model, embedding_dim)"
    " VALUES (:chunk_id, :embedding, :embedding_model, :embedding_dim)"
    " ON CONFLICT(chunk_id) DO UPDATE SET"
    " embedding = excluded.embedding,"
    " embedding_model = excluded.embedding_model,"
    " embedding_dim = excluded.embedding_dim"
)


def upsert_search_vector(conn: sqlite3.Connection, row: dict) -> None:
    """Insert or replace the stored embedding payload for a chunk."""
    conn.execute(_UPSERT_SEARCH_VECTOR, row)


# ---------------------------------------------------------------------------
# task table helpers
# ---------------------------------------------------------------------------

_TASK_COLUMNS = (
    "task_id",
    "file_path",
    "parent_task_id",
    "created_date",
    "due_date",
    "est_comp_date",
    "act_comp_date",
    "checked",
    "task_text",
    "overflow",
    "headers",
    "attributes",
    "errors",
    "twin_index",
)

_UPSERT_TASK = (
    "INSERT OR REPLACE INTO task ("
    + ", ".join(_TASK_COLUMNS)
    + ") VALUES ("
    + ", ".join(f":{c}" for c in _TASK_COLUMNS)
    + ")"
)


def upsert_task(conn: sqlite3.Connection, task_dict: dict) -> None:
    """Insert or fully replace a row in the ``task`` table."""
    conn.execute(_UPSERT_TASK, task_dict)


def get_tasks_for_file(
    conn: sqlite3.Connection, file_path: str
) -> list[sqlite3.Row]:
    """Return all task rows whose ``file_path`` matches *file_path*."""
    return conn.execute(
        "SELECT * FROM task WHERE file_path = ?", (file_path,)
    ).fetchall()


def delete_tasks_for_file(conn: sqlite3.Connection, file_path: str) -> None:
    """Delete all tasks belonging to *file_path*."""
    conn.execute("DELETE FROM task WHERE file_path = ?", (file_path,))


# ---------------------------------------------------------------------------
# Project table helpers (delete-then-insert / replace-all)
# ---------------------------------------------------------------------------


def replace_super_projects(
    conn: sqlite3.Connection, rows: list[dict]
) -> None:
    """Replace the entire ``super_project`` table with *rows*.

    Deletes all existing rows, then inserts the provided list.
    Must be called before ``replace_projects`` to satisfy FK constraints.
    """
    conn.execute("DELETE FROM super_project")
    conn.executemany(
        "INSERT INTO super_project (super_project_id, title, priority)"
        " VALUES (:super_project_id, :title, :priority)",
        rows,
    )


def replace_projects(conn: sqlite3.Connection, rows: list[dict]) -> None:
    """Replace the entire ``project`` table with *rows*.

    Deletes all existing rows, then inserts the provided list.
    ``replace_super_projects`` must have been called first if any row
    references a ``super_project_id``.
    """
    conn.execute("DELETE FROM project")
    conn.executemany(
        "INSERT INTO project"
        " (project_id, super_project_id, title, home_file, priority, status, start_date, due_date)"
        " VALUES"
        " (:project_id, :super_project_id, :title, :home_file, :priority, :status, :start_date, :due_date)",
        rows,
    )


def replace_file_projects(
    conn: sqlite3.Connection, rows: list[dict]
) -> None:
    """Replace the entire ``file_project`` table with *rows*.

    Both ``file`` and ``project`` rows must already exist for all
    referenced ``file_path`` / ``project_id`` values.
    """
    conn.execute("DELETE FROM file_project")
    conn.executemany(
        "INSERT INTO file_project (file_path, project_id)"
        " VALUES (:file_path, :project_id)",
        rows,
    )


# ---------------------------------------------------------------------------
# daily_metric table helpers
# ---------------------------------------------------------------------------

_DAILY_METRIC_COLUMNS = (
    "metric_date",
    "project_id",
    "tasks_created_count",
    "tasks_created_minutes",
    "tasks_completed_count",
    "tasks_completed_minutes",
    "tasks_due_tomorrow_count",
    "tasks_due_tomorrow_minutes",
    "tasks_past_due_count",
    "tasks_past_due_minutes",
    "tasks_future_due_count",
    "tasks_future_due_minutes",
    "files_created_count",
    "files_modified_count",
    "files_deleted_count",
)

_UPSERT_DAILY_METRIC = (
    "INSERT OR REPLACE INTO daily_metric ("
    + ", ".join(_DAILY_METRIC_COLUMNS)
    + ") VALUES ("
    + ", ".join(f":{c}" for c in _DAILY_METRIC_COLUMNS)
    + ")"
)


def upsert_daily_metric(conn: sqlite3.Connection, row: dict) -> None:
    """Insert or replace a row in the ``daily_metric`` table.

    ``row["project_id"]`` may be ``None`` (stored as SQL NULL).
    Re-inserting the same ``(metric_date, project_id)`` pair replaces
    the existing row, making the rollup stage idempotent.

    SQLite treats two NULLs as distinct for uniqueness purposes, so
    ``INSERT OR REPLACE`` does not detect a conflict when
    ``project_id IS NULL``.  We handle this by explicitly deleting the
    existing NULL row before inserting, which is a no-op on the first run.
    """
    if row["project_id"] is None:
        conn.execute(
            "DELETE FROM daily_metric"
            " WHERE metric_date = :metric_date AND project_id IS NULL",
            {"metric_date": row["metric_date"]},
        )
    conn.execute(_UPSERT_DAILY_METRIC, row)


# ---------------------------------------------------------------------------
# file_touch table helpers
# ---------------------------------------------------------------------------

_FILE_TOUCH_COLUMNS = ("file_path", "touch_date", "event_type", "modified")

_UPSERT_FILE_TOUCH = (
    "INSERT OR REPLACE INTO file_touch ("
    + ", ".join(_FILE_TOUCH_COLUMNS)
    + ") VALUES ("
    + ", ".join(f":{c}" for c in _FILE_TOUCH_COLUMNS)
    + ")"
)


def upsert_file_touch(conn: sqlite3.Connection, row: dict) -> None:
    """Insert or replace a row in the ``file_touch`` table."""
    conn.execute(_UPSERT_FILE_TOUCH, row)


# ---------------------------------------------------------------------------
# daily_task table helpers
# ---------------------------------------------------------------------------

_DAILY_TASK_COLUMNS = (
    "task_id",
    "event_date",
    "event_type",
    "task_text",
    "file_path",
    "attributes",
)

_UPSERT_DAILY_TASK = (
    "INSERT OR REPLACE INTO daily_task ("
    + ", ".join(_DAILY_TASK_COLUMNS)
    + ") VALUES ("
    + ", ".join(f":{c}" for c in _DAILY_TASK_COLUMNS)
    + ")"
)


def upsert_daily_task(conn: sqlite3.Connection, row: dict) -> None:
    """Insert or replace a row in the ``daily_task`` table."""
    conn.execute(_UPSERT_DAILY_TASK, row)
