# Matlock Implementation Roadmap

**Version:** 0.7.x

This index lists all implementation phases. Each active phase links to its detailed plan document (using the `copilot-plan-template.md` format). Phases are executed sequentially; a phase must be marked `Completed` before the next begins.

---

## Phase Summary

| Phase | Name | Status | Plan Doc |
|:------|:-----|:-------|:---------|
| 0 | Foundation: Rename & Scaffold | Completed | `docs/roadmap/phase-0-rename-and-scaffold.md` |
| 1 | Configuration Layer | Completed | `docs/copilot/plans/20260424-phase-1-configuration-layer.md` |
| 2 | Database Layer | Completed | `docs/copilot/plans/20260424-phase-2-database-layer.md` |
| 3 | Stage I — Sync | Completed | `docs/copilot/plans/20260427-phase-3-stage-sync.md` |
| 4 | Stage II — Extract (DB Integration) | Completed | `docs/copilot/plans/20260427-phase-4-stage-parse.md` |
| 5 | Stage III — Projects Map | Completed | `docs/copilot/plans/20260427-phase-5-stage-map-projects.md` |
| 6 | Stage IV — Metrics Rollup | Completed | `docs/copilot/plans/20260427-phase-6-stage-rollup.md` |
| 7 | Stage V — Reports Render | Completed | `docs/copilot/plans/20260427-phase-7-stage-report.md` |
| 8 | `pipeline run` Command | Completed | `docs/copilot/plans/20260427-phase-8-run-all.md` |
| 9 | Server Daemon | Completed | `docs/copilot/plans/20260427-phase-9-server-daemon.md` |
| 10 | `projects discover` Command | Completed | `docs/copilot/plans/20260430-scan-projects.md` |

---

## Phase Descriptions

### Phase 0 — Foundation: Rename & Scaffold

Rename the existing `markdown_stuff` package to `matlock`. Rename `ParsedDocument` → `ParsedMarkdownFile` and `ParsedTask` → `ParsedMarkdownTask`. Update `pyproject.toml`. Confirm all existing tests pass. Create the `matlock/stages/` subpackage stub.

**Key deliverables:** Working `matlock` package with all existing tests green.

---

### Phase 1 — Configuration Layer

Implement `matlock/config.py`: Pydantic models for `MatlockConfig`, `SuperProjectConfig`, `ProjectConfig`, `TaskAttributeConfig`. Load and validate `config.yaml`. Write an example config. Write tests.

**Key deliverables:** `matlock.config.load_config(path)` returns a validated `MatlockConfig`. All validation rules from `docs/matlock-configuration.md` enforced.

---

### Phase 2 — Database Layer

Implement `matlock/db.py`: SQLite connection with WAL/FK PRAGMAs. Schema creation (all 6 tables from `docs/matlock-data-model.md`). Generic upsert/select helpers. Write tests for schema creation and CRUD operations.

**Key deliverables:** `matlock.db.get_connection(config)` returns a configured `sqlite3.Connection`. All tables created idempotently. Tests cover schema and data round-trips.

---

### Phase 3 — Stage I: Sync

Implement `matlock/stages/sync.py` and the `matlock sync` CLI command. Recursive vault walk, SHA-256 hashing, upsert into `file` table, `needs_parsing` flagging, soft-delete for missing files, `--force` flag. Write tests (including mocked filesystem).

**Key deliverables:** `matlock sync` correctly identifies new, modified, unchanged, and deleted files and updates the DB accordingly.

---

### Phase 4 — Stage II: Extract (DB Integration)

Implement `matlock/stages/parse.py` and the `matlock extract` CLI command. Wire the existing `extract_tasks_from_markdown` engine to read from DB (`needs_parsing = 1` query), delete stale tasks, insert new tasks, and clear the flag. Tests cover DB state before/after extraction.

**Key deliverables:** After `matlock sync && matlock extract`, the `task` table contains accurate data for all modified files.

---

### Phase 5 — Stage III: Map Projects

Implement `matlock/stages/map_projects.py` and the `matlock projects map` CLI command. Read `config.yaml`, rebuild `super_project`, `project`, and `file_project` tables per rules in `docs/matlock-pipeline-specification.md`. Write tests for FILE and DIRECTORY resource matching, multi-project membership, and full-rebuild idempotency.

**Key deliverables:** `matlock projects map` correctly links files to projects as defined in config.

---

### Phase 6 — Stage IV: Rollup

Implement `matlock/stages/rollup.py` and the `matlock metrics rollup` CLI command. COUNT/SUM queries against `task` and `file` tables, upsert into `daily_metric`, `--date` override. Write tests including streak calculation logic.

**Key deliverables:** `matlock metrics rollup` produces correct `daily_metric` rows. Re-running for the same date is idempotent.

---

### Phase 7 — Stage V: Report

Implement `matlock/stages/report.py`, all four Jinja2 templates (see `docs/matlock-generated-reports.md`), and the `matlock reports render` CLI command. Output files written to `output_directory`; registered in `file` table with `is_generated = 1`. Tests verify template rendering and output file existence.

**Key deliverables:** All four dashboard types render correctly from database state. `_Matlock/` directory is populated.

---

### Phase 8 — `pipeline run` Command

Implement the `matlock pipeline run` command that chains all five stages in order. Honour `--skip-rollup` and `--force-sync` flags. Write an integration test that runs the full pipeline against a test vault fixture.

**Key deliverables:** `matlock pipeline run` against a fixture vault produces correct output files end-to-end.

---

### Phase 9 — Server Daemon

Implement `matlock/server.py` and the `matlock serve` CLI command. `watchdog` watcher, dirty-project queue, debouncer, midnight scheduler. Tests mock `watchdog` events and verify correct stage triggers and debounce behaviour.

**Key deliverables:** `matlock serve` responds to file-system events, debounces report generation, and triggers nightly metrics rollup at midnight.

---

### Phase 10 — `projects discover` Command

Implement `matlock/scan_models.py` (`ScannedFile`, `ProjectCandidate`), `matlock/stages/scan_projects.py` (vault scanner, `--print-yaml`/`--diff`/`--merge` formatters), and the `projects discover` CLI command. The scanner walks the vault, infers the project/super-project hierarchy from frontmatter metadata, and can merge findings back into `config.yaml`.

**Key deliverables:** `matlock projects discover` (all three modes), 97 new tests, `docs/matlock-scan-projects.md`, version bumped to `0.2.0`.
