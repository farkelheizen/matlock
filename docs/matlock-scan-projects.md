# Matlock scan-projects Command

**Version:** 0.2.0

The `scan-projects` command is a **discovery tool** that walks your Markdown vault, reverse-engineers a project/super-project hierarchy from frontmatter metadata, and presents the results in one of three modes.

Unlike the pipeline stages (`sync`, `parse`, `map-projects`, etc.), `scan-projects` does **not** use the SQLite database. It reads vault files directly and either prints its findings or writes them back to `config.yaml`.

---

## Purpose

When bootstrapping Matlock against an existing vault, or auditing drift between your vault and your config, `scan-projects` saves you from manually curating `config.yaml` by hand.

---

## Usage

```
matlock scan-projects [OPTIONS]
```

| Option | Short | Default | Description |
|:-------|:------|:--------|:------------|
| `--print-yaml` | `-p` | *(default when no flag given)* | Print the scanned hierarchy as YAML |
| `--diff` | `-d` | — | Show what would change versus the current config |
| `--merge` | `-m` | — | Apply changes into `config.yaml` (creates a backup first) |
| `--config PATH` | `-c` | `./config.yaml` | Config file location |

Only **one** mode flag may be given at a time. If none is given, `--print-yaml` is assumed.

---

## Output Modes

### `--print-yaml` (default)

Prints a YAML block to stdout with two top-level keys: `super_projects` and `projects`. This output can be inspected, edited, and pasted into `config.yaml` manually.

```yaml
super_projects:
  - id: Personal Admin
    title: ''
  - id: Work
    title: ''
projects:
  - id: Taxes 2026
    title: Taxes 2026
    super_project_id: Personal Admin
    home_file: Projects/Taxes 2026/Taxes 2026.md
    priority: High
    status: In Progress
    start_date: '2026-01-01'
    due_date: '2026-04-30'
    resources:
      - type: DIRECTORY
        path: Projects/Taxes 2026
```

None-valued fields are omitted from the output.

### `--diff`

Compares the scanned results against the **current** `config.yaml` and prints a human-readable diff. No changes are written.

```
=== Super-projects ===
Added:
  + Work

=== Projects ===
Added:
  + New Initiative
Removed:
  - OldProject
Changed:
  ~ Taxes 2026:
      title: Old Title → Taxes 2026
Unchanged:
  = Personal Admin Tasks
```

### `--merge`

Applies the scanned results directly to `config.yaml`. The vault is treated as the **source of truth**:
- Projects and super-projects found in the scan are added or updated.
- Projects and super-projects **absent** from the scan are **deleted** from `config.yaml`.
- All other config keys (`base_directory`, `db_path`, `task_attributes`, etc.) are preserved.

A timestamped backup is created in the same directory as `config.yaml` before any changes are written:

```
config.yaml.2026-04-30T14-22-05.bak
```

After completion, a summary is printed:

```
Merge complete: 1 super_project(s) added, 0 updated, 0 deleted; 3 project(s) added, 1 updated, 0 deleted. Backup: /path/to/config.yaml.2026-04-30T14-22-05.bak.
```

A warning is also printed to stderr before the merge executes.

---

## Scanning Logic

### Vault Walk

The scanner uses the same directory exclusion rules as `matlock sync`:
- Skips `output_directory` (the `_Matlock/` folder).
- Skips all directories listed in `ignore_dirs`.
- Descends alphabetically.

Files are processed in case-folded alphabetical order by their relative path.

Files with **no YAML frontmatter** (or an empty frontmatter block) are silently skipped.

Files with malformed frontmatter/read issues are reported as per-file warnings and do not abort the scan.
The scanner continues processing remaining files and returns warning entries bound to each failing `file_path`.

Warning prefixes are stable and category-specific:
- `Failed to parse frontmatter YAML: ...`
- `Failed to read file as UTF-8 text: ...`
- `Failed to read file: ...`
- `Failed to scan file: ...`

### Frontmatter Key Handling

All frontmatter keys are normalised to lowercase before reading. If duplicate case-variant keys exist (e.g., `projects` and `Projects`), their values are **combined** rather than one being dropped.

PyYAML parses bare `YYYY-MM-DD` values as `datetime.date` objects; the scanner handles these transparently, coercing them to ISO 8601 strings.

### Project ID Inference

A file is identified as a project candidate via three mechanisms (in priority order):

| Mechanism | Rule | Example |
|:----------|:-----|:--------|
| **Directory pattern** | Relative path matches `Projects/<Name>/<Name>.md` (or `projects/`) | `Projects/Taxes 2026/Taxes 2026.md` |
| **Named-file pattern** | Filename ends with exactly `, Project.md` or `, Master Project.md` (case-sensitive) | `Taxes 2026, Project.md` |
| **Tag-based** | Frontmatter `tag: project` (or `project`) or `tags` list contains `project` | Any file tagged `project` |

The project ID is inferred as:
- Directory match → parent directory name
- Named-file match → stem before the suffix
- Tag match → file stem (without `.md`)

### `start_date` Fallback

`start_date` is **always populated**. If no valid `start_date`, `started`, or aliased date field is found in frontmatter, the file's creation date (`st_birthtime` on macOS, `st_ctime` fallback) is used.

### Resource Discovery

Resources are collected from both `resources` and `related` frontmatter keys. Entries are resolved against the vault directory:
- If the path points to a file → `type: FILE`
- If the path points to a directory → `type: DIRECTORY`
- If the path does not exist → skipped, and a warning is recorded

Duplicate entries (same path) are deduplicated.

### `projects` Frontmatter Key

Any page (project page or otherwise) may contain a `projects` frontmatter list. When present, that page is added as a `FILE` resource to each project listed.

### Super-project Inference

Super-projects are inferred solely from the `super_project_id` field on project candidate pages. No separate scan mechanism exists for super-projects.

---

## Data Models

### `ScannedFile`

Represents a single vault file after frontmatter parsing.

| Field | Type | Description |
|:------|:-----|:------------|
| `file_path` | `str` | Vault-relative path |
| `tagged_project` | `bool` | True if `tag`/`tags` contains `project` |
| `named_file_project` | `str \| None` | Project name inferred from filename suffix |
| `projects_directory_project` | `str \| None` | Project name inferred from directory pattern |
| `project_directory` | `ResourceConfig \| None` | Directory resource for this project (set on first encounter) |
| `project_id` | `str \| None` | Best inferred project ID |
| `super_project_id` | `str \| None` | From frontmatter `super_project_id` |
| `title` | `str \| None` | From frontmatter `title` |
| `priority` | `str \| None` | Normalised to `Low \| Medium \| High` |
| `start_date` | `str \| None` | ISO 8601; always populated with file creation fallback |
| `due_date` | `str \| None` | ISO 8601 |
| `status` | `str \ None` | Normalised to one of `Planned \| In Progress \| On Hold \| Complete \| Cancelled` (case-insensitive match; e.g., `in progress` → `In Progress`) |
| `resources` | `list[ResourceConfig]` | Collected from `resources` + `related` frontmatter |
| `warnings` | `list[str]` | Validation warnings (invalid priority, date, etc.) |
| `projects` | `list[str]` | Contents of the `projects` frontmatter key |

### `ProjectCandidate`

Aggregates all `ScannedFile` instances that share the same `project_id`.

| Field | Type | Description |
|:------|:-----|:------------|
| `project_id` | `str` | The inferred project ID |
| `candidates` | `list[ScannedFile]` | All files contributing to this project |
| `resources` | `list[ResourceConfig]` | Merged resources (directory + file references) |

**Multi-candidate field resolution:** When building output for a project, metadata fields (`super_project_id`, `priority`, `status`, `title`, `start_date`, `due_date`) are resolved by scanning **all** candidates in order and using the first non-`None` value found. This means a project's `super_project_id` or status can come from any file in the project, not just the primary home file.

---

## Warnings

Scan warnings are printed to **stderr** after the main output in all three modes. Common warning scenarios:

- Invalid `priority` value (not `low`, `medium`, or `high`)
- Invalid `status` value (not one of the five valid statuses, even after case-folding)
- Invalid date string (not matching `YYYY-MM-DD`)
- Frontmatter `resources`/`related` entry pointing to a non-existent path

---

## Examples

```bash
# Preview discovered projects as YAML
poetry run matlock scan-projects

# See what would change against current config
poetry run matlock scan-projects --diff

# Apply to config.yaml (creates backup first)
poetry run matlock scan-projects --merge

# Use a non-default config file
poetry run matlock --config ~/vaults/work/config.yaml scan-projects --diff
```
