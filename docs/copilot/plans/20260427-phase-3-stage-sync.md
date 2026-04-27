# 20260427-phase-3 Plan: Stage I — Sync

> Date: 4/27/2026
> Owner: Copilot
> Branch: feat/phase-3-stage-sync
> Related docs: `docs/matlock-pipeline-specification.md`, `docs/matlock-cli.md`, `docs/matlock-configuration.md`, `docs/matlock-data-model.md`

## Problem Summary

No pipeline stage exists yet. The first stage — `sync` — is responsible for keeping the `file` table in sync with the physical vault on disk. It is the entry point to every pipeline run: no file can be parsed, mapped, or reported on unless `sync` has registered it first.

The sync stage is also where the CLI framework is introduced. Currently `pyproject.toml` registers `matlock = "matlock.cli:app"` as a script entrypoint, but `matlock/cli.py` does not exist. This phase creates it alongside the sync stage module.

## Goal

Implement `matlock/stages/sync.py` (filesystem walk, SHA-256 hashing, `file` table upsert logic, soft-delete), `matlock/cli.py` (CLI entrypoint with `sync` subcommand, `--config`, `--force`), add the CLI framework as a dependency, and write comprehensive tests including a mocked-filesystem test suite.

## Scope

- **In scope:**
  - Add CLI framework dependency to `pyproject.toml` (see Q1)
  - `matlock/cli.py` — top-level CLI app with `--config` global option and `sync` subcommand
  - `matlock/stages/sync.py` — `run_sync(config, force=False)` pure function (no CLI coupling)
  - `tests/test_sync.py` — unit tests using `tmp_path` real filesystem (no mocking needed at this scale)
  - Update `docs/roadmap/index.md` Phase 3 row when work begins/completes

- **Out of scope:**
  - Any other CLI subcommands (`parse`, `map-projects`, etc.) — stubs only at most
  - File extension filtering beyond `.md` — spec only mentions `.md`
  - `is_generated` flag population — set by `report` stage, not by `sync`
  - Non-`.md` file tracking

## Constraints / Requirements

- `run_sync(config: MatlockConfig, force: bool = False) -> SyncResult` must be a pure function with no side effects beyond DB writes. It accepts an already-opened `sqlite3.Connection` or a `MatlockConfig` (see Q2).
- The sync stage must **never** parse file content — only read bytes for hashing.
- `output_directory` and all paths listed in `ignore_dirs` (plus `output_directory` always) are skipped without error even if they do not exist on disk.
- File walk is limited to `.md` files only (case-insensitive extension check).
- `file_ext` stored in the `file` table is the lowercase extension (e.g., `".md"`).
- `file_path` stored is **relative to `base_directory`** (POSIX separators).
- All DB writes go through the helpers in `matlock/db.py` — no raw SQL in the stage.
- The CLI `--config` option defaults to `./config.yaml`. If the file does not exist, the command exits with a clear error message and non-zero exit code.
- `validate_config_paths()` is called by the CLI before invoking the stage, not inside the stage itself.

---

## Implementation Plan

| Step ID | Status | Goal | Planned Changes | Test Coverage |
|---|---|---|---|---|
| P3-S1 | Completed | Add CLI framework dependency | `pyproject.toml` — `poetry add <framework>` | `poetry run matlock --help` works |
| P3-S2 | Completed | Implement `matlock/stages/sync.py` | `run_sync()`, `_walk_vault()`, `_hash_file()`, `SyncResult` | `tests/test_sync.py`: walk, hash, new/changed/unchanged/deleted logic, `--force` |
| P3-S3 | Completed | Implement `matlock/cli.py` | Top-level app, `--config` option, `sync` subcommand wired to `run_sync()` | `tests/test_cli_sync.py`: CLI invocation via `CliRunner` or subprocess |
| P3-S4 | Completed | Regression + commit | Full suite green | `poetry run pytest` |

Status values: `Not Started` | `In Progress` | `Completed` | `Blocked`

---

## Step Details

### P3-S1 — Add CLI Framework Dependency

**Files (expected):**
- `pyproject.toml`

**Implementation notes:**
- Decision pending Q1. Either `typer` (`poetry add "typer[all]"`) or `click` (`poetry add click`).
- No code changes in this step.

**Definition of done:**
- `poetry run matlock --help` exits without `ModuleNotFoundError`.

---

### P3-S2 — Implement `matlock/stages/sync.py`

**Files (expected):**
- `matlock/stages/sync.py` *(new)*

**`SyncResult` dataclass** (or `@dataclasses.dataclass`):

```python
@dataclasses.dataclass
class SyncResult:
    inserted: int = 0    # new files added
    updated: int = 0     # existing files with hash change
    unchanged: int = 0   # existing files with matching hash
    deleted: int = 0     # files soft-deleted (no longer on disk)
```

**`run_sync(config, conn, force=False) -> SyncResult`** — accepts `MatlockConfig` and an open `sqlite3.Connection`. Returns a `SyncResult`. Signature chosen to separate DB management from stage logic (see D2).

**Walk logic:**

```
for each .md file under base_directory (recursive):
    skip if path is inside output_directory
    skip if path is inside any directory in ignore_dirs
    compute sha256 of file bytes
    relative_path = path.relative_to(base_directory), as POSIX string

    existing_row = get_file(conn, relative_path)
    if existing_row is None:
        upsert_file(conn, {..., needs_parsing=1})
        result.inserted += 1
    elif force or existing_row["sha256"] != new_sha256:
        upsert_file(conn, {..., needs_parsing=1})
        result.updated += 1
    else:
        result.unchanged += 1

for each file row in DB where deleted=0 and not found on disk:
    mark_file_deleted(conn, file_path)
    result.deleted += 1
```

**`_hash_file(path: Path) -> str`** — reads file in binary mode, returns lowercase hex SHA-256.

**`_walk_vault(base_dir: Path, output_dir: Path, ignore_dirs: list[str]) -> Iterator[Path]`** — yields `.md` paths. Skips `output_dir` and any path whose parts include a directory name in `ignore_dirs`.

**Implementation notes:**
- Soft-delete detection: after the walk, query `get_files_needing_parsing`? No — query all non-deleted rows and compare against the set of files found on disk. See Q3.
- `conn.commit()` is called once at the end of `run_sync()`, not after each file write.
- `file_ext` is stored as the lowercased suffix (`.md`).
- `created` and `modified` are populated from `os.stat()` (`st_birthtime` on macOS, `st_ctime` as fallback on Linux).
- `modified_date` is derived from `modified` as `YYYY-MM-DD` (UTC).
- `word_count` and `meta_data` in the `file` row are left as `NULL` by sync — populated by the parse stage.
- `length` is `os.stat().st_size`.

**Definition of done:**
- Given a `tmp_path` vault with 3 `.md` files, `run_sync()` inserts all 3 with `needs_parsing=1`.
- Running sync again (unchanged) produces `unchanged=3`, no DB writes.
- Modifying one file and re-syncing produces `updated=1`, `needs_parsing=1` on that file.
- Deleting a file from disk and re-syncing produces `deleted=1`, `deleted=1` in DB.
- `--force` re-marks all files `needs_parsing=1` regardless of hash.
- Files inside `output_directory` are skipped.
- Files inside `ignore_dirs` entries are skipped.

---

### P3-S3 — Implement `matlock/cli.py`

**Files (expected):**
- `matlock/cli.py` *(new)*

**Structure (Typer example — adjust if Click chosen per Q1):**

```python
app = typer.Typer()

@app.callback()
def main(ctx: typer.Context, config: Path = typer.Option(Path("config.yaml"), ...)):
    ...

@app.command()
def sync(ctx: typer.Context, force: bool = typer.Option(False, "--force")):
    ...
```

**CLI behaviour:**
- Load config via `load_config(config_path)`. If `FileNotFoundError` or `ValidationError`, print a clear error and call `raise SystemExit(1)`.
- Call `validate_config_paths(config)`. If `ValueError`, print error and `raise SystemExit(1)`.
- Open DB connection via `get_connection(config.db_path)`.
- Call `init_db(conn)` (idempotent — safe on first run).
- Call `run_sync(config, conn, force=force)`.
- Print a one-line summary: `Sync complete: 3 inserted, 0 updated, 5 unchanged, 1 deleted`.
- Commit and close connection.

**Definition of done:**
- `poetry run matlock --help` shows top-level help.
- `poetry run matlock sync --help` shows sync-specific options.
- Running `matlock sync` against a real `tmp_path` vault via `CliRunner` (or subprocess) produces the expected summary output and correct DB state.

---

### P3-S4 — Regression + Commit

**Files (expected):**
- `docs/roadmap/index.md` — Phase 3 row updated
- `docs/copilot/current-plan.md` — updated

**Definition of done:**
- `poetry run pytest` passes with 132+ tests, zero failures.

---

## Acceptance Criteria

- `poetry run matlock sync` against a real vault directory correctly inserts, updates, and soft-deletes `file` rows.
- `poetry run matlock sync --force` sets `needs_parsing=1` on all non-deleted files.
- Files in `output_directory` and `ignore_dirs` are never inserted into the DB.
- `SyncResult` counts are accurate and printed to stdout.
- All sync logic tested in `tests/test_sync.py` using `tmp_path`.
- CLI error handling tested: missing config file → non-zero exit; invalid config → non-zero exit.
- Full suite passes with zero regressions.

## Risks / Notes

- **`st_birthtime` vs `st_ctime`**: macOS `stat` has `st_birthtime` for true creation time; Linux does not. Use `getattr(stat_result, "st_birthtime", stat_result.st_ctime)` for portability.
- **Symlinks**: The spec does not mention symlinks. `Path.rglob("*.md")` follows symlinks by default, which could create infinite loops for circular symlinks. Safe default: use `os.walk()` with `followlinks=False`, which gives more control.
- **Large vaults**: Hashing every file on every sync call is O(n × file size). Acceptable for Phase 3; a future optimisation could use `modified` timestamp as a pre-check before hashing.
- **Transaction isolation**: A single `conn.commit()` at the end of `run_sync()` means a mid-run crash leaves the DB unchanged (all-or-nothing). This is the correct behaviour.

## Validation Plan

Run tests in this order:
1. Focused tests: `poetry run pytest tests/test_sync.py -v`
2. Regression tests: `poetry run pytest tests/test_db.py tests/test_matlock_config.py -v`
3. Full suite: `poetry run pytest`

Record results:
- Focused (test_sync.py): **28 passed**
- Focused (test_cli_sync.py): **13 passed**
- Regression (test_db.py + test_matlock_config.py): **49 passed**
- Full suite: **173 passed, 0 failures**

---

## Design Decisions (Resolved)

**D1 — CLI framework: Typer (`typer[all]`)**
Chosen over Click. Pydantic + Typer is a natural fit; each future subcommand is a type-annotated function with minimal boilerplate.

**D2 — `run_sync()` signature: `run_sync(config, conn, force)`**
Accepts an already-opened `sqlite3.Connection`. Caller (CLI) opens and manages the connection. Consistent with Phase 2 DB helper pattern and trivially testable with `":memory:"`.

**D3 — Soft-delete detection: seen-set diff (Option A)**
Collect all discovered `relative_path` values into a Python set during the walk. After the walk, query all non-deleted DB rows and soft-delete any not in the set.

**D4 — CLI test strategy: `CliRunner` + one subprocess smoke test**
`typer.testing.CliRunner` for all unit/integration tests. One `subprocess` invocation to confirm the installed `matlock` entrypoint resolves correctly.

**Q4 — CLI test strategy: `CliRunner` vs subprocess**
- **Typer/Click `CliRunner`**: in-process, fast, captures stdout/stderr, but shares the same process state (e.g., working directory). Slightly fragile for `--config` path resolution if CWD matters.
- **`subprocess`**: truly isolated, tests the installed script, but slower and harder to inspect internals.
**Recommendation: `CliRunner` for unit/integration tests; one smoke-test subprocess call to confirm the installed script resolves correctly.**
