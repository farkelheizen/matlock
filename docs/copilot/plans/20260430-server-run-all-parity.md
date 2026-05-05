# 20260430-server-run-all-parity Plan: Server Command Run-All Options Parity

> Date: 4/30/2026
> Owner: Copilot
> Branch: feat/scan-projects
> Related docs: docs/matlock-cli.md, docs/matlock-high-level-design.md

## Problem Summary

The `run-all` command accepts four opt-in flags that control pipeline behaviour:
`--scan-projects`, `--skip-rollup`, `--force-sync`, `--force-report`. The `server`
command currently only has `--debounce`. Users who run the server cannot use any
of these flags, so startup behaviour (initial sync force, initial scan-projects
merge, nightly rollup suppression) is hard-coded.

## Goal

Add the four `run-all` flags to the `server` command so that startup and nightly
behaviour can be customised at launch without editing the config file.

## Scope

- In scope: `--scan-projects`, `--skip-rollup`, `--force-sync`, `--force-report`
  on `server`; wiring them into `run_server()`; tests; CLI docs; CHANGELOG.
- Out of scope: changing existing trigger logic beyond applying the new flags;
  adding per-trigger flag overrides (e.g. force-sync only on watcher events);
  config-file support for these flags.

## Constraints / Requirements

- `--force-sync` and `--force-report` must only apply to a single point in the
  server lifecycle (e.g. startup), not to every subsequent watcher event.
- `--skip-rollup` must not break the scheduler thread when rollup is skipped.
- All existing `server` behaviour when none of the new flags are passed must be
  unchanged (backward-compatible).
- `run_server()` signature must accept the new parameters; `cli.py` passes them
  through.

---

## Implementation Plan

| Step ID  | Status      | Goal                                             | Planned Changes                                          | Test Coverage                                 |
|----------|-------------|--------------------------------------------------|----------------------------------------------------------|-----------------------------------------------|
| SRPA-S1  | Completed   | Add flags to CLI + wire into run_server          | `matlock/cli.py`, `matlock/server.py`                    | `tests/test_server.py`, `tests/test_cli_server.py` |
| SRPA-S2  | Completed   | Docs + CHANGELOG                                 | `docs/matlock-cli.md`, `README.md`, `CHANGELOG.md`       | n/a (doc-only)                                |

Status values: `Not Started` | `In Progress` | `Completed` | `Blocked`

---

## Step Details

### SRPA-S1 — Add flags to CLI + wire into run_server

**Files (expected):**
- `matlock/cli.py` — add 4 `typer.Option` params to `server()`; pass to `run_server()`
- `matlock/server.py` — update `run_server()` signature; implement startup block and
  nightly-scheduler flag handling

**Implementation notes:**

- **`--scan-projects` (startup):** run `scan_vault` + `merge_into_config` once
  inside `run_server()` before the observer starts. Reload cfg after merge.
  Emit any scan warnings to stdout via `typer_echo`.

- **`--force-sync` (startup):** run `run_sync(cfg, conn, force=True)` +
  `run_parse(cfg, conn)` once inside `run_server()` before the observer starts.
  This ensures the DB is fully up to date before the watcher takes over.

- **`--force-report` (startup):** run `run_report(cfg, conn, target="all",
  force=True)` once inside `run_server()` before the observer starts (after any
  startup sync).

- **`--skip-rollup` (nightly scheduler):** pass as a flag to `_scheduler_thread`;
  when `True`, skip the `run_rollup()` call in the nightly job.

- Startup block ordering: scan-projects → sync (force if requested) → parse →
  force-report (if requested). Only run sync+parse startup if `--force-sync` is
  set (otherwise watcher handles incremental sync on first change).

- `_scheduler_thread` must accept a new `skip_rollup: bool` parameter; update its
  call site in `run_server()`.

**Definition of done:**
- `poetry run matlock server --help` shows all 5 flags.
- Tests cover: scan-projects runs at startup when flag set; force-sync runs at
  startup; skip-rollup suppresses nightly rollup; force-report runs at startup.
- Full suite green.

### SRPA-S2 — Docs + CHANGELOG

**Files (expected):**
- `docs/matlock-cli.md` — update `server` synopsis, add options table rows
- `README.md` — update server CLI reference if present
- `CHANGELOG.md` — add Changed entry under [0.2.0]

**Implementation notes:**
- Mirror the style already used for the `run-all` option additions in the same
  docs from RASP-S1.

**Definition of done:**
- All four new flags documented with descriptions matching the CLI help text.

---

## Acceptance Criteria

- `matlock server --help` lists `--scan-projects`, `--skip-rollup`, `--force-sync`,
  `--force-report`, `--debounce`.
- When `--scan-projects` is passed, scan-projects merge runs once at startup before
  the watcher starts.
- When `--force-sync` is passed, a full forced sync+parse runs once at startup.
- When `--force-report` is passed, a full forced report runs once at startup
  (after any startup sync).
- When `--skip-rollup` is passed, the nightly scheduler skips `run_rollup`.
- No flags: server behaves exactly as before (no regression).
- `CHANGELOG.md` updated.
- Docs updated.

## Risks / Notes

- The startup block adds latency before the watcher begins; this is acceptable and
  expected.
- `--force-sync` + `--scan-projects` together at startup could take a long time on
  a large vault; document this.
- `_scheduler_thread` currently takes positional args; adding `skip_rollup` must
  be done carefully to avoid breaking the `Thread(args=...)` call.

## Validation Plan

Run tests in this order:
1. Focused tests: `poetry run pytest tests/test_server.py tests/test_cli_server.py -q`
2. Adjacent/regression tests: `poetry run pytest tests/test_cli_run_all.py tests/test_scan_projects.py -q`
3. Full suite: `poetry run pytest -q`

Record results:
- Focused: 41 passed (test_server.py + test_cli_server.py)
- Regression: n/a
- Full suite: 615 passed

---

## Design Decisions (Resolved)

**Q1 — Does `--force-sync` also run parse at startup?**
Yes — startup forced sync also runs parse for DB consistency. (Agreed 2026-04-30)

**Q2 — Should `--scan-projects` reload the config before the startup sync?**
Yes — reload cfg after merge, same as run-all. (Agreed 2026-04-30)

**Q3 — Are `--force-report` and `--skip-rollup` orthogonal?**
Yes — `--force-report` always runs report; `--skip-rollup` only affects the nightly scheduler. (Agreed 2026-04-30)

**Q4 — Does `--skip-rollup` only affect the scheduler?**
Yes — affects only the nightly scheduler. If a startup rollup is ever added, it would respect the same flag. (Agreed 2026-04-30)

**Q5 — Does `--force-sync` opt-in to startup sync?**
Yes — startup sync only runs when `--force-sync` is explicitly passed. No silent change to baseline behaviour. (Agreed 2026-04-30)

---

## Step Notes Log (update as work progresses)

### SRPA-S1 Notes
- Changes made: Added `skip_rollup: bool = False` param to `_scheduler_thread`; wrapped `run_rollup` call. Added `skip_rollup`, `force_sync`, `force_report` to `run_server()`; added startup block (force_sync → sync+parse, force_report → report). Updated `_scheduler_thread` args to pass `skip_rollup`. Added 4 new typer options to `server()` in cli.py; `--scan-projects` handled in cli.py (same pattern as run-all) before calling `run_server()`. Updated existing `TestServerCommand` fake signatures to accept `**kwargs`.
- Deviations: `--scan-projects` wired in cli.py rather than run_server() (cleaner — no config path needed in server.py).
- Validation: Focused 41 passed; full suite 615 passed.

### SRPA-S2 Notes
- Changes made: `docs/matlock-cli.md` server table + notes updated; `README.md` synopsis + table + examples updated; `CHANGELOG.md` added server entry under Changed.
- Deviations: none
- Validation: n/a (doc-only)
