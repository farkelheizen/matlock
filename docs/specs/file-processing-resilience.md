# File Processing Resilience Specification

Date: 2026-05-06
Owner: Copilot
Scope: parse stage retry suppression, scan-projects per-file fault tolerance

## Objective

Define resilient behavior for parse and scan-projects so single-file failures do not destabilize runs and repeated unchanged failures avoid noisy retry loops.

## Functional Requirements

1. Parse poison-file cache
- Parse shall maintain an in-memory poison cache keyed by `file_path` with associated `sha256` value for failures.
- When a file previously failed extraction and its current `sha256` matches cached poison sha, parse shall skip extraction for that file in the current run.
- When a cached-poison file's `sha256` changes, parse shall clear poison state and attempt extraction again.
- On extraction failure, parse shall update poison cache for that file to current sha.
- On extraction success, parse shall remove poison state for that file.

2. Parse run accounting
- Skipped unchanged poison files shall increment parse skipped count.
- Skipped unchanged poison files shall not repeatedly emit the same extraction warning on each run.

3. scan-projects per-file failure tolerance
- scan-projects vault walk shall continue processing remaining files when a single file fails to read/parse.
- Per-file failures shall be represented as scanned-file warning entries bound to the affected `file_path`.
- Failures that shall be converted to warnings include malformed YAML, text decoding errors, and file read errors.

4. Warning formatting
- Warning strings shall be stable and human-readable, with category-specific prefixes:
  - Failed to parse frontmatter YAML
  - Failed to read file as UTF-8 text
  - Failed to read file
  - Failed to scan file

5. CLI warning surface
- `scan-projects` CLI output shall include these warnings with source file tags while still returning success exit status when only per-file read/parse errors occur.

## Non-Functional Requirements

1. Isolation
- A failing file shall not prevent successful files in the same run from being processed.

2. Retry efficiency
- Unchanged repeated parse failures shall not re-run full extraction logic each run.

3. Automatic recovery
- File content changes shall automatically re-enable parse attempts without manual cache reset.

## Observability and Testability

1. Parse tests
- Verify unchanged poison file behavior suppresses repeated warning retries.
- Verify sha change triggers retry path and warning emission again.

2. scan-projects stage tests
- Verify malformed YAML creates warning and run continues with valid files.
- Verify non-UTF8 markdown creates warning and run continues with valid files.

3. scan-projects CLI tests
- Verify malformed frontmatter warning appears in output and command exits zero.

## Out of Scope

- Persistent poison-cache storage across process restarts.
- New CLI flags for parse retry policy tuning.
