"""Matlock parse stage.

Reads all files flagged ``needs_parsing = 1`` from the ``file`` table,
runs ``extract_tasks_from_markdown`` on each, and persists the results
to the ``task`` table. ``file`` metadata (``word_count``, ``meta_data``,
``needs_parsing``, and secret-detection state) is updated after each
successful extraction.

Public API:
    run_parse(config, conn) -> ParseResult
"""

from __future__ import annotations

import dataclasses
import datetime
import json
import logging
import sqlite3
from pathlib import Path

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
from matlock.redaction import SecretScanResult, scan_document_for_secrets

log = logging.getLogger(__name__)

_POISON_CACHE: dict[str, str | None] = {}


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
# JSON helpers
# ---------------------------------------------------------------------------


def _json_default(obj: object) -> str:
    """Fallback serializer for types not handled by the stdlib JSON encoder.

    Converts ``datetime.date`` and ``datetime.datetime`` to ISO-8601 strings so
    that YAML front-matter values (which PyYAML natively parses as date objects)
    can be stored as JSON text in the database.
    """
    if isinstance(obj, (datetime.date, datetime.datetime)):
        return obj.isoformat()
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _build_extractor_config(config: MatlockConfig) -> dict:
    """Convert ``MatlockConfig`` into extractor configuration values."""
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
    """Map one parsed task into the ``task`` table column dictionary."""
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


def _scan_file_for_secrets(file_path: str, abs_path: Path) -> SecretScanResult:
    """Run secret scanning with a final fail-closed guardrail."""
    try:
        return scan_document_for_secrets(abs_path)
    except Exception as exc:
        log.warning("parse: secret scan failed for %s", file_path, exc_info=True)
        return SecretScanResult(
            has_secrets=True,
            secret_detection_error=str(exc),
            findings=(),
        )


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
    2. On success: delete stale tasks, insert new tasks, scan for secrets, update
       ``file`` metadata and secret fields, then set ``needs_parsing = 0``.
    3. On extraction exception: increment ``skipped`` and leave ``needs_parsing = 1``.

    A single ``conn.commit()`` covers all successful writes at the end.
    Does not call ``validate_config_paths()`` — that is the CLI's responsibility.
    """
    result = ParseResult()
    extractor_cfg = _build_extractor_config(config)
    files = get_files_needing_parsing(conn)
    log.info("parse: %d file(s) flagged for parsing", len(files))

    for file_row in files:
        file_path: str = file_row["file_path"]
        abs_path = config.base_directory / file_path
        current_sha = file_row["sha256"]

        if file_path in _POISON_CACHE:
            cached_sha = _POISON_CACHE[file_path]
            if cached_sha == current_sha:
                result.skipped += 1
                continue
            _POISON_CACHE.pop(file_path, None)

        # --- Extraction (no DB writes yet) ---
        try:
            content = abs_path.read_text(encoding="utf-8")
            parsed_file = extract_tasks_from_markdown(content, extractor_cfg, file_path)
        except Exception:
            _POISON_CACHE[file_path] = current_sha
            log.warning("parse: skipped %s (extraction error)", file_path, exc_info=True)
            result.skipped += 1
            continue

        _POISON_CACHE.pop(file_path, None)

        # --- DB writes (only reached on successful extraction) ---
        _, body = parse_front_matter(content)
        word_count = len(body.split())
        meta_json = json.dumps(parsed_file.meta_data, default=_json_default)
        scan_result = _scan_file_for_secrets(file_path, abs_path)

        old_tasks = get_tasks_for_file(conn, file_path)
        delete_tasks_for_file(conn, file_path)
        result.tasks_deleted += len(old_tasks)

        for task in parsed_file.tasks:
            upsert_task(conn, _task_to_dict(file_path, task))
            result.tasks_inserted += 1

        conn.execute(
            "UPDATE file SET needs_parsing = 0, word_count = ?, meta_data = ?,"
            " has_secrets = ?, secret_detection_error = ?"
            " WHERE file_path = ?",
            (
                word_count,
                meta_json,
                1 if scan_result.has_secrets else 0,
                scan_result.secret_detection_error,
                file_path,
            ),
        )
        log.debug("parse: parsed %s (%d tasks)", file_path, len(parsed_file.tasks))
        result.parsed += 1

    conn.commit()
    log.info(
        "parse complete: parsed=%d skipped=%d tasks_inserted=%d tasks_deleted=%d",
        result.parsed,
        result.skipped,
        result.tasks_inserted,
        result.tasks_deleted,
    )
    return result
