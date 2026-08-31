# Matlock: High-Level Design

**Version:** 0.5.x
**Motto:** "I'm just looking at the evidence… and the evidence says you're procrastinating."

## 1. Core Philosophy

Matlock is a pipeline-driven task extractor, report generator, and optional local search engine for Markdown "second brain" vaults.

- **Source-of-Truth:** Your existing Markdown notes. Matlock never modifies them.
- **Comprehensible:** A "worker-per-task" script pipeline where every stage is a standalone, independently runnable command. Shared state lives entirely in a single SQLite database.
- **Performance:** Incremental sync via SHA-256 hashing — only modified files are re-parsed. Full-vault re-scans are always available via explicit CLI flags.
- **Local-First Search:** Optional chunk, FTS, and embedding-backed search stays on the same SQLite foundation and remains opt-in at runtime.
- **Secret-Safe Retrieval:** Secret detection state controls whether search/doc reads emit raw or redacted content.

## 2. 2-Tier Project Hierarchy

Matlock enforces a strict, flat two-tier hierarchy defined in `config.yaml`. This constraint prevents deep nesting and keeps SQL aggregations simple.

1. **Super Projects** — Broad thematic categories (e.g., "Personal Admin", "Website Overhaul").
2. **Projects** — Actionable buckets (e.g., "Taxes 2026") that map directly to specific files or directories in your vault.

## 3. The Core Five-Stage Pipeline

Each stage is a discrete, independently invocable command. Stages communicate **exclusively** through the SQLite database — no stage calls another directly.

| Stage | Command              | Job                                                               |
|:------|:---------------------|:------------------------------------------------------------------|
| I     | `matlock sync`       | Hash files, update `file` table, flag `needs_parsing`            |
| II    | `matlock parse`      | Extract tasks from flagged files, write to `task` table          |
| III   | `matlock map-projects` | Link files to projects per `config.yaml`                       |
| IV    | `matlock rollup`     | Nightly math: daily metrics, streaks, heatmap data               |
| V     | `matlock report`     | Render Jinja2 Markdown dashboards to `_Matlock/`                 |

Run all five stages in sequence with `matlock run-all`.

For direct reads with the same safety guarantees, use `matlock doc-read <file_path>`.
For bulk legacy-state resolution, use `matlock detect-backfill [--retry-errors]`.

Matlock Search is additive rather than replacing the core pipeline:

- `matlock search index` reads active vault files, creates chunk/FTS/vector search state, and tracks freshness on `file` rows.
- `matlock search query` is read-only against the local SQLite search index and supports both human CLI output and strict machine JSON transport.
- `matlock run-all --index-search` appends indexing after the five core stages.

## 4. Directory Structure Boundary (The Guardrail)

```
<base_directory>/
    Your Notes/
    Projects/
    ...
    _Matlock/               ← Output only. Never read as input.
        Home.md
        Due Today.md
        Past Due.md
        Due Soon.md
        Future Due.md
        Not Due.md
        Warnings.md
        Projects.md         ← Projects index
        Super Projects.md   ← Super Projects index
        Projects/
            Unassigned.md   ← Virtual page (always generated)
        Super Projects/
            Unassigned.md   ← Virtual page (always generated)
        History/
```

The `sync` stage skips `_Matlock/` entirely (the output directory is excluded from the scan path). This prevents recursive loops where generated reports are re-ingested.

## 5. Execution Modes

### Manual Invocation (Primary)

Each pipeline stage runs independently from the CLI. This is the primary mode for development, debugging, and ad-hoc refreshes. See `docs/matlock-cli.md` for the full command reference.

### Continuous Server Mode (`matlock server`)

A persistent daemon with three core triggers plus an optional search indexer:

- **Watcher** — `watchdog` monitors the `base_directory` for file-system events. On create/modify/delete: triggers `sync` + `parse`, then marks matching projects dirty.
- **Debouncer** — Coalesces rapid file changes (configurable idle window, default 5 seconds) before triggering `map-projects` + a full `report` pass.
- **Scheduler** — Midnight trigger runs `rollup` + `report` (Daily History page + refreshed Global Dashboard).
- **Search Indexer** — Optional startup and continuous background indexing can run under the same pipeline lock as the watcher, debouncer, and scheduler.

For search-specific behavior and contracts, see `docs/matlock-search.md`.

## 6. Search Subsystem

The optional search subsystem adds local retrieval without changing the existing pipeline contract.

| Command | Purpose |
|:--------|:--------|
| `matlock search index` | Build or refresh local chunk, FTS, and embedding-backed search state |
| `matlock search query` | Query the local index in human mode or strict `--stdio` JSON mode |

Secret-safe behavior applies to search results and direct document reads:

- Unsafe documents are indexed and returned as redacted content.
- Legacy unknown-state documents are resolved lazily and persisted.

Search indexing excludes generated and soft-deleted files, honors per-file frontmatter overrides, and can run from `run-all` or `server` entrypoints.

## 7. Discovery Commands

Alongside the pipeline, Matlock provides standalone **discovery commands** that read the vault directly and do not touch the SQLite database.

| Command | Purpose |
|:--------|:--------|
| `matlock scan-projects` | Reverse-engineer the project/super-project hierarchy from frontmatter metadata |

`scan-projects` is designed for **bootstrapping** (initial `config.yaml` population) and **drift auditing** (checking whether the vault has diverged from the config). See `docs/matlock-scan-projects.md` for full details.

## 8. Decoupled Stage Design (The "Why")

Each stage can be swapped or extended without touching any other stage:

- **Stage I (Sync)** — Today it watches the filesystem. Tomorrow it could pull from a remote API or Git repository. The rest of the system only cares that the `file` table is current.
- **Stage II (Parse)** — Today it extracts Markdown tasks. You could add a second parser that extracts flashcards or meeting action items; they would write to separate tables and run independently.
- **Stage III (Map Projects)** — Today it maps by directory/file path in `config.yaml`. Tomorrow it could map by `#tags` inside files. Only this module changes.
- **Stage IV (Rollup)** — Runs computationally expensive historical queries once per day without slowing real-time editing.
- **Stage V (Report)** — Today it outputs Markdown dashboards. A future module could generate a local web UI or send an email. The same reliable database is the data source.
- **Search Layer** — Today it uses SQLite FTS plus local embeddings. A future module could swap embedding providers or ranking strategies without changing the core sync/parse/report pipeline.

## 9. Debugging the Pipeline

Because there is no hidden event loop or magic, diagnosing stale output is trivial:

1. Did the file save? → Check the filesystem.
2. Did `sync` catch it? → `SELECT needs_parsing FROM file WHERE file_path = '...'`
3. Did `parse` extract it? → `SELECT * FROM task WHERE file_path = '...'`
4. Did `report` fail? → Check the log file (see `log_path` in `config.yaml`).

Each stage can be re-run independently without side effects. All DB writes are upserts or atomic delete-then-insert operations.

## 10. Logging

Matlock uses Python's stdlib `logging` module. Logging is configured once at CLI startup via `matlock.logging_setup.setup_logging(config)`.

- **Console (stderr):** WARNING and above — always active, regardless of config.
- **File (rotating):** DEBUG and above — enabled when `log_path` is set in `config.yaml`. The file rotates when it reaches `log_max_bytes`; up to `log_backup_count` rotated files are kept.
- **Search query isolation:** `matlock search query --stdio` temporarily routes root logging to a dedicated `search.log` file while reserving stderr for error-level events only, so stdout remains machine-parseable JSON.

Typical log file entries:

```
2026-04-28T09:01:00 INFO     matlock.stages.sync: sync complete: inserted=12 updated=3 ...
2026-04-28T09:01:01 INFO     matlock.stages.parse: parse: 15 file(s) flagged for parsing
2026-04-28T09:01:02 WARNING  matlock.stages.parse: parse: skipped Notes/broken.md (extraction error)
```

Configure in `config.yaml`:

```yaml
log_path: "/Users/me/Matlock/matlock.log"   # absolute path recommended
log_max_bytes: 10000000                       # 10 MB
log_backup_count: 3
```

Omit `log_path` (or set to `null`) to suppress file logging while keeping stderr warnings.

## 11. Output & Rendering

All reports are standard Markdown (tables, checkboxes, emojis). They render natively in Obsidian, VS Code, and GitHub — no plugins required.

Four dashboard types:

- **Global Dashboard** — Daily launchpad with GitHub-style emoji heatmap and streak tracker.
- **Super-Project Page** — Status roll-up of all child projects.
- **Project Page** — Actionable task lists with `[[backlinks]]` deep-linked to source files.
- **Daily History** — Immutable daily completed-task log (automated work journal).

See `docs/matlock-generated-reports.md` for full Jinja2 template specifications.
