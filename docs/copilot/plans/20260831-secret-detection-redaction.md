# 20260831-secret-detection-redaction Plan: Secret Detection and Redaction

> Date: 8/31/2026
> Owner: Copilot
> Branch: feature branches created after each phase receives review approval
> Related docs: `Secret Detection in Matlock.md`, `Copilot Implementation Plan - Secret Detection and Redaction.md`, `docs/matlock-pipeline-specification.md`, `docs/matlock-data-model.md`, `docs/matlock-search.md`

## Problem Summary

Matlock currently stores and returns Markdown document content without recognizing potential credentials and tokens. Search responses, including `--include-content`, can therefore expose secrets to users and downstream agents.

This feature introduces fail-closed detection during parsing, persistent document-level state, cached redacted copies, a safe document-reading command, and protected search indexing and results. Existing SQLite databases must upgrade without destructive migration.

## Goal

Release Matlock 0.5.0 with `detect-secrets`-based detection and safe document/search content delivery while preserving normal behavior for clean documents and parser progress when scanning fails.

## Scope

- In scope: `detect-secrets`; `cache.redacted_dir`; file secret columns; parser scanning; redaction cache; `doc-read`; protected indexing/results; tests; release and docs.
- Out of scope: source-file modification, persisted secret values/findings, configurable detector plugins, a new pipeline stage, secret filters, and a `doc-read` machine protocol.

## Constraints / Requirements

- Use Poetry for dependencies and validation.
- New logic has simultaneous or prior test coverage.
- `has_secrets`: `None` is unscanned legacy state, `False` is clean, `True` is a finding or scanner failure. `secret_detection_error` is set only for scanner failures.
- A finding exists only when `SecretsCollection.json()["results"]` is non-empty; `bool(SecretsCollection.json())` is invalid because plugin metadata makes it truthy for clean files.
- Scan failures do not poison or skip an otherwise parseable file. They fail closed, persist metadata, and clear `needs_parsing`.
- Cache paths expand `~` and are created only on first redaction request.
- Unsafe document content must never be returned or newly indexed. A read-time scan failure returns a fully redacted placeholder without caching it.
- Redaction uses an exact in-memory finding value when safely available; otherwise it replaces the reported line with `[REDACTED]`.
- Cache key is the raw-byte SHA-256 and contents must not contain a secret.
- Each phase requires focused, adjacent, full-suite validation, recorded notes, review, user approval, then a commit before the following phase begins.

---

## Implementation Plan

| Step ID | Status | Goal | Planned Changes | Test Coverage |
|---|---|---|---|---|
| SDR-S1 | Completed | Establish dependency, configuration, and persistent contract | Dependency/lock, cache config, file migration/upsert behavior, search result fields, active-plan tracking | `test_config.py`, `test_matlock_config.py`, `test_db.py`, `test_db_search_schema.py`, `test_search_models.py`, `test_search_query_engine.py` |
| SDR-S2 | Completed | Scan current parsed documents safely | Shared detector adapter and parse integration | `test_redaction.py`, `test_parse.py` |
| SDR-S3 | Completed | Build redaction cache and protect search | Cache helper, search invalidation/index/query protection | `test_redaction.py`, `test_search_indexer.py`, `test_query_engine.py`, `test_cli_search_query.py`, `test_db_search_schema.py` |
| SDR-S4 | Completed | Deliver safe direct file reads | Top-level `doc-read` and legacy-state update | `test_cli_doc_read.py` |
| SDR-S5 | Completed | Ship the versioned feature | 0.5.0 metadata, changelog, docs scan | full suite and metadata checks |

Status values: `Not Started` | `In Progress` | `Completed` | `Blocked`

---

## Step Details

### SDR-S1 Foundation, Configuration, and Schema
**Files (expected):** `pyproject.toml`, `poetry.lock`, `matlock/config.py`, `config/default_config.json`, `matlock/db.py`, `matlock/search/models.py`, and the listed focused tests.

**Implementation notes:** Add frozen `CacheConfig` with an expanded default `~/.matlock/cache/redacted`. Add nullable `has_secrets` and `secret_detection_error` to `file` DDL and idempotent migration. New records are unknown; unchanged sync upserts preserve a completed state, while changed, deleted, or generated rows clear both fields. Map the fields through `SearchResponseResult` and each query projection.

**Definition of done:** Config has tested defaults/overrides, new and legacy schemas expose the columns, upsert invalidation is correct, and search result JSON includes all three state values.

### SDR-S2 Parse-Time Detection
**Files (expected):** new `matlock/redaction.py`, `matlock/stages/parse.py`, `tests/test_redaction.py`, `tests/test_parse.py`.

**Implementation notes:** Add a typed `detect-secrets` adapter with an in-memory normalized finding representation. Scan only after source extraction succeeds; scanner errors become fail-closed metadata and do not interact with parse poison state.

**Definition of done:** Clean/finding/error scans are persisted correctly and extraction behavior remains isolated.

### SDR-S3 Redaction Cache and Search Protection
**Files (expected):** `matlock/redaction.py`, `matlock/db.py`, `matlock/search/indexer.py`, `matlock/search/query_engine.py`, and listed tests.

**Implementation notes:** Cache redacted source by raw SHA-256. Do not cache scan-failure placeholders. Delete raw chunks on hash changes. Index unsafe files from redacted content, and sanitize query-time chunk, neighbor, and full-file content for unsafe or legacy files.

**Definition of done:** Test secret text is absent from cache, indexed chunks, human output, and JSON output.

### SDR-S4 Safe Document Read CLI
**Files (expected):** `matlock/cli.py`, `matlock/db.py`, `matlock/redaction.py`, new `tests/test_cli_doc_read.py`, `docs/matlock-cli.md`.

**Implementation notes:** Accept vault-relative and vault-contained absolute paths, normalize to the database key, reject external/untracked/deleted paths, update legacy state on demand, and emit raw content only for confirmed-clean documents.

**Definition of done:** All three state paths and path safety cases pass through the CLI.

### SDR-S5 Release and Documentation Review
**Files (expected):** `pyproject.toml`, `matlock/__init__.py`, `CHANGELOG.md`, `README.md`, and affected configuration, CLI, schema, pipeline, search, design, and docs-map files.

**Implementation notes:** Bump to 0.5.0 and add `matlock.__version__`. There is no tracked `examples/` directory, so document that the applicable examples are README and reference documentation. Perform the mandatory targeted documentation scan.

**Definition of done:** All affected user-facing documentation and release metadata are complete.

---

## Acceptance Criteria

- Clean, finding, and failed scans produce the specified persistent states.
- Legacy databases migrate safely; unchanged files retain status and changed files become unscanned.
- Unsafe content does not appear in new search indexes or any search response.
- `doc-read` safely supports relative and vault-contained absolute paths and updates legacy state.
- Cache reuse/invalidation is hash-based and never persists raw secrets.
- Changelog, affected documentation, and 0.5.0 version metadata are complete.
- Each phase passes focused, adjacent, and full Poetry test gates before review.

## Risks / Notes

- Detector serialized results may contain hashes rather than raw values; never attempt to recover a secret from a hash.
- False positives intentionally redact content. Detector tuning is future work.
- Legacy query protection may scan during reads until reindexing has replaced prior chunks.

## Validation Plan

1. Focused: `poetry run pytest` for the phase test files.
2. Adjacent: `poetry run pytest tests/test_config.py tests/test_db.py tests/test_parse.py tests/test_search_indexer.py tests/test_query_engine.py tests/test_cli_search_query.py` as applicable.
3. Full: `poetry run pytest`.
4. Manual temporary-vault smoke tests: clean, finding, and legacy `doc-read`; index/search, checking that fixture secrets never reach cache, chunks, stdout, or stdio JSON.
5. Confirm the Poetry lock is valid and package metadata is 0.5.0 before build.

## Design Decisions (Resolved)

- Redact search content during both indexing and querying, rather than expose flags only.
- Use exact value masking where safely available, with full-line redaction fallback.
- `doc-read` accepts relative and vault-contained absolute paths.
- Expand `~` and create the cache directory on demand.
- Determine findings from non-empty detector `results`, not the outer JSON object.
- Bump 0.4.1 to 0.5.0.
- Ask for commit approval after each phase.

## Step Notes Log

### SDR-S1 Notes
- Changes made: Activated the plan and added `detect-secrets` 1.5.0 through Poetry. Added frozen `cache.redacted_dir` configuration with `~` expansion and the JSON default. Added nullable `file.has_secrets` and `file.secret_detection_error` columns to new and migrated schemas, a persistence helper, and upsert lifecycle rules that preserve state for identical hashes and reset it for changed/deleted/generated rows. Added secret-state fields to the search response contract and query projections.
- Deviations: none.
- Validation: Focused `poetry run pytest tests/test_config.py tests/test_matlock_config.py tests/test_db.py tests/test_db_search_schema.py tests/test_search_models.py tests/test_search_query_engine.py` passed (121 passed, 1 existing `pytimeparse` deprecation warning). Adjacent `poetry run pytest tests/test_config.py tests/test_db.py tests/test_parse.py tests/test_search_indexer.py tests/test_search_query_engine.py tests/test_cli_search_query.py` passed (94 passed, 1 warning). Full `poetry run pytest` passed (838 passed, 1 warning). `poetry check --lock` passed. The installed Poetry release does not support `poetry lock --check`, so its supported lock check was used.

### SDR-S2 Notes
- Changes made: Added `matlock/redaction.py` with a typed detect-secrets adapter (`SecretFinding`, `SecretScanResult`, `scan_document_for_secrets`) that treats non-empty `results` entries as findings and fail-closes to `has_secrets=True` on scanner exceptions. Integrated parsing to run secret scanning only after successful extraction and persist `has_secrets` and `secret_detection_error` while preserving existing poison-cache behavior for extraction errors. Added scanner-focused tests in `tests/test_redaction.py` and parse-stage scan persistence/fail-closed tests in `tests/test_parse.py`.
- Deviations: none.
- Validation: Focused `poetry run pytest tests/test_redaction.py tests/test_parse.py` passed (37 passed, 1 existing `pytimeparse` deprecation warning). Adjacent `poetry run pytest tests/test_config.py tests/test_db.py tests/test_parse.py tests/test_redaction.py tests/test_search_indexer.py tests/test_search_query_engine.py tests/test_cli_search_query.py` passed (103 passed, 1 warning). Full `poetry run pytest` passed (845 passed, 1 warning).

### SDR-S3 Notes
- Changes made: Expanded `matlock/redaction.py` with cache-backed `get_redacted_document`, exact-value masking, line-level fallback masking, and fail-closed full-document placeholders when scanning fails. Updated search hash-change trigger logic to purge stale search chunks immediately. Integrated search indexing to lazily resolve unknown secret state, persist results, and index redacted content for unsafe files while skipping frontmatter-prefix injection for those redacted chunks. Integrated query-time safeguards to lazily resolve unknown secret state, persist it, suppress unsafe frontmatter payloads, and return redacted chunk/file content for unsafe documents.
- Deviations: none.
- Validation: Focused `poetry run pytest tests/test_redaction.py tests/test_search_indexer.py tests/test_search_query_engine.py tests/test_cli_search_query.py tests/test_db_search_schema.py` passed (36 passed, 1 existing `pytimeparse` deprecation warning). Adjacent `poetry run pytest tests/test_config.py tests/test_db.py tests/test_parse.py tests/test_redaction.py tests/test_search_indexer.py tests/test_search_query_engine.py tests/test_cli_search_query.py tests/test_db_search_schema.py` passed (120 passed, 1 warning). Full `poetry run pytest` passed (855 passed, 1 warning).

### SDR-S4 Notes
- Changes made: Added top-level `matlock doc-read FILE_PATH` in `matlock/cli.py` with vault-relative and vault-contained absolute path support, strict base-directory containment checks, database-key normalization, and rejection for untracked/deleted files. Added legacy-state handling (`has_secrets IS NULL`) that performs on-demand detection, persists `has_secrets` and `secret_detection_error`, and then emits raw or redacted output accordingly. Added focused CLI coverage in `tests/test_cli_doc_read.py` for clean, unsafe, legacy, absolute-path, and path-safety/error flows. Updated `docs/matlock-cli.md` with command usage, behavior contract, and examples.
- Deviations: none.
- Validation: Focused `poetry run pytest tests/test_cli_doc_read.py` passed (7 passed, 1 existing `pytimeparse` deprecation warning). Adjacent `poetry run pytest tests/test_cli_doc_read.py tests/test_cli_parse.py tests/test_parse.py tests/test_redaction.py tests/test_db.py` passed (101 passed, 1 warning). Full `poetry run pytest` passed (862 passed, 1 warning).

### SDR-S5 Notes
- Changes made: Bumped release metadata to 0.5.0 in `pyproject.toml` and added exported package version `matlock.__version__` in `matlock/__init__.py`. Added a 0.5.0 changelog section summarizing secret detection, redaction cache, doc-read, and search protection behavior. Performed the targeted docs scan gate and updated affected references in `README.md`, `docs/matlock-cli.md`, `docs/matlock-configuration.md`, `docs/matlock-data-model.md`, `docs/matlock-pipeline-specification.md`, `docs/matlock-search.md`, `docs/matlock-high-level-design.md`, and `docs/copilot/copilot-docs-reference.md` to document cache configuration, secret-state fields, safe doc-read behavior, and secret-safe search indexing/querying.
- Changes made: There is no tracked `examples/` directory in this repository. Per plan scope, applicable usage examples were updated in `README.md` and `docs/matlock-cli.md`.
- Deviations: none.
- Validation: Metadata gate `poetry check --lock` passed. Focused `poetry run pytest tests/test_cli_doc_read.py tests/test_redaction.py tests/test_search_indexer.py tests/test_search_query_engine.py` passed (30 passed, 1 existing `pytimeparse` deprecation warning). Adjacent `poetry run pytest tests/test_config.py tests/test_db.py tests/test_parse.py tests/test_cli_doc_read.py tests/test_cli_search_query.py tests/test_search_indexer.py tests/test_search_query_engine.py` passed (113 passed, 1 warning). Full `poetry run pytest` passed (862 passed, 1 warning).

## Copilot Execution Protocol

1. Set the active step to `In Progress` before code changes.
2. Implement only the active step and its tests.
3. Run and record focused, adjacent, and full validation.
4. Mark the step complete, request review approval, then commit only with explicit approval.
5. Continue only after the approved commit.