# Matlock CLI Reference

**Version:** 0.2.x

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
| `--force` | False | Regenerate all reports and delete any generated files that are no longer valid |
| `--config PATH` | `./config.yaml` | Config file location |

**Example:**
```bash
poetry run matlock report
poetry run matlock report --target projects
poetry run matlock report --project-id backend_api
poetry run matlock report --force
```

**Note:** `--target projects` regenerates both project pages and super-project pages. There is no separate `super-projects` target in the current CLI.

---

### `matlock run-all`

Run all pipeline stages in sequence: `[scan-projects →]` `sync` → `parse` → `map-projects` → `rollup` → `report`.

```
matlock run-all [OPTIONS]
```

| Option | Default | Description |
|:-------|:--------|:------------|
| `--scan-projects` | False | Run `scan-projects --merge` before `sync` (opt-in) |
| `--skip-rollup` | False | Skip Stage IV (for mid-day runs; rollup is designed for nightly use) |
| `--force-sync` | False | Pass `--force` to the `sync` stage |
| `--force-report` | False | Pass `--force` to the `report` stage (regenerate all, delete stale files) |
| `--config PATH` | `./config.yaml` | Config file location |

**Example:**
```bash
poetry run matlock run-all
poetry run matlock run-all --scan-projects
poetry run matlock run-all --skip-rollup
poetry run matlock run-all --force-sync
poetry run matlock run-all --force-report
```

When `--scan-projects` is enabled, Matlock runs vault scan + config merge first, then reloads config and continues the normal pipeline.

---

## Discovery Commands

### `matlock scan-projects`

Walk the vault and reverse-engineer the project/super-project hierarchy from frontmatter metadata. Does **not** require an initialised database.

```
matlock scan-projects [OPTIONS]
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
poetry run matlock scan-projects
poetry run matlock scan-projects --diff
poetry run matlock scan-projects --merge
```

See `docs/matlock-scan-projects.md` for the full specification including scanning logic, data models, and output format details.

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
| `--scan-projects` | `False` | Run `scan-projects --merge` once at startup before the watcher starts |
| `--force-sync` | `False` | Run a full forced sync+parse once at startup before the watcher starts |
| `--force-rollup` | `False` | Run rollup for today once at startup (after any forced sync) |
| `--force-report` | `False` | Run a full forced report once at startup (after any startup sync) |
| `--skip-rollup` | `False` | Skip rollup in the nightly scheduled job |
| `--config PATH` | `./config.yaml` | Config file location |

**Example:**
```bash
poetry run matlock server
poetry run matlock server --debounce 10
poetry run matlock server --force-sync --force-rollup --force-report
poetry run matlock server --skip-rollup
```

**Notes:**
- `--scan-projects`, `--force-sync`, `--force-rollup`, and `--force-report` are **startup-only** — they run once before the watcher starts.
- When combined, startup actions run in deterministic order: forced sync+parse, then forced rollup for today, then forced report.
- `--skip-rollup` affects only the nightly scheduler job; the watcher and debouncer paths are unaffected.
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
