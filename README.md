# Matlock

Pipeline-driven task extractor, report generator, and local search engine for Markdown second brains.

Matlock walks an Obsidian/Markdown vault, extracts checkbox tasks, maps files to projects, calculates daily metrics, renders Jinja2 Markdown dashboards, and can build a local search index for human and machine query flows.

## Requirements

- Python 3.11+
- Poetry 2.x

## Install

```bash
poetry install
```

This registers the `matlock` command in the Poetry virtual environment.

See [SECURITY.md](SECURITY.md) for recommended separation between the public code repository and private vault data.

## Quick Start

```bash
# Run the full pipeline (sync → extract → projects map → metrics rollup → reports render)
poetry run matlock pipeline run

# Run full pipeline and do an opt-in project scan/merge first
poetry run matlock pipeline run --discover-projects

# Run full pipeline, then refresh the local search index
poetry run matlock pipeline run --index-search

# Or run each stage individually
poetry run matlock sync
poetry run matlock extract
poetry run matlock secrets backfill
poetry run matlock document read Notes/today.md
poetry run matlock projects map
poetry run matlock metrics rollup
poetry run matlock reports render

# Query project and task records directly from SQLite-backed metadata
poetry run matlock projects query
poetry run matlock projects query "alpha"
poetry run matlock super-projects list
poetry run matlock tasks query --checked --task-text "review"

# Or build/query the local search index directly
poetry run matlock search index
poetry run matlock search query "database"
printf '{"query": "database", "search_mode": "fts_only"}' | poetry run matlock search query --stdio

# Or run the server daemon (watch vault continuously)
poetry run matlock serve
poetry run matlock serve --index-search --index-search-continuous
```

## Configuration

Matlock is configured via a `config.yaml` file (default: `./config.yaml`). Pass a custom path with `--config PATH` (or `-c PATH`) on any command.

```yaml
base_directory: /path/to/vault
db_path: matlock.db
output_directory: /path/to/vault/_Matlock

ignore_dirs:
  - .obsidian
  - _Matlock

debounce_seconds: 5

headers:
  header_text_maxlen: 200

tasks:
  task_text_maxlen: 500

cache:
  redacted_dir: ~/.matlock/cache/redacted

task_attributes:
  due_date:
    type: date
    alias: "📅"
  act_comp_date:
    type: date
    alias: "✅"
  priority:
    type: domain
    values:
      low:
        alias: "🔽"
      medium:
        alias: "🔼"
      high:
        alias: "⏫"
  estimate:
    type: time

super_projects:
  - id: work
    title: Work
    priority: high

projects:
  - id: backend_api
    title: Backend API
    super_project_id: work
    home_file: Projects/Backend API.md
    resources:
      - type: DIRECTORY
        path: Tech/Backend
      - type: FILE
        path: Projects/Backend API.md
```

Secret safety behavior:

- Parse records document-level secret detection state.
- `secrets backfill` scans existing unknown-state tracked documents and can retry prior scanner-error rows.
- Search indexing/query and the `document read` command redact unsafe content.
- Legacy documents (unknown secret state) are scanned lazily and then persisted.
- Redaction cache files are stored by hash under `cache.redacted_dir/<sha256[:2]>/<sha256[2:4]>/<sha256>.txt`, with backward-compatible reads for older flat cache files.

Project `resources` control which files are associated with a project during `projects map`:

- `type: DIRECTORY` matches every non-generated, non-deleted file under that directory.
- `type: FILE` matches one exact relative path.

Examples:

```yaml
projects:
  - id: backend_api
    title: Backend API
    resources:
      - type: DIRECTORY
        path: Tech/Backend

  - id: planning
    title: Planning
    resources:
      - type: FILE
        path: Projects/Planning.md
```

Notes:

- Resource paths are relative to `base_directory`.
- `DIRECTORY` matching is path-based, so `Tech/Backend` matches `Tech/Backend/api.md` but not `Tech/BackendExtra/api.md`.
- A file may belong to multiple projects if it matches multiple resource rules.

## Search

Matlock Search is an optional local-first subsystem layered on top of the core pipeline.

- `matlock search index` chunks active vault files, injects optional frontmatter context, and stores FTS plus embedding-backed search records in SQLite.
- `matlock search query` supports human CLI output and strict `--stdio` JSON transport for editor and agent integrations.
- `matlock run-all --index-search` runs indexing after the core pipeline.
- `matlock server --index-search --index-search-continuous` can do one startup indexing pass and continue polling for stale search work in the background.

See [docs/matlock-search.md](docs/matlock-search.md) for the full search guide.

## CLI Reference

All commands accept `--config PATH` to override the default config location.

### `matlock run-all`

Run all pipeline stages in sequence, with optional `scan-projects` pre-stage.

```
matlock run-all [--scan-projects] [--skip-rollup] [--force-sync] [--force-report] [--config PATH]
```

| Flag | Default | Description |
|------|---------|-------------|
| `--scan-projects` | False | Run `scan-projects --merge` before `sync` (opt-in) |
| `--skip-rollup` | False | Skip Stage IV rollup (useful for mid-day runs) |
| `--force-sync` | False | Re-hash every file regardless of stored hash |
| `--force-report` | False | Regenerate all reports and delete stale generated files |
| `--index-search` | False | Run search indexing after `report` completes (opt-in) |

```bash
poetry run matlock run-all
poetry run matlock run-all --scan-projects
poetry run matlock run-all --skip-rollup
poetry run matlock run-all --force-sync
poetry run matlock run-all --force-report
poetry run matlock run-all --index-search
```

---

### `matlock sync`

**Stage I.** Walk the vault, hash all Markdown files, update the `file` table, and flag changed files for re-parsing.

```bash
poetry run matlock sync
poetry run matlock sync --force
```

### `matlock parse`

**Stage II.** Extract tasks from all files flagged `needs_parsing = 1`.

```bash
poetry run matlock parse
```

### `matlock detect-backfill`

Scan tracked files to populate or retry persisted secret-detection state.

```bash
poetry run matlock detect-backfill
poetry run matlock detect-backfill --retry-errors
```

Behavior:

- Default mode scans only active, non-generated tracked files with `has_secrets IS NULL`.
- `--retry-errors` additionally rescans rows where `secret_detection_error IS NOT NULL`.
- Missing/unreadable files are reported as skipped and retain existing state.
- The command does not re-parse tasks, generate redaction cache files, or reindex search.

### `matlock list-projects`

Return project records as a JSON list. Without a term it returns all projects; with a term it performs a case-insensitive literal substring match across all project columns and automatically unions in projects associated with matching indexed files. Field filters can be combined with AND semantics using flags such as `--project-id`, `--title`, `--home-file`, `--priority`, `--status`, and ISO date predicates.

```bash
poetry run matlock list-projects
poetry run matlock list-projects "alpha"
poetry run matlock list-projects "alpha" --search-mode hybrid
poetry run matlock list-projects --project-id backend --status active
```

### `matlock list-super-projects`

Return every super-project row as a JSON list.

```bash
poetry run matlock list-super-projects
```

### `matlock list-tasks`

Return active, non-generated task rows as JSON. Supports repeatable `--due-date`, `--est-comp-date`, and `--act-comp-date` predicates, `--checked/--unchecked`, `--task-text`, `--headers`, `--attributes`, `--project-id`, and `--super-project-id` filters.

```bash
poetry run matlock list-tasks
poetry run matlock list-tasks --checked --task-text "review"
poetry run matlock list-tasks --project-id backend --due-date ">=2026-01-01"
```

### `matlock map-projects`

**Stage III.** Rebuild the `project`, `super_project`, and `file_project` tables from `config.yaml`. Always a full rebuild.

```bash
poetry run matlock map-projects
```

### `matlock rollup`

**Stage IV.** Calculate and persist daily metrics for the previous day (or a given date). Designed for nightly use; idempotent.

```bash
poetry run matlock rollup
poetry run matlock rollup --date 2026-04-20
```

### `matlock report`

**Stage V.** Render Jinja2 Markdown dashboards into `output_directory`.

```bash
poetry run matlock report
poetry run matlock report --target projects
poetry run matlock report --project-id backend_api
```

| Flag | Default | Description |
|------|---------|-------------|
| `--target` | `all` | One of: `all`, `dashboard`, `projects`, `history` |
| `--project-id ID` | None | Regenerate a single project page |

`--target projects` regenerates both project pages and super-project pages. There is no separate `super-projects` target in the current CLI.

---

### `matlock server`

Run a persistent daemon that monitors the vault in real time and orchestrates the pipeline automatically.

```
matlock server [--debounce SECONDS] [--scan-projects] [--force-sync] [--force-report] [--skip-rollup] [--index-search] [--index-search-continuous] [--config PATH]
```

| Flag | Default | Description |
|------|---------|-------------|
| `--debounce SECONDS` | From config (`debounce_seconds`) | Idle window before triggering `report` after file changes |
| `--scan-projects` | `False` | Run `scan-projects --merge` once at startup before the watcher starts |
| `--force-sync` | `False` | Run a full forced sync+parse once at startup before the watcher starts |
| `--force-report` | `False` | Run a full forced report once at startup (after any startup sync) |
| `--skip-rollup` | `False` | Skip rollup in the nightly scheduled job |
| `--index-search` | `False` | Run search indexing once at startup before the watcher starts |
| `--index-search-continuous` | `False` | Continuously poll for stale search work in the background |

Three integrated triggers:

1. **File Watcher** — on create/modify/delete under `base_directory`: runs `sync` + `parse`, marks affected project(s) dirty.
2. **Debouncer** — after `debounce_seconds` of idle time, runs `report` for all dirty projects.
3. **Scheduler** — at 00:01 each night: runs `rollup` then a full `report` rebuild.

```bash
poetry run matlock server
poetry run matlock server --debounce 10
poetry run matlock server --force-sync --force-report
poetry run matlock server --skip-rollup
poetry run matlock server --index-search --index-search-continuous
```

Press `Ctrl+C` for a clean shutdown.

## Pipeline Overview

```
Vault (Markdown files)
        │
        ▼
   Stage I: sync          ── file table (hash, needs_parsing)
        │
        ▼
  Stage II: parse         ── task table (checkbox tasks)
        │
        ▼
 Stage III: map-projects  ── project / super_project / file_project tables
        │
        ▼
  Stage IV: rollup        ── daily_metric table
        │
        ▼
   Stage V: report        ── Markdown dashboards written to output_directory
```

## Project Layout

```text
matlock/
    cli.py              ← Typer CLI entrypoint
    config.py           ← MatlockConfig (Pydantic v2)
    db.py               ← SQLite connection, schema, CRUD helpers
    server.py           ← Server daemon (watcher, debouncer, scheduler)
    stages/
        sync.py
        parse.py
        map_projects.py
        rollup.py
        report.py
    templates/          ← Jinja2 Markdown templates (.md.j2)
docs/
    matlock-cli.md
  matlock-configuration.md
  matlock-data-model.md
  matlock-high-level-design.md
  matlock-search.md
    matlock-pipeline-specification.md
    roadmap/
        index.md
tests/
pyproject.toml
config.yaml             ← your local config (not committed)
```

## Documentation

- [docs/matlock-search.md](docs/matlock-search.md) — search indexing, query modes, STDIO transport, and server/search orchestration.
- [docs/matlock-cli.md](docs/matlock-cli.md) — full CLI reference including `search` commands.
- [docs/matlock-configuration.md](docs/matlock-configuration.md) — `config.yaml` schema, including the `search` block.
- [docs/matlock-data-model.md](docs/matlock-data-model.md) — SQLite search tables plus request/response model contracts.
- [docs/matlock-pipeline-specification.md](docs/matlock-pipeline-specification.md) — core pipeline stages and optional search indexing hooks.
- [docs/matlock-high-level-design.md](docs/matlock-high-level-design.md) — architecture overview and execution modes.

## Development

```bash
# Run all tests
poetry run pytest

# Run a specific test file
poetry run pytest tests/test_cli_run_all.py -v
```
