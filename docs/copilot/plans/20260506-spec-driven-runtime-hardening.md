# 20260506-spec-driven-runtime-hardening Plan: Spec-Driven Runtime Hardening & Startup Refresh

> Date: 5/6/2026
> Owner: Copilot
> Branch: TBD
> Related docs: `docs/matlock-pipeline-specification.md`, `docs/matlock-cli.md`, `docs/matlock-generated-reports.md`, `docs/matlock-scan-projects.md`, `docs/matlock-data-model.md`

## Problem Summary

Matlock now needs a cohesive feature set across five surfaces: server startup orchestration, history rendering freshness, parse failure suppression, scan-projects file-level fault tolerance, and the documentation/tests that define those behaviors. The current repository has code and tests that demonstrate the intended semantics, but those requirements are not yet captured as a durable, implementation-ready specification set.

Without a clear requirements packet, future implementation work depends on reading scattered code, commit history, and tests to reconstruct behavior. That makes it harder to reason about ordering guarantees, retry semantics, warning behavior, and what counts as a regression.

This plan creates a spec-first path: write the requirement documents, add a dedicated implementation agent that works from those docs, and then implement the feature set from the approved specification.

## Goal

Produce a spec-driven implementation package for startup refresh and runtime resilience, then implement the feature set from those specs with matching tests and updated product documentation.

## Scope

- In scope:
  - Requirements documents for startup refresh, history freshness, parse retry handling, and scan-projects degraded-file behavior
  - A traceability document mapping requirement statements to implementation files and tests
  - A workspace custom agent that reads the approved specs and implements only from those specs
  - Implementation across CLI, server, report, parse, and scan-projects modules
  - Regression tests for all new behaviors
  - Product docs updates for CLI/stage behavior

- Out of scope:
  - Unrelated refactors outside the target behaviors
  - New user-facing commands beyond the startup/report/parse/scan scope defined here
  - Release/publish work beyond normal version/changelog updates required when the feature set is ready

## Constraints / Requirements

- Specs must be written before implementation starts and must be detailed enough that an implementation agent can work without consulting prior commits.
- Startup actions must have deterministic ordering when multiple startup flags are enabled.
- History rendering must preserve idempotence while refreshing today's snapshot-derived data.
- Parse failure suppression must be keyed to unchanged file content and must automatically retry when file content changes.
- scan-projects must continue scanning after per-file read/parse failures and surface actionable warnings.
- Tests must be added or updated alongside implementation changes for each step.
- Product docs must reflect any new CLI flags and stage semantics introduced by this feature set.

---

## Implementation Plan

| Step ID | Status | Goal | Planned Changes | Test Coverage |
|---|---|---|---|---|
| SDR-S1 | Completed | Author the runtime requirements packet | Add requirements docs under `docs/specs/` covering startup refresh/history freshness and file-processing resilience; add a traceability matrix | Review-only; no executable validation |
| SDR-S2 | Completed | Create a spec-driven implementation agent | Add `.github/agents/spec-runtime-hardening.agent.md` with instructions to consume the new requirements docs and implement stepwise | Review agent frontmatter and file placement |
| SDR-S3 | Completed | Implement startup refresh and history freshness | Update `matlock/cli.py`, `matlock/server.py`, `matlock/stages/report.py`; align CLI/server/report docs as needed | `tests/test_cli_server.py`, `tests/test_server.py`, `tests/test_report.py` |
| SDR-S4 | Completed | Implement file-processing resilience behaviors | Update `matlock/stages/parse.py`, `matlock/stages/scan_projects.py`; update behavior docs as needed | `tests/test_parse.py`, `tests/test_scan_projects.py`, `tests/test_cli_scan_projects.py` |
| SDR-S5 | Completed | Complete release-facing documentation and validation | Update `docs/matlock-cli.md`, `docs/matlock-pipeline-specification.md`, `CHANGELOG.md`, `pyproject.toml` as applicable | Focused suites above, then `poetry run pytest` |

Status values: `Not Started` | `In Progress` | `Completed` | `Blocked`

---

## Step Details

### SDR-S1 — Runtime Requirements Packet
**Files (expected):**
- `docs/specs/startup-refresh-and-history-freshness.md`
- `docs/specs/file-processing-resilience.md`
- `docs/specs/runtime-hardening-traceability.md`

**Implementation notes:**
- Capture normative behavior in requirement language rather than commit-oriented language.
- Cover ordering rules, retry semantics, idempotence, warning surfaces, and test-observable outcomes.
- The traceability document should map each requirement to code modules and tests that must exist when implementation completes.

**Definition of done:**
- A reviewer can understand the intended behavior without opening prior implementation commits.
- The spec packet enumerates expected modules, flags, edge cases, and validation scenarios.

### SDR-S2 — Spec-Driven Implementation Agent
**Files (expected):**
- `.github/agents/spec-runtime-hardening.agent.md`

**Implementation notes:**
- Agent should explicitly consume the new requirements docs before changing code.
- Agent instructions should enforce stepwise execution, focused tests after each implementation slice, and updates to the traceability matrix / docs where behavior changes.
- Keep the agent workspace-scoped and narrowly targeted to this feature set.

**Definition of done:**
- The repo contains a custom agent file with valid frontmatter and a description that makes its purpose discoverable.
- The agent instructions point to the spec docs as the primary source of truth.

### SDR-S3 — Startup Refresh & History Freshness Implementation
**Files (expected):**
- `matlock/cli.py`
- `matlock/server.py`
- `matlock/stages/report.py`
- `tests/test_cli_server.py`
- `tests/test_server.py`
- `tests/test_report.py`
- `docs/matlock-cli.md`
- `docs/matlock-pipeline-specification.md`

**Implementation notes:**
- Introduce startup rollup semantics through the CLI/server boundary with explicit flag pass-through.
- Define the exact startup order when sync, rollup, and report are combined.
- Ensure history generation refreshes today's rollup-backed snapshot data every time the history target is rendered.

**Definition of done:**
- Server startup behavior is deterministic and documented.
- Repeated history renders for today refresh snapshot-backed tables and keep tests green.

### SDR-S4 — File-Processing Resilience Implementation
**Files (expected):**
- `matlock/stages/parse.py`
- `matlock/stages/scan_projects.py`
- `tests/test_parse.py`
- `tests/test_scan_projects.py`
- `tests/test_cli_scan_projects.py`
- `docs/matlock-pipeline-specification.md`
- `docs/matlock-scan-projects.md`

**Implementation notes:**
- Parse should suppress repeated retries for unchanged failing files, but immediately retry when file content changes.
- scan-projects should convert per-file read/parse failures into warnings and continue processing the rest of the vault.
- Warning text must be stable enough for tests and user-facing diagnostics.

**Definition of done:**
- Unchanged failing files no longer re-trigger the same parse extraction path repeatedly.
- scan-projects completes with warnings instead of aborting on single-file failures.

### SDR-S5 — Documentation, Release Metadata, and Final Validation
**Files (expected):**
- `docs/matlock-cli.md`
- `docs/matlock-pipeline-specification.md`
- `docs/matlock-generated-reports.md`
- `CHANGELOG.md`
- `pyproject.toml`

**Implementation notes:**
- Fold the finalized behavior into public docs once implementation is stable.
- Update changelog/version only in the final stabilization step for this feature set.
- Run focused, regression, and full-suite validation in that order.

**Definition of done:**
- Public docs match the implemented behavior.
- Release metadata is updated once, at the end of the work.

---

## Acceptance Criteria
- Requirements docs exist and fully describe startup refresh, history freshness, parse retry suppression, and scan-projects failure handling.
- A dedicated workspace custom agent exists and is directed to implement from the new specs.
- `matlock server` supports the documented startup behavior and passes CLI/server regression tests.
- History rendering refreshes today's snapshot-derived tables on repeated runs and passes report regression tests.
- Parse and scan-projects resilience behaviors match the approved specs and pass regression tests.
- Public docs are updated where behavior changed.
- `CHANGELOG.md` is updated with Added / Changed / Fixed entries for this version.
- Example updates are evaluated for applicability; if no example assets exist for the affected behavior, record N/A explicitly in step notes.
- Version bump is performed in `pyproject.toml` when the implementation is finalized.
- Doc Scan is performed for affected docs and reference maps before completion.

## Risks / Notes
- The feature set spans multiple modules with different failure models; vague specs could still lead to inconsistent behavior unless requirement language is precise.
- Agent instructions that are too broad may cause the implementation agent to wander across unrelated files.
- Startup ordering bugs can be subtle because they only appear when multiple flags are combined.
- History freshness changes must preserve rollup idempotence and avoid duplicate snapshot rows.

## Validation Plan
Run tests in this order:
1. Focused tests for changed module(s): `poetry run pytest tests/test_cli_server.py tests/test_server.py tests/test_report.py tests/test_parse.py tests/test_scan_projects.py tests/test_cli_scan_projects.py`
2. Adjacent/regression tests: `poetry run pytest tests/test_cli_run_all.py tests/test_cli_script.py tests/test_cli_report.py`
3. Full suite: `poetry run pytest`

Record results:
- Focused: pass — `poetry run pytest tests/test_cli_server.py tests/test_server.py tests/test_report.py` (204 passed)
- Regression: pass — `poetry run pytest tests/test_cli_run_all.py tests/test_cli_script.py tests/test_cli_report.py` (64 passed)
- Full suite: pass — `poetry run pytest` (749 passed)

Latest implementation validation:
- Focused startup/history: pass — `poetry run pytest -q tests/test_cli_server.py tests/test_server.py tests/test_report.py` (206 passed)
- Focused resilience: pass — `poetry run pytest -q tests/test_parse.py tests/test_scan_projects.py tests/test_cli_scan_projects.py` (143 passed)
- Regression: pass — `poetry run pytest -q tests/test_cli_run_all.py tests/test_cli_script.py tests/test_cli_report.py` (64 passed)
- Full suite: pass — `poetry run pytest --tb=short -q` (751 passed)

---

## Design Decisions (Resolved)

**D1 — Requirements doc location and naming**
Accepted default: requirements packet is stored under `docs/specs/` as:
- `docs/specs/startup-refresh-and-history-freshness.md`
- `docs/specs/file-processing-resilience.md`
- `docs/specs/runtime-hardening-traceability.md`

**D2 — Custom agent scope**
Accepted default: use one workspace custom agent at `.github/agents/spec-runtime-hardening.agent.md`, scoped to the runtime hardening feature set and required to consume the new specs first.

**D3 — Final release target**
Accepted default: finalization follows normal patch-release flow with changelog and version update in the last step.

---

## Step Notes Log (update as work progresses)

### SDR-S1 Notes
- Changes made: authored requirements packet in `docs/specs/` with startup/history requirements, resilience requirements, and a requirement-to-code/test traceability matrix.
- Deviations: [none]
- Validation: document review completed (non-executable step)

### SDR-S2 Notes
- Changes made: added workspace custom agent `.github/agents/spec-runtime-hardening.agent.md` with spec-first execution protocol and test gating.
- Deviations: [none]
- Validation: frontmatter and file placement reviewed

### SDR-S3 Notes
- Changes made: added CLI/server startup `--force-rollup` support, enforced startup order `sync+parse -> rollup(today) -> report`, and made history rendering refresh today's rollup-backed snapshots on every run.
- Deviations: [none]
- Validation: `poetry run pytest -q tests/test_cli_server.py tests/test_server.py tests/test_report.py` → 206 passed

### SDR-S4 Notes
- Changes made: added an in-memory parse poison cache keyed by `file_path` and `sha256`, suppressed unchanged repeat extraction retries, retried automatically on hash changes, and converted scan-projects per-file YAML/UTF-8/read failures into stable warning entries while continuing the vault walk.
- Deviations: [none]
- Validation: `poetry run pytest -q tests/test_parse.py tests/test_scan_projects.py tests/test_cli_scan_projects.py` → 143 passed

### SDR-S5 Notes
- Changes made: completed focused, regression, and full-suite validation after the spec-driven implementation updates; release-facing docs already matched the implemented behavior so no additional doc edits were required in this change set.
- Deviations: [none]
- Validation: focused/regression/full suites all passing (206 + 143 + 64 + 751 tests)

---

## Copilot Execution Protocol
When Copilot uses this plan:
1. Resolve the `Questions / Concerns` section first and replace it with `Design Decisions (Resolved)`.
2. Set the current step to `In Progress` before coding.
3. Implement only the current step scope.
4. Run the listed focused tests for the step.
5. Update the traceability doc and step notes before moving on.
6. Continue only after validation is recorded.