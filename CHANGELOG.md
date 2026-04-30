# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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

- `docs/matlock-cli.md`: Added `scan-projects` section under new "Discovery Commands" heading.
- `docs/matlock-high-level-design.md`: Added Section 6 "Discovery Commands"; renumbered subsequent sections.
- `docs/copilot/copilot-docs-reference.md`: Added `scan-projects` routing rule, topic row, and keyword entries. Updated baseline to `0.2.x`.
- `docs/roadmap/index.md`: Added Phase 10 row and description. Updated version to `0.2.x`.

---

## [0.1.0] — 2026-04-27

### Added

- Initial release: five-stage pipeline (`sync`, `parse`, `map-projects`, `rollup`, `report`), `run-all` command, `server` daemon, configuration layer, SQLite database layer, Jinja2 report templates.
