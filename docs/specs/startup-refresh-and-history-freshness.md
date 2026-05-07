# Startup Refresh and History Freshness Specification

Date: 2026-05-06
Owner: Copilot
Scope: server startup orchestration, history rendering freshness

## Objective

Define deterministic startup execution behavior and history refresh semantics so runtime behavior is testable, repeatable, and implementation-independent.

## Functional Requirements

1. Server startup rollup flag
- The server CLI shall expose a startup flag `--force-rollup`.
- The flag shall run `rollup` for today's date exactly once during server startup initialization.
- The startup rollup step shall run after any startup forced sync/parse step and before any startup forced report step.

2. Startup sequencing
- When startup flags are combined, execution order shall be:
  1. forced sync+parse (if enabled)
  2. forced rollup for today (if enabled)
  3. forced report (if enabled)
- Startup steps shall run before observer/debouncer/scheduler threads are started.

3. Startup logging
- Startup operations shall emit explicit startup log/console messages for start and completion of each enabled startup step.
- Rollup startup messaging shall include the date being rolled up.

4. History render freshness
- History rendering shall always invoke rollup for today's date before rendering history pages.
- This invocation shall occur regardless of whether today's `daily_metric` row already exists.
- The behavior shall preserve idempotence by relying on rollup's upsert semantics.

5. History date inclusion
- History render shall include today's date in the render list if today's date is not currently present in distinct metric dates.

## Non-Functional Requirements

1. Determinism
- Combined startup flags shall produce stable ordering across runs.

2. Idempotence
- Re-running report history target multiple times the same day shall refresh snapshot-derived tables (`file_touch`, `daily_task`) without duplicate logical rows.

3. Backward compatibility
- Existing startup flags (`--force-sync`, `--force-report`, `--skip-rollup`) shall retain current behavior.

## Observability and Testability

1. CLI pass-through checks
- Tests shall verify `--force-rollup` is present in CLI help and passed to `run_server`.

2. Server startup checks
- Tests shall verify `run_rollup` is called with `datetime.date.today()` when startup rollup is enabled.
- Tests shall verify call order where forced sync precedes forced rollup when both are enabled.

3. History refresh checks
- Tests shall verify repeated history report execution refreshes today's `file_touch.modified` value when source `file.modified` changes.

## Out of Scope

- Changes to nightly scheduler behavior.
- New report targets beyond existing target set.
- Changes to database schema for this requirement set.
