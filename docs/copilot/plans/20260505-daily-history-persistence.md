# 20260505-daily-history-persistence Plan: Daily History Page Persistence & Enrichment

> Date: 5/5/2026
> Owner: Copilot
> Branch: feat/daily-history-persistence
> Related docs: `docs/matlock-data-model.md`, `docs/matlock-pipeline-specification.md`, `docs/matlock-generated-reports.md`

## Problem Summary

The daily history page (`_Matlock/History/<YYYY-MM-DD>.md`) is regenerated from `daily_metric` rows alone. This data is aggregated at the project level, so the history page only shows total counts and a per-project breakdown (completed count, overdue count). There is no record of *which* files were touched on a given day, nor *which specific tasks* were created or completed.

If a history page is regenerated many days after it was first written, the `task` and `file` tables may have changed (tasks edited, files moved, completed tasks re-opened), making it impossible to accurately reconstruct what happened on that day.

The goal is to introduce two new immutable-record tables (`file_touch` and `daily_task`) that snapshot per-day file and task events at rollup time, allowing accurate re-generation of history pages at any future date.

## Goal

Persist per-day file-touch and task-event snapshots at rollup time, and use this data to enrich the daily history page with: a full file-touch list, task-level detail, super-project and project summaries, and improved navigation (prev/next day, generated date, home link in the header).

## Scope

- In scope:
  - Two new DB tables: `file_touch`, `daily_task`
  - New `deleted_date` column on the `file` table (migration + sync update) for accurate deletion tracking
  - Rollup stage: populate `file_touch` and `daily_task` at rollup time
  - Report stage: query new tables and pass enriched data to `_render_history_page`
  - Template: full redesign of `daily_history.md.j2`
  - Docs: update `matlock-data-model.md`, `matlock-pipeline-specification.md`, `matlock-generated-reports.md`

- Out of scope:
  - Backfilling `file_touch` / `daily_task` for dates that predate this change (no historical data available)
  - Changes to any other report templates
  - UI-level task linking (tasks link to the source file page, not a dedicated task detail page)

## Constraints / Requirements

- Rollup must remain idempotent (re-running for the same date replaces the snapshot cleanly)
- `daily_task` must store enough denormalized data (task_text, attributes) to survive task deletion or edits in the source vault
- `file_touch` should record both the event_type and the modified epoch for ordering
- History pages must degrade gracefully when `file_touch` / `daily_task` have no rows for a date (pre-migration data)
- `deleted_date` is populated by `sync` when it detects a deletion; if not set, the history page omits deletion events for that file
- Template must be valid Obsidian Markdown (no plugins required)

---

## Implementation Plan

| Step ID | Status | Goal | Planned Changes | Test Coverage |
|---|---|---|---|---|
| HDP-S1 | Completed | Add `file_touch`, `daily_task` tables and `deleted_date` migration | `matlock/db.py` — schema, migrations, upsert helpers | `tests/test_db.py` — table exists, upsert helpers work |
| HDP-S2 | Completed | Populate `deleted_date` in sync; populate `file_touch` + `daily_task` in rollup | `matlock/stages/sync.py`, `matlock/stages/rollup.py` | `tests/test_sync.py`, `tests/test_rollup.py` |
| HDP-S3 | Completed | Build enriched history context in report stage | `matlock/stages/report.py` — `_render_history_page` | `tests/test_report.py` |
| HDP-S4 | Completed | Update `daily_history.md.j2` template | `matlock/templates/daily_history.md.j2` | Visual review + `test_report.py` render test |
| HDP-S5 | Completed | Update docs | `docs/matlock-data-model.md`, `docs/matlock-pipeline-specification.md`, `docs/matlock-generated-reports.md` | — |

Status values: `Not Started` | `In Progress` | `Completed` | `Blocked`

---

## Step Details

### HDP-S1 — DB Schema: `file_touch`, `daily_task`, `deleted_date`

**Files (expected):**
- `matlock/db.py`

**New tables to add to `_SCHEMA`:**

#### `file_touch` table

Immutable per-date file event record. Populated by rollup.

| Column | Type | Constraints | Description |
|:--|:--|:--|:--|
| `file_path` | TEXT | PK part | Relative path from `base_directory` |
| `touch_date` | TEXT | PK part | YYYY-MM-DD of the event |
| `event_type` | TEXT | PK part | `'created'` \| `'modified'` \| `'deleted'` |
| `modified` | INTEGER | | Epoch ms of last modification at snapshot time (nullable) |

PRIMARY KEY: `(file_path, touch_date, event_type)` — allows a file to be both created and deleted on the same day (edge case).

#### `daily_task` table

Immutable per-date task event record. Populated by rollup.

| Column | Type | Constraints | Description |
|:--|:--|:--|:--|
| `task_id` | TEXT | PK part | SHA-256 task identifier |
| `event_date` | TEXT | PK part | YYYY-MM-DD of the event |
| `event_type` | TEXT | PK part | `'created'` \| `'completed'` |
| `task_text` | TEXT | | Denormalized task text at snapshot time |
| `file_path` | TEXT | | Source file path at snapshot time |
| `attributes` | TEXT | | JSON attributes at snapshot time (for estimate) |

PRIMARY KEY: `(task_id, event_date, event_type)` — a task can be both created and completed on the same day.

**Migration to add `deleted_date` to `file` table:**
```sql
ALTER TABLE file ADD COLUMN deleted_date TEXT;
```
Add to `_migrate()` in `db.py` with the standard guard pattern (check `PRAGMA table_info(file)` before altering).

**New db.py helpers:**
- `upsert_file_touch(conn, row: dict) -> None` — INSERT OR REPLACE
- `upsert_daily_task(conn, row: dict) -> None` — INSERT OR REPLACE

**Definition of done:**
- `init_db` creates both new tables without error on a fresh DB
- Migration adds `deleted_date` to existing DBs without error
- `upsert_file_touch` and `upsert_daily_task` round-trip correctly (insert, re-insert same PK replaces)

---

### HDP-S2 — Sync & Rollup: Populate new tables

**Files (expected):**
- `matlock/stages/sync.py`
- `matlock/stages/rollup.py`
- `matlock/db.py` (add import of new helpers)

**sync.py change:**
- In `mark_file_deleted` (in `db.py`): add `deleted_date = today_str` parameter, or update sync.py to set `deleted_date` directly when calling the deletion path.
- Simpler approach: update `db.mark_file_deleted` to also set `deleted_date = date.today().isoformat()`.

**rollup.py changes — `run_rollup`:**

After computing `daily_metric` rows (existing logic), add:

1. **Populate `file_touch`** for `rollup_date_str`:
   - Created: query `file` WHERE `date(created/1000, 'unixepoch') = rollup_date_str AND is_generated = 0`
   - Modified: query `file` WHERE `modified_date = rollup_date_str AND deleted = 0 AND is_generated = 0`
   - Deleted: query `file` WHERE `deleted_date = rollup_date_str AND deleted = 1 AND is_generated = 0`
   - For each matching file, call `upsert_file_touch(conn, {...})`
   - Idempotent: `INSERT OR REPLACE` handles re-runs

2. **Populate `daily_task`** for `rollup_date_str`:
   - Created: query `task` JOIN `file` WHERE `created_date = rollup_date_str AND file.deleted = 0` (include file to avoid deleted-file noise)
   - Completed: query `task` JOIN `file` WHERE `act_comp_date = rollup_date_str AND checked = 1 AND file.deleted = 0`
   - For each matching task, call `upsert_daily_task(conn, {...})`
   - Denormalize: store `task_text`, `file_path`, `attributes` at snapshot time

**Definition of done:**
- After `run_rollup(config, conn, yesterday)`, `file_touch` and `daily_task` have rows for `yesterday`
- Re-running rollup for the same date replaces rows (idempotent)
- Deletion events appear when `deleted_date` is set on a file

---

### HDP-S3 — Report: Enriched `_render_history_page`

**Files (expected):**
- `matlock/stages/report.py`

**New data queries in `_render_history_page`:**

1. **`file_touches`** — all `file_touch` rows for `metric_date`, ordered by `modified DESC NULLS LAST`:
   ```python
   rows = conn.execute(
       "SELECT file_path, event_type, modified FROM file_touch"
       " WHERE touch_date = ? ORDER BY modified DESC, file_path ASC",
       (metric_date,)
   ).fetchall()
   ```
   Build `SimpleNamespace` with `file_path`, `event_type`, `modified_text` (formatted timestamp), `source_link` (relative path to actual file).

2. **`task_list`** — all `daily_task` rows for `metric_date`, grouped by `task_id`:
   ```python
   rows = conn.execute(
       "SELECT task_id, event_type, task_text, file_path, attributes"
       " FROM daily_task WHERE event_date = ? ORDER BY task_id, event_type",
       (metric_date,)
   ).fetchall()
   ```
   Collapse into per-task objects with `events` list (e.g. `['created', 'completed']`), `estimate_display`, `source_link`, `project_link`.

3. **`super_project_summary`** — aggregate `daily_metric` via project→super_project:
   ```python
   # Join daily_metric → project → super_project
   # Group by super_project_id, SUM tasks_created/completed counts and minutes
   ```
   Build list of `SimpleNamespace(super_project_title, sp_link, tasks_created_count, tasks_created_minutes, tasks_completed_count, tasks_completed_minutes, files_modified_count)`.
   Include unassigned (NULL project_id) as a separate row.

4. **`next_date`** — first `metric_date > metric_date` in `daily_metric`:
   ```python
   row = conn.execute(
       "SELECT MIN(metric_date) AS next_d FROM daily_metric WHERE metric_date > ?",
       (metric_date,)
   ).fetchone()
   next_date = row["next_d"]  # None if no later date
   ```

5. **`generated_at`** — current timestamp string.

6. Existing `project_rows` — enhance to include `tasks_created_count`, `tasks_created_minutes` (from `daily_metric`), and replace the "Breakdown by Project" section with a proper table.

**Template variables to add/change:**

| Variable | Type | Source |
|:--|:--|:--|
| `generated_at` | str | Runtime |
| `next_date` | str \| None | `daily_metric` |
| `file_touches` | list[SimpleNamespace] | `file_touch` table |
| `task_list` | list[SimpleNamespace] | `daily_task` table |
| `super_project_summary` | list[SimpleNamespace] | `daily_metric` + project joins |

**Definition of done:**
- `_render_history_page` passes all new variables to the template
- When `file_touch` / `daily_task` have no rows (pre-migration), template renders gracefully with "No data available" fallback
- Existing template variables are preserved (no regressions)

---

### HDP-S4 — Template: `daily_history.md.j2`

**Files (expected):**
- `matlock/templates/daily_history.md.j2`

**New template structure:**

```jinja
---
**Generated:** {{ generated_at }} | [🏠 Home]({{ dashboard_link }}){% if prev_date %} | [⬅ {{ prev_date }}]({{ history_link(prev_date) }}){% endif %}{% if next_date %} | [{{ next_date }} ➡]({{ history_link(next_date) }}){% endif %}
---

# 📅 Daily History: {{ metric_date }}
**Day of Week:** {{ day_of_week }}

## 📈 Daily Totals
| Metric | Count | Minutes |
| :--- | :--- | :--- |
| **Tasks Completed** | {{ totals.tasks_completed_count }} | {{ totals.tasks_completed_minutes }} |
| **Tasks Created** | {{ totals.tasks_created_count }} | {{ totals.tasks_created_minutes }} |
| **Files Modified** | {{ totals.files_modified_count }} | — |

## 🌍 Summary by Super-Project
{% if super_project_summary %}
| Super-Project | Created | Created (m) | Completed | Completed (m) |
| :--- | :--- | :--- | :--- | :--- |
{% for row in super_project_summary %}
| {% if row.sp_link %}[{{ row.super_project_title }}]({{ row.sp_link }}){% else %}{{ row.super_project_title }}{% endif %} | {{ row.tasks_created_count }} | {{ row.tasks_created_minutes }} | {{ row.tasks_completed_count }} | {{ row.tasks_completed_minutes }} |
{% endfor %}
{% else %}
*No super-project data available.*
{% endif %}

## 🚀 Summary by Project
{% if project_rows %}
| Project | Created | Created (m) | Completed | Completed (m) | Overdue (EoD) |
| :--- | :--- | :--- | :--- | :--- | :--- |
{% for row in project_rows %}
| {% if row.project %}[{{ row.project.title }}]({{ project_link(row.project.id) }}){% else %}Unassigned{% endif %} | {{ row.tasks_created_count }} | {{ row.tasks_created_minutes }} | {{ row.tasks_completed_count }} | {{ row.tasks_completed_minutes }} | {{ row.tasks_past_due_count }} |
{% endfor %}
{% else %}
*No project data available.*
{% endif %}

## 📄 Files Touched
{% if file_touches %}
| File | Event | Time |
| :--- | :--- | :--- |
{% for ft in file_touches %}
| [{{ ft.file_path | basename }}]({{ ft.source_link }}) | {{ ft.event_type }} | {{ ft.modified_text }} |
{% endfor %}
{% else %}
*No file-touch data available for this date. (Data captured from {{ [metric_date, "2026-05-05"] | max }} onward.)*
{% endif %}

## ✅ Tasks This Day
{% if task_list %}
| Task | Events | Estimate | Project |
| :--- | :--- | :--- | :--- |
{% for t in task_list %}
| [{{ t.task_text }}]({{ t.source_link }}) | {{ t.events_display }} | {{ t.estimate_display }} | {% if t.project_link %}[{{ t.project_title }}]({{ t.project_link }}){% else %}—{% endif %} |
{% endfor %}
{% else %}
*No task-event data available for this date.*
{% endif %}
```

**Key design decisions:**
- Header block uses a YAML-like separator (`---`) so it renders as a distinct info block in Obsidian
- Prev/next links shown only if data exists (no dead links)
- File-touch and task list sections degrade gracefully with a note explaining when data collection started
- Task `events_display` is computed Python-side (e.g. `"created"`, `"completed"`, `"created + completed"`)
- File `source_link` is relative path from the history page to the source vault file (may be a dead link if file was deleted — this is acceptable and intentional)

**Definition of done:**
- Rendered output looks correct for a date that has `file_touch` / `daily_task` data
- Rendered output has graceful fallback for a date without that data
- Prev/next links are correct relative paths

---

### HDP-S5 — Docs Update

**Files (expected):**
- `docs/matlock-data-model.md` — add `file_touch` and `daily_task` table specs; add `deleted_date` column to `file` table
- `docs/matlock-pipeline-specification.md` — update Stage IV rollup (populate `file_touch`, `daily_task`); update sync (set `deleted_date`)
- `docs/matlock-generated-reports.md` — update daily history template and template variables

**Definition of done:**
- All three docs reflect new schema and behavior
- No stale references to old template structure remain

---

## Acceptance Criteria

- After a `rollup` run, `file_touch` has one row per file-event per date; `daily_task` has one row per task-event per date
- Re-running rollup for the same date produces identical results (idempotent)
- History pages for dates with `file_touch`/`daily_task` data render: header with generated_at + prev/next navigation, super-project summary, project summary, file-touch list, task list
- History pages for dates without `file_touch`/`daily_task` data render gracefully (current sections show fallback text)
- `deleted_date` is set on file rows when sync marks them deleted
- Full test suite passes
- Docs updated
- `CHANGELOG.md` updated
- No version bump required (internal feature)

## Risks / Notes

- **Backfill gap**: Dates before this change is deployed will have no `file_touch` / `daily_task` rows. Template handles this with a fallback message. No backfill is planned.
- **Large vaults**: `file_touch` may accumulate many rows over time. No pruning mechanism is planned; row count is bounded by (files × days) which is manageable.
- **`deleted_date` accuracy**: Once deployed, `deleted_date` reflects the day sync detected the deletion (not necessarily the day the user deleted the file). This is documented and acceptable.
- **Task identity across edits**: `daily_task` stores `task_id` (SHA-256 of text+position). If a task is edited after rollup, the snapshot in `daily_task` still reflects the original text. This is the desired behavior (historical record).

## Validation Plan

Run tests in this order:
1. Focused tests: `poetry run pytest tests/test_db.py tests/test_sync.py tests/test_rollup.py tests/test_report.py -v`
2. Adjacent/regression tests: `poetry run pytest tests/ -v`
3. Full suite: `poetry run pytest`

Record results:
- Focused: 158 passed (test_report.py), 122 passed (test_db + test_rollup + test_sync) ✅
- Regression: covered by full suite ✅
- Full suite: `poetry run pytest -q` → **737 passed** ✅

---

## Questions / Concerns

1. **`super_project_summary` for NULL project_id rows**: `daily_metric` has rows where `project_id IS NULL` representing unassigned tasks. Should these appear in the super-project summary as "Unassigned" or only in the project summary? **Recommendation:** Show in both sections as "Unassigned".

2. **`file_touch.modified` for deleted files**: Deleted files have `deleted=1` and `modified` reflects the file's last OS modification time (before deletion). Is this acceptable for display, or should we display "—" for the time on deletion events? **Recommendation:** Show "—" for deleted events since the time doesn't reflect when deletion was detected.

3. **`daily_task` task count could be large**: If many tasks are created/completed on a day, the task list table could be very long. Should we cap or paginate? **Recommendation:** No cap for V1 — history pages are historical reference pages, not live dashboards. If needed in future, a cap can be added.

4. **Task source link for deleted files**: If a file is deleted from the vault, `ft.source_link` in the task list will be a dead link. Should we show a plain text label instead of a link for deleted-file tasks? **Recommendation:** Keep the link (Obsidian handles dead links gracefully; it is useful to see the path even when the file is gone).

5. **`mark_file_deleted` signature change**: Currently `db.mark_file_deleted(conn, file_path)` takes two args. Adding `deleted_date` will require either adding a third parameter with a default, or updating sync.py to set it separately. **Recommendation:** Update `db.mark_file_deleted` to also accept an optional `deleted_date` parameter (defaults to `date.today().isoformat()`), keeping backward compatibility.

6. **`project_rows` enhancement**: Currently `project_rows` only has `tasks_completed_count`, `tasks_completed_minutes`, `tasks_past_due_count`. Adding `tasks_created_count` and `tasks_created_minutes` requires reading those from `daily_metric`. Is this the right source? **Recommendation:** Yes — `daily_metric` already has this data; read it at report time per existing pattern.

---

## Design Decisions (Resolved)

All six questions resolved by user: "use best judgment for all and continue."

1. **Unassigned in super-project summary**: Shown in both sections as "Unassigned". ✅
2. **Deleted file `modified_text`**: Displays "—" for deleted events. ✅
3. **Task list cap**: No cap for V1. ✅
4. **Dead source links**: Keep link (path still useful). ✅
5. **`mark_file_deleted` signature**: Added optional `deleted_date` param defaulting to today. ✅
6. **`project_rows` source for created counts**: `daily_metric` (already has this data). ✅

---

## Step Notes Log

### HDP-S1 — Completed
- Added `file_touch` and `daily_task` tables to `_SCHEMA` in `db.py`
- Added `deleted_date TEXT` migration to `_migrate()` with guard pattern
- Added `upsert_file_touch(conn, row)` and `upsert_daily_task(conn, row)` helpers
- Updated `mark_file_deleted` to accept optional `deleted_date` param (defaults to today)
- Note: `deleted_date` kept out of `_FILE_COLUMNS` intentionally — `upsert_file` NULLs it on re-sync of a returned file (correct), `mark_file_deleted` manages it via UPDATE
- Validation: `poetry run pytest tests/test_db.py -v` → 37 passed ✅

### HDP-S2 — Completed
- Added `_populate_file_touch(conn, rollup_date_str)` to `rollup.py`
- Added `_populate_daily_tasks(conn, rollup_date_str)` to `rollup.py`
- Both called from `run_rollup()` after existing `daily_metric` logic, before `conn.commit()`
- `sync.py` already calls `mark_file_deleted` which now sets `deleted_date`
- `test_rollup.py` `_seed_file` extended with optional `deleted_date` param (separate UPDATE)
- Validation: `poetry run pytest tests/test_db.py tests/test_rollup.py tests/test_sync.py -v` → 122 passed ✅

### HDP-S3 — Completed
- `_render_history_page` fully replaced in `report.py`
- Added queries for: `file_touch`, `daily_task`, `super_project_summary`, `next_date`
- `project_rows` enriched with `tasks_created_count` and `tasks_created_minutes`
- Fixed: ambiguous column name `title` in super_project JOIN query (used explicit table aliases)
- Added 12 new tests to `TestRunReportHistoryPage`; updated `_seed_metric` to accept `created` param
- Validation: `poetry run pytest tests/test_report.py -v` → 158 passed ✅

### HDP-S4 — Completed
- Full redesign of `daily_history.md.j2`
- New sections: header with generated_at + prev/next nav, super-project summary table, project summary table (now tabular), files touched table, tasks this day table
- Graceful fallback text for dates without `file_touch`/`daily_task` data
- Validation: covered by HDP-S3 test run (158 passed) ✅

### HDP-S5 — Completed
- `docs/matlock-data-model.md`: added `deleted_date` to `file` table, added full `file_touch` and `daily_task` table specs
- `docs/matlock-pipeline-specification.md`: updated Stage I sync (deleted_date), updated Stage IV rollup (populate file_touch, daily_task — steps 5 and 6)
- `docs/matlock-generated-reports.md`: replaced old daily history template spec with new design, added full template variables table
