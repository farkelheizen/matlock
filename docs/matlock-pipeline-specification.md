# Matlock Pipeline Specification

**Version:** 0.5.x

This document specifies the behavior of Matlock's five core pipeline stages plus the optional local search hooks that integrate with them. Each core stage is a discrete, independently invocable unit. Stages communicate **exclusively** through the SQLite database; no core stage imports or calls another directly.

See `docs/matlock-data-model.md` for the full schema referenced here.

---

## Stage I — `sync` (The Syncer)

**Module:** `matlock/stages/sync.py`
**Command:** `matlock sync`

### Responsibility

Keep the `file` table in sync with the physical filesystem under `base_directory`. Within the core pipeline, this is the only stage that reads source files directly from disk (excluding the `report` stage's writes to `_Matlock/`).

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
   a. Check the in-memory poison cache for this `file_path` and current `sha256`.
      - If the file failed extraction previously with the same `sha256`, skip extraction for this run (`skipped += 1`).
      - If `sha256` changed, clear poison state and retry extraction.
   a. Delete all existing rows in `task` where `file_path` matches (full replace).
   b. Read the file from disk.
   c. Run `extract_tasks_from_markdown(content, config)` to produce a `ParsedMarkdownFile`.
   d. Run document-level secret detection and persist `has_secrets` plus `secret_detection_error`.
      - Findings set `has_secrets = 1`.
      - Clean scans set `has_secrets = 0`.
      - Scanner exceptions fail closed (`has_secrets = 1`) and persist scanner error text.
      - Scanner failures do not poison a successfully parsed file.
   e. Insert each `ParsedMarkdownTask` from the result into the `task` table.
   f. Set `needs_parsing = 0` on the `file` row.
   g. Update `word_count`, `length`, `meta_data`, `sha256` on the `file` row from the parsed result.
3. If extraction raises an unhandled exception for a file: record/update poison cache for `(file_path, sha256)`, log the error, leave `needs_parsing = 1`, continue to the next file.
4. On successful extraction, clear poison state for that `file_path`.

### Flags

| Flag | Description |
|:-----|:------------|
| `--config PATH` | Path to `config.yaml` |

### What it does NOT do

- It does not walk the filesystem.
- It does not modify project mappings.
- It does not generate any reports.
- It does not persist poison-cache state across process restarts.

---

## Secret Detection Backfill (Standalone Utility)

**Module:** `matlock/stages/detect_backfill.py`
**Command:** `matlock detect-backfill`

### Responsibility

Resolve legacy or errored secret-detection state in bulk without re-parsing tasks.

### Logic

1. Select active, non-generated files:
   - Default: `has_secrets IS NULL`.
   - With `--retry-errors`: `has_secrets IS NULL OR secret_detection_error IS NOT NULL`.
2. For each candidate, attempt to open the tracked file path under `base_directory`.
3. If file access fails, skip it and keep existing DB state.
4. Otherwise, run `scan_document_for_secrets()` and persist `has_secrets` plus `secret_detection_error`.
5. Keep fail-closed scanner semantics (`has_secrets = 1` when scanning fails).

### What it does NOT do

- It does not modify `needs_parsing`.
- It does not update tasks.
- It does not create redaction cache files.
- It does not run search indexing.

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
| `daily_history.md.j2` | `_Matlock/History/<YYYY-MM-DD>.md` | After `rollup`; history renders also refresh today's rollup snapshot |

### Logic

1. Load Jinja2 templates from `matlock/templates/`.
2. For history target (`history` or `all`), run `rollup` for today before rendering history pages so `file_touch` and `daily_task` are refreshed.
3. Query the database for required data per dashboard type (see `docs/matlock-generated-reports.md` for field details).
4. Render each template with the query results.
5. Write output to `_Matlock/` (creates directories as needed).
6. Rows written to `file` with `is_generated = 1` for each output file, so `sync` ignores them.

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

Runs all five stages in order: `[scan-projects →]` `sync` → `parse` → `map-projects` → `rollup` → `report`, with optional post-pipeline search indexing.

| Flag | Description |
|:-----|:------------|
| `--scan-projects` | Run `scan-projects --merge` before `sync` |
| `--skip-rollup` | Skip Stage IV (use when running mid-day; rollup is designed for nightly use) |
| `--force-sync` | Pass `--force` to the `sync` stage |
| `--force-report` | Pass `--force` to the `report` stage |
| `--index-search` | Run search indexing after `report` |
| `--config PATH` | Passed through to all stages |

When `--index-search` is enabled, search indexing runs after the core pipeline so `file`, `task`, `project`, and report-side generated-file metadata are already current.

---

## Optional Search Services

Search is additive to the core five-stage pipeline. It introduces one write path (`matlock search index`) and one read path (`matlock search query`).

### `matlock search index`

**Module:** `matlock/stages/search_index.py` and `matlock/search/indexer.py`
**Command:** `matlock search index`

#### Responsibility

Build or refresh local search state for active, non-generated Markdown files.

#### Logic

1. Select candidate rows from `file` where the file is active and search freshness is missing or stale, plus any files forced by frontmatter override or CLI `--force`.
2. Read each file from disk and parse frontmatter when available.
3. Apply per-file overrides from `matlock.search` frontmatter:
   - `exclude: true` clears search rows and marks the file as indexed without chunk/vector writes.
   - `force_index: true` forces re-indexing even when freshness appears current.
4. Chunk the body text using the configured chunking strategy and optional frontmatter/context template prefix.
5. For unsafe files (`has_secrets = 1`), replace raw body text with cached redacted content before chunk persistence.
6. Replace `search_chunks` rows for the file and synchronize `search_fts` through triggers.
7. Generate embeddings for each chunk and upsert `search_vec` rows.
8. Mark the file's `search_indexed_at` and `search_index_hash`, batching commits according to `search.indexing.batch_size`.
9. Purge orphaned search rows whose owning file is deleted or generated.

### `matlock search query`

**Module:** `matlock/search/query_cli.py` and `matlock/search/query_engine.py`
**Command:** `matlock search query`

#### Responsibility

Execute `metadata_only`, `fts_only`, `vector_only`, or `hybrid` search requests against the local SQLite search index.

#### Behavior

- Human mode prints concise text results to stdout.
- `--stdio` mode reads a JSON request from stdin and emits a JSON response only to stdout.
- Deterministic exit codes are reserved for success (`0`), input/schema failures (`1`), storage failures (`2`), and embedding-provider failures (`3`).
- Search query logging is isolated away from stdout so machine callers can parse responses safely.
- Unsafe file/chunk content is emitted as redacted text in both human and machine responses.

---

## Server Mode — `matlock server`

**Module:** `matlock/server.py`
**Command:** `matlock server`

### Behaviour

Runs a persistent daemon that orchestrates all pipeline stages in response to events. Combines three triggers:

1. **Watcher (real-time I/O):**
   - Uses `watchdog` to monitor `base_directory`.
   - On file create/modify/delete: runs `sync`, then `parse`.
   - Marks affected project IDs as "dirty" in an in-memory queue.

2. **Debouncer (UI refresh):**
   - Listens to the dirty-project queue.
   - After a configurable idle period with no new changes (`debounce_seconds`, default `5`), triggers `map-projects` followed by a full `report` pass.

3. **Scheduler (nightly metrics):**
   - At midnight (00:01): triggers `rollup` then a full `report` rebuild (Daily Dashboard + new History page).

4. **Startup refresh (optional):**
   - One-time startup actions can run before watcher/debouncer/scheduler threads start.
   - Startup order when combined: forced `sync`+`parse`, then forced `rollup` for today, then forced `report`, then one startup search indexing pass.

5. **Continuous search indexing (optional):**
   - A background thread can poll for stale search work when `--index-search-continuous` is enabled.
   - It shares the same pipeline lock as watcher, debouncer, and scheduler execution to avoid concurrent SQLite work.

### Flags

| Flag | Description |
|:-----|:------------|
| `--config PATH` | Path to `config.yaml` |
| `--debounce SECONDS` | Override debounce window (default: `5`) |
| `--scan-projects` | Run `scan-projects --merge` once at startup before the watcher starts |
| `--force-sync` | Run a full forced sync+parse once at startup before the watcher starts |
| `--force-rollup` | Run rollup for today once at startup (after any forced sync) |
| `--force-report` | Run a full forced report once at startup (after any startup sync/rollup) |
| `--index-search` | Run search indexing once at startup before the watcher starts |
| `--index-search-continuous` | Continuously poll for stale search work in the background |
| `--skip-rollup` | Skip rollup in the nightly scheduled job |

Startup flags are one-shot startup actions; they do not change watcher/debouncer behavior after startup. `--index-search-continuous` is the long-lived search option.

---

## Database State Invariants

At any point in time, the database state should satisfy:

- `needs_parsing = 0` for all non-deleted files → `parse` is up to date.
- `file_project` reflects the current `config.yaml` → `map-projects` has run since last config change.
- `daily_metric` has a row for every past date → `rollup` has run each night.
- All `_Matlock/` files have `is_generated = 1` in the `file` table.
- Files with current search rows have `search_index_hash = file.sha256` and a non-null `search_indexed_at`.
