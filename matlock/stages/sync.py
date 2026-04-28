"""
Matlock sync stage.

Walks the vault directory, hashes every ``.md`` file, and keeps the
``file`` table in sync with the physical filesystem.

Public API:
    run_sync(config, conn, force=False) -> SyncResult
"""

from __future__ import annotations

import dataclasses
import hashlib
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from matlock.config import MatlockConfig
from matlock.db import get_file, mark_file_deleted, upsert_file


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class SyncResult:
    """Counts of file-table changes produced by a single sync run."""

    inserted: int = 0
    updated: int = 0
    unchanged: int = 0
    deleted: int = 0


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _hash_file(path: Path) -> str:
    """Return the lowercase hex SHA-256 digest of *path*'s contents."""
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _walk_vault(
    base_dir: Path,
    output_dir: Path,
    ignore_dirs: list[str],
) -> list[Path]:
    """Yield all ``.md`` files under *base_dir*, skipping excluded paths.

    Exclusions:
    - *output_dir* and any of its descendants.
    - Any directory whose name appears in *ignore_dirs*.

    Uses ``os.walk`` with ``followlinks=False`` to avoid symlink loops.
    """
    output_dir_resolved = output_dir.resolve()
    ignore_set = set(ignore_dirs)
    results: list[Path] = []

    for dirpath, dirnames, filenames in os.walk(base_dir, followlinks=False):
        current = Path(dirpath).resolve()

        # Skip output_directory and its descendants
        try:
            current.relative_to(output_dir_resolved)
            dirnames.clear()
            continue
        except ValueError:
            pass

        # Prune ignored directory names in-place so os.walk skips them
        dirnames[:] = [d for d in dirnames if d not in ignore_set]

        for filename in filenames:
            if Path(filename).suffix.lower() == ".md":
                results.append(Path(dirpath) / filename)

    return results


def _stat_times(stat_result: os.stat_result) -> tuple[int, int]:
    """Return (created_ms, modified_ms) as integer Unix milliseconds."""
    created_s: float = getattr(stat_result, "st_birthtime", stat_result.st_ctime)
    modified_s: float = stat_result.st_mtime
    return int(created_s * 1000), int(modified_s * 1000)


def _modified_date(modified_ms: int) -> str:
    """Return the UTC date string 'YYYY-MM-DD' from *modified_ms*."""
    dt = datetime.fromtimestamp(modified_ms / 1000, tz=timezone.utc)
    return dt.strftime("%Y-%m-%d")


# ---------------------------------------------------------------------------
# Public stage function
# ---------------------------------------------------------------------------


def run_sync(
    config: MatlockConfig,
    conn: sqlite3.Connection,
    force: bool = False,
) -> SyncResult:
    """Synchronise the ``file`` table with the physical vault.

    Steps:
    1. Walk *config.base_directory*, skipping *output_directory* and
       *ignore_dirs*.
    2. For each discovered ``.md`` file:
       - If new: insert with ``needs_parsing=1``.
       - If changed (hash differs) or *force*: update with ``needs_parsing=1``.
       - Otherwise: leave untouched (count as unchanged).
    3. Soft-delete any previously-tracked non-deleted file no longer on disk.
    4. ``conn.commit()`` once — all-or-nothing transaction.

    Does not parse file content.  Does not call ``validate_config_paths()``.
    Callers are responsible for path validation before invoking this function.
    """
    result = SyncResult()
    seen: set[str] = set()

    paths = _walk_vault(
        config.base_directory,
        config.output_directory,
        config.ignore_dirs,
    )

    for abs_path in paths:
        rel_posix = abs_path.relative_to(config.base_directory).as_posix()
        seen.add(rel_posix)

        sha256 = _hash_file(abs_path)
        stat = abs_path.stat()
        created_ms, modified_ms = _stat_times(stat)

        existing = get_file(conn, rel_posix)

        if existing is None:
            upsert_file(conn, {
                "file_path": rel_posix,
                "sha256": sha256,
                "file_ext": abs_path.suffix.lower(),
                "created": created_ms,
                "modified": modified_ms,
                "modified_date": _modified_date(modified_ms),
                "deleted": 0,
                "length": stat.st_size,
                "word_count": None,
                "meta_data": None,
                "is_generated": 0,
                "needs_parsing": 1,
            })
            result.inserted += 1
        elif force or existing["sha256"] != sha256:
            upsert_file(conn, {
                "file_path": rel_posix,
                "sha256": sha256,
                "file_ext": abs_path.suffix.lower(),
                "created": existing["created"],
                "modified": modified_ms,
                "modified_date": _modified_date(modified_ms),
                "deleted": 0,
                "length": stat.st_size,
                "word_count": existing["word_count"],
                "meta_data": existing["meta_data"],
                "is_generated": existing["is_generated"],
                "needs_parsing": 1,
            })
            result.updated += 1
        else:
            result.unchanged += 1

    # Soft-delete files no longer on disk
    rows = conn.execute(
        "SELECT file_path FROM file WHERE deleted = 0"
    ).fetchall()
    for row in rows:
        if row["file_path"] not in seen:
            mark_file_deleted(conn, row["file_path"])
            result.deleted += 1

    conn.commit()
    return result
