"""Tests for matlock/stages/report.py."""
from __future__ import annotations

import datetime
import json
from pathlib import Path

import pytest

from matlock.config import MatlockConfig
from matlock.db import get_connection, init_db, upsert_daily_metric, upsert_file, upsert_task
from matlock.stages.report import (
    ReportResult,
    _calc_streak,
    _calc_heatmap,
    _build_jinja_env,
    _cleanup_stale_report_files,
    _compute_expected_paths,
    _get_due_today_tasks,
    _get_past_due_tasks,
    _get_due_soon_tasks,
    _get_future_due_tasks,
    _get_not_due_tasks,
    _build_tiers,
    _history_subpath,
    _purge_stale_generated,
    run_report,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _conn():
    conn = get_connection(":memory:")
    init_db(conn)
    return conn


def _make_config(out_dir: Path) -> MatlockConfig:
    return MatlockConfig(
        base_directory=out_dir / "vault",
        db_path=out_dir / "matlock.db",
        output_directory=out_dir / "_Matlock",
    )


def _seed_file(conn, file_path: str, *, modified_date: str = "2026-04-26") -> None:
    upsert_file(conn, {
        "file_path": file_path,
        "sha256": "abc",
        "file_ext": ".md",
        "created": 0,
        "modified": 0,
        "modified_date": modified_date,
        "deleted": 0,
        "length": 0,
        "word_count": None,
        "meta_data": None,
        "is_generated": 0,
        "needs_parsing": 0,
    })


def _seed_task(
    conn,
    task_id: str,
    file_path: str,
    *,
    checked: int = 0,
    due_date: str | None = None,
    act_comp_date: str | None = None,
    created_date: str = "2026-04-26",
) -> None:
    upsert_task(conn, {
        "task_id": task_id,
        "file_path": file_path,
        "parent_task_id": None,
        "created_date": created_date,
        "due_date": due_date,
        "est_comp_date": None,
        "act_comp_date": act_comp_date,
        "checked": checked,
        "task_text": f"Task {task_id}",
        "overflow": 0,
        "headers": None,
        "attributes": None,
        "errors": None,
        "twin_index": 0,
    })


def _seed_metric(conn, metric_date: str, project_id: str | None, *, completed: int = 0) -> None:
    upsert_daily_metric(conn, {
        "metric_date": metric_date,
        "project_id": project_id,
        "tasks_created_count": 0,
        "tasks_created_minutes": 0,
        "tasks_completed_count": completed,
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
    })
    conn.commit()


def _seed_project(conn, project_id: str, title: str, super_project_id: str | None = None) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO project (project_id, super_project_id, title, priority)"
        " VALUES (?, ?, ?, ?)",
        (project_id, super_project_id, title, "medium"),
    )
    conn.commit()


def _seed_super_project(conn, sp_id: str, title: str) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO super_project (super_project_id, title) VALUES (?, ?)",
        (sp_id, title),
    )
    conn.commit()


def _link(conn, file_path: str, project_id: str) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO file_project (file_path, project_id) VALUES (?, ?)",
        (file_path, project_id),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Template loading smoke test
# ---------------------------------------------------------------------------


class TestTemplateLoading:
    def test_all_templates_load(self):
        env = _build_jinja_env()
        for name in [
            "daily_dashboard.md.j2",
            "project.md.j2",
            "super_project.md.j2",
            "daily_history.md.j2",
        ]:
            tmpl = env.get_template(name)
            assert tmpl is not None

    def test_basename_filter_registered(self):
        env = _build_jinja_env()
        assert "basename" in env.filters


# ---------------------------------------------------------------------------
# Streak calculation
# ---------------------------------------------------------------------------


class TestCalcStreak:
    def test_empty_db_returns_zeros(self):
        conn = _conn()
        assert _calc_streak(conn) == (0, 0, None)

    def test_single_active_day(self):
        conn = _conn()
        _seed_metric(conn, "2026-04-20", None, completed=3)
        current, longest, longest_end = _calc_streak(conn)
        assert current == 1
        assert longest == 1
        assert longest_end == "2026-04-20"

    def test_two_consecutive_active_days(self):
        conn = _conn()
        _seed_metric(conn, "2026-04-19", None, completed=1)
        _seed_metric(conn, "2026-04-20", None, completed=2)
        current, longest, _ = _calc_streak(conn)
        assert current == 2
        assert longest == 2

    def test_gap_resets_current_streak(self):
        conn = _conn()
        _seed_metric(conn, "2026-04-17", None, completed=1)
        _seed_metric(conn, "2026-04-18", None, completed=1)
        _seed_metric(conn, "2026-04-20", None, completed=1)  # gap on 19th
        current, longest, _ = _calc_streak(conn)
        assert current == 1
        assert longest == 2

    def test_inactive_day_breaks_current(self):
        conn = _conn()
        _seed_metric(conn, "2026-04-19", None, completed=1)
        _seed_metric(conn, "2026-04-20", None, completed=0)  # inactive
        current, longest, _ = _calc_streak(conn)
        assert current == 0
        assert longest == 1

    def test_multiple_projects_summed_per_day(self):
        """Completions from different project rows on same date should be summed."""
        conn = _conn()
        _seed_metric(conn, "2026-04-20", "p1", completed=1)
        _seed_metric(conn, "2026-04-20", None, completed=0)
        current, longest, _ = _calc_streak(conn)
        assert current == 1

    def test_longest_streak_across_gap(self):
        conn = _conn()
        for d in ["2026-04-10", "2026-04-11", "2026-04-12"]:
            _seed_metric(conn, d, None, completed=1)
        _seed_metric(conn, "2026-04-14", None, completed=1)  # gap, only 1-day streak
        _, longest, longest_end = _calc_streak(conn)
        assert longest == 3


# ---------------------------------------------------------------------------
# run_report — basic writes
# ---------------------------------------------------------------------------


class TestRunReportBasic:
    def test_returns_report_result(self, tmp_path: Path):
        conn = _conn()
        cfg = _make_config(tmp_path)
        result = run_report(cfg, conn)
        assert isinstance(result, ReportResult)

    def test_target_stored_in_result(self, tmp_path: Path):
        conn = _conn()
        cfg = _make_config(tmp_path)
        result = run_report(cfg, conn, target="all")
        assert result.target == "all"

    def test_creates_output_directory(self, tmp_path: Path):
        conn = _conn()
        cfg = _make_config(tmp_path)
        out = cfg.output_directory
        assert not out.exists()
        run_report(cfg, conn)
        assert out.exists()

    def test_invalid_target_raises(self, tmp_path: Path):
        conn = _conn()
        cfg = _make_config(tmp_path)
        with pytest.raises(ValueError, match="Invalid target"):
            run_report(cfg, conn, target="bogus")

    def test_dashboard_file_written(self, tmp_path: Path):
        conn = _conn()
        cfg = _make_config(tmp_path)
        run_report(cfg, conn)
        assert (cfg.output_directory / "000_Daily_Dashboard.md").exists()

    def test_dashboard_registered_in_file_table(self, tmp_path: Path):
        conn = _conn()
        cfg = _make_config(tmp_path)
        run_report(cfg, conn)
        path = str(cfg.output_directory / "000_Daily_Dashboard.md")
        row = conn.execute(
            "SELECT is_generated FROM file WHERE file_path = ?", (path,)
        ).fetchone()
        assert row is not None
        assert row["is_generated"] == 1

    def test_files_written_count_empty_db(self, tmp_path: Path):
        """Empty DB: dashboard + 5 task view pages (no projects, no history)."""
        conn = _conn()
        cfg = _make_config(tmp_path)
        result = run_report(cfg, conn)
        assert result.files_written == 6  # dashboard + 5 task view pages

    def test_idempotent_second_run(self, tmp_path: Path):
        conn = _conn()
        cfg = _make_config(tmp_path)
        run_report(cfg, conn)
        result2 = run_report(cfg, conn)
        assert result2.files_written == 6
        assert (cfg.output_directory / "000_Daily_Dashboard.md").exists()


# ---------------------------------------------------------------------------
# run_report — target filtering
# ---------------------------------------------------------------------------


class TestRunReportTargetFilter:
    def _setup(self, tmp_path: Path):
        conn = _conn()
        _seed_project(conn, "p1", "Project One")
        _seed_metric(conn, "2026-04-20", None, completed=1)
        cfg = _make_config(tmp_path)
        return conn, cfg

    def test_target_dashboard_only_writes_dashboard(self, tmp_path: Path):
        conn, cfg = self._setup(tmp_path)
        result = run_report(cfg, conn, target="dashboard")
        assert (cfg.output_directory / "000_Daily_Dashboard.md").exists()
        assert (cfg.output_directory / "001_Due_Today.md").exists()
        assert (cfg.output_directory / "002_Past_Due.md").exists()
        assert (cfg.output_directory / "003_Due_Soon.md").exists()
        assert (cfg.output_directory / "004_Future_Due.md").exists()
        assert (cfg.output_directory / "005_Not_Due.md").exists()
        assert not (cfg.output_directory / "Projects").exists()
        assert not (cfg.output_directory / "History").exists()
        assert result.files_written == 6

    def test_target_projects_writes_project_page(self, tmp_path: Path):
        conn, cfg = self._setup(tmp_path)
        result = run_report(cfg, conn, target="projects")
        assert (cfg.output_directory / "Projects" / "p1.md").exists()
        assert not (cfg.output_directory / "000_Daily_Dashboard.md").exists()

    def test_target_history_writes_history_page(self, tmp_path: Path):
        conn, cfg = self._setup(tmp_path)
        result = run_report(cfg, conn, target="history")
        assert (cfg.output_directory / _history_subpath("2026-04-20")).exists()
        assert not (cfg.output_directory / "000_Daily_Dashboard.md").exists()
        assert result.files_written == 1

    def test_target_all_writes_all(self, tmp_path: Path):
        conn, cfg = self._setup(tmp_path)
        result = run_report(cfg, conn, target="all")
        assert (cfg.output_directory / "000_Daily_Dashboard.md").exists()
        assert (cfg.output_directory / "Projects" / "p1.md").exists()
        assert (cfg.output_directory / _history_subpath("2026-04-20")).exists()


# ---------------------------------------------------------------------------
# run_report — project page content
# ---------------------------------------------------------------------------


class TestRunReportProjectPage:
    def test_project_page_created(self, tmp_path: Path):
        conn = _conn()
        _seed_project(conn, "proj1", "My Project")
        cfg = _make_config(tmp_path)
        run_report(cfg, conn, target="projects")
        assert (cfg.output_directory / "Projects" / "proj1.md").exists()

    def test_project_title_in_content(self, tmp_path: Path):
        conn = _conn()
        _seed_project(conn, "proj1", "My Project")
        cfg = _make_config(tmp_path)
        run_report(cfg, conn, target="projects")
        content = (cfg.output_directory / "Projects" / "proj1.md").read_text()
        assert "My Project" in content

    def test_project_page_registered_as_generated(self, tmp_path: Path):
        conn = _conn()
        _seed_project(conn, "proj1", "My Project")
        cfg = _make_config(tmp_path)
        run_report(cfg, conn, target="projects")
        path = str(cfg.output_directory / "Projects" / "proj1.md")
        row = conn.execute(
            "SELECT is_generated FROM file WHERE file_path = ?", (path,)
        ).fetchone()
        assert row is not None
        assert row["is_generated"] == 1

    def test_open_task_appears_in_project_page(self, tmp_path: Path):
        conn = _conn()
        _seed_project(conn, "proj1", "My Project")
        _seed_file(conn, "notes.md")
        _link(conn, "notes.md", "proj1")
        _seed_task(conn, "t1", "notes.md", checked=0)
        cfg = _make_config(tmp_path)
        run_report(cfg, conn, target="projects")
        content = (cfg.output_directory / "Projects" / "proj1.md").read_text()
        assert "Task t1" in content

    def test_source_files_listed(self, tmp_path: Path):
        conn = _conn()
        _seed_project(conn, "proj1", "My Project")
        _seed_file(conn, "notes.md")
        _link(conn, "notes.md", "proj1")
        cfg = _make_config(tmp_path)
        run_report(cfg, conn, target="projects")
        content = (cfg.output_directory / "Projects" / "proj1.md").read_text()
        assert "notes.md" in content

    def test_project_id_flag_writes_only_that_project(self, tmp_path: Path):
        conn = _conn()
        _seed_project(conn, "p1", "P One")
        _seed_project(conn, "p2", "P Two")
        cfg = _make_config(tmp_path)
        result = run_report(cfg, conn, project_id="p1")
        assert (cfg.output_directory / "Projects" / "p1.md").exists()
        assert not (cfg.output_directory / "Projects" / "p2.md").exists()
        assert result.files_written == 1

    def test_project_id_unknown_writes_zero(self, tmp_path: Path):
        conn = _conn()
        cfg = _make_config(tmp_path)
        result = run_report(cfg, conn, project_id="nonexistent")
        assert result.files_written == 0


# ---------------------------------------------------------------------------
# run_report — super-project page
# ---------------------------------------------------------------------------


class TestRunReportSuperProjectPage:
    def test_super_project_page_created(self, tmp_path: Path):
        conn = _conn()
        _seed_super_project(conn, "sp1", "Big Initiative")
        _seed_project(conn, "p1", "Sub Project", super_project_id="sp1")
        cfg = _make_config(tmp_path)
        run_report(cfg, conn, target="projects")
        assert (cfg.output_directory / "SuperProjects" / "sp1.md").exists()

    def test_super_project_title_in_content(self, tmp_path: Path):
        conn = _conn()
        _seed_super_project(conn, "sp1", "Big Initiative")
        cfg = _make_config(tmp_path)
        run_report(cfg, conn, target="projects")
        content = (cfg.output_directory / "SuperProjects" / "sp1.md").read_text()
        assert "Big Initiative" in content


# ---------------------------------------------------------------------------
# run_report — history page content
# ---------------------------------------------------------------------------


class TestRunReportHistoryPage:
    def test_history_page_per_date(self, tmp_path: Path):
        conn = _conn()
        _seed_metric(conn, "2026-04-20", None, completed=2)
        _seed_metric(conn, "2026-04-21", None, completed=1)
        cfg = _make_config(tmp_path)
        run_report(cfg, conn, target="history")
        assert (cfg.output_directory / _history_subpath("2026-04-20")).exists()
        assert (cfg.output_directory / _history_subpath("2026-04-21")).exists()

    def test_history_page_content_contains_date(self, tmp_path: Path):
        conn = _conn()
        _seed_metric(conn, "2026-04-20", None, completed=1)
        cfg = _make_config(tmp_path)
        run_report(cfg, conn, target="history")
        content = (cfg.output_directory / _history_subpath("2026-04-20")).read_text()
        assert "2026-04-20" in content

    def test_history_page_day_of_week(self, tmp_path: Path):
        conn = _conn()
        _seed_metric(conn, "2026-04-20", None, completed=1)  # Monday
        cfg = _make_config(tmp_path)
        run_report(cfg, conn, target="history")
        content = (cfg.output_directory / _history_subpath("2026-04-20")).read_text()
        assert "Monday" in content

    def test_history_page_registered_as_generated(self, tmp_path: Path):
        conn = _conn()
        _seed_metric(conn, "2026-04-20", None, completed=1)
        cfg = _make_config(tmp_path)
        run_report(cfg, conn, target="history")
        path = str(cfg.output_directory / _history_subpath("2026-04-20"))
        row = conn.execute(
            "SELECT is_generated FROM file WHERE file_path = ?", (path,)
        ).fetchone()
        assert row is not None
        assert row["is_generated"] == 1

    def test_no_history_if_no_daily_metrics(self, tmp_path: Path):
        conn = _conn()
        cfg = _make_config(tmp_path)
        result = run_report(cfg, conn, target="history")
        assert result.files_written == 0
        assert not (cfg.output_directory / "History").exists()

    def test_history_breakdown_shows_unassigned(self, tmp_path: Path):
        conn = _conn()
        _seed_metric(conn, "2026-04-20", None, completed=3)
        cfg = _make_config(tmp_path)
        run_report(cfg, conn, target="history")
        content = (cfg.output_directory / _history_subpath("2026-04-20")).read_text()
        assert "Unassigned" in content

    def test_history_breakdown_shows_project(self, tmp_path: Path):
        conn = _conn()
        _seed_project(conn, "p1", "My Project")
        _seed_metric(conn, "2026-04-20", "p1", completed=2)
        cfg = _make_config(tmp_path)
        run_report(cfg, conn, target="history")
        content = (cfg.output_directory / _history_subpath("2026-04-20")).read_text()
        assert "p1" in content


# ---------------------------------------------------------------------------
# Dashboard content
# ---------------------------------------------------------------------------


class TestDashboardContent:
    def test_dashboard_shows_streak(self, tmp_path: Path):
        conn = _conn()
        _seed_metric(conn, "2026-04-19", None, completed=1)
        _seed_metric(conn, "2026-04-20", None, completed=1)
        cfg = _make_config(tmp_path)
        run_report(cfg, conn, target="dashboard")
        content = (cfg.output_directory / "000_Daily_Dashboard.md").read_text()
        assert "Current Streak" in content

    def test_dashboard_shows_project_hub(self, tmp_path: Path):
        conn = _conn()
        _seed_project(conn, "alpha", "Alpha Project")
        cfg = _make_config(tmp_path)
        run_report(cfg, conn, target="dashboard")
        content = (cfg.output_directory / "000_Daily_Dashboard.md").read_text()
        assert "alpha" in content

    def test_dashboard_shows_past_due_task(self, tmp_path: Path):
        conn = _conn()
        _seed_file(conn, "work.md")
        _seed_task(conn, "td1", "work.md", checked=0, due_date="2020-01-01")
        cfg = _make_config(tmp_path)
        run_report(cfg, conn, target="dashboard")
        content = (cfg.output_directory / "000_Daily_Dashboard.md").read_text()
        assert "Task td1" in content

    def test_dashboard_heatmap_section_present(self, tmp_path: Path):
        conn = _conn()
        cfg = _make_config(tmp_path)
        run_report(cfg, conn, target="dashboard")
        content = (cfg.output_directory / "000_Daily_Dashboard.md").read_text()
        assert "Activity Heatmap" in content


# ---------------------------------------------------------------------------
# Report cleanup — stale project / super-project page deletion
# ---------------------------------------------------------------------------


class TestReportCleanup:
    def test_stale_project_file_deleted(self, tmp_path: Path):
        """A project page for a removed project is deleted on the next full report."""
        conn = _conn()
        cfg = _make_config(tmp_path)
        # First run: project "old" exists
        _seed_project(conn, "old", "Old Project")
        run_report(cfg, conn, target="projects")
        stale = cfg.output_directory / "Projects" / "old.md"
        assert stale.exists()

        # Remove the project from the DB (simulates removing from config + map-projects)
        conn.execute("DELETE FROM project WHERE project_id = 'old'")
        conn.commit()

        result = run_report(cfg, conn, target="projects")

        assert not stale.exists()
        assert result.files_deleted == 1

    def test_stale_super_project_file_deleted(self, tmp_path: Path):
        """A super-project page for a removed super-project is deleted on the next full report."""
        conn = _conn()
        cfg = _make_config(tmp_path)
        _seed_super_project(conn, "old-sp", "Old SP")
        run_report(cfg, conn, target="projects")
        stale = cfg.output_directory / "SuperProjects" / "old-sp.md"
        assert stale.exists()

        conn.execute("DELETE FROM super_project WHERE super_project_id = 'old-sp'")
        conn.commit()

        result = run_report(cfg, conn, target="projects")

        assert not stale.exists()
        assert result.files_deleted == 1

    def test_current_project_file_not_deleted(self, tmp_path: Path):
        """Project pages for still-active projects must survive cleanup."""
        conn = _conn()
        cfg = _make_config(tmp_path)
        _seed_project(conn, "keep", "Keep Me")
        run_report(cfg, conn, target="projects")

        result = run_report(cfg, conn, target="projects")

        assert (cfg.output_directory / "Projects" / "keep.md").exists()
        assert result.files_deleted == 0

    def test_files_deleted_count_multiple(self, tmp_path: Path):
        """files_deleted reflects the total number of stale files removed."""
        conn = _conn()
        cfg = _make_config(tmp_path)
        _seed_project(conn, "p1", "P1")
        _seed_project(conn, "p2", "P2")
        _seed_project(conn, "keep", "Keep")
        run_report(cfg, conn, target="projects")

        conn.execute("DELETE FROM project WHERE project_id IN ('p1', 'p2')")
        conn.commit()

        result = run_report(cfg, conn, target="projects")

        assert not (cfg.output_directory / "Projects" / "p1.md").exists()
        assert not (cfg.output_directory / "Projects" / "p2.md").exists()
        assert (cfg.output_directory / "Projects" / "keep.md").exists()
        assert result.files_deleted == 2

    def test_cleanup_removes_file_table_row(self, tmp_path: Path):
        """The file table row for a deleted report file must also be removed."""
        conn = _conn()
        cfg = _make_config(tmp_path)
        _seed_project(conn, "old", "Old")
        run_report(cfg, conn, target="projects")
        stale_path = str(cfg.output_directory / "Projects" / "old.md")
        # Row should exist before cleanup
        assert conn.execute(
            "SELECT 1 FROM file WHERE file_path = ?", (stale_path,)
        ).fetchone() is not None

        conn.execute("DELETE FROM project WHERE project_id = 'old'")
        conn.commit()
        run_report(cfg, conn, target="projects")

        assert conn.execute(
            "SELECT 1 FROM file WHERE file_path = ?", (stale_path,)
        ).fetchone() is None

    def test_cleanup_not_triggered_for_targeted_project_id(self, tmp_path: Path):
        """A single-project targeted render must NOT delete other project pages."""
        conn = _conn()
        cfg = _make_config(tmp_path)
        _seed_project(conn, "keep", "Keep")
        _seed_project(conn, "target", "Target")
        run_report(cfg, conn, target="projects")

        # Remove "keep" from DB — but then only render "target" via project_id
        conn.execute("DELETE FROM project WHERE project_id = 'keep'")
        conn.commit()
        run_report(cfg, conn, project_id="target")

        # "keep.md" should still exist — targeted render must not clean up
        assert (cfg.output_directory / "Projects" / "keep.md").exists()

    def test_cleanup_not_triggered_for_target_dashboard(self, tmp_path: Path):
        """target='dashboard' must NOT delete stale project pages."""
        conn = _conn()
        cfg = _make_config(tmp_path)
        _seed_project(conn, "old", "Old")
        run_report(cfg, conn, target="projects")

        conn.execute("DELETE FROM project WHERE project_id = 'old'")
        conn.commit()
        result = run_report(cfg, conn, target="dashboard")

        assert (cfg.output_directory / "Projects" / "old.md").exists()
        assert result.files_deleted == 0

    def test_cleanup_no_error_when_subdir_missing(self, tmp_path: Path):
        """Cleanup must be a no-op (not an error) when the report subdirectory doesn't exist."""
        conn = _conn()
        cfg = _make_config(tmp_path)
        # Never ran a project render — Projects/ doesn't exist
        result = run_report(cfg, conn, target="projects")
        assert result.files_deleted == 0


# ---------------------------------------------------------------------------
# Force regeneration
# ---------------------------------------------------------------------------


class TestRunReportForce:
    def test_force_runs_without_error(self, tmp_path: Path):
        conn = _conn()
        cfg = _make_config(tmp_path)
        result = run_report(cfg, conn, target="all", force=True)
        assert result.files_written >= 1

    def test_force_no_stale_files_deletes_nothing(self, tmp_path: Path):
        """force=True with a clean state should delete 0 files."""
        conn = _conn()
        cfg = _make_config(tmp_path)
        run_report(cfg, conn, target="all")
        result = run_report(cfg, conn, target="all", force=True)
        assert result.files_deleted == 0

    def test_force_deletes_stale_db_tracked_project_file(self, tmp_path: Path):
        """force=True removes a DB-tracked generated file for a removed project."""
        conn = _conn()
        cfg = _make_config(tmp_path)
        _seed_project(conn, "gone", "Gone")
        run_report(cfg, conn, target="all")
        stale = cfg.output_directory / "Projects" / "gone.md"
        assert stale.exists()

        conn.execute("DELETE FROM project WHERE project_id = 'gone'")
        conn.commit()

        result = run_report(cfg, conn, target="all", force=True)

        assert not stale.exists()
        assert result.files_deleted >= 1

    def test_force_deletes_stale_history_file(self, tmp_path: Path):
        """force=True removes a History page for a date no longer in daily_metric."""
        conn = _conn()
        cfg = _make_config(tmp_path)
        _seed_metric(conn, "2026-01-01", None, completed=1)
        run_report(cfg, conn, target="all")
        stale = cfg.output_directory / _history_subpath("2026-01-01")
        assert stale.exists()

        conn.execute("DELETE FROM daily_metric WHERE metric_date = '2026-01-01'")
        conn.commit()

        result = run_report(cfg, conn, target="all", force=True)

        assert not stale.exists()
        assert result.files_deleted >= 1

    def test_force_deletes_orphaned_md_file(self, tmp_path: Path):
        """force=True removes .md files in output_directory with no DB row."""
        conn = _conn()
        cfg = _make_config(tmp_path)
        run_report(cfg, conn, target="all")

        # Plant an orphaned file (not registered in DB)
        orphan = cfg.output_directory / "orphan.md"
        orphan.write_text("stale")

        result = run_report(cfg, conn, target="all", force=True)

        assert not orphan.exists()
        assert result.files_deleted >= 1

    def test_force_keeps_current_files(self, tmp_path: Path):
        """force=True must not delete files that belong to the current run."""
        conn = _conn()
        cfg = _make_config(tmp_path)
        _seed_project(conn, "keep", "Keep")
        result = run_report(cfg, conn, target="all", force=True)
        assert (cfg.output_directory / "000_Daily_Dashboard.md").exists()
        assert (cfg.output_directory / "Projects" / "keep.md").exists()

    def test_force_removes_stale_file_table_row(self, tmp_path: Path):
        """force=True removes the file table row for deleted generated files."""
        conn = _conn()
        cfg = _make_config(tmp_path)
        _seed_project(conn, "old", "Old")
        run_report(cfg, conn, target="all")
        stale_path = str(cfg.output_directory / "Projects" / "old.md")
        assert conn.execute(
            "SELECT 1 FROM file WHERE file_path = ?", (stale_path,)
        ).fetchone() is not None

        conn.execute("DELETE FROM project WHERE project_id = 'old'")
        conn.commit()
        run_report(cfg, conn, target="all", force=True)

        assert conn.execute(
            "SELECT 1 FROM file WHERE file_path = ?", (stale_path,)
        ).fetchone() is None


# ---------------------------------------------------------------------------
# _compute_expected_paths / _purge_stale_generated
# ---------------------------------------------------------------------------


class TestComputeExpectedPaths:
    def test_all_target_includes_dashboard(self, tmp_path: Path):
        conn = _conn()
        out = tmp_path / "_Matlock"
        paths = _compute_expected_paths(conn, out, "all")
        assert out / "000_Daily_Dashboard.md" in paths

    def test_all_target_includes_due_today(self, tmp_path: Path):
        conn = _conn()
        out = tmp_path / "_Matlock"
        paths = _compute_expected_paths(conn, out, "all")
        assert out / "001_Due_Today.md" in paths

    def test_all_target_includes_all_task_views(self, tmp_path: Path):
        conn = _conn()
        out = tmp_path / "_Matlock"
        paths = _compute_expected_paths(conn, out, "all")
        for name in ("002_Past_Due.md", "003_Due_Soon.md", "004_Future_Due.md", "005_Not_Due.md"):
            assert out / name in paths

    def test_dashboard_target_includes_due_today(self, tmp_path: Path):
        conn = _conn()
        out = tmp_path / "_Matlock"
        paths = _compute_expected_paths(conn, out, "dashboard")
        assert out / "001_Due_Today.md" in paths

    def test_dashboard_target_includes_all_task_views(self, tmp_path: Path):
        conn = _conn()
        out = tmp_path / "_Matlock"
        paths = _compute_expected_paths(conn, out, "dashboard")
        for name in ("002_Past_Due.md", "003_Due_Soon.md", "004_Future_Due.md", "005_Not_Due.md"):
            assert out / name in paths

    def test_dashboard_target_only_has_dashboard(self, tmp_path: Path):
        conn = _conn()
        _seed_project(conn, "p1", "P1")
        out = tmp_path / "_Matlock"
        paths = _compute_expected_paths(conn, out, "dashboard")
        assert out / "000_Daily_Dashboard.md" in paths
        assert out / "Projects" / "p1.md" not in paths

    def test_projects_target_includes_project_paths(self, tmp_path: Path):
        conn = _conn()
        _seed_project(conn, "p1", "P1")
        _seed_super_project(conn, "sp1", "SP1")
        out = tmp_path / "_Matlock"
        paths = _compute_expected_paths(conn, out, "projects")
        assert out / "Projects" / "p1.md" in paths
        assert out / "SuperProjects" / "sp1.md" in paths

    def test_history_target_includes_metric_dates(self, tmp_path: Path):
        conn = _conn()
        _seed_metric(conn, "2026-04-20", None, completed=1)
        out = tmp_path / "_Matlock"
        paths = _compute_expected_paths(conn, out, "history")
        assert out / _history_subpath("2026-04-20") in paths


# ---------------------------------------------------------------------------
# _get_due_today_tasks
# ---------------------------------------------------------------------------


def _seed_task_full(
    conn,
    task_id: str,
    file_path: str,
    *,
    checked: int = 0,
    due_date: str | None = None,
    task_priority: str | None = None,
    estimate_secs: int = 0,
) -> None:
    attrs = {}
    if task_priority:
        attrs["priority"] = task_priority
    if estimate_secs:
        attrs["estimate"] = estimate_secs
    upsert_task(conn, {
        "task_id": task_id,
        "file_path": file_path,
        "parent_task_id": None,
        "created_date": "2026-05-01",
        "due_date": due_date,
        "est_comp_date": None,
        "act_comp_date": None,
        "checked": checked,
        "task_text": f"Task {task_id}",
        "overflow": 0,
        "headers": None,
        "attributes": json.dumps(attrs) if attrs else None,
        "errors": None,
        "twin_index": 0,
    })


def _seed_project_priority(conn, project_id: str, title: str, priority: str | None) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO project (project_id, title, priority) VALUES (?, ?, ?)",
        (project_id, title, priority),
    )
    conn.commit()


class TestGetDueTodayTasks:
    TODAY = "2026-05-01"

    def test_returns_empty_when_no_tasks(self):
        conn = _conn()
        result = _get_due_today_tasks(conn, self.TODAY)
        assert result == []

    def test_excludes_checked_tasks(self):
        conn = _conn()
        _seed_file(conn, "/vault/f.md")
        _seed_task_full(conn, "t1", "/vault/f.md", due_date=self.TODAY, checked=1)
        result = _get_due_today_tasks(conn, self.TODAY)
        assert result == []

    def test_excludes_tasks_due_other_dates(self):
        conn = _conn()
        _seed_file(conn, "/vault/f.md")
        _seed_task_full(conn, "t1", "/vault/f.md", due_date="2026-04-30")
        result = _get_due_today_tasks(conn, self.TODAY)
        assert result == []

    def test_includes_task_due_today(self):
        conn = _conn()
        _seed_file(conn, "/vault/f.md")
        _seed_task_full(conn, "t1", "/vault/f.md", due_date=self.TODAY)
        result = _get_due_today_tasks(conn, self.TODAY)
        assert len(result) == 1
        assert result[0].task_text == "Task t1"

    def test_file_stem_strips_extension(self):
        conn = _conn()
        _seed_file(conn, "/vault/My Note.md")
        _seed_task_full(conn, "t1", "/vault/My Note.md", due_date=self.TODAY)
        result = _get_due_today_tasks(conn, self.TODAY)
        assert result[0].file_stem == "My Note"

    def test_estimate_formatted_as_minutes(self):
        conn = _conn()
        _seed_file(conn, "/vault/f.md")
        _seed_task_full(conn, "t1", "/vault/f.md", due_date=self.TODAY, estimate_secs=1800)
        result = _get_due_today_tasks(conn, self.TODAY)
        assert result[0].estimate_display == "30m"

    def test_no_estimate_shows_dash(self):
        conn = _conn()
        _seed_file(conn, "/vault/f.md")
        _seed_task_full(conn, "t1", "/vault/f.md", due_date=self.TODAY)
        result = _get_due_today_tasks(conn, self.TODAY)
        assert result[0].estimate_display == "—"

    def test_task_priority_high_sorts_first(self):
        conn = _conn()
        _seed_file(conn, "/vault/f.md")
        _seed_task_full(conn, "t_low", "/vault/f.md", due_date=self.TODAY, task_priority="Low")
        _seed_task_full(conn, "t_high", "/vault/f.md", due_date=self.TODAY, task_priority="High")
        _seed_task_full(conn, "t_none", "/vault/f.md", due_date=self.TODAY)
        result = _get_due_today_tasks(conn, self.TODAY)
        priorities = [t.task_priority for t in result]
        assert priorities == ["High", "Low", None]

    def test_lowercase_priority_normalised(self):
        """Priorities stored as 'high'/'medium'/'low' (lowercase) must be treated the same as title-case."""
        conn = _conn()
        _seed_file(conn, "/vault/f.md")
        _seed_task_full(conn, "t1", "/vault/f.md", due_date=self.TODAY, task_priority="high")
        result = _get_due_today_tasks(conn, self.TODAY)
        assert result[0].task_priority == "High"
        assert result[0].task_priority_rank == 0

    def test_project_priority_used_as_tiebreaker(self):
        conn = _conn()
        _seed_file(conn, "/vault/a.md")
        _seed_file(conn, "/vault/b.md")
        _seed_project_priority(conn, "p_high", "High Project", "High")
        _seed_project_priority(conn, "p_low", "Low Project", "Low")
        conn.execute("INSERT OR REPLACE INTO file_project VALUES (?, ?)", ("/vault/a.md", "p_low"))
        conn.execute("INSERT OR REPLACE INTO file_project VALUES (?, ?)", ("/vault/b.md", "p_high"))
        conn.commit()
        # Both tasks have same task priority (none), sorted by project priority
        _seed_task_full(conn, "t_low_proj", "/vault/a.md", due_date=self.TODAY)
        _seed_task_full(conn, "t_high_proj", "/vault/b.md", due_date=self.TODAY)
        result = _get_due_today_tasks(conn, self.TODAY)
        assert result[0].project_id == "p_high"
        assert result[1].project_id == "p_low"

    def test_no_priority_task_sorts_last(self):
        conn = _conn()
        _seed_file(conn, "/vault/f.md")
        _seed_task_full(conn, "t_med", "/vault/f.md", due_date=self.TODAY, task_priority="Medium")
        _seed_task_full(conn, "t_none", "/vault/f.md", due_date=self.TODAY)
        result = _get_due_today_tasks(conn, self.TODAY)
        assert result[-1].task_priority is None

    def test_project_priority_emoji_high(self):
        conn = _conn()
        _seed_file(conn, "/vault/f.md")
        _seed_project_priority(conn, "p1", "P1", "High")
        conn.execute("INSERT OR REPLACE INTO file_project VALUES (?, ?)", ("/vault/f.md", "p1"))
        conn.commit()
        _seed_task_full(conn, "t1", "/vault/f.md", due_date=self.TODAY)
        result = _get_due_today_tasks(conn, self.TODAY)
        assert result[0].project_priority_emoji == "⏫"

    def test_task_without_project(self):
        conn = _conn()
        _seed_file(conn, "/vault/f.md")
        _seed_task_full(conn, "t1", "/vault/f.md", due_date=self.TODAY)
        result = _get_due_today_tasks(conn, self.TODAY)
        assert result[0].project_id is None
        assert result[0].project_title is None

    def test_excludes_tasks_on_deleted_files(self):
        conn = _conn()
        upsert_file(conn, {
            "file_path": "/vault/deleted.md",
            "sha256": "x", "file_ext": ".md", "created": 0,
            "modified": 0, "modified_date": "2026-05-01", "deleted": 1,
            "length": 0, "word_count": 0, "meta_data": None,
            "is_generated": 0, "needs_parsing": 0,
        })
        _seed_task_full(conn, "t1", "/vault/deleted.md", due_date=self.TODAY)
        result = _get_due_today_tasks(conn, self.TODAY)
        assert result == []


class TestDueTodayPage:
    TODAY = datetime.date.today().isoformat()

    def _cfg(self, tmp_path: Path) -> MatlockConfig:
        vault = tmp_path / "vault"
        vault.mkdir()
        return MatlockConfig(
            base_directory=vault,
            db_path=tmp_path / "matlock.db",
            output_directory=tmp_path / "_Matlock",
        )

    def test_due_today_file_created(self, tmp_path: Path):
        cfg = self._cfg(tmp_path)
        conn = get_connection(cfg.db_path)
        init_db(conn)
        run_report(cfg, conn)
        conn.close()
        assert (tmp_path / "_Matlock" / "001_Due_Today.md").exists()

    def test_due_today_created_by_dashboard_target(self, tmp_path: Path):
        cfg = self._cfg(tmp_path)
        conn = get_connection(cfg.db_path)
        init_db(conn)
        run_report(cfg, conn, target="dashboard")
        conn.close()
        assert (tmp_path / "_Matlock" / "001_Due_Today.md").exists()

    def test_due_today_not_created_by_projects_target(self, tmp_path: Path):
        cfg = self._cfg(tmp_path)
        conn = get_connection(cfg.db_path)
        init_db(conn)
        run_report(cfg, conn, target="projects")
        conn.close()
        assert not (tmp_path / "_Matlock" / "001_Due_Today.md").exists()

    def test_empty_page_shows_no_tasks_message(self, tmp_path: Path):
        cfg = self._cfg(tmp_path)
        conn = get_connection(cfg.db_path)
        init_db(conn)
        run_report(cfg, conn, target="dashboard")
        conn.close()
        content = (tmp_path / "_Matlock" / "001_Due_Today.md").read_text()
        assert "No tasks due today" in content

    def test_page_contains_task_text(self, tmp_path: Path):
        cfg = self._cfg(tmp_path)
        conn = get_connection(cfg.db_path)
        init_db(conn)
        upsert_file(conn, {
            "file_path": str(cfg.base_directory / "note.md"),
            "sha256": "abc", "file_ext": ".md", "created": 0,
            "modified": 0, "modified_date": self.TODAY, "deleted": 0,
            "length": 0, "word_count": 0, "meta_data": None,
            "is_generated": 0, "needs_parsing": 0,
        })
        upsert_task(conn, {
            "task_id": "t1",
            "file_path": str(cfg.base_directory / "note.md"),
            "parent_task_id": None,
            "created_date": self.TODAY,
            "due_date": self.TODAY,
            "est_comp_date": None,
            "act_comp_date": None,
            "checked": 0,
            "task_text": "Fix the thing",
            "overflow": 0,
            "headers": None,
            "attributes": json.dumps({"priority": "High"}),
            "errors": None,
            "twin_index": 0,
        })
        conn.commit()
        run_report(cfg, conn, target="dashboard")
        conn.close()
        content = (tmp_path / "_Matlock" / "001_Due_Today.md").read_text()
        assert "Fix the thing" in content
        assert "⏫ High Priority" in content
        assert "note" in content  # wikilink stem

    def test_dashboard_links_to_due_today(self, tmp_path: Path):
        cfg = self._cfg(tmp_path)
        conn = get_connection(cfg.db_path)
        init_db(conn)
        upsert_file(conn, {
            "file_path": str(cfg.base_directory / "note.md"),
            "sha256": "abc", "file_ext": ".md", "created": 0,
            "modified": 0, "modified_date": self.TODAY, "deleted": 0,
            "length": 0, "word_count": 0, "meta_data": None,
            "is_generated": 0, "needs_parsing": 0,
        })
        upsert_task(conn, {
            "task_id": "t1",
            "file_path": str(cfg.base_directory / "note.md"),
            "parent_task_id": None,
            "created_date": self.TODAY,
            "due_date": self.TODAY,
            "est_comp_date": None,
            "act_comp_date": None,
            "checked": 0,
            "task_text": "Some task",
            "overflow": 0,
            "headers": None,
            "attributes": None,
            "errors": None,
            "twin_index": 0,
        })
        conn.commit()
        run_report(cfg, conn, target="dashboard")
        conn.close()
        dashboard = (tmp_path / "_Matlock" / "000_Daily_Dashboard.md").read_text()
        assert "001_Due_Today" in dashboard


# ---------------------------------------------------------------------------
# _build_tiers
# ---------------------------------------------------------------------------


class TestBuildTiers:
    def _task(self, priority, estimate_secs=0):
        from types import SimpleNamespace
        t = SimpleNamespace(
            task_priority=priority,
            task_priority_rank={"High": 0, "Medium": 1, "Low": 2, None: 3}[priority],
            estimate_secs=estimate_secs,
        )
        return t

    def test_four_tiers_returned(self):
        tiers = _build_tiers([])
        assert len(tiers) == 4

    def test_tasks_grouped_by_priority(self):
        tasks = [self._task("High"), self._task("Low"), self._task(None)]
        tiers = _build_tiers(tasks)
        assert len(tiers[0]["tasks"]) == 1  # High
        assert len(tiers[1]["tasks"]) == 0  # Medium
        assert len(tiers[2]["tasks"]) == 1  # Low
        assert len(tiers[3]["tasks"]) == 1  # None

    def test_total_est_none_when_no_estimates(self):
        tasks = [self._task("High", 0)]
        tiers = _build_tiers(tasks)
        assert tiers[0]["total_est"] is None

    def test_total_est_sums_minutes(self):
        tasks = [self._task("High", 1200), self._task("High", 600)]
        tiers = _build_tiers(tasks)
        assert tiers[0]["total_est"] == "30m"

    def test_total_est_only_for_populated_tier(self):
        tasks = [self._task("High", 600)]
        tiers = _build_tiers(tasks)
        assert tiers[0]["total_est"] == "10m"
        assert tiers[1]["total_est"] is None  # Medium empty


# ---------------------------------------------------------------------------
# New task query functions
# ---------------------------------------------------------------------------

_YESTERDAY = (datetime.date.today() - datetime.timedelta(days=1)).isoformat()
_TODAY = datetime.date.today().isoformat()
_TOMORROW = (datetime.date.today() + datetime.timedelta(days=1)).isoformat()
_IN_5_DAYS = (datetime.date.today() + datetime.timedelta(days=5)).isoformat()
_IN_10_DAYS = (datetime.date.today() + datetime.timedelta(days=10)).isoformat()


class TestGetPastDueTasks:
    def test_empty_when_nothing_past_due(self):
        conn = _conn()
        assert _get_past_due_tasks(conn, _TODAY) == []

    def test_returns_past_due_task(self):
        conn = _conn()
        _seed_file(conn, "/vault/f.md")
        _seed_task_full(conn, "t1", "/vault/f.md", due_date=_YESTERDAY)
        result = _get_past_due_tasks(conn, _TODAY)
        assert len(result) == 1

    def test_excludes_today(self):
        conn = _conn()
        _seed_file(conn, "/vault/f.md")
        _seed_task_full(conn, "t1", "/vault/f.md", due_date=_TODAY)
        assert _get_past_due_tasks(conn, _TODAY) == []

    def test_excludes_checked(self):
        conn = _conn()
        _seed_file(conn, "/vault/f.md")
        _seed_task_full(conn, "t1", "/vault/f.md", due_date=_YESTERDAY, checked=1)
        assert _get_past_due_tasks(conn, _TODAY) == []

    def test_has_due_date_field(self):
        conn = _conn()
        _seed_file(conn, "/vault/f.md")
        _seed_task_full(conn, "t1", "/vault/f.md", due_date=_YESTERDAY)
        result = _get_past_due_tasks(conn, _TODAY)
        assert result[0].due_date == _YESTERDAY

    def test_sorted_by_priority_then_date(self):
        conn = _conn()
        _seed_file(conn, "/vault/f.md")
        # Two high-priority tasks, different dates
        two_days_ago = (datetime.date.today() - datetime.timedelta(days=2)).isoformat()
        _seed_task_full(conn, "t_old", "/vault/f.md", due_date=two_days_ago, task_priority="High")
        _seed_task_full(conn, "t_new", "/vault/f.md", due_date=_YESTERDAY, task_priority="High")
        _seed_task_full(conn, "t_med", "/vault/f.md", due_date=_YESTERDAY, task_priority="Medium")
        result = _get_past_due_tasks(conn, _TODAY)
        assert result[0].due_date == two_days_ago
        assert result[1].due_date == _YESTERDAY
        assert result[2].task_priority == "Medium"


class TestGetDueSoonTasks:
    def test_empty_when_nothing_due_soon(self):
        conn = _conn()
        assert _get_due_soon_tasks(conn, _TODAY) == []

    def test_returns_task_due_tomorrow(self):
        conn = _conn()
        _seed_file(conn, "/vault/f.md")
        _seed_task_full(conn, "t1", "/vault/f.md", due_date=_TOMORROW)
        result = _get_due_soon_tasks(conn, _TODAY)
        assert len(result) == 1

    def test_returns_task_due_in_7_days(self):
        conn = _conn()
        _seed_file(conn, "/vault/f.md")
        _seed_task_full(conn, "t1", "/vault/f.md", due_date=_IN_5_DAYS)
        result = _get_due_soon_tasks(conn, _TODAY)
        assert len(result) == 1

    def test_excludes_today(self):
        conn = _conn()
        _seed_file(conn, "/vault/f.md")
        _seed_task_full(conn, "t1", "/vault/f.md", due_date=_TODAY)
        assert _get_due_soon_tasks(conn, _TODAY) == []

    def test_excludes_tasks_beyond_7_days(self):
        conn = _conn()
        _seed_file(conn, "/vault/f.md")
        _seed_task_full(conn, "t1", "/vault/f.md", due_date=_IN_10_DAYS)
        assert _get_due_soon_tasks(conn, _TODAY) == []

    def test_excludes_past_due(self):
        conn = _conn()
        _seed_file(conn, "/vault/f.md")
        _seed_task_full(conn, "t1", "/vault/f.md", due_date=_YESTERDAY)
        assert _get_due_soon_tasks(conn, _TODAY) == []


class TestGetFutureDueTasks:
    def test_empty_when_nothing_future(self):
        conn = _conn()
        assert _get_future_due_tasks(conn, _TODAY) == []

    def test_returns_task_due_in_10_days(self):
        conn = _conn()
        _seed_file(conn, "/vault/f.md")
        _seed_task_full(conn, "t1", "/vault/f.md", due_date=_IN_10_DAYS)
        result = _get_future_due_tasks(conn, _TODAY)
        assert len(result) == 1

    def test_excludes_tasks_within_7_days(self):
        conn = _conn()
        _seed_file(conn, "/vault/f.md")
        _seed_task_full(conn, "t1", "/vault/f.md", due_date=_IN_5_DAYS)
        assert _get_future_due_tasks(conn, _TODAY) == []

    def test_excludes_today(self):
        conn = _conn()
        _seed_file(conn, "/vault/f.md")
        _seed_task_full(conn, "t1", "/vault/f.md", due_date=_TODAY)
        assert _get_future_due_tasks(conn, _TODAY) == []


class TestGetNotDueTasks:
    def test_empty_when_all_have_dates(self):
        conn = _conn()
        _seed_file(conn, "/vault/f.md")
        _seed_task_full(conn, "t1", "/vault/f.md", due_date=_TODAY)
        assert _get_not_due_tasks(conn) == []

    def test_returns_task_with_no_date(self):
        conn = _conn()
        _seed_file(conn, "/vault/f.md")
        _seed_task_full(conn, "t1", "/vault/f.md")
        result = _get_not_due_tasks(conn)
        assert len(result) == 1
        assert result[0].due_date is None

    def test_excludes_checked(self):
        conn = _conn()
        _seed_file(conn, "/vault/f.md")
        _seed_task_full(conn, "t1", "/vault/f.md", checked=1)
        assert _get_not_due_tasks(conn) == []


# ---------------------------------------------------------------------------
# New task view pages
# ---------------------------------------------------------------------------


class TestTaskViewPages:
    """Smoke tests that the 4 new task-view pages are created by run_report."""

    TODAY = datetime.date.today().isoformat()

    def _cfg(self, tmp_path: Path) -> MatlockConfig:
        vault = tmp_path / "vault"
        vault.mkdir()
        return MatlockConfig(
            base_directory=vault,
            db_path=tmp_path / "matlock.db",
            output_directory=tmp_path / "_Matlock",
        )

    def test_all_task_view_files_created(self, tmp_path: Path):
        cfg = self._cfg(tmp_path)
        conn = get_connection(cfg.db_path)
        init_db(conn)
        run_report(cfg, conn, target="dashboard")
        conn.close()
        for name in ("002_Past_Due.md", "003_Due_Soon.md", "004_Future_Due.md", "005_Not_Due.md"):
            assert (tmp_path / "_Matlock" / name).exists(), f"Missing {name}"

    def test_past_due_shows_overdue_task(self, tmp_path: Path):
        cfg = self._cfg(tmp_path)
        conn = get_connection(cfg.db_path)
        init_db(conn)
        yesterday = (datetime.date.today() - datetime.timedelta(days=1)).isoformat()
        fp = str(cfg.base_directory / "note.md")
        upsert_file(conn, {"file_path": fp, "sha256": "x", "file_ext": ".md",
                            "created": 0, "modified": 0, "modified_date": self.TODAY,
                            "deleted": 0, "length": 0, "word_count": 0,
                            "meta_data": None, "is_generated": 0, "needs_parsing": 0})
        upsert_task(conn, {"task_id": "t1", "file_path": fp, "parent_task_id": None,
                           "created_date": self.TODAY, "due_date": yesterday,
                           "est_comp_date": None, "act_comp_date": None, "checked": 0,
                           "task_text": "Overdue thing", "overflow": 0, "headers": None,
                           "attributes": None, "errors": None, "twin_index": 0})
        conn.commit()
        run_report(cfg, conn, target="dashboard")
        conn.close()
        content = (tmp_path / "_Matlock" / "002_Past_Due.md").read_text()
        assert "Overdue thing" in content
        assert yesterday in content

    def test_due_soon_shows_upcoming_task(self, tmp_path: Path):
        cfg = self._cfg(tmp_path)
        conn = get_connection(cfg.db_path)
        init_db(conn)
        in_3 = (datetime.date.today() + datetime.timedelta(days=3)).isoformat()
        fp = str(cfg.base_directory / "note.md")
        upsert_file(conn, {"file_path": fp, "sha256": "x", "file_ext": ".md",
                            "created": 0, "modified": 0, "modified_date": self.TODAY,
                            "deleted": 0, "length": 0, "word_count": 0,
                            "meta_data": None, "is_generated": 0, "needs_parsing": 0})
        upsert_task(conn, {"task_id": "t1", "file_path": fp, "parent_task_id": None,
                           "created_date": self.TODAY, "due_date": in_3,
                           "est_comp_date": None, "act_comp_date": None, "checked": 0,
                           "task_text": "Coming up soon", "overflow": 0, "headers": None,
                           "attributes": None, "errors": None, "twin_index": 0})
        conn.commit()
        run_report(cfg, conn, target="dashboard")
        conn.close()
        content = (tmp_path / "_Matlock" / "003_Due_Soon.md").read_text()
        assert "Coming up soon" in content

    def test_not_due_shows_undated_task(self, tmp_path: Path):
        cfg = self._cfg(tmp_path)
        conn = get_connection(cfg.db_path)
        init_db(conn)
        fp = str(cfg.base_directory / "note.md")
        upsert_file(conn, {"file_path": fp, "sha256": "x", "file_ext": ".md",
                            "created": 0, "modified": 0, "modified_date": self.TODAY,
                            "deleted": 0, "length": 0, "word_count": 0,
                            "meta_data": None, "is_generated": 0, "needs_parsing": 0})
        upsert_task(conn, {"task_id": "t1", "file_path": fp, "parent_task_id": None,
                           "created_date": self.TODAY, "due_date": None,
                           "est_comp_date": None, "act_comp_date": None, "checked": 0,
                           "task_text": "No date task", "overflow": 0, "headers": None,
                           "attributes": None, "errors": None, "twin_index": 0})
        conn.commit()
        run_report(cfg, conn, target="dashboard")
        conn.close()
        content = (tmp_path / "_Matlock" / "005_Not_Due.md").read_text()
        assert "No date task" in content

    def test_estimate_total_in_tier_heading(self, tmp_path: Path):
        cfg = self._cfg(tmp_path)
        conn = get_connection(cfg.db_path)
        init_db(conn)
        fp = str(cfg.base_directory / "note.md")
        upsert_file(conn, {"file_path": fp, "sha256": "x", "file_ext": ".md",
                            "created": 0, "modified": 0, "modified_date": self.TODAY,
                            "deleted": 0, "length": 0, "word_count": 0,
                            "meta_data": None, "is_generated": 0, "needs_parsing": 0})
        upsert_task(conn, {"task_id": "t1", "file_path": fp, "parent_task_id": None,
                           "created_date": self.TODAY, "due_date": self.TODAY,
                           "est_comp_date": None, "act_comp_date": None, "checked": 0,
                           "task_text": "Estimated task", "overflow": 0, "headers": None,
                           "attributes": json.dumps({"priority": "High", "estimate": 1800}),
                           "errors": None, "twin_index": 0})
        conn.commit()
        run_report(cfg, conn, target="dashboard")
        conn.close()
        content = (tmp_path / "_Matlock" / "001_Due_Today.md").read_text()
        assert "30m" in content
        assert "⏫ High Priority" in content

    def test_dashboard_has_task_views_nav(self, tmp_path: Path):
        cfg = self._cfg(tmp_path)
        conn = get_connection(cfg.db_path)
        init_db(conn)
        run_report(cfg, conn, target="dashboard")
        conn.close()
        content = (tmp_path / "_Matlock" / "000_Daily_Dashboard.md").read_text()
        assert "002_Past_Due" in content
        assert "003_Due_Soon" in content
        assert "004_Future_Due" in content
        assert "005_Not_Due" in content
