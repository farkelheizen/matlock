# 20260427-phase-6 Plan: Stage IV — Rollup

> Date: 4/27/2026
> Owner: Copilot
> Branch: feat/phase-6-stage-rollup
> Related docs: `docs/matlock-pipeline-specification.md`, `docs/matlock-data-model.md`

## Problem Summary

The `daily_metric` table exists in the schema but is never populated. The `rollup` stage calculates historical metrics for a given date (defaulting to yesterday) and upserts one `daily_metric` row per project — plus one `NULL`-project row for tasks not linked to any project. It is designed to run once per day (nightly) and is fully idempotent: re-running for the same date produces the same result.

All input tables (`task`, `file`, `project`, `file_project`) are read-only from this stage's perspective. There is no `upsert_daily_metric` DB helper yet — it must be added to `db.py`.

## Goal

Implement `matlock/stages/rollup.py` (metric calculation + upsert), add the `matlock rollup` CLI subcommand to `matlock/cli.py` with a `--date` flag, add `upsert_daily_metric` to `matlock/db.py`, and write comprehensive tests covering all metric columns, the NULL-project row, idempotency, and the `--date` override.

## Scope

- **In scope:**
  - `matlock/db.py` — add `upsert_daily_metric(conn, row_dict)` helper
  - `matlock/stages/rollup.py` — `RollupResult`, `run_rollup(config, conn, rollup_date)`
  - `matlock/cli.py` — add `rollup` subcommand with `--date` option
  - `tests/test_rollup.py` — unit/integration tests using `":memory:"`
  - `tests/test_cli_rollup.py` — CLI tests using `CliRunner`
  - Update `docs/roadmap/index.md` Phase 6 row when complete

- **Out of scope:**
  - Streak calculation — spec says this is computed at report time, not stored
  - Report generation — Phase 7
  - Modifying any task/file/project rows

## Constraints / Requirements

- `run_rollup(config: MatlockConfig, conn: sqlite3.Connection, rollup_date: datetime.date) -> RollupResult`
- `rollup_date` is always passed explicitly by the CLI (defaulting to `datetime.date.today() - timedelta(days=1)`); no default in the stage function itself.
- One `daily_metric` row per project in the DB, plus one row with `project_id = NULL` for unassigned tasks.
- Unassigned tasks: tasks whose `file_path` does NOT appear in `file_project` at all (see D2).
- All `*_minutes` columns use `estimate` task attribute (seconds ÷ 60, integer division); NULL estimate → 0 (see D1).
- `INSERT OR REPLACE INTO daily_metric` is used to make re-runs idempotent; the `(metric_date, project_id)` pair is the composite PK.
- File activity counts are per-project (files linked via `file_project`), except the NULL row uses files with no project links (see D3).
- `files_created_count` uses a derived date from the `created` (epoch) column (see D4).
- `files_modified_count`: files where `modified_date = rollup_date` AND `deleted = 0` AND `is_generated = 0`.
- `files_deleted_count`: files where `deleted = 1` AND `modified_date = rollup_date` AND `is_generated = 0` (the day they were soft-deleted).
- All task metric queries filter `deleted = 0` on the joined file rows implicitly via `file_project` (D2 covers the unassigned case).
- `conn.commit()` called once at the end of `run_rollup()`.
- `validate_config_paths()` called by CLI, not inside stage.

---

## Implementation Plan

| Step ID | Status | Goal | Planned Changes | Test Coverage |
|---|---|---|---|---|
| P6-S1 | Completed | Add `upsert_daily_metric` to `db.py` | `matlock/db.py` — new helper | `tests/test_db.py` (extend existing) |
| P6-S2 | Completed | Implement `matlock/stages/rollup.py` | `run_rollup()`, `_calc_metrics()`, `RollupResult` | `tests/test_rollup.py` |
| P6-S3 | Completed | Add `rollup` subcommand to `matlock/cli.py` | `cli.py` — new `rollup` command with `--date` | `tests/test_cli_rollup.py` |
| P6-S4 | Completed | Regression + commit | Full suite green | `poetry run pytest` |

Status values: `Not Started` | `In Progress` | `Completed` | `Blocked`

---

## Step Details

### P6-S1 — Add `upsert_daily_metric` to `db.py`

**Files (expected):**
- `matlock/db.py` *(modified)*

**New helper:**

```python
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

def upsert_daily_metric(conn: sqlite3.Connection, row: dict) -> None:
    """Insert or replace a row in the ``daily_metric`` table."""
    conn.execute(
        "INSERT OR REPLACE INTO daily_metric ("
        + ", ".join(_DAILY_METRIC_COLUMNS)
        + ") VALUES ("
        + ", ".join(f":{c}" for c in _DAILY_METRIC_COLUMNS)
        + ")",
        row,
    )
```

Note: `project_id` may be `None` (Python `None` → SQLite `NULL`). `INSERT OR REPLACE` handles this correctly since the PK is `(metric_date, project_id)`.

**Definition of done:**
- `upsert_daily_metric` is importable from `matlock.db`.
- Inserting a row with `project_id=None` succeeds.
- Re-inserting the same `(metric_date, project_id)` replaces the row.

---

### P6-S2 — Implement `matlock/stages/rollup.py`

**Files (expected):**
- `matlock/stages/rollup.py` *(new)*

**`RollupResult` dataclass:**

```python
@dataclasses.dataclass
class RollupResult:
    rollup_date: str          # YYYY-MM-DD string of the date rolled up
    rows_written: int = 0     # total daily_metric rows upserted
```

**`_date_to_str(d: datetime.date) -> str`** — `d.strftime("%Y-%m-%d")`

**`_epoch_to_date_str(epoch_ms: int) -> str`** — converts a Unix epoch millisecond integer to a YYYY-MM-DD UTC string. (The `file.created` column stores milliseconds, matching the convention from Phase 3.)

**`_estimate_minutes(attributes_json: str | None) -> int`** — parse `json.loads(attributes_json or "{}")`, extract `"estimate"` key (seconds int), return `value // 60`. Returns `0` if key absent or value is `None`.

**`_calc_project_metrics(conn, rollup_date_str, tomorrow_str, project_id, file_paths) -> dict`** — given a list of `file_path` values belonging to this project (or an indicator for the unassigned case), execute COUNT/SUM queries and return a dict ready for `upsert_daily_metric`:

```sql
-- tasks_created_count / tasks_created_minutes
SELECT attributes FROM task
WHERE file_path IN (...) AND created_date = :rollup_date

-- tasks_completed_count / tasks_completed_minutes
SELECT attributes FROM task
WHERE file_path IN (...) AND act_comp_date = :rollup_date AND checked = 1

-- tasks_due_tomorrow_count / tasks_due_tomorrow_minutes
SELECT attributes FROM task
WHERE file_path IN (...) AND due_date = :tomorrow AND checked = 0

-- tasks_past_due_count / tasks_past_due_minutes
SELECT attributes FROM task
WHERE file_path IN (...) AND due_date < :rollup_date AND checked = 0

-- tasks_future_due_count / tasks_future_due_minutes
SELECT attributes FROM task
WHERE file_path IN (...) AND due_date > :rollup_date AND checked = 0
```

`*_minutes` is computed Python-side by summing `_estimate_minutes(row["attributes"])` for each returned row.

**File activity (per project, D3):**

```sql
-- files_created_count: files belonging to this project created on rollup_date
-- uses created epoch → date conversion (D4)

-- files_modified_count
SELECT COUNT(*) FROM file
WHERE file_path IN (...) AND modified_date = :rollup_date
  AND deleted = 0 AND is_generated = 0

-- files_deleted_count
SELECT COUNT(*) FROM file
WHERE file_path IN (...) AND deleted = 1 AND modified_date = :rollup_date
  AND is_generated = 0
```

For `files_created_count`: filter `file` rows in the set where `created` epoch converts to `rollup_date` (D4).

**`run_rollup(config, conn, rollup_date) -> RollupResult`:**

```
rollup_date_str = _date_to_str(rollup_date)
tomorrow_str    = _date_to_str(rollup_date + timedelta(days=1))

# 1. Collect project → file_paths mapping from file_project
project_file_map = defaultdict(list)
for row in conn.execute("SELECT project_id, file_path FROM file_project"):
    project_file_map[row["project_id"]].append(row["file_path"])

# 2. Collect all file_paths that ARE linked to any project (for unassigned calc)
linked_paths = set of all file_path values in file_project

# 3. Collect unassigned file_paths: in file table (deleted=0, is_generated=0) but NOT in linked_paths
unassigned_paths = [fp for fp in all_eligible_file_paths if fp not in linked_paths]

# 4. Write one row per project
rows_written = 0
for project_id, file_paths in project_file_map.items():
    row = _calc_project_metrics(conn, rollup_date_str, tomorrow_str, project_id, file_paths)
    upsert_daily_metric(conn, row)
    rows_written += 1

# 5. Write the NULL row for unassigned tasks/files
null_row = _calc_project_metrics(conn, rollup_date_str, tomorrow_str, None, unassigned_paths)
upsert_daily_metric(conn, null_row)
rows_written += 1

conn.commit()
return RollupResult(rollup_date=rollup_date_str, rows_written=rows_written)
```

**Definition of done:**
- For a vault with 2 projects + some unassigned files, `run_rollup` produces 3 `daily_metric` rows.
- Re-running the same date produces identical rows (idempotent).
- All 15 metric columns are populated with integers (never NULL).
- `project_id = NULL` row exists for unassigned tasks.

---

### P6-S3 — Add `rollup` Subcommand to `matlock/cli.py`

**Files (expected):**
- `matlock/cli.py` *(modified)*

**New command:**

```python
@app.command()
def rollup(
    ctx: typer.Context,
    date: str = typer.Option(
        None,
        "--date",
        help="Date to roll up (YYYY-MM-DD). Defaults to yesterday.",
    ),
) -> None:
    """Calculate daily metrics for a given date (default: yesterday)."""
    cfg = _load_and_validate(ctx.obj[_CONFIG_KEY])

    if date is None:
        rollup_date = datetime.date.today() - datetime.timedelta(days=1)
    else:
        try:
            rollup_date = datetime.date.fromisoformat(date)
        except ValueError:
            typer.echo(f"Error: invalid date '{date}' — expected YYYY-MM-DD", err=True)
            raise typer.Exit(code=1)

    conn = get_connection(cfg.db_path)
    try:
        init_db(conn)
        result = run_rollup(cfg, conn, rollup_date)
    finally:
        conn.close()

    typer.echo(
        f"Rollup complete: {result.rollup_date}, {result.rows_written} rows written"
    )
```

**Definition of done:**
- `matlock rollup` defaults to yesterday's date.
- `matlock rollup --date 2026-04-01` rolls up for the specified date.
- Invalid date string exits with code 1 and a helpful message.

---

### P6-S4 — Regression + Commit

**Files (expected):**
- `docs/roadmap/index.md` — Phase 6 row updated
- `docs/copilot/current-plan.md` — updated

**Definition of done:**
- `poetry run pytest` passes with 258+ tests, zero failures.

---

## Acceptance Criteria

- `daily_metric` has one row per project + one NULL row after `rollup`.
- All 15 metric columns contain correct integer values (not NULL).
- Task counts filter by `file_path` membership in the project's linked files.
- `estimate` attribute (seconds) is correctly converted to minutes (integer division).
- Re-running `rollup` for the same date produces identical rows (idempotent via `INSERT OR REPLACE`).
- `matlock rollup --date 2026-01-15` uses 2026-01-15 as the rollup date.
- An invalid `--date` value exits with code 1.
- Full suite passes with zero regressions.

## Risks / Notes

- **`project_id = NULL` PK**: SQLite treats two NULLs as distinct in a UNIQUE constraint but as equal in a PRIMARY KEY context with `INSERT OR REPLACE`. This is the correct behaviour for idempotency — test this explicitly.
- **Empty vault**: If no tasks exist, all count/minutes columns should be `0`, not `NULL`. `_calc_project_metrics` must default to `0` when no rows match.
- **`created` epoch is milliseconds**: Phase 3 stores `created` and `modified` as milliseconds (Unix epoch × 1000). `_epoch_to_date_str` must divide by 1000 before converting.
- **No projects defined**: If `file_project` is empty, only the NULL row is written. `rows_written = 1`.
- **`IN ()` with empty list**: Python's sqlite3 does not support empty `IN ()` clauses. `_calc_project_metrics` must short-circuit to zero counts when `file_paths` is empty.

## Validation Plan

Run tests in this order:
1. DB helper: `poetry run pytest tests/test_db.py -v -k daily_metric`
2. Focused: `poetry run pytest tests/test_rollup.py -v`
3. CLI: `poetry run pytest tests/test_cli_rollup.py -v`
4. Full suite: `poetry run pytest`

Record results:
- DB helper: 1 test updated in test_db.py (nullable project_id)
- Focused: 43 passed (test_rollup.py)
- CLI: 15 passed (test_cli_rollup.py)
- Full suite: 316 passed, 0 failures

---

## Design Decisions (Resolved)

**D1 — `estimate` → minutes: Python-side.**
Fetch all matching rows' `attributes` JSON, parse in Python, sum `value // 60`. Consistent with the Phase 5 approach; avoids SQLite JSON extension dependency.

**D2 — Unassigned tasks: file-based.**
Collect the set of all `file_path` values in `file_project`; unassigned files are all non-deleted, non-generated files NOT in that set.

**D3 — File activity counts: per-project.**
Each project row counts only files linked to it via `file_project`. The NULL row counts files with no project links. A file belonging to two projects counts once in each project's row.

**D4 — `files_created_count`: derive from `created` epoch.**
Use SQL: `date(created / 1000, 'unixepoch') = :rollup_date`. The `created` column is epoch milliseconds (established in Phase 3).

**D5 — `upsert_daily_metric`: add to `db.py`.**
Consistent with `upsert_file`, `upsert_task`, and all prior DB helpers. Stages call helpers only; no raw SQL in stage files.
