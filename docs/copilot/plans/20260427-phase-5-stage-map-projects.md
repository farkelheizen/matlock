# 20260427-phase-5 Plan: Stage III — Map Projects

> Date: 4/27/2026
> Owner: Copilot
> Branch: feat/phase-5-stage-map-projects
> Related docs: `docs/matlock-pipeline-specification.md`, `docs/matlock-data-model.md`, `docs/matlock-configuration.md`

## Problem Summary

The `super_project`, `project`, and `file_project` tables exist in the schema but are never populated by the sync or parse stages. The `map-projects` stage is responsible for rebuilding these three tables from the definitions in `config.yaml` and matching the resulting project roster against the `file` table.

Matching is resource-based: each project declares a list of `resources`, each being either a `FILE` (exact `file_path` match) or a `DIRECTORY` (prefix match on `file_path`). A file may match more than one project. The stage is always a full rebuild — it truncates and reinserts every time, making it idempotent.

All three DB helpers needed (`replace_super_projects`, `replace_projects`, `replace_file_projects`) already exist in `matlock/db.py` from Phase 2. This phase is primarily logic (the matching algorithm) and wiring.

## Goal

Implement `matlock/stages/map_projects.py` (full-rebuild logic with resource matching), add the `matlock map-projects` CLI subcommand to `matlock/cli.py`, and write comprehensive tests covering FILE/DIRECTORY matching, multi-project membership, no-resources projects, and full-rebuild idempotency.

## Scope

- **In scope:**
  - `matlock/stages/map_projects.py` — `MapProjectsResult`, `run_map_projects(config, conn)`
  - `matlock/cli.py` — add `map-projects` subcommand
  - `tests/test_map_projects.py` — unit/integration tests using `":memory:"`
  - Update `docs/roadmap/index.md` Phase 5 row when complete

- **Out of scope:**
  - Modifying `matlock/db.py` — all needed helpers already exist
  - `needs_parsing` — not touched by this stage
  - Report generation — Phase 7
  - Parsing files — Phase 4 (complete)

## Constraints / Requirements

- `run_map_projects(config: MatlockConfig, conn: sqlite3.Connection) -> MapProjectsResult` — pure function, no side effects beyond DB writes.
- The three tables (`super_project`, `project`, `file_project`) are always fully replaced: truncate-then-insert with no incremental diffing.
- `replace_super_projects` must be called before `replace_projects` (FK constraint: `project.super_project_id → super_project.super_project_id`).
- `replace_projects` must be called before `replace_file_projects` (FK constraint: `file_project.project_id → project.project_id`).
- `file_project` rows must only reference `file_path` values that exist in the `file` table with `deleted = 0`. Deleted files are silently excluded from matching.
- `DIRECTORY` match rule: `file.file_path` starts with the resource `path`. The match must be a proper prefix — i.e. the resource `path` should be treated as a path prefix (see Q1 for trailing-slash handling).
- `FILE` match rule: `file.file_path` equals the resource `path` exactly.
- A file can match multiple projects — one `file_project` row per `(file_path, project_id)` pair.
- A project with no `resources` entries generates no `file_project` rows (it is registered in `project` but has no file associations).
- `conn.commit()` is called once at the end of `run_map_projects()`.
- All DB writes go through the helpers in `matlock/db.py` — no raw SQL in the stage.
- `validate_config_paths()` is called by the CLI before invoking the stage.

---

## Implementation Plan

| Step ID | Status | Goal | Planned Changes | Test Coverage |
|---|---|---|---|---|
| P5-S1 | Completed | Implement `matlock/stages/map_projects.py` | `run_map_projects()`, `_match_files()`, `MapProjectsResult` | `tests/test_map_projects.py`: FILE match, DIRECTORY match, multi-project, no-resources, idempotency, deleted-file exclusion |
| P5-S2 | Completed | Add `map-projects` subcommand to `matlock/cli.py` | `cli.py` — new `map-projects` command | `tests/test_cli_map_projects.py`: CLI invocation, summary output, error cases |
| P5-S3 | Completed | Regression + commit | Full suite green | `poetry run pytest` |

Status values: `Not Started` | `In Progress` | `Completed` | `Blocked`

---

## Step Details

### P5-S1 — Implement `matlock/stages/map_projects.py`

**Files (expected):**
- `matlock/stages/map_projects.py` *(new)*

**`MapProjectsResult` dataclass:**

```python
@dataclasses.dataclass
class MapProjectsResult:
    super_projects_written: int = 0   # rows inserted into super_project
    projects_written: int = 0         # rows inserted into project
    file_project_rows: int = 0        # rows inserted into file_project
```

**`_match_files(conn, project_id, resources) -> list[str]`** — queries all non-deleted `file_path` values from the `file` table and returns those matching at least one resource entry:

```
all_paths = SELECT file_path FROM file WHERE deleted = 0
matched = []
for path in all_paths:
    for resource in resources:
        if resource.type == "FILE" and path == resource.path:
            matched.append(path); break
        if resource.type == "DIRECTORY" and _is_under(path, resource.path):
            matched.append(path); break
return matched
```

**`_is_under(file_path: str, dir_path: str) -> bool`** — POSIX-safe prefix check. Ensures the match is at a directory boundary, not a substring match (see D1):

```python
# Normalise: strip trailing slash from dir_path
prefix = dir_path.rstrip("/") + "/"
return file_path.startswith(prefix) or file_path == dir_path.rstrip("/")
```

Wait — files can't equal a directory path, so the meaningful rule is:
```python
prefix = dir_path.rstrip("/") + "/"
return file_path.startswith(prefix)
```

**`run_map_projects(config, conn) -> MapProjectsResult`:**

```
result = MapProjectsResult()

# 1. Rebuild super_project
sp_rows = [
    {"super_project_id": sp.id, "title": sp.title, "priority": sp.priority}
    for sp in config.super_projects
]
replace_super_projects(conn, sp_rows)
result.super_projects_written = len(sp_rows)

# 2. Rebuild project
proj_rows = [
    {
        "project_id": p.id,
        "super_project_id": p.super_project_id,
        "title": p.title,
        "home_file": p.home_file,
        "priority": p.priority,
        "start_date": p.start_date,
        "due_date": p.due_date,
    }
    for p in config.projects
]
replace_projects(conn, proj_rows)
result.projects_written = len(proj_rows)

# 3. Rebuild file_project
fp_rows = []
for project in config.projects:
    for file_path in _match_files(conn, project.id, project.resources):
        fp_rows.append({"file_path": file_path, "project_id": project.id})
replace_file_projects(conn, fp_rows)
result.file_project_rows = len(fp_rows)

conn.commit()
return result
```

**Definition of done:**
- `super_project` and `project` tables are populated from config.
- A `FILE` resource produces a `file_project` row for the exact matching file.
- A `DIRECTORY` resource produces `file_project` rows for all files whose `file_path` starts with the directory prefix.
- A file matching two projects produces two `file_project` rows.
- A project with no resources produces no `file_project` rows.
- Deleted files (where `deleted = 1`) are never included in `file_project`.
- Running `run_map_projects` twice produces identical DB state (idempotent).

---

### P5-S2 — Add `map-projects` Subcommand to `matlock/cli.py`

**Files (expected):**
- `matlock/cli.py` *(modified)*

**New command:**

```python
@app.command(name="map-projects")
def map_projects(ctx: typer.Context) -> None:
    """Rebuild project-to-file associations from config."""
    cfg = _load_and_validate(ctx.obj[_CONFIG_KEY])

    conn = get_connection(cfg.db_path)
    try:
        init_db(conn)
        result = run_map_projects(cfg, conn)
    finally:
        conn.close()

    typer.echo(
        f"Map-projects complete: {result.super_projects_written} super-projects, "
        f"{result.projects_written} projects, "
        f"{result.file_project_rows} file-project links"
    )
```

**Definition of done:**
- `poetry run matlock map-projects --help` works.
- Running `matlock sync && matlock map-projects` against a `tmp_path` vault produces correct `file_project` rows.

---

### P5-S3 — Regression + Commit

**Files (expected):**
- `docs/roadmap/index.md` — Phase 5 row updated
- `docs/copilot/current-plan.md` — updated

**Definition of done:**
- `poetry run pytest` passes with 211+ tests, zero failures.

---

## Acceptance Criteria

- After `matlock sync && matlock map-projects`, `super_project` and `project` tables reflect the config exactly.
- `file_project` rows are correct for FILE and DIRECTORY resource types.
- Deleted files are never linked to projects.
- Running `map-projects` twice produces the same result (full-rebuild idempotency).
- A file belonging to multiple projects has one `file_project` row per project.
- Full suite passes with zero regressions.

## Risks / Notes

- **FK ordering**: `replace_super_projects` → `replace_projects` → `replace_file_projects` must be called in this order. Violating it raises an FK constraint error even in WAL mode because `PRAGMA foreign_keys = ON`.
- **DIRECTORY prefix boundary**: A naive `str.startswith("Tech/")` is correct as long as `file_path` values always use POSIX separators (guaranteed by Phase 3's sync stage storing them as `relative_to(base_directory).as_posix()`). The resource `path` in config may or may not have a trailing slash — normalise it before comparison (see D1).
- **Empty vault**: If `file` table has no rows, `file_project` will be empty even if projects have resources. No error.
- **Config with no projects**: If `config.projects` is empty, all three replace calls insert empty sets. DB ends up empty for these tables. No error.
- **`home_file` is not auto-linked**: A project's `home_file` is informational metadata stored in the `project` row. The stage does not automatically add a `FILE` resource for it. Linking happens only via explicit `resources` entries.

## Validation Plan

Run tests in this order:
1. Focused tests: `poetry run pytest tests/test_map_projects.py -v`
2. CLI tests: `poetry run pytest tests/test_cli_map_projects.py -v`
3. Regression tests: `poetry run pytest tests/test_sync.py tests/test_parse.py tests/test_db.py -v`
4. Full suite: `poetry run pytest`

Record results:
- Focused: 35 passed (test_map_projects.py)
- CLI: 12 passed (test_cli_map_projects.py)
- Regression: All prior test files green
- Full suite: 258 passed, 0 failures

---

## Design Decisions (Resolved)

**D1 — DIRECTORY trailing-slash normalisation: normalise in the stage.**
`prefix = resource.path.rstrip("/") + "/"`. Works regardless of whether config includes a trailing slash or not. Users should not need to know about trailing-slash rules.

**D2 — Files-to-match query scope: exclude `is_generated = 1` files.**
Query `WHERE deleted = 0 AND is_generated = 0`. Generated files are artefacts and should not appear in project associations, even after Phase 7 is implemented.

**D3 — `_match_files` implementation: Python-side filtering.**
Fetch all eligible paths once per `run_map_projects` call (`WHERE deleted = 0 AND is_generated = 0`), then filter in Python using `str.startswith` / `==`. Avoids SQL LIKE escaping edge cases and is simpler for vault-scale data.
