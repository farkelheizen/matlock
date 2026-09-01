"""Bulk backfill stage for persisted document secret-detection state."""

from __future__ import annotations

import dataclasses
import logging
import sqlite3
from pathlib import Path

from matlock.config import MatlockConfig
from matlock.db import get_files_needing_secret_detection, set_file_secret_detection
from matlock.redaction import SecretScanResult, scan_document_for_secrets

log = logging.getLogger(__name__)


@dataclasses.dataclass
class DetectBackfillResult:
    """Counts produced by one secret-detection backfill run."""

    scanned: int = 0
    skipped: int = 0
    retried: int = 0
    errors: int = 0


def _scan_file_for_secrets(file_path: str, abs_path: Path) -> SecretScanResult:
    """Return a fail-closed scanner result even if an adapter unexpectedly raises."""
    try:
        return scan_document_for_secrets(abs_path)
    except Exception as exc:
        log.warning("detect-backfill: secret scan failed for %s", file_path, exc_info=True)
        return SecretScanResult(
            has_secrets=True,
            secret_detection_error=str(exc),
        )


def run_detect_backfill(
    config: MatlockConfig,
    conn: sqlite3.Connection,
    *,
    retry_errors: bool = False,
) -> DetectBackfillResult:
    """Populate secret state for active tracked files without re-parsing them.

    Default mode scans only legacy unknown-state rows. When ``retry_errors`` is
    true, rows with a previous scanner error are included as well. Files that
    cannot be opened are skipped without changing their persisted state.
    """
    result = DetectBackfillResult()
    candidates = get_files_needing_secret_detection(conn, retry_errors=retry_errors)
    log.info("detect-backfill: %d candidate file(s)", len(candidates))

    for file_row in candidates:
        file_path: str = file_row["file_path"]
        abs_path = config.base_directory / file_path
        was_error = file_row["secret_detection_error"] is not None

        try:
            with abs_path.open("rb"):
                pass
        except OSError as exc:
            log.warning("detect-backfill: skipped unavailable file %s: %s", file_path, exc)
            result.skipped += 1
            continue

        scan_result = _scan_file_for_secrets(file_path, abs_path)
        set_file_secret_detection(
            conn,
            file_path,
            has_secrets=scan_result.has_secrets,
            secret_detection_error=scan_result.secret_detection_error,
        )
        result.scanned += 1
        if retry_errors and was_error:
            result.retried += 1
        if scan_result.secret_detection_error is not None:
            result.errors += 1

    conn.commit()
    log.info(
        "detect-backfill complete: scanned=%d skipped=%d retried=%d errors=%d",
        result.scanned,
        result.skipped,
        result.retried,
        result.errors,
    )
    return result
