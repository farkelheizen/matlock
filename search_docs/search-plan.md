# 20260808-search-plan Plan: Matlock Search Integration

> Date: 8/8/2026
> Owner: Copilot
> Branch: TBD (implementation not started)
> Related docs:
> - docs/matlock-high-level-design.md
> - docs/matlock-pipeline-specification.md
> - docs/matlock-data-model.md
> - docs/matlock-configuration.md
> - docs/matlock-cli.md
> - /Volumes/Lab1/Obsidian/Bill2/Matlock/search/Matlock Search - API Specification.md
> - /Volumes/Lab1/Obsidian/Bill2/Matlock/search/Matlock Search - CLI Specification.md
> - /Volumes/Lab1/Obsidian/Bill2/Matlock/search/Matlock Search - Configuration Specification.md
> - /Volumes/Lab1/Obsidian/Bill2/Matlock/search/Matlock Search - Database Schema.md
> - /Volumes/Lab1/Obsidian/Bill2/Matlock/search/Matlock Search - Resilience and Edge Cases Specification.md
> - /Volumes/Lab1/Obsidian/Bill2/Matlock/search/Matlock Search - Third-Party Library Security Evaluation.md

This file is a planning artifact only. No implementation code is included.

## Problem Summary

Matlock currently ships a five-stage pipeline (`sync`, `parse`, `map-projects`, `rollup`, `report`) plus discovery and server orchestration, but it does not have a native local-first search subsystem.

The new "Matlock Search" feature set introduces:
- local indexing into SQLite-backed chunk, FTS, and vector structures,
- hybrid retrieval (metadata + FTS + vector),
- strict machine-to-machine JSON query transport over STDIO for MCP usage,
- query-time output shaping suitable for Copilot and similar agents.

Integrating this safely requires additive schema evolution, new CLI flows, optional embedding dependencies, and strict output/logging isolation so current CLI behavior is preserved.

## Goal

Integrate Matlock Search as a first-class, optional capability that supports:
- `matlock search index` for incremental indexing,
- `matlock search query` for human and machine modes,
- strict STDIO JSON contracts for MCP use,
- complete tests and updated project documentation,
while preserving existing pipeline behavior and backward compatibility.

## Scope

- In scope:
  - Search configuration schema and validation
  - Search request/response contracts
  - SQLite schema/migration changes for search
  - Indexing pipeline (chunking, embeddings, incremental refresh, orphan cleanup)
  - Query engine (metadata-only, FTS-only, vector-only, hybrid)
  - CLI (`matlock search index`, `matlock search query`) including STDIO mode
  - Logging/telemetry hardening for STDIO safety
  - Docs and changelog updates
  - Test coverage across config, db, index, query, and CLI

- Out of scope:
  - External hosted vector DB integrations (Pinecone/Qdrant/etc.)
  - Non-SQLite search backends
  - UI/frontend search interface
  - Automatic model fine-tuning or advanced ranking learning

## Constraints / Requirements

- Maintain backward compatibility for existing commands and tests.
- Use Poetry for dependency and test execution.
- Keep search indexing optional (no mandatory behavior change for users not using search).
- Preserve strict STDIO contract in machine mode: no non-JSON stdout output.
- Route operational logs away from stdout in STDIO mode.
- Prefer additive schema migrations; avoid destructive migration behavior.
- Design decision gate is resolved and captured in Design Decisions (Resolved).
- Server integration must include an optional continuous background indexing mode.

---

## Phase Status Overview

| Phase ID | Status | Objective | Exit Criteria |
|---|---|---|---|
| MS-P0 | Completed | Lock design decisions and integration boundaries | All open questions resolved; `Questions / Concerns` replaced with `Design Decisions (Resolved)` |
| MS-P1 | Completed | Add search config and API contract models | Config and request/response schemas validated by tests |
| MS-P2 | Completed | Add DB schema, migrations, and helper APIs for search | Search tables/triggers/migrations pass db tests |
| MS-P3 | Completed | Implement indexing engine and CLI index/server integration | Incremental and forced indexing work end-to-end, including optional continuous server indexing |
| MS-P4 | Not Started | Implement query engine and result shaping | All search modes + filters + granularity behave per spec |
| MS-P5 | Not Started | Harden CLI transport, exit codes, and logging isolation | STDIO mode is parser-safe and error-code stable |
| MS-P6 | Not Started | Complete docs, changelog, and final regression validation | Docs/changelog updated and full test suite passes |

---

## Implementation Plan

| Step ID | Phase | Status | Goal | Planned Changes | Test Coverage |
|---|---|---|---|---|---|
| MS-P0-S1 | MS-P0 | Completed | Baseline audit and plan authoring | Capture architecture gaps, integration touchpoints, and phased rollout plan in this file | N/A (planning step) |
| MS-P0-S2 | MS-P0 | Completed | Resolve design ambiguities | Resolve all questions below; replace with `Design Decisions (Resolved)` | N/A (decision gate) |
| MS-P1-S1 | MS-P1 | Completed | Add search config and contract models | Update `matlock/config.py` with `search` block models; add search request/response Pydantic models in new search module; define defaults and validators | `tests/test_matlock_config.py`, new `tests/test_search_models.py` |
| MS-P2-S1 | MS-P2 | Completed | Add search schema + migrations | Extend `matlock/db.py` schema/migration for file search tracking columns and search tables/triggers; add db helper functions | `tests/test_db.py`, new `tests/test_db_search_schema.py` |
| MS-P3-S1 | MS-P3 | Completed | Build chunking + embedding + indexing core | Add search indexing modules (chunker, embedding provider abstraction, index coordinator, frontmatter template injection, per-file overrides, cleanup passes) | new `tests/test_search_chunking.py`, `tests/test_search_indexer.py`, `tests/test_search_embedding_provider.py` |
| MS-P3-S2 | MS-P3 | Completed | Expose indexing via CLI and orchestration hooks | Add `matlock search index`; add `--index-search` to `run-all`; add server indexing options including continuous background indexing mode; enforce no behavior change unless opted in | new `tests/test_cli_search_index.py`, updates to `tests/test_cli_run_all.py`, `tests/test_cli_server.py`, new `tests/test_server_search_indexing.py` |
| MS-P4-S1 | MS-P4 | Not Started | Build query execution engine | Implement metadata filter SQL mapping, FTS/vector/hybrid ranking, project filters, and chunk/file output shaping | new `tests/test_search_query_engine.py`, `tests/test_search_metadata_filters.py`, `tests/test_search_output_shape.py` |
| MS-P5-S1 | MS-P5 | Not Started | Deliver CLI query transport hardening | Add `matlock search query` human mode and `--stdio` mode, strict stdout purity, deterministic exit code mapping, structured error payloads, isolated search logging | new `tests/test_cli_search_query.py`, new `tests/test_search_stdio_contract.py`, new `tests/test_search_logging.py` |
| MS-P6-S1 | MS-P6 | Not Started | Documentation, changelog, and final validation | Update docs and changelog, run focused/regression/full suites, record validation outcomes in step log | docs updates + `CHANGELOG.md`; full `poetry run pytest` |

Status values: `Not Started` | `In Progress` | `Completed` | `Blocked`

---

## Step Details

### MS-P0-S1 Baseline Audit and Plan Authoring

**Files (actual):**
- search_docs/search-plan.md

**Implementation notes:**
- Analyze existing CLI/config/db/test architecture.
- Reconcile new search specs with current Matlock command and schema model.
- Produce an implementation plan with explicit phases, tests, and docs impact.

**Definition of done:**
- Plan file exists with phased steps, statuses, and open questions.

### MS-P0-S2 Resolve Design Ambiguities (Gate)

**Files (expected):**
- search_docs/search-plan.md

**Implementation notes:**
- Resolve every item in `Questions / Concerns`.
- Replace `Questions / Concerns` with `Design Decisions (Resolved)`.
- Confirm no implementation begins before this gate is complete.

**Definition of done:**
- All design decisions documented and approved.

**Decision outcome summary:**
- Search remains under a dedicated CLI group (`matlock search ...`).
- `run-all` gains opt-in indexing (`--index-search`).
- `server` gains search indexing support including an opt-in continuous background indexing path.
- Query mode must enforce strict JSON-only stdout in `--stdio` mode.

### MS-P1-S1 Search Config and Contract Models

**Files (expected):**
- matlock/config.py
- matlock/search/models.py (new)
- tests/test_matlock_config.py
- tests/test_search_models.py (new)

**Implementation notes:**
- Add `search.indexing`, `search.chunking`, and `search.embedding` config sections with defaults.
- Add request/response Pydantic models for `matlock.search.v1` and `matlock.search.response.v1`.
- Add request normalization rules (for example, implicit `metadata_only` when query missing).
- Support environment overrides agreed in design decisions.

**Definition of done:**
- Config and request/response validation pass for all required and edge scenarios.

### MS-P2-S1 Search Schema and Migrations

**Files (expected):**
- matlock/db.py
- tests/test_db.py
- tests/test_db_search_schema.py (new)

**Implementation notes:**
- Add file-level search freshness tracking fields.
- Create `search_chunks`, `search_fts`, and `search_vec` structures (final shape per design decisions).
- Add synchronization triggers and helper operations for insert/update/delete cleanup.
- Implement additive migrations compatible with existing DBs.

**Definition of done:**
- Fresh and migrated databases both satisfy schema requirements; trigger behavior is verified.

### MS-P3-S1 Search Indexing Core

**Files (expected):**
- matlock/search/chunking.py (new)
- matlock/search/embedding.py (new)
- matlock/search/indexer.py (new)
- matlock/search/frontmatter_template.py (new)
- tests/test_search_chunking.py (new)
- tests/test_search_indexer.py (new)
- tests/test_search_embedding_provider.py (new)

**Implementation notes:**
- Implement "greedy but polite" chunking with markdown-aware fallback splitting.
- Add optional frontmatter/context injection with namespace-safe templates.
- Implement per-file override handling (`force_index`, `exclude`).
- Implement update purge and orphan purge passes.

**Definition of done:**
- Indexing produces deterministic chunk records, handles re-index and deletion cleanup correctly, and supports selected embedding provider.

### MS-P3-S2 CLI Index Command and Pipeline Hooks

**Files (expected):**
- matlock/cli.py
- matlock/stages/search_index.py (new) or matlock/search/indexer.py entrypoint wiring
- matlock/server.py
- tests/test_cli_search_index.py (new)
- tests/test_cli_run_all.py
- tests/test_cli_server.py
- tests/test_server_search_indexing.py (new)

**Implementation notes:**
- Add `matlock search index` command with `--force`, `--batch-size`, and optional model override behavior.
- Add `--index-search` flag to `run-all` for opt-in indexing after core parse/map orchestration (exact sequence finalized in step design).
- Add server indexing options:
  - startup opt-in indexing hook for parity with current forced startup behaviors,
  - continuous background indexing mode (non-stop) that safely coexists with watcher/debouncer/scheduler and avoids concurrent DB contention.
- Ensure all progress/status output policy is compatible with future programmatic invocation.

**Definition of done:**
- Search indexing runs standalone, via `run-all --index-search`, and via server options including continuous background indexing mode.

### MS-P4-S1 Query Engine and Result Shaping

**Files (expected):**
- matlock/search/query_engine.py (new)
- matlock/search/sql_filters.py (new)
- matlock/search/ranking.py (new)
- tests/test_search_query_engine.py (new)
- tests/test_search_metadata_filters.py (new)
- tests/test_search_output_shape.py (new)

**Implementation notes:**
- Implement metadata filter operator mapping to SQLite JSON functions.
- Implement FTS, vector, hybrid, and metadata-only execution strategies.
- Implement output granularity handling (`chunk` vs `file`) and adjacent chunk retrieval.
- Populate stats and score breakdown fields per response contract.

**Definition of done:**
- Query engine returns validated responses for all supported modes and filter combinations.

### MS-P5-S1 Query CLI Transport, Exit Codes, and Logging Isolation

**Files (expected):**
- matlock/cli.py
- matlock/search/query_cli.py (new)
- matlock/search/logging.py (new)
- matlock/logging_setup.py (if shared behavior needs extension)
- tests/test_cli_search_query.py (new)
- tests/test_search_stdio_contract.py (new)
- tests/test_search_logging.py (new)

**Implementation notes:**
- Add `matlock search query` with human-friendly mode and strict `--stdio` machine mode.
- In `--stdio` mode: read JSON from stdin, write JSON only to stdout, route non-fatal logs away from stdout.
- Add deterministic exit code handling:
  - `0` success
  - `1` input/schema errors
  - `2` database/search storage errors
  - `3` embedding/provider errors
- Ensure structured error object is included in response payload on failures where applicable.

**Definition of done:**
- MCP-style subprocess calls can parse output reliably and branch on exit code semantics.

### MS-P6-S1 Docs, Changelog, and Validation

**Files (expected):**
- docs/matlock-cli.md
- docs/matlock-configuration.md
- docs/matlock-data-model.md
- docs/matlock-pipeline-specification.md
- docs/matlock-high-level-design.md
- docs/copilot/copilot-docs-reference.md
- README.md
- CHANGELOG.md

**Implementation notes:**
- Document new command group, config schema, schema objects, and operational behavior.
- Add or update any architecture references and docs routing map entries.
- Update changelog under `Unreleased` with Added/Changed/Fixed notes.
- Validate focused, regression, and full tests via Poetry.

**Definition of done:**
- Documentation accurately reflects implemented search behavior; validation results are recorded.

---

## Acceptance Criteria

- `matlock search index` supports incremental and forced indexing and honors per-file exclusion/force flags.
- `matlock run-all --index-search` executes indexing as an explicit opt-in path.
- `matlock server` supports opt-in startup indexing and opt-in continuous background indexing mode.
- `matlock search query` supports `metadata_only`, `fts_only`, `vector_only`, and `hybrid` modes.
- `--stdio` mode emits strict JSON on stdout and deterministic exit codes.
- Search indexing cleanly handles updates and deleted-file orphans.
- Search filters (`project_id`, date bounds, metadata operators) produce correct results.
- Existing non-search commands retain current behavior and remain backward compatible.
- Search-related test suite passes, and existing regression tests remain green.
- Documentation under `docs/` and `README.md` is updated to match final implementation.
- `CHANGELOG.md` is updated under `Unreleased`.

## Risks / Notes

- SQLite FTS external-content and vector extension details may require schema adjustments from draft specs.
- Embedding dependency footprint and model download behavior can affect offline reliability and CI stability.
- Strict STDIO purity requires careful handling of logger setup and all print paths.
- Hybrid ranking calibration (`hybrid_alpha`, thresholds) may need tuning with real vault data.
- Existing data model uses `file_path` as primary key; search schema assumptions using file IDs must be reconciled.

## Validation Plan

Run tests in this order for implementation phases:

1. Focused tests for the active step.
- Example: `poetry run pytest tests/test_search_models.py`
- Example: `poetry run pytest tests/test_db_search_schema.py`
- Example: `poetry run pytest tests/test_cli_search_query.py`

2. Adjacent/regression tests for touched integration surfaces.
- Example: `poetry run pytest tests/test_cli_run_all.py tests/test_cli_server.py tests/test_matlock_config.py tests/test_db.py`

3. Full suite.
- `poetry run pytest`

Record results in Step Notes Log before marking a step `Completed`.

---

## Design Decisions (Resolved)

All design questions from the initial plan were accepted using the proposed recommendations, with one explicit requirement added by the user: search indexing must be available as a server option and support continuous background indexing behavior.

**D1 Command topology**
- Decision: implement search as a Typer command group (`matlock search ...`) with a dedicated search sub-app.

**D2 Pipeline integration point**
- Decision: add `--index-search` to `run-all`.
- Decision: add search indexing support to `server`.
- Requirement: server mode must support continuous/non-stop background indexing behavior, not only one-time startup indexing.

**D3 Search table key strategy**
- Decision: use `file_path` as the `search_chunks.file_id` reference to stay aligned with the current `file` table primary key.

**D4 FTS row identity constraints**
- Decision: introduce an integer surrogate row key for FTS row identity while keeping stable string `chunk_id` for external references.

**D5 Vector storage schema compatibility**
- Decision: validate `sqlite-vec` table shape and join semantics early in MS-P2 before broad implementation.

**D6 Dependency strategy for embeddings**
- Decision: use optional/lazy imports for `fastembed` and `sqlite-vec` with clear, actionable error messages when unavailable.

**D7 Embedding defaults and overrides**
- Decision: apply env-var overrides only for explicitly declared keys; otherwise use config defaults.

**D8 Time format normalization**
- Decision: keep DB storage unchanged and normalize to RFC3339 datetime strings at response serialization time.

**D9 Project filter semantics**
- Decision: implement `exact` project matching first.
- Decision: defer fuzzy/vector/hybrid project resolution behavior behind a later milestone unless required by immediate acceptance criteria.

**D10 Metadata operator behavior**
- Decision: support scalar and array forms consistently for `contains` and `in`, with explicit coercion rules and tests.

**D11 Chunking strategy scope for v1**
- Decision: implement `fixed_token` first with markdown-aware "greedy but polite" splitting.
- Decision: defer `markdown_header` strategy unless it can be added with low risk/effort inside the same phase.

**D12 STDIO error payload contract**
- Decision: in malformed stdin/error conditions, return valid deterministic JSON error payloads whenever possible before exiting with code `1`.

**D13 Logging isolation**
- Decision: use a dedicated search logger/handler path for query mode to enforce strict stdout isolation.

**D14 Search index scope boundaries**
- Decision: exclude generated files (`is_generated=1`) and soft-deleted files from indexing.

**D15 Frontmatter template safety**
- Decision: missing namespaced template values resolve to empty string.

**D16 Performance testing policy**
- Decision: avoid brittle wall-clock assertions in unit tests; prioritize correctness tests and optional benchmark scripts.

**D17 Documentation strategy**
- Decision: add a dedicated `docs/matlock-search.md` and also update existing core docs with cross-references and integration details.

---

## Autonomous Execution Framework

This framework is designed for long-running, low-supervision implementation with strong git hygiene.

### Operating Mode

- Mode A (default-safe): supervised commits and PR actions with explicit approval at each gate.
- Mode B (unattended): pre-authorized commit and PR workflow for this plan only, with automatic quality gates and rollback rules.
- Recommended for your goal: Mode B with strict guardrails, then final human acceptance review.

### Git Hygiene Strategy

- Branch model:
  - Integration branch: `feat/search-integration`
  - Step branches: `feat/search-ms-p1-s1`, `feat/search-ms-p2-s1`, etc.
- Commit cadence:
  - Minimum one commit per completed step.
  - Additional checkpoint commit every 60 to 90 minutes when a step is large.
  - Commit message format: `feat(search): <short outcome> (MS-Px-Sy)`.
- PR strategy:
  - One PR per step for maximum traceability, or
  - Stacked PRs per phase when dependency chains are tight.
  - Always include validation summary and touched-step IDs in PR body.

### Quality Gates For Unattended Runs

- Gate 1: focused tests for active step must pass.
- Gate 2: adjacent regression tests must pass.
- Gate 3: full test suite must pass before phase-level merge.
- Gate 4: step notes log and status table must be updated in this plan before commit.
- Gate 5: docs and changelog updates must be included when behavior changes.

### Agent Topology

- Orchestrator agent:
  - Owns sequencing, branch transitions, status updates, and handoffs.
  - Enforces gate policy and blocks merges on failing validation.
- Worker agents (step-owned):
  - Foundation worker: MS-P1-S1 and MS-P2-S1.
  - Indexing worker: MS-P3-S1 and MS-P3-S2.
  - Query/transport worker: MS-P4-S1 and MS-P5-S1.
  - Docs/release worker: MS-P6-S1.
- Optional sub-agent use:
  - Use read-only Explore sub-agent for broad codebase discovery.
  - Keep write authority with step-owned worker agents to avoid overlap conflicts.

### Handoff Contract

Every handoff must include:
- Active step ID and status change.
- Files changed.
- Tests run and results.
- Open risks/blockers.
- Exact next action for the receiving agent.

### Server Background Indexing Requirement

Because continuous indexing is mandatory for your workflow, unattended execution must include:
- explicit server option design for non-stop background indexing,
- concurrency safety validation with watcher/debouncer/scheduler,
- regression coverage for startup and continuous indexing modes.

### Kickoff Checklist For Multi-Hour Runs

- Confirm Mode B pre-authorization for commit/PR actions on this plan.
- Start orchestrator agent on `feat/search-integration`.
- Execute step sequence MS-P1-S1 through MS-P6-S1 with worker handoffs.
- Enforce automatic gates at each step before commit.
- Open or update PR(s) continuously.
- Return final run summary with:
  - completed steps,
  - final test results,
  - PR links,
  - remaining follow-ups (if any).

---

## Step Notes Log (update as work progresses)

### MS-P0-S1 Notes
- Changes made: Authored phased integration plan in `search_docs/search-plan.md`.
- Deviations: None.
- Validation: N/A (planning only).

### MS-P0-S2 Notes
- Changes made: Replaced `Questions / Concerns` with `Design Decisions (Resolved)` and recorded accepted decisions D1-D17.
- Deviations: Added explicit requirement that `server` supports continuous/non-stop background indexing behavior.
- Validation: Decision gate completed by user approval.

### MS-P1-S1 Notes
- Changes made: Added `search.indexing`, `search.chunking`, and `search.embedding` config models with defaults and validators in `matlock/config.py`; added new `matlock/search/models.py` request/response contracts for `matlock.search.v1` and `matlock.search.response.v1`; added focused coverage in `tests/test_matlock_config.py` and new `tests/test_search_models.py`.
- Deviations: Added explicit companion override key `search.embedding.api_base_url_env_var` and a non-serialized resolved `api_key` field so environment overrides remain opt-in and limited to declared keys only.
- Validation: `poetry run pytest tests/test_search_models.py tests/test_matlock_config.py` -> 54 passed; `poetry run pytest tests/test_config.py tests/test_db.py` -> 44 passed.

### MS-P2-S1 Notes
- Changes made: Extended `matlock/db.py` with additive file search freshness columns, idempotent search schema creation (`search_chunks`, `search_fts`, `search_vec`), FTS/file cleanup triggers, search indexing helper APIs, and a compatibility-safe `upsert_file` path that preserves search freshness when file content hash is unchanged. Added focused coverage in new `tests/test_db_search_schema.py` and updated `tests/test_db.py` for the expanded schema baseline.
- Deviations: Implemented `search_vec` as an additive relational table keyed by `chunk_id` so existing SQLite databases initialize cleanly without requiring a loaded `sqlite-vec` extension during MS-P2-S1.
- Validation: `poetry run pytest tests/test_db_search_schema.py tests/test_db.py` -> 45 passed; `poetry run pytest tests/test_matlock_config.py tests/test_config.py` -> 51 passed.

### MS-P3-S1 Notes
- Changes made: Added `matlock/search/chunking.py` for fixed-token markdown-aware chunking with header/paragraph/line fallback, overlap handling, and code-fence closure protection; added `matlock/search/frontmatter_template.py` for namespace-safe template rendering with empty-string fallback on missing values; added `matlock/search/embedding.py` with lazy `fastembed` loading plus an `openai-compatible` provider wrapper; added `matlock/search/indexer.py` to coordinate stale-file selection, per-file `force_index` and `exclude` overrides, frontmatter/context injection, chunk replacement, embedding persistence, freshness updates, batched commits, and orphan cleanup. Added focused coverage in new `tests/test_search_chunking.py`, `tests/test_search_indexer.py`, and `tests/test_search_embedding_provider.py`.
- Deviations: Kept `markdown_header` chunking out of scope for MS-P3-S1 and left existing CLI/server wiring untouched for MS-P3-S2. Reused the existing `search_vec` relational table shape from MS-P2-S1, so provider output is stored as JSON-encoded blobs without introducing `sqlite-vec` runtime requirements yet.
- Validation: `poetry run pytest tests/test_search_chunking.py tests/test_search_indexer.py tests/test_search_embedding_provider.py` -> 10 passed; `poetry run pytest tests/test_db.py tests/test_db_search_schema.py tests/test_matlock_config.py` -> 89 passed; `poetry run pytest tests/test_cli_run_all.py tests/test_cli_server.py` -> 52 passed; `poetry run pytest` -> 787 passed.

### MS-P3-S2 Notes
- Changes made: Added a `search` CLI group with `matlock search index`, including `--force`, `--batch-size`, and `--model` overrides wired through new `matlock/stages/search_index.py`. Added opt-in `--index-search` to `run-all` so search indexing runs after report generation without changing default pipeline behavior. Extended `matlock/server.py` with startup search indexing and a continuous background search indexer thread that shares the existing `_pipeline_lock` with watcher, debouncer, and scheduler execution. Added focused coverage in new `tests/test_cli_search_index.py` and `tests/test_server_search_indexing.py`, plus updated `tests/test_cli_run_all.py` and `tests/test_cli_server.py`.
- Deviations: Reused the existing server-wide pipeline lock to serialize continuous search indexing against watcher/debouncer/scheduler database work instead of introducing a second search-specific concurrency primitive.
- Validation: `poetry run pytest tests/test_cli_search_index.py tests/test_server_search_indexing.py` -> 9 passed; `poetry run pytest tests/test_cli_run_all.py tests/test_cli_server.py tests/test_search_indexer.py tests/test_db.py` -> 101 passed; `poetry run pytest` -> 803 passed.

### MS-P4-S1 Notes
- Changes made: Pending.
- Deviations: Pending.
- Validation: Pending.

### MS-P5-S1 Notes
- Changes made: Pending.
- Deviations: Pending.
- Validation: Pending.

### MS-P6-S1 Notes
- Changes made: Pending.
- Deviations: Pending.
- Validation: Pending.

---

## Copilot Execution Protocol for This Plan

1. Set active step status to `In Progress` before coding.
2. Implement only the active step scope.
3. Run step-focused tests first.
4. Update step status and notes log with validation evidence.
5. Do not start the next step until current step is validated.
6. Use the Autonomous Execution Framework above for branch/commit/PR hygiene during unattended runs.
