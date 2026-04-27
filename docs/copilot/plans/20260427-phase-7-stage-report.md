# 20260427-phase-7 Plan: Stage V — Report

> Date: 4/27/2026
> Owner: Copilot
> Branch: feat/phase-7-stage-report
> Related docs: `docs/matlock-pipeline-specification.md`, `docs/matlock-generated-reports.md`, `docs/matlock-data-model.md`

## Problem Summary

All five pipeline stages are defined in the spec, but Stage V (`report`) has not yet been implemented. The `daily_metric` table is now populated by the rollup stage, the `project` / `super_project` / `file_project` tables are maintained by map-projects, and the `task` table is maintained by parse. The data needed to render all four dashboard types is available.

The report stage is a pure "data-to-text" step: query the DB, render Jinja2 Markdown templates, write the results to `output_directory` (`_Matlock/`), and register each written file in the `file` table with `is_generated = 1` so the sync stage ignores it.

## Goal

Implement `matlock/stages/report.py` (data assembly + Jinja2 rendering + file writing), create the four Jinja2 templates under `matlock/templates/`, add the `matlock report` CLI subcommand with `--target` and `--project-id` flags, and write comprehensive tests covering rendering output, file registration, target filtering, and error cases.

## Scope

- **In scope:**
  - `matlock/templates/` — 4 Jinja2 template files (verbatim from spec)
  - `matlock/stages/report.py` — `ReportResult`, `run_report()`, data assemblers, streak/heatmap helpers
  - `matlock/cli.py` — add `report` subcommand with `--target` and `--project-id` options
  - `tests/test_report.py` — unit/integration tests using `":memory:"` + `tmp_path`
  - `tests/test_cli_report.py` — CLI tests using `CliRunner`
  - Update `docs/roadmap/index.md` Phase 7 row when complete

- **Out of scope:**
  - Modifying any task/file/project source rows
  - Recalculating `daily_metric` rows (that is the rollup stage)
  - Implementing `run-all` (Phase 8)
  - Server mode or file-watcher logic (Phase 9)

## Constraints / Requirements

- `run_report(config: MatlockConfig, conn: sqlite3.Connection, target: str = "all", project_id: str | None = None) -> ReportResult`
- `target` must be one of: `"all"`, `"dashboard"`, `"projects"`, `"history"`.
- Templates live at `matlock/templates/` (package-relative, loaded via `importlib.resources` or `Path(__file__).parent.parent / "templates"`).
- Output directory is `config.output_directory` (e.g. `_Matlock/`). Sub-directories `Projects/`, `SuperProjects/`, `History/` are created as needed.
- Every written file is registered in the `file` table via `upsert_file` with `is_generated = 1`, `needs_parsing = 0`, `deleted = 0`.
- The `basename` Jinja2 filter must be registered as a custom filter (`os.path.basename`).
- `conn.commit()` called once at the end of `run_report()`.
- `validate_config_paths()` is called by the CLI, not inside the stage.
- `poetry add jinja2` — Jinja2 must be added as a project dependency.

---

## Implementation Plan

| Step ID | Status | Goal | Planned Changes | Test Coverage |
|---|---|---|---|---|
| P7-S1 | Completed | Create Jinja2 templates | `matlock/templates/` — 4 `.md.j2` files | Manual render smoke test in P7-S2 tests |
| P7-S2 | Completed | Implement `matlock/stages/report.py` | `run_report()`, `ReportResult`, data assemblers, streak + heatmap helpers | `tests/test_report.py` |
| P7-S3 | Completed | Add `report` CLI subcommand | `matlock/cli.py` — new `report` command with `--target` and `--project-id` | `tests/test_cli_report.py` |
| P7-S4 | Completed | Regression + commit | Full suite green | `poetry run pytest` |

Status values: `Not Started` | `In Progress` | `Completed` | `Blocked`

---

## Step Details

### P7-S1 — Create Jinja2 templates

**Files (expected):**
- `matlock/templates/daily_dashboard.md.j2` *(new)*
- `matlock/templates/project.md.j2` *(new)*
- `matlock/templates/super_project.md.j2` *(new)*
- `matlock/templates/daily_history.md.j2` *(new)*
- `matlock/templates/__init__.py` *(new, empty — makes the directory a package-accessible resource)*

**Implementation notes:**
- Template content is verbatim from `docs/matlock-generated-reports.md`. No logic changes.
- The `basename` filter is registered at the Jinja2 environment level in `report.py`, not in the templates themselves — templates just use `| basename` as documented.

**Definition of done:**
- All 4 template files exist under `matlock/templates/`.
- `jinja2.Environment(...).get_template("daily_dashboard.md.j2")` succeeds without error in a test.

---

### P7-S2 — Implement `matlock/stages/report.py`

**Files (expected):**
- `matlock/stages/report.py` *(new)*
- `tests/test_report.py` *(new)*

**Key public API:**

```python
@dataclass
class ReportResult:
    files_written: int       # total files written this run
    target: str              # "all" | "dashboard" | "projects" | "history"

def run_report(
    config: MatlockConfig,
    conn: sqlite3.Connection,
    target: str = "all",
    project_id: str | None = None,
) -> ReportResult:
    ...
```

**Internal structure:**

```
run_report()
├── _build_jinja_env()              # load templates dir, register basename filter
├── _render_dashboard(env, conn, out_dir)
│   ├── _calc_streak(conn) -> (current, longest, longest_end)
│   ├── _calc_heatmap(conn) -> dict[str, list[str]]
│   └── _get_due_tasks(conn) -> (past_due, due_today)
├── _render_projects(env, conn, out_dir, project_id=None)
│   └── _calc_project_stats(conn, project_id) -> stats object
├── _render_super_projects(env, conn, out_dir)
│   └── _calc_super_project_stats(conn, sp_id) -> stats + child projects
└── _render_history(env, conn, out_dir)
    └── one page per date in daily_metric
```

**Implementation notes:**

- **Jinja2 Environment**: use `jinja2.FileSystemLoader` pointing at `Path(__file__).parent.parent / "templates"`. Register `basename` as `env.filters["basename"] = os.path.basename`.
- **Streak calculation**: query `daily_metric` grouped by `metric_date`, summing `tasks_completed_count` across all `project_id` values (including NULL). Walk the resulting date-ordered series to find the current streak (consecutive days ending on the most recent `daily_metric` date where sum > 0) and the longest streak.
- **Heatmap**: requires the last 5 calendar weeks anchored on today. For each day-of-week (Mon–Sun), build a list of 5 emoji values (`⬜`, `🟩`, `🟦`, `🟪`) based on `tasks_completed_count` summed per date from `daily_metric`. Dates missing from `daily_metric` are treated as 0.
- **Project stats**: query `task` table directly (filtered by `file_project` join) for `open_count`, `completed_count`, `past_due_count`, and `*_minutes`. Scoped to the project's linked files.
- **Super-project stats**: aggregate child projects (joined via `super_project_id` FK).
- **History pages**: one file per distinct `metric_date` in `daily_metric`. Build `project_rows` by querying all `daily_metric` rows for that date, joining `project` for title.
- **File registration**: after writing each file, call `upsert_file(conn, {..., "is_generated": 1, "needs_parsing": 0, "deleted": 0, "sha256": None, ...})`. Use `str(output_path)` as `file_path`.
- Output directories are created with `Path.mkdir(parents=True, exist_ok=True)`.

**Definition of done:**
- `run_report(config, conn)` writes `000_Daily_Dashboard.md`, one page per project, one per super-project, and one history page per `daily_metric` date.
- All written files appear in the `file` table with `is_generated = 1`.
- Tests cover: empty DB (no projects, no history), dashboard with streak data, project page content, super-project aggregation, `--target` filtering skips correct dashboards, `--project-id` limits project pages, idempotency (second run overwrites without error).

---

### P7-S3 — Add `report` CLI subcommand

**Files (expected):**
- `matlock/cli.py` *(modified)*
- `tests/test_cli_report.py` *(new)*

**Implementation notes:**

```python
@app.command()
def report(
    ctx: typer.Context,
    target: str = typer.Option(
        "all",
        "--target",
        help="Which dashboards to regenerate: all, dashboard, projects, history.",
    ),
    project_id: str = typer.Option(
        None,
        "--project-id",
        help="Regenerate a single project page (implies --target projects).",
    ),
) -> None:
    """Render Jinja2 Markdown dashboards into the output directory."""
```

- Invalid `target` values should exit 1 with a clear error message.
- Output: `f"Report complete: {result.files_written} files written ({result.target})"`
- Update docstring at top of `cli.py` to include `matlock --config PATH report [--target {all,dashboard,projects,history}] [--project-id ID]`.

**Definition of done:**
- `matlock --config PATH report` exits 0 and prints summary.
- `--target dashboard`, `--target projects`, `--target history` each exit 0.
- `--target invalid` exits 1 with error to stderr.
- `--project-id` triggers single-project generation.
- CLI tests cover: `--help`, exit codes, summary format, `--target` variants, invalid target, `--project-id`, DB rows written.

---

### P7-S4 — Regression + commit

**Files (expected):**
- `docs/copilot/plans/20260427-phase-7-stage-report.md` *(this file — update steps to Completed)*
- `docs/roadmap/index.md` *(update Phase 7 row to Completed)*
- `docs/copilot/current-plan.md` *(point to Phase 8 as next)*

**Definition of done:**
- `poetry run pytest` passes with 0 failures (316 + new tests).
- All steps marked `Completed` in this plan doc.
- Commit on `feat/phase-7-stage-report`.

---

## Acceptance Criteria

- `matlock report` exits 0 and writes all four dashboard types into `output_directory`.
- All written files are registered in `file` with `is_generated = 1`.
- `--target dashboard` writes only `000_Daily_Dashboard.md`.
- `--target projects` writes one `.md` per project (and super-project pages).
- `--target history` writes one `.md` per date in `daily_metric`.
- `--project-id ID` writes only `_Matlock/Projects/<ID>.md`.
- Invalid `--target` exits 1 with a descriptive error.
- Running `report` twice for the same data produces identical output files (idempotent).
- No source files (`is_generated = 0`) are modified.
- Full test suite passes with 0 failures.

## Risks / Notes

- **Jinja2 dependency**: must be added via `poetry add jinja2` before implementation. Current `pyproject.toml` does not list it.
- **Heatmap anchor date**: the "current week" column is anchored on `datetime.date.today()` at render time, not on any `daily_metric` date. This means the heatmap always shows real calendar weeks ending today, which may include days with no `daily_metric` data (shown as `⬜`).
- **`upsert_file` for generated files**: several columns (`sha256`, `created`, `modified`, `length`, `word_count`, `meta_data`, `file_ext`) are not meaningful for generated files. Use `None` / `0` / `""` defaults rather than computing them — sync already excludes `is_generated = 1` rows.
- **Template path portability**: using `Path(__file__).parent.parent / "templates"` is simpler than `importlib.resources` for a flat file layout; `importlib.resources` would be needed only for zip/wheel distribution. Accept this limitation for 0.1.x.

## Validation Plan

Run tests in this order:
1. Focused tests for the stage: `poetry run pytest tests/test_report.py -v`
2. CLI tests: `poetry run pytest tests/test_cli_report.py -v`
3. Full suite: `poetry run pytest`

Record results:
- Focused: 41 passed (tests/test_report.py)
- CLI: 20 passed (tests/test_cli_report.py)
- Full suite: 377 passed, 0 failures

---

## Design Decisions (Resolved)

**D1 — History file scope:** Option A accepted. On `report` or `--target all`, write one history file per every distinct `metric_date` in `daily_metric` (full rebuild). Simple, predictable, idempotent.

**D2 — Streak aggregate source:** Option A accepted. Sum `tasks_completed_count` across ALL `daily_metric` rows for the date (all project_ids including NULL). A date is "active" if the sum > 0. Ensures project-linked completions are not missed.

**D3 — `--project-id` scope:** Option A accepted. Only `_Matlock/Projects/<ID>.md` is written. No cascade to super-project page or dashboard. Targeted regeneration only.

**D4 — `upsert_file` for generated files:** Option A accepted. Use `None` / `0` / `""` for non-essential columns (`sha256`, `created`, `modified`, `length`, `word_count`, `meta_data`, `file_ext`). Set `is_generated = 1`, `needs_parsing = 0`, `deleted = 0`, `modified_date` = today's date string.

**D5 — Jinja2 dependency:** Main dependency. Added via `poetry add jinja2`.
