# Matlock Data Model

**Version:** 0.5.x

This document defines the in-memory Python models produced by the parser engine and the SQLite database schema that persists all pipeline data.

---

## In-Memory Models (Parser Output)

These Pydantic models are the output of the Stage II Parse engine (`matlock.extractor`). They are intermediate representations that are serialised into the database by the `parse` stage.

### `ParsedMarkdownFile`

Represents a fully parsed Markdown file.

| Field        | Type                  | Description                                         |
|:-------------|:----------------------|:----------------------------------------------------|
| `file_path`  | `str \| None`         | Relative path from `base_directory`                |
| `created`    | `int \| None`         | File creation timestamp (Unix epoch)               |
| `modified`   | `int \| None`         | File modification timestamp (Unix epoch)           |
| `length`     | `int \| None`         | File size in bytes                                 |
| `word_count` | `int \| None`         | Approximate word count of the document body       |
| `sha256`     | `str \| None`         | SHA-256 hex digest of file content                |
| `meta_data`  | `dict[str, Any]`      | Parsed YAML/TOML front-matter key-value pairs     |
| `tasks`      | `list[ParsedMarkdownTask]` | All tasks extracted from the document        |

### `ParsedMarkdownTask`

Represents a single checkbox task item.

| Field            | Type                  | Description                                                      |
|:-----------------|:----------------------|:-----------------------------------------------------------------|
| `task_id`        | `str`                 | Deterministic SHA-256 ID (stable across re-parses)             |
| `parent_task_id` | `str \| None`         | `task_id` of the parent task if this is a sub-task             |
| `checked`        | `bool`                | `True` if `[x]`, `False` if `[ ]`                              |
| `task_text`      | `str`                 | Cleaned task text with attributes stripped                     |
| `overflow`       | `bool`                | `True` if `task_text` was truncated to `task_text_maxlen`      |
| `headers`        | `list[str]`           | Ancestor heading chain at the point of the task (nearest first)|
| `attributes`     | `dict[str, Any]`      | Parsed structured attributes (dates, domains, durations)       |
| `errors`         | `list[str]`           | Attribute parse errors (do not block task insertion)           |
| `twin_index`     | `int`                 | Disambiguator for otherwise-identical tasks in the same file   |

**`task_id` stability:** The ID is derived from `file_path`, `task_text`, `overflow`, `headers`, `parent_task_id`, and `twin_index` via SHA-256. It is stable as long as the task text and its structural position do not change.

---

## Search API Models

Matlock Search uses two Pydantic contracts for machine-facing request/response transport.

### `MatlockSearchRequest` (`matlock.search.v1`)

| Field | Type | Description |
|:------|:-----|:------------|
| `query` | `str \| None` | Search text. Empty or omitted values implicitly switch execution to `metadata_only`. |
| `search_mode` | `hybrid \| fts_only \| vector_only \| metadata_only` | Requested retrieval strategy. |
| `tuning.hybrid_alpha` | `float` | Hybrid weighting hint for reciprocal-rank fusion. |
| `tuning.min_score` | `float \| None` | Optional score threshold. |
| `filters.created` / `filters.modified` | Date range object | RFC3339-normalized min/max bounds. |
| `filters.project_id` / `filters.super_project_id` | `list[str] \| None` | Project-scoped filtering. Strings are normalized to single-item lists. |
| `filters.project_match_mode` | `exact \| fuzzy \| vector \| hybrid` | Match mode field accepted by the contract. Current query execution uses exact project filtering. |
| `filters.file_paths` | `list[str]` | Exact file-path filters. |
| `filters.metadata` | `list[SearchMetadataFilter]` | Frontmatter filter predicates (`eq`, `neq`, `contains`, `gt`, `gte`, `lt`, `lte`, `in`). |
| `output.granularity` | `chunk \| file` | Result shape. |
| `output.surrounding_chunks` | `int` | Number of adjacent chunks to include around chunk hits (0-3). |
| `output.limit` / `output.offset` | `int` | Result window controls. |
| `output.include_content` | `bool` | Whether to include matched content in returned payloads. |

### `MatlockSearchResponse` (`matlock.search.response.v1`)

| Field | Type | Description |
|:------|:-----|:------------|
| `status` | `success \| error` | Overall execution status. |
| `stats.total_matches` | `int` | Number of matches found before pagination. |
| `stats.returned_matches` | `int` | Number of results emitted in this response. |
| `stats.query_time_ms` | `float` | Query execution time in milliseconds. |
| `stats.search_mode_executed` | Search mode literal | Actual mode used after normalization. |
| `results[]` | `SearchResponseResult` | Per-file or per-chunk result rows, including score breakdown and normalized timestamps. |
| `error` | `SearchError \| None` | Structured error payload for non-success responses. |

`SearchResponseResult` includes document safety metadata copied from the `file` table:

- `has_secrets`: `NULL` (legacy unknown), `0` (clean), `1` (secrets found or scan failed).
- `secret_detection_error`: optional scanner error text. Populated only when detection fails.

---

## Database Models (SQLite)

The SQLite database is the sole integration point between pipeline stages. All stages read from and write to it exclusively.

### `file` table

Tracks every Markdown file under `base_directory`.

| Column          | Type    | Constraints      | Description                                              |
|:----------------|:--------|:-----------------|:---------------------------------------------------------|
| `file_path`     | TEXT    | PRIMARY KEY      | Path relative to `base_directory`                       |
| `sha256`        | TEXT    |                  | SHA-256 of current file content                        |
| `file_ext`      | TEXT    |                  | File extension (e.g., `.md`)                           |
| `created`       | INTEGER |                  | Creation timestamp (Unix epoch)                        |
| `modified`      | INTEGER |                  | Last-modified timestamp (Unix epoch)                   |
| `modified_date` | TEXT    |                  | YYYY-MM-DD of `modified`                               |
| `deleted`       | INTEGER |                  | `1` if the file no longer exists on disk               |
| `length`        | INTEGER |                  | File size in bytes                                     |
| `word_count`    | INTEGER |                  | Approximate word count                                 |
| `meta_data`     | TEXT    |                  | JSON-serialised front-matter dict                      |
| `is_generated`  | INTEGER |                  | `1` if created by `report` stage; skipped by `sync`   |
| `needs_parsing` | INTEGER |                  | `1` if `sync` detected a change; `0` after `parse`    |
| `deleted_date`  | TEXT    |                  | YYYY-MM-DD when the file was first detected as deleted (set by `sync`; NULL for active files) |
| `search_indexed_at` | TEXT |                | RFC3339 timestamp for the last successful search indexing pass |
| `search_index_hash` | TEXT |                | File SHA-256 captured during the last successful search indexing pass |
| `has_secrets` | INTEGER |                  | Nullable detection state: `NULL` unknown legacy state, `0` confirmed clean, `1` secrets found or scan failed |
| `secret_detection_error` | TEXT |            | Nullable scanner failure detail set only when secret detection errors occur |

Backfill behavior:

- `matlock detect-backfill` updates only `has_secrets` and `secret_detection_error` for candidate rows.
- Default candidates are active tracked rows where `has_secrets IS NULL`.
- `--retry-errors` includes rows with `secret_detection_error IS NOT NULL`.

Redaction cache layout:

- Successful redacted document content is cached by raw-byte SHA-256 under `cache.redacted_dir/<sha256[:2]>/<sha256[2:4]>/<sha256>.txt`.
- Reads check the sharded path first, then the legacy flat fallback `cache.redacted_dir/<sha256>.txt`.
- Scan failures return placeholders and do not create cache files.

### `task` table

Stores all extracted task items.

| Column           | Type    | Constraints              | Description                                          |
|:-----------------|:--------|:-------------------------|:-----------------------------------------------------|
| `task_id`        | TEXT    | PRIMARY KEY              | Deterministic SHA-256 ID                            |
| `file_path`      | TEXT    | FOREIGN KEY → `file`     | Source file path                                    |
| `parent_task_id` | TEXT    |                          | Parent task ID (nullable)                           |
| `created_date`   | TEXT    |                          | YYYY-MM-DD from `created_date` attribute            |
| `due_date`       | TEXT    |                          | YYYY-MM-DD from `due_date` attribute                |
| `est_comp_date`  | TEXT    |                          | YYYY-MM-DD from `est_comp_date` attribute           |
| `act_comp_date`  | TEXT    |                          | YYYY-MM-DD from `act_comp_date` attribute           |
| `checked`        | INTEGER |                          | `1` if completed                                    |
| `task_text`      | TEXT    |                          | Cleaned task text                                   |
| `overflow`       | INTEGER |                          | `1` if truncated                                    |
| `headers`        | TEXT    |                          | JSON-serialised list of ancestor headings           |
| `attributes`     | TEXT    |                          | JSON-serialised attribute dict                      |
| `errors`         | TEXT    |                          | JSON-serialised list of parse error strings         |
| `twin_index`     | INTEGER |                          | Duplicate disambiguation index                      |

### `super_project` table

Top-level project groupings defined in `config.yaml`.

| Column             | Type | Constraints | Description              |
|:-------------------|:-----|:------------|:-------------------------|
| `super_project_id` | TEXT | PRIMARY KEY | Unique string identifier |
| `title`            | TEXT |             | Display name             |
| `priority`         | TEXT |             | `Low`, `Medium`, `High`  |

### `project` table

Actionable project buckets, optionally grouped under a SuperProject.

| Column             | Type | Constraints                       | Description                  |
|:-------------------|:-----|:----------------------------------|:-----------------------------|
| `project_id`       | TEXT | PRIMARY KEY                       | Unique string identifier     |
| `super_project_id` | TEXT | FOREIGN KEY → `super_project` (nullable) | Parent grouping       |
| `title`            | TEXT |                                   | Display name                 |
| `home_file`        | TEXT |                                   | Primary source file path     |
| `priority`         | TEXT |                                   | `Low`, `Medium`, `High`      |
| `status`           | TEXT |                                   | `Planned`, `In Progress`, `Complete`, `On Hold`, `Cancelled` |
| `start_date`       | TEXT |                                   | YYYY-MM-DD                   |
| `due_date`         | TEXT |                                   | YYYY-MM-DD                   |

### `file_project` table

Many-to-many join between files and projects.

| Column       | Type | Constraints                   | Description    |
|:-------------|:-----|:------------------------------|:---------------|
| `file_path`  | TEXT | PRIMARY KEY, FOREIGN KEY → `file`    | Source file |
| `project_id` | TEXT | PRIMARY KEY, FOREIGN KEY → `project` | Owning project |

### `daily_metric` table

Immutable historical record populated by the `rollup` stage each night.

| Column                       | Type    | Constraints | Description                         |
|:-----------------------------|:--------|:------------|:------------------------------------|
| `metric_date`                | TEXT    | PRIMARY KEY | YYYY-MM-DD                          |
| `project_id`                 | TEXT    | PRIMARY KEY (nullable) | Project ID or NULL for unassigned tasks |
| `tasks_created_count`        | INTEGER |             | Tasks created on this date          |
| `tasks_created_minutes`      | INTEGER |             | Estimated minutes for created tasks |
| `tasks_completed_count`      | INTEGER |             | Tasks completed on this date        |
| `tasks_completed_minutes`    | INTEGER |             | Minutes for completed tasks         |
| `tasks_due_tomorrow_count`   | INTEGER |             | Tasks due the following day         |
| `tasks_due_tomorrow_minutes` | INTEGER |             |                                     |
| `tasks_past_due_count`       | INTEGER |             | Overdue open tasks at rollup time   |
| `tasks_past_due_minutes`     | INTEGER |             |                                     |
| `tasks_future_due_count`     | INTEGER |             | Open tasks with a future due date   |
| `tasks_future_due_minutes`   | INTEGER |             |                                     |
| `files_created_count`        | INTEGER |             | Files created on this date          |
| `files_modified_count`       | INTEGER |             | Files modified on this date         |
| `files_deleted_count`        | INTEGER |             | Files deleted on this date          |

### `file_touch` table

Immutable per-day file-event snapshot. Populated by the `rollup` stage alongside `daily_metric`. Allows accurate re-generation of history pages long after the events occurred.

| Column       | Type    | Constraints | Description                                                    |
|:-------------|:--------|:------------|:---------------------------------------------------------------|
| `file_path`  | TEXT    | PK part     | Path relative to `base_directory`                             |
| `touch_date` | TEXT    | PK part     | YYYY-MM-DD of the event                                       |
| `event_type` | TEXT    | PK part     | `'created'` \| `'modified'` \| `'deleted'`                    |
| `modified`   | INTEGER |             | File `modified` epoch (ms) at snapshot time; NULL for deleted |

**Primary key:** `(file_path, touch_date, event_type)` — allows a file to be both created and deleted on the same day.

**Idempotency:** `INSERT OR REPLACE` — re-running rollup for the same date replaces existing rows cleanly.

### Search tables

Search indexing is additive to the main schema and keyed from `file.file_path`.

#### `search_chunks`

| Column | Type | Constraints | Description |
|:-------|:-----|:------------|:------------|
| `search_rowid` | INTEGER | PRIMARY KEY AUTOINCREMENT | Surrogate row ID used by FTS |
| `chunk_id` | TEXT | UNIQUE, NOT NULL | Stable external chunk ID (`<file_path>#<index>`) |
| `file_id` | TEXT | FOREIGN KEY → `file.file_path` | Owning file path |
| `chunk_index` | INTEGER | CHECK `>= 0` | Chunk position within the file |
| `content` | TEXT | NOT NULL | Stored chunk text, including optional frontmatter prefix |

#### `search_vec`

| Column | Type | Constraints | Description |
|:-------|:-----|:------------|:------------|
| `chunk_id` | TEXT | PRIMARY KEY, FOREIGN KEY → `search_chunks.chunk_id` | Chunk identity |
| `embedding` | BLOB | | JSON-encoded vector bytes |
| `embedding_model` | TEXT | | Model name used to produce the vector |
| `embedding_dim` | INTEGER | | Vector length |

#### `search_fts`

`search_fts` is an FTS5 virtual table with external content wiring to `search_chunks.content` through `search_rowid`. The schema uses the Porter tokenizer.

#### Search triggers

- `search_chunks_ai`, `search_chunks_ad`, `search_chunks_au` keep `search_fts` synchronized with inserts, deletes, and updates in `search_chunks`.
- `file_search_cleanup_on_soft_delete` removes search rows and clears freshness fields when a file becomes deleted or generated.
- `file_search_reset_on_hash_change` clears freshness fields when a tracked file hash changes.

### `daily_task` table

Immutable per-day task-event snapshot. Populated by the `rollup` stage. Denormalizes key task fields so history pages can be regenerated accurately even if the source task is later edited or deleted.

| Column       | Type | Constraints | Description                                            |
|:-------------|:-----|:------------|:-------------------------------------------------------|
| `task_id`    | TEXT | PK part     | SHA-256 task identifier (matches `task.task_id`)      |
| `event_date` | TEXT | PK part     | YYYY-MM-DD of the event                               |
| `event_type` | TEXT | PK part     | `'created'` \| `'completed'`                          |
| `task_text`  | TEXT |             | Denormalized task text at snapshot time               |
| `file_path`  | TEXT |             | Source file path at snapshot time                     |
| `attributes` | TEXT |             | JSON-serialized attributes at snapshot time (used for estimate extraction) |

**Primary key:** `(task_id, event_date, event_type)` — a task can be both created and completed on the same day.

**Idempotency:** `INSERT OR REPLACE` — re-running rollup for the same date replaces existing rows cleanly.

---

## Database Connection

The database is a single SQLite file. Path is configured via `db_path` in `config.yaml` and is not rewritten relative to `base_directory`. The connection enforces the following PRAGMAs on every open:

```sql
PRAGMA journal_mode = WAL;
PRAGMA synchronous = NORMAL;
PRAGMA busy_timeout = 5000;
PRAGMA foreign_keys = ON;
```
