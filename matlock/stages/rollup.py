"""Stage IV — Rollup (The Chronicler).

Calculates historical metrics for a given date and upserts one
``daily_metric`` row per project, plus one NULL-project row for
tasks and files that belong to no project.

Design decisions
----------------
D1  ``estimate`` attribute (seconds) is converted to minutes Python-side
    by summing ``json.loads(attributes).get("estimate", 0) // 60`` for
    each matching task row.
D2  "Unassigned" files are those whose ``file_path`` does not appear in
    ``file_project`` at all.  Tasks are classified by their file's
    project membership.
D3  File activity counts are scoped per project (files linked via
    ``file_project``).  The NULL row covers files with no project link.
D4  ``files_created_count`` derives the creation date from the
    ``created`` epoch-millisecond column via SQLite's
    ``date(created / 1000, 'unixepoch')``.
D5  ``upsert_daily_metric`` lives in ``db.py`` (consistent with all
    prior DB helpers).
"""

from __future__ import annotations

import dataclasses
import datetime
import json
import sqlite3
from collections import defaultdict

from matlock.config import MatlockConfig
from matlock.db import upsert_daily_metric, upsert_daily_task, upsert_file_touch


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class RollupResult:
    """Summary of a single ``run_rollup`` call."""

    rollup_date: str      # YYYY-MM-DD
    rows_written: int = 0


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _date_to_str(d: datetime.date) -> str:
    return d.strftime("%Y-%m-%d")


def _estimate_minutes(attributes_json: str | None) -> int:
    """Return the ``estimate`` attribute (seconds) converted to minutes.

    Returns ``0`` if the key is absent, None, or ``attributes_json`` is None.
    """
    if not attributes_json:
        return 0
    try:
        attrs = json.loads(attributes_json)
    except (json.JSONDecodeError, TypeError):
        return 0
    value = attrs.get("estimate")
    if not value:
        return 0
    return int(value) // 60


def _sum_minutes(rows: list[sqlite3.Row]) -> int:
    """Sum ``_estimate_minutes`` across a list of task rows."""
    return sum(_estimate_minutes(row["attributes"]) for row in rows)


def _task_rows(
    conn: sqlite3.Connection,
    file_paths: list[str],
    where_clause: str,
    params: dict,
) -> list[sqlite3.Row]:
    """Fetch task rows matching *where_clause* scoped to *file_paths*.

    Returns an empty list immediately when *file_paths* is empty to avoid
    an invalid ``IN ()`` SQL clause.
    """
    if not file_paths:
        return []
    placeholders = ",".join("?" * len(file_paths))
    sql = (
        f"SELECT attributes FROM task "
        f"WHERE file_path IN ({placeholders}) AND {where_clause}"
    )
    positional = list(file_paths) + [params[k] for k in sorted(params)]
    # Build positional args in the order the WHERE clause references them.
    # Use named params approach instead to keep clarity:
    named_placeholders = ",".join(f":fp{i}" for i in range(len(file_paths)))
    named_params = {f"fp{i}": fp for i, fp in enumerate(file_paths)}
    named_params.update(params)
    sql_named = (
        f"SELECT attributes FROM task "
        f"WHERE file_path IN ({named_placeholders}) AND {where_clause}"
    )
    return conn.execute(sql_named, named_params).fetchall()


def _file_count(
    conn: sqlite3.Connection,
    file_paths: list[str],
    where_clause: str,
    params: dict,
) -> int:
    """COUNT(*) on the file table scoped to *file_paths*."""
    if not file_paths:
        return 0
    named_placeholders = ",".join(f":fp{i}" for i in range(len(file_paths)))
    named_params = {f"fp{i}": fp for i, fp in enumerate(file_paths)}
    named_params.update(params)
    sql = (
        f"SELECT COUNT(*) FROM file "
        f"WHERE file_path IN ({named_placeholders}) AND {where_clause}"
    )
    return conn.execute(sql, named_params).fetchone()[0]


def _calc_metrics(
    conn: sqlite3.Connection,
    rollup_date_str: str,
    tomorrow_str: str,
    project_id: str | None,
    file_paths: list[str],
) -> dict:
    """Build a ``daily_metric`` row dict for *project_id* and *file_paths*."""

    params_date = {"d": rollup_date_str}
    params_tomorrow = {"d": tomorrow_str}

    # ---- task metrics -------------------------------------------------------

    created_rows = _task_rows(conn, file_paths, "created_date = :d", params_date)
    completed_rows = _task_rows(
        conn, file_paths, "act_comp_date = :d AND checked = 1", params_date
    )
    due_tomorrow_rows = _task_rows(
        conn, file_paths, "due_date = :d AND checked = 0", params_tomorrow
    )
    past_due_rows = _task_rows(
        conn, file_paths, "due_date < :d AND checked = 0", params_date
    )
    future_due_rows = _task_rows(
        conn, file_paths, "due_date > :d AND checked = 0", params_date
    )

    # ---- file metrics -------------------------------------------------------

    files_created = _file_count(
        conn,
        file_paths,
        "date(created / 1000, 'unixepoch') = :d AND is_generated = 0",
        params_date,
    )
    files_modified = _file_count(
        conn,
        file_paths,
        "modified_date = :d AND deleted = 0 AND is_generated = 0",
        params_date,
    )
    files_deleted = _file_count(
        conn,
        file_paths,
        "deleted = 1 AND modified_date = :d AND is_generated = 0",
        params_date,
    )

    return {
        "metric_date": rollup_date_str,
        "project_id": project_id,
        "tasks_created_count": len(created_rows),
        "tasks_created_minutes": _sum_minutes(created_rows),
        "tasks_completed_count": len(completed_rows),
        "tasks_completed_minutes": _sum_minutes(completed_rows),
        "tasks_due_tomorrow_count": len(due_tomorrow_rows),
        "tasks_due_tomorrow_minutes": _sum_minutes(due_tomorrow_rows),
        "tasks_past_due_count": len(past_due_rows),
        "tasks_past_due_minutes": _sum_minutes(past_due_rows),
        "tasks_future_due_count": len(future_due_rows),
        "tasks_future_due_minutes": _sum_minutes(future_due_rows),
        "files_created_count": files_created,
        "files_modified_count": files_modified,
        "files_deleted_count": files_deleted,
    }


# ---------------------------------------------------------------------------
# Snapshot helpers (file_touch, daily_task)
# ---------------------------------------------------------------------------


def _populate_file_touch(
    conn: sqlite3.Connection,
    rollup_date_str: str,
) -> None:
    """Snapshot file events for *rollup_date_str* into ``file_touch``.

    Three event types:
    - ``created``  — files whose creation date (derived from ``created`` epoch) equals the rollup date
    - ``modified`` — non-deleted, non-generated files with ``modified_date`` equal to rollup date
    - ``deleted``  — files marked deleted with ``deleted_date`` equal to rollup date

    Idempotent: ``INSERT OR REPLACE`` via ``upsert_file_touch``.
    """
    # Created
    for row in conn.execute(
        "SELECT file_path, modified FROM file"
        " WHERE date(created / 1000, 'unixepoch') = ? AND is_generated = 0",
        (rollup_date_str,),
    ).fetchall():
        upsert_file_touch(conn, {
            "file_path": row["file_path"],
            "touch_date": rollup_date_str,
            "event_type": "created",
            "modified": row["modified"],
        })

    # Modified (non-deleted)
    for row in conn.execute(
        "SELECT file_path, modified FROM file"
        " WHERE modified_date = ? AND deleted = 0 AND is_generated = 0",
        (rollup_date_str,),
    ).fetchall():
        upsert_file_touch(conn, {
            "file_path": row["file_path"],
            "touch_date": rollup_date_str,
            "event_type": "modified",
            "modified": row["modified"],
        })

    # Deleted
    for row in conn.execute(
        "SELECT file_path FROM file"
        " WHERE deleted_date = ? AND deleted = 1 AND is_generated = 0",
        (rollup_date_str,),
    ).fetchall():
        upsert_file_touch(conn, {
            "file_path": row["file_path"],
            "touch_date": rollup_date_str,
            "event_type": "deleted",
            "modified": None,
        })


def _populate_daily_tasks(
    conn: sqlite3.Connection,
    rollup_date_str: str,
) -> None:
    """Snapshot task events for *rollup_date_str* into ``daily_task``.

    Two event types:
    - ``created``   — tasks with ``created_date`` equal to rollup date
    - ``completed`` — tasks with ``act_comp_date`` equal to rollup date and ``checked = 1``

    Denormalizes ``task_text``, ``file_path``, and ``attributes`` so the
    snapshot survives future edits to the source vault.

    Idempotent: ``INSERT OR REPLACE`` via ``upsert_daily_task``.
    """
    # Created
    for row in conn.execute(
        "SELECT task_id, task_text, file_path, attributes FROM task"
        " WHERE created_date = ?",
        (rollup_date_str,),
    ).fetchall():
        upsert_daily_task(conn, {
            "task_id": row["task_id"],
            "event_date": rollup_date_str,
            "event_type": "created",
            "task_text": row["task_text"],
            "file_path": row["file_path"],
            "attributes": row["attributes"],
        })

    # Completed
    for row in conn.execute(
        "SELECT task_id, task_text, file_path, attributes FROM task"
        " WHERE act_comp_date = ? AND checked = 1",
        (rollup_date_str,),
    ).fetchall():
        upsert_daily_task(conn, {
            "task_id": row["task_id"],
            "event_date": rollup_date_str,
            "event_type": "completed",
            "task_text": row["task_text"],
            "file_path": row["file_path"],
            "attributes": row["attributes"],
        })


# ---------------------------------------------------------------------------
# Public stage entry point
# ---------------------------------------------------------------------------


def run_rollup(
    config: MatlockConfig,
    conn: sqlite3.Connection,
    rollup_date: datetime.date,
) -> RollupResult:
    """Calculate metrics for *rollup_date* and upsert into ``daily_metric``.

    Writes one row per project plus one NULL-project row for unassigned
    tasks/files.  Also snapshots file-touch and task events for the date.
    Commits once at the end.
    """
    rollup_date_str = _date_to_str(rollup_date)
    tomorrow_str = _date_to_str(rollup_date + datetime.timedelta(days=1))

    # Build project → [file_path, ...] map from file_project (D2, D3)
    project_file_map: dict[str, list[str]] = defaultdict(list)
    linked_paths: set[str] = set()
    for row in conn.execute("SELECT project_id, file_path FROM file_project"):
        project_file_map[row["project_id"]].append(row["file_path"])
        linked_paths.add(row["file_path"])

    # Unassigned file paths: non-deleted, non-generated, not in any project (D2)
    all_eligible = [
        r["file_path"]
        for r in conn.execute(
            "SELECT file_path FROM file WHERE deleted = 0 AND is_generated = 0"
        )
    ]
    unassigned_paths = [fp for fp in all_eligible if fp not in linked_paths]

    rows_written = 0

    # One row per project
    for project_id, file_paths in project_file_map.items():
        row = _calc_metrics(conn, rollup_date_str, tomorrow_str, project_id, file_paths)
        upsert_daily_metric(conn, row)
        rows_written += 1

    # NULL row for unassigned tasks/files
    null_row = _calc_metrics(conn, rollup_date_str, tomorrow_str, None, unassigned_paths)
    upsert_daily_metric(conn, null_row)
    rows_written += 1

    # Snapshot file-touch and task events for this date
    _populate_file_touch(conn, rollup_date_str)
    _populate_daily_tasks(conn, rollup_date_str)

    conn.commit()
    return RollupResult(rollup_date=rollup_date_str, rows_written=rows_written)
