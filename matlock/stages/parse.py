"""
Matlock parse stage.

Reads all files flagged ``needs_parsing = 1`` from the ``file`` table,
runs ``extract_tasks_from_markdown`` on each, and persists the results
to the ``task`` table.  ``file`` metadata (``word_count``, ``meta_data``,
``needs_parsing``) is updated after each successful extraction.

Public API:
    run_parse(config, conn) -> ParseResult
"""

from __future__ import annotations

import dataclasses
import json
import sqlite3

from matlock.config import MatlockConfig
from matlock.db import (
    delete_tasks_for_file,
    get_files_needing_parsing,
    get_tasks_for_file,
    upsert_task,
)
from matlock.extractor import extract_tasks_from_markdown
from matlock.models import ParsedMarkdownTask
from matlock.parser import parse_front_matter


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class ParseResult:
    """Counts produced by a single parse run."""

    parsed: int = 0          # files successfully extracted and committed
    skipped: int = 0         # files that raised an exception (needs_parsing left as 1)
    tasks_inserted: int = 0  # total task rows inserted across all parsed files
    tasks_deleted: int = 0   # total task rows deleted (stale) across all parsed files


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _build_extractor_config(config: MatlockConfig) -> dict:
    """Convert ``MatlockConfig`` into the ``config_dict`` dict expected by
    ``extract_tasks_from_markdown``."""
    attributes: dict = {}
    for name, attr in config.task_attributes.items():
        entry: dict = {"type": attr.type}
        if attr.alias:
            entry["alias"] = attr.alias
        if attr.type == "domain" and attr.values:
            entry["values"] = {
                vname: ({"alias": v.alias} if v.alias else {})
                for vname, v in attr.values.items()
            }
        attributes[name] = entry

    return {
        "headers": {"header_text_maxlen": config.headers.header_text_maxlen},
        "tasks": {
            "task_text_maxlen": config.tasks.task_text_maxlen,
            "attributes": attributes,
        },
    }


def _task_to_dict(file_path: str, task: ParsedMarkdownTask) -> dict:
    """Map a ``ParsedMarkdownTask`` to a ``task`` table column dict."""
    attrs = task.attributes
    return {
        "task_id": task.task_id,
        "file_path": file_path,
        "parent_task_id": task.parent_task_id,
        "created_date": attrs.get("created_date"),
        "due_date": attrs.get("due_date"),
        "est_comp_date": attrs.get("est_comp_date"),
        "act_comp_date": attrs.get("act_comp_date"),
        "checked": 1 if task.checked else 0,
        "task_text": task.task_text,
        "overflow": 1 if task.overflow else 0,
        "headers": json.dumps(task.headers),
        "attributes": json.dumps(attrs),
        "errors": json.dumps(task.errors),
        "twin_index": task.twin_index,
    }


# ---------------------------------------------------------------------------
# Public stage function
# ---------------------------------------------------------------------------


def run_parse(
    config: MatlockConfig,
    conn: sqlite3.Connection,
) -> ParseResult:
    """Parse all files with ``needs_parsing = 1`` and update the ``task`` table.

    For each flagged file:
    1. Read the file from disk and run ``extract_tasks_from_markdown``.
    2. On success: delete stale tasks, insert new tasks, update ``file``
       metadata (``word_count``, ``meta_data``), set ``needs_parsing = 0``.
    3. On exception: increment ``skipped``; leave DB unchanged for that file
       (``needs_parsing`` remains ``1``).

    A single ``conn.commit()`` covers all successful writes at the end.
    Does not call ``validate_config_paths()`` — that is the CLI's responsibility.
    """
    result = ParseResult()
    extractor_cfg = _build_extractor_config(config)
    files = get_files_needing_parsing(conn)

    for file_row in files:
        file_path: str = file_row["file_path"]
        abs_path = config.base_directory / file_path

        # --- Extraction (no DB writes yet) ---
        try:
            content = abs_path.read_text(encoding="utf-8")
            parsed_file = extract_tasks_from_markdown(content, extractor_cfg, file_path)
        except Exception:
            result.skipped += 1
            continue

        # --- DB writes (only reached on success) ---
        _, body = parse_front_matter(content)
        word_count = len(body.split())
        meta_json = json.dumps(parsed_file.meta_data)

        old_tasks = get_tasks_for_file(conn, file_path)
        delete_tasks_for_file(conn, file_path)
        result.tasks_deleted += len(old_tasks)

        for task in parsed_file.tasks:
            upsert_task(conn, _task_to_dict(file_path, task))
            result.tasks_inserted += 1

        conn.execute(
            "UPDATE file SET needs_parsing = 0, word_count = ?, meta_data = ?"
            " WHERE file_path = ?",
            (word_count, meta_json, file_path),
        )
        result.parsed += 1

    conn.commit()
    return result
