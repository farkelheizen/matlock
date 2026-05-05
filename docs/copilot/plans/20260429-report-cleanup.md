# Plan: Report Cleanup — Delete Stale Project / Super-Project Pages

**Status:** Completed  
**Created:** 2026-04-29  
**Branch:** feat/phase-9-server-daemon (appended to existing feature branch)

---

## Problem Summary

When a project or super-project is removed from `matlock.yaml`, its generated report
file (`Projects/<id>.md`, `SuperProjects/<id>.md`) is never deleted.  It remains on
disk indefinitely, showing stale (and wrong) task data in Obsidian.

---

## Goal

After every full `run_report` (and `run-all`) that regenerates all project / super-project
pages, delete any `.md` files in `Projects/` and `SuperProjects/` whose stem does not
match a currently-known ID.  Update the `file` table accordingly.

---

## Scope

- `matlock/stages/report.py` — new helper + wired into `run_report`
- `ReportResult` — new `files_deleted: int = 0` field
- `matlock/cli.py` — echo updated for `report` and `run-all` subcommands
- `tests/test_report.py` — new `TestReportCleanup` class

**Out of scope:** History pages (dates can never be "removed" from config); dashboard.

---

## Design Decisions (Resolved)

| Question | Decision |
|---|---|
| When should cleanup run? | Only on a full projects render (`target in ("all","projects")`), never on `--project-id` targeted renders or `--target dashboard/history` |
| How to handle the `file` table? | `DELETE FROM file WHERE file_path = ?` (absolute path) — generated files need no tombstone |
| Should `files_deleted` appear in the `ReportResult`? | Yes — CLI surfaces it to the user |

---

## Implementation Plan

| Step | Status | Description | Files |
|---|---|---|---|
| RC-S1 | Completed | Add `_cleanup_stale_report_files` helper and `files_deleted` to `ReportResult`; wire into `run_report`; update CLI echo | `report.py`, `cli.py` |
| RC-S2 | Completed | Tests: `TestReportCleanup` class | `tests/test_report.py` |

---

## Step Details

### RC-S1 — Core implementation

1. Add `files_deleted: int = 0` to `ReportResult` dataclass.
2. Add `_cleanup_stale_report_files(conn, report_subdir, current_ids) -> int` helper.
   - If subdir doesn't exist → return 0.
   - Glob `*.md`; for each file whose `.stem` is not in `current_ids`: `DELETE FROM file`, `unlink()`.
3. In `run_report`, after rendering all projects and super-projects (only when
   `target in ("all","projects")` and `project_id is None`):
   - Query current project IDs and super-project IDs from DB.
   - Call cleanup for `Projects/` and `SuperProjects/`.
   - Accumulate into `files_deleted`.
4. Return `ReportResult(files_written=..., files_deleted=..., target=...)`.
5. Update CLI echo strings in `report` and `run-all`.

### RC-S2 — Tests

`TestReportCleanup`:
- `test_stale_project_file_deleted` — pre-create stale file, remove project from DB, run report, assert file gone + file table row gone + `files_deleted == 1`
- `test_stale_super_project_file_deleted` — same for super-projects
- `test_current_project_file_not_deleted` — current project file must survive
- `test_files_deleted_count_correct` — multiple stale files, assert correct count
- `test_cleanup_not_triggered_for_targeted_project_id` — `--project-id foo` must NOT delete other files
- `test_cleanup_not_triggered_for_target_dashboard` — `target="dashboard"` must NOT clean project files
- `test_cleanup_removes_file_table_row` — verify file table row is gone after cleanup

---

## Acceptance Criteria

- After removing a project from config and running `run-all`, its `Projects/<id>.md` file is gone from disk.
- Current project pages are never touched.
- `files_deleted` count appears in CLI output.
- All 436+ tests pass.

---

## Validation Plan

```
poetry run pytest tests/test_report.py -v
poetry run pytest -q
```

---

## Step Notes Log

### RC-S1
- **Changes:** `report.py` — `ReportResult.files_deleted`, `_cleanup_stale_report_files`, `run_report` wiring; `cli.py` — echo updates
- **Deviations:** none

### RC-S2
- **Changes:** `tests/test_report.py` — `TestReportCleanup` (7 tests)
- **Validation:** all tests pass
