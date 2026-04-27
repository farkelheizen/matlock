# Matlock

Pipeline-driven task extractor and report generator for Markdown second brains.

Matlock walks a Obsidian/Markdown vault, extracts checkbox tasks, maps files to projects, calculates daily metrics, and renders Jinja2 Markdown dashboards — all orchestrated from a single CLI.

## Requirements

- Python 3.11+
- Poetry 2.x

## Install

```bash
poetry install
```

This registers the `matlock` command in the Poetry virtual environment.

## Quick Start

```bash
# Run the full pipeline (sync → parse → map-projects → rollup → report)
poetry run matlock run-all

# Or run each stage individually
poetry run matlock sync
poetry run matlock parse
poetry run matlock map-projects
poetry run matlock rollup
poetry run matlock report
```

## Configuration

Matlock is configured via a `config.yaml` file (default: `./config.yaml`). Pass a custom path with `--config PATH` on any command.

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

task_attributes:
  due_date:
    type: date
    alias: "📅"
  complete_date:
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
  actual:
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

Project `resources` control which files are associated with a project during `map-projects`:

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

## CLI Reference

All commands accept `--config PATH` to override the default config location.

### `matlock run-all`

Run all five pipeline stages in sequence.

```
matlock run-all [--skip-rollup] [--force-sync] [--config PATH]
```

| Flag | Default | Description |
|------|---------|-------------|
| `--skip-rollup` | False | Skip Stage IV rollup (useful for mid-day runs) |
| `--force-sync` | False | Re-hash every file regardless of stored hash |

```bash
poetry run matlock run-all
poetry run matlock run-all --skip-rollup
poetry run matlock run-all --force-sync
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
    stages/
        sync.py
        parse.py
        map_projects.py
        rollup.py
        report.py
    templates/          ← Jinja2 Markdown templates (.md.j2)
docs/
    matlock-cli.md
    matlock-pipeline-specification.md
    roadmap/
        index.md
tests/
pyproject.toml
config.yaml             ← your local config (not committed)
```

## Development

```bash
# Run all tests
poetry run pytest

# Run a specific test file
poetry run pytest tests/test_cli_run_all.py -v
```

403 tests, 0 failures as of Phase 8.
