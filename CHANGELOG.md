# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [Unreleased]

### Added

- `SECURITY.md` with guidance for keeping the public code repository separate from private vault data and generated outputs.

---

## [0.3.1] — 2026-05-05

### Added

- `matlock server --force-rollup` startup option to run `rollup` for today's date once before watcher/debouncer threads start.

### Changed

- History rendering now always runs `rollup` for today before generating history pages, even when today's `daily_metric` row already exists, so `file_touch`/`daily_task` reflect the latest tracked file state.
- Parse stage now keeps an in-memory poison-file cache keyed by `(file_path, sha256)` to avoid repeated extraction retries/log spam for unchanged failing files while still retrying automatically when file content changes.
- `scan-projects` now handles per-file read/parse failures gracefully by emitting per-file warnings and continuing to scan remaining files.

### Fixed

- Repeated `report --target history` runs now refresh today's `file_touch` data instead of leaving stale `modified` timestamps from earlier runs.

### Changed

- Public package metadata now declares Apache-2.0 license information, README metadata, project URLs, and keywords/classifiers for publishing.
- `.gitignore` now excludes local deployment artifacts such as `config.yaml`, config backups, SQLite files, logs, and generated `_Matlock/` output.
- Redacted a real local absolute config path from a tracked plan note to reduce accidental privacy leakage in the public repository.

---

## [0.3.0] — 2026-05-05

### Added

- **`file_touch` table** — Immutable per-day file-event snapshot (created/modified/deleted) populated by the `rollup` stage. Enables accurate history page regeneration at any future date.
- **`daily_task` table** — Immutable per-day task-event snapshot (created/completed) populated by the `rollup` stage. Denormalizes `task_text`, `file_path`, and `attributes` at rollup time.
- **`deleted_date` column on `file` table** — YYYY-MM-DD when `sync` first detected a file deletion. Auto-migrated on next `init_db` call.
- **Enriched daily history page** — `_Matlock/History/<date>.md` now includes:
  - Header: generated-at timestamp, home link, previous/next day navigation
  - Summary by super-project table
  - Summary by project table (now tabular, includes created + completed counts)
  - Files Touched table (descending by time; deleted events show "—" for time)
  - Tasks This Day table (with event type, estimate, and project link)
  - Graceful fallback text for dates without snapshot data (pre-migration history pages)
- **Virtual Unassigned project and super-project pages** — `Projects/Unassigned.md` and `Super Projects/Unassigned.md` are always generated. They surface all tasks, files, and daily metrics not mapped to any project. The Unassigned page appears in the Projects index, Super Projects index, and all history pages.

### Changed

- `rollup` stage now populates `file_touch` and `daily_task` after writing `daily_metric` rows (idempotent via `INSERT OR REPLACE`).
- `db.mark_file_deleted` now sets `deleted_date = today` in addition to `deleted = 1`.
- `report` stage: today's history page is always generated; if today's `daily_metric` row is absent, `rollup` is invoked inline.
- `project.md.j2`: added `generated_at` timestamp to the header; fixed list-item rendering (trailing-whitespace-strip bug caused items to collapse onto one line).
- `scan-projects`: `status` validation is now case-insensitive (`in progress` → `In Progress`).
- `scan-projects`: project metadata fields (`super_project_id`, `priority`, `status`, `title`, `start_date`, `due_date`) are now resolved across **all** candidate files for a project, not just the first/home file.

### Documentation

- `matlock-generated-reports.md`: rewrote all template blocks and variable tables to reflect current templates (dashboard, project page, super-project page). Added Unassigned virtual pages. Updated output directory structure.
- `matlock-pipeline-specification.md`: updated Stage V output files table with new pages (`Warnings.md`, `Super Projects.md`, `Projects.md`, virtual Unassigned pages).
- `matlock-high-level-design.md`: updated `_Matlock/` directory structure diagram.
- `matlock-scan-projects.md`: documented case-insensitive status validation and multi-candidate field resolution.

---

## [0.2.0] — 2026-04-30

### Added

- **`matlock scan-projects` command** — Walk the vault and reverse-engineer the project/super-project hierarchy from frontmatter metadata. Supports three output modes:
  - `--print-yaml` (default): Print discovered projects/super-projects as YAML.
  - `--diff`: Human-readable comparison against the current `config.yaml`.
  - `--merge`: Apply scanned results to `config.yaml` with a timestamped backup.
- **`matlock/scan_models.py`** — New Pydantic v2 data models: `ScannedFile` (16 fields) and `ProjectCandidate` (3 fields).
- **`matlock/stages/scan_projects.py`** — Full scanner implementation: `scan_vault`, `_scan_file`, `_build_project_candidates`, `format_as_yaml`, `format_as_diff`, `merge_into_config`, `MergeResult`.
- **97 new tests** across `tests/test_scan_models.py`, `tests/test_scan_projects.py`, and `tests/test_cli_scan_projects.py`.
- **`docs/matlock-scan-projects.md`** — Full specification for the `scan-projects` command.

### Changed

- `matlock run-all`: added opt-in `--scan-projects` to run `scan-projects --merge` before sync.
- `matlock server`: added `--scan-projects`, `--force-sync`, `--force-report` (startup-only) and `--skip-rollup` (nightly scheduler) options for parity with `run-all`.
- Report output pages now use friendly filenames (`Home.md`, `Due Today.md`, `Past Due.md`, `Due Soon.md`, `Future Due.md`, `Not Due.md`) and each due-list page includes a link back to the dashboard.
- `Home.md` now includes a Task Matrix by Priority table; each populated cell links directly to the corresponding due-view page and priority anchor section.
- `docs/matlock-cli.md`: Added `scan-projects` section under new "Discovery Commands" heading.
- `docs/matlock-high-level-design.md`: Added Section 6 "Discovery Commands"; renumbered subsequent sections.
- `docs/copilot/copilot-docs-reference.md`: Added `scan-projects` routing rule, topic row, and keyword entries. Updated baseline to `0.2.x`.
- `docs/roadmap/index.md`: Added Phase 10 row and description. Updated version to `0.2.x`.

---

## [0.1.0] — 2026-04-27

### Added

- Initial release: five-stage pipeline (`sync`, `parse`, `map-projects`, `rollup`, `report`), `run-all` command, `server` daemon, configuration layer, SQLite database layer, Jinja2 report templates.
