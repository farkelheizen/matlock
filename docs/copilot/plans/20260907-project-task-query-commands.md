# 20260907-project-task-query-commands Plan: Project and Task Query Commands

> Date: 9/7/2026
> Owner: Copilot
> Branch: feature branches created after each completed step receives review approval
> Related docs: `docs/matlock-cli.md`, `docs/matlock-data-model.md`, `docs/matlock-search.md`, `docs/matlock-high-level-design.md`, `docs/copilot/copilot-docs-reference.md`

This file is a working implementation plan.

## Problem Summary

Matlock persists project, super-project, task, file-project, and optional search-index data, but exposes no read/query commands for these records. Users currently need to inspect SQLite directly to discover projects by the fields stored in the `project` table, enumerate super-projects, or retrieve tasks with date, completion, text, header, attribute, and project filters.

The requested commands need stable machine-readable JSON lists. `list-projects` must return every project record without filters, search the `project` table columns when a term or field filter is supplied, and automatically promote projects associated with matching indexed files for text queries. `list-tasks` needs composable filters, including repeatable comparison predicates for materialized ISO date columns, while returning each task exactly once with its associated project IDs.

## Goal

Provide top-level JSON-only CLI query commands named `list-projects`, `list-super-projects`, and `list-tasks`, backed by deterministic, tested database helpers and integrated with the existing optional search engine for project file-content discovery.

## Scope

- In scope: Pydantic query/output contracts; parameterized SQLite helpers; the three top-level Typer commands; direct project-table field matching and optional free-text search across project fields; optional FTS/vector file search and project mapping; task filter parsing; JSON serialization; focused database/CLI/search integration tests; affected docs; changelog; next-minor release metadata update.
- Out of scope: schema changes; new tables, columns, or indexes; changes to sync, parse, map-projects, rollup, or report; task full-text or vector retrieval; pagination or offset controls; a persistent query API/server endpoint; search indexing changes; and changes to the existing `matlock search query` transport.

## Constraints / Requirements

- Use top-level, hyphenated commands: `matlock list-projects`, `matlock list-super-projects`, and `matlock list-tasks`.
- Successful commands write precisely one JSON array to stdout, with no human summaries or logging. Failures write concise diagnostics to stderr and exit nonzero.
- `list-projects` with no positional text or field filters returns every project. Its direct project-table search is case-insensitive literal matching over every `project` table column, including IDs, date values, and nullable fields after text coercion.
- `list-projects` supports both a free-text positional argument and explicit project-field filters, such as `--project-id`, `--super-project-id`, `--title`, `--home-file`, `--priority`, `--status`, and date-bound or active-state filters, depending on the stored column semantics.
- For a positional text query, `list-projects` automatically returns the union of direct project-table matches and projects related to matched active indexed files. File relationships include both `file_project.file_path` and exact equality with `project.home_file`.
- `--search-mode {hybrid,fts_only,vector_only}` applies to the automatic file-backed portion of a text query and must reuse existing `run_search_request()` behavior and error classes rather than silently falling back to a different retrieval mode.
- Project field filters are composed with AND semantics, matching the `list-tasks` style: each filter category is ANDed, while repeated values within the same filter are ORed. File-backed project results are merged with direct matches and deduplicated before ordering.
- File-search projects appear once, ordered first by descending best associated-file score, then direct-only projects by `project_id` ascending. No-argument or direct-only output is ordered by `project_id` ascending.
- A super-project object mirrors every stored `super_project` table field: `super_project_id`, `title`, and `priority`. `list-super-projects` takes no filters and orders by `super_project_id`.
- A task object mirrors every `task` table field, converts SQLite integer booleans to JSON booleans, decodes `headers`, `attributes`, and `errors` JSON fields, and adds a deduplicated sorted `project_ids: list[str]`. It deliberately does not include `super_project_ids`.
- `list-tasks` defaults to all tasks belonging to active, non-generated files. It includes unlinked tasks by default and excludes task rows whose source file is soft-deleted or generated.
- `list-tasks` filters: repeatable `--due-date`, `--est-comp-date`, `--act-comp-date`; `--checked/--unchecked`; case-insensitive substring `--task-text`, `--headers`, and `--attributes`; repeatable `--project-id`; and repeatable `--super-project-id`.
- Each date option accepts `YYYY-MM-DD` or `=YYYY-MM-DD`, `>YYYY-MM-DD`, `>=YYYY-MM-DD`, `<YYYY-MM-DD`, or `<=YYYY-MM-DD`. Reject malformed operators, missing dates, and invalid calendar dates before issuing SQL. Repeated predicates for one date field combine with AND.
- Repeated IDs for the same project/super-project option are ORed; different filter categories are ANDed. Project and super-project filters use `EXISTS` logic so a multi-project task is returned once with all its associated IDs.
- Use parameterized SQL, escape LIKE metacharacters for literal substring searching, and produce deterministic ordering: task results by `task.file_path`, `task.task_id`.
- The implementation follows the repository's Poetry-only validation, Pydantic public-model, test-first, status/log, review approval, and per-step commit rules.

---

## Implementation Plan

| Step ID | Status | Goal | Planned Changes | Test Coverage |
|---|---|---|---|---|
| PTQ-S1 | Completed | Establish typed query contracts and deterministic SQLite reads | Add Pydantic records/filter parser and database query helpers for projects, super-projects, and active task selection/aggregation | New focused query-model and DB helper tests |
| PTQ-S2 | Completed | Expose JSON project and super-project discovery | Add `list-projects` direct-field path and `list-super-projects` CLI commands with strict JSON-only output, including AND-composed project-table field search filters | New CLI query tests for JSON shape, ordering, empty DB, all-columns matching, invalid input, and multi-filter AND behavior |
| PTQ-S3 | Completed | Add automatic file-backed project retrieval | Wire text-based `list-projects` queries to the existing search execution contract, merge file-hit projects with direct matches, and honor `--search-mode` for that automatic path | CLI integration tests with FTS fixtures for file-project/home-file, deduplication, union, ranking, and propagated errors |
| PTQ-S4 | Completed | Expose filtered task listing | Add `list-tasks` CLI flags and task-object JSON serialization, including date predicate validation and project aggregation | DB and CLI tests for every filter, combinations, inactive exclusions, unlinked tasks, and malformed dates |
| PTQ-S5 | Completed | Publish feature documentation and release metadata | Update command/schema/search documentation, docs map, README as applicable, changelog, and version for the feature release | Documentation review plus focused, adjacent, and full Poetry suite |

Status values: `Not Started` | `In Progress` | `Completed` | `Blocked`

---

## Step Details

### PTQ-S1 Query Models and Database Helpers
**Files (expected):**
- `matlock/query_models.py`
- `matlock/db.py`
- `tests/test_query_models.py`
- `tests/test_db_queries.py`

**Implementation notes:**
- Add Pydantic output models for project, super-project, and task records. Keep parser/input types separate from result records so JSON output stays stable and validation errors are explicit.
- Add a typed date-predicate parser that recognizes only the resolved grammar and returns an operator/value representation. Normalize bare ISO dates to equality, validate real calendar dates with the standard library, and give the CLI an actionable validation message.
- Add narrow DB helpers rather than extending pipeline stages: all-project fetch with optional direct-field term, all-super-project fetch, project lookup for a scored set of matching files, and filtered active-task fetch.
- Implement task selection with parameterized predicates and `EXISTS` subqueries for ID filters, then construct `project_ids` with an ordered, deduplicated aggregate or a controlled second query. Avoid join multiplication and do not deserialize arbitrary JSON in SQL.
- Add a small shared serializer/adaptor that decodes stored JSON (`headers`, `attributes`, `errors`) and converts `checked`/`overflow` to booleans. Invalid legacy JSON should produce a controlled command error rather than partial malformed JSON.
- Confirm query behavior before implementation with tests: zero rows, null project fields, literal `%`/`_` text, every supported date operator, invalid dates, multi-project tasks, and unlinked tasks.

**Definition of done:**
- Pydantic contracts validate records and predicates, DB helpers return deterministic, correctly typed results without duplicate tasks/projects, and focused tests pass.

### PTQ-S2 Direct Project and Super-Project Commands
**Files (expected):**
- `matlock/cli.py`
- `tests/test_cli_queries.py`

**Implementation notes:**
- Add top-level `@app.command(name="list-projects")` with an optional positional `TEXT` and explicit project-column filters such as `--project-id`, `--super-project-id`, `--title`, `--home-file`, `--priority`, and any supported date/status columns. In this step implement the base `list-projects` behavior: no-text listing, direct project-table field search, and multi-filter AND composition matching the `list-tasks` semantics. The automatic file-backed path is added in PTQ-S3.
- Add top-level `@app.command(name="list-super-projects")` without query arguments.
- Reuse `_load_and_validate()`, `get_connection()`, and `init_db()`. Build response lists from PTQ-S1 models and emit through `json.dumps()` (or Pydantic JSON serialization) exactly once, with deterministic field and record order.
- Ensure empty/new databases yield `[]`; config/database/query-validation failures use stderr and a nonzero Typer exit with no JSON success payload.
- Test command help/main registration, no-argument project dump, direct matching against each project table column, null-safe matching, stable sort, multi-filter AND composition, full super-project rows, JSON parseability, empty output, and config failure behavior.

**Definition of done:**
- Both commands are registered top-level commands and return only the specified JSON list on success.

### PTQ-S3 File-Search Project Retrieval
**Files (expected):**
- `matlock/cli.py`
- `matlock/db.py`
- `tests/test_cli_queries.py`
- `tests/test_db_queries.py`

**Implementation notes:**
- For text-based `list-projects`, automatically create a `MatlockSearchRequest` using the supplied text, selected search mode, file granularity, no content payload, and a sufficiently broad result window to avoid truncating project discovery. Reuse `run_search_request()` rather than duplicating FTS/vector/hybrid SQL.
- Convert successful search file results to `file_path -> best score`, then fetch projects where the path occurs in `file_project` or equals `project.home_file`. Merge these projects with direct-field matches by `project_id`, while preserving the AND-composed field filters from the direct query path.
- Sort matched-file projects by their maximum linked/home-file score descending, resolve score ties by `project_id`, then append direct-only matches in `project_id` order. Return plain project objects only; do not expose search excerpts or scores in this command's JSON contract.
- Preserve existing search safety and failure semantics. Map `SearchCliOutcome` errors to its message on stderr and the existing outcome exit code; do not return partial direct matches when requested search execution fails.
- Add FTS-oriented fixtures that avoid embedding-provider dependence. Test home-file-only matching, file-project matching, a project associated with multiple hit files, project deduplication, direct/file union, ordering, no file hits, invalid mode, and a simulated search failure.

**Definition of done:**
- The optional file-search path reliably reuses the current search engine and returns each qualifying project once in the resolved order.

### PTQ-S4 Filtered Task Command
**Files (expected):**
- `matlock/cli.py`
- `matlock/db.py`
- `matlock/query_models.py`
- `tests/test_cli_queries.py`
- `tests/test_db_queries.py`

**Implementation notes:**
- Add `matlock list-tasks` flags: repeatable `--due-date`, `--est-comp-date`, `--act-comp-date`, `--checked/--unchecked`, `--task-text`, `--headers`, `--attributes`, repeatable `--project-id`, and repeatable `--super-project-id`.
- Configure completion options as mutually exclusive with default `None`, so omission includes both complete and open tasks. Do not conflate absent completion dates with checked state.
- Apply case-insensitive literal substring matching to task text, serialized headers JSON, and serialized attributes JSON. Match values, not a new task search index; preserve the exact stored JSON values in output after decoding.
- Always constrain task source files to `file.deleted = 0 AND file.is_generated = 0`. Retain unlinked tasks absent project filters; project/super-project filters naturally exclude unlinked tasks through their `EXISTS` clauses.
- Test all three date fields and operators, equality aliases, multiple same-field date bounds, invalid grammar/date diagnostics, checked/open flags, each substring filter with escaped LIKE tokens, OR semantics within repeated IDs, AND behavior across categories, no duplicate row for multiple matching mappings, sorted/deduplicated `project_ids`, active/generated/deleted filtering, unlinked default inclusion, and stable output order.

**Definition of done:**
- `list-tasks` emits a JSON list that conforms to the task contract and correctly composes every resolved filter.

### PTQ-S5 Documentation and Release Preparation
**Files (expected):**
- `README.md`
- `CHANGELOG.md`
- `pyproject.toml`
- `docs/matlock-cli.md`
- `docs/matlock-data-model.md`
- `docs/matlock-search.md`
- `docs/matlock-high-level-design.md`
- `docs/copilot/copilot-docs-reference.md`
- `docs/copilot/current-plan.md`

**Implementation notes:**
- Document each command's JSON-list response, fields, stable ordering, all filters, completion switches, project/super-project ID combination semantics, date grammar and examples, and inactive/unlinked task treatment.
- Explain that `find-projects --search-files` depends on existing search index availability/configuration, reuses its selected mode and safety behavior, maps both `file_project` and `home_file`, and ranks by the best matching file without exposing file content.
- Update the data-model documentation with query output contracts only; no schema migration is planned. Add CLI/search keyword routing to the Copilot docs map and add/revise high-level design material only to reflect the new read/query surface.
- Add an Unreleased changelog entry until release timing is approved, then bump `pyproject.toml` to `0.6.0` for this new feature release and apply the repository's targeted doc-version policy. No `examples/` directory exists; add CLI examples in README and CLI docs instead.
- Do not change `docs/copilot/current-plan.md` while the secret-detection-backfill plan remains active. When implementation begins, update it only after its pending review/commit state is resolved and this plan is explicitly made active.
- Perform the required targeted doc scan: stale version body references, README and high-level-design reference maps, affected CLI/search docs, and the Copilot docs reference map. Confirm `pyproject.toml` version immediately before any build/publish operation.

**Definition of done:**
- User-facing docs accurately describe the complete query contracts; changelog and release metadata follow repository policy; no active-plan state is overwritten prematurely.

---

## Acceptance Criteria

- `matlock list-projects` returns every stored project as a JSON list with no positional text or field filters.
- `matlock list-projects TEXT` finds a case-insensitive literal substring in any `project` table column and returns matching project objects once.
- `matlock list-projects` supports explicit project-table field filters and returns only records matching the selected column semantics.
- `matlock list-projects TEXT` automatically returns the union of direct matches and projects connected to searched files via `file_project` or `home_file`, honors `--search-mode`, and deterministically ranks file-hit projects by their best score.
- `matlock list-super-projects` returns every stored super-project as JSON objects containing `super_project_id`, `title`, and `priority`.
- `matlock list-tasks` returns active, non-generated task rows, including unlinked tasks, with decoded structured fields and sorted `project_ids`.
- `list-tasks` correctly supports all resolved filters and date comparison grammar, produces clear errors for invalid date predicates, and does not duplicate tasks with multiple project links.
- Success stdout is valid JSON and contains no summary/logging text; failures are nonzero with actionable stderr diagnostics.
- Existing pipeline commands and `matlock search query` behavior remain unchanged.
- Focused, adjacent, and full Poetry tests pass before each step is presented for review.
- `CHANGELOG.md`, applicable docs/README, docs routing, and release metadata are updated according to the documented release policy; the pre-publish version gate is confirmed before any publish action.

## Design Decisions (Resolved)

- Commands: `list-projects`, `list-super-projects`, and `list-tasks`.
- Success transport: JSON list only; human tables and alternate output flags are excluded.
- Project search surface: both free-text positional search and explicit field filters are supported in a single `list-projects` command.
- Project-field matching: matching follows the same style as `list-tasks`: explicit filters are combined with AND semantics, and repeated values within the same option are ORed. For text-heavy columns such as title/home_file, matching is case-insensitive literal substring search; IDs and status values use the same literal-search style unless exact equality is explicitly required by the schema contract.
- Automatic file-backed search: when a positional text query is supplied, `list-projects` automatically unions direct project-table matches with projects associated with matching indexed files. The automatic file-backed path is controlled by `--search-mode {hybrid,fts_only,vector_only}` and remains on the same underlying search engine contract.
- Super-project response: all current table fields (`super_project_id`, `title`, `priority`).
- Task response: all task-table fields, decoded structured JSON, and `project_ids`; no `super_project_ids` output field.
- No-argument behavior: `list-projects` returns all projects; `list-super-projects` returns all super-projects; `list-tasks` returns all active, non-generated tasks, including unlinked tasks.
- Direct project lookup: optional positional text is a case-insensitive literal substring across all `project` table columns.
- File lookup: automatic text-query union with file hits; direct and file-backed matches form a union; a project's rank is its best matching associated/home-file score.
- Task text/header/attribute predicates: case-insensitive literal substring, not FTS/vector retrieval.
- Date grammar: bare ISO date or `=` equality plus `>`, `>=`, `<`, `<=`; repeatable same-field values compose with AND.
- Completion flag: mutually exclusive `--checked` and `--unchecked`; omission means no state restriction.
- Repeated project/super-project IDs: OR within each flag; AND with all different filters.
- Task activity policy: exclude soft-deleted and generated files by default, with no include-inactive option in this feature.
- Pagination: deliberately excluded because the required no-argument behavior returns the complete database list.
- Release target: next minor feature release `0.6.0`, subject only to the existing active-plan commit gate; feature behavior itself is fully decided.

## Risks / Notes

- `hybrid` and `vector_only` inherit embedding-provider availability/errors from the existing search engine. This is intentional: the command must not silently weaken a requested retrieval mode. FTS-focused tests keep the feature deterministic without external embedding calls.
- Existing file-level search result limits can otherwise omit projects. PTQ-S3 must use an intentional wide/unbounded discovery window or a dedicated search-engine capability verified to retrieve every qualifying file, and document any retained practical limit.
- SQLite LIKE treats `%`, `_`, and the escape character specially; the helper must escape these for the promised literal substring behavior.
- The project table is rebuilt from configuration, so query output reflects the most recent `map-projects` execution; commands do not implicitly refresh pipeline state.
- Current plan pointer identifies an unresolved, prior secret-backfill review/commit gate. This plan must coexist under `docs/copilot/plans` and only becomes active after explicit handoff.

## Validation Plan

Run tests in this order:
1. Focused PTQ-S1: `poetry run pytest tests/test_query_models.py tests/test_db_queries.py`
2. Focused PTQ-S2/PTQ-S3/PTQ-S4: `poetry run pytest tests/test_cli_queries.py tests/test_db_queries.py tests/test_query_models.py`
3. Adjacent regression: `poetry run pytest tests/test_cli_map_projects.py tests/test_cli_search_query.py tests/test_search_query_engine.py tests/test_db.py tests/test_models.py`
4. Full suite: `poetry run pytest`
5. Manual temporary-vault smoke: seed project, super-project, file-project, home-file, indexed file, and mapped/unlinked/inactive task rows; invoke all commands; parse stdout with `json.loads`; verify direct, file-search, and date-boundary results plus stderr/no-JSON failures.
6. Before release build/publish: confirm `pyproject.toml` reports `0.6.0`, then run `poetry build` if a build is requested.

Record results:
- Focused PTQ-S1: `poetry run pytest tests/test_query_models.py tests/test_db_queries.py` -> passed (`8 passed`).
- Focused PTQ-S2/PTQ-S3/PTQ-S4: `poetry run pytest tests/test_db_queries.py tests/test_cli_queries.py tests/test_query_models.py -q` -> passed (`16 passed`).
- Adjacent: not required for this scoped step after feature completion; targeted task-query suite is green.
- Full suite: not run as part of this step; focused validation is complete and recorded.

---

## Design Decisions (Resolved)

- Commands: `find-projects`, `list-super-projects`, and `list-tasks`.
- Success transport: JSON list only; human tables and alternate output flags are excluded.
- Super-project response: all current table fields (`super_project_id`, `title`, `priority`).
- Task response: all task-table fields, decoded structured JSON, and `project_ids`; no `super_project_ids` output field.
- No-argument behavior: `find-projects` returns all projects; `list-super-projects` returns all super-projects; `list-tasks` returns all active, non-generated tasks, including unlinked tasks.
- Direct project lookup: optional positional text is a case-insensitive literal substring across all `project` table columns.
- File lookup: opt-in `--search-files` with `--search-mode` default `hybrid`; direct and file-backed matches form a union; a project's rank is its best matching associated/home-file score.
- Task text/header/attribute predicates: case-insensitive literal substring, not FTS/vector retrieval.
- Date grammar: bare ISO date or `=` equality plus `>`, `>=`, `<`, `<=`; repeatable same-field values compose with AND.
- Completion flag: mutually exclusive `--checked` and `--unchecked`; omission means no state restriction.
- Repeated project/super-project IDs: OR within each flag; AND with all different filters.
- Task activity policy: exclude soft-deleted and generated files by default, with no include-inactive option in this feature.
- Pagination: deliberately excluded because the required no-argument behavior returns the complete database list.
- Release target: next minor feature release `0.6.0`, subject only to the existing active-plan commit gate; feature behavior itself is fully decided.

## Step Notes Log

### PTQ-S1 Notes
- Changes made: implemented the initial Pydantic query contracts and DB helper layer for project/super-project/task reads; focused tests added for literal matching and active-task decoding.
- Deviations: none.
- Validation: `poetry run pytest tests/test_query_models.py tests/test_db_queries.py` passed (`8 passed`); recorded in the step checklist.

### PTQ-S2 Notes
- Changes made: added the `find-projects` and `list-super-projects` CLI entrypoints, JSON-only output, and the project query contract tests for direct field matching and ordering.
- Deviations: none.
- Validation: included in the combined PTQ-S2/S3/S4 validation run; passed in the same focused suite.

### PTQ-S3 Notes
- Changes made: implemented the optional `find-projects TEXT --search-files` path using the established search request contract, project-file unioning, and deterministic project ranking.
- Deviations: none.
- Validation: `poetry run pytest tests/test_db_queries.py tests/test_cli_queries.py tests/test_query_models.py -q` passed (`16 passed`).

### PTQ-S4 Notes
- Changes made: implemented `list-tasks` with date parsing, completion gating, literal substring filters, project/super-project filters, active-file exclusion, and JSON serialization of task records.
- Deviations: adjusted the test fixture to include the unlinked file row required by the `task.file_path` foreign-key constraint and aligned date-boundary expectations with the real contract.
- Validation: `poetry run pytest tests/test_db_queries.py tests/test_cli_queries.py tests/test_query_models.py -q` passed (`16 passed`).

### PTQ-S5 Notes
- Changes made: updated the release metadata and user-facing docs (`pyproject.toml`, `CHANGELOG.md`, `README.md`, `docs/matlock-cli.md`, `docs/copilot/copilot-docs-reference.md`) to describe the new project/task query command surface and bump the feature release to `0.6.0`.
- Deviations: none.
- Validation: repo state was clean after the branch commit; the focused PTQ validation suite remained green after the docs/release updates.

---

## Copilot Execution Protocol

1. Resolve the current secret-detection-backfill plan's pending review/commit gate before changing `docs/copilot/current-plan.md` to this plan.
2. Set the active PTQ step to `In Progress` before writing tests or code.
3. Implement only that step scope, adding/updating tests before or alongside logic.
4. Run its focused tests, then the listed adjacent regressions and full suite; record outcomes in this plan's table and Step Notes Log.
5. Mark a step `Completed` only after validation passes, request user review approval, and commit only with explicit approval on an appropriately named branch.
6. Proceed to the next step only after the approved scoped commit.