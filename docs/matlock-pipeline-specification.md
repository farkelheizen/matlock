# Matlock Pipeline Specification

**Version:** 0.1.x

This document specifies the behaviour of each of the five pipeline stages. Each stage is a discrete, independently invocable unit. Stages communicate **exclusively** through the SQLite database; no stage imports or calls another directly.

See `docs/matlock-data-model.md` for the full schema referenced here.

---

## Stage I — `sync` (The Syncer)

**Module:** `matlock/stages/sync.py`
**Command:** `matlock sync`

### Responsibility

Keep the `file` table in sync with the physical filesystem under `base_directory`. This is the only stage that touches the filesystem directly (excluding the `report` stage's writes to `_Matlock/`).

### Logic

1. Recursively walk `base_directory`. Skip `output_directory` (`_Matlock/`) and any path covered by `ignore_dirs` in `config.yaml`.
2. For each `.md` file found:
   - Compute the SHA-256 hash of its content.
   - If the file is **new** (not in `file` table): insert a row with `needs_parsing = 1`.
   - If the file **exists** and its hash has changed: update the row, set `needs_parsing = 1`.
   - If the file **exists** and its hash is unchanged: no write needed (skip).
3. For files in the `file` table that were **not found** on disk: set `deleted = 1` and `deleted_date = today` (YYYY-MM-DD). If `deleted_date` is already set, it is not overwritten (preserves the first detection date).

### Flags

| Flag | Description |
|:-----|:------------|
| `--force` | Re-hash all files and set `needs_parsing = 1` regardless of hash comparison |
| `--config PATH` | Path to `config.yaml` (default: `./config.yaml`) |

### What it does NOT do

- It does not know what a task is.
- It does not parse any file content beyond hashing.
- It does not delete rows from the `file` table (uses soft-delete via `deleted = 1`).

---

## Stage II — `parse` (The Parser)

**Module:** `matlock/stages/parse.py`
**Command:** `matlock parse`

### Responsibility

Extract task data from files flagged by the `sync` stage and persist results to the `task` table.

### Logic

1. Query: `SELECT file_path FROM file WHERE needs_parsing = 1 AND deleted = 0`.
2. For each result:
   a. Delete all existing rows in `task` where `file_path` matches (full replace).
   b. Read the file from disk.
   c. Run `extract_tasks_from_markdown(content, config)` to produce a `ParsedMarkdownFile`.
   d. Insert each `ParsedMarkdownTask` from the result into the `task` table.
   e. Set `needs_parsing = 0` on the `file` row.
   f. Update `word_count`, `length`, `meta_data`, `sha256` on the `file` row from the parsed result.
3. If extraction raises an unhandled exception for a file: log the error, leave `needs_parsing = 1`, continue to the next file.

### Flags

| Flag | Description |
|:-----|:------------|
| `--config PATH` | Path to `config.yaml` |
| `--file PATH` | Parse a single file by path (bypasses `needs_parsing` check) |

### What it does NOT do

- It does not walk the filesystem.
- It does not modify project mappings.
- It does not generate any reports.

---

## Stage III — `map-projects` (The Project Mapper)

**Module:** `matlock/stages/map_projects.py`
**Command:** `matlock map-projects`

### Responsibility

Rebuild the project-to-file associations from `config.yaml`. This stage is always a full rebuild; it does not do incremental updates.

### Logic

1. Read `super_projects` and `projects` from `config.yaml`.
2. **Truncate** the `super_project`, `project`, and `file_project` tables.
3. Insert all `super_projects` from config.
4. Insert all `projects` from config.
5. For each project, evaluate its `resources` entries:
   - `type: FILE` — match if `file.file_path` equals the resource `path`.
   - `type: DIRECTORY` — match if `file.file_path` starts with the resource `path`.
6. Insert matching `(file_path, project_id)` pairs into `file_project`.
7. A file can belong to multiple projects.

### Flags

| Flag | Description |
|:-----|:------------|
| `--config PATH` | Path to `config.yaml` |

### What it does NOT do

- It does not parse files.
- It does not touch `needs_parsing`.

---

## Stage IV — `rollup` (The Chronicler)

**Module:** `matlock/stages/rollup.py`
**Command:** `matlock rollup`

### Responsibility

Calculate historical metrics for the previous day and persist them to `daily_metric`. Designed to run once per day (midnight).

### Logic

1. Determine `rollup_date` (defaults to yesterday; overridable via `--date`).
2. For each project (plus one NULL-project row for unassigned tasks), execute COUNT/SUM queries against the `task` table:
   - `tasks_created_count/minutes`: tasks whose `created_date = rollup_date`.
   - `tasks_completed_count/minutes`: tasks whose `act_comp_date = rollup_date` and `checked = 1`.
   - `tasks_due_tomorrow_count/minutes`: tasks whose `due_date = rollup_date + 1 day` and `checked = 0`.
   - `tasks_past_due_count/minutes`: tasks whose `due_date < rollup_date` and `checked = 0`.
   - `tasks_future_due_count/minutes`: tasks whose `due_date > rollup_date` and `checked = 0`.
3. Count file activity from the `file` table using `modified_date`.
4. Upsert all rows into `daily_metric` (idempotent; re-running the same date is safe).
5. **Populate `file_touch`** for `rollup_date`: query the `file` table for three event types and upsert one row per event into `file_touch`:
   - `created`: files with `date(created/1000, 'unixepoch') = rollup_date AND is_generated = 0`
   - `modified`: files with `modified_date = rollup_date AND deleted = 0 AND is_generated = 0`
   - `deleted`: files with `deleted_date = rollup_date AND deleted = 1 AND is_generated = 0`
6. **Populate `daily_task`** for `rollup_date`: query the `task` table for two event types and upsert one row per event into `daily_task`:
   - `created`: tasks whose `created_date = rollup_date` (file not deleted)
   - `completed`: tasks whose `act_comp_date = rollup_date AND checked = 1` (file not deleted)
   - Denormalized fields (`task_text`, `file_path`, `attributes`) are snapshotted at rollup time so that history pages remain accurate even if the source task is later edited or deleted.

Both steps are idempotent via `INSERT OR REPLACE`.

### Streak Calculation

A "streak" is the count of consecutive days (ending with `rollup_date`) where `tasks_completed_count > 0`. Calculated from `daily_metric` at report time, not stored separately.

### Flags

| Flag | Description |
|:-----|:------------|
| `--date YYYY-MM-DD` | Run rollup for a specific date instead of yesterday |
| `--config PATH` | Path to `config.yaml` |

---

## Stage V — `report` (The Reporter)

**Module:** `matlock/stages/report.py`
**Command:** `matlock report`

### Responsibility

Query the database and render Jinja2 Markdown dashboards into the `output_directory` (`_Matlock/`). This is a pure "data-to-text" stage.

### Output Files

| Template | Output Path | Trigger |
|:---------|:------------|:--------|
| `daily_dashboard.md.j2` | `_Matlock/Home.md` | `matlock report` or `run-all` |
| `due_today.md.j2` | `_Matlock/Due Today.md` | `matlock report` or `run-all` |
| `past_due.md.j2` | `_Matlock/Past Due.md` | `matlock report` or `run-all` |
| `due_soon.md.j2` | `_Matlock/Due Soon.md` | `matlock report` or `run-all` |
| `future_due.md.j2` | `_Matlock/Future Due.md` | `matlock report` or `run-all` |
| `not_due.md.j2` | `_Matlock/Not Due.md` | `matlock report` or `run-all` |
| `warnings.md.j2` | `_Matlock/Warnings.md` | `matlock report` or `run-all` |
| `super_projects_index.md.j2` | `_Matlock/Super Projects.md` | `matlock report` or `run-all` |
| `projects_index.md.j2` | `_Matlock/Projects.md` | `matlock report` or `run-all` |
| `super_project.md.j2` | `_Matlock/Super Projects/<id>.md` | Per super-project |
| `super_project.md.j2` | `_Matlock/Super Projects/Unassigned.md` | Virtual page — always generated |
| `project.md.j2` | `_Matlock/Projects/<id>.md` | Per project |
| `project.md.j2` | `_Matlock/Projects/Unassigned.md` | Virtual page — always generated |
| `daily_history.md.j2` | `_Matlock/History/<YYYY-MM-DD>.md` | After `rollup` |

### Logic

1. Load Jinja2 templates from `matlock/templates/`.
2. Query the database for required data per dashboard type (see `docs/matlock-generated-reports.md` for field details).
3. Render each template with the query results.
4. Write output to `_Matlock/` (creates directories as needed).
5. Rows written to `file` with `is_generated = 1` for each output file, so `sync` ignores them.

### Flags

| Flag | Description |
|:-----|:------------|
| `--target {all,dashboard,projects,history}` | Limit which dashboards to regenerate |
| `--project-id ID` | Regenerate a single project page |
| `--config PATH` | Path to `config.yaml` |

### What it does NOT do

- It does not modify any source file.
- It does not recalculate metrics (that is `rollup`'s job).
- It does not parse tasks (that is `parse`'s job).

---

## `run-all` — Full Pipeline

**Command:** `matlock run-all`

Runs all five stages in order: `sync` → `parse` → `map-projects` → `rollup` → `report`.

| Flag | Description |
|:-----|:------------|
| `--skip-rollup` | Skip Stage IV (use when running mid-day; rollup is designed for nightly use) |
| `--config PATH` | Passed through to all stages |

---

## Server Mode — `matlock server`

**Module:** `matlock/server.py`
**Command:** `matlock server`

### Behaviour

Runs a persistent daemon that orchestrates all pipeline stages in response to events. Combines three triggers:

1. **Watcher (real-time I/O):**
   - Uses `watchdog` to monitor `base_directory`.
   - On file create/modify/delete: calls `sync` for that file, then `parse` if `needs_parsing`.
   - Marks affected project IDs as "dirty" in an in-memory queue.

2. **Debouncer (UI refresh):**
   - Listens to the dirty-project queue.
   - After a configurable idle period with no new changes (`debounce_seconds`, default `5`), triggers `report` for affected projects only.

3. **Scheduler (nightly metrics):**
   - At midnight (00:01): triggers `rollup` then a full `report` rebuild (Daily Dashboard + new History page).

### Flags

| Flag | Description |
|:-----|:------------|
| `--config PATH` | Path to `config.yaml` |
| `--debounce SECONDS` | Override debounce window (default: `5`) |

---

## Database State Invariants

At any point in time, the database state should satisfy:

- `needs_parsing = 0` for all non-deleted files → `parse` is up to date.
- `file_project` reflects the current `config.yaml` → `map-projects` has run since last config change.
- `daily_metric` has a row for every past date → `rollup` has run each night.
- All `_Matlock/` files have `is_generated = 1` in the `file` table.
