# 20260427-phase-9 Plan: Server Daemon

> Date: 4/27/2026
> Owner: Copilot
> Branch: feat/phase-9-server-daemon
> Related docs: `docs/matlock-pipeline-specification.md` (Server Mode section), `docs/matlock-cli.md` (`matlock server`), `docs/matlock-configuration.md`

## Problem Summary

All pipeline stages are now individually runnable and `run-all` provides a one-shot full-pipeline command. However, there is no persistent process that keeps dashboards up-to-date automatically as the vault changes throughout the day.

The spec defines `matlock server` as a daemon with three integrated triggers:
1. A **file watcher** that reacts to vault changes in real time.
2. A **debouncer** that coalesces rapid file-change bursts before triggering a report.
3. A **nightly scheduler** that runs `rollup` at midnight and then does a full `report` rebuild.

## Goal

Implement `matlock server`: a long-running process that watches `base_directory` for file changes, debounces report generation, and schedules nightly rollup + full rebuild — all with a clean SIGINT shutdown.

## Scope

- **In scope:**
  - `matlock/server.py` — server orchestration module
  - `matlock/cli.py` — add `server` subcommand
  - `pyproject.toml` + `poetry.lock` — add `watchdog` dependency
  - `tests/test_server.py` — unit/integration tests (mocked watcher + scheduler)
  - `tests/test_cli_server.py` — CLI tests (`--help`, `--debounce`, missing config, `--version`)
  - Update `docs/roadmap/index.md` Phase 9 row when complete
  - Update `docs/copilot/current-plan.md` when complete

- **Out of scope:**
  - `launchd` / `systemd` service files
  - Log rotation or structured logging
  - Any GUI or web interface
  - Changes to existing stage logic

## Constraints / Requirements

- **Module:** `matlock/server.py`. The `server` CLI subcommand calls `run_server(config, debounce_seconds=None)`.
- **File watcher library:** `watchdog` (add via `poetry add watchdog`). Minimum version TBD from latest stable.
- **Three triggers (all must be present):**
  1. `FileSystemEventHandler` subclass — on create/modify/delete under `base_directory`, calls `run_sync` + `run_parse` for the changed file, then marks affected project IDs dirty.
  2. Debouncer thread — monitors the dirty-project queue; after `debounce_seconds` of quiet, calls `run_report` for dirty projects.
  3. Scheduler thread — fires at 00:01 local time each night; calls `run_rollup` then `run_report(target="all")`.
- **Dirty-project tracking:** in-memory set or queue; cleared after each report run.
- **Shutdown:** `SIGINT` (Ctrl+C) must cleanly stop the watcher, debouncer, and scheduler threads.
- **`--debounce SECONDS`:** overrides `config.debounce_seconds`. Accepts a positive integer.
- **Each file-change event opens its own short-lived DB connection** (same pattern as the individual stage CLI commands). The scheduler and debouncer also each open and close their own connection per run.
- **`output_directory` is excluded from the watcher** — same as sync's ignore list, to avoid an infinite loop of generated files triggering re-runs.
- **Logging:** `print()` / `typer.echo()` to stdout is acceptable for 0.1.x. No structured logging library required.
- **Python stdlib only** for threading and scheduling (no `APScheduler`, `celery`, etc.).

---

## Implementation Plan

| Step ID | Status | Goal | Planned Changes | Test Coverage |
|---|---|---|---|---|
| P9-S1 | Completed | Add `watchdog` dependency | `pyproject.toml`, `poetry.lock` | N/A |
| P9-S2 | Completed | Implement `matlock/server.py` | `server.py` — watcher, debouncer, scheduler, `run_server()` | `tests/test_server.py` |
| P9-S3 | Completed | Add `server` CLI subcommand | `matlock/cli.py` | `tests/test_cli_server.py` |
| P9-S4 | Completed | Regression + commit | Full suite green | `poetry run pytest` |

Status values: `Not Started` | `In Progress` | `Completed` | `Blocked`

---

## Step Details

### P9-S1 — Add `watchdog` dependency

**Files (expected):**
- `pyproject.toml` *(modified)*
- `poetry.lock` *(modified)*

**Implementation notes:**
- Run `poetry add watchdog` to pin the latest stable release.
- Confirm `from watchdog.observers import Observer` and `from watchdog.events import FileSystemEventHandler` import cleanly after install.

**Definition of done:**
- `watchdog` appears in `pyproject.toml` dependencies.
- `poetry install` completes with no errors.

---

### P9-S2 — Implement `matlock/server.py`

**Files (expected):**
- `matlock/server.py` *(new)*
- `tests/test_server.py` *(new)*

**Implementation notes:**

Public interface:
```python
def run_server(config: MatlockConfig, debounce_seconds: int | None = None) -> None:
    """Start the file watcher, debouncer, and scheduler. Blocks until SIGINT."""
```

**Watcher (`VaultEventHandler`):**
- Subclass `watchdog.events.FileSystemEventHandler`.
- `on_created`, `on_modified`, `on_deleted`: filter for `.md` files; skip paths under `output_directory` and `ignore_dirs`.
- On event: open a short-lived connection, call `run_sync` with the specific file if possible (or a full sync if needed), then `run_parse`. Determine which `project_id` values are affected via a DB query on `file_project`, and add them to `dirty_projects` (a `threading.Event`-guarded set).
- Log to stdout: `"[watcher] <path> <event_type>"`.

**Debouncer thread:**
- Loop: wait for `dirty_projects` to be non-empty. Once non-empty, sleep `debounce_seconds`. If no new event arrived during sleep, pop all dirty project IDs, open connection, call `run_report` for each dirty project, clear dirty set. Log `"[debouncer] report triggered for N projects"`.
- Implemented as a `threading.Thread(daemon=True)`.

**Scheduler thread:**
- Loop: compute seconds until next 00:01. Sleep that long. On wake: open connection, call `run_rollup(config, conn, date.today() - timedelta(days=1))`, then `run_report(config, conn, target="all")`. Log `"[scheduler] nightly rollup+report complete"`.
- Implemented as a `threading.Thread(daemon=True)`.

**Shutdown:**
- `run_server` catches `KeyboardInterrupt`, calls `observer.stop()`, `observer.join()`, sets a `threading.Event` stop flag that the debouncer and scheduler threads check.

**Definition of done:**
- `run_server()` starts without error against a real (tmp) vault in tests.
- Injecting a mock file event causes `run_sync` + `run_parse` to be called.
- Debouncer fires `run_report` after the configured quiet period.
- Scheduler fires `run_rollup` + `run_report` on the mocked midnight tick.
- SIGINT (simulated via stop event) terminates all threads cleanly.

---

### P9-S3 — Add `server` CLI subcommand

**Files (expected):**
- `matlock/cli.py` *(modified)*
- `tests/test_cli_server.py` *(new)*

**Implementation notes:**

```python
@app.command()
def server(
    ctx: typer.Context,
    debounce: int = typer.Option(
        None,
        "--debounce",
        help="Idle seconds before triggering report after file changes. "
             "Overrides config debounce_seconds.",
    ),
) -> None:
    """Run the server daemon: watch vault, debounce reports, schedule nightly rollup."""
    cfg = _load_and_validate(ctx.obj[_CONFIG_KEY])
    run_server(cfg, debounce_seconds=debounce)
```

- Update the docstring at top of `cli.py` to include `matlock --config PATH server [--debounce SECONDS]`.
- CLI tests are intentionally shallow — `run_server()` is mocked so tests don't start real threads. Cover: `--help`, `--debounce`, missing config, debounce value passed through.

**Definition of done:**
- `matlock server --help` exits 0 and shows `--debounce` flag.
- Missing config exits 1.
- `run_server` is invoked with the correct `debounce_seconds` value.

---

### P9-S4 — Regression + commit

**Files (expected):**
- `docs/copilot/plans/20260427-phase-9-server-daemon.md` *(this file — steps updated to Completed)*
- `docs/roadmap/index.md` *(Phase 9 row updated to Completed)*
- `docs/copilot/current-plan.md` *(updated)*

**Definition of done:**
- `poetry run pytest` passes with 0 failures.
- All steps marked `Completed` in this plan doc.
- Commit on `feat/phase-9-server-daemon`.

---

## Acceptance Criteria

- `matlock server` starts without error and prints a startup message to stdout.
- File changes under `base_directory` trigger `sync` + `parse` within a few seconds.
- After `debounce_seconds` of quiet, `report` is triggered for affected projects.
- At midnight (00:01), `rollup` then full `report` run automatically.
- `SIGINT` (Ctrl+C) terminates the daemon cleanly with no zombie threads.
- `--debounce SECONDS` overrides `config.debounce_seconds`.
- Missing/invalid config exits 1.
- Full test suite passes with 0 failures.

## Risks / Notes

- **`watchdog` threading model:** `watchdog` dispatches events on its own observer thread. All stage calls from the handler must be thread-safe. Since each call opens its own connection, this is safe — SQLite WAL mode allows concurrent readers.
- **Infinite-loop guard:** The watcher must exclude `output_directory` (and `ignore_dirs`) or generated dashboard writes will trigger re-parse loops.
- **Test harness for long-running process:** `run_server()` blocks; tests must either mock `watchdog.Observer` or inject a stop event to terminate quickly. This is the main test complexity.
- **Scheduler drift:** `sleep(seconds_until_midnight)` can drift across DST transitions. Acceptable for 0.1.x.
- **No `--file` targeting in watcher:** The watcher calls full `run_sync` + `run_parse` (not per-file targeted variants) to keep implementation simple. This is consistent with how the spec describes it but may be revisited.

## Validation Plan

Run tests in this order:
1. Focused server unit tests: `poetry run pytest tests/test_server.py -v`
2. Focused CLI tests: `poetry run pytest tests/test_cli_server.py -v`
3. Full suite: `poetry run pytest`

Record results:
- Server tests: 16 passed, 0 failures
- CLI tests: 11 passed, 0 failures
- Full suite: 430 passed, 0 failures

---

## Design Decisions (Resolved)

- **Q1:** Full vault walk (`run_sync` as-is) on every file change — not per-file targeted. Option A.
- **Q2:** Debouncer always calls `run_report(target="all")` — not per-project targeted. Option A.
- **Q3:** `_pipeline_lock = threading.Lock()` prevents concurrent stage execution. Option A.
- **Q4:** `print()` / `typer_echo()` to stdout — no `logging` module. Option A.

When the watcher detects a change to a single file, should it call `run_sync(cfg, conn)` (full vault walk) or perform a targeted single-file sync (not currently exposed by `run_sync`)?

- Option A: Full vault walk (`run_sync` as-is). Simple; correct; slightly over-scans when only one file changed.
- Option B: Single-file sync — add a `file_path` parameter to `run_sync` to limit the walk to one file. More efficient; requires a new code path in `run_sync`.

**Recommendation:** Option A for Phase 9. The vault is typically small and a full sync on every change is fast enough. Option B can be added as a targeted optimization in a future phase.

---

**Q2 — Debouncer behaviour: all dirty projects vs. targeted report**

When the debouncer fires, should it:

- Option A: Run `run_report(cfg, conn, target="all")` — ignores the dirty set, always rebuilds everything. Simpler; no risk of missing a super-project dashboard update.
- Option B: Run `run_report(cfg, conn, project_id=pid)` per dirty project ID. Targeted; more efficient for large vaults.

**Recommendation:** Option A for Phase 9. Correctness first; targeted optimization later.

---

**Q3 — Thread safety of stage calls**

All three triggers (watcher, debouncer, scheduler) could theoretically fire concurrently. Should there be a global lock preventing concurrent stage execution?

- Option A: Add a `threading.Lock` that each trigger acquires before calling any stage. Simple; prevents DB contention.
- Option B: Trust SQLite WAL mode to handle concurrent connections. No application-level lock.

**Recommendation:** Option A. A single `_pipeline_lock = threading.Lock()` is a small safety net and prevents confusing interleaved log output. Even though SQLite WAL tolerates concurrency, running two reports simultaneously doubles work with no benefit.

---

**Q4 — Logging format**

Should the server use `print()` / `typer.echo()` or Python's `logging` module?

- Option A: `print()` / `typer.echo()`. Consistent with the rest of the CLI; no new dependencies.
- Option B: `logging.getLogger("matlock.server")` with configurable level. More appropriate for a long-running daemon; allows log-level filtering.

**Recommendation:** Option A for Phase 9. The rest of the codebase uses `typer.echo()` and the spec says "logs to stdout by default." The `logging` module can be introduced in a future hardening phase.
