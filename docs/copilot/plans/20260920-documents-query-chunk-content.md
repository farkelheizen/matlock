# 20260920-documents-query-chunk-content Plan: Restrict Document Query Content to Chunk Results

> Date: 9/20/2026
> Owner: Copilot
> Branch: [to be chosen after design approval]
> Related docs: `docs/matlock-cli.md`, `docs/matlock-search.md`, `docs/copilot/vscode-extension-documents-query-prompt.md`, `docs/copilot/plans/20260917-documents-query.md`

This file is a working implementation plan.

## Problem Summary

`matlock documents query` currently exposes `--include-content` and `--no-include-content`, inherited from the lower-level search query contract. Its default is `include_content=True`, so file-granularity document queries can read and return an entire document body even when the caller only requested document metadata.

The desired document-oriented contract is simpler: file results should remain metadata-only, while chunk results should include matched and requested surrounding content. The lower-level `matlock search query` contract and its strict `--stdio` transport are separate compatibility surfaces and should not change as part of this fix.

## Goal

Make `matlock documents query` derive content inclusion solely from `--granularity`: file results never return document content, and chunk results return matched/surrounding content without either content flag.

## Scope

- In scope: remove both content flags from `documents query`; derive the search request's `include_content` value from `granularity`; update focused CLI tests; update affected CLI, search, and VS Code prompt documentation; add a `0.9.1` patch release entry and version metadata.
- Out of scope: changing `matlock search query`, `SearchOutputOptions`, the search engine's direct request contract, secret-redaction behavior, chunking, indexing, or the legacy `--stdio` transport.

## Constraints / Requirements

- `documents query --granularity file` must never read or return full document content; `file_details.content` should be `null`.
- `documents query --granularity chunk` must return matched chunk content and requested surrounding content, subject to existing secret-safe redaction.
- `--include-content` and `--no-include-content` must no longer be accepted by `documents query`.
- `--surrounding-chunks` remains meaningful only for chunk results; existing validation and pagination behavior must remain unchanged.
- Preserve the shared search engine API so legacy search behavior and direct engine tests remain unchanged.
- Use Poetry commands for all validation.
- Apply a patch version bump from `0.9.0` to `0.9.1`; update release metadata and only behaviorally affected documentation rather than performing blanket version-header rewrites.
- No `examples/` directory exists; retain examples in the affected documentation files.

---

## Implementation Plan

| Step ID | Status | Goal | Planned Changes | Test Coverage |
|---|---|---|---|---|
| DQC-S1 | Completed | Resolve the content contract | File results are metadata-only; chunk content is implicit; legacy search remains unchanged; release target is `0.9.1` | Contract review against current CLI and engine behavior |
| DQC-S2 | Completed | Implement granularity-driven output | Updated `matlock/cli.py` to remove document-query content options and set `include_content = granularity == "chunk"`; normalized suppressed file content to `null` in the document adapter | `tests/test_cli_documents_query.py`: file metadata-only, chunk content/context, removed flags rejected |
| DQC-S3 | Completed | Update release documentation | Updated `docs/matlock-cli.md`, `docs/matlock-search.md`, `docs/copilot/vscode-extension-documents-query-prompt.md`, `CHANGELOG.md`, and `pyproject.toml` for `0.9.1` | Documentation consistency review plus CLI help assertion |
| DQC-S4 | Completed | Validate the release change | Ran focused, adjacent, and full Poetry test suites; recorded results and completed plan notes | Focused: 6 passed; adjacent: 25 passed; full: 929 passed; CLI help verified |

Status values: `Not Started` | `In Progress` | `Completed` | `Blocked`

---

## Step Details

### DQC-S1 Contract and Design Resolution
**Files (expected):**
- `docs/copilot/plans/20260920-documents-query-chunk-content.md`
- `docs/copilot/current-plan.md`

**Implementation notes:**
- Resolve the single contract question below before implementation.
- The recommended behavior is that `--granularity file` always produces `file_details.content: null`, while `--granularity chunk` always enables matched and surrounding content.
- Keep `matlock/search/models.py` and the legacy `search query` flags unchanged.

**Definition of done:**
- The question is acknowledged, this section is recorded as resolved, and the active plan pointer names this plan and the next implementation step.

### DQC-S2 Granularity-Driven Implementation
**Files (expected):**
- `matlock/cli.py`
- `tests/test_cli_documents_query.py`

**Implementation notes:**
- Remove the `include_content` parameter from the `documents_query` Typer signature.
- Set the request payload's `output.include_content` to `granularity == "chunk"`.
- Keep the existing search engine path so secret-safe redaction remains centralized.
- Assert that the old flags are rejected by the command parser, not silently accepted.

**Definition of done:**
- File queries return metadata without content, chunk queries return content, and neither content flag is present in `documents query --help` or accepted by the command.

### DQC-S3 Documentation and Patch Release
**Files (expected):**
- `docs/matlock-cli.md`
- `docs/matlock-search.md`
- `docs/copilot/vscode-extension-documents-query-prompt.md`
- `CHANGELOG.md`
- `pyproject.toml`

**Implementation notes:**
- Remove document-query references to both content flags and describe content as implicit for chunk granularity.
- Leave the separate legacy `search query` documentation for `--include-content / --no-include-content` intact.
- Add a `0.9.1` changelog entry under `Changed`, `Fixed`, and `Removed` as applicable, explicitly noting the document-query flag removal and chunk-only content behavior.
- Bump `pyproject.toml` from `0.9.0` to `0.9.1`.
- Scan the root README and `docs/` for stale document-query references; do not rewrite unrelated stable documentation baselines.

**Definition of done:**
- User-facing docs, release metadata, and examples describe one consistent command contract.

### DQC-S4 Validation and Completion
**Files (expected):**
- `docs/copilot/plans/20260920-documents-query-chunk-content.md`
- `docs/copilot/current-plan.md`

**Implementation notes:**
- Run focused tests first, then adjacent CLI/search tests, then the full suite.
- Record command results in the Step Notes Log and mark completed steps only after validation passes.

**Definition of done:**
- Focused and regression tests pass, the full suite result is recorded, and the plan is complete without changing unrelated search behavior.

---

## Acceptance Criteria

- `documents query --granularity file` returns no document body content and does not expose `--include-content` or `--no-include-content`.
- `documents query --granularity chunk` returns matched chunk content and requested surrounding chunks through the existing secret-safe path.
- Existing `matlock search query` flags, models, engine behavior, and `--stdio` output remain unchanged.
- Pagination, count-only behavior, filters, scoring, and metadata-only browsing remain unchanged.
- Focused, adjacent, and full Poetry test suites pass.
- `CHANGELOG.md` and affected documentation are updated for `0.9.1`.
- `pyproject.toml` version is `0.9.1` before build/publish validation.
- No examples directory changes are required because the repository has no `examples/` directory.

## Risks / Notes

- Removing flags is a breaking change for callers that pass them to `documents query`; this is intentional and should be called out in the `0.9.1` changelog.
- Consumers that need full-file content must use `document read` or a future explicitly scoped document-content command; this query will not provide it.
- The shared engine still supports content suppression and inclusion for legacy search callers, preventing an unrelated compatibility regression.

## Validation Plan

Run tests in this order:
1. Focused tests: `poetry run pytest tests/test_cli_documents_query.py`
2. Adjacent regression tests: `poetry run pytest tests/test_cli_search_query.py tests/test_search_query_engine.py tests/test_search_output_shape.py tests/test_search_stdio_contract.py`
3. Full suite: `poetry run pytest`
4. CLI contract check: `poetry run matlock documents query --help`

Record results:
- Focused: [not run]
- Regression: [not run]
- Full suite: [not run]
- CLI contract: [not run]

---

## Design Decisions (Resolved)

- `documents query --granularity chunk` always returns matched and requested surrounding content through the existing secret-safe path.
- `documents query --granularity file` always returns metadata only, with `file_details.content: null`.
- Both content flags are removed from `documents query`; the separate legacy `search query` flags remain unchanged.
- The release target is `0.9.1`, with the removed options called out in the changelog.

## Step Notes Log (update as work progresses)

### DQC-S1 Notes
- Changes made: Resolved the chunk-only content contract and preserved the legacy search contract.
- Deviations: None.
- Validation: Contract reviewed against the CLI, adapter, and engine paths.

### DQC-S2 Notes
- Changes made: Removed document-query content flags, derived inclusion from granularity, and normalized suppressed file content to `null`.
- Deviations: Preserved the shared engine's empty-string sentinel for legacy callers; normalized only in the document-query adapter.
- Validation: `poetry run pytest tests/test_cli_documents_query.py` — 6 passed.

### DQC-S3 Notes
- Changes made: Updated affected docs, changelog, and bumped `pyproject.toml` to `0.9.1`.
- Deviations: No examples directory exists; legacy search documentation was intentionally left unchanged.
- Validation: `poetry run matlock documents query --help` contains neither removed flag.

### DQC-S4 Notes
- Changes made: Completed focused, adjacent, and full validation.
- Deviations: One existing `pytimeparse` deprecation warning remains.
- Validation: Adjacent tests 25 passed; full suite 929 passed; no failures.

---

## Copilot Execution Protocol

When Copilot uses this plan:
1. Resolve Q1 and Q2 with the user and replace `Questions / Concerns` with `Design Decisions (Resolved)`.
2. Update `docs/copilot/current-plan.md` to point to DQC-S2 before coding.
3. Set the current step to `In Progress` before each implementation step.
4. Implement only the current step scope.
5. Run the listed tests for the step.
6. Update step status to `Completed` or `Blocked` with notes.
7. Continue only after validation is recorded.
