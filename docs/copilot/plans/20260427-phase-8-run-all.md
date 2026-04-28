# 20260427-phase-8 Plan: `run-all` Command

> Date: 4/27/2026
> Owner: Copilot
> Branch: feat/phase-8-run-all
> Related docs: `docs/matlock-pipeline-specification.md`, `docs/matlock-cli.md`

## Problem Summary

All five pipeline stages are now individually callable via `matlock sync`, `parse`, `map-projects`, `rollup`, and `report`. However, there is no single command that runs the full pipeline end-to-end. Users who want to perform a complete nightly refresh must chain five commands manually.

The spec defines `matlock run-all` as a convenience command that runs all five stages in order with a single invocation, with optional flags to skip rollup (for mid-day runs) and force-sync (to re-hash all files).

## Goal

Implement the `matlock run-all` CLI subcommand that runs `sync → parse → map-projects → rollup → report` in sequence within a single DB connection, prints a consolidated summary, and exits non-zero if any stage fails.

## Scope

- **In scope:**
  - `matlock/cli.py` — add `run_all` subcommand with `--skip-rollup` and `--force-sync` flags
  - `tests/test_cli_run_all.py` — CLI tests using `CliRunner` and `tmp_path`
  - Update `docs/roadmap/index.md` Phase 8 row when complete

- **Out of scope:**
  - Server/daemon mode (Phase 9)
  - Any changes to existing stage logic
  - A separate `run_all.py` stage module — `run-all` is a CLI orchestration command only; it calls the existing `run_*` functions directly

## Constraints / Requirements

- Command name: `run-all` (registered via `@app.command(name="run-all")`).
- Stage execution order: `run_sync` → `run_parse` → `run_map_projects` → `run_rollup` → `run_report`.
- All five stages share a **single** `sqlite3.Connection` opened once and closed once (no per-stage reconnects).
- `--skip-rollup` (default `False`): when set, skip `run_rollup` AND still run `run_report`.
- `--force-sync` (default `False`): when set, pass `force=True` to `run_sync`.
- `rollup_date` defaults to `datetime.date.today() - timedelta(days=1)` (same as the standalone `rollup` command).
- `validate_config_paths()` called once by the CLI before any stage runs.
- `init_db(conn)` called once, before the first stage.
- Output: one summary line per stage, then a final `"run-all complete"` line. Format:

  ```
  Sync: N inserted, N updated, N unchanged, N deleted
  Parse: N parsed, N skipped, N tasks inserted, N tasks deleted
  Map-projects: N super-projects, N projects, N file-project links
  Rollup: YYYY-MM-DD, N rows written
  Report: N files written (all)
  run-all complete.
  ```

  When `--skip-rollup` is used, the `Rollup:` line is omitted.

- Exit code `1` if config load or path validation fails (same as other commands via `_load_and_validate`).

---

## Implementation Plan

| Step ID | Status | Goal | Planned Changes | Test Coverage |
|---|---|---|---|---|
| P8-S1 | Completed | Add `run-all` subcommand to `matlock/cli.py` | `cli.py` — new `run_all` command | `tests/test_cli_run_all.py` |
| P8-S2 | Completed | Regression + commit | Full suite green | `poetry run pytest` |

Status values: `Not Started` | `In Progress` | `Completed` | `Blocked`

---

## Step Details

### P8-S1 — Add `run-all` subcommand

**Files (expected):**
- `matlock/cli.py` *(modified)*
- `tests/test_cli_run_all.py` *(new)*

**Implementation notes:**

```python
@app.command(name="run-all")
def run_all(
    ctx: typer.Context,
    skip_rollup: bool = typer.Option(
        False, "--skip-rollup", help="Skip Stage IV rollup."
    ),
    force_sync: bool = typer.Option(
        False, "--force-sync", help="Pass --force to the sync stage."
    ),
) -> None:
    """Run all pipeline stages in sequence: sync → parse → map-projects → rollup → report."""
```

- Update the docstring at top of `cli.py` to include `matlock --config PATH run-all [--skip-rollup] [--force-sync]`.
- `rollup_date = datetime.date.today() - datetime.timedelta(days=1)` — fixed, not user-configurable in `run-all`.
- `report` always runs with `target="all"` and `project_id=None`.
- Open connection once: `conn = get_connection(cfg.db_path)` in a `try/finally`.
- Echo each stage result immediately after it completes (not buffered to the end).

**Definition of done:**
- `matlock run-all` exits 0 and prints all 5 (or 4, with `--skip-rollup`) stage summary lines plus `"run-all complete."`.
- `--skip-rollup` omits rollup line, still runs report.
- `--force-sync` causes sync to re-hash all files.
- Missing config exits 1.
- Tests cover: `--help`, exit code, summary format, `--skip-rollup`, `--force-sync`, missing config, DB rows after pipeline, subprocess smoke.

---

### P8-S2 — Regression + commit

**Files (expected):**
- `docs/copilot/plans/20260427-phase-8-run-all.md` *(this file — update steps to Completed)*
- `docs/roadmap/index.md` *(update Phase 8 row to Completed)*
- `docs/copilot/current-plan.md` *(point to Phase 9 as next)*

**Definition of done:**
- `poetry run pytest` passes with 0 failures (377 + new tests).
- All steps marked `Completed` in this plan doc.
- Commit on `feat/phase-8-run-all`.

---

## Acceptance Criteria

- `matlock run-all` exits 0 and prints a summary line for each stage run.
- `--skip-rollup` skips Stage IV and omits the `Rollup:` line; report still runs.
- `--force-sync` passes `force=True` to `run_sync`, causing all files to be rehashed.
- Invalid/missing config exits 1 with a descriptive error to stderr.
- All six tables are consistent after `run-all` completes (same state as running each stage individually in order).
- Full test suite passes with 0 failures.

## Risks / Notes

- **Single connection**: All stages share one connection. Each stage currently calls `conn.commit()` at its end — this is fine; the shared connection accumulates commits per stage normally.
- **No new module**: `run-all` is pure CLI orchestration; no `matlock/stages/run_all.py` is needed or created.
- **rollup_date fixed**: The `--date` override available on the standalone `rollup` command is intentionally omitted from `run-all`. The nightly use case always means "yesterday."

## Validation Plan

Run tests in this order:
1. Focused CLI tests: `poetry run pytest tests/test_cli_run_all.py -v`
2. Full suite: `poetry run pytest`

Record results:
- CLI tests: 26 passed, 0 failures
- Full suite: 403 passed, 0 failures

---

## Questions / Concerns

**Q1 — Summary output ordering**

Should the `run-all complete.` line appear on stdout even when `--skip-rollup` is set?

**Recommendation:** Yes — always print `run-all complete.` as the final line regardless of flags. It signals successful completion of all requested stages.

---

**Q2 — Stage error handling**

If an individual stage raises an unexpected exception (not a config error), should `run-all` catch it and exit 1 with a message, or let it propagate as a traceback?

- Option A: Let it propagate (Python traceback visible). Simple; consistent with standalone commands which also don't wrap stage exceptions.
- Option B: Wrap each stage call in `try/except Exception`, echo `"Error in <stage>: <msg>"`, and `raise typer.Exit(code=1)`.

**Recommendation:** Option A. Standalone stage commands do not wrap stage exceptions either. Tracebacks are informative for debugging and the spec does not prescribe error wrapping for `run-all`. Option B can be added in a future hardening phase.

---

**Q3 — `map-projects` re-run cost**

`run-all` always re-runs `map-projects` (full table rebuild). Is this acceptable even if `config.yaml` has not changed since the last run?

**Recommendation:** Yes — `map-projects` is already a fast, idempotent full rebuild. It is not worth adding a "dirty config" check for 0.1.x. Always re-run.
