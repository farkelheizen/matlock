# Matlock Configuration

**Version:** 0.3.x

All Matlock configuration lives in a single `config.yaml` file — one per "second brain" vault. It combines application-level settings (paths, DB connection, project structure) with task-attribute parsing rules.

---

## File Location

By convention, `config.yaml` lives in the same directory where you run `matlock` CLI commands. Pass `--config PATH` to any command to specify an alternative location.

---

## Full Schema

```yaml
# ────────────────────────────────────────────────────
# Core paths
# ────────────────────────────────────────────────────

# Absolute path to your Markdown vault (second brain root).
base_directory: "/Users/me/SecondBrain"

# Path to the SQLite database file.
# Must be an absolute path. The database is NOT resolved relative to
# base_directory, so it can live anywhere on your filesystem (e.g. outside
# the vault).
db_path: "/Users/me/Matlock/matlock.db"

# Output directory for generated dashboards.
# Relative paths are resolved relative to base_directory.
# This directory is NEVER read as input by the sync stage.
output_directory: "_Matlock"

# ────────────────────────────────────────────────────
# Sync behaviour
# ────────────────────────────────────────────────────

# Directories to skip during sync (relative to base_directory).
# output_directory is always skipped; list additional dirs here.
ignore_dirs:
  - ".git"
  - ".obsidian"

# ────────────────────────────────────────────────────
# Server mode
# ────────────────────────────────────────────────────

# Seconds of file-change inactivity before triggering report.
debounce_seconds: 5

# Number of recently changed tracked files shown on _Matlock/Home.md.
dashboard_recent_changes_limit: 10

# ────────────────────────────────────────────────────
# Logging
# ────────────────────────────────────────────────────

# Path to the rotating log file.
# Must be an absolute path. The log file is NOT resolved relative to
# base_directory, so it can live outside the vault.
# Omit (or set to null) to disable file logging (warnings still appear on stderr).
log_path: "/Users/me/Matlock/matlock.log"

# Maximum size of a single log file before rotation, in bytes. Default: 10 MB.
log_max_bytes: 10000000

# Number of rotated backup files to keep (e.g. matlock.log.1, .log.2, …). Default: 3.
log_backup_count: 3

# ────────────────────────────────────────────────────
# Parser: text limits
# ────────────────────────────────────────────────────

headers:
  header_text_maxlen: 200   # Characters before truncation (adds "...")

tasks:
  task_text_maxlen: 500     # Characters before truncation (sets overflow=True)

# ────────────────────────────────────────────────────
# Search
# ────────────────────────────────────────────────────

search:
  indexing:
    enabled: false
    batch_size: 100

  chunking:
    strategy: fixed_token      # fixed_token | markdown_header
    chunk_size: 500
    chunk_overlap: 50
    inject_frontmatter: true
    frontmatter_template: "[Project: {db.project_id} | File: {sys.file_name} | Status: {fm.status}]\n\n"

  embedding:
    provider: fastembed        # fastembed | openai-compatible
    model_name: all-MiniLM-L6-v2
    dimensions: 384
    api_base_url: null
    api_base_url_env_var: null
    api_key_env_var: OPENAI_API_KEY

# ────────────────────────────────────────────────────
# Parser: task attributes
# ────────────────────────────────────────────────────

# Attribute definitions consumed by the task extraction engine.
# Each key is the canonical attribute name stored in the task table.
#
# Supported types:
#   date    — expects YYYY-MM-DD; stored as string
#   time    — expects a human duration (e.g. "30m", "1h30m"); stored as seconds (int)
#   domain  — expects one of a defined set of string values
#
# alias: a short alias (typically an emoji) that can appear in task text
#        instead of the full {key: value} syntax.
#
# For domain types, each value can also have an alias (a literal shorthand
# that implies the value — no explicit value needed in task text).

task_attributes:
  due_date:
    type: date
    alias: "📅"

  created_date:
    type: date
    alias: "➕"

  est_comp_date:
    type: date
    alias: "🏁"

  act_comp_date:
    type: date
    alias: "✅"

  estimate:
    type: time
    alias: "⏱"

  priority:
    type: domain
    values:
      high:
        alias: "🔴"
      medium:
        alias: "🟡"
      low:
        alias: "🟢"

# ────────────────────────────────────────────────────
# Project hierarchy
# ────────────────────────────────────────────────────

super_projects:
  - id: "website_overhaul"
    title: "Website Overhaul 2026"
    priority: "High"

projects:
  - id: "backend_api"
    super_project_id: "website_overhaul"   # optional; omit for standalone projects
    title: "Backend API"
    home_file: "Projects/Backend_Notes.md" # relative to base_directory
    priority: "High"
    status: "In Progress"                  # optional; Planned | In Progress | Complete | On Hold | Cancelled
    start_date: "2026-01-01"               # YYYY-MM-DD (optional)
    due_date: "2026-06-01"                 # YYYY-MM-DD (optional)
    resources:
      # Files and directories whose tasks are attributed to this project.
      # Evaluated by the map-projects stage.
      - type: "DIRECTORY"
        path: "Tech/Backend/"
      - type: "FILE"
        path: "Projects/Backend_Notes.md"
```

### Resource Examples

Use `resources` to associate files with a project during the `map-projects` stage.

Example: whole directory

```yaml
projects:
  - id: "backend_api"
    title: "Backend API"
    resources:
      - type: "DIRECTORY"
        path: "Tech/Backend"
```

This matches files such as:

- `Tech/Backend/API.md`
- `Tech/Backend/Notes/TODO.md`

It does not match:

- `Tech/BackendExtra/API.md`

Example: single file

```yaml
projects:
  - id: "weekly_planning"
    title: "Weekly Planning"
    resources:
      - type: "FILE"
        path: "Projects/Planning.md"
```

This matches only `Projects/Planning.md`.

---

## Field Reference

### Core Paths

| Field | Type | Required | Description |
|:------|:-----|:---------|:------------|
| `base_directory` | string | Yes | Absolute path to the vault root |
| `db_path` | string | Yes | SQLite file path — **absolute path required**; not resolved relative to `base_directory` |
| `output_directory` | string | Yes | Report output directory (relative to `base_directory` or absolute) |

### Sync Behaviour

| Field | Type | Default | Description |
|:------|:-----|:--------|:------------|
| `ignore_dirs` | list[string] | `[]` | Additional directories to skip during sync |

### Server Mode

| Field | Type | Default | Description |
|:------|:-----|:--------|:------------|
| `debounce_seconds` | int | `5` | Idle window before triggering report after file changes |
| `dashboard_recent_changes_limit` | int | `10` | Max number of recently changed tracked files shown in `_Matlock/Home.md` |

### Logging

| Field | Type | Default | Description |
|:------|:-----|:--------|:------------|
| `log_path` | string \| null | `null` | **Absolute path** to the rotating log file; not resolved relative to `base_directory`. Omit or set to `null` to disable file logging. |
| `log_max_bytes` | int | `10000000` | Maximum size of a single log file before rotation (bytes). |
| `log_backup_count` | int | `3` | Number of rotated backup files to keep (e.g. `matlock.log.1`, `.log.2`, …). |

### Search Indexing

| Field | Type | Default | Description |
|:------|:-----|:--------|:------------|
| `search.indexing.enabled` | bool | `false` | Search feature toggle stored in config. Search work remains opt-in at runtime via `matlock search ...`, `run-all --index-search`, or server search flags. |
| `search.indexing.batch_size` | int | `100` | Commit cadence for search indexing writes. Must be `>= 1`. |

### Search Chunking

| Field | Type | Default | Description |
|:------|:-----|:--------|:------------|
| `search.chunking.strategy` | string | `fixed_token` | Chunking mode. Current implementation uses `fixed_token`; `markdown_header` is validated config for forward compatibility. |
| `search.chunking.chunk_size` | int | `500` | Target chunk size in approximate tokens. Must be `>= 1`. |
| `search.chunking.chunk_overlap` | int | `50` | Overlap between adjacent chunks. Must be `>= 0` and smaller than `chunk_size`. |
| `search.chunking.inject_frontmatter` | bool | `true` | Prefix each chunk with rendered context derived from frontmatter, system fields, and DB project metadata. |
| `search.chunking.frontmatter_template` | string | Built-in template | Template used when `inject_frontmatter` is enabled. Missing namespaced values render as empty strings. |

Available template namespaces:

- `fm.*` for parsed frontmatter fields.
- `sys.*` for runtime file context such as `absolute_path`, `file_name`, `file_path`, `created`, and `modified`.
- `db.*` for mapped project context such as `project_id` and `super_project_id`.

### Search Embedding

| Field | Type | Default | Description |
|:------|:-----|:--------|:------------|
| `search.embedding.provider` | string | `fastembed` | Embedding backend: `fastembed` or `openai-compatible`. |
| `search.embedding.model_name` | string | `all-MiniLM-L6-v2` | Embedding model name used for indexing and vector queries. |
| `search.embedding.dimensions` | int | `384` | Expected vector dimension. Must match provider output. |
| `search.embedding.api_base_url` | string \| null | `null` | Base URL for `openai-compatible` providers. |
| `search.embedding.api_base_url_env_var` | string \| null | `null` | Optional env var that overrides `api_base_url` when the env var is set. |
| `search.embedding.api_key_env_var` | string \| null | `OPENAI_API_KEY` | Optional env var used to resolve a non-serialized API key. |

Environment override behavior is opt-in and limited to explicitly configured keys. If `api_base_url_env_var` or `api_key_env_var` is present and the environment variable is set, the loaded config uses the environment value.

### Parser Limits

| Field | Type | Default | Description |
|:------|:-----|:--------|:------------|
| `headers.header_text_maxlen` | int | `200` | Max heading characters (excess truncated with `...`) |
| `tasks.task_text_maxlen` | int | `500` | Max task text characters; sets `overflow = True` if hit |

### Task Attribute Definition

| Sub-field | Required | Description |
|:----------|:---------|:------------|
| `type` | Yes | `date`, `time`, or `domain` |
| `alias` | No | Emoji or short string shortcut in task text |
| `values` (domain only) | Yes | Map of allowed value names → optional `alias` |

### Project Resources

| `type` value | Match rule |
|:-------------|:-----------|
| `DIRECTORY` | `file.file_path` starts with `path` |
| `FILE` | `file.file_path` equals `path` |

A file can match resources from multiple projects (many-to-many).

Important details:

- Resource paths are relative to `base_directory`.
- `DIRECTORY` paths are normalized internally so both `Tech/Backend` and `Tech/Backend/` behave the same.
- `DIRECTORY` matching is directory-aware, not a raw substring prefix: `Tech/Backend` does not match `Tech/BackendExtra`.
- Generated files and deleted files are excluded from `file_project` associations.

---

## Validation Rules

- `base_directory` must exist and be readable at startup.
- `output_directory` is resolved relative to `base_directory` when given as a relative path.
- `db_path` is not resolved relative to `base_directory`; use an absolute path if you want the database outside the vault. The parent directory must already exist.
- `log_path`, when set, is not resolved relative to `base_directory`; relative values remain relative. Search query logging writes to a sibling `search.log` file next to `log_path`, or `~/.matlock/logs/search.log` when `log_path` is unset.
- All `home_file` and resource `path` values are relative to `base_directory`.
- `super_project_id` on a project must reference a defined `super_project.id` or be omitted.
- Duplicate `id` values within `super_projects` or `projects` are a fatal config error.
- `search.chunking.chunk_overlap` must be smaller than `search.chunking.chunk_size`.

For per-file search overrides, see `docs/matlock-search.md`.
