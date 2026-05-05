# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [Unreleased]

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

### Changed

- `rollup` stage now populates `file_touch` and `daily_task` after writing `daily_metric` rows (idempotent via `INSERT OR REPLACE`).
- `db.mark_file_deleted` now sets `deleted_date = today` in addition to `deleted = 1`.

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
