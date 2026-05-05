# 20260430-run-all-scan-projects Plan: Add scan-projects as opt-in stage in run-all

> Date: 4/30/2026
> Owner: Copilot
> Branch: feat/scan-projects
> Related docs: docs/matlock-scan-projects.md, matlock/cli.py, matlock/stages/scan_projects.py

## Problem Summary

`run-all` runs the full pipeline (sync → parse → map-projects → rollup → report) but has no knowledge of `scan-projects`. Users who want the config kept up-to-date automatically must run `scan-projects --merge` as a separate step before `run-all`. There is no way to do it in one command.

Because `scan-projects --merge` rewrites the config YAML (adding/updating/deleting projects and super-projects), it must run **before** the main pipeline so that `map-projects` picks up the latest config data.

## Goal

Add a `--scan-projects` opt-in flag to `run-all` that, when set, runs the scan-and-merge step as the first stage, then continues with the normal pipeline.

## Scope

- In scope:
  - New `--scan-projects` boolean flag on the `run-all` command
  - Call `scan_vault` + `merge_into_config` when flag is set, before `run_sync`
  - Emit scan warnings to stderr (same as standalone command)
  - Print a one-line summary for the scan-projects stage
  - Update `run-all` docstring and help text to reflect the new optional stage
  - Tests in `tests/test_cli_run_all.py`
  - `CHANGELOG.md` entry
- Out of scope:
  - Changing the standalone `scan-projects` command
  - Any new config keys
  - Changing pipeline stage order for other flags

## Constraints / Requirements

- Flag is opt-in: `--scan-projects` defaults to `False`; existing `run-all` behaviour is unchanged
- Scan-projects runs **first**, before `run_sync`, so the updated config is in effect for the rest of the pipeline
- The config file path comes from `ctx.obj[_CONFIG_KEY]` (already available in `run_all`)
- Scan warnings emitted to stderr with `err=True`, same format as the standalone command
- A failed scan (exception raised) should abort `run-all` with a non-zero exit (consistent with other stage failures — unhandled exceptions propagate naturally)
- Backup path is **not** printed in the summary line (too verbose for a pipeline run)

---

## Implementation Plan

| Step ID | Status | Goal | Planned Changes | Test Coverage |
|---|---|---|---|---|
| RASP-S1 | Completed | Add `--scan-projects` flag to `run-all` | `matlock/cli.py`, `tests/test_cli_run_all.py`, `README.md`, `docs/matlock-cli.md`, `CHANGELOG.md` | New `TestRunAllScanProjects` class in `test_cli_run_all.py` |

Status values: `Not Started` | `In Progress` | `Completed` | `Blocked`

---

## Step Details

### RASP-S1 — Add `--scan-projects` flag to `run-all`

**Files (expected):**
- `matlock/cli.py`
- `tests/test_cli_run_all.py`
- `CHANGELOG.md`

**Implementation notes:**

*CLI changes (`matlock/cli.py`):*
- Add import: `from matlock.stages.scan_projects import scan_vault, merge_into_config`
- Add `--scan-projects` / `--no-scan-projects` boolean option to `run_all`, default `False`
- Insert scan block at the top of the pipeline body, before `run_sync`:
  ```python
  if scan_projects_flag:
      sp_scanned, sp_candidates = scan_vault(cfg)
      warned = False
      for sf in sp_scanned:
          for w in sf.warnings:
              if not warned:
                  typer.echo("Scan-projects warnings:", err=True)
                  warned = True
              typer.echo(f"  [{sf.file_path}] {w}", err=True)
      sp_result = merge_into_config(ctx.obj[_CONFIG_KEY], sp_candidates, sp_scanned)
      typer.echo(
          f"Scan-projects: "
          f"{sp_result.super_projects_added} super-projects added, "
          f"{sp_result.super_projects_updated} updated, "
          f"{sp_result.super_projects_deleted} deleted; "
          f"{sp_result.projects_added} projects added, "
          f"{sp_result.projects_updated} updated, "
          f"{sp_result.projects_deleted} deleted"
      )
  ```
- Update `run-all` docstring to mention the optional stage:
  `"""Run all pipeline stages in sequence: [scan-projects →] sync → parse → map-projects → rollup → report."""`
- Update module-level usage comment at the top of `cli.py`

*Tests (`tests/test_cli_run_all.py`):*
- Add helper `_write_project_file(vault, project_id)` to create a minimal Projects-dir project page
- New class `TestRunAllScanProjects`:
  - `test_scan_projects_flag_shown_in_help` — `--scan-projects` in help output
  - `test_without_flag_no_scan_line` — default run, `"Scan-projects:"` not in output
  - `test_with_flag_prints_scan_line` — `--scan-projects` flag, `"Scan-projects:"` in output
  - `test_with_flag_exits_zero` — exit code 0 with `--scan-projects`
  - `test_with_flag_pipeline_still_runs` — sync/parse/map-projects/report lines still present

**Definition of done:**
- `--scan-projects` flag visible in `run-all --help`
- Without flag: output unchanged (no `Scan-projects:` line)
- With flag: `Scan-projects:` summary line appears before `Sync:` line
- All `TestRunAllScanProjects` tests pass
- Full test suite passes

---

## Acceptance Criteria

- `matlock run-all --scan-projects` runs scan-and-merge before sync
- `matlock run-all` (no flag) is fully unchanged
- `Scan-projects:` summary line appears before `Sync:` in output
- Scan warnings emitted to stderr, not stdout
- `CHANGELOG.md` updated

## Risks / Notes

- **Config reload**: `merge_into_config` writes the config YAML, but `cfg` was already loaded. The rest of the pipeline uses the in-memory `cfg` object, which won't reflect the scan-projects changes. This is acceptable — `scan-projects` updates `projects`/`super_projects` lists in the YAML, but `map_projects` reads those from the in-memory `cfg` which was loaded from the same file before the merge. **This means the updated config is NOT picked up in the same run** — the user would need to run `run-all` a second time (or run `scan-projects --merge` and then `run-all`).
  - **Mitigation**: After `merge_into_config`, reload `cfg` by calling `_load_and_validate` again with the same config path. This ensures the rest of the pipeline uses the freshly merged config.
  - This should be called out in the plan as a deliberate design choice.

## Validation Plan

Run tests in this order:
1. Focused tests: `poetry run pytest tests/test_cli_run_all.py -v`
2. Adjacent/regression tests: `poetry run pytest tests/test_cli_scan_projects.py tests/test_scan_projects.py -v`
3. Full suite: `poetry run pytest -q`

Record results:
- Focused: [pass/fail + summary]
- Regression: [pass/fail + summary]
- Full suite: [pass/fail + summary]

---

## Design Decisions (Resolved)

- **Q1 — Config reload after merge:** Resolved to **reload cfg** after `merge_into_config` by calling `_load_and_validate` again on the same config path.
- **Q2 — Flag name:** Resolved to **`--scan-projects`**.
- **Q3 — Scan failure handling:** Resolved to **abort run-all** on scan exceptions (default exception propagation).

---

## Step Notes Log (update as work progresses)

### RASP-S1 Notes
- Changes made: Added `--scan-projects` opt-in flag to `run-all`; runs scan+merge pre-stage, emits scan warnings, prints scan summary, reloads config before `sync`; added tests in `tests/test_cli_run_all.py`; updated `README.md`, `docs/matlock-cli.md`, and `CHANGELOG.md`.
- Deviations: Added README and CLI-reference doc updates in the same step for user-facing discoverability.
- Validation:
  - Focused: `poetry run pytest tests/test_cli_run_all.py -q` → 31 passed
  - Regression: `poetry run pytest tests/test_cli_scan_projects.py -q` → 19 passed
  - Full suite: `poetry run pytest -q` → 598 passed
  - Manual smoke: `poetry run matlock --config /path/to/config.yaml run-all --scan-projects` completed successfully
