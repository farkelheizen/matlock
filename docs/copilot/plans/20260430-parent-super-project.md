# 20260430-parent-super-project Plan: Infer super_project_id from `parent` Obsidian Link

> Date: 4/30/2026
> Owner: Copilot
> Branch: feat/scan-projects
> Related docs: docs/matlock-scan-projects.md, docs/matlock-cli.md

## Problem Summary

Some vaults use a `parent` frontmatter attribute containing an Obsidian wiki-link to a super-project page. When the linked page name ends with `, Super-Project`, the prefix before that suffix is the super-project name. The current scanner only reads `super_project_id` directly from frontmatter and ignores the `parent` attribute entirely.

Example:
```yaml
parent: "[[IT Learning, Super-Project]]"
```
→ `super_project_id: "IT Learning"`

Aliases must also be handled:
```yaml
parent: "[[IT Learning, Super-Project|IT Learning]]"
```
→ same result.

## Goal

When `_scan_file` processes a file that is a project candidate, and `super_project_id` is not already set, check the `parent` frontmatter attribute for a wiki-link whose page name ends with `, Super-Project` and extract the prefix as `super_project_id`.

## Scope

- **In scope:** `matlock/stages/scan_projects.py` (`_scan_file` only); new tests in `tests/test_scan_projects.py`
- **Out of scope:** non-project pages; `super_project_id` frontmatter key (still takes precedence); any other Obsidian link attributes

## Constraints / Requirements

- Suffix matched: `, Super-Project` exactly (case-sensitive, hyphen required).
- `super_project_id` frontmatter key takes precedence — `parent` is fallback only.
- Aliases stripped before matching: `[[Name, Super-Project|alias]]` → page name is `Name, Super-Project`.
- Only applied to pages that already have a `project_id` (project candidates).
- No new dependencies.

---

## Implementation Plan

| Step ID | Status | Goal | Planned Changes | Test Coverage |
|---|---|---|---|---|
| PSP-S1 | Completed | Extract super_project_id from `parent` Obsidian link | `matlock/stages/scan_projects.py`: add `_PARENT_SUPER_RE` regex; update `_scan_file` `super_project_id` block | `tests/test_scan_projects.py`: add `TestParentSuperProject` class (5 scenarios) |

---

## Step Details

### PSP-S1 — Extract super_project_id from `parent` Obsidian link

**Files (expected):**
- `matlock/stages/scan_projects.py`
- `tests/test_scan_projects.py`

**Implementation notes:**

Add module-level regex:
```python
# Matches [[Page Name, Super-Project]] or [[Page Name, Super-Project|alias]]
_PARENT_SUPER_RE = re.compile(r'^\[\[(.+?), Super-Project(?:\|[^\]]+)?\]\]$')
```

In `_scan_file`, the `super_project_id` block currently reads:
```python
spid = meta.get("super_project_id")
super_project_id: str | None = spid if isinstance(spid, str) else None
```

Extend it to fall back to `parent` when `super_project_id` is not set:
```python
spid = meta.get("super_project_id")
super_project_id: str | None = spid if isinstance(spid, str) else None

if super_project_id is None:
    parent_val = meta.get("parent")
    if isinstance(parent_val, str):
        m = _PARENT_SUPER_RE.match(parent_val.strip())
        if m:
            super_project_id = m.group(1)
```

The `parent` check runs unconditionally on all pages, but `project_id`-less pages will simply have `super_project_id` set on a `ScannedFile` that never contributes to a `ProjectCandidate` — harmless. (Alternatively, the check could be gated on `project_id is not None` after the project_id block, which is cleaner.)

**Gate on project_id for cleanliness:**  
Apply the parent check only after project_id is determined, and only when `project_id is not None`. This matches the constraint: "only applied to project candidate pages".

**Definition of done:**
- `_PARENT_SUPER_RE` is defined at module level.
- `_scan_file` populates `super_project_id` from `parent` when `super_project_id` is absent and the file is a project candidate.
- 5+ new tests pass.
- Full suite (585+) passes.

---

## Acceptance Criteria

1. `parent: "[[IT Learning, Super-Project]]"` → `super_project_id == "IT Learning"`
2. `parent: "[[IT Learning, Super-Project|IT Learning]]"` → `super_project_id == "IT Learning"`
3. `super_project_id: "Explicit"` + `parent: "[[Other, Super-Project]]"` → `super_project_id == "Explicit"` (explicit wins)
4. `parent: "[[Something Else]]"` (no `, Super-Project` suffix) → `super_project_id` unchanged (None)
5. Non-project page with matching `parent` → `super_project_id` not populated (page has no project_id)
6. Alias with spaces: `[[IT Learning, Super-Project|IT Learning]]` → `super_project_id == "IT Learning"`

---

## Design Decisions (Resolved)

| Question | Decision |
|---|---|
| Suffix variants | `, Super-Project` only (hyphen, exact case) |
| Precedence when both `super_project_id` and `parent` present | `super_project_id` wins; `parent` is fallback |
| Obsidian aliases (`[[Name\|alias]]`) | Yes — strip alias before matching |
| Apply to non-project pages? | No — only when `project_id is not None` |

---

## Step Notes Log

### PSP-S1
- Status: Completed
- Changes: `matlock/stages/scan_projects.py` — `_PARENT_SUPER_RE` constant, `super_project_id` fallback block; `tests/test_scan_projects.py` — `TestParentSuperProject` (5 tests)
- Validation: 590 tests pass
