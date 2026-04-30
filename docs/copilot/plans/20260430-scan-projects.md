# 20260430-scan-projects Plan: `scan-projects` Command

> Date: 4/30/2026
> Owner: Copilot
> Branch: feat/scan-projects
> Related docs: `docs/matlock-cli.md`, `docs/matlock-configuration.md`, `docs/matlock-high-level-design.md`

---

## Problem Summary

Matlock's project hierarchy (projects and super_projects) is currently maintained manually in `config.yaml`. When a second-brain vault contains many Markdown files with structured frontmatter that already encodes project identity (via `tag: Project`, naming conventions like `, Project.md`, or standard directory layouts like `Projects/<name>/<name>.md`), this manual upkeep becomes error-prone and tedious.

There is no current mechanism to introspect the vault and discover what projects and super_projects exist in the actual notes. A practitioner who migrates to Matlock, or who adds many new project pages in bulk, has no assisted path to bring their config into alignment with their notes.

The `scan-projects` command addresses this gap: it walks the vault, reverse-engineers the project structure from frontmatter and naming conventions, and offers three output modes — print the discovered YAML, print a diff against the current config, or merge the discovered structure directly into the config file.

---

## Goal

Implement a new `matlock scan-projects` CLI command that scans all Markdown files in the vault, infers the project and super_project hierarchy from frontmatter and file/directory naming patterns, and outputs that information in a user-chosen mode (`--print-yaml`, `--diff`, or `--merge`).

---

## Scope

**In scope:**
- New Pydantic models: `ScannedFile`, `ProjectCandidate` in `matlock/scan_models.py`
- New stage module: `matlock/stages/scan_projects.py` — vault walker, frontmatter parser, and output logic
- New CLI command: `scan-projects` in `matlock/cli.py` with three mutually exclusive output modes
- New spec doc: `docs/matlock-scan-projects.md`
- Updates to `docs/matlock-cli.md`, `docs/matlock-high-level-design.md`, `docs/copilot/copilot-docs-reference.md`, `docs/roadmap/index.md`
- New `CHANGELOG.md` with an `Added` entry
- Version bump: `0.1.0 → 0.2.0`

**Out of scope:**
- Modifications to the five pipeline stages (sync, parse, map-projects, rollup, report)
- Changes to the SQLite schema or `db.py`
- Any interactive / prompting mode for merge conflicts
- Auto-discovery of `task_attributes` from frontmatter
- Scanning files already excluded by `ignore_dirs` or `output_directory` (same exclusions as `sync`)

---

## Constraints / Requirements

- **Read-only vault:** Matlock never modifies source Markdown files. Only `--merge` may write, and it writes only to the config YAML.
- **Alpha-order scanning:** Files must be walked and processed in strict alphabetical order, from root downward. Subdirectory entries are sorted before descending.
- **Pydantic models:** `ScannedFile` and `ProjectCandidate` must use Pydantic v2.
- **`python-frontmatter`:** Already a dependency (`python-frontmatter >=1.1.0`). Use it to parse frontmatter; do not reinvent YAML parsing.
- **`PyYAML`:** Already a dependency. Use for YAML serialization in `--print-yaml` and `--merge` output.
- **No DB dependency:** `scan-projects` reads directly from disk. It must not require an initialized database or a `sync`/`parse` run.
- **Mutually exclusive options:** Only one of `--print-yaml`, `--diff`, `--merge` may be given. If none is given, default to `--print-yaml`.
- **`--merge` is destructive:** Must write a warning to stderr before modifying the config file. No backup is created by default (see Questions).
- **`project_directory` resource** — populated only on the _first_ `ScannedFile` candidate for a given `project_id` (alpha-order first). Subsequent candidates for the same project do not add a duplicate directory resource.
- **`warnings`** — printed to stderr after the main output (all modes).
- **Valid date check:** Use a strict `YYYY-MM-DD` regex (matching `re.fullmatch`) for date field validation — do not attempt `datetime.date.fromisoformat` with values that contain extra characters.
- **`start_date` file creation fallback:** Use `st_birthtime` (macOS) if available, otherwise `st_ctime`. This mirrors `_stat_times` in `sync.py`.

---

## Implementation Plan

| Step ID | Status | Goal | Planned Changes | Test Coverage |
|---|---|---|---|---|
| SP-S1 | Completed | Define `ScannedFile` and `ProjectCandidate` Pydantic models | `matlock/scan_models.py` (new) | `tests/test_scan_models.py` (new) — model construction, field defaults, `project_id` priority logic |
| SP-S2 | Completed | Implement vault walker and frontmatter scanner | `matlock/stages/scan_projects.py` (new) — `scan_vault()`, `_scan_file()`, `_build_project_candidates()` | `tests/test_scan_projects.py` (new) — scanned file field population, project candidate aggregation |
| SP-S3 | Completed | Implement `--print-yaml` and `--diff` output formatters | `matlock/stages/scan_projects.py` — `format_as_yaml()`, `format_as_diff()` | `tests/test_scan_projects.py` — YAML output shape, diff add/change/remove sections |
| SP-S4 | Completed | Implement `--merge` config writer | `matlock/stages/scan_projects.py` — `merge_into_config()` | `tests/test_scan_projects.py` — merge adds new projects, merge preserves unrelated config keys, merge no-op when already in sync |
| SP-S5 | Not Started | Wire up `scan-projects` CLI command | `matlock/cli.py` — new `scan_projects` command with mutually exclusive option group | `tests/test_cli_scan_projects.py` (new) — each mode flag, default-to-print-yaml, no-config error path |
| SP-S6 | Not Started | Documentation, CHANGELOG, and version bump | `docs/matlock-scan-projects.md` (new), `docs/matlock-cli.md`, `docs/matlock-high-level-design.md`, `docs/copilot/copilot-docs-reference.md`, `docs/roadmap/index.md`, `CHANGELOG.md` (new), `pyproject.toml` | No additional tests — doc and version changes only |

Status values: `Not Started` | `In Progress` | `Completed` | `Blocked`

---

## Step Details

### SP-S1 — `ScannedFile` and `ProjectCandidate` Models

**Files (expected):**
- `matlock/scan_models.py` (new)
- `tests/test_scan_models.py` (new)

**Implementation notes:**

`ScannedFile` — one instance per Markdown file that has frontmatter:

| Field | Type | Notes |
|---|---|---|
| `file_path` | `str` | Relative path from `base_directory` |
| `tagged_project` | `bool` | `True` if `tag == "Project"/"project"` or `tags` list contains either value (case-insensitive) |
| `named_file_project` | `str \| None` | Leading portion when filename ends with `, Project.md` or `, Master Project.md` |
| `projects_directory_project` | `str \| None` | `project_name` from `projects/<project_name>/<project_name>.md` or `Projects/<project_name>/<project_name>.md` |
| `project_directory` | `ResourceConfig \| None` | Directory-based resource when `projects_directory_project` is set and this is the first candidate for this `project_id` |
| `project_id` | `str \| None` | Priority: `projects_directory_project` → `named_file_project` → (`tagged_project` → stem) |
| `super_project_id` | `str \| None` | From `super_project_id` frontmatter attribute (string) |
| `title` | `str \| None` | From `title` frontmatter attribute (string) |
| `priority` | `str \| None` | From `priority` frontmatter attribute; normalized to canonical case; invalid value appends warning |
| `start_date` | `str \| None` | From `started`, `start_date`, or `start date` (case-insensitive, first valid YYYY-MM-DD wins); fallback to file creation date in local timezone |
| `due_date` | `str \| None` | From `due_date`, `due date`, `est_comp`, or `est comp` (case-insensitive, first valid YYYY-MM-DD wins) |
| `status` | `str \| None` | From `status` frontmatter; must exactly match a domain value (case-sensitive); invalid value appends warning |
| `resources` | `list[ResourceConfig]` | Files/directories from frontmatter `resources` **and** `related` attributes (both string arrays); each entry validated as a relative path pointing to an existing file or directory under `base_directory` |
| `warnings` | `list[str]` | Accumulated warnings for this file |
| `projects` | `list[str]` | Distinct values from `projects` + `Projects` frontmatter attributes |

`ProjectCandidate` — one instance per discovered `project_id`:

| Field | Type | Notes |
|---|---|---|
| `project_id` | `str` | The project identifier |
| `candidates` | `list[ScannedFile]` | All scanned files that are project-definition pages (have this `project_id`) — first one is the home file |
| `resources` | `list[ResourceConfig]` | From first candidate: `project_directory` + `resources`; plus: any non-candidate page whose `projects` list contains this `project_id` gets its `file_path` appended as a `FILE` resource |

**Definition of done:**
- Both models instantiate correctly in tests.
- Field defaults (`False`, `None`, empty list) are correct.
- `project_id` priority logic is covered (all three resolution paths).
- `tagged_project` recognizes both scalar `tag` and array `tags`.

---

### SP-S2 — Vault Walker and Frontmatter Scanner

**Files (expected):**
- `matlock/stages/scan_projects.py` (new)
- `tests/test_scan_projects.py` (new)

**Implementation notes:**

- `scan_vault(config: MatlockConfig) -> tuple[list[ScannedFile], list[ProjectCandidate]]`
  - Walks `config.base_directory` using `os.walk`, applying the same exclusions as `sync.py` (`output_directory`, `ignore_dirs`).
  - Collects all `.md` file paths, sorts them **alphabetically** (case-folded, full relative path sort).
  - For each file, calls `_scan_file(base_dir, rel_path, seen_project_ids)` where `seen_project_ids` tracks which project directories have already been registered (for the "first candidate" rule on `project_directory`).
  - After all files are scanned, calls `_build_project_candidates(scanned_files)` to aggregate.
  - Returns `(scanned_files, project_candidates)`.

- `_scan_file(base_dir: Path, rel_path: str, seen_project_ids: set[str]) -> ScannedFile | None`
  - Opens the file with `python-frontmatter`. If no frontmatter, returns `None`.
  - Populates all `ScannedFile` fields per the specification.
  - `projects_directory_project` pattern matching: check if `rel_path` matches `[Pp]rojects/<name>/<name>.md`.
  - For `project_directory`: if `projects_directory_project` is set and `project_id` (derived) is NOT already in `seen_project_ids`, create the `ResourceConfig(type="DIRECTORY", path=<parent_dir>)` and add `project_id` to `seen_project_ids`.
  - Date fields: iterate candidate attribute names in priority order; for each, check value is a string matching `r'^\d{4}-\d{2}-\d{2}$'`; use first valid hit; add warning for string values that don't match the pattern.
  - `start_date` fallback: use `pathlib.Path.stat().st_birthtime` (macOS) / `st_ctime`; convert using local timezone via `datetime.fromtimestamp(...).date().isoformat()`.
  - `resources`: iterate both the `resources` and `related` frontmatter lists (union, deduped by path); for each string, check if `base_dir / entry` is an existing file (→ `FILE`) or directory (→ `DIRECTORY`); skip and warn if neither.
  - `priority` normalization: compare lower-cased value against `{"low", "medium", "high"}`; map to title-case canonical; warn on invalid.
  - `status` validation: exact match against `{"Planned", "In Progress", "On Hold", "Complete", "Cancelled"}`; warn on non-match.

- `_build_project_candidates(files: list[ScannedFile]) -> list[ProjectCandidate]`
  - First pass: collect all files with a non-None `project_id` into `candidates` list.
  - Second pass: for **every** `ScannedFile` (whether or not it is itself a project page) whose `projects` list is non-empty, append a `FILE` resource for that file to each matching `ProjectCandidate`. The `projects` attribute is a general-purpose association mechanism that links any page to any project as a file-based resource.
  - Sort `ProjectCandidate` list by `project_id` alphabetically.

**Definition of done:**
- `scan_vault` returns correct `ScannedFile` objects for all three `project_id` resolution paths.
- `tagged_project` and `named_file_project` and `projects_directory_project` each tested independently.
- `project_directory` is set only on the first candidate for each `project_id`.
- `start_date` fallback to file creation date is exercised.
- Warning accumulation tested: invalid priority, invalid status, invalid date.

---

### SP-S3 — `--print-yaml` and `--diff` Output Formatters

**Files (expected):**
- `matlock/stages/scan_projects.py` — `format_as_yaml()`, `format_as_diff()`
- `tests/test_scan_projects.py` — extended

**Implementation notes:**

- `format_as_yaml(candidates: list[ProjectCandidate], scanned: list[ScannedFile]) -> str`
  - Derive a `super_projects` list: distinct `super_project_id` values from all first-candidates, sorted alphabetically.
  - For each `ProjectCandidate`, build a `ProjectConfig`-shaped dict (id, title, super_project_id, home_file, priority, status, start_date, due_date, resources) from the first `ScannedFile` candidate. Omit None fields.
  - Serialize with `yaml.dump(..., default_flow_style=False, sort_keys=False, allow_unicode=True)`.
  - Output format: two top-level keys `super_projects:` and `projects:` — ready to paste into `config.yaml`.

- `format_as_diff(candidates: list[ProjectCandidate], scanned: list[ScannedFile], config: MatlockConfig) -> str`
  - Build a set of project IDs from `config.projects` and from `candidates`.
  - Sections: **Added** (in scan, not in config), **Changed** (in both, at least one field differs), **Unchanged** (in both, all fields match), **Removed** (in config, not in scan).
  - For **Changed** entries, list each differing field as `  field: config_value → scanned_value`.
  - Same diff for `super_projects`.
  - Output is human-readable plain text (not a unified diff).

**Definition of done:**
- `format_as_yaml` produces valid YAML that round-trips through `yaml.safe_load` back to the expected dict structure.
- `format_as_diff` correctly categorizes Added / Changed / Unchanged / Removed for both projects and super_projects.
- Empty sections are omitted from diff output.

---

### SP-S4 — `--merge` Config Writer

**Files (expected):**
- `matlock/stages/scan_projects.py` — `merge_into_config()`, `MergeResult`
- `tests/test_scan_projects.py` — extended

**Implementation notes:**

- `MergeResult` dataclass: `super_projects_added`, `super_projects_updated`, `super_projects_deleted`, `projects_added`, `projects_updated`, `projects_deleted`, `backup_path: str`.
- `merge_into_config(config_path: Path, candidates: list[ProjectCandidate], scanned: list[ScannedFile]) -> MergeResult`
  - **Backup first:** Before any write, copy `config_path` to `config_path.with_suffix(f'.{timestamp}.bak')` where `timestamp` is `datetime.now().strftime("%Y-%m-%dT%H-%M-%S")`. Record the backup path in `MergeResult.backup_path`.
  - Load the raw YAML dict from `config_path` (preserve all non-project/super_project keys).
  - **Full sync semantics (ins/upd/del):** The scan result is the source of truth.
    - **Added** — entries in scan not in config: append.
    - **Updated** — entries in both: replace all scanned fields; preserve any config-only fields that scan did not produce (e.g., `home_file` if scan found no `project_directory`).
    - **Deleted** — entries in config not in scan: remove entirely.
  - Write back with `yaml.dump(..., default_flow_style=False, sort_keys=False, allow_unicode=True)`.
  - Return `MergeResult` with counts.
- Existing entries are matched by `id`.

**Definition of done:**
- `merge_into_config` creates a `.bak` file before writing.
- Adds new projects/super_projects; updates existing ones with scanned fields; deletes entries absent from scan.
- Unrelated YAML keys (paths, logging, task_attributes, etc.) are preserved exactly.
- `MergeResult` counts (added/updated/deleted) and `backup_path` are accurate.
- Config file is still valid `MatlockConfig` after merge (round-trip test via `load_config`).

---

### SP-S5 — `scan-projects` CLI Command

**Files (expected):**
- `matlock/cli.py` — new `scan_projects` command
- `tests/test_cli_scan_projects.py` (new)

**Implementation notes:**

- Command name: `scan-projects`.
- Options (mutually exclusive — enforced with a Typer callback or manual check):
  - `--print-yaml` / `-p` — print discovered YAML (default if no mode given).
  - `--diff` / `-d` — print diff against current config.
  - `--merge` / `-m` — merge into config; print warning to stderr first.
- Warnings from scanning are printed to stderr after the main output (all modes).
- Uses `_load_and_validate` helper for config loading (same as all other commands).
- Does NOT require DB connection or `init_db`.
- Before executing `--merge`, print to stderr: `"Warning: --merge will overwrite config.yaml. Backup will be written to <path>.bak."`
- Echo format for `--merge`: `"Merge complete: X super_project(s) added, Y updated, Z deleted; A project(s) added, B updated, C deleted. Backup: <backup_path>."`.

Typer does not have a built-in mutually exclusive group. Enforce via:
```python
modes_given = sum([print_yaml, diff, merge])
if modes_given > 1:
    typer.echo("Error: only one of --print-yaml, --diff, --merge may be given", err=True)
    raise typer.Exit(code=1)
if modes_given == 0:
    print_yaml = True  # default
```

**Definition of done:**
- `matlock scan-projects` runs without DB, prints YAML by default.
- `--diff` and `--merge` modes produce correct output.
- Mutually exclusive enforcement tested.
- Config-not-found error path tested.

---

### SP-S6 — Documentation, CHANGELOG, and Version Bump

**Files (expected):**
- `docs/matlock-scan-projects.md` (new)
- `docs/matlock-cli.md` — add `scan-projects` command section
- `docs/matlock-high-level-design.md` — add `scan-projects` to Key Components and/or Execution Modes
- `docs/copilot/copilot-docs-reference.md` — add keyword rows and topic entry
- `docs/roadmap/index.md` — add Phase 10 row
- `CHANGELOG.md` (new) — `[0.2.0]` section with `Added` entry
- `pyproject.toml` — bump version `0.1.0 → 0.2.0`

**Implementation notes:**
- `docs/matlock-scan-projects.md` should document: purpose, data model (`ScannedFile`, `ProjectCandidate`), scanning logic, output modes, and YAML output schema.
- CLI doc: add `scan-projects` section following the same format as other commands (table of options, examples).
- HLD: add `scan-projects` as a "Discovery Command" (not part of the 5-stage pipeline, but a standalone utility).
- Doc Scan gate: check for stale `0.1.x` version references in all docs.

**Definition of done:**
- `docs/matlock-scan-projects.md` exists and covers all spec detail.
- `docs/matlock-cli.md` has the new command section.
- `CHANGELOG.md` exists with a `[0.2.0] - 2026-04-30` section.
- `pyproject.toml` shows `version = "0.2.0"`.
- All doc links resolve (no broken references).

---

## Acceptance Criteria

- `matlock scan-projects` runs against a real vault without requiring a DB.
- `--print-yaml` outputs valid YAML with `super_projects:` and `projects:` keys.
- `--diff` shows correct Added / Changed / Unchanged / Removed sections (human-readable).
- `--merge` performs full ins/upd/del sync: adds new entries, updates existing, removes entries absent from scan; creates a timestamped `.bak` before writing; the file remains valid `MatlockConfig` after merge.
- Warnings from scanning are emitted to stderr in all three modes.
- Scan order is strictly alphabetical (root to leaf).
- `project_directory` resource is only added to the first candidate for each `project_id`.
- `start_date` falls back to file creation date when no date attribute is present.
- No regressions — all pre-existing tests pass.
- `CHANGELOG.md` updated with `[0.2.0]` `Added` entries.
- Version bumped to `0.2.0` in `pyproject.toml`.
- Affected docs updated: `matlock-cli.md`, `matlock-high-level-design.md`, `copilot-docs-reference.md`, `roadmap/index.md`.

---

## Risks / Notes

- **`python-frontmatter` behavior on files with no frontmatter block:** `frontmatter.load()` returns a `Post` with an empty `metadata` dict; `_scan_file` should return `None` for these (no `---` block present). Verify with the library's `Post.metadata` attribute.
- **Alpha-order definition:** Python's `str` sort is lexicographic by Unicode code point. This should be fine for typical vault paths. Note that uppercase letters sort before lowercase in ASCII (e.g., `A < a`). Normalize with `str.lower()` as the sort key to match user expectations.
- **`projects_directory_project` pattern ambiguity:** A file at `projects/Foo/Bar.md` does not match (filename must equal the directory name). Only `projects/Foo/Foo.md` matches. This is intentional.
- **`named_file_project` vs. `projects_directory_project` overlap:** A file could match both patterns (e.g., `projects/My Project/My Project, Project.md`). Per the `project_id` priority order, `projects_directory_project` wins. Both fields are still populated.
- **`--merge` ordering:** `yaml.dump` writes keys in insertion order when `sort_keys=False`. The existing non-project keys must be preserved in their original order. Load as `dict`, update `super_projects`/`projects` keys, dump back.
- **Frontmatter attribute name case:** YAML keys are case-sensitive. The spec says "case-insensitive" for attribute lookup (e.g., `start_date` vs `Start_Date`). Normalize by lower-casing all frontmatter keys before lookup.
- **`status` domain values are case-sensitive** (per spec: `"Planned"`, `"In Progress"`, etc.). This is intentional — the canonical values use title case and must be matched exactly.

---

## Validation Plan

Run tests in this order:
1. Focused tests (new models): `poetry run pytest tests/test_scan_models.py -v`
2. Focused tests (scanner + formatters + merge): `poetry run pytest tests/test_scan_projects.py -v`
3. Focused tests (CLI): `poetry run pytest tests/test_cli_scan_projects.py -v`
4. Adjacent/regression tests: `poetry run pytest tests/test_config.py tests/test_cli_script.py -v`
5. Full suite: `poetry run pytest -q`

Record results:
- Focused: [pass/fail + summary]
- Regression: [pass/fail + summary]
- Full suite: [pass/fail + summary]

---

## Design Decisions (Resolved)

| # | Question | Decision |
|---|---|---|
| Q1 | **`resources` frontmatter attribute name(s)** | Scan both `resources` **and** `related` string-array attributes; union and deduplicate by path. |
| Q2 | **`--merge` scope** | Full ins/upd/del sync. The scanned vault is the source of truth. Entries absent from the scan are deleted from config; entries present are added or updated. |
| Q3 | **`--diff` format** | Human-readable sections: **Added**, **Changed**, **Unchanged**, **Removed** for both projects and super_projects. |
| Q4 | **`--merge` backup** | Yes — create a timestamped backup at `<config_path>.<YYYY-MM-DDTHH-MM-SS>.bak` before writing. Print backup path to stderr warning and include in CLI echo. |
| Q5 | **Super-project discovery** | Infer super_projects entirely from project pages: collect the distinct set of `super_project_id` values found across all project-candidate `ScannedFile` objects. No dedicated super-project home page scanning. |
| Q6 | **`named_file_project` case sensitivity** | Case-sensitive match: exact strings `, Project.md` and `, Master Project.md`. |
| Q7 | **`projects` attribute — resource linking scope** | Applies to **all** pages (project pages and non-project pages alike). The `projects` attribute is a general-purpose mechanism that associates any page with 0 or more projects as a file-based resource. |
| Q8 | **Version bump** | `0.1.0 → 0.2.0` (new user-visible command = minor feature addition). |
| Q9 | **Warnings display** | Always print to stderr in all modes — warnings indicate data quality issues the user should know about regardless of mode. |
| Q10 | **`status` case sensitivity vs. `priority`** | Intentional per spec. `priority` is normalized (case-insensitive); `status` is validated case-sensitively against the canonical domain values. No change needed. |

---

## Step Notes Log

_(Populated during implementation. One entry per completed step.)_

### SP-S1
- Status: Completed
- Changes: `matlock/scan_models.py` (new), `tests/test_scan_models.py` (new)
- Validation: 12 tests pass

### SP-S2
- Status: Completed
- Changes: `matlock/stages/scan_projects.py` (new — scan_vault, _scan_file, _build_project_candidates)
- Validation: 78 tests pass

### SP-S3
- Status: Completed
- Changes: `matlock/stages/scan_projects.py` — format_as_yaml, format_as_diff, _diff_project_fields
- Validation: covered by test_scan_projects.py

### SP-S4
- Status: Completed
- Changes: `matlock/stages/scan_projects.py` — MergeResult, merge_into_config, _build_project_dict
- Validation: 78 tests pass; full suite 566 pass

### SP-S5
- Status: Completed
- Changes: `matlock/cli.py` (new scan-projects command); `tests/test_cli_scan_projects.py` (new, 19 tests)
- Validation: 585 tests pass

### SP-S6
- Status: Not Started
