from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .models import ScanItem


class ScannerDatabaseError(RuntimeError):
    """Raised when the index database is missing or not ready for scanning."""


def utc_now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


class ScanStore:
    """Reads unscanned content and inserts findings into content_attributes."""

    def __init__(self, path: Path, busy_timeout_ms: int) -> None:
        if not path.is_file():
            raise ScannerDatabaseError(
                f"Database not found: {path}. Run `matlock2 scan` to build the index first."
            )
        try:
            self.connection = sqlite3.connect(
                path, timeout=busy_timeout_ms / 1000, isolation_level=None
            )
            self.connection.row_factory = sqlite3.Row
            self.connection.execute(f"PRAGMA busy_timeout={int(busy_timeout_ms)}")
            self._check_schema()
        except sqlite3.Error as exc:
            raise ScannerDatabaseError(f"Cannot open database {path}: {exc}") from exc

    def _check_schema(self) -> None:
        tables = {
            row[0]
            for row in self.connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        missing = {"source_documents", "chunks", "content_attributes"} - tables
        columns = {row[1] for row in self.connection.execute("PRAGMA table_info(chunks)")}
        if missing or "content_sha256" not in columns:
            self.connection.close()
            raise ScannerDatabaseError(
                "The database has no content attribute support. "
                "Run `matlock2 scan` with the current version first."
            )

    def close(self) -> None:
        self.connection.close()

    def unscanned(self, domain: str, limit: int | None) -> list[ScanItem]:
        """Return content with no content_attributes row of any kind."""
        if domain == "chunk":
            table, hash_column, order = "chunks", "content_sha256", "id"
        else:
            table, hash_column, order = "source_documents", "body_sha256", "rowid"
        sql = (
            f"SELECT t.{hash_column} AS sha, MIN(t.{order}) AS first FROM {table} t "
            "LEFT JOIN content_attributes a ON a.domain=? AND a.sha256=t."
            f"{hash_column} WHERE a.sha256 IS NULL AND t.{hash_column} IS NOT NULL "
            f"GROUP BY t.{hash_column} ORDER BY first"
        )
        params: list[Any] = [domain]
        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)
        shas = [row["sha"] for row in self.connection.execute(sql, params)]
        return [self._load_item(domain, sha) for sha in shas]

    def _load_item(self, domain: str, sha: str) -> ScanItem:
        if domain == "chunk":
            rows = self.connection.execute(
                "SELECT content, source_path, ordinal FROM chunks "
                "WHERE content_sha256=? ORDER BY id",
                (sha,),
            ).fetchall()
            content = rows[0]["content"]
            paths = tuple(dict.fromkeys(row["source_path"] for row in rows))
            ordinals = tuple(row["ordinal"] for row in rows)
        else:
            rows = self.connection.execute(
                "SELECT body AS content, path AS source_path FROM source_documents "
                "WHERE body_sha256=? ORDER BY rowid",
                (sha,),
            ).fetchall()
            content = rows[0]["content"]
            paths = tuple(row["source_path"] for row in rows)
            ordinals = ()
        metadata: dict[str, dict[str, Any]] = {}
        for path in paths:
            row = self.connection.execute(
                "SELECT metadata_json FROM source_documents WHERE path=?", (path,)
            ).fetchone()
            if row is not None:
                metadata[path] = json.loads(row["metadata_json"])
        return ScanItem(domain, sha, content, paths, ordinals, metadata)

    def insert_results(self, rows: list[tuple[str, str, dict[str, Any]]], key: str) -> int:
        """Insert (domain, sha256, result) rows; existing rows are never modified."""
        inserted = 0
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            for domain, sha256, result in rows:
                cursor = self.connection.execute(
                    "INSERT INTO content_attributes(domain, sha256, attributes_json, updated_at) "
                    "VALUES (?, ?, ?, ?) ON CONFLICT(domain, sha256) DO NOTHING",
                    (
                        domain,
                        sha256,
                        json.dumps({key: result}, ensure_ascii=False, allow_nan=False),
                        utc_now(),
                    ),
                )
                inserted += cursor.rowcount
            self.connection.execute("COMMIT")
        except BaseException:
            self.connection.execute("ROLLBACK")
            raise
        return inserted

    def reset(
        self,
        key: str,
        *,
        domain: str | None = None,
        llm_statuses: tuple[str, ...] = (),
        min_risk: int | None = None,
        sha256: str | None = None,
        dry_run: bool = False,
    ) -> int:
        """Remove scanner results so the content is scanned again.

        Only rows that contain the scanner key are touched. A row holding nothing
        else is deleted, because any remaining row would block rescanning; other
        keys are preserved.
        """
        path = f"$.{key}"
        conditions = ["json_type(attributes_json, ?) IS NOT NULL"]
        params: list[Any] = [path]
        if domain is not None:
            conditions.append("domain = ?")
            params.append(domain)
        if sha256 is not None:
            conditions.append("sha256 = ?")
            params.append(sha256)
        if llm_statuses:
            marks = ",".join("?" for _ in llm_statuses)
            conditions.append(f"json_extract(attributes_json, ? || '.llm_status') IN ({marks})")
            params.extend([path, *llm_statuses])
        if min_risk is not None:
            conditions.append("json_extract(attributes_json, ? || '.risk_score') >= ?")
            params.extend([path, min_risk])
        where = " AND ".join(conditions)
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            count = self.connection.execute(
                f"SELECT COUNT(*) FROM content_attributes WHERE {where}", params
            ).fetchone()[0]
            if not dry_run and count:
                self.connection.execute(
                    f"DELETE FROM content_attributes WHERE {where} "
                    "AND json_remove(attributes_json, ?) = '{}'",
                    [*params, path],
                )
                self.connection.execute(
                    "UPDATE content_attributes SET attributes_json = "
                    f"json_remove(attributes_json, ?), updated_at = ? WHERE {where}",
                    [path, utc_now(), *params],
                )
            self.connection.execute("COMMIT")
        except BaseException:
            self.connection.execute("ROLLBACK")
            raise
        return count
