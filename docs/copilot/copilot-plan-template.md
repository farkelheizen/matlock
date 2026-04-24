# [YYYYMMDD]-[short-slug] Plan: [Feature or Fix Name]

> Date: [M/D/YYYY]
> Owner: Copilot
> Branch: [optional-branch-name]
> Related docs: [doc paths]

This file is intended as a working implementation plan.

## Problem Summary
[1-3 short paragraphs describing the current issue or gap, with concrete examples.]

## Goal
[Single clear outcome statement.]

## Scope
- In scope: [what this plan will change]
- Out of scope: [what this plan explicitly will not change]

## Constraints / Requirements
- [Any architectural, UX, compatibility, performance, or policy constraints]
- [Required behavior that must be preserved]

---

## Implementation Plan

| Step ID | Status | Goal | Planned Changes | Test Coverage |
|---|---|---|---|---|
| [TAG-S1] | Not Started | [Short step outcome] | [Files/modules + key changes] | [Exact test file(s) and scenario(s)] |
| [TAG-S2] | Not Started | [Short step outcome] | [Files/modules + key changes] | [Exact test file(s) and scenario(s)] |
| [TAG-S3] | Not Started | [Short step outcome] | [Files/modules + key changes] | [Exact test file(s) and scenario(s)] |

Status values: `Not Started` | `In Progress` | `Completed` | `Blocked`

---

## Step Details

### [TAG-S1] [Step Name]
**Files (expected):**
- [path/to/file1]
- [path/to/file2]

**Implementation notes:**
- [Design decisions and minimal approach]
- [Edge cases / failure handling]

**Definition of done:**
- [Observable condition that confirms completion]

### [TAG-S2] [Step Name]
**Files (expected):**
- [path/to/file1]

**Implementation notes:**
- [Design decisions and minimal approach]

**Definition of done:**
- [Observable condition that confirms completion]

### [TAG-S3] [Step Name]
**Files (expected):**
- [path/to/file1]

**Implementation notes:**
- [Design decisions and minimal approach]

**Definition of done:**
- [Observable condition that confirms completion]

---

## Acceptance Criteria
- [User-visible behavior works as specified]
- [No regressions in related paths]
- [Diagnostics/errors are clear and actionable]
- [Documentation updated where behavior changed]
- `CHANGELOG.md` updated with Added / Changed / Fixed entries for this version.
- `examples/` updated if any example is affected by the change (version header, YAML config, CLI invocations, new example files).
- Version bump performed: `pyproject.toml` and all `docs/` version headers updated to the new version number.
- Doc Scan performed: stale body-text version references updated, reference maps updated, new architectural areas reflected in high-level design and copilot docs reference map.
- **Pre-publish gate:** `pyproject.toml` `version` field must reflect the new version before running `poetry build` / `poetry publish`. Verify this is the last thing confirmed before publishing to PyPI.

## Risks / Notes
- [Potential implementation risk]
- [Potential compatibility risk]
- [Mitigation strategy]

## Validation Plan
Run tests in this order:
1. Focused tests for changed module(s): `[command]`
2. Adjacent/regression tests: `[command]`
3. Full suite: `[command]`

Record results:
- Focused: [pass/fail + summary]
- Regression: [pass/fail + summary]
- Full suite: [pass/fail + summary]

---

## Step Notes Log (update as work progresses)

### [TAG-S1] Notes
- Changes made: [what was implemented]
- Deviations: [none / description]
- Validation: [tests run + result]

### [TAG-S2] Notes
- Changes made: [what was implemented]
- Deviations: [none / description]
- Validation: [tests run + result]

### [TAG-S3] Notes
- Changes made: [what was implemented]
- Deviations: [none / description]
- Validation: [tests run + result]

---

## Copilot Execution Protocol
When Copilot uses this plan:
1. Set current step to `In Progress` before coding.
2. Implement only the current step scope.
3. Run listed tests for the step.
4. Update step status to `Completed` (or `Blocked`) with notes.
5. Continue to next step only after validation is recorded.
