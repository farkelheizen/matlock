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
import json
import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import jinja2

from matlock.config import MatlockConfig
from matlock.db import upsert_file
from matlock.stages.rollup import run_rollup

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_VALID_TARGETS = {"all", "dashboard", "projects", "history"}

_PRIORITY_RANK: dict[str | None, int] = {"High": 0, "Medium": 1, "Low": 2, None: 3}

_PRIORITY_EMOJI: dict[str | None, str] = {
    "High": "⏫",
    "Medium": "🔼",
    "Low": "🔽",
    None: "",
}

_HEATMAP_EMOJI = {0: "⬜", 1: "🟩", 2: "🟩", 3: "🟩", 4: "🟦", 5: "🟦", 6: "🟦", 7: "🟦"}

_DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

_HOME_PAGE = "Home.md"
_DUE_TODAY_PAGE = "Due Today.md"
_PAST_DUE_PAGE = "Past Due.md"
_DUE_SOON_PAGE = "Due Soon.md"
_FUTURE_DUE_PAGE = "Future Due.md"
_NOT_DUE_PAGE = "Not Due.md"
_WARNINGS_PAGE = "Warnings.md"
_SUPER_PROJECTS_INDEX_PAGE = "Super Projects.md"
_PROJECTS_INDEX_PAGE = "Projects.md"
_SUPER_PROJECTS_DIR = "Super Projects"


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
# Link helpers
# ---------------------------------------------------------------------------


def _history_subpath(metric_date: str) -> Path:
    """Return the relative path under out_dir for a history page.

    e.g. "2026-05-01" → Path("History/2026/2026-05/2026-05-01.md")
    """
    yyyy = metric_date[:4]
    yyyy_mm = metric_date[:7]
    return Path("History") / yyyy / yyyy_mm / f"{metric_date}.md"


def _rel(from_file: Path, to_file: Path) -> str:
    """Return the relative path from *from_file*'s directory to *to_file*.

    Both paths must be absolute.  The result is URL-encoded (spaces → %20)
    for use in Markdown links.
    """
    rel = os.path.relpath(to_file, from_file.parent)
    # Encode spaces for markdown link compatibility
    return rel.replace(" ", "%20")


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
    base_dir = config.base_directory

    env = _build_jinja_env()
    today_str = datetime.date.today().isoformat()
    files_written = 0

    files_deleted = 0

    if project_id is not None:
        # Targeted single-project regeneration
        files_written += _render_single_project(env, conn, out_dir, base_dir, project_id, today_str)
    else:
        if target in ("all", "dashboard"):
            files_written += _render_dashboard(
                env,
                conn,
                out_dir,
                base_dir,
                today_str,
                config.dashboard_recent_changes_limit,
            )
            files_written += _render_due_today(env, conn, out_dir, base_dir, today_str)
            files_written += _render_past_due(env, conn, out_dir, base_dir, today_str)
            files_written += _render_due_soon(env, conn, out_dir, base_dir, today_str)
            files_written += _render_future_due(env, conn, out_dir, base_dir, today_str)
            files_written += _render_not_due(env, conn, out_dir, base_dir, today_str)
            files_written += _render_warnings(env, conn, out_dir, today_str)
            files_written += _render_super_projects_index(env, conn, out_dir, today_str)
            files_written += _render_projects_index(env, conn, out_dir, today_str)
        if target in ("all", "projects"):
            files_written += _render_all_projects(env, conn, out_dir, base_dir, today_str)
            files_written += _render_all_super_projects(env, conn, out_dir, base_dir, today_str)
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
                files_deleted += _cleanup_stale_report_files(
                    conn, out_dir / _SUPER_PROJECTS_DIR, current_sp_ids
                )
        if target in ("all", "history"):
            files_written += _render_history(env, conn, out_dir, base_dir, today_str, config)

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
        trim_blocks=True,
        lstrip_blocks=True,
    )
    env.filters["basename"] = os.path.basename
    env.filters["stem"] = lambda p: Path(p).stem
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
# Task query helpers
# ---------------------------------------------------------------------------


def _query_open_tasks(
    conn: sqlite3.Connection,
    where_extra: str,
    params: list,
) -> list[SimpleNamespace]:
    """Query open, non-deleted tasks with an extra WHERE clause fragment.

    Returns SimpleNamespace objects enriched with project info, sorted by
    ``(task_priority_rank, due_date or "", project_priority_rank)``.

    Fields on each item:
        task_text, file_path, file_stem, due_date,
        task_priority, task_priority_rank,
        project_id, project_title, project_priority, project_priority_rank,
        project_priority_emoji, estimate_secs, estimate_display
    """
    rows = conn.execute(
        "SELECT t.task_id, t.task_text, t.file_path, t.due_date, t.attributes"
        " FROM task t"
        " JOIN file f ON t.file_path = f.file_path"
        f" WHERE t.checked = 0 AND f.deleted = 0 {where_extra}",
        params,
    ).fetchall()

    result: list[SimpleNamespace] = []
    for row in rows:
        attrs: dict = json.loads(row["attributes"] or "{}")
        raw_priority = attrs.get("priority") or None
        task_priority: str | None = raw_priority.title() if raw_priority else None
        estimate_secs = int(attrs.get("estimate", 0) or 0)
        estimate_mins = estimate_secs // 60 if estimate_secs > 0 else None
        estimate_display = f"{estimate_mins}m" if estimate_mins else "—"

        # Find the highest-priority project this file belongs to
        proj_rows = conn.execute(
            "SELECT p.project_id, p.title, p.priority"
            " FROM file_project fp"
            " JOIN project p ON fp.project_id = p.project_id"
            " WHERE fp.file_path = ?"
            " ORDER BY p.project_id",
            (row["file_path"],),
        ).fetchall()

        best_proj_id: str | None = None
        best_proj_title: str | None = None
        best_proj_priority: str | None = None
        best_proj_rank = 4
        for pr in proj_rows:
            rank = _PRIORITY_RANK.get(pr["priority"], 3)
            if rank < best_proj_rank:
                best_proj_rank = rank
                best_proj_id = pr["project_id"]
                best_proj_title = pr["title"]
                best_proj_priority = pr["priority"]

        result.append(
            SimpleNamespace(
                task_text=row["task_text"],
                file_path=row["file_path"],
                file_stem=Path(row["file_path"]).stem,
                due_date=row["due_date"],
                task_priority=task_priority,
                task_priority_rank=_PRIORITY_RANK.get(task_priority, 3),
                project_id=best_proj_id,
                project_title=best_proj_title or best_proj_id,
                project_priority=best_proj_priority,
                project_priority_rank=best_proj_rank,
                project_priority_emoji=_PRIORITY_EMOJI.get(best_proj_priority, ""),
                estimate_secs=estimate_secs,
                estimate_display=estimate_display,
            )
        )

    result.sort(key=lambda x: (x.task_priority_rank, x.due_date or "", x.project_priority_rank))
    return result


def _get_due_today_tasks(conn: sqlite3.Connection, today: str) -> list[SimpleNamespace]:
    return _query_open_tasks(conn, "AND t.due_date = ?", [today])


def _get_past_due_tasks(conn: sqlite3.Connection, today: str) -> list[SimpleNamespace]:
    return _query_open_tasks(conn, "AND t.due_date < ?", [today])


def _get_due_soon_tasks(conn: sqlite3.Connection, today: str) -> list[SimpleNamespace]:
    """Tasks due in the next 1–7 days (not today, not 8+ days out)."""
    soon = (datetime.date.fromisoformat(today) + datetime.timedelta(days=7)).isoformat()
    return _query_open_tasks(conn, "AND t.due_date > ? AND t.due_date <= ?", [today, soon])


def _get_future_due_tasks(conn: sqlite3.Connection, today: str) -> list[SimpleNamespace]:
    """Tasks due more than 7 days from today."""
    future = (datetime.date.fromisoformat(today) + datetime.timedelta(days=7)).isoformat()
    return _query_open_tasks(conn, "AND t.due_date > ?", [future])


def _get_not_due_tasks(conn: sqlite3.Connection) -> list[SimpleNamespace]:
    """Tasks with no due date."""
    return _query_open_tasks(conn, "AND t.due_date IS NULL", [])


_TASK_TIERS: list[tuple[str | None, str]] = [
    ("High",   "⏫ High Priority"),
    ("Medium", "🔼 Medium Priority"),
    ("Low",    "🔽 Low Priority"),
    (None,     "— No Priority"),
]

_TASK_TIER_ANCHORS: dict[str | None, str] = {
    "High": "priority-high",
    "Medium": "priority-medium",
    "Low": "priority-low",
    None: "priority-none",
}

# Keep old name as alias for backwards compatibility with tests
_DUE_TODAY_TIERS = _TASK_TIERS


def _build_tiers(tasks: list[SimpleNamespace]) -> list[dict]:
    """Group tasks into priority tiers with per-tier estimate totals."""
    tiers: list[dict] = []
    for priority_key, heading in _TASK_TIERS:
        tier_tasks = [t for t in tasks if t.task_priority == priority_key]
        total_secs = sum(t.estimate_secs for t in tier_tasks)
        total_mins = total_secs // 60 if total_secs > 0 else None
        tiers.append({
            "priority_key": priority_key,
            "heading": heading,
            "anchor": _TASK_TIER_ANCHORS[priority_key],
            "tasks": tier_tasks,
            "total_est": f"{total_mins}m" if total_mins else None,
        })
    return tiers


def _attach_task_links(
    tasks: list[SimpleNamespace],
    this_file: Path,
    base_dir: Path,
    out_dir: Path,
) -> None:
    """Stamp source_link and project_link onto each task in-place."""
    for t in tasks:
        t.source_link = _rel(this_file, base_dir / t.file_path)
        t.project_link = (
            _rel(this_file, out_dir / "Projects" / f"{t.project_id}.md")
            if t.project_id
            else None
        )


def _render_due_today(
    env: jinja2.Environment,
    conn: sqlite3.Connection,
    out_dir: Path,
    base_dir: Path,
    today_str: str,
) -> int:
    tasks = _get_due_today_tasks(conn, today_str)
    this_file = out_dir / _DUE_TODAY_PAGE
    _attach_task_links(tasks, this_file, base_dir, out_dir)
    tiers = _build_tiers(tasks)
    any_tasks = any(tier["tasks"] for tier in tiers)
    generated_at = datetime.datetime.now().strftime("%Y-%m-%d %-I:%M %p")
    content = env.get_template("due_today.md.j2").render(
        today=today_str,
        generated_at=generated_at,
        tiers=tiers,
        any_tasks=any_tasks,
        dashboard_link=_rel(this_file, out_dir / _HOME_PAGE),
    )
    _write_file(conn, this_file, content, today_str)
    return 1


def _render_past_due(
    env: jinja2.Environment,
    conn: sqlite3.Connection,
    out_dir: Path,
    base_dir: Path,
    today_str: str,
) -> int:
    tasks = _get_past_due_tasks(conn, today_str)
    this_file = out_dir / _PAST_DUE_PAGE
    _attach_task_links(tasks, this_file, base_dir, out_dir)
    tiers = _build_tiers(tasks)
    any_tasks = any(tier["tasks"] for tier in tiers)
    generated_at = datetime.datetime.now().strftime("%Y-%m-%d %-I:%M %p")
    content = env.get_template("past_due.md.j2").render(
        today=today_str,
        generated_at=generated_at,
        tiers=tiers,
        any_tasks=any_tasks,
        dashboard_link=_rel(this_file, out_dir / _HOME_PAGE),
    )
    _write_file(conn, this_file, content, today_str)
    return 1


def _render_due_soon(
    env: jinja2.Environment,
    conn: sqlite3.Connection,
    out_dir: Path,
    base_dir: Path,
    today_str: str,
) -> int:
    tasks = _get_due_soon_tasks(conn, today_str)
    this_file = out_dir / _DUE_SOON_PAGE
    _attach_task_links(tasks, this_file, base_dir, out_dir)
    tiers = _build_tiers(tasks)
    any_tasks = any(tier["tasks"] for tier in tiers)
    soon_date = (datetime.date.fromisoformat(today_str) + datetime.timedelta(days=7)).isoformat()
    generated_at = datetime.datetime.now().strftime("%Y-%m-%d %-I:%M %p")
    content = env.get_template("due_soon.md.j2").render(
        today=today_str,
        soon_date=soon_date,
        generated_at=generated_at,
        tiers=tiers,
        any_tasks=any_tasks,
        dashboard_link=_rel(this_file, out_dir / _HOME_PAGE),
    )
    _write_file(conn, this_file, content, today_str)
    return 1


def _render_future_due(
    env: jinja2.Environment,
    conn: sqlite3.Connection,
    out_dir: Path,
    base_dir: Path,
    today_str: str,
) -> int:
    tasks = _get_future_due_tasks(conn, today_str)
    this_file = out_dir / _FUTURE_DUE_PAGE
    _attach_task_links(tasks, this_file, base_dir, out_dir)
    tiers = _build_tiers(tasks)
    any_tasks = any(tier["tasks"] for tier in tiers)
    generated_at = datetime.datetime.now().strftime("%Y-%m-%d %-I:%M %p")
    content = env.get_template("future_due.md.j2").render(
        today=today_str,
        generated_at=generated_at,
        tiers=tiers,
        any_tasks=any_tasks,
        dashboard_link=_rel(this_file, out_dir / _HOME_PAGE),
    )
    _write_file(conn, this_file, content, today_str)
    return 1


def _render_not_due(
    env: jinja2.Environment,
    conn: sqlite3.Connection,
    out_dir: Path,
    base_dir: Path,
    today_str: str,
) -> int:
    tasks = _get_not_due_tasks(conn)
    this_file = out_dir / _NOT_DUE_PAGE
    _attach_task_links(tasks, this_file, base_dir, out_dir)
    tiers = _build_tiers(tasks)
    any_tasks = any(tier["tasks"] for tier in tiers)
    generated_at = datetime.datetime.now().strftime("%Y-%m-%d %-I:%M %p")
    content = env.get_template("not_due.md.j2").render(
        today=today_str,
        generated_at=generated_at,
        tiers=tiers,
        any_tasks=any_tasks,
        dashboard_link=_rel(this_file, out_dir / _HOME_PAGE),
    )
    _write_file(conn, this_file, content, today_str)
    return 1


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


def _get_recently_changed_tracked_files(
    conn: sqlite3.Connection,
    limit: int,
) -> list[SimpleNamespace]:
    """Return recent non-generated tracked files ordered by last change."""
    rows = conn.execute(
        "SELECT file_path, modified, modified_date"
        " FROM file"
        " WHERE deleted = 0 AND is_generated = 0"
        " ORDER BY"
        " CASE WHEN modified IS NULL OR modified <= 0 THEN 0 ELSE 1 END DESC,"
        " modified DESC,"
        " modified_date DESC,"
        " file_path ASC"
        " LIMIT ?",
        (limit,),
    ).fetchall()

    files: list[SimpleNamespace] = []
    for row in rows:
        modified_ms = row["modified"]
        if isinstance(modified_ms, int) and modified_ms > 0:
            changed_at = datetime.datetime.fromtimestamp(modified_ms / 1000)
            changed_at_text = changed_at.strftime("%Y-%m-%d %-I:%M %p")
        elif row["modified_date"]:
            changed_at_text = f"{row['modified_date']} 12:00 AM"
        else:
            changed_at_text = "Unknown"

        files.append(
            SimpleNamespace(
                file_path=row["file_path"],
                changed_at_text=changed_at_text,
            )
        )

    return files


def _build_dashboard_task_view_table(
    conn: sqlite3.Connection,
    out_dir: Path,
    this_file: Path,
    today_str: str,
) -> list[SimpleNamespace]:
    """Build a dashboard matrix of due-view counts by priority tier."""
    view_specs: list[tuple[str, Path, list[SimpleNamespace]]] = [
        ("📅 Due Today", out_dir / _DUE_TODAY_PAGE, _get_due_today_tasks(conn, today_str)),
        ("⚠️ Past Due", out_dir / _PAST_DUE_PAGE, _get_past_due_tasks(conn, today_str)),
        ("⏰ Due Soon", out_dir / _DUE_SOON_PAGE, _get_due_soon_tasks(conn, today_str)),
        ("📆 Future Due", out_dir / _FUTURE_DUE_PAGE, _get_future_due_tasks(conn, today_str)),
        ("📝 Not Due", out_dir / _NOT_DUE_PAGE, _get_not_due_tasks(conn)),
    ]

    rows: list[SimpleNamespace] = []
    for label, page_path, tasks in view_specs:
        tiers = _build_tiers(tasks)
        cells: list[SimpleNamespace] = []
        for tier in tiers:
            task_count = len(tier["tasks"])
            if task_count == 0:
                cells.append(SimpleNamespace(display="-", link=None))
                continue

            total_mins = sum(t.estimate_secs for t in tier["tasks"]) // 60
            cells.append(
                SimpleNamespace(
                    display=f"{task_count:,} ({total_mins}m)",
                    link=f"{_rel(this_file, page_path)}#{tier['anchor']}",
                )
            )

        rows.append(
            SimpleNamespace(
                label=label,
                view_link=_rel(this_file, page_path),
                cells=cells,
            )
        )

    return rows


def _estimate_minutes_from_attributes(attributes: str | None) -> int:
    """Parse task estimate minutes from JSON attributes payload."""
    try:
        attrs = json.loads(attributes or "{}")
        return int(attrs.get("estimate", 0) or 0) // 60
    except Exception:
        return 0


def _calc_current_streak_for_projects(
    conn: sqlite3.Connection,
    project_ids: list[str],
) -> int:
    """Return consecutive active-day streak for a project-id set."""
    if not project_ids:
        return 0

    placeholders = ",".join("?" * len(project_ids))
    rows = conn.execute(
        "SELECT metric_date, SUM(tasks_completed_count) AS total"
        " FROM daily_metric"
        f" WHERE project_id IN ({placeholders})"
        " GROUP BY metric_date"
        " ORDER BY metric_date ASC",
        project_ids,
    ).fetchall()

    if not rows:
        return 0

    dated: list[tuple[datetime.date, bool]] = []
    for row in rows:
        dated.append((datetime.date.fromisoformat(row["metric_date"]), (row["total"] or 0) > 0))

    current = 0
    prev_d: datetime.date | None = None
    for d, active in reversed(dated):
        if not active:
            break
        if prev_d is not None and (prev_d - d).days > 1:
            break
        current += 1
        prev_d = d

    return current


def _calc_super_project_health(
    current_streak: int,
    past_due_count: int,
    past_due_minutes: int,
    total_open_count: int,
    due_today_count: int,
    due_soon_count: int,
) -> SimpleNamespace:
    """Blend streak momentum with overdue burden into a compact health signal."""
    overdue_count_penalty = past_due_count * 12
    overdue_time_penalty = (past_due_minutes // 30) * 2
    overdue_ratio_penalty = (
        int((past_due_count / total_open_count) * 25) if total_open_count > 0 else 0
    )
    due_today_penalty = due_today_count * 3
    due_soon_penalty = due_soon_count
    backlog_penalty = min(15, total_open_count // 25)
    streak_bonus = min(24, current_streak * 3)
    on_time_bonus = 4 if past_due_count == 0 and current_streak >= 1 else 0

    score = max(
        0,
        min(
            100,
            (
                78
                + streak_bonus
                + on_time_bonus
                - overdue_count_penalty
                - overdue_time_penalty
                - overdue_ratio_penalty
                - due_today_penalty
                - due_soon_penalty
                - backlog_penalty
            ),
        ),
    )

    if score >= 80:
        emoji, label = "🟢", "Strong"
    elif score >= 60:
        emoji, label = "🟡", "Steady"
    elif score >= 40:
        emoji, label = "🟠", "Watch"
    else:
        emoji, label = "🔴", "At Risk"

    return SimpleNamespace(score=score, label=label, emoji=emoji, display=f"{emoji} {label} ({score})")


def _build_active_super_projects_table(
    conn: sqlite3.Connection,
    out_dir: Path,
    this_file: Path,
    today_str: str,
) -> list[SimpleNamespace]:
    """Build dashboard rows for super projects with at least one In Progress child project."""
    soon_date = (datetime.date.fromisoformat(today_str) + datetime.timedelta(days=7)).isoformat()

    sp_rows = conn.execute(
        "SELECT super_project_id, title FROM super_project ORDER BY super_project_id"
    ).fetchall()

    rows: list[SimpleNamespace] = []
    for sp in sp_rows:
        sp_id = sp["super_project_id"]
        active_project_rows = conn.execute(
            "SELECT project_id FROM project"
            " WHERE super_project_id = ? AND status = 'In Progress'"
            " ORDER BY project_id",
            (sp_id,),
        ).fetchall()
        active_project_ids = [r["project_id"] for r in active_project_rows]

        if not active_project_ids:
            continue

        sp_title_raw = (sp["title"] or "").strip()
        sp_display_title = sp_title_raw or sp_id

        placeholders = ",".join("?" * len(active_project_ids))
        task_rows = conn.execute(
            "SELECT t.due_date, t.attributes"
            " FROM task t"
            " JOIN file f ON t.file_path = f.file_path"
            " WHERE t.checked = 0 AND f.deleted = 0"
            " AND EXISTS ("
            "   SELECT 1 FROM file_project fp"
            "   WHERE fp.file_path = t.file_path"
            f"     AND fp.project_id IN ({placeholders})"
            " )",
            active_project_ids,
        ).fetchall()

        buckets: dict[str, dict[str, int]] = {
            "due_today": {"count": 0, "minutes": 0},
            "past_due": {"count": 0, "minutes": 0},
            "due_soon": {"count": 0, "minutes": 0},
            "future_due": {"count": 0, "minutes": 0},
            "not_due": {"count": 0, "minutes": 0},
        }

        for task in task_rows:
            due_date = task["due_date"]
            estimate_minutes = _estimate_minutes_from_attributes(task["attributes"])

            if due_date is None:
                bucket = "not_due"
            elif due_date < today_str:
                bucket = "past_due"
            elif due_date == today_str:
                bucket = "due_today"
            elif due_date <= soon_date:
                bucket = "due_soon"
            else:
                bucket = "future_due"

            buckets[bucket]["count"] += 1
            buckets[bucket]["minutes"] += estimate_minutes

        last_updated_row = conn.execute(
            "SELECT f.modified, f.modified_date"
            " FROM file f"
            " WHERE f.deleted = 0 AND f.is_generated = 0"
            " AND EXISTS ("
            "   SELECT 1 FROM file_project fp"
            "   WHERE fp.file_path = f.file_path"
            f"     AND fp.project_id IN ({placeholders})"
            " )"
            " ORDER BY"
            " CASE WHEN f.modified IS NULL OR f.modified <= 0 THEN 0 ELSE 1 END DESC,"
            " f.modified DESC,"
            " f.modified_date DESC,"
            " f.file_path ASC"
            " LIMIT 1",
            active_project_ids,
        ).fetchone()

        last_updated_text = _format_last_updated_row(last_updated_row)

        current_streak = _calc_current_streak_for_projects(conn, active_project_ids)
        total_open_count = sum(v["count"] for v in buckets.values())
        health = _calc_super_project_health(
            current_streak=current_streak,
            past_due_count=buckets["past_due"]["count"],
            past_due_minutes=buckets["past_due"]["minutes"],
            total_open_count=total_open_count,
            due_today_count=buckets["due_today"]["count"],
            due_soon_count=buckets["due_soon"]["count"],
        )

        rows.append(
            SimpleNamespace(
                super_project_label=f"{sp_display_title} ({len(active_project_ids)})",
                super_project_link=_rel(this_file, out_dir / _SUPER_PROJECTS_DIR / f"{sp_id}.md"),
                current_streak=current_streak,
                health=health.display,
                last_updated=last_updated_text,
                due_today_display=(
                    f"{buckets['due_today']['count']:,} ({buckets['due_today']['minutes']}m)"
                ),
                past_due_display=f"{buckets['past_due']['count']:,}",
                due_soon_display=f"{buckets['due_soon']['count']:,}",
                future_due_display=f"{buckets['future_due']['count']:,}",
                not_due_display=f"{buckets['not_due']['count']:,}",
            )
        )

    return rows


def _format_last_updated_row(last_updated_row: Any) -> str:
    if last_updated_row is None:
        return "Unknown"
    if isinstance(last_updated_row["modified"], int) and last_updated_row["modified"] > 0:
        changed_at = datetime.datetime.fromtimestamp(last_updated_row["modified"] / 1000)
        return changed_at.strftime("%Y-%m-%d %-I:%M %p")
    if last_updated_row["modified_date"]:
        return f"{last_updated_row['modified_date']} 12:00 AM"
    return "Unknown"


def _build_super_project_project_rows(
    conn: sqlite3.Connection,
    out_dir: Path,
    this_file: Path,
    today_str: str,
    super_project_id: str,
    status: str,
    include_health: bool,
) -> list[SimpleNamespace]:
    soon_date = (datetime.date.fromisoformat(today_str) + datetime.timedelta(days=7)).isoformat()

    project_rows = conn.execute(
        "SELECT project_id, title FROM project"
        " WHERE super_project_id = ? AND status = ?"
        " ORDER BY project_id",
        (super_project_id, status),
    ).fetchall()

    rows: list[SimpleNamespace] = []
    for project in project_rows:
        project_id = project["project_id"]
        project_title_raw = (project["title"] or "").strip()
        project_display_title = project_title_raw or project_id

        task_rows = conn.execute(
            "SELECT t.due_date, t.attributes"
            " FROM task t"
            " JOIN file f ON t.file_path = f.file_path"
            " WHERE t.checked = 0 AND f.deleted = 0"
            " AND EXISTS ("
            "   SELECT 1 FROM file_project fp"
            "   WHERE fp.file_path = t.file_path"
            "     AND fp.project_id = ?"
            " )",
            (project_id,),
        ).fetchall()

        buckets: dict[str, dict[str, int]] = {
            "due_today": {"count": 0, "minutes": 0},
            "past_due": {"count": 0, "minutes": 0},
            "due_soon": {"count": 0, "minutes": 0},
            "future_due": {"count": 0, "minutes": 0},
            "not_due": {"count": 0, "minutes": 0},
        }

        for task in task_rows:
            due_date = task["due_date"]
            estimate_minutes = _estimate_minutes_from_attributes(task["attributes"])

            if due_date is None:
                bucket = "not_due"
            elif due_date < today_str:
                bucket = "past_due"
            elif due_date == today_str:
                bucket = "due_today"
            elif due_date <= soon_date:
                bucket = "due_soon"
            else:
                bucket = "future_due"

            buckets[bucket]["count"] += 1
            buckets[bucket]["minutes"] += estimate_minutes

        last_updated_row = conn.execute(
            "SELECT f.modified, f.modified_date"
            " FROM file f"
            " JOIN file_project fp ON fp.file_path = f.file_path"
            " WHERE fp.project_id = ? AND f.deleted = 0 AND f.is_generated = 0"
            " ORDER BY"
            " CASE WHEN f.modified IS NULL OR f.modified <= 0 THEN 0 ELSE 1 END DESC,"
            " f.modified DESC,"
            " f.modified_date DESC,"
            " f.file_path ASC"
            " LIMIT 1",
            (project_id,),
        ).fetchone()

        current_streak = _calc_current_streak_for_projects(conn, [project_id])
        total_open_count = sum(v["count"] for v in buckets.values())
        row = SimpleNamespace(
            project_label=project_display_title,
            project_link=_rel(this_file, out_dir / "Projects" / f"{project_id}.md"),
            current_streak=current_streak,
            last_updated=_format_last_updated_row(last_updated_row),
            due_today_display=(
                f"{buckets['due_today']['count']:,} ({buckets['due_today']['minutes']}m)"
            ),
            past_due_display=f"{buckets['past_due']['count']:,}",
            due_soon_display=f"{buckets['due_soon']['count']:,}",
            future_due_display=f"{buckets['future_due']['count']:,}",
            not_due_display=f"{buckets['not_due']['count']:,}",
            health=None,
        )

        if include_health:
            health = _calc_super_project_health(
                current_streak=current_streak,
                past_due_count=buckets["past_due"]["count"],
                past_due_minutes=buckets["past_due"]["minutes"],
                total_open_count=total_open_count,
                due_today_count=buckets["due_today"]["count"],
                due_soon_count=buckets["due_soon"]["count"],
            )
            row.health = health.display

        rows.append(row)

    return rows


def _render_dashboard(
    env: jinja2.Environment,
    conn: sqlite3.Connection,
    out_dir: Path,
    base_dir: Path,
    today_str: str,
    recent_changes_limit: int,
) -> int:
    current_streak, longest_streak, longest_streak_end = _calc_streak(conn)
    heatmap = _calc_heatmap(conn)
    yesterday_count, yesterday_date, yesterday_by_project = _calc_yesterday_stats(conn)
    past_due, due_today = _get_due_tasks(conn)
    recently_changed_files = _get_recently_changed_tracked_files(conn, recent_changes_limit)

    super_projects = conn.execute(
        "SELECT super_project_id AS id, title FROM super_project ORDER BY super_project_id"
    ).fetchall()
    projects = conn.execute(
        "SELECT project_id AS id, title FROM project ORDER BY project_id"
    ).fetchall()

    this_file = out_dir / _HOME_PAGE
    task_view_table_rows = _build_dashboard_task_view_table(conn, out_dir, this_file, today_str)
    active_super_project_rows = _build_active_super_projects_table(conn, out_dir, this_file, today_str)

    def source_link(file_path: str) -> str:
        return _rel(this_file, base_dir / file_path)

    def project_link(project_id: str) -> str:
        return _rel(this_file, out_dir / "Projects" / f"{project_id}.md")

    def super_project_link(sp_id: str) -> str:
        return _rel(this_file, out_dir / _SUPER_PROJECTS_DIR / f"{sp_id}.md")

    def history_link(metric_date: str) -> str:
        return _rel(this_file, out_dir / _history_subpath(metric_date))

    generated_at = datetime.datetime.now().strftime("%Y-%m-%d %-I:%M %p")
    content = env.get_template("daily_dashboard.md.j2").render(
        generated_at=generated_at,
        warnings_link=_rel(this_file, out_dir / _WARNINGS_PAGE),
        super_projects_index_link=_rel(this_file, out_dir / _SUPER_PROJECTS_INDEX_PAGE),
        projects_index_link=_rel(this_file, out_dir / _PROJECTS_INDEX_PAGE),
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
        source_link=source_link,
        project_link=project_link,
        super_project_link=super_project_link,
        history_link=history_link,
        task_view_table_rows=task_view_table_rows,
        active_super_project_rows=active_super_project_rows,
        recently_changed_files=recently_changed_files,
        due_today_link=_rel(this_file, out_dir / _DUE_TODAY_PAGE),
        past_due_link=_rel(this_file, out_dir / _PAST_DUE_PAGE),
        due_soon_link=_rel(this_file, out_dir / _DUE_SOON_PAGE),
        future_due_link=_rel(this_file, out_dir / _FUTURE_DUE_PAGE),
        not_due_link=_rel(this_file, out_dir / _NOT_DUE_PAGE),
    )
    _write_file(conn, this_file, content, today_str)
    return 1


# ---------------------------------------------------------------------------
# Warnings renderer
# ---------------------------------------------------------------------------


def _render_warnings(
    env: jinja2.Environment,
    conn: sqlite3.Connection,
    out_dir: Path,
    today_str: str,
) -> int:
    this_file = out_dir / _WARNINGS_PAGE
    generated_at = datetime.datetime.now().strftime("%Y-%m-%d %-I:%M %p")

    # Tasks with parse errors
    task_error_rows = conn.execute(
        "SELECT task_id, task_text, file_path, errors FROM task"
        " WHERE errors IS NOT NULL AND errors != '[]'"
        " ORDER BY file_path, task_id"
    ).fetchall()
    tasks_with_errors: list[SimpleNamespace] = []
    for r in task_error_rows:
        try:
            errs = json.loads(r["errors"] or "[]")
        except Exception:
            errs = [str(r["errors"])]
        tasks_with_errors.append(
            SimpleNamespace(
                task_text=r["task_text"] or "(empty)",
                file_path=r["file_path"],
                errors=errs,
            )
        )

    # Projects without a super-project
    projects_no_super = conn.execute(
        "SELECT project_id, title FROM project"
        " WHERE super_project_id IS NULL"
        " ORDER BY project_id"
    ).fetchall()
    projects_no_super_rows: list[SimpleNamespace] = []
    for r in projects_no_super:
        projects_no_super_rows.append(
            SimpleNamespace(
                project_id=r["project_id"],
                title=(r["title"] or "").strip() or r["project_id"],
                project_link=_rel(this_file, out_dir / "Projects" / f"{r['project_id']}.md"),
            )
        )

    # Projects with no associated files
    projects_no_files_rows_raw = conn.execute(
        "SELECT p.project_id, p.title FROM project p"
        " WHERE NOT EXISTS ("
        "   SELECT 1 FROM file_project fp WHERE fp.project_id = p.project_id"
        " )"
        " ORDER BY p.project_id"
    ).fetchall()
    projects_no_files: list[SimpleNamespace] = []
    for r in projects_no_files_rows_raw:
        projects_no_files.append(
            SimpleNamespace(
                project_id=r["project_id"],
                title=(r["title"] or "").strip() or r["project_id"],
                project_link=_rel(this_file, out_dir / "Projects" / f"{r['project_id']}.md"),
            )
        )

    # Orphaned files (tracked, not deleted, not generated, not linked to any project)
    orphaned_file_rows = conn.execute(
        "SELECT file_path FROM file"
        " WHERE deleted = 0 AND is_generated = 0"
        " AND NOT EXISTS ("
        "   SELECT 1 FROM file_project fp WHERE fp.file_path = file.file_path"
        " )"
        " ORDER BY file_path"
    ).fetchall()
    orphaned_files = [r["file_path"] for r in orphaned_file_rows]

    # Orphaned tasks (tasks whose file is not linked to any project)
    orphaned_task_rows = conn.execute(
        "SELECT t.task_id, t.task_text, t.file_path FROM task t"
        " WHERE NOT EXISTS ("
        "   SELECT 1 FROM file_project fp WHERE fp.file_path = t.file_path"
        " )"
        " ORDER BY t.file_path, t.task_id"
    ).fetchall()
    orphaned_tasks: list[SimpleNamespace] = [
        SimpleNamespace(
            task_text=r["task_text"] or "(empty)",
            file_path=r["file_path"],
        )
        for r in orphaned_task_rows
    ]

    content = env.get_template("warnings.md.j2").render(
        generated_at=generated_at,
        dashboard_link=_rel(this_file, out_dir / _HOME_PAGE),
        tasks_with_errors=tasks_with_errors,
        projects_no_super=projects_no_super_rows,
        projects_no_files=projects_no_files,
        orphaned_files=orphaned_files,
        orphaned_tasks=orphaned_tasks,
    )
    _write_file(conn, this_file, content, today_str)
    return 1


# ---------------------------------------------------------------------------
# Super Projects index renderer
# ---------------------------------------------------------------------------

_STATUS_SORT_ORDER = {
    "In Progress": 0,
    "On Hold": 1,
    "Planned": 2,
    "Complete": 3,
    "Cancelled": 4,
}
_ALL_PROJECT_STATUSES = list(_STATUS_SORT_ORDER.keys())


def _render_super_projects_index(
    env: jinja2.Environment,
    conn: sqlite3.Connection,
    out_dir: Path,
    today_str: str,
) -> int:
    this_file = out_dir / _SUPER_PROJECTS_INDEX_PAGE
    generated_at = datetime.datetime.now().strftime("%Y-%m-%d %-I:%M %p")
    soon_date = (datetime.date.fromisoformat(today_str) + datetime.timedelta(days=7)).isoformat()

    sp_rows = conn.execute(
        "SELECT super_project_id, title FROM super_project ORDER BY super_project_id"
    ).fetchall()

    rows: list[SimpleNamespace] = []
    for sp in sp_rows:
        sp_id = sp["super_project_id"]
        sp_title_raw = (sp["title"] or "").strip()
        sp_display_title = sp_title_raw or sp_id

        # Count projects per status
        status_counts: dict[str, int] = {s: 0 for s in _ALL_PROJECT_STATUSES}
        project_rows = conn.execute(
            "SELECT project_id, status FROM project WHERE super_project_id = ? ORDER BY project_id",
            (sp_id,),
        ).fetchall()
        for proj in project_rows:
            s = proj["status"]
            if s in status_counts:
                status_counts[s] += 1

        # Active (In Progress) project ids for health + streak
        active_project_ids = [
            proj["project_id"] for proj in project_rows if proj["status"] == "In Progress"
        ]
        has_active = bool(active_project_ids)

        # Last updated across all non-generated, non-deleted files belonging to this SP
        all_project_ids = [proj["project_id"] for proj in project_rows]
        if all_project_ids:
            placeholders = ",".join("?" * len(all_project_ids))
            last_updated_row = conn.execute(
                "SELECT f.modified, f.modified_date"
                " FROM file f"
                " WHERE f.deleted = 0 AND f.is_generated = 0"
                " AND EXISTS ("
                "   SELECT 1 FROM file_project fp"
                "   WHERE fp.file_path = f.file_path"
                f"     AND fp.project_id IN ({placeholders})"
                " )"
                " ORDER BY"
                " CASE WHEN f.modified IS NULL OR f.modified <= 0 THEN 0 ELSE 1 END DESC,"
                " f.modified DESC,"
                " f.modified_date DESC,"
                " f.file_path ASC"
                " LIMIT 1",
                all_project_ids,
            ).fetchone()
        else:
            last_updated_row = None

        current_streak = (
            _calc_current_streak_for_projects(conn, active_project_ids) if has_active else 0
        )

        # Health — only computed when there are active projects
        if has_active:
            placeholders = ",".join("?" * len(active_project_ids))
            task_rows = conn.execute(
                "SELECT t.due_date, t.attributes"
                " FROM task t"
                " JOIN file f ON t.file_path = f.file_path"
                " WHERE t.checked = 0 AND f.deleted = 0"
                " AND EXISTS ("
                "   SELECT 1 FROM file_project fp"
                "   WHERE fp.file_path = t.file_path"
                f"     AND fp.project_id IN ({placeholders})"
                " )",
                active_project_ids,
            ).fetchall()
            buckets: dict[str, dict[str, int]] = {
                "due_today": {"count": 0, "minutes": 0},
                "past_due": {"count": 0, "minutes": 0},
                "due_soon": {"count": 0, "minutes": 0},
                "future_due": {"count": 0, "minutes": 0},
                "not_due": {"count": 0, "minutes": 0},
            }
            for task in task_rows:
                due_date = task["due_date"]
                est = _estimate_minutes_from_attributes(task["attributes"])
                if due_date is None:
                    bucket_key = "not_due"
                elif due_date < today_str:
                    bucket_key = "past_due"
                elif due_date == today_str:
                    bucket_key = "due_today"
                elif due_date <= soon_date:
                    bucket_key = "due_soon"
                else:
                    bucket_key = "future_due"
                buckets[bucket_key]["count"] += 1
                buckets[bucket_key]["minutes"] += est

            total_open = sum(v["count"] for v in buckets.values())
            health_ns = _calc_super_project_health(
                current_streak=current_streak,
                past_due_count=buckets["past_due"]["count"],
                past_due_minutes=buckets["past_due"]["minutes"],
                total_open_count=total_open,
                due_today_count=buckets["due_today"]["count"],
                due_soon_count=buckets["due_soon"]["count"],
            )
            health_display = health_ns.display
            health_score = health_ns.score
        else:
            health_display = ""
            health_score = None

        rows.append(
            SimpleNamespace(
                sp_label=sp_display_title,
                sp_link=_rel(this_file, out_dir / _SUPER_PROJECTS_DIR / f"{sp_id}.md"),
                current_streak=current_streak,
                health=health_display,
                last_updated=_format_last_updated_row(last_updated_row),
                active_count=status_counts["In Progress"],
                on_hold_count=status_counts["On Hold"],
                planned_count=status_counts["Planned"],
                complete_count=status_counts["Complete"],
                cancelled_count=status_counts["Cancelled"],
                _has_active=has_active,
                _sp_display_title=sp_display_title,
                _health_score=health_score,
            )
        )

    # Sort: worst-to-best health first, then by display title.
    # Super-projects without active projects have no health and are placed after scored rows.
    rows.sort(
        key=lambda r: (
            1 if r._health_score is None else 0,
            r._health_score if r._health_score is not None else 999,
            r._sp_display_title.lower(),
        )
    )

    content = env.get_template("super_projects_index.md.j2").render(
        generated_at=generated_at,
        dashboard_link=_rel(this_file, out_dir / _HOME_PAGE),
        rows=rows,
    )
    _write_file(conn, this_file, content, today_str)
    return 1


# ---------------------------------------------------------------------------
# Projects index renderer
# ---------------------------------------------------------------------------


def _render_projects_index(
    env: jinja2.Environment,
    conn: sqlite3.Connection,
    out_dir: Path,
    today_str: str,
) -> int:
    this_file = out_dir / _PROJECTS_INDEX_PAGE
    generated_at = datetime.datetime.now().strftime("%Y-%m-%d %-I:%M %p")
    soon_date = (datetime.date.fromisoformat(today_str) + datetime.timedelta(days=7)).isoformat()

    project_rows = conn.execute(
        "SELECT project_id, title, status FROM project ORDER BY project_id"
    ).fetchall()

    rows: list[SimpleNamespace] = []
    for proj in project_rows:
        project_id = proj["project_id"]
        title_raw = (proj["title"] or "").strip()
        display_title = title_raw or project_id
        status = proj["status"] or "Planned"

        task_rows = conn.execute(
            "SELECT t.due_date, t.attributes"
            " FROM task t"
            " JOIN file f ON t.file_path = f.file_path"
            " WHERE t.checked = 0 AND f.deleted = 0"
            " AND EXISTS ("
            "   SELECT 1 FROM file_project fp"
            "   WHERE fp.file_path = t.file_path"
            "     AND fp.project_id = ?"
            " )",
            (project_id,),
        ).fetchall()

        buckets: dict[str, dict[str, int]] = {
            "due_today": {"count": 0, "minutes": 0},
            "past_due": {"count": 0, "minutes": 0},
            "due_soon": {"count": 0, "minutes": 0},
            "future_due": {"count": 0, "minutes": 0},
            "not_due": {"count": 0, "minutes": 0},
        }
        for task in task_rows:
            due_date = task["due_date"]
            est = _estimate_minutes_from_attributes(task["attributes"])
            if due_date is None:
                bucket_key = "not_due"
            elif due_date < today_str:
                bucket_key = "past_due"
            elif due_date == today_str:
                bucket_key = "due_today"
            elif due_date <= soon_date:
                bucket_key = "due_soon"
            else:
                bucket_key = "future_due"
            buckets[bucket_key]["count"] += 1
            buckets[bucket_key]["minutes"] += est

        last_updated_row = conn.execute(
            "SELECT f.modified, f.modified_date"
            " FROM file f"
            " JOIN file_project fp ON fp.file_path = f.file_path"
            " WHERE fp.project_id = ? AND f.deleted = 0 AND f.is_generated = 0"
            " ORDER BY"
            " CASE WHEN f.modified IS NULL OR f.modified <= 0 THEN 0 ELSE 1 END DESC,"
            " f.modified DESC,"
            " f.modified_date DESC,"
            " f.file_path ASC"
            " LIMIT 1",
            (project_id,),
        ).fetchone()

        current_streak = _calc_current_streak_for_projects(conn, [project_id])
        total_open = sum(v["count"] for v in buckets.values())
        health_ns = _calc_super_project_health(
            current_streak=current_streak,
            past_due_count=buckets["past_due"]["count"],
            past_due_minutes=buckets["past_due"]["minutes"],
            total_open_count=total_open,
            due_today_count=buckets["due_today"]["count"],
            due_soon_count=buckets["due_soon"]["count"],
        )

        rows.append(
            SimpleNamespace(
                project_label=display_title,
                project_link=_rel(this_file, out_dir / "Projects" / f"{project_id}.md"),
                status=status,
                current_streak=current_streak,
                health=health_ns.display,
                _health_score=health_ns.score,
                last_updated=_format_last_updated_row(last_updated_row),
                due_today_display=(
                    f"{buckets['due_today']['count']:,} ({buckets['due_today']['minutes']}m)"
                ),
                past_due_display=f"{buckets['past_due']['count']:,}",
                due_soon_display=f"{buckets['due_soon']['count']:,}",
                future_due_display=f"{buckets['future_due']['count']:,}",
                not_due_display=f"{buckets['not_due']['count']:,}",
                _status_order=_STATUS_SORT_ORDER.get(status, 99),
                _display_title=display_title,
            )
        )

    active_rows = [r for r in rows if r.status == "In Progress"]
    on_hold_rows = [r for r in rows if r.status == "On Hold"]
    planned_rows = [r for r in rows if r.status == "Planned"]
    complete_rows = [r for r in rows if r.status == "Complete"]
    cancelled_rows = [r for r in rows if r.status == "Cancelled"]

    # Active projects: worst-to-best health, then title.
    active_rows.sort(key=lambda r: (r._health_score, r._display_title.lower()))
    # Other statuses: title.
    on_hold_rows.sort(key=lambda r: r._display_title.lower())
    planned_rows.sort(key=lambda r: r._display_title.lower())
    complete_rows.sort(key=lambda r: r._display_title.lower())
    cancelled_rows.sort(key=lambda r: r._display_title.lower())

    content = env.get_template("projects_index.md.j2").render(
        generated_at=generated_at,
        dashboard_link=_rel(this_file, out_dir / _HOME_PAGE),
        active_rows=active_rows,
        on_hold_rows=on_hold_rows,
        planned_rows=planned_rows,
        complete_rows=complete_rows,
        cancelled_rows=cancelled_rows,
    )
    _write_file(conn, this_file, content, today_str)
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
    base_dir: Path,
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

    this_file = out_dir / "Projects" / f"{proj_id}.md"

    def source_link(file_path: str) -> str:
        return _rel(this_file, base_dir / file_path)

    def super_project_link(sp_id: str) -> str:
        return _rel(this_file, out_dir / _SUPER_PROJECTS_DIR / f"{sp_id}.md")

    content = env.get_template("project.md.j2").render(
        project=project_row,
        stats=stats,
        past_due_tasks=past_due_tasks,
        open_by_file=open_by_file,
        recently_completed=recently_completed,
        source_files=file_paths,
        source_link=source_link,
        super_project_link=super_project_link,
    )
    _write_file(conn, this_file, content, today_str)


def _render_single_project(
    env: jinja2.Environment,
    conn: sqlite3.Connection,
    out_dir: Path,
    base_dir: Path,
    project_id: str,
    today_str: str,
) -> int:
    row = conn.execute(
        "SELECT * FROM project WHERE project_id = ?", (project_id,)
    ).fetchone()
    if row is None:
        return 0
    _render_project_page(env, conn, out_dir, base_dir, row, today_str)
    return 1


def _render_all_projects(
    env: jinja2.Environment,
    conn: sqlite3.Connection,
    out_dir: Path,
    base_dir: Path,
    today_str: str,
) -> int:
    rows = conn.execute("SELECT * FROM project ORDER BY project_id").fetchall()
    for row in rows:
        _render_project_page(env, conn, out_dir, base_dir, row, today_str)
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
        expected.add(out_dir / _HOME_PAGE)
        expected.add(out_dir / _DUE_TODAY_PAGE)
        expected.add(out_dir / _PAST_DUE_PAGE)
        expected.add(out_dir / _DUE_SOON_PAGE)
        expected.add(out_dir / _FUTURE_DUE_PAGE)
        expected.add(out_dir / _NOT_DUE_PAGE)
        expected.add(out_dir / _WARNINGS_PAGE)
        expected.add(out_dir / _SUPER_PROJECTS_INDEX_PAGE)
        expected.add(out_dir / _PROJECTS_INDEX_PAGE)
    if target in ("all", "projects"):
        for r in conn.execute("SELECT project_id FROM project").fetchall():
            expected.add(out_dir / "Projects" / f"{r['project_id']}.md")
        for r in conn.execute("SELECT super_project_id FROM super_project").fetchall():
            expected.add(out_dir / _SUPER_PROJECTS_DIR / f"{r['super_project_id']}.md")
    if target in ("all", "history"):
        for r in conn.execute("SELECT DISTINCT metric_date FROM daily_metric").fetchall():
            expected.add(out_dir / _history_subpath(r["metric_date"]))
        # today is always generated
        expected.add(out_dir / _history_subpath(datetime.date.today().isoformat()))
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


def _render_all_super_projects(
    env: jinja2.Environment,
    conn: sqlite3.Connection,
    out_dir: Path,
    base_dir: Path,
    today_str: str,
) -> int:
    sp_rows = conn.execute(
        "SELECT * FROM super_project ORDER BY super_project_id"
    ).fetchall()
    count = 0
    for sp in sp_rows:
        sp_id = sp["super_project_id"]
        this_file = out_dir / _SUPER_PROJECTS_DIR / f"{sp_id}.md"
        active_project_rows = _build_super_project_project_rows(
            conn, out_dir, this_file, today_str, sp_id, "In Progress", True
        )
        on_hold_project_rows = _build_super_project_project_rows(
            conn, out_dir, this_file, today_str, sp_id, "On Hold", False
        )
        cancelled_project_rows = _build_super_project_project_rows(
            conn, out_dir, this_file, today_str, sp_id, "Cancelled", False
        )
        completed_project_rows = _build_super_project_project_rows(
            conn, out_dir, this_file, today_str, sp_id, "Complete", False
        )
        sp_title_raw = (sp["title"] or "").strip()
        sp_display_title = sp_title_raw or sp_id
        generated_at = datetime.datetime.now().strftime("%Y-%m-%d %-I:%M %p")

        def home_link(_tf: Path = this_file) -> str:
            return _rel(_tf, out_dir / _HOME_PAGE)

        def project_link(pid: str, _tf: Path = this_file) -> str:
            return _rel(_tf, out_dir / "Projects" / f"{pid}.md")

        content = env.get_template("super_project.md.j2").render(
            super_project=sp,
            super_project_display_title=sp_display_title,
            active_project_rows=active_project_rows,
            on_hold_project_rows=on_hold_project_rows,
            cancelled_project_rows=cancelled_project_rows,
            completed_project_rows=completed_project_rows,
            generated_at=generated_at,
            home_link=home_link,
            project_link=project_link,
        )
        _write_file(conn, this_file, content, today_str)
        count += 1
    return count


# ---------------------------------------------------------------------------
# History renderer
# ---------------------------------------------------------------------------


def _render_history(
    env: jinja2.Environment,
    conn: sqlite3.Connection,
    out_dir: Path,
    base_dir: Path,
    today_str: str,
    config: MatlockConfig,
) -> int:
    dates_rows = conn.execute(
        "SELECT DISTINCT metric_date FROM daily_metric ORDER BY metric_date"
    ).fetchall()
    dates = [r["metric_date"] for r in dates_rows]

    # Always include today — run rollup for today if not already present
    if today_str not in dates:
        run_rollup(config, conn, datetime.date.today())
        dates.append(today_str)

    count = 0
    for metric_date in dates:
        _render_history_page(env, conn, out_dir, base_dir, metric_date, today_str)
        count += 1
    return count


def _render_history_page(
    env: jinja2.Environment,
    conn: sqlite3.Connection,
    out_dir: Path,
    base_dir: Path,
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

    # Per-project breakdown rows (enhanced with created counts; omit all-zero rows)
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
        created_count = r["tasks_created_count"] or 0
        created_mins = r["tasks_created_minutes"] or 0
        completed_count = r["tasks_completed_count"] or 0
        completed_mins = r["tasks_completed_minutes"] or 0
        past_due = r["tasks_past_due_count"] or 0
        if not (created_count or created_mins or completed_count or completed_mins or past_due):
            continue
        project_rows.append(
            SimpleNamespace(
                project=proj,
                tasks_created_count=created_count,
                tasks_created_minutes=created_mins,
                tasks_completed_count=completed_count,
                tasks_completed_minutes=completed_mins,
                tasks_past_due_count=past_due,
            )
        )

    # Super-project summary (aggregate daily_metric via project → super_project join)
    sp_agg: dict[str | None, dict] = {}  # super_project_id → aggregated counts
    for r in dm_rows:
        pid = r["project_id"]
        if pid is not None:
            sp_row = conn.execute(
                "SELECT sp.super_project_id, sp.title FROM super_project sp"
                " JOIN project p ON p.super_project_id = sp.super_project_id"
                " WHERE p.project_id = ?",
                (pid,),
            ).fetchone()
            sp_id = sp_row["super_project_id"] if sp_row else None
            sp_title = sp_row["title"] if sp_row else None
        else:
            sp_id = None
            sp_title = None

        key = sp_id  # None = Unassigned
        if key not in sp_agg:
            sp_agg[key] = {
                "sp_id": sp_id,
                "sp_title": sp_title,
                "tasks_created_count": 0,
                "tasks_created_minutes": 0,
                "tasks_completed_count": 0,
                "tasks_completed_minutes": 0,
            }
        sp_agg[key]["tasks_created_count"] += r["tasks_created_count"] or 0
        sp_agg[key]["tasks_created_minutes"] += r["tasks_created_minutes"] or 0
        sp_agg[key]["tasks_completed_count"] += r["tasks_completed_count"] or 0
        sp_agg[key]["tasks_completed_minutes"] += r["tasks_completed_minutes"] or 0

    out_path = out_dir / _history_subpath(metric_date)
    super_project_summary: list[SimpleNamespace] = []
    for key, agg in sp_agg.items():
        sp_link = (
            _rel(out_path, out_dir / _SUPER_PROJECTS_DIR / f"{agg['sp_id']}.md")
            if agg["sp_id"]
            else None
        )
        super_project_title = agg["sp_title"] or (agg["sp_id"] or "Unassigned")
        super_project_summary.append(
            SimpleNamespace(
                super_project_title=super_project_title,
                sp_link=sp_link,
                tasks_created_count=agg["tasks_created_count"],
                tasks_created_minutes=agg["tasks_created_minutes"],
                tasks_completed_count=agg["tasks_completed_count"],
                tasks_completed_minutes=agg["tasks_completed_minutes"],
            )
        )
    # Filter out super-projects with all-zero counts, then sort
    super_project_summary = [
        sp for sp in super_project_summary
        if sp.tasks_created_count or sp.tasks_created_minutes
        or sp.tasks_completed_count or sp.tasks_completed_minutes
    ]
    super_project_summary.sort(
        key=lambda x: (x.super_project_title == "Unassigned", x.super_project_title.lower())
    )

    # File touch list for this date
    ft_rows = conn.execute(
        "SELECT file_path, event_type, modified FROM file_touch"
        " WHERE touch_date = ? ORDER BY modified DESC, file_path ASC",
        (metric_date,),
    ).fetchall()
    file_touches: list[SimpleNamespace] = []
    for ft in ft_rows:
        if ft["event_type"] != "deleted" and isinstance(ft["modified"], int) and ft["modified"] > 0:
            mod_dt = datetime.datetime.fromtimestamp(ft["modified"] / 1000)
            modified_text = mod_dt.strftime("%Y-%m-%d %-I:%M %p")
        else:
            modified_text = "—"
        file_touches.append(
            SimpleNamespace(
                file_path=ft["file_path"],
                event_type=ft["event_type"],
                modified_text=modified_text,
                source_link=_rel(out_path, base_dir / ft["file_path"]),
            )
        )

    # Task list for this date (collapse created+completed per task_id)
    dt_rows = conn.execute(
        "SELECT task_id, event_type, task_text, file_path, attributes"
        " FROM daily_task WHERE event_date = ? ORDER BY task_id, event_type",
        (metric_date,),
    ).fetchall()
    task_map: dict[str, dict] = {}
    for dt in dt_rows:
        tid = dt["task_id"]
        if tid not in task_map:
            task_map[tid] = {
                "task_text": dt["task_text"] or "",
                "file_path": dt["file_path"] or "",
                "attributes": dt["attributes"],
                "events": [],
            }
        task_map[tid]["events"].append(dt["event_type"])

    task_list: list[SimpleNamespace] = []
    for tid, t in task_map.items():
        events_display = " + ".join(sorted(set(t["events"])))
        estimate_mins = _estimate_minutes_from_attributes(t["attributes"])
        estimate_display = f"{estimate_mins}m" if estimate_mins else "—"

        # Find project for this file
        proj_link_row = conn.execute(
            "SELECT fp.project_id FROM file_project fp"
            " WHERE fp.file_path = ? LIMIT 1",
            (t["file_path"],),
        ).fetchone()
        proj_title = None
        proj_link = None
        if proj_link_row:
            proj_id = proj_link_row["project_id"]
            proj_title_row = conn.execute(
                "SELECT title FROM project WHERE project_id = ?", (proj_id,)
            ).fetchone()
            proj_title = (proj_title_row["title"] if proj_title_row else proj_id) or proj_id
            proj_link = _rel(out_path, out_dir / "Projects" / f"{proj_id}.md")

        task_list.append(
            SimpleNamespace(
                task_text=t["task_text"],
                file_path=t["file_path"],
                events_display=events_display,
                estimate_display=estimate_display,
                source_link=_rel(out_path, base_dir / t["file_path"]) if t["file_path"] else None,
                project_title=proj_title,
                project_link=proj_link,
            )
        )

    # Next date (first metric_date > metric_date in daily_metric)
    next_row = conn.execute(
        "SELECT MIN(metric_date) AS next_d FROM daily_metric WHERE metric_date > ?",
        (metric_date,),
    ).fetchone()
    next_date = next_row["next_d"] if next_row else None

    def project_link(pid: str) -> str:
        return _rel(out_path, out_dir / "Projects" / f"{pid}.md")

    def history_link(date_str: str) -> str:
        return _rel(out_path, out_dir / _history_subpath(date_str))

    dashboard_link = _rel(out_path, out_dir / _HOME_PAGE)
    generated_at = datetime.datetime.now().strftime("%Y-%m-%d %-I:%M %p")

    content = env.get_template("daily_history.md.j2").render(
        metric_date=metric_date,
        day_of_week=day_of_week,
        generated_at=generated_at,
        totals=totals,
        project_rows=project_rows,
        super_project_summary=super_project_summary,
        file_touches=file_touches,
        task_list=task_list,
        prev_date=prev_date,
        next_date=next_date,
        project_link=project_link,
        history_link=history_link,
        dashboard_link=dashboard_link,
    )
    _write_file(conn, out_path, content, today_str)
