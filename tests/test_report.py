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
        """Empty DB: only the dashboard is written (no projects, no history)."""
        conn = _conn()
        cfg = _make_config(tmp_path)
        result = run_report(cfg, conn)
        assert result.files_written == 1  # just dashboard

    def test_idempotent_second_run(self, tmp_path: Path):
        conn = _conn()
        cfg = _make_config(tmp_path)
        run_report(cfg, conn)
        result2 = run_report(cfg, conn)
        assert result2.files_written == 1
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
        assert not (cfg.output_directory / "Projects").exists()
        assert not (cfg.output_directory / "History").exists()
        assert result.files_written == 1

    def test_target_projects_writes_project_page(self, tmp_path: Path):
        conn, cfg = self._setup(tmp_path)
        result = run_report(cfg, conn, target="projects")
        assert (cfg.output_directory / "Projects" / "p1.md").exists()
        assert not (cfg.output_directory / "000_Daily_Dashboard.md").exists()

    def test_target_history_writes_history_page(self, tmp_path: Path):
        conn, cfg = self._setup(tmp_path)
        result = run_report(cfg, conn, target="history")
        assert (cfg.output_directory / "History" / "2026-04-20.md").exists()
        assert not (cfg.output_directory / "000_Daily_Dashboard.md").exists()
        assert result.files_written == 1

    def test_target_all_writes_all(self, tmp_path: Path):
        conn, cfg = self._setup(tmp_path)
        result = run_report(cfg, conn, target="all")
        assert (cfg.output_directory / "000_Daily_Dashboard.md").exists()
        assert (cfg.output_directory / "Projects" / "p1.md").exists()
        assert (cfg.output_directory / "History" / "2026-04-20.md").exists()


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
        assert (cfg.output_directory / "History" / "2026-04-20.md").exists()
        assert (cfg.output_directory / "History" / "2026-04-21.md").exists()

    def test_history_page_content_contains_date(self, tmp_path: Path):
        conn = _conn()
        _seed_metric(conn, "2026-04-20", None, completed=1)
        cfg = _make_config(tmp_path)
        run_report(cfg, conn, target="history")
        content = (cfg.output_directory / "History" / "2026-04-20.md").read_text()
        assert "2026-04-20" in content

    def test_history_page_day_of_week(self, tmp_path: Path):
        conn = _conn()
        _seed_metric(conn, "2026-04-20", None, completed=1)  # Monday
        cfg = _make_config(tmp_path)
        run_report(cfg, conn, target="history")
        content = (cfg.output_directory / "History" / "2026-04-20.md").read_text()
        assert "Monday" in content

    def test_history_page_registered_as_generated(self, tmp_path: Path):
        conn = _conn()
        _seed_metric(conn, "2026-04-20", None, completed=1)
        cfg = _make_config(tmp_path)
        run_report(cfg, conn, target="history")
        path = str(cfg.output_directory / "History" / "2026-04-20.md")
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
        content = (cfg.output_directory / "History" / "2026-04-20.md").read_text()
        assert "Unassigned" in content

    def test_history_breakdown_shows_project(self, tmp_path: Path):
        conn = _conn()
        _seed_project(conn, "p1", "My Project")
        _seed_metric(conn, "2026-04-20", "p1", completed=2)
        cfg = _make_config(tmp_path)
        run_report(cfg, conn, target="history")
        content = (cfg.output_directory / "History" / "2026-04-20.md").read_text()
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
