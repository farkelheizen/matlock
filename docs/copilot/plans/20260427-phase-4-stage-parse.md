# 20260427-phase-4 Plan: Stage II — Parse (DB Integration)

> Date: 4/27/2026
> Owner: Copilot
> Branch: feat/phase-4-stage-parse
> Related docs: `docs/matlock-pipeline-specification.md`, `docs/matlock-data-model.md`, `docs/matlock-configuration.md`

## Problem Summary

After `sync` runs, the `file` table has rows with `needs_parsing = 1` for every new or changed file. No tasks exist for these files yet. The `parse` stage is responsible for reading each flagged file, running the existing extraction engine (`extract_tasks_from_markdown`), and persisting results to the `task` table — clearing the `needs_parsing` flag when done.

The extraction engine (`matlock/extractor.py`) already works correctly in isolation. This phase is purely about wiring it to the DB: querying which files need parsing, driving the extract→delete→insert cycle, updating `file` metadata, and exposing the `matlock parse` CLI subcommand.

## Goal

Implement `matlock/stages/parse.py` (DB-backed parse loop with error isolation), add the `matlock parse` CLI subcommand to `matlock/cli.py`, and write comprehensive tests covering DB state before and after parsing.

## Scope

- **In scope:**
  - `matlock/stages/parse.py` — `ParseResult`, `run_parse(config, conn)`, `_build_extractor_config(config)`
  - `matlock/cli.py` — add `parse` subcommand
  - `tests/test_parse.py` — unit/integration tests using `tmp_path` + `":memory:"`
  - Update `docs/roadmap/index.md` Phase 4 row when complete

- **Out of scope:**
  - Modifying `matlock/extractor.py` — it is not touched in this phase
  - `--file` flag implementation (deferred; see Q4)
  - `is_generated` flag — set by `report` stage, not `parse`
  - Project mapping — covered by Phase 5

## Constraints / Requirements

- `run_parse(config: MatlockConfig, conn: sqlite3.Connection) -> ParseResult` — pure function, no side effects beyond DB writes.
- Files are processed in the order returned by `get_files_needing_parsing()`.
- Error isolation: if `extract_tasks_from_markdown` raises for a file, that file is skipped entirely (no DB writes for it), `needs_parsing` remains `1`, and processing continues with the next file.
- The extraction must happen **before** any DB writes for that file — to prevent delete-without-reinsert if extraction fails.
- `conn.commit()` is called once at the end of `run_parse()`, covering all successful file writes in a single transaction.
- All DB helpers from `matlock/db.py` are used — no raw SQL in the stage.
- `validate_config_paths()` is called by the CLI before invoking the stage.
- `word_count` is computed by the parse stage as `len(body.split())` where `body` is the post-frontmatter text (see D3).
- `meta_data` is stored as a JSON string in the `file` row.

---

## Implementation Plan

| Step ID | Status | Goal | Planned Changes | Test Coverage |
|---|---|---|---|---|
| P4-S1 | Completed | Implement `matlock/stages/parse.py` | `run_parse()`, `_build_extractor_config()`, `ParseResult` | `tests/test_parse.py`: extract→insert, error isolation, word_count, meta_data, needs_parsing flag |
| P4-S2 | Completed | Add `parse` subcommand to `matlock/cli.py` | `cli.py` — new `parse` command | `tests/test_cli_parse.py`: CLI invocation, `--config`, error cases |
| P4-S3 | Completed | Regression + commit | Full suite green | `poetry run pytest` |

Status values: `Not Started` | `In Progress` | `Completed` | `Blocked`

---

## Step Details

### P4-S1 — Implement `matlock/stages/parse.py`

**Files (expected):**
- `matlock/stages/parse.py` *(new)*

**`ParseResult` dataclass:**

```python
@dataclasses.dataclass
class ParseResult:
    parsed: int = 0    # files successfully parsed and committed
    skipped: int = 0   # files that raised an exception (needs_parsing left as 1)
    tasks_inserted: int = 0   # total task rows inserted across all parsed files
    tasks_deleted: int = 0    # total task rows deleted (stale) across all parsed files
```

**`_build_extractor_config(config: MatlockConfig) -> dict`** — converts `MatlockConfig` into the `config_dict` format expected by `extract_tasks_from_markdown`:

```python
{
    "headers": {"header_text_maxlen": config.headers.header_text_maxlen},
    "tasks": {
        "task_text_maxlen": config.tasks.task_text_maxlen,
        "attributes": {
            name: {
                "type": attr.type,
                **({"alias": attr.alias} if attr.alias else {}),
                **({"values": {
                    vname: ({"alias": v.alias} if v.alias else {})
                    for vname, v in attr.values.items()
                }} if attr.type == "domain" and attr.values else {}),
            }
            for name, attr in config.task_attributes.items()
        }
    }
}
```

**`run_parse(config, conn) -> ParseResult`** — per-file loop:

```
extractor_cfg = _build_extractor_config(config)
files = get_files_needing_parsing(conn)  # needs_parsing=1, deleted=0

for file_row in files:
    file_path = file_row["file_path"]
    abs_path = config.base_directory / file_path

    try:
        content = abs_path.read_text(encoding="utf-8")
        parsed_file = extract_tasks_from_markdown(content, extractor_cfg, file_path)
    except Exception:
        result.skipped += 1
        continue   # no DB writes; needs_parsing stays 1

    # --- DB writes (only reached if extract succeeded) ---
    old_tasks = get_tasks_for_file(conn, file_path)
    delete_tasks_for_file(conn, file_path)
    result.tasks_deleted += len(old_tasks)

    for task in parsed_file.tasks:
        upsert_task(conn, _task_to_dict(file_path, task))
        result.tasks_inserted += 1

    # update file metadata
    body = content  # raw text used for word_count
    meta_json = json.dumps(parsed_file.meta_data)
    word_count = len(body.split())
    conn.execute(
        "UPDATE file SET needs_parsing=0, word_count=?, meta_data=? WHERE file_path=?",
        (word_count, meta_json, file_path),
    )
    result.parsed += 1

conn.commit()
return result
```

**`_task_to_dict(file_path: str, task: ParsedMarkdownTask) -> dict`** — maps a `ParsedMarkdownTask` to the `task` table column dict:

```python
{
    "task_id": task.task_id,
    "file_path": file_path,
    "parent_task_id": task.parent_task_id,
    "created_date": task.attributes.get("created_date"),
    "due_date": task.attributes.get("due_date"),
    "est_comp_date": task.attributes.get("est_comp_date"),
    "act_comp_date": task.attributes.get("act_comp_date"),
    "checked": 1 if task.checked else 0,
    "task_text": task.task_text,
    "overflow": 1 if task.overflow else 0,
    "headers": json.dumps(task.headers),
    "attributes": json.dumps(task.attributes),
    "errors": json.dumps(task.errors),
    "twin_index": task.twin_index,
}
```

**Definition of done:**
- A file with `needs_parsing=1` is parsed; its tasks are in the `task` table; `needs_parsing=0` after.
- Running `run_parse` again (all `needs_parsing=0`) produces `parsed=0`.
- A file that raises during extraction has `needs_parsing=1` and no task changes in DB.
- `word_count` and `meta_data` are written to the `file` row.
- `tasks_deleted` is non-zero when re-parsing a file that already has tasks.

---

### P4-S2 — Add `parse` Subcommand to `matlock/cli.py`

**Files (expected):**
- `matlock/cli.py` *(modified)*

**New command:**

```python
@app.command()
def parse(ctx: typer.Context) -> None:
    """Parse all files flagged by sync (needs_parsing=1)."""
    ...
    result = run_parse(cfg, conn)
    typer.echo(
        f"Parse complete: {result.parsed} parsed, {result.skipped} skipped, "
        f"{result.tasks_inserted} tasks inserted, {result.tasks_deleted} tasks deleted"
    )
```

**Definition of done:**
- `poetry run matlock parse --help` works.
- `poetry run matlock sync && poetry run matlock parse` against a `tmp_path` vault produces correct task rows.

---

### P4-S3 — Regression + Commit

**Files (expected):**
- `docs/roadmap/index.md` — Phase 4 row updated
- `docs/copilot/current-plan.md` — updated

**Definition of done:**
- `poetry run pytest` passes with 173+ tests, zero failures.

---

## Acceptance Criteria

- After `matlock sync && matlock parse`, the `task` table contains accurate task data for all `.md` files.
- Re-running `matlock parse` with no changed files (`needs_parsing=0`) is a no-op.
- A file that fails extraction is skipped; its `needs_parsing` flag stays `1`; other files are unaffected.
- `word_count` and `meta_data` (JSON front-matter) are populated on the `file` row after parsing.
- `tasks_deleted` correctly reflects stale task removal when re-parsing modified files.
- Full suite passes with zero regressions.

## Risks / Notes

- **`extract_tasks_from_markdown` takes `config_dict: dict`** (not `MatlockConfig`). The `_build_extractor_config()` helper bridges this gap. Its output must match the format expected by `build_aliases()` and `parse_attribute_value()` in `extractor.py`.
- **Fixed date attribute names** (`created_date`, `due_date`, `est_comp_date`, `act_comp_date`) are hardcoded in `_task_to_dict`. If a user's config uses different attribute names, those DB columns will be NULL. This is a spec constraint, not a bug.
- **`word_count` approximation**: `len(content.split())` counts all whitespace-separated tokens including YAML front-matter keys, Markdown syntax tokens, attribute strings, etc. It is an approximation. A more accurate count would split only the post-frontmatter body. See D3.
- **File encoding**: The spec does not address non-UTF-8 files. The stage opens files as UTF-8 and treats a `UnicodeDecodeError` as a parse failure (file skipped with `needs_parsing=1`).
- **`abs_path` existence**: If a file row has `needs_parsing=1` but the physical file is gone (deleted between sync and parse), `read_text()` raises `FileNotFoundError` — caught by the broad `except Exception`, file is skipped. Acceptable.

## Validation Plan

Run tests in this order:
1. Focused tests: `poetry run pytest tests/test_parse.py -v`
2. CLI tests: `poetry run pytest tests/test_cli_parse.py -v`
3. Regression tests: `poetry run pytest tests/test_sync.py tests/test_db.py tests/test_matlock_config.py -v`
4. Full suite: `poetry run pytest`

Record results:
- Focused (test_parse.py): **27 passed**
- CLI (test_cli_parse.py): **11 passed**
- Regression (test_sync.py + test_db.py + test_matlock_config.py): **77 passed**
- Full suite: **211 passed, 0 failures**

---

## Design Decisions (Resolved)

**D1 — `_build_extractor_config` placement: private in `stages/parse.py`**
Only the parse stage calls the extractor. Keeping it module-private avoids polluting the config module with an extractor-specific concern.

**D2 — Fixed DB date column names: hardcoded**
The four standard attribute names (`created_date`, `due_date`, `est_comp_date`, `act_comp_date`) are hardcoded in `_task_to_dict`. Config-driven mapping can be added later if needed.

**D3 — `word_count`: post-frontmatter body only**
Call `parse_front_matter(content)` in the parse stage explicitly to obtain `body`, then compute `len(body.split())`. More accurate than counting raw file text including YAML keys. Note: the `run_parse` pseudocode in the step details uses `body = content` as a placeholder — the implementation must call `parse_front_matter` first.

**D4 — `--file PATH` flag: deferred**
Keep Phase 4 focused on the core parse loop. The `--file` flag adds path resolution complexity and can be added in a follow-up without changing stage internals.
