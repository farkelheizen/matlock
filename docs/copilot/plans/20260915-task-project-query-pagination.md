# 20260915-task-project-query-pagination Plan: Pagination, Count-Only, and Attribute Filtering for `tasks query` / `projects query`

> Date: 9/15/2026
> Owner: Copilot
> Branch: feature branches created after each completed step receives review approval
> Related docs: `docs/matlock-cli.md`, `docs/matlock-data-model.md`, `docs/matlock-configuration.md`, `docs/copilot/plans/20260907-project-task-query-commands.md`, `docs/copilot/copilot-docs-reference.md`

This file is a working implementation plan.

## Problem Summary

`matlock tasks query` and `matlock projects query` currently return a bare JSON array with no total-count, `limit`, or `offset` support (`matlock/cli.py` `list_tasks`/`list_projects`, backed by `fetch_active_tasks`/`fetch_projects` in `matlock/db.py`). Every matching row is always fetched and returned. This was an explicit, documented design decision in the prior feature plan (`docs/copilot/plans/20260907-project-task-query-commands.md`, "Pagination: deliberately excluded because the required no-argument behavior returns the complete database list.") — this plan intentionally reverses that decision.

For tools built on top of Matlock to browse a large second brain, callers need: (1) a way to know how many rows match before deciding how many to request, (2) `limit`/`offset` controls for paging through large result sets, (3) a `--count-only` mode that returns just the pagination envelope with no row payload, and (4) for `tasks query` specifically, structured JSON-path filtering against the `task.attributes` clob (currently only a blunt case-insensitive substring match over the serialized JSON text) so callers can filter on real attribute values rather than string-matching serialized JSON.

## Goal

Reshape `tasks query` and `projects query` JSON output into a paginated response envelope (`total_matches`, `returned_matches`, `limit`, `offset`, `results`), add `--limit`/`--offset`/`--count-only` flags to both commands with a configurable default page size, add a `--min-score` flag to `projects query`'s file-backed text search path, and add a structured JSON-path attribute filter flag to `tasks query` only (the `project` table has no JSON/attributes column).

## Scope

- In scope: response envelope reshape for `tasks query` and `projects query`; `--limit`/`--offset`/`--count-only` flags and DB-layer support for both commands; a new `queries.default_limit` config setting (default `20`); a `--min-score FLOAT` flag on `projects query`'s automatic file-backed search path; a structured `--attribute-filter PATH:OP:VALUE` flag for `tasks query` reusing the existing `json_extract`-based filter logic from `matlock/search/sql_filters.py`, extracted into a shared, search-independent module; focused/adjacent test updates; CHANGELOG and doc updates; version bump to `0.8.0`.
- Out of scope: any change to `matlock search query`'s own request/response contract (it already has `limit`/`offset`/`total_matches`/`min_score`); a `--sort` / alternate ordering flag (existing deterministic ordering by `file_path`/`task_id`/`project_id` is preserved and is sufficient for stable pagination); schema changes; changes to `super-projects list` (no filters exist today and it was not requested); adding a JSON-attribute-style filter to `projects query` (no JSON column exists on `project`); any compatibility shim for the old bare-array output (not needed — single-user project, no external consumers).

## Constraints / Requirements

- This is an accepted **breaking change** to the current `tasks query` / `projects query` output contract (bare array → enveloped object). No compatibility flag is required since this project has no external consumers today. Document it plainly in `CHANGELOG.md` under `0.8.0` regardless.
- `total_matches` must reflect the count of all rows matching the current filters, independent of `--limit`/`--offset`.
- `--count-only` must return `results: []` and `returned_matches: 0`, while still computing an accurate `total_matches` for the same filter set.
- Default page size: add `queries.default_limit: int = 20` (`ge=1`) to `MatlockConfig` (new `QueriesConfig` model, mirroring the existing `SearchConfig`/`SearchIndexingConfig` nesting style in `matlock/config.py`). `--limit` on both commands defaults to this config value when not explicitly passed on the CLI; passing `--limit` always overrides the config value for that invocation.
- For `tasks query`, add `--limit INT` (config-defaulted), `--offset INT` (default `0`, `ge=0`), `--count-only`, and `--attribute-filter PATH:OP:VALUE` (repeatable, AND-composed, colon-delimited grammar) using the same operator vocabulary as `SearchMetadataFilter` (`eq`, `neq`, `gt`, `gte`, `lt`, `lte`, `in`, `contains`) via `json_extract`/`json_each` against `t.attributes`.
- For `projects query`, add `--limit`/`--offset`/`--count-only` (same config-defaulted behavior) and `--min-score FLOAT` (applies only to the automatic file-backed text-search path, forwarded to `MatlockSearchRequest.tuning.min_score`, filtering out low-scoring file hits before they are merged/counted/ranked). No attribute-filter flag (no JSON column exists on `project`).
- `projects query`'s text-search path (`list_projects` in `matlock/cli.py`) merges direct `LIKE`-matched rows with file-backed search hits and orders the merged list in Python (not SQL `ORDER BY`), before it ever reaches a `LIMIT`. Pagination for that path must slice the already-ordered in-memory list rather than push `LIMIT`/`OFFSET` into SQL, and `total_matches` must be the size of that merged/deduplicated list (after `--min-score` filtering) before slicing.
- `--count-only` on `projects query`'s text-search path still requires running the full `run_search_request()` scoring pass (embedding calls included for `hybrid`/`vector_only`) to know the true post-merge count — there is no cheaper accurate shortcut in the current engine, and that cost is accepted.
- Preserve the existing case-insensitive substring `--attributes TEXT` flag on `tasks query` unchanged for backward compatibility; `--attribute-filter` is additive, not a replacement.
- **Maximize code reuse:** extract the generic JSON-path clause-building logic (`json_extract`/`json_each`/`json_type`/value coercion) currently in `matlock/search/sql_filters.py::build_metadata_filter_clause` into a new search-independent module, `matlock/sql_filters.py`, parameterized on the target JSON column (e.g., `f.meta_data` or `t.attributes`). `matlock/search/sql_filters.py` and `matlock/db.py` both import from it; neither duplicates the SQL-building logic, and `matlock/db.py` does not import from `matlock/search/`.
- Response envelope field naming is unified: `total_matches` and `returned_matches` match the existing naming used by `matlock.search.models.SearchResponseStats`, plus `limit` and `offset` (not present on `SearchResponseStats` today). The new envelope is its own model (not a subclass of `SearchResponseStats`) since `query_time_ms`/`search_mode_executed` do not apply to `tasks query`/`projects query`, and `search query`'s own contract is out of scope for this plan.
- Use parameterized SQL throughout; do not string-interpolate filter values into `json_extract` paths or values.
- The implementation follows the repository's Poetry-only validation, Pydantic public-model, test-first, status/log, review approval, and per-step commit rules.

---

## Implementation Plan

| Step ID | Status | Goal | Planned Changes | Test Coverage |
|---|---|---|---|---|
| TPQ-S1 | Completed | Define the shared paginated response contract and config default | Add `QueryResponseStats`/envelope model to `matlock/query_models.py`; add `QueriesConfig.default_limit` to `matlock/config.py` | `tests/test_query_models.py`, `tests/test_matlock_config.py` |
| TPQ-S2 | Completed | Extract shared JSON-path filter module | Create `matlock/sql_filters.py` from the generic parts of `matlock/search/sql_filters.py::build_metadata_filter_clause`; update `matlock/search/sql_filters.py` to import from it | `tests/test_search_metadata_filters.py` (regression), new `tests/test_sql_filters.py` |
| TPQ-S3 | Completed | Add limit/offset/count-only to `tasks query` | Extend `fetch_active_tasks` in `matlock/db.py` with a count query + `LIMIT`/`OFFSET`/count-only short-circuit; add `--limit`/`--offset`/`--count-only` to `list_tasks` in `matlock/cli.py`, defaulted from `queries.default_limit`; reshape output to the envelope | `tests/test_db_queries.py`, `tests/test_cli_queries.py` |
| TPQ-S4 | Completed | Add the `task.attributes` JSON-path filter | Add `--attribute-filter` parsing to `matlock/query_models.py`; use the TPQ-S2 shared helper against `t.attributes` in `matlock/db.py`; wire the flag in `matlock/cli.py` | `tests/test_db_queries.py`, `tests/test_cli_queries.py`, `tests/test_query_models.py` |
| TPQ-S5 | Not Started | Add limit/offset/count-only/min-score to `projects query` | Extend `fetch_projects` and the in-memory merged-ranking path in `list_projects` (`matlock/cli.py`) with slicing/count support, `--min-score` forwarding to `tuning.min_score`, and envelope output | `tests/test_db_queries.py`, `tests/test_cli_queries.py` |
| TPQ-S6 | Not Started | Documentation and release preparation | Update `docs/matlock-cli.md`, `docs/matlock-data-model.md`, `docs/matlock-configuration.md`, `CHANGELOG.md`, `pyproject.toml`, `docs/copilot/copilot-docs-reference.md` | Doc review plus focused, adjacent, and full Poetry suite |

Status values: `Not Started` | `In Progress` | `Completed` | `Blocked`

---

## Step Details

### TPQ-S1 Shared Paginated Response Contract and Config Default
**Files (expected):**
- `matlock/query_models.py`
- `matlock/config.py`
- `tests/test_query_models.py`
- `tests/test_config.py`

**Implementation notes:**
- Add a `QueryResponseStats` model (`total_matches: int`, `returned_matches: int`, `limit: int | None`, `offset: int`) and a thin envelope wrapper (stats + `results: list[...]`) to `matlock/query_models.py`, reused by both `tasks query` and `projects query`.
- Add `QueriesConfig` (`default_limit: int = Field(default=20, ge=1)`) to `matlock/config.py` following the existing frozen/`extra="forbid"` style of `SearchConfig`, and add `queries: QueriesConfig = Field(default_factory=QueriesConfig)` to `MatlockConfig`.

**Definition of done:**
- Model validates the envelope shape (populated and count-only/empty cases); config loads/validates the new `queries.default_limit` key with its default and overrides.

### TPQ-S2 Shared JSON-Path Filter Module
**Files (expected):**
- `matlock/sql_filters.py` (new)
- `matlock/search/sql_filters.py`
- `tests/test_sql_filters.py` (new)
- `tests/test_search_metadata_filters.py`

**Implementation notes:**
- Move `build_metadata_filter_clause` (and its `_coerce_scalar_value`/`_coerce_collection_values`/`_placeholders` helpers) out of `matlock/search/sql_filters.py` into `matlock/sql_filters.py`, keeping the function's `json_column` parameterization so both `f.meta_data` and `t.attributes` work unchanged.
- Update `matlock/search/sql_filters.py::build_file_filter_clause` to import and call the relocated function; no behavior change for existing search callers.
- Add a dedicated `tests/test_sql_filters.py` for the relocated generic logic (operator coverage, collection/`in`/`contains`, scalar coercion) so this behavior isn't only indirectly covered through search tests.

**Definition of done:**
- `matlock/db.py` can import the shared filter builder without importing anything from `matlock/search/`; existing search metadata filter tests remain green with no logic changes.

### TPQ-S3 `tasks query` Pagination
**Files (expected):**
- `matlock/db.py`
- `matlock/cli.py`
- `tests/test_db_queries.py`
- `tests/test_cli_queries.py`

**Implementation notes:**
- Add `limit: int | None`, `offset: int`, and `count_only: bool` to the `fetch_active_tasks` filter contract. When `count_only` is set, run only `SELECT COUNT(*) FROM task t WHERE ...` (same clause-building logic, no `t.*` fetch, no JSON decode, no `project_ids` aggregation) and return an empty row list plus the count.
- Otherwise, always compute `total_matches` via a `COUNT(*)` query using the identical `WHERE` clause as the row query (share the clause/params builder rather than duplicating it), then apply `LIMIT ? OFFSET ?` to the existing `SELECT t.* ... ORDER BY t.file_path ASC, t.task_id ASC` query.
- In `list_tasks` (`matlock/cli.py`), default `--limit` from `cfg.queries.default_limit` when the CLI flag is omitted; add `--offset`/`--count-only`; build the envelope and emit exactly one JSON object.

**Definition of done:**
- `tasks query` returns the envelope shape in all cases (default, filtered, count-only, paginated), `total_matches` is filter-accurate and independent of `limit`, the configured default limit applies when `--limit` is omitted, and existing filter behavior is unchanged.

### TPQ-S4 `task.attributes` JSON-Path Filter
**Files (expected):**
- `matlock/query_models.py`
- `matlock/db.py`
- `matlock/cli.py`
- `tests/test_db_queries.py`
- `tests/test_cli_queries.py`
- `tests/test_query_models.py`

**Implementation notes:**
- Add `parse_attribute_filter_predicate(raw: str)` to `matlock/query_models.py`, parsing colon-delimited `path:operator:value` tokens (e.g., `owner:eq:ops`, `estimate_hours:gte:5`) into a structured filter object shaped like `matlock.search.models.SearchMetadataFilter` (`path`, `operator`, `value`). Auto-prefix bare paths with `$.` when the caller doesn't supply a leading `$`.
- Call the TPQ-S2 shared helper with `json_column="t.attributes"` to build the clause; repeated `--attribute-filter` values AND-compose, matching the existing repeated-filter convention used elsewhere in `tasks query`/`projects query`.

**Definition of done:**
- `tasks query --attribute-filter "owner:eq:ops"` (and other supported operators) filters correctly against real JSON attribute values, with tests for each operator, invalid grammar, and combination with existing filters.

### TPQ-S5 `projects query` Pagination and Min-Score
**Files (expected):**
- `matlock/db.py`
- `matlock/cli.py`
- `tests/test_db_queries.py`
- `tests/test_cli_queries.py`

**Implementation notes:**
- No-text path (`fetch_projects` only): mirror TPQ-S3's approach — a `COUNT(*)` query sharing the same clause builder, then `LIMIT`/`OFFSET` on the row query, with the same config-defaulted `--limit`.
- Text-search path (`list_projects` merged direct + file-backed ranking): add `--min-score FLOAT` forwarded as `tuning.min_score` on the `MatlockSearchRequest` built for the automatic file-backed lookup, so low-scoring file hits are excluded before they contribute to `file_scores`/ranking/counting. `total_matches` is `len(ordered_project_ids)` computed from the existing in-memory merge (after min-score filtering); `--limit`/`--offset` slice that same ordered list before the final `IN (...)` row fetch. `--count-only` skips the final `IN (...)` row fetch entirely once the ordered ID list (and thus the count) is known, but still executes the underlying `run_search_request()` call — this cost is accepted per the resolved design decision below.

**Definition of done:**
- `projects query` returns the envelope shape for both the direct-only and text/file-backed paths, with accurate counts, correct slicing, and working `--min-score` filtering on the file-backed path.

### TPQ-S6 Documentation and Release Preparation
**Files (expected):**
- `docs/matlock-cli.md`
- `docs/matlock-data-model.md`
- `docs/matlock-configuration.md`
- `CHANGELOG.md`
- `pyproject.toml`
- `docs/copilot/copilot-docs-reference.md`
- `docs/copilot/current-plan.md`

**Implementation notes:**
- Document the new envelope shape, `--limit`/`--offset`/`--count-only` flags for both commands, `queries.default_limit` in the configuration doc, `--min-score` for `projects query`, and `--attribute-filter` grammar/operators for `tasks query`. Note the breaking output-shape change plainly in `CHANGELOG.md`.
- Do not update `docs/copilot/current-plan.md` until the prior plan's pending review/commit gate (`docs/copilot/plans/20260831-secret-detection-backfill.md`, step SDB-S4) is resolved and this plan is explicitly made active.
- Bump `pyproject.toml` to `0.8.0` immediately before any build/publish step (pre-publish gate).

**Definition of done:**
- Docs and changelog accurately describe the new contract, config key, and flags; release metadata reflects `0.8.0`; doc scan gate satisfied.

---

## Acceptance Criteria

- `matlock tasks query` and `matlock projects query` return a JSON object (not a bare array) containing `total_matches`, `returned_matches`, `limit`, `offset`, and `results`.
- `--count-only` returns `results: []`, `returned_matches: 0`, and an accurate `total_matches` for both commands.
- `--limit`/`--offset` correctly page through results with stable ordering, for both the direct-only and (for `projects query`) file-backed-merge paths.
- Omitting `--limit` uses `queries.default_limit` (default `20`) from config; passing `--limit` explicitly overrides it.
- `projects query`'s automatic file-backed text search supports `--min-score` to exclude low-scoring file hits from ranking, counting, and pagination.
- `tasks query --attribute-filter PATH:OP:VALUE` correctly filters on real JSON attribute values via `json_extract`, is repeatable with AND semantics, and coexists with the existing `--attributes` substring flag.
- `projects query` gains no attribute-filter flag (no applicable JSON column).
- The JSON-path filter clause-building logic exists in exactly one shared module (`matlock/sql_filters.py`) used by both `matlock/db.py` and `matlock/search/sql_filters.py`.
- Existing filters, ordering, and error behavior for both commands are otherwise unchanged.
- Focused, adjacent, and full Poetry tests pass before each step is presented for review.
- `CHANGELOG.md`, `docs/matlock-cli.md`, `docs/matlock-data-model.md`, `docs/matlock-configuration.md`, docs routing, and release metadata (`0.8.0`) are updated per repository policy.

## Design Decisions (Resolved)

- **Breaking change accepted, no compatibility shim.** This project has no external consumers today; the bare-array → envelope reshape ships as a straightforward breaking change in `0.8.0`, documented in `CHANGELOG.md`.
- **Default page size is configurable.** New `queries.default_limit` config key (default `20`, `ge=1`) supplies the default for `--limit` on both commands when the flag is omitted; explicit `--limit` always overrides it. This preserves the original "no-argument returns a bounded, predictable page" behavior while keeping the size adjustable per vault.
- **`--attribute-filter` grammar:** colon-delimited `PATH:OPERATOR:VALUE` (e.g., `owner:eq:ops`, `estimate_hours:gte:5`), operators matching `SearchMetadataFilter` (`eq`, `neq`, `gt`, `gte`, `lt`, `lte`, `in`, `contains`). Bare paths are auto-prefixed with `$.`.
- **Maximize code reuse:** the generic `json_extract`/`json_each` clause-building logic is extracted from `matlock/search/sql_filters.py` into a new shared, search-independent module `matlock/sql_filters.py`. Both `matlock/db.py` (tasks) and `matlock/search/sql_filters.py` (file metadata) call the shared function; `matlock/db.py` does not depend on `matlock/search/`.
- **`--min-score` added to `projects query`'s file-backed search path.** Forwarded to `MatlockSearchRequest.tuning.min_score`, applied before ranking/counting/merging. The full embedding-scored search cost under `--count-only` for `hybrid`/`vector_only` is accepted as-is (no cheaper accurate shortcut exists); this is a single-user project so the cost tradeoff is acceptable.
- **Envelope field naming:** reuse `total_matches`/`returned_matches` naming from `matlock.search.models.SearchResponseStats` for cross-command consistency, add `limit`/`offset` (new), omit `query_time_ms`/`search_mode_executed` (not applicable; `search query`'s own contract is unchanged and out of scope).

## Risks / Notes

- The prior plan (`20260907-project-task-query-commands.md`) explicitly decided against pagination for a documented reason ("no-argument behavior returns the complete database list"). This plan reverses that decision in favor of a configurable bounded default (`queries.default_limit = 20`), which still gives predictable behavior without requiring `--limit` on every call.
- `docs/copilot/current-plan.md` currently points at `20260831-secret-detection-backfill.md` (SDB-S4, review/commit pending). This plan must not become "active" in that pointer file until that gate clears.
- Moving `build_metadata_filter_clause` out of `matlock/search/sql_filters.py` touches an existing, tested module boundary — TPQ-S2 must keep `matlock/search/sql_filters.py`'s public behavior and existing test coverage green with a pure refactor (no logic changes) before any new callers are added in later steps.
- For `projects query`'s `hybrid`/`vector_only` text-search path, an accurate `total_matches`/`--count-only` result still requires running full search scoring (embedding calls included) even with `--min-score` applied afterward — `--min-score` reduces which hits count as matches but does not reduce the scoring cost itself.

## Validation Plan

Run tests in this order:
1. Focused TPQ-S1: `poetry run pytest tests/test_query_models.py tests/test_config.py`
2. Focused TPQ-S2: `poetry run pytest tests/test_sql_filters.py tests/test_search_metadata_filters.py tests/test_search_query_engine.py`
3. Focused TPQ-S3/S4: `poetry run pytest tests/test_db_queries.py tests/test_cli_queries.py tests/test_query_models.py`
4. Focused TPQ-S5: `poetry run pytest tests/test_db_queries.py tests/test_cli_queries.py`
5. Adjacent regression: `poetry run pytest tests/test_cli_map_projects.py tests/test_search_query_engine.py tests/test_search_metadata_filters.py tests/test_db.py tests/test_models.py tests/test_config.py`
6. Full suite: `poetry run pytest`

Record results:
- Focused TPQ-S1: not yet run.
- Focused TPQ-S2: not yet run.
- Focused TPQ-S3/S4: not yet run.
- Focused TPQ-S5: not yet run.
- Adjacent: not yet run.
- Full suite: not yet run.

---

## Step Notes Log

### TPQ-S1 Notes
- Changes made: added `QueryResponseStats` (`total_matches`, `returned_matches`, `limit`, `offset`) plus `TaskQueryResponse`/`ProjectQueryResponse` envelope subclasses to `matlock/query_models.py`; added `QueriesConfig.default_limit` (default `20`, `ge=1`) and wired it into `MatlockConfig.queries` in `matlock/config.py`.
- Deviations: none.
- Validation: `poetry run pytest tests/test_query_models.py tests/test_matlock_config.py -q` -> passed (`58 passed`); adjacent `poetry run pytest tests/test_config.py tests/test_db.py tests/test_models.py -q` -> passed (`63 passed`).

### TPQ-S2 Notes
- Changes made: extracted the generic `json_extract`/`json_each` clause-building logic into a new `matlock/sql_filters.py` (`build_json_path_filter_clause`, parameterized on `json_column`); `matlock/search/sql_filters.py::build_metadata_filter_clause` is now a thin wrapper delegating to it, with no behavior change. Added `tests/test_sql_filters.py` covering all operators, list coercion, and column parameterization.
- Deviations: none.
- Validation: `poetry run pytest tests/test_sql_filters.py tests/test_search_metadata_filters.py tests/test_search_query_engine.py -q` -> passed (`21 passed`); full suite `poetry run pytest -q` -> passed (`903 passed`).

### TPQ-S3 Notes
- Changes made: `fetch_active_tasks` now returns `(total_matches, results)`, computing `total_matches` via a `COUNT(*)` query sharing the same WHERE-clause builder, then applying `LIMIT ? OFFSET ?` (or `LIMIT -1 OFFSET ?` when only an offset is given) to the row query; `count_only=True` short-circuits before the row fetch/decode. Added `--limit`/`--offset`/`--count-only` to `tasks query` in `matlock/cli.py`, defaulting `--limit` from `cfg.queries.default_limit`, and reshaped output to `TaskQueryResponse`. Also added inert `attribute_filters` clause support (wired to the CLI in TPQ-S4) since it required editing the same function.
- Deviations: none.
- Validation: `poetry run pytest tests/test_db_queries.py tests/test_cli_queries.py tests/test_query_models.py -q` -> passed (`24 passed`); full suite `poetry run pytest -q` -> passed (`905 passed`).

### TPQ-S4 Notes
- Changes made: added `AttributeFilterPredicate` and `parse_attribute_filter_predicate` (colon-delimited `path:operator:value`, auto-`$.`-prefixing, numeric/bool/null coercion, comma-split `in` lists) to `matlock/query_models.py`; wired repeatable `--attribute-filter` into `list_tasks` in `matlock/cli.py` (AND-composed with existing filters, distinct error message from date-predicate failures); `fetch_active_tasks` consumes `filters["attribute_filters"]` via the TPQ-S2 shared `build_json_path_filter_clause` against `t.attributes` (this clause-building was already added in TPQ-S3's edit to the same function).
- Deviations: none.
- Validation: `poetry run pytest tests/test_db_queries.py tests/test_cli_queries.py tests/test_query_models.py -q` -> passed (`32 passed`); full suite `poetry run pytest -q` -> passed (`913 passed`).

### TPQ-S5 Notes
- Changes made: none yet.
- Deviations: none.
- Validation: not yet run.

### TPQ-S6 Notes
- Changes made: none yet.
- Deviations: none.
- Validation: not yet run.

---

## Copilot Execution Protocol

1. Confirm the prior plan's pending review/commit gate (`20260831-secret-detection-backfill.md`, SDB-S4) before updating `docs/copilot/current-plan.md` to point at this plan.
2. Set the active TPQ step to `In Progress` before writing tests or code.
3. Implement only that step's scope, adding/updating tests before or alongside logic.
4. Run its focused tests, then the listed adjacent regressions and full suite; record outcomes in this plan's table and Step Notes Log.
5. Mark a step `Completed` only after validation passes, request user review approval, and commit only with explicit approval on an appropriately named branch.
6. Proceed to the next step only after the approved scoped commit.
