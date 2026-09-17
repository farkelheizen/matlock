# 20260917-documents-query Plan: Add `matlock documents query`

> Date: 9/17/2026
> Owner: Copilot
> Branch: [to be chosen after design approval]
> Related docs: `docs/matlock-cli.md`, `docs/matlock-search.md`, `docs/matlock-data-model.md`, `docs/matlock-configuration.md`, `docs/copilot/copilot-plan-template.md`

This file is a working implementation plan.

## Problem Summary

Matlock currently exposes document retrieval through `matlock search query`, whose contract is centered on the lower-level search subsystem. It supports chunk/file granularity, metadata filters, project filters, scoring, content inclusion, and a machine-facing `--stdio` transport, but its CLI surface and response shape are different from the newer `tasks query` and `projects query` commands.

The intended user-facing replacement is `matlock documents query`: a paginated document-oriented query with the same `total_matches` / `returned_matches` / `limit` / `offset` envelope used by the task and project query commands. It should support both ordinary document browsing and indexed lexical/vector retrieval, while retaining the useful chunk-level controls from `search query` and using named file options plus task-style frontmatter attributes.

## Goal

Add a document-oriented `matlock documents query` command that provides paginated, secret-safe document and chunk retrieval with metadata, project, file-state, search-mode, scoring, content, and named filtering controls, while preserving `matlock search query` as a compatibility path during migration.

## Scope

- In scope: a new `documents query` Typer command and document-oriented response models; paginated file/chunk results; the agreed core arguments; chunk-level arguments; named file filters and a task-style `--attributes` frontmatter filter; repeatable date predicates for `--created` and `--modified`; reuse of the existing search engine and secret-redaction behavior; focused tests; CLI/search/data-model documentation; changelog and release metadata updates required by the repository policy.
- In scope: a deprecation/migration path from `search query`, subject to the decisions below.
- Out of scope: changes to search indexing, chunking, embedding generation, task/project query behavior, project mapping, database schema migrations unless the resolved filter implementation proves one is required, and unrelated server behavior.

## Constraints / Requirements

- Follow the established paginated query envelope: `total_matches`, `returned_matches`, `limit`, `offset`, and `results`.
- Reuse `SearchQueryEngine` and its existing search modes rather than duplicating FTS, vector, hybrid, metadata, ranking, or secret-safe content logic.
- Preserve secret-safety guarantees: unsafe document content, matched chunks, and surrounding chunks must use the existing redaction path.
- Keep document-level results as the default. Chunk-level retrieval remains available through explicit options.
- Use parameterized SQL for every structured filter value. Do not expose a generic SQL-like expression language or execute raw SQL fragments.
- Keep filters composable: filters within a category should use the established repeated-value convention, while separate categories should use AND semantics unless a resolved decision says otherwise.
- Maintain deterministic ordering for stable pagination, including explicit tie-breakers after score ordering.
- Use Pydantic models for public request and response contracts, `pathlib` for filesystem paths, and Poetry commands for validation.
- Do not remove or silently change `matlock search query` before the migration/deprecation decision is resolved.
- Because this is a user-visible CLI/API change, the final implementation plan must include `CHANGELOG.md`, affected documentation, and any affected examples. No `examples/` directory currently exists; CLI examples belong in the relevant docs unless that changes.

---

## Implementation Plan

| Step ID | Status | Goal | Planned Changes | Test Coverage |
|---|---|---|---|---|
| DQ-S1 | Completed | Resolve the public document-query contract | Finalize command naming, response shape, compatibility policy, named file filters, frontmatter attribute grammar, and defaults; update this plan's questions section to `Design Decisions (Resolved)` | Existing contract/model tests for accepted and rejected date and attribute shapes |
| DQ-S2 | Completed | Define document and chunk result models | Add document-oriented result/envelope models while preserving or adapting existing search fields; define file metadata, project linkage, score, chunk details, and optional content | `tests/test_query_models.py`, `tests/test_documents_query_models.py`, `tests/test_search_models.py` |
| DQ-S3 | In Progress | Implement named document filters | Add named file-column options and a task-style `--attributes PATH:OPERATOR:VALUE` parser/compiler for JSON frontmatter; support repeated predicates, typed values, dates, path-prefix matching, and clear validation errors | New document-filter tests; existing `tests/test_sql_filters.py` and `tests/test_search_metadata_filters.py` regression coverage |
| DQ-S3 | Not Started | Implement named document filters | Add named file-column options and a task-style `--attributes PATH:OPERATOR:VALUE` parser/compiler for JSON frontmatter; support repeated predicates, typed values, dates, path-prefix matching, and clear validation errors | New document-filter tests; existing `tests/test_sql_filters.py` and `tests/test_search_metadata_filters.py` regression coverage |
| DQ-S4 | Not Started | Expose `matlock documents query` | Register the command, wire core arguments, chunk-level controls, filters, pagination, count-only behavior, search modes, and secret-safe output through the existing engine | New `tests/test_cli_documents_query.py`; focused engine integration tests |
| DQ-S5 | Not Started | Add migration and compatibility behavior | Implement the resolved alias/deprecation/forwarding strategy for `search query`, including `--stdio` treatment and exit-code behavior | `tests/test_cli_search_query.py`, document-query compatibility tests |
| DQ-S6 | Not Started | Document and release the new surface | Update CLI/search/data-model/configuration docs, README or examples as applicable, changelog, version metadata, docs routing, and high-level design references | Documentation scan plus focused, adjacent, and full Poetry test suite |

Status values: `Not Started` | `In Progress` | `Completed` | `Blocked`

---

## Step Details

### DQ-S1 Contract and Design Resolution
**Files (expected):**
- `docs/copilot/plans/20260917-documents-query.md`
- `docs/matlock-cli.md`
- `docs/matlock-search.md`

**Implementation notes:**
- Resolve all questions in this plan before implementation begins.
- Keep the command aligned with `tasks query` and `projects query`: `--text` is optional, `--limit` defaults from `queries.default_limit`, and `--count-only` returns no result rows.
- Use named options for file columns, just as `tasks query` uses named options for task fields. Use `--attributes PATH:OPERATOR:VALUE` for frontmatter JSON-path predicates, retaining the established attribute-filter grammar and operators.

**Definition of done:**
- Every question below has an acknowledged answer and this section is replaced with `Design Decisions (Resolved)`.

### DQ-S2 Document and Chunk Result Contracts
**Files (expected):**
- `matlock/query_models.py`
- `matlock/search/models.py` or a new document-query model module
- `tests/test_query_models.py`
- `tests/test_documents_query_models.py`

**Implementation notes:**
- Define the stable public result shape for document-level and chunk-level results.
- Candidate document fields include `file_path`, `absolute_path` or its chosen replacement, `created`, `modified`, `modified_date`, `file_ext`, `length`, `word_count`, `project_ids`, `project_id`, `super_project_id`, `has_secrets`, and `secret_detection_error`.
- Candidate search fields include `score`, `score_breakdown`, `chunk_details`, `file_details`, and optionally matched content.
- Ensure one result cannot contain both file and chunk detail payloads unless the resolved contract explicitly permits a unified shape.

**Definition of done:**
- Models validate normal, empty, count-only, metadata-only, file-level, and chunk-level response cases and reject malformed combinations.

### DQ-S3 Named Document Filters
**Files (expected):**
- `matlock/sql_filters.py`
- `matlock/search/sql_filters.py`
- `matlock/search/models.py` or a new filter model module
- `tests/test_sql_filters.py`
- `tests/test_search_metadata_filters.py`

**Implementation notes:**
- Reuse the shared JSON-path filter compiler already used by task attributes and search frontmatter metadata.
- Add only allow-listed file-column fields and operators. Candidate fields are `file_path`, `file_ext`, `created`, `modified`, `modified_date`, `length`, `word_count`, `deleted`, `is_generated`, `needs_parsing`, `search_indexed_at`, `search_index_hash`, `has_secrets`, and `secret_detection_error`.
- Support frontmatter JSON paths through the existing metadata filter machinery rather than allowing arbitrary SQL identifiers.
- Add the initial named file-column options: `--file-path`, `--file-ext`, `--created`, and `--modified`.
- `--file-path` is repeatable and each value matches the beginning of the normalized vault-relative path. Strip one or more leading `/` characters from each supplied value before matching, so `/Notes/today.md` and `Notes/today.md` address the same path prefix. Repeated path values OR within the option; distinct filter categories AND.
- `--file-ext` is repeatable and repeated extensions OR within the option; distinct filter categories AND.
- `--project-id` and `--super-project-id` are repeatable case-insensitive exact-match filters. Repeated values OR within each option; the two categories AND-compose with one another and with all other filters.
- `--created` and `--modified` are repeatable date predicates accepting `YYYY-MM-DD` or an explicit comparison operator: `=`, `>`, `>=`, `<`, or `<=`.
- Repeated date predicates for the same field AND-compose, allowing ranges such as `--modified ">=2026-01-01" --modified "<2026-02-01"`.
- Use `--attributes PATH:OPERATOR:VALUE` for frontmatter JSON-path filtering, reusing the task query grammar and supported operators.
- Validate option values, attribute paths, operators, date syntax, empty values, and incompatible combinations before issuing SQL.
- Preserve parameterization and deterministic query plans. Never concatenate user-provided SQL, column names, operators, or JSON paths without allow-list validation.

**Definition of done:**
- Named filters and attribute expressions compile to parameterized SQL, and invalid values produce actionable input errors without reaching SQLite.

### DQ-S4 `matlock documents query`
**Files (expected):**
- `matlock/cli.py`
- `matlock/query_models.py`
- `matlock/search/query_engine.py` or a focused adapter module
- `matlock/search/models.py`
- `tests/test_cli_documents_query.py`
- `tests/test_search_query_engine.py`

**Implementation notes:**
- Add the `documents` Typer group and `query` subcommand.
- Core arguments under consideration:
  - `--text`
  - `--search-mode {hybrid,fts_only,vector_only,metadata_only}`
  - `--min-score`
  - `--project-id`
  - `--super-project-id`
  - `--file-path` (normalized path-prefix matching)
  - `--attributes` (repeatable frontmatter JSON-path filter)
  - `--created`
  - `--modified`
  - `--limit`
  - `--offset`
  - `--count-only`
  - `--include-content / --no-include-content`
- Chunk-level arguments under consideration:
  - `--granularity {file,chunk}` or a document-first equivalent
  - `--surrounding-chunks INT`
- No generic SQL-like filtering argument. File-table fields use named options; frontmatter uses repeatable `--attributes PATH:OPERATOR:VALUE`.
- Empty `--text` or no text should enter metadata-only browsing mode if that remains compatible with the chosen CLI contract.
- For default document results, use file-level grouping and choose the best matching chunk as the score representative. Preserve chunk details when chunk granularity is explicitly requested.
- Apply pagination after ranking/grouping at the selected granularity, and ensure `total_matches` counts the unpaginated result set.

**Definition of done:**
- The command supports the resolved options, returns one valid paginated JSON envelope, handles metadata-only and indexed searches, and passes secret-safe content tests.

### DQ-S5 Search Query Migration
**Files (expected):**
- `matlock/cli.py`
- `matlock/search/query_cli.py`
- `tests/test_cli_search_query.py`
- `tests/test_cli_documents_query.py`

**Implementation notes:**
- Apply the resolved compatibility policy. Possible strategies are: retain `search query` unchanged; make it a deprecated forwarding alias; or remove it only in a planned breaking release.
- Treat `--stdio` as a separate compatibility concern because it is a machine-facing transport with strict stdout and deterministic exit codes.
- Do not introduce human logging into the JSON-only document-query response.

**Definition of done:**
- Existing integrations either remain unchanged or receive the explicitly documented migration behavior, with no accidental stdout or exit-code regressions.

### DQ-S6 Documentation and Release Preparation
**Files (expected):**
- `docs/matlock-cli.md`
- `docs/matlock-search.md`
- `docs/matlock-data-model.md`
- `docs/matlock-configuration.md`
- `docs/matlock-high-level-design.md`
- `README.md`
- `CHANGELOG.md`
- `pyproject.toml`
- `docs/copilot/copilot-docs-reference.md`
- `docs/copilot/current-plan.md` only when this plan is explicitly made active

**Implementation notes:**
- Document all resolved options, filter grammar, operators, result shapes, pagination, count-only behavior, content redaction, chunk details, migration behavior, and examples.
- Update version metadata according to the selected release target and repository policy. Do not run `poetry build` or `poetry publish` until the version gate is satisfied.
- Scan all `docs/**/*.md` and the root `README.md` for stale `search query` usage, outdated result contracts, and references to the new command surface.

**Definition of done:**
- Documentation and release metadata match the implemented contract, affected examples are updated, and the required doc scan is recorded.

---

## Acceptance Criteria

- `matlock documents query` exists under the `documents` command group.
- The command returns a paginated JSON envelope with accurate counts and stable ordering.
- The command supports the resolved core argument set: text search, search mode, score threshold, project/file filters, pagination, count-only, content inclusion, and metadata/date filters.
- `--created` and `--modified` accept repeatable comparison predicates, including `>YYYY-MM-DD`, `>=YYYY-MM-DD`, `<YYYY-MM-DD`, and `<=YYYY-MM-DD`, with repeated predicates AND-composed.
- The command supports the resolved chunk-level arguments, including chunk granularity and surrounding chunks.
- Named file filters and `--attributes` are parsed and validated into parameterized SQL; raw SQL injection and unknown attribute operators are rejected.
- Repeated values OR within the same option and distinct filter categories AND, matching the task/project query conventions.
- `--file-path` and `--file-ext` are repeatable. Values OR within each option, while distinct filter categories AND. Paths use normalized vault-relative path-prefix matching and strip leading `/` characters from supplied values.
- `--project-id` and `--super-project-id` are repeatable case-insensitive exact matches. Values OR within each option; project and super-project filters AND-compose.
- Metadata-only document browsing works without a search query and does not require embeddings.
- FTS, vector, and hybrid retrieval reuse existing search behavior and error handling.
- Unsafe document content and chunk context remain redacted through the existing secret-safe path.
- `matlock search query` follows the resolved compatibility/migration policy, including strict `--stdio` behavior.
- Existing task, project, indexing, and search-engine behavior has no unintended regression.
- `CHANGELOG.md` has Added / Changed / Fixed entries for the release as applicable.
- Affected examples and documentation are updated; the full documentation scan is recorded.
- Version metadata and the pre-publish gate follow repository policy.

## Risks / Notes

- The existing search response already contains much of the needed data, but its file result shape is search-centric and does not expose every useful `file` table field. A thin adapter may be safer than changing the established `matlock.search.response.v1` contract.
- Named options and the task-style attribute grammar avoid creating a second general-purpose query language while still supporting structured filtering.
- Chunk-level output increases response size and can expose more nearby content. Existing redaction must apply equally to matched and surrounding chunks.
- File-level pagination must happen after chunk grouping and score selection; paginating raw chunks first can omit relevant documents or produce unstable counts.
- Vector and hybrid modes depend on the configured embedding provider. Metadata-only and FTS-only tests should remain deterministic and should cover the command independently of external model availability.
- Retiring `search query` immediately could break editor/agent integrations using `--stdio`; migration should be explicit and versioned.

## Validation Plan

Run tests in this order:
1. Focused contract/filter tests: `poetry run pytest tests/test_query_models.py tests/test_sql_filters.py tests/test_search_metadata_filters.py -q`
2. Focused document command tests: `poetry run pytest tests/test_cli_documents_query.py tests/test_search_query_engine.py -q`
3. Compatibility regressions: `poetry run pytest tests/test_cli_search_query.py tests/test_cli_queries.py tests/test_search_stdio_contract.py -q`
4. Adjacent regressions: `poetry run pytest tests/test_db_queries.py tests/test_search_chunking.py tests/test_search_embedding_provider.py tests/test_server_search_indexing.py -q`
5. Full suite: `poetry run pytest`
6. Documentation scan: search every Markdown file under `docs/` and the root `README.md` for old command usage, response-shape claims, and stale version references.

Record results:
- Focused: [not run; plan only]
- Regression: [not run; plan only]
- Full suite: [not run; plan only]

---

## Design Decisions (Resolved)

**Q1 — Should there be a generic SQL-like `--filter` option? Resolved: no.**

Do not expose a generic SQL-like expression language. Use named options for file-table fields and repeatable `--attributes PATH:OPERATOR:VALUE` for frontmatter JSON-path filters, matching `tasks query`.

**Q2 — Which named file filters are required in the first release? Resolved: initial field set selected.**

The initial named file options are repeatable `--file-path` and `--file-ext`, plus repeatable `--created` and `--modified`. The existing repeatable `--project-id` and `--super-project-id` filters are also included, using case-insensitive exact matching. Size, indexing-state, generated-file, and secret-state filters are deferred. Path, extension, project, and super-project values OR within their respective options. `--created` and `--modified` accept `YYYY-MM-DD`, `=YYYY-MM-DD`, `>YYYY-MM-DD`, `>=YYYY-MM-DD`, `<YYYY-MM-DD`, and `<=YYYY-MM-DD`; repeated date predicates for one field AND-compose.

**Q3 — What should the public result shape be? Resolved: use a new document-oriented model.**

Add a new document-query result model and keep `matlock.search.response.v1` unchanged, with file-level results by default and optional `chunk_details` when chunk granularity is selected.

**Q4 — How should `--granularity` and chunk controls work? Resolved: retain the existing controls.**

Retain `--granularity {file,chunk}` for migration compatibility, defaulting to `file`, and retain `--surrounding-chunks` when `chunk` granularity is selected.

**Q5 — Should `--text` be required for non-metadata modes? Resolved: fall back to metadata-only.**

When `--text` is absent or empty, execute metadata-only browsing even if a non-metadata search mode was requested, matching the existing request model's normalization behavior.

**Q6 — What does `--file-path` match? Resolved: normalized path prefix.**

Each value matches the beginning of the vault-relative path. Strip all leading `/` characters before matching. Repeated values OR together; no wildcard semantics are implied.

**Q7 — Which files are included by default? Resolved: active, non-generated files.**

Match the search engine default of active, non-generated files only. Administrative inclusion flags are deferred unless separately requested.

**Q8 — What is the `search query` retirement policy? Resolved: deprecate, then remove at the next major.**

Add `documents query`, deprecate `search query`, and remove it at the next major release. The `--stdio` transport remains a compatibility concern and must not lose its strict JSON/exit-code behavior during the deprecation period.

**Q9 — What is the release target? Resolved: next major release.**

This feature and the eventual `search query` removal target the next major release. The exact semantic version remains to be set by release planning.

**Q10 — Should `documents` be singular or plural elsewhere? Resolved: keep both groups for now.**

Add plural `documents query` without moving `document read` in this work, avoiding an unrelated compatibility break.

---

## Step Notes Log (update as work progresses)

### DQ-S1 Notes
- Changes made: Recorded the resolved public contract, including the plural `documents query` command, named filters, metadata-only fallback, default file granularity, and deprecated `search query` policy.
- Deviations: None.
- Validation: `poetry run pytest tests/test_query_models.py -q` passed.

### DQ-S2 Notes
- Changes made: Added `DocumentRecord`, `DocumentChunkDetails`, `DocumentFileDetails`, and `DocumentQueryResponse` with mutually exclusive detail validation.
- Deviations: None.
- Validation: `poetry run pytest tests/test_documents_query_models.py tests/test_query_models.py tests/test_search_models.py -q` passed (30 tests).

### DQ-S3 Notes
- Changes made: In progress.
- Deviations: None.
- Validation: Not run.

### DQ-S4 Notes
- Changes made: Not started.
- Deviations: None.
- Validation: Not run.

### DQ-S5 Notes
- Changes made: Not started.
- Deviations: None.
- Validation: Not run.

### DQ-S6 Notes
- Changes made: Not started.
- Deviations: None.
- Validation: Not run.

---

## Copilot Execution Protocol

When Copilot uses this plan:
1. Set current step to `In Progress` before coding.
2. Implement only the current step scope.
3. Run listed tests for the step.
4. Update step status to `Completed` (or `Blocked`) with notes.
5. Continue to next step only after validation is recorded.
