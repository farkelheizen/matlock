# 20260424-phase-0 Plan: Foundation — Rename & Scaffold

> Date: 4/24/2026
> Owner: Copilot
> Branch: feat/phase-0-rename-scaffold
> Related docs: `docs/roadmap/index.md`, `docs/matlock-high-level-design.md`, `docs/matlock-data-model.md`

## Problem Summary

The existing codebase lives in the `markdown_stuff` package with model names `ParsedDocument` and `ParsedTask`. The project has been renamed to Matlock. The package, model names, and project metadata must be updated to reflect this before any new Matlock-specific pipeline code is written.

## Goal

Rename the `markdown_stuff` package to `matlock`, rename `ParsedDocument` → `ParsedMarkdownFile` and `ParsedTask` → `ParsedMarkdownTask`, update `pyproject.toml`, and confirm all existing tests pass under the new names.

## Scope

- **In scope:**
  - Rename `markdown_stuff/` directory → `matlock/`
  - Rename `ParsedDocument` → `ParsedMarkdownFile` everywhere
  - Rename `ParsedTask` → `ParsedMarkdownTask` everywhere
  - Update all internal imports (`from markdown_stuff...` → `from matlock...`)
  - Update `pyproject.toml`: `name`, `description`, `[project.scripts]` entrypoint stub
  - Update `scripts/parse_markdown_file.py`
  - Create `matlock/stages/__init__.py` stub subpackage
  - Ensure all existing tests pass with zero changes to test logic

- **Out of scope:**
  - Any new pipeline stage code
  - Config loading, database layer, CLI framework
  - Changes to test logic or assertions

## Constraints / Requirements

- All 7 existing test files must pass without modifying their assertions.
- The public API exported from `matlock/__init__.py` must export `ParsedMarkdownFile`, `ParsedMarkdownTask`, `extract_tasks_from_markdown`, and `parse_front_matter`.
- `pyproject.toml` package name becomes `matlock`.

---

## Implementation Plan

| Step ID | Status | Goal | Planned Changes | Test Coverage |
|---|---|---|---|---|
| P0-S1 | Not Started | Rename package directory | `mv markdown_stuff/ matlock/`; update all `from markdown_stuff` imports | Run full suite |
| P0-S2 | Not Started | Rename models | `ParsedDocument` → `ParsedMarkdownFile`, `ParsedTask` → `ParsedMarkdownTask` in `models.py`, `extractor.py`, `__init__.py`, all test files | `test_models.py`, `test_imports.py`, `test_walk_ast.py` |
| P0-S3 | Not Started | Update pyproject.toml | `name = "matlock"`, `description`, add `[project.scripts]` stub | `poetry install` succeeds |
| P0-S4 | Not Started | Create stages stub | `matlock/stages/__init__.py` | Import smoke test |

Status values: `Not Started` | `In Progress` | `Completed` | `Blocked`

---

## Step Details

### P0-S1 — Rename Package Directory

**Files (expected):**
- `markdown_stuff/` → `matlock/` (directory rename via `mv`)
- `matlock/extractor.py` — update `from markdown_stuff.models` and `from markdown_stuff.parser`
- `tests/test_extract_tasks.py` — update import
- `tests/test_helpers.py` — update import
- `tests/test_models.py` — update import
- `tests/test_extract_attributes.py` — update import
- `tests/test_imports.py` — update import
- `tests/test_walk_ast.py` — update import
- `tests/test_config.py` — update import if applicable
- `scripts/parse_markdown_file.py` — update import

**Implementation notes:**
- Use `git mv markdown_stuff matlock` to preserve git history.
- All `from markdown_stuff.X import Y` → `from matlock.X import Y`.
- All `import markdown_stuff` → `import matlock`.

**Definition of done:**
- `poetry run pytest` passes with no import errors.

---

### P0-S2 — Rename Models

**Files (expected):**
- `matlock/models.py` — rename class definitions
- `matlock/extractor.py` — update `ParsedDocument` → `ParsedMarkdownFile`, `ParsedTask` → `ParsedMarkdownTask` in imports and usage
- `matlock/__init__.py` — update exports
- `tests/test_models.py` — update all references
- `tests/test_imports.py` — update all references
- `tests/test_walk_ast.py` — update any `ParsedTask` references

**Implementation notes:**
- `ParsedDocument` is renamed to `ParsedMarkdownFile`; all fields are preserved.
- `ParsedTask` is renamed to `ParsedMarkdownTask`; all fields are preserved.
- No changes to model field names or validation logic.

**Definition of done:**
- `poetry run pytest tests/test_models.py tests/test_imports.py tests/test_walk_ast.py` all pass.

---

### P0-S3 — Update pyproject.toml

**Files (expected):**
- `pyproject.toml`

**Implementation notes:**
- `name = "matlock"`
- `description = "Pipeline-driven task extractor and report generator for Markdown second brains"`
- Add `[project.scripts]` section with `matlock = "matlock.cli:app"` (the CLI entrypoint will be implemented in a later phase; the entry is a forward declaration and will fail gracefully until Phase 1).
- Do not add new dependencies in this phase.

**Definition of done:**
- `poetry install` completes successfully.
- `poetry run pytest` still passes.

---

### P0-S4 — Create Stages Stub

**Files (expected):**
- `matlock/stages/__init__.py` (empty)

**Implementation notes:**
- Creates the subpackage that future pipeline stage modules will live in.
- No code needed beyond the empty `__init__.py`.

**Definition of done:**
- `python -c "from matlock import stages"` executes without error.

---

## Acceptance Criteria

- All existing tests pass: `poetry run pytest` shows 0 failures, 0 errors.
- `from matlock import ParsedMarkdownFile, ParsedMarkdownTask, extract_tasks_from_markdown, parse_front_matter` succeeds.
- `matlock/stages/__init__.py` exists.
- `pyproject.toml` `name = "matlock"`.
- No references to `markdown_stuff` remain in `matlock/` or `tests/` (verify with grep).

## Risks / Notes

- `git mv` preserves history; plain `mv` does not. Use `git mv`.
- `test_config.py` may reference `markdown_stuff`; check and update if needed.
- The `[project.scripts]` entry for the CLI will raise an `ImportError` until `matlock/cli.py` exists (Phase 1). This is expected and acceptable.

## Validation Plan

Run tests in this order:
1. Focused: `poetry run pytest tests/test_models.py tests/test_imports.py`
2. Regression: `poetry run pytest tests/test_walk_ast.py tests/test_extract_tasks.py tests/test_helpers.py`
3. Full suite: `poetry run pytest`

Record results:
- Focused: [pass/fail + summary]
- Regression: [pass/fail + summary]
- Full suite: [pass/fail + summary]

---

## Step Notes Log

*(Updated as steps are completed)*
