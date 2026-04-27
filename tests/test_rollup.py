"""Tests for matlock/stages/rollup.py and matlock/db.py upsert_daily_metric."""
from __future__ import annotations

import datetime
import json
from pathlib import Path

import pytest

from matlock.config import MatlockConfig
from matlock.db import get_connection, init_db, upsert_daily_metric, upsert_file, upsert_task
from matlock.stages.rollup import (
    RollupResult,
    _calc_metrics,
    _date_to_str,
    _estimate_minutes,
    run_rollup,
)


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

DATE = datetime.date(2026, 4, 26)
DATE_STR = "2026-04-26"
TOMORROW_STR = "2026-04-27"


def _conn():
    conn = get_connection(":memory:")
    init_db(conn)
    return conn


def _make_config() -> MatlockConfig:
    return MatlockConfig(
        base_directory=Path("/vault"),
        db_path=Path("/vault/matlock.db"),
        output_directory=Path("/vault/_output"),
    )


# epoch ms for a date in UTC
def _epoch_ms(date_str: str) -> int:
    """Convert YYYY-MM-DD to Unix epoch milliseconds (noon UTC)."""
    dt = datetime.datetime.strptime(date_str + "T12:00:00", "%Y-%m-%dT%H:%M:%S")
    return int(dt.replace(tzinfo=datetime.timezone.utc).timestamp() * 1000)


def _seed_file(
    conn,
    file_path: str,
    *,
    modified_date: str = DATE_STR,
    created: int | None = None,
    deleted: int = 0,
    is_generated: int = 0,
) -> None:
    upsert_file(conn, {
        "file_path": file_path,
        "sha256": "aaa",
        "file_ext": ".md",
        "created": created if created is not None else _epoch_ms(modified_date),
        "modified": _epoch_ms(modified_date),
        "modified_date": modified_date,
        "deleted": deleted,
        "length": 0,
        "word_count": None,
        "meta_data": None,
        "is_generated": is_generated,
        "needs_parsing": 0,
    })


def _seed_task(
    conn,
    task_id: str,
    file_path: str,
    *,
    checked: int = 0,
    created_date: str | None = None,
    due_date: str | None = None,
    act_comp_date: str | None = None,
    estimate_seconds: int | None = None,
) -> None:
    attributes = {}
    if estimate_seconds is not None:
        attributes["estimate"] = estimate_seconds
    upsert_task(conn, {
        "task_id": task_id,
        "file_path": file_path,
        "parent_task_id": None,
        "created_date": created_date,
        "due_date": due_date,
        "est_comp_date": None,
        "act_comp_date": act_comp_date,
        "checked": checked,
        "task_text": "Task text",
        "overflow": 0,
        "headers": "[]",
        "attributes": json.dumps(attributes),
        "errors": "[]",
        "twin_index": 0,
    })


def _link_file_project(conn, file_path: str, project_id: str) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO project (project_id, title) VALUES (?, ?)",
        (project_id, project_id),
    )
    conn.execute(
        "INSERT OR REPLACE INTO file_project (file_path, project_id) VALUES (?, ?)",
        (file_path, project_id),
    )


# ---------------------------------------------------------------------------
# _date_to_str
# ---------------------------------------------------------------------------


class TestDateToStr:
    def test_formats_correctly(self):
        assert _date_to_str(datetime.date(2026, 4, 26)) == "2026-04-26"

    def test_pads_single_digits(self):
        assert _date_to_str(datetime.date(2026, 1, 5)) == "2026-01-05"


# ---------------------------------------------------------------------------
# _estimate_minutes
# ---------------------------------------------------------------------------


class TestEstimateMinutes:
    def test_converts_seconds_to_minutes(self):
        assert _estimate_minutes('{"estimate": 3600}') == 60

    def test_integer_division(self):
        assert _estimate_minutes('{"estimate": 90}') == 1  # 90s → 1m (floor)

    def test_none_returns_zero(self):
        assert _estimate_minutes(None) == 0

    def test_empty_json_returns_zero(self):
        assert _estimate_minutes("{}") == 0

    def test_missing_key_returns_zero(self):
        assert _estimate_minutes('{"due_date": "2026-01-01"}') == 0

    def test_null_value_returns_zero(self):
        assert _estimate_minutes('{"estimate": null}') == 0

    def test_zero_seconds(self):
        assert _estimate_minutes('{"estimate": 0}') == 0


# ---------------------------------------------------------------------------
# upsert_daily_metric (db.py helper)
# ---------------------------------------------------------------------------


class TestUpsertDailyMetric:
    def _row(self, project_id=None, metric_date=DATE_STR, count=1):
        return {
            "metric_date": metric_date,
            "project_id": project_id,
            "tasks_created_count": count,
            "tasks_created_minutes": 0,
            "tasks_completed_count": 0,
            "tasks_completed_minutes": 0,
            "tasks_due_tomorrow_count": 0,
            "tasks_due_tomorrow_minutes": 0,
            "tasks_past_due_count": 0,
            "tasks_past_due_minutes": 0,
            "tasks_future_due_count": 0,
            "tasks_future_due_minutes": 0,
            "files_created_count": 0,
            "files_modified_count": 0,
            "files_deleted_count": 0,
        }

    def test_inserts_row(self):
        conn = _conn()
        upsert_daily_metric(conn, self._row("p1"))
        conn.commit()
        rows = conn.execute("SELECT * FROM daily_metric").fetchall()
        assert len(rows) == 1

    def test_null_project_id(self):
        conn = _conn()
        upsert_daily_metric(conn, self._row(None))
        conn.commit()
        row = conn.execute("SELECT * FROM daily_metric").fetchone()
        assert row["project_id"] is None

    def test_replace_existing_row(self):
        conn = _conn()
        upsert_daily_metric(conn, self._row("p1", count=1))
        upsert_daily_metric(conn, self._row("p1", count=99))
        conn.commit()
        row = conn.execute("SELECT * FROM daily_metric WHERE project_id = 'p1'").fetchone()
        assert row["tasks_created_count"] == 99

    def test_null_replace_idempotent(self):
        conn = _conn()
        upsert_daily_metric(conn, self._row(None, count=5))
        upsert_daily_metric(conn, self._row(None, count=10))
        conn.commit()
        rows = conn.execute("SELECT * FROM daily_metric WHERE project_id IS NULL").fetchall()
        assert len(rows) == 1
        assert rows[0]["tasks_created_count"] == 10

    def test_different_dates_are_separate_rows(self):
        conn = _conn()
        upsert_daily_metric(conn, self._row("p1", metric_date="2026-04-25"))
        upsert_daily_metric(conn, self._row("p1", metric_date="2026-04-26"))
        conn.commit()
        assert conn.execute("SELECT COUNT(*) FROM daily_metric").fetchone()[0] == 2


# ---------------------------------------------------------------------------
# run_rollup — basic structure
# ---------------------------------------------------------------------------


class TestRunRollupBasic:
    def test_returns_rollup_result_type(self):
        conn = _conn()
        result = run_rollup(_make_config(), conn, DATE)
        assert isinstance(result, RollupResult)

    def test_rollup_date_in_result(self):
        conn = _conn()
        result = run_rollup(_make_config(), conn, DATE)
        assert result.rollup_date == DATE_STR

    def test_always_writes_null_row(self):
        conn = _conn()
        result = run_rollup(_make_config(), conn, DATE)
        assert result.rows_written == 1
        row = conn.execute(
            "SELECT * FROM daily_metric WHERE project_id IS NULL"
        ).fetchone()
        assert row is not None

    def test_rows_written_includes_null_row(self):
        conn = _conn()
        _seed_file(conn, "a.md")
        _link_file_project(conn, "a.md", "p1")
        result = run_rollup(_make_config(), conn, DATE)
        # 1 project row + 1 null row
        assert result.rows_written == 2

    def test_all_metric_columns_are_integers(self):
        conn = _conn()
        run_rollup(_make_config(), conn, DATE)
        row = conn.execute("SELECT * FROM daily_metric WHERE project_id IS NULL").fetchone()
        int_columns = [
            "tasks_created_count", "tasks_created_minutes",
            "tasks_completed_count", "tasks_completed_minutes",
            "tasks_due_tomorrow_count", "tasks_due_tomorrow_minutes",
            "tasks_past_due_count", "tasks_past_due_minutes",
            "tasks_future_due_count", "tasks_future_due_minutes",
            "files_created_count", "files_modified_count", "files_deleted_count",
        ]
        for col in int_columns:
            assert isinstance(row[col], int), f"{col} is not int: {row[col]}"


# ---------------------------------------------------------------------------
# run_rollup — task metrics
# ---------------------------------------------------------------------------


class TestRunRollupTaskMetrics:
    def test_tasks_created_count(self):
        conn = _conn()
        _seed_file(conn, "a.md")
        _link_file_project(conn, "a.md", "p1")
        _seed_task(conn, "t1", "a.md", created_date=DATE_STR)
        _seed_task(conn, "t2", "a.md", created_date=DATE_STR)
        run_rollup(_make_config(), conn, DATE)
        row = conn.execute(
            "SELECT * FROM daily_metric WHERE project_id = 'p1'"
        ).fetchone()
        assert row["tasks_created_count"] == 2

    def test_tasks_created_minutes(self):
        conn = _conn()
        _seed_file(conn, "a.md")
        _link_file_project(conn, "a.md", "p1")
        _seed_task(conn, "t1", "a.md", created_date=DATE_STR, estimate_seconds=3600)
        _seed_task(conn, "t2", "a.md", created_date=DATE_STR, estimate_seconds=1800)
        run_rollup(_make_config(), conn, DATE)
        row = conn.execute(
            "SELECT * FROM daily_metric WHERE project_id = 'p1'"
        ).fetchone()
        assert row["tasks_created_minutes"] == 90  # 60 + 30

    def test_tasks_completed_count(self):
        conn = _conn()
        _seed_file(conn, "a.md")
        _link_file_project(conn, "a.md", "p1")
        _seed_task(conn, "t1", "a.md", checked=1, act_comp_date=DATE_STR)
        run_rollup(_make_config(), conn, DATE)
        row = conn.execute(
            "SELECT * FROM daily_metric WHERE project_id = 'p1'"
        ).fetchone()
        assert row["tasks_completed_count"] == 1

    def test_completed_requires_checked_true(self):
        conn = _conn()
        _seed_file(conn, "a.md")
        _link_file_project(conn, "a.md", "p1")
        # act_comp_date matches but checked=0 — should NOT count
        _seed_task(conn, "t1", "a.md", checked=0, act_comp_date=DATE_STR)
        run_rollup(_make_config(), conn, DATE)
        row = conn.execute(
            "SELECT * FROM daily_metric WHERE project_id = 'p1'"
        ).fetchone()
        assert row["tasks_completed_count"] == 0

    def test_tasks_due_tomorrow(self):
        conn = _conn()
        _seed_file(conn, "a.md")
        _link_file_project(conn, "a.md", "p1")
        _seed_task(conn, "t1", "a.md", due_date=TOMORROW_STR, checked=0)
        run_rollup(_make_config(), conn, DATE)
        row = conn.execute(
            "SELECT * FROM daily_metric WHERE project_id = 'p1'"
        ).fetchone()
        assert row["tasks_due_tomorrow_count"] == 1

    def test_tasks_past_due(self):
        conn = _conn()
        _seed_file(conn, "a.md")
        _link_file_project(conn, "a.md", "p1")
        _seed_task(conn, "t1", "a.md", due_date="2026-04-20", checked=0)
        run_rollup(_make_config(), conn, DATE)
        row = conn.execute(
            "SELECT * FROM daily_metric WHERE project_id = 'p1'"
        ).fetchone()
        assert row["tasks_past_due_count"] == 1

    def test_tasks_future_due(self):
        conn = _conn()
        _seed_file(conn, "a.md")
        _link_file_project(conn, "a.md", "p1")
        _seed_task(conn, "t1", "a.md", due_date="2026-05-10", checked=0)
        run_rollup(_make_config(), conn, DATE)
        row = conn.execute(
            "SELECT * FROM daily_metric WHERE project_id = 'p1'"
        ).fetchone()
        assert row["tasks_future_due_count"] == 1

    def test_checked_tasks_excluded_from_overdue(self):
        conn = _conn()
        _seed_file(conn, "a.md")
        _link_file_project(conn, "a.md", "p1")
        # Past due but already checked — should not count as past_due
        _seed_task(conn, "t1", "a.md", due_date="2026-04-01", checked=1)
        run_rollup(_make_config(), conn, DATE)
        row = conn.execute(
            "SELECT * FROM daily_metric WHERE project_id = 'p1'"
        ).fetchone()
        assert row["tasks_past_due_count"] == 0

    def test_no_tasks_all_counts_zero(self):
        conn = _conn()
        _seed_file(conn, "a.md")
        _link_file_project(conn, "a.md", "p1")
        run_rollup(_make_config(), conn, DATE)
        row = conn.execute(
            "SELECT * FROM daily_metric WHERE project_id = 'p1'"
        ).fetchone()
        assert row["tasks_created_count"] == 0
        assert row["tasks_completed_count"] == 0
        assert row["tasks_past_due_count"] == 0


# ---------------------------------------------------------------------------
# run_rollup — unassigned (NULL project) row
# ---------------------------------------------------------------------------


class TestRunRollupNullRow:
    def test_unassigned_task_goes_to_null_row(self):
        conn = _conn()
        _seed_file(conn, "unlinked.md")
        _seed_task(conn, "t1", "unlinked.md", created_date=DATE_STR)
        run_rollup(_make_config(), conn, DATE)
        null_row = conn.execute(
            "SELECT * FROM daily_metric WHERE project_id IS NULL"
        ).fetchone()
        assert null_row["tasks_created_count"] == 1

    def test_linked_task_not_in_null_row(self):
        conn = _conn()
        _seed_file(conn, "linked.md")
        _link_file_project(conn, "linked.md", "p1")
        _seed_task(conn, "t1", "linked.md", created_date=DATE_STR)
        run_rollup(_make_config(), conn, DATE)
        null_row = conn.execute(
            "SELECT * FROM daily_metric WHERE project_id IS NULL"
        ).fetchone()
        assert null_row["tasks_created_count"] == 0

    def test_null_row_always_present_even_empty_vault(self):
        conn = _conn()
        run_rollup(_make_config(), conn, DATE)
        row = conn.execute(
            "SELECT * FROM daily_metric WHERE project_id IS NULL"
        ).fetchone()
        assert row is not None

    def test_file_belonging_to_no_project_in_null_row(self):
        conn = _conn()
        _seed_file(conn, "orphan.md", modified_date=DATE_STR)
        run_rollup(_make_config(), conn, DATE)
        null_row = conn.execute(
            "SELECT * FROM daily_metric WHERE project_id IS NULL"
        ).fetchone()
        assert null_row["files_modified_count"] == 1


# ---------------------------------------------------------------------------
# run_rollup — file metrics
# ---------------------------------------------------------------------------


class TestRunRollupFileMetrics:
    def test_files_modified_count(self):
        conn = _conn()
        _seed_file(conn, "a.md", modified_date=DATE_STR)
        _link_file_project(conn, "a.md", "p1")
        run_rollup(_make_config(), conn, DATE)
        row = conn.execute(
            "SELECT * FROM daily_metric WHERE project_id = 'p1'"
        ).fetchone()
        assert row["files_modified_count"] == 1

    def test_deleted_file_not_in_modified_count(self):
        conn = _conn()
        _seed_file(conn, "a.md", modified_date=DATE_STR, deleted=1)
        _link_file_project(conn, "a.md", "p1")
        run_rollup(_make_config(), conn, DATE)
        row = conn.execute(
            "SELECT * FROM daily_metric WHERE project_id = 'p1'"
        ).fetchone()
        assert row["files_modified_count"] == 0

    def test_generated_file_not_counted(self):
        conn = _conn()
        _seed_file(conn, "_Matlock/dash.md", modified_date=DATE_STR, is_generated=1)
        run_rollup(_make_config(), conn, DATE)
        null_row = conn.execute(
            "SELECT * FROM daily_metric WHERE project_id IS NULL"
        ).fetchone()
        assert null_row["files_modified_count"] == 0

    def test_files_deleted_count(self):
        conn = _conn()
        # A file soft-deleted on rollup_date: modified_date = DATE_STR, deleted=1
        _seed_file(conn, "gone.md", modified_date=DATE_STR, deleted=1)
        _link_file_project(conn, "gone.md", "p1")
        run_rollup(_make_config(), conn, DATE)
        row = conn.execute(
            "SELECT * FROM daily_metric WHERE project_id = 'p1'"
        ).fetchone()
        assert row["files_deleted_count"] == 1

    def test_files_created_count(self):
        conn = _conn()
        # created epoch maps to DATE_STR
        _seed_file(conn, "new.md", modified_date=DATE_STR, created=_epoch_ms(DATE_STR))
        _link_file_project(conn, "new.md", "p1")
        run_rollup(_make_config(), conn, DATE)
        row = conn.execute(
            "SELECT * FROM daily_metric WHERE project_id = 'p1'"
        ).fetchone()
        assert row["files_created_count"] == 1

    def test_file_created_different_date_not_counted(self):
        conn = _conn()
        # created epoch maps to a different date
        _seed_file(conn, "old.md", modified_date=DATE_STR, created=_epoch_ms("2026-01-01"))
        _link_file_project(conn, "old.md", "p1")
        run_rollup(_make_config(), conn, DATE)
        row = conn.execute(
            "SELECT * FROM daily_metric WHERE project_id = 'p1'"
        ).fetchone()
        assert row["files_created_count"] == 0


# ---------------------------------------------------------------------------
# run_rollup — multi-project
# ---------------------------------------------------------------------------


class TestRunRollupMultiProject:
    def test_two_projects_two_rows_plus_null(self):
        conn = _conn()
        _seed_file(conn, "a.md")
        _seed_file(conn, "b.md")
        _link_file_project(conn, "a.md", "p1")
        _link_file_project(conn, "b.md", "p2")
        result = run_rollup(_make_config(), conn, DATE)
        assert result.rows_written == 3  # p1 + p2 + NULL

    def test_file_in_two_projects_counted_in_both(self):
        conn = _conn()
        _seed_file(conn, "shared.md", modified_date=DATE_STR)
        _link_file_project(conn, "shared.md", "p1")
        # Insert project first (FK), then file_project link
        conn.execute(
            "INSERT OR REPLACE INTO project (project_id, title) VALUES (?, ?)",
            ("p2", "P2"),
        )
        conn.execute(
            "INSERT OR REPLACE INTO file_project (file_path, project_id) VALUES (?, ?)",
            ("shared.md", "p2"),
        )
        _seed_task(conn, "t1", "shared.md", created_date=DATE_STR)
        run_rollup(_make_config(), conn, DATE)
        p1 = conn.execute(
            "SELECT tasks_created_count FROM daily_metric WHERE project_id = 'p1'"
        ).fetchone()
        p2 = conn.execute(
            "SELECT tasks_created_count FROM daily_metric WHERE project_id = 'p2'"
        ).fetchone()
        assert p1["tasks_created_count"] == 1
        assert p2["tasks_created_count"] == 1

    def test_tasks_scoped_to_project_files(self):
        """Tasks in project p2's files must not appear in project p1's row."""
        conn = _conn()
        _seed_file(conn, "a.md")
        _seed_file(conn, "b.md")
        _link_file_project(conn, "a.md", "p1")
        _link_file_project(conn, "b.md", "p2")
        _seed_task(conn, "t1", "b.md", created_date=DATE_STR)
        run_rollup(_make_config(), conn, DATE)
        p1 = conn.execute(
            "SELECT tasks_created_count FROM daily_metric WHERE project_id = 'p1'"
        ).fetchone()
        assert p1["tasks_created_count"] == 0


# ---------------------------------------------------------------------------
# run_rollup — idempotency
# ---------------------------------------------------------------------------


class TestRunRollupIdempotency:
    def test_second_run_same_date_same_result(self):
        conn = _conn()
        _seed_file(conn, "a.md")
        _link_file_project(conn, "a.md", "p1")
        _seed_task(conn, "t1", "a.md", created_date=DATE_STR)
        run_rollup(_make_config(), conn, DATE)
        run_rollup(_make_config(), conn, DATE)
        rows = conn.execute("SELECT * FROM daily_metric").fetchall()
        # Still 2 rows (p1 + NULL), not 4
        assert len(rows) == 2
        p1 = conn.execute(
            "SELECT tasks_created_count FROM daily_metric WHERE project_id = 'p1'"
        ).fetchone()
        assert p1["tasks_created_count"] == 1

    def test_different_dates_separate_rows(self):
        conn = _conn()
        date_a = datetime.date(2026, 4, 25)
        date_b = datetime.date(2026, 4, 26)
        run_rollup(_make_config(), conn, date_a)
        run_rollup(_make_config(), conn, date_b)
        rows = conn.execute("SELECT * FROM daily_metric").fetchall()
        assert len(rows) == 2
