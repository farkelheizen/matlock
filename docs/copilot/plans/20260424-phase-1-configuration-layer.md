# 20260424-phase-1 Plan: Configuration Layer

> Date: 4/24/2026
> Owner: Copilot
> Branch: feat/phase-1-config-layer
> Related docs: `docs/matlock-configuration.md`, `docs/roadmap/index.md`

## Problem Summary

Matlock has no application-level config system. All subsequent stages (sync, parse, map-projects, etc.) depend on a validated `MatlockConfig` object to know where the vault is, where the database lives, what projects exist, and how task attributes should be parsed. Without this layer, no pipeline stage can be built.

The existing `config/default_config.json` is a *parser-level* config (task attribute definitions only) used directly by the extractor. It is **separate** from the new `config.yaml` application config and is not replaced by this phase.

## Goal

Implement `matlock/config.py` with Pydantic models for the full `config.yaml` schema. `matlock.config.load_config(path)` must return a validated `MatlockConfig`. All structural validation rules from `docs/matlock-configuration.md` must be enforced at load time.

## Scope

- **In scope:**
  - Add `pyyaml` to `pyproject.toml` dependencies
  - `matlock/config.py` — all Pydantic models + `load_config()` + `validate_config_paths()`
  - `tests/test_matlock_config.py` — full test coverage for models, loading, and validation
  - `config/example_config.yaml` — annotated example based on the spec
  - Update `docs/roadmap/index.md` Phase 1 row (status + plan doc link)

- **Out of scope:**
  - Replacing or modifying `config/default_config.json`
  - Wiring `MatlockConfig` into the extractor (Phase 4)
  - CLI integration (`--config` flag handling) — deferred to Phase 3+
  - Filesystem creation (e.g., `output_directory` mkdir) — deferred to Stage I (Phase 3)

## Constraints / Requirements

- `load_config()` handles **schema validation only** (Pydantic). Filesystem checks (`base_directory` exists, `db_path` parent writable) live in a separate `validate_config_paths()` function so tests can construct `MatlockConfig` objects without a real filesystem.
- All Pydantic models use `model_config = ConfigDict(frozen=True)` — configs are read-only after load.
- `load_config()` raises `pydantic.ValidationError` on structural failures.
- `validate_config_paths()` raises `ValueError` with a clear message for each failed check.
- Relative `db_path` and `output_directory` are resolved against `base_directory` inside `load_config()` — stored as absolute `Path` objects in the model.
- `home_file` and resource `path` values remain as strings (relative to `base_directory`); resolution is deferred to the stage that uses them.

---

## Implementation Plan

| Step ID | Status | Goal | Planned Changes | Test Coverage |
|---|---|---|---|---|
| P1-S1 | Completed | Add PyYAML dependency | `pyproject.toml` — add `pyyaml` | `poetry install` succeeds; `import yaml` works |
| P1-S2 | Completed | Implement Pydantic models | `matlock/config.py` — all model classes, `load_config()`, `validate_config_paths()` | Covered by P1-S3 |
| P1-S3 | Completed | Write tests | `tests/test_matlock_config.py` — structural, defaults, validation errors, path checks | `poetry run pytest tests/test_matlock_config.py` |
| P1-S4 | Completed | Add example config | `config/example_config.yaml` — full annotated example from spec | Smoke-test: `load_config("config/example_config.yaml")` passes |

Status values: `Not Started` | `In Progress` | `Completed` | `Blocked`

---

## Step Details

### P1-S1 — Add PyYAML Dependency

**Files (expected):**
- `pyproject.toml`

**Implementation notes:**
- Run `poetry add pyyaml` to add the runtime dependency.
- No code changes yet.

**Definition of done:**
- `pyproject.toml` lists `pyyaml` under `[project]` `dependencies`.
- `python -c "import yaml; print(yaml.__version__)"` succeeds in the venv.

---

### P1-S2 — Implement `matlock/config.py`

**Files (expected):**
- `matlock/config.py` *(new)*

**Model hierarchy (all `frozen=True`):**

```
DomainValueConfig         — alias: str | None
TaskAttributeConfig       — type: Literal["date","time","domain"], alias: str | None, values: dict[str, DomainValueConfig] | None
ResourceConfig            — type: Literal["DIRECTORY","FILE"], path: str
ProjectConfig             — id, super_project_id, title, home_file, priority, start_date, due_date, resources
SuperProjectConfig        — id, title, priority
HeadersConfig             — header_text_maxlen: int = 200
TasksConfig               — task_text_maxlen: int = 500
MatlockConfig             — base_directory, db_path (Path), output_directory (Path), ignore_dirs, debounce_seconds, headers, tasks, task_attributes, super_projects, projects
```

**`load_config(path: str | Path) -> MatlockConfig`:**
- Open and parse YAML with `yaml.safe_load`.
- Resolve `db_path` and `output_directory` relative to `base_directory` (if not already absolute) — store as `Path` objects.
- Instantiate `MatlockConfig(**data)` — Pydantic raises `ValidationError` on bad input.

**`validate_config_paths(config: MatlockConfig) -> None`:**
- `base_directory` exists and is a directory → `ValueError("base_directory does not exist: ...")`
- `db_path.parent` exists → `ValueError("db_path parent directory does not exist: ...")`
- Duplicate `id` values in `super_projects` → `ValueError("Duplicate super_project id: ...")`
- Duplicate `id` values in `projects` → `ValueError("Duplicate project id: ...")`
- Any `super_project_id` on a project must match a defined `super_project.id` → `ValueError("Unknown super_project_id '...' on project '...'")`

**Notes:**
- The duplicate ID and cross-reference checks could be Pydantic `@model_validator(mode='after')` validators instead of living in `validate_config_paths()`. Prefer `@model_validator` for structural invariants (duplicate IDs, broken references) since they are schema errors, not filesystem errors. Only filesystem existence checks go in `validate_config_paths()`.
- `ignore_dirs` defaults to `[]` if omitted from YAML.
- `debounce_seconds` defaults to `5` if omitted.
- `super_projects` and `projects` default to `[]` if omitted.
- `task_attributes` defaults to `{}` if omitted (valid for a minimal config).
- `domain` type attributes must have a non-empty `values` dict — enforce with a field validator on `TaskAttributeConfig`.

**Definition of done:**
- `from matlock.config import load_config, MatlockConfig` imports cleanly.
- `load_config("config/example_config.yaml")` (created in P1-S4) returns a `MatlockConfig` instance with no errors.

---

### P1-S3 — Write Tests in `tests/test_matlock_config.py`

**Files (expected):**
- `tests/test_matlock_config.py` *(new)*

**Test scenarios:**

| Test | Description |
|:-----|:------------|
| `test_load_minimal_config` | A config with only required fields loads without error |
| `test_load_full_config` | The full spec example parses all fields correctly |
| `test_default_debounce_seconds` | Omitting `debounce_seconds` yields `5` |
| `test_default_ignore_dirs` | Omitting `ignore_dirs` yields `[]` |
| `test_default_header_maxlen` | Omitting `headers` section yields `header_text_maxlen=200` |
| `test_default_task_maxlen` | Omitting `tasks` section yields `task_text_maxlen=500` |
| `test_missing_required_base_directory` | Missing `base_directory` raises `ValidationError` |
| `test_missing_required_db_path` | Missing `db_path` raises `ValidationError` |
| `test_missing_required_output_directory` | Missing `output_directory` raises `ValidationError` |
| `test_duplicate_super_project_ids` | Duplicate IDs in `super_projects` raises `ValidationError` |
| `test_duplicate_project_ids` | Duplicate IDs in `projects` raises `ValidationError` |
| `test_unknown_super_project_id_ref` | A project with a `super_project_id` not in `super_projects` raises `ValidationError` |
| `test_domain_attribute_missing_values` | A `domain`-type attribute with no `values` raises `ValidationError` |
| `test_relative_db_path_resolved` | A relative `db_path` is stored as an absolute `Path` under `base_directory` |
| `test_relative_output_directory_resolved` | A relative `output_directory` is stored as an absolute `Path` |
| `test_validate_paths_base_dir_missing` | `validate_config_paths()` raises `ValueError` when `base_directory` does not exist |
| `test_validate_paths_db_parent_missing` | `validate_config_paths()` raises `ValueError` when `db_path.parent` does not exist |
| `test_validate_paths_ok` | `validate_config_paths()` passes with a real temp dir (using `tmp_path`) |

**Implementation notes:**
- Use `tmp_path` (pytest fixture) for any test needing a real directory.
- Write YAML directly as inline strings with `yaml.safe_load` or write to `tmp_path / "config.yaml"` and call `load_config()` — both approaches are valid.
- Do **not** modify `tests/test_config.py` (it tests `default_config.json` and is unrelated).

**Definition of done:**
- `poetry run pytest tests/test_matlock_config.py` passes with all scenarios covered.

---

### P1-S4 — Add `config/example_config.yaml`

**Files (expected):**
- `config/example_config.yaml` *(new)*

**Implementation notes:**
- Transcribe the full example from `docs/matlock-configuration.md` verbatim (all comments preserved).
- Include all sections: core paths, sync behaviour, server mode, parser limits, task attributes (all 6 from the spec), super_projects (one example), projects (one example with resources).
- This file serves as both documentation and a smoke-test fixture.

**Definition of done:**
- `load_config("config/example_config.yaml")` returns a valid `MatlockConfig` in a test or quick script (note: `base_directory` in the example points to a non-existent path, so call `load_config()` only — not `validate_config_paths()`).

---

## Acceptance Criteria

- `from matlock.config import load_config, validate_config_paths, MatlockConfig` imports cleanly.
- `load_config(path)` returns a fully populated `MatlockConfig` for valid YAML, raises `ValidationError` for structural errors.
- `validate_config_paths(config)` raises `ValueError` with a descriptive message for each filesystem/reference violation.
- All 18 scenarios in `tests/test_matlock_config.py` pass.
- Existing tests are unaffected: `poetry run pytest` passes with 83+ tests.
- `config/example_config.yaml` exists and parses without errors.
- `docs/roadmap/index.md` Phase 1 row updated to `In Progress` when work begins, `Completed` when done.

## Risks / Notes

- **`tests/test_config.py` naming**: This file currently tests `default_config.json` and has its own local `load_config()` function. The new `matlock.config.load_config` is a different function in a different module; no conflict at runtime. Keep tests in separate files (`test_config.py` for JSON; `test_matlock_config.py` for YAML/Pydantic).
- **`default_config.json` coexistence**: The extractor still reads `default_config.json` directly until Phase 4 wires `MatlockConfig` into it. These two configs define overlapping data (task attributes) but serve different code paths until the wiring phase. No changes to `default_config.json` or its consumers in this phase.
- **Frozen models and `Path` coercion**: Pydantic v2 coerces `str` → `Path` when the field type is `Path`, so YAML string values for `db_path`/`output_directory` will be converted automatically. Relative path resolution (prepending `base_directory`) must happen *before* constructing the model, or via a `@model_validator(mode='before')`.

## Validation Plan

Run tests in this order:

1. Focused tests for new module: `poetry run pytest tests/test_matlock_config.py -v`
2. Regression tests (existing config tests): `poetry run pytest tests/test_config.py -v`
3. Full suite: `poetry run pytest`

Record results:
- Focused: pass — 21 passed, 0 failures
- Regression: pass — 7 passed (`test_config.py`), 0 failures
- Full suite: pass — 104 passed, 0 failures, 1 deprecation warning (pytimeparse, unrelated)

---

## Design Decisions (Resolved)

The following decisions were confirmed before implementation:

**D1 — Path resolution timing**: Relative `db_path` / `output_directory` are resolved against `base_directory` inside `load_config()`, before Pydantic instantiation. Not via `@model_validator`.

**D2 — `db_path` parent check**: `validate_config_paths()` checks that `db_path.parent` *exists* (not that it is writable). Actual write failures surface when the DB connection is opened.

**D3 — `home_file` validation**: `validate_config_paths()` does **not** check `home_file` existence. A missing `home_file` is a soft concern, not a startup failure. Revisit in Phase 5.

**D4 — `output_directory` creation**: `validate_config_paths()` does **not** create `output_directory`. Creation is deferred to Phase 7 (first report run).
