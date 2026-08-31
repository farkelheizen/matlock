# 20260831-secret-detection-backfill Plan: Secret Detection Backfill

> Date: 8/31/2026
> Owner: Copilot
> Branch: feature branch created after each step receives review approval
> Related docs: `docs/matlock-cli.md`, `docs/matlock-data-model.md`, `docs/matlock-pipeline-specification.md`, `docs/matlock-search.md`, `docs/copilot/plans/20260831-secret-detection-redaction.md`

## Problem Summary

Secret detection currently occurs while parsing documents and lazily during safe document/search reads. Existing tracked documents with `file.has_secrets IS NULL` stay unknown until one of those paths touches them. A large vault can therefore retain an unknown state indefinitely, and previous scan failures lack a targeted retry operation.

Matlock needs a deliberate, bulk remediation command that scans legacy unknown documents without re-parsing them and can optionally retry only prior scan failures.

## Goal

Provide a safe `matlock detect-backfill` command that resolves secret-detection state for existing active tracked documents and supports targeted retry of previous scan errors.

## Scope

- In scope: candidate DB query, backfill stage/result model, top-level CLI command, `--retry-errors`, focused tests, CLI/pipeline/schema/search/README documentation, Unreleased changelog entry.
- Out of scope: automatic recurring scans, source-file changes, cache generation, search reindexing, task/parse-state updates, detector plugin configuration, and a new package version.

## Constraints / Requirements

- Default candidates are active, non-generated documents where `has_secrets IS NULL`.
- `--retry-errors` additionally includes rows where `secret_detection_error IS NOT NULL`; it does not rescan confirmed clean/finding rows that have no detection error.
- Reuse `scan_document_for_secrets()` and preserve its fail-closed result semantics.
- Missing or unreadable candidates are skipped with a warning and retain their existing DB state; filesystem availability failures are distinct from scanner failures.
- Scanner failures persist `has_secrets = 1` and `secret_detection_error`.
- The command must not alter `needs_parsing`, tasks, source content, search chunks, or redaction cache content.
- Use Poetry for validation. Each step requires focused, adjacent, and full test gates before review and commit approval.

---

## Implementation Plan

| Step ID | Status | Goal | Planned Changes | Test Coverage |
|---|---|---|---|---|
| SDB-S1 | Completed | Implement secret-state bulk backfill | Candidate DB helper, backfill stage/result, `detect-backfill` CLI, focused DB/stage/CLI tests | `test_detect_backfill.py`, `test_cli_detect_backfill.py`, `test_db.py` |
| SDB-S2 | Not Started | Document the command | CLI/pipeline/schema/search/README/docs-map updates and Unreleased changelog entry | focused documentation assertions where applicable; full suite |

Status values: `Not Started` | `In Progress` | `Completed` | `Blocked`

---

## Step Details

### SDB-S1 Secret-State Backfill
**Files (expected):**
- `matlock/db.py`
- `matlock/stages/detect_backfill.py`
- `matlock/cli.py`
- `tests/test_detect_backfill.py`
- `tests/test_cli_detect_backfill.py`
- `tests/test_db.py`

**Implementation notes:**
- Add `get_files_needing_secret_detection(conn, *, retry_errors: bool = False)` that returns ordered active (`deleted = 0`), non-generated (`is_generated = 0`) file rows. Default mode filters `has_secrets IS NULL`; retry mode filters `(has_secrets IS NULL OR secret_detection_error IS NOT NULL)`.
- Add `DetectBackfillResult` with `scanned`, `skipped`, `retried`, and `errors` counters. `run_detect_backfill(config, conn, *, retry_errors=False)` scans each candidate using `scan_document_for_secrets()` and persists results with `set_file_secret_detection()`.
- Count a row as `retried` only when it had a pre-existing `secret_detection_error` and was selected through retry mode. Count a returned scanner error in `errors`; persist its fail-closed state.
- Catch `OSError` around available-file checks/scans, log a warning, increment `skipped`, and leave state untouched. One end-of-run commit persists successful scan results.
- Add top-level `matlock detect-backfill [--retry-errors]` and emit `Secret detection backfill complete: N scanned, M skipped, K retried, E errors`.

**Definition of done:**
- The CLI deterministically scans only eligible active files, persists fail-closed scan state, retries only error rows when requested, and leaves parse/search/cache state untouched.

### SDB-S2 Documentation and Release Notes
**Files (expected):**
- `CHANGELOG.md`
- `README.md`
- `docs/matlock-cli.md`
- `docs/matlock-data-model.md`
- `docs/matlock-pipeline-specification.md`
- `docs/matlock-search.md`
- `docs/matlock-high-level-design.md`
- `docs/copilot/copilot-docs-reference.md`

**Implementation notes:**
- Add an Unreleased changelog entry and explain default candidate selection, `--retry-errors`, result counters, and deliberate non-effects (no reparse/cache/reindex).
- Update affected architecture and command references. Configuration documentation requires review only: no new configuration key is introduced.
- No `examples/` directory exists; README and CLI reference examples are the applicable examples.

**Definition of done:**
- Documentation accurately explains the backfill operation and its boundaries, while release metadata remains at 0.5.0.

---

## Acceptance Criteria

- Default `detect-backfill` scans only active, non-generated, unknown-state tracked files.
- `--retry-errors` retries errored records without rescreening ordinary confirmed states.
- Clean/finding/error scanner outcomes persist through the existing state contract.
- Unavailable files are reported as skipped without mutation.
- The operation leaves tasks, `needs_parsing`, source text, search rows, and cache files unchanged.
- User-facing documentation and Unreleased changelog entry explain the new command.
- Each step passes focused, adjacent, and full Poetry test gates before review.

## Risks / Notes

- Documents can change after their most recent sync hash; this point-in-time operation follows the tracked DB candidate set and does not replace `sync`/`parse`.
- Backfill can be expensive for a large vault because each candidate is scanned once; it intentionally does not run automatically.
- A scan failure must be persisted as unsafe, while a filesystem failure must not overwrite a potentially useful prior state.

## Validation Plan

1. Focused SDB-S1: `poetry run pytest tests/test_detect_backfill.py tests/test_cli_detect_backfill.py tests/test_db.py`
2. Adjacent SDB-S1: `poetry run pytest tests/test_parse.py tests/test_redaction.py tests/test_cli_doc_read.py tests/test_search_indexer.py tests/test_search_query_engine.py`
3. Full suite: `poetry run pytest`
4. Manual temporary-vault smoke: invoke default command, verify only unknown rows change; invoke `--retry-errors`, verify only errored rows are retried.

Record results:
- Focused: pending.
- Adjacent: pending.
- Full suite: pending.

---

## Design Decisions (Resolved)

- Command name: `detect-backfill`.
- Default scope: active, non-generated files with unknown (`NULL`) secret state only.
- Retry behavior: `--retry-errors` adds only files with `secret_detection_error`, including any unknown rows.
- Filesystem failure policy: skip and retain state; do not conflate unavailable files with scanner errors.
- Scope boundary: redaction cache population and search reindexing remain owned by existing lazy read/index workflows.
- Commit cadence: request approval after each completed step, then commit only its scoped changes.

## Step Notes Log

### SDB-S1 Notes
- Changes made: Added `get_files_needing_secret_detection()` to select ordered active, non-generated unknown-state rows, optionally including prior scanner-error rows. Added `matlock.stages.detect_backfill` with `DetectBackfillResult` and `run_detect_backfill()`; it reuses the shared scanner and persistence helper, preserves fail-closed scanner outcomes, skips unavailable files without DB mutation, and does not touch parse/task/search/cache state. Added top-level `matlock detect-backfill [--retry-errors]` command with summary counters. Added focused stage and CLI tests for selection, persistence, retry behavior, unavailable files, exclusions, and command output.
- Deviations: none.
- Validation: Initial test-first probe failed as expected because `matlock.stages.detect_backfill` did not yet exist. Focused `poetry run pytest tests/test_detect_backfill.py tests/test_cli_detect_backfill.py tests/test_db.py` passed (48 passed, 1 existing `pytimeparse` deprecation warning). Adjacent `poetry run pytest tests/test_parse.py tests/test_redaction.py tests/test_cli_doc_read.py tests/test_search_indexer.py tests/test_search_query_engine.py` passed (64 passed, 1 warning). Full `poetry run pytest` passed (868 passed, 1 warning). Diagnostics reported no errors in changed modules or focused tests.

### SDB-S2 Notes
- Changes made: pending.
- Deviations: none.
- Validation: pending.

---

## Copilot Execution Protocol

1. Set the current step to `In Progress` before code changes.
2. Implement only the active step and its tests.
3. Run listed focused, adjacent, and full validation.
4. Update step status and notes, request review approval, and commit only with explicit approval.
5. Continue to the next step after the approved commit.
