"""
Stage V — Report.

Query the database and render Jinja2 Markdown dashboards into
``config.output_directory`` (``_Matlock/`` by default).

Public API
----------
run_report(config, conn, target="all", project_id=None) -> ReportResult
"""

from __future__ import annotations

import datetime
import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import jinja2

from matlock.config import MatlockConfig
from matlock.db import upsert_file

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_VALID_TARGETS = {"all", "dashboard", "projects", "history"}

_HEATMAP_EMOJI = {0: "⬜", 1: "🟩", 2: "🟩", 3: "🟩", 4: "🟦", 5: "🟦", 6: "🟦", 7: "🟦"}

_DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def _heatmap_emoji(count: int) -> str:
    if count >= 8:
        return "🟪"
    return _HEATMAP_EMOJI.get(count, "⬜")


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


@dataclass
class ReportResult:
    files_written: int
    target: str
    files_deleted: int = 0


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def run_report(
    config: MatlockConfig,
    conn: sqlite3.Connection,
    target: str = "all",
    project_id: str | None = None,
    force: bool = False,
) -> ReportResult:
    """Render Jinja2 Markdown dashboards and write them to ``output_directory``.

    Parameters
    ----------
    config:
        Validated ``MatlockConfig`` instance.
    conn:
        Open SQLite connection (WAL mode, FK on).
    target:
        Which dashboards to write: ``"all"``, ``"dashboard"``,
        ``"projects"``, or ``"history"``.
    project_id:
        When provided, write only ``_Matlock/Projects/<project_id>.md``
        (implies ``target="projects"`` for just that one file).
    force:
        When ``True``, regenerate all reports and delete any generated
        files that are no longer valid (not produced by this run).

    Returns
    -------
    ReportResult
        ``files_written`` is the total number of files written this run.
    """
    if target not in _VALID_TARGETS:
        raise ValueError(
            f"Invalid target {target!r}. Must be one of: {sorted(_VALID_TARGETS)}"
        )

    out_dir = config.output_directory
    out_dir.mkdir(parents=True, exist_ok=True)

    env = _build_jinja_env()
    today_str = datetime.date.today().isoformat()
    files_written = 0

    files_deleted = 0

    if project_id is not None:
        # Targeted single-project regeneration
        files_written += _render_single_project(env, conn, out_dir, project_id, today_str)
    else:
        if target in ("all", "dashboard"):
            files_written += _render_dashboard(env, conn, out_dir, today_str)
        if target in ("all", "projects"):
            files_written += _render_all_projects(env, conn, out_dir, today_str)
            files_written += _render_all_super_projects(env, conn, out_dir, today_str)
            if not force:
                # Incremental cleanup: remove stale project/super-project files
                current_proj_ids = {
                    r["project_id"]
                    for r in conn.execute("SELECT project_id FROM project").fetchall()
                }
                current_sp_ids = {
                    r["super_project_id"]
                    for r in conn.execute("SELECT super_project_id FROM super_project").fetchall()
                }
                files_deleted += _cleanup_stale_report_files(conn, out_dir / "Projects", current_proj_ids)
                files_deleted += _cleanup_stale_report_files(conn, out_dir / "SuperProjects", current_sp_ids)
        if target in ("all", "history"):
            files_written += _render_history(env, conn, out_dir, today_str)

        if force:
            # Full purge: delete every generated file not produced by this run
            expected = _compute_expected_paths(conn, out_dir, target)
            files_deleted += _purge_stale_generated(conn, out_dir, expected)

    conn.commit()
    return ReportResult(files_written=files_written, target=target, files_deleted=files_deleted)


# ---------------------------------------------------------------------------
# Jinja2 environment
# ---------------------------------------------------------------------------


def _build_jinja_env() -> jinja2.Environment:
    templates_dir = Path(__file__).parent.parent / "templates"
    env = jinja2.Environment(
        loader=jinja2.FileSystemLoader(str(templates_dir)),
        autoescape=False,
        keep_trailing_newline=True,
    )
    env.filters["basename"] = os.path.basename
    return env


# ---------------------------------------------------------------------------
# File registration helper
# ---------------------------------------------------------------------------


def _register_file(conn: sqlite3.Connection, file_path: Path, today_str: str) -> None:
    """Register a generated file in the ``file`` table (is_generated=1)."""
    upsert_file(
        conn,
        {
            "file_path": str(file_path),
            "sha256": None,
            "file_ext": file_path.suffix or "",
            "created": None,
            "modified": None,
            "modified_date": today_str,
            "deleted": 0,
            "length": 0,
            "word_count": 0,
            "meta_data": None,
            "is_generated": 1,
            "needs_parsing": 0,
        },
    )


def _write_file(
    conn: sqlite3.Connection,
    out_path: Path,
    content: str,
    today_str: str,
) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(content, encoding="utf-8")
    _register_file(conn, out_path, today_str)


# ---------------------------------------------------------------------------
# Streak helpers
# ---------------------------------------------------------------------------


def _calc_streak(conn: sqlite3.Connection) -> tuple[int, int, str | None]:
    """Return (current_streak, longest_streak, longest_streak_end).

    A date is "active" if the sum of tasks_completed_count across all
    project_id rows (including NULL) is > 0.
    """
    rows = conn.execute(
        "SELECT metric_date, SUM(tasks_completed_count) AS total"
        " FROM daily_metric"
        " GROUP BY metric_date"
        " ORDER BY metric_date ASC"
    ).fetchall()

    if not rows:
        return 0, 0, None

    # Build sorted list of (date, is_active)
    dated: list[tuple[datetime.date, bool]] = []
    for row in rows:
        d = datetime.date.fromisoformat(row["metric_date"])
        dated.append((d, (row["total"] or 0) > 0))

    longest = 0
    longest_end: str | None = None
    current = 0
    run = 0
    run_end: datetime.date | None = None

    prev_date: datetime.date | None = None
    for d, active in dated:
        # Gap detection — reset run if days are not consecutive
        if prev_date is not None and (d - prev_date).days > 1:
            run = 0
        if active:
            run += 1
            run_end = d
        else:
            run = 0
        if run > longest:
            longest = run
            longest_end = run_end.isoformat() if run_end else None
        prev_date = d

    # Current streak: how many consecutive active days ending at last date
    current = 0
    prev_d: datetime.date | None = None
    for d, active in reversed(dated):
        if not active:
            break
        if prev_d is not None and (prev_d - d).days > 1:
            break  # gap detected
        current += 1
        prev_d = d

    return current, longest, longest_end


# ---------------------------------------------------------------------------
# Heatmap helper
# ---------------------------------------------------------------------------


def _calc_heatmap(conn: sqlite3.Connection) -> dict[str, list[str]]:
    """Return day-name → list of 5 emoji (W-4 … Current).

    Anchored on today. Each cell = total tasks_completed_count for that
    calendar day summed across all projects.
    """
    today = datetime.date.today()
    # Build the 35-day grid (5 weeks × 7 days), Monday-aligned
    today_weekday = today.weekday()  # 0=Mon, 6=Sun
    # Start of current week (Mon)
    week_start = today - datetime.timedelta(days=today_weekday)
    # Go back 4 more weeks
    grid_start = week_start - datetime.timedelta(weeks=4)

    # Fetch sums for the date range
    start_str = grid_start.isoformat()
    end_str = today.isoformat()
    rows = conn.execute(
        "SELECT metric_date, SUM(tasks_completed_count) AS total"
        " FROM daily_metric"
        " WHERE metric_date >= ? AND metric_date <= ?"
        " GROUP BY metric_date",
        (start_str, end_str),
    ).fetchall()
    totals: dict[str, int] = {r["metric_date"]: (r["total"] or 0) for r in rows}

    # Build 5 weeks × 7 days matrix
    # result[day_index][week_index] where day_index 0=Mon, week_index 0=W-4
    matrix: list[list[str]] = []
    for day_idx in range(7):
        week_emojis: list[str] = []
        for week_idx in range(5):
            date = grid_start + datetime.timedelta(days=week_idx * 7 + day_idx)
            count = totals.get(date.isoformat(), 0)
            week_emojis.append(_heatmap_emoji(count))
        matrix.append(week_emojis)

    return {name: matrix[i] for i, name in enumerate(_DAY_NAMES)}


# ---------------------------------------------------------------------------
# Dashboard renderer
# ---------------------------------------------------------------------------


def _calc_yesterday_stats(
    conn: sqlite3.Connection,
) -> tuple[int, str, dict[str, int]]:
    """Return (yesterday_completed_count, yesterday_date, yesterday_by_project)."""
    yesterday = (datetime.date.today() - datetime.timedelta(days=1)).isoformat()
    rows = conn.execute(
        "SELECT project_id, tasks_completed_count"
        " FROM daily_metric"
        " WHERE metric_date = ?",
        (yesterday,),
    ).fetchall()
    total = sum((r["tasks_completed_count"] or 0) for r in rows)
    by_project: dict[str, int] = {}
    for r in rows:
        if r["project_id"] is not None and (r["tasks_completed_count"] or 0) > 0:
            by_project[r["project_id"]] = r["tasks_completed_count"]
    return total, yesterday, by_project


def _get_due_tasks(conn: sqlite3.Connection) -> tuple[list[Any], list[Any]]:
    """Return (past_due_tasks, due_today_tasks) as lists of sqlite3.Row."""
    today = datetime.date.today().isoformat()
    past_due = conn.execute(
        "SELECT t.task_text, t.due_date, t.file_path"
        " FROM task t"
        " JOIN file f ON t.file_path = f.file_path"
        " WHERE t.checked = 0 AND t.due_date IS NOT NULL AND t.due_date < ?"
        " AND f.deleted = 0",
        (today,),
    ).fetchall()
    due_today = conn.execute(
        "SELECT t.task_text, t.due_date, t.file_path"
        " FROM task t"
        " JOIN file f ON t.file_path = f.file_path"
        " WHERE t.checked = 0 AND t.due_date = ?"
        " AND f.deleted = 0",
        (today,),
    ).fetchall()
    return past_due, due_today


def _render_dashboard(
    env: jinja2.Environment,
    conn: sqlite3.Connection,
    out_dir: Path,
    today_str: str,
) -> int:
    current_streak, longest_streak, longest_streak_end = _calc_streak(conn)
    heatmap = _calc_heatmap(conn)
    yesterday_count, yesterday_date, yesterday_by_project = _calc_yesterday_stats(conn)
    past_due, due_today = _get_due_tasks(conn)

    super_projects = conn.execute(
        "SELECT super_project_id AS id, title FROM super_project ORDER BY super_project_id"
    ).fetchall()
    projects = conn.execute(
        "SELECT project_id AS id, title FROM project ORDER BY project_id"
    ).fetchall()

    generated_at = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    content = env.get_template("daily_dashboard.md.j2").render(
        generated_at=generated_at,
        current_streak=current_streak,
        longest_streak=longest_streak,
        longest_streak_end=longest_streak_end,
        heatmap=heatmap,
        yesterday_completed_count=yesterday_count,
        yesterday_date=yesterday_date,
        yesterday_by_project=yesterday_by_project,
        past_due_tasks=past_due,
        due_today_tasks=due_today,
        super_projects=super_projects,
        projects=projects,
    )
    _write_file(conn, out_dir / "000_Daily_Dashboard.md", content, today_str)
    return 1


# ---------------------------------------------------------------------------
# Project stats helper
# ---------------------------------------------------------------------------


def _calc_project_stats(conn: sqlite3.Connection, proj_id: str) -> SimpleNamespace:
    today = datetime.date.today().isoformat()
    seven_days_ago = (datetime.date.today() - datetime.timedelta(days=7)).isoformat()

    def _mins(rows_: list, col: str = "attributes") -> int:
        total = 0
        for r in rows_:
            try:
                import json
                attrs = json.loads(r[col] or "{}")
                total += int(attrs.get("estimate", 0) or 0) // 60
            except Exception:
                pass
        return total

    file_paths = [
        r["file_path"]
        for r in conn.execute(
            "SELECT file_path FROM file_project WHERE project_id = ?", (proj_id,)
        ).fetchall()
    ]
    if not file_paths:
        return SimpleNamespace(
            open_count=0,
            open_minutes=0,
            completed_count=0,
            completed_minutes=0,
            past_due_count=0,
            past_due_minutes=0,
        )

    placeholders = ",".join("?" * len(file_paths))
    open_rows = conn.execute(
        f"SELECT attributes FROM task WHERE checked = 0 AND file_path IN ({placeholders})",
        file_paths,
    ).fetchall()
    completed_rows = conn.execute(
        f"SELECT attributes FROM task WHERE checked = 1 AND file_path IN ({placeholders})",
        file_paths,
    ).fetchall()
    past_due_rows = conn.execute(
        f"SELECT attributes FROM task"
        f" WHERE checked = 0 AND due_date < ? AND file_path IN ({placeholders})",
        [today] + file_paths,
    ).fetchall()

    return SimpleNamespace(
        open_count=len(open_rows),
        open_minutes=_mins(open_rows),
        completed_count=len(completed_rows),
        completed_minutes=_mins(completed_rows),
        past_due_count=len(past_due_rows),
        past_due_minutes=_mins(past_due_rows),
    )


def _render_project_page(
    env: jinja2.Environment,
    conn: sqlite3.Connection,
    out_dir: Path,
    project_row: sqlite3.Row,
    today_str: str,
) -> None:
    proj_id = project_row["project_id"]
    today = today_str
    seven_days_ago = (datetime.date.today() - datetime.timedelta(days=7)).isoformat()

    file_paths = [
        r["file_path"]
        for r in conn.execute(
            "SELECT file_path FROM file_project WHERE project_id = ?", (proj_id,)
        ).fetchall()
    ]

    stats = _calc_project_stats(conn, proj_id)

    if file_paths:
        ph = ",".join("?" * len(file_paths))
        past_due_tasks = conn.execute(
            f"SELECT task_text, due_date, file_path FROM task"
            f" WHERE checked = 0 AND due_date < ? AND file_path IN ({ph})",
            [today] + file_paths,
        ).fetchall()
        open_tasks = conn.execute(
            f"SELECT task_text, file_path FROM task"
            f" WHERE checked = 0 AND file_path IN ({ph})"
            f" ORDER BY file_path",
            file_paths,
        ).fetchall()
        recently_completed = conn.execute(
            f"SELECT task_text, file_path FROM task"
            f" WHERE checked = 1 AND act_comp_date >= ? AND file_path IN ({ph})",
            [seven_days_ago] + file_paths,
        ).fetchall()
    else:
        past_due_tasks = []
        open_tasks = []
        recently_completed = []

    # Group open tasks by file
    open_by_file: dict[str, list[Any]] = {}
    for t in open_tasks:
        open_by_file.setdefault(t["file_path"], []).append(t)

    content = env.get_template("project.md.j2").render(
        project=project_row,
        stats=stats,
        past_due_tasks=past_due_tasks,
        open_by_file=open_by_file,
        recently_completed=recently_completed,
        source_files=file_paths,
    )
    out_path = out_dir / "Projects" / f"{proj_id}.md"
    _write_file(conn, out_path, content, today_str)


def _render_single_project(
    env: jinja2.Environment,
    conn: sqlite3.Connection,
    out_dir: Path,
    project_id: str,
    today_str: str,
) -> int:
    row = conn.execute(
        "SELECT * FROM project WHERE project_id = ?", (project_id,)
    ).fetchone()
    if row is None:
        return 0
    _render_project_page(env, conn, out_dir, row, today_str)
    return 1


def _render_all_projects(
    env: jinja2.Environment,
    conn: sqlite3.Connection,
    out_dir: Path,
    today_str: str,
) -> int:
    rows = conn.execute("SELECT * FROM project ORDER BY project_id").fetchall()
    for row in rows:
        _render_project_page(env, conn, out_dir, row, today_str)
    return len(rows)


# ---------------------------------------------------------------------------
# Report cleanup helper
# ---------------------------------------------------------------------------


def _cleanup_stale_report_files(
    conn: sqlite3.Connection,
    report_subdir: Path,
    current_ids: set[str],
) -> int:
    """Delete generated .md files in *report_subdir* whose stem is not in *current_ids*.

    Also removes the corresponding row from the ``file`` table (generated files are
    stored with their absolute path as ``file_path``).

    Returns the number of files deleted.
    """
    if not report_subdir.exists():
        return 0
    removed = 0
    for md_file in report_subdir.glob("*.md"):
        if md_file.stem not in current_ids:
            conn.execute("DELETE FROM file WHERE file_path = ?", (str(md_file),))
            md_file.unlink()
            removed += 1
    return removed


def _compute_expected_paths(
    conn: sqlite3.Connection,
    out_dir: Path,
    target: str,
) -> set[Path]:
    """Return the complete set of paths that should exist after a run with *target*."""
    expected: set[Path] = set()
    if target in ("all", "dashboard"):
        expected.add(out_dir / "000_Daily_Dashboard.md")
    if target in ("all", "projects"):
        for r in conn.execute("SELECT project_id FROM project").fetchall():
            expected.add(out_dir / "Projects" / f"{r['project_id']}.md")
        for r in conn.execute("SELECT super_project_id FROM super_project").fetchall():
            expected.add(out_dir / "SuperProjects" / f"{r['super_project_id']}.md")
    if target in ("all", "history"):
        for r in conn.execute("SELECT DISTINCT metric_date FROM daily_metric").fetchall():
            expected.add(out_dir / "History" / f"{r['metric_date']}.md")
    return expected


def _purge_stale_generated(
    conn: sqlite3.Connection,
    out_dir: Path,
    expected_paths: set[Path],
) -> int:
    """Delete all generated files under *out_dir* not in *expected_paths*.

    Handles both DB-tracked files (``is_generated = 1``) and fully orphaned
    ``.md`` files on disk that have no DB row.

    Returns the number of files deleted.
    """
    removed = 0
    out_prefix = str(out_dir)

    # 1. DB-tracked generated files not in the expected set
    rows = conn.execute(
        "SELECT file_path FROM file WHERE is_generated = 1"
    ).fetchall()
    for row in rows:
        fp = Path(row["file_path"])
        if str(fp).startswith(out_prefix) and fp not in expected_paths:
            conn.execute("DELETE FROM file WHERE file_path = ?", (str(fp),))
            if fp.exists():
                fp.unlink()
            removed += 1

    # 2. Orphaned .md files on disk not tracked in DB and not in expected set
    if out_dir.exists():
        for md_file in out_dir.rglob("*.md"):
            if md_file not in expected_paths:
                tracked = conn.execute(
                    "SELECT 1 FROM file WHERE file_path = ?", (str(md_file),)
                ).fetchone()
                if tracked is None:
                    md_file.unlink()
                    removed += 1

    return removed


# ---------------------------------------------------------------------------
# Super-project renderer
# ---------------------------------------------------------------------------


def _status_for(stats: SimpleNamespace) -> tuple[str, str]:
    if stats.past_due_count > 0:
        return "🔴", "At Risk"
    if stats.open_count > 0:
        return "🟡", "On Track"
    return "🟢", "Complete"


def _render_all_super_projects(
    env: jinja2.Environment,
    conn: sqlite3.Connection,
    out_dir: Path,
    today_str: str,
) -> int:
    sp_rows = conn.execute(
        "SELECT * FROM super_project ORDER BY super_project_id"
    ).fetchall()
    count = 0
    for sp in sp_rows:
        sp_id = sp["super_project_id"]
        child_rows = conn.execute(
            "SELECT * FROM project WHERE super_project_id = ? ORDER BY project_id",
            (sp_id,),
        ).fetchall()

        child_projects = []
        critical_tasks: list[Any] = []
        today = today_str

        for p in child_rows:
            stats = _calc_project_stats(conn, p["project_id"])
            status_emoji, status_label = _status_for(stats)
            child_projects.append(
                SimpleNamespace(
                    id=p["project_id"],
                    title=p["title"],
                    priority=p["priority"] or "",
                    due_date=p["due_date"],
                    stats=stats,
                    status_emoji=status_emoji,
                    status_label=status_label,
                )
            )
            # Aggregate critical (past-due) tasks
            file_paths = [
                r["file_path"]
                for r in conn.execute(
                    "SELECT file_path FROM file_project WHERE project_id = ?",
                    (p["project_id"],),
                ).fetchall()
            ]
            if file_paths:
                ph = ",".join("?" * len(file_paths))
                pd = conn.execute(
                    f"SELECT task_text, file_path FROM task"
                    f" WHERE checked = 0 AND due_date < ? AND file_path IN ({ph})",
                    [today] + file_paths,
                ).fetchall()
                for t in pd:
                    critical_tasks.append(
                        SimpleNamespace(
                            project_title=p["title"] or p["project_id"],
                            task_text=t["task_text"],
                            file_path=t["file_path"],
                        )
                    )

        content = env.get_template("super_project.md.j2").render(
            super_project=sp,
            child_projects=child_projects,
            critical_tasks=critical_tasks,
        )
        out_path = out_dir / "SuperProjects" / f"{sp_id}.md"
        _write_file(conn, out_path, content, today_str)
        count += 1
    return count


# ---------------------------------------------------------------------------
# History renderer
# ---------------------------------------------------------------------------


def _render_history(
    env: jinja2.Environment,
    conn: sqlite3.Connection,
    out_dir: Path,
    today_str: str,
) -> int:
    dates = conn.execute(
        "SELECT DISTINCT metric_date FROM daily_metric ORDER BY metric_date"
    ).fetchall()
    count = 0
    for date_row in dates:
        metric_date = date_row["metric_date"]
        _render_history_page(env, conn, out_dir, metric_date, today_str)
        count += 1
    return count


def _render_history_page(
    env: jinja2.Environment,
    conn: sqlite3.Connection,
    out_dir: Path,
    metric_date: str,
    today_str: str,
) -> None:
    d = datetime.date.fromisoformat(metric_date)
    day_of_week = d.strftime("%A")
    prev_date = (d - datetime.timedelta(days=1)).isoformat()

    dm_rows = conn.execute(
        "SELECT * FROM daily_metric WHERE metric_date = ? ORDER BY project_id",
        (metric_date,),
    ).fetchall()

    # Cross-date totals (sum across all project rows)
    totals = SimpleNamespace(
        tasks_completed_count=sum(r["tasks_completed_count"] or 0 for r in dm_rows),
        tasks_completed_minutes=sum(r["tasks_completed_minutes"] or 0 for r in dm_rows),
        tasks_created_count=sum(r["tasks_created_count"] or 0 for r in dm_rows),
        tasks_created_minutes=sum(r["tasks_created_minutes"] or 0 for r in dm_rows),
        files_modified_count=sum(r["files_modified_count"] or 0 for r in dm_rows),
    )

    # Per-project breakdown rows
    project_rows: list[SimpleNamespace] = []
    for r in dm_rows:
        if r["project_id"] is not None:
            proj_row = conn.execute(
                "SELECT * FROM project WHERE project_id = ?", (r["project_id"],)
            ).fetchone()
            proj = (
                SimpleNamespace(id=proj_row["project_id"], title=proj_row["title"])
                if proj_row
                else None
            )
        else:
            proj = None
        project_rows.append(
            SimpleNamespace(
                project=proj,
                tasks_completed_count=r["tasks_completed_count"] or 0,
                tasks_completed_minutes=r["tasks_completed_minutes"] or 0,
                tasks_past_due_count=r["tasks_past_due_count"] or 0,
            )
        )

    content = env.get_template("daily_history.md.j2").render(
        metric_date=metric_date,
        day_of_week=day_of_week,
        totals=totals,
        project_rows=project_rows,
        prev_date=prev_date,
    )
    out_path = out_dir / "History" / f"{metric_date}.md"
    _write_file(conn, out_path, content, today_str)
