# Matlock CLI Reference

**Version:** 0.7.x

Matlock is invoked via the `matlock` command (registered as a Poetry script entrypoint). All commands accept `--config PATH` to specify a non-default `config.yaml` location.

---

## Global Options

```
matlock [OPTIONS] COMMAND [ARGS]...
```

| Option | Description |
|:-------|:------------|
| `--config PATH`, `-c PATH` | Path to `config.yaml` (default: `./config.yaml`) |
| `--help` | Show help and exit |

---

## Pipeline Commands

### `matlock sync`

**Stage I.** Walk the vault, hash all Markdown files, update the `file` table, and flag changed files with `needs_parsing = 1`.

```
matlock sync [OPTIONS]
```

| Option | Default | Description |
|:-------|:--------|:------------|
| `--force` | False | Re-hash every file and mark all `needs_parsing = 1`, regardless of hash comparison |
| `--config PATH` | `./config.yaml` | Config file location |

**Example:**
```bash
poetry run matlock sync
poetry run matlock sync --force
```

---

### `matlock extract`

**Stage II.** Extract tasks from all files where `needs_parsing = 1` and write results to the `task` table.

```
matlock extract [OPTIONS]
```

| Option | Default | Description |
|:-------|:--------|:------------|
| `--config PATH` | `./config.yaml` | Config file location |

**Example:**
```bash
poetry run matlock extract
```

---

### `matlock secrets backfill`

Backfill secret-detection state for tracked vault documents.

```
matlock secrets backfill [OPTIONS]
```

| Option | Default | Description |
|:-------|:--------|:------------|
| `--retry-errors` | False | Also re-scan active tracked files where a previous secret scan stored `secret_detection_error` |
| `--config PATH` | `./config.yaml` | Config file location |

Behavior:

- Default mode scans only active, non-generated tracked files with `has_secrets IS NULL`.
- `--retry-errors` expands candidate selection to include rows with prior scanner errors.
- Missing/unreadable files are skipped and keep their prior DB state.
- The command updates only `has_secrets` and `secret_detection_error`; it does not re-parse, reindex search, or write redaction cache files.

**Examples:**
```bash
poetry run matlock secrets backfill
poetry run matlock secrets backfill --retry-errors
```

---

### `matlock document read`

Read one tracked document from the vault with secret-safe behavior.

```
matlock document read FILE_PATH [OPTIONS]
```

| Argument / Option | Description |
|:------------------|:------------|
| `FILE_PATH` | Vault-relative path (for example `Notes/today.md`) or an absolute path contained inside `base_directory` |
| `--config PATH` | Config file location |

Behavior:

- Rejects paths outside `base_directory`.
- Rejects files not tracked in the `file` table.
- Rejects files marked deleted in the database.
- If `file.has_secrets = 0`, emits raw UTF-8 file content.
- If `file.has_secrets = 1`, emits cache-backed redacted content.
- If `file.has_secrets IS NULL` (legacy state), runs an on-demand secret scan, persists the state, then emits raw or redacted content based on that result.

**Examples:**
```bash
poetry run matlock document read Notes/today.md
poetry run matlock document read /absolute/path/inside/your/vault/Notes/today.md
```

---

### `matlock projects query`

Return project records as JSON. Without a positional term, it emits the complete project table in `project_id` order. With a term, it performs a case-insensitive literal substring match across every project-column value and automatically unions in projects associated with matching indexed files. Field filters are AND-composed across categories with repeated values ORed within the same option.

```
matlock projects query [TEXT] [OPTIONS]
```

| Argument / Option | Default | Description |
|:------------------|:--------|:------------|
| `TEXT` | None | Optional project text filter |
| `--project-id VALUE` | repeated | Repeatable project ID match |
| `--super-project-id VALUE` | repeated | Repeatable super-project ID match |
| `--title TEXT` | repeated | Repeatable title literal substring search |
| `--home-file TEXT` | repeated | Repeatable home-file literal substring search |
| `--priority TEXT` | repeated | Repeatable priority literal substring search |
| `--status TEXT` | repeated | Repeatable status literal substring search |
| `--start-date VALUE` | repeated | Repeatable ISO date predicate such as `>=2026-01-01` |
| `--due-date VALUE` | repeated | Repeatable ISO date predicate for due_date |
| `--search-mode {hybrid,fts_only,vector_only}` | `hybrid` | Search mode reused by the existing search contract |
| `--config PATH` | `./config.yaml` | Config file location |

**Examples:**
```bash
poetry run matlock projects query
poetry run matlock projects query "alpha"
poetry run matlock projects query "alpha" --search-mode hybrid
poetry run matlock projects query --project-id backend --status active
```

---

### `matlock super-projects list`

Return every `super_project` row as JSON.

```
matlock super-projects list [OPTIONS]
```

| Option | Default | Description |
|:-------|:--------|:------------|
| `--config PATH` | `./config.yaml` | Config file location |

**Example:**
```bash
poetry run matlock super-projects list
```

---

### `matlock tasks query`

Return active, non-generated tasks as JSON, optionally filtered by date, completion, text, headers, attributes, project IDs, and super-project IDs.

```
matlock tasks query [OPTIONS]
```

| Option | Default | Description |
|:-------|:--------|:------------|
| `--due-date VALUE` | repeated | Repeatable ISO date predicate such as `>=2026-01-01` |
| `--est-comp-date VALUE` | repeated | Repeatable estimated completion predicate |
| `--act-comp-date VALUE` | repeated | Repeatable actual completion predicate |
| `--checked / --unchecked` | none | Filter by completion state |
| `--task-text TEXT` | None | Case-insensitive literal substring match |
| `--headers TEXT` | None | Case-insensitive literal JSON-list match |
| `--attributes TEXT` | None | Case-insensitive literal JSON-object match |
| `--project-id VALUE` | repeated | Repeatable project filter |
| `--super-project-id VALUE` | repeated | Repeatable super-project filter |
| `--config PATH` | `./config.yaml` | Config file location |

**Examples:**
```bash
poetry run matlock tasks query
poetry run matlock tasks query --checked --task-text "review"
poetry run matlock tasks query --project-id backend --due-date ">=2026-01-01"
```

---

### `matlock projects map`

**Stage III.** Rebuild the `project`, `super_project`, and `file_project` tables from `config.yaml`. Always a full rebuild.

```
matlock projects map [OPTIONS]
```

| Option | Default | Description |
|:-------|:--------|:------------|
| `--config PATH` | `./config.yaml` | Config file location |

**Example:**
```bash
poetry run matlock projects map
```

---

### `matlock metrics rollup`

**Stage IV.** Calculate and persist daily metrics for the previous day (or a specified date) into the `daily_metric` table. Designed for nightly use; idempotent.

```
matlock metrics rollup [OPTIONS]
```

| Option | Default | Description |
|:-------|:--------|:------------|
| `--date YYYY-MM-DD` | Yesterday | Calculate metrics for this specific date |
| `--config PATH` | `./config.yaml` | Config file location |

**Example:**
```bash
poetry run matlock metrics rollup
poetry run matlock metrics rollup --date 2026-04-20
```

---

### `matlock reports render`

**Stage V.** Render all (or a subset of) Jinja2 Markdown dashboards and write them to `output_directory`.

```
matlock reports render [OPTIONS]
```

| Option | Default | Description |
|:-------|:--------|:------------|
| `--target {all,dashboard,projects,history}` | `all` | Limit which report types to generate |
| `--project-id ID` | None | Regenerate a single project page by its config ID |
| `--force` | False | Regenerate all reports and delete any generated files that are no longer valid |
| `--config PATH` | `./config.yaml` | Config file location |

**Example:**
```bash
poetry run matlock reports render
poetry run matlock reports render --target projects
poetry run matlock reports render --project-id backend_api
poetry run matlock reports render --force
```

**Note:** `--target projects` regenerates both project pages and super-project pages. There is no separate `super-projects` target in the current CLI.

---

### `matlock pipeline run`

Run all pipeline stages in sequence: `[projects discover →]` `sync` → `extract` → `projects map` → `metrics rollup` → `reports render`.

```
matlock pipeline run [OPTIONS]
```

| Option | Default | Description |
|:-------|:--------|:------------|
| `--discover-projects` | False | Run `projects discover --merge` before `sync` (opt-in) |
| `--skip-rollup` | False | Skip Stage IV (for mid-day runs; rollup is designed for nightly use) |
| `--force-sync` | False | Pass `--force` to the `sync` stage |
| `--force-report` | False | Pass `--force` to the `report` stage (regenerate all, delete stale files) |
| `--index-search` | False | Run search indexing after the core pipeline completes (opt-in) |
| `--config PATH` | `./config.yaml` | Config file location |

**Example:**
```bash
poetry run matlock pipeline run
poetry run matlock pipeline run --discover-projects
poetry run matlock pipeline run --skip-rollup
poetry run matlock pipeline run --force-sync
poetry run matlock pipeline run --force-report
poetry run matlock pipeline run --index-search
```

When `--discover-projects` is enabled, Matlock runs vault scan + config merge first, then reloads config and continues the normal pipeline. When `--index-search` is enabled, search indexing runs after `reports render` so file metadata, project mappings, and reports are already current.

---

## Search Commands

The `matlock search` command group exposes optional local indexing and query flows. See `docs/matlock-search.md` for the deeper operational guide and request/response contract details.

### `matlock search index`

Build or refresh the local search index for all eligible vault files.

```
matlock search index [OPTIONS]
```

| Option | Short | Default | Description |
|:-------|:------|:--------|:------------|
| `--force` | `-f` | `False` | Re-index all eligible files regardless of stored search freshness state |
| `--batch-size INT` | — | From `search.indexing.batch_size` | Override the configured SQLite commit batch size for this run |
| `--model STRING` | — | From `search.embedding.model_name` | Override the configured embedding model name for this run |
| `--config PATH` | `-c` | `./config.yaml` | Config file location |

The indexer only considers active, non-generated Markdown files. It clears search rows automatically for soft-deleted/generated files and refreshes stale files whose SHA-256 hash no longer matches `file.search_index_hash`.

**Examples:**
```bash
poetry run matlock search index
poetry run matlock search index --force
poetry run matlock search index --batch-size 25 --model sentence-transformers/all-MiniLM-L6-v2
```

### `matlock search query`

Run a local search query in either human CLI mode or strict `--stdio` machine mode.

```
matlock search query [QUERY] [OPTIONS]
```

| Option | Default | Description |
|:-------|:--------|:------------|
| `--stdio` | `False` | Read a JSON request from stdin and emit JSON-only to stdout |
| `--search-mode TEXT` | `hybrid` | Human-mode search strategy: `hybrid`, `fts_only`, `vector_only`, `metadata_only` |
| `--granularity TEXT` | `chunk` | Human-mode result shape: `chunk` or `file` |
| `--limit INT` | `10` | Maximum number of results to return in human mode |
| `--min-score FLOAT` | `None` | Optional minimum score threshold for returned matches |
| `--surrounding-chunks INT` | `0` | Number of adjacent chunks to include before/after a chunk hit (0-3) |
| `--include-content / --no-include-content` | `True` | Include matched content in human-mode output |
| `--config PATH` | `./config.yaml` | Config file location |

Human mode requires the positional `QUERY` argument. In `--stdio` mode, omit the positional argument and send a JSON object matching the `matlock.search.v1` request contract on stdin.

**Examples:**
```bash
poetry run matlock search query "database"
poetry run matlock search query "database" --search-mode fts_only --granularity file --limit 5
poetry run matlock search query "database" --search-mode hybrid --min-score 0.03 --limit 20
printf '{"query": "database", "search_mode": "fts_only"}' | poetry run matlock search query --stdio
```

In `--stdio` mode, stdout is reserved for the JSON response only. Operational logging is redirected to an isolated search log and stderr is reserved for error-level messages.

Deterministic exit codes:

| Exit Code | Meaning |
|:----------|:--------|
| `0` | Success |
| `1` | Input or schema error |
| `2` | Database or search storage error |
| `3` | Embedding provider error |

---

## Discovery Commands

### `matlock projects discover`

Walk the vault and reverse-engineer the project/super-project hierarchy from frontmatter metadata. Does **not** require an initialised database.

```
matlock projects discover [OPTIONS]
```

| Option | Short | Default | Description |
|:-------|:------|:--------|:------------|
| `--print-yaml` | `-p` | *(default)* | Print discovered projects as YAML |
| `--diff` | `-d` | — | Show a human-readable diff against the current config |
| `--merge` | `-m` | — | Apply changes into `config.yaml` (creates a timestamped backup first) |
| `--config PATH` | `-c` | `./config.yaml` | Config file location |

Only one mode flag may be given at a time. If none is given, `--print-yaml` is assumed.

**Examples:**
```bash
poetry run matlock projects discover
poetry run matlock projects discover --diff
poetry run matlock projects discover --merge
```

See `docs/matlock-scan-projects.md` for the full specification including scanning logic, data models, and output format details.

---

## Server Command

### `matlock serve`

Run a persistent daemon that monitors the vault in real time and orchestrates the pipeline automatically.

```
matlock serve [OPTIONS]
```

Three integrated triggers:

1. **File Watcher** (`watchdog`) — File create/modify/delete events wake the watcher, which runs `sync` + `parse` and marks matching project IDs dirty.
2. **Debouncer** — After `debounce_seconds` of idle time, runs `projects map` + a full `reports render` pass while clearing the dirty-project set.
3. **Scheduler** — At midnight (00:01): triggers `rollup` followed by a full `report` rebuild.
4. **Optional Search Indexer** — When `--index-search-continuous` is enabled, a background thread polls for stale search work and runs search indexing under the same pipeline lock as the other triggers.

| Option | Default | Description |
|:-------|:--------|:------------|
| `--debounce SECONDS` | From config (`5`) | Idle window before triggering `report` after file changes |
| `--discover-projects` | `False` | Run `projects discover --merge` once at startup before the watcher starts |
| `--force-sync` | `False` | Run a full forced sync+parse once at startup before the watcher starts |
| `--force-rollup` | `False` | Run rollup for today once at startup (after any forced sync) |
| `--force-report` | `False` | Run a full forced report once at startup (after any startup sync) |
| `--skip-rollup` | `False` | Skip rollup in the nightly scheduled job |
| `--index-search` | `False` | Run search indexing once at startup before the watcher starts |
| `--index-search-continuous` | `False` | Continuously poll for stale search work in a background thread |
| `--config PATH` | `./config.yaml` | Config file location |

**Example:**
```bash
poetry run matlock serve
poetry run matlock serve --debounce 10
poetry run matlock serve --force-sync --force-rollup --force-report
poetry run matlock serve --skip-rollup
poetry run matlock serve --index-search --index-search-continuous
```

**Notes:**
- `--discover-projects`, `--force-sync`, `--force-rollup`, and `--force-report` are **startup-only** — they run once before the watcher starts.
- `--index-search` is also a startup-only action.
- When combined, startup actions run in deterministic order: forced sync+parse, then forced rollup for today, then forced report, then one startup search indexing pass.
- `--index-search-continuous` starts a long-lived background poller after the watcher/debouncer/scheduler threads are up.
- `--skip-rollup` affects only the nightly scheduler job; the watcher and debouncer paths are unaffected.
- Run as a background process or managed via `launchd` / `systemd` for continuous operation.
- The server logs to stdout by default. Redirect to a file for daemon use: `matlock serve >> matlock.log 2>&1 &`
- Sending `SIGINT` (Ctrl+C) triggers a clean shutdown.

---

## Entrypoint Registration

The `matlock` command is registered in `pyproject.toml`:

```toml
[project.scripts]
matlock = "matlock.cli:app"
```

Install with `poetry install` to make the command available in the Poetry virtual environment.
