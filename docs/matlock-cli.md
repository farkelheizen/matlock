# Matlock CLI Reference

**Version:** 0.1.x

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

### `matlock parse`

**Stage II.** Extract tasks from all files where `needs_parsing = 1` and write results to the `task` table.

```
matlock parse [OPTIONS]
```

| Option | Default | Description |
|:-------|:--------|:------------|
| `--config PATH` | `./config.yaml` | Config file location |

**Example:**
```bash
poetry run matlock parse
```

---

### `matlock map-projects`

**Stage III.** Rebuild the `project`, `super_project`, and `file_project` tables from `config.yaml`. Always a full rebuild.

```
matlock map-projects [OPTIONS]
```

| Option | Default | Description |
|:-------|:--------|:------------|
| `--config PATH` | `./config.yaml` | Config file location |

**Example:**
```bash
poetry run matlock map-projects
```

---

### `matlock rollup`

**Stage IV.** Calculate and persist daily metrics for the previous day (or a specified date) into the `daily_metric` table. Designed for nightly use; idempotent.

```
matlock rollup [OPTIONS]
```

| Option | Default | Description |
|:-------|:--------|:------------|
| `--date YYYY-MM-DD` | Yesterday | Calculate metrics for this specific date |
| `--config PATH` | `./config.yaml` | Config file location |

**Example:**
```bash
poetry run matlock rollup
poetry run matlock rollup --date 2026-04-20
```

---

### `matlock report`

**Stage V.** Render all (or a subset of) Jinja2 Markdown dashboards and write them to `output_directory`.

```
matlock report [OPTIONS]
```

| Option | Default | Description |
|:-------|:--------|:------------|
| `--target {all,dashboard,projects,history}` | `all` | Limit which report types to generate |
| `--project-id ID` | None | Regenerate a single project page by its config ID |
| `--config PATH` | `./config.yaml` | Config file location |

**Example:**
```bash
poetry run matlock report
poetry run matlock report --target projects
poetry run matlock report --project-id backend_api
```

**Note:** `--target projects` regenerates both project pages and super-project pages. There is no separate `super-projects` target in the current CLI.

---

### `matlock run-all`

Run all five pipeline stages in sequence: `sync` → `parse` → `map-projects` → `rollup` → `report`.

```
matlock run-all [OPTIONS]
```

| Option | Default | Description |
|:-------|:--------|:------------|
| `--skip-rollup` | False | Skip Stage IV (for mid-day runs; rollup is designed for nightly use) |
| `--force-sync` | False | Pass `--force` to the `sync` stage |
| `--config PATH` | `./config.yaml` | Config file location |

**Example:**
```bash
poetry run matlock run-all
poetry run matlock run-all --skip-rollup
poetry run matlock run-all --force-sync
```

---

## Server Command

### `matlock server`

Run a persistent daemon that monitors the vault in real time and orchestrates the pipeline automatically.

```
matlock server [OPTIONS]
```

Three integrated triggers:

1. **File Watcher** (`watchdog`) — File create/modify/delete triggers `sync` + `parse` for the affected file, then marks the owning project(s) dirty.
2. **Debouncer** — After `debounce_seconds` of idle time, triggers `report` for dirty projects.
3. **Scheduler** — At midnight (00:01): triggers `rollup` followed by a full `report` rebuild.

| Option | Default | Description |
|:-------|:--------|:------------|
| `--debounce SECONDS` | From config (`5`) | Idle window before triggering `report` after file changes |
| `--config PATH` | `./config.yaml` | Config file location |

**Example:**
```bash
poetry run matlock server
poetry run matlock server --debounce 10
```

**Notes:**
- Run as a background process or managed via `launchd` / `systemd` for continuous operation.
- The server logs to stdout by default. Redirect to a file for daemon use: `matlock server >> matlock.log 2>&1 &`
- Sending `SIGINT` (Ctrl+C) triggers a clean shutdown.

---

## Entrypoint Registration

The `matlock` command is registered in `pyproject.toml`:

```toml
[project.scripts]
matlock = "matlock.cli:app"
```

Install with `poetry install` to make the command available in the Poetry virtual environment.
