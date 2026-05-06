# Runtime Hardening Traceability Matrix

Date: 2026-05-06
Owner: Copilot

## Purpose

Map specification requirements to implementation modules and regression tests.

## Matrix

| Requirement ID | Requirement Summary | Implementation Surface | Test Coverage |
|---|---|---|---|
| SRHF-1 | Add server startup `--force-rollup` flag | `matlock/cli.py`, `matlock/server.py` | `tests/test_cli_server.py`, `tests/test_server.py` |
| SRHF-2 | Deterministic startup order sync -> rollup -> report | `matlock/server.py` | `tests/test_server.py` |
| SRHF-3 | Always run rollup for today in history render | `matlock/stages/report.py` | `tests/test_report.py` |
| SRHF-4 | Refresh today snapshot data on repeated history render | `matlock/stages/report.py` | `tests/test_report.py` |
| FPR-1 | Parse poison cache keyed by `(file_path, sha256)` | `matlock/stages/parse.py` | `tests/test_parse.py` |
| FPR-2 | Unchanged poison files skip repeated retry/warn loop | `matlock/stages/parse.py` | `tests/test_parse.py` |
| FPR-3 | scan-projects converts per-file failures to warnings and continues | `matlock/stages/scan_projects.py` | `tests/test_scan_projects.py`, `tests/test_cli_scan_projects.py` |
| FPR-4 | Stable warning prefixes for YAML/UTF8/read/scan failures | `matlock/stages/scan_projects.py` | `tests/test_scan_projects.py`, `tests/test_cli_scan_projects.py` |

## Validation Commands

1. `poetry run pytest tests/test_cli_server.py tests/test_server.py tests/test_report.py`
2. `poetry run pytest tests/test_parse.py tests/test_scan_projects.py tests/test_cli_scan_projects.py`
3. `poetry run pytest`

## Completion Criteria

- Every requirement row has at least one implementation surface and one regression test.
- Any behavior change updates this matrix in the same change set.
