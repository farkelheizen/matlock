from __future__ import annotations

from pathlib import Path

from matlock.config import MatlockConfig
from matlock.db import get_connection, get_file, get_tasks_for_file, init_db, upsert_file, upsert_task
from matlock.redaction import SecretScanResult
from matlock.stages.detect_backfill import run_detect_backfill


def _config(tmp_path: Path) -> MatlockConfig:
    vault = tmp_path / "vault"
    vault.mkdir()
    return MatlockConfig(
        base_directory=vault,
        db_path=tmp_path / "matlock.db",
        output_directory=vault / "_output",
    )


def _seed_file(
    conn,
    config: MatlockConfig,
    file_path: str,
    *,
    has_secrets: int | None,
    secret_detection_error: str | None = None,
    deleted: int = 0,
    is_generated: int = 0,
    create_on_disk: bool = True,
) -> None:
    if create_on_disk:
        abs_path = config.base_directory / file_path
        abs_path.parent.mkdir(parents=True, exist_ok=True)
        abs_path.write_text("# Note\n", encoding="utf-8")
    upsert_file(
        conn,
        {
            "file_path": file_path,
            "sha256": "sha-" + file_path,
            "file_ext": ".md",
            "created": 1723200000,
            "modified": 1723286400,
            "modified_date": "2026-08-31",
            "deleted": deleted,
            "length": 7,
            "word_count": 1,
            "meta_data": "{}",
            "is_generated": is_generated,
            "needs_parsing": 0,
            "has_secrets": has_secrets,
            "secret_detection_error": secret_detection_error,
        },
    )


def test_backfill_scans_unknown_rows_and_preserves_other_pipeline_state(tmp_path: Path, monkeypatch):
    config = _config(tmp_path)
    conn = get_connection(":memory:")
    init_db(conn)
    _seed_file(conn, config, "Notes/clean.md", has_secrets=None)
    _seed_file(conn, config, "Notes/secret.md", has_secrets=None)
    _seed_file(conn, config, "Notes/known.md", has_secrets=0)
    upsert_task(
        conn,
        {
            "task_id": "task-1",
            "file_path": "Notes/clean.md",
            "parent_task_id": None,
            "created_date": None,
            "due_date": None,
            "est_comp_date": None,
            "act_comp_date": None,
            "checked": 0,
            "task_text": "unchanged",
            "overflow": 0,
            "headers": "[]",
            "attributes": "{}",
            "errors": "[]",
            "twin_index": 0,
        },
    )
    conn.commit()

    def scan(path: Path) -> SecretScanResult:
        return SecretScanResult(
            has_secrets=path.name == "secret.md",
            secret_detection_error=None,
        )

    monkeypatch.setattr("matlock.stages.detect_backfill.scan_document_for_secrets", scan)

    result = run_detect_backfill(config, conn)

    assert result.scanned == 2
    assert result.skipped == 0
    assert result.retried == 0
    assert result.errors == 0
    assert get_file(conn, "Notes/clean.md")["has_secrets"] == 0
    assert get_file(conn, "Notes/secret.md")["has_secrets"] == 1
    assert get_file(conn, "Notes/known.md")["has_secrets"] == 0
    assert get_file(conn, "Notes/clean.md")["needs_parsing"] == 0
    assert len(get_tasks_for_file(conn, "Notes/clean.md")) == 1


def test_backfill_persists_fail_closed_scanner_result(tmp_path: Path, monkeypatch):
    config = _config(tmp_path)
    conn = get_connection(":memory:")
    init_db(conn)
    _seed_file(conn, config, "Notes/unknown.md", has_secrets=None)
    conn.commit()

    monkeypatch.setattr(
        "matlock.stages.detect_backfill.scan_document_for_secrets",
        lambda _path: SecretScanResult(
            has_secrets=True,
            secret_detection_error="scanner unavailable",
        ),
    )

    result = run_detect_backfill(config, conn)

    row = get_file(conn, "Notes/unknown.md")
    assert result.scanned == 1
    assert result.errors == 1
    assert row["has_secrets"] == 1
    assert row["secret_detection_error"] == "scanner unavailable"


def test_backfill_skips_missing_documents_without_changing_state(tmp_path: Path, monkeypatch):
    config = _config(tmp_path)
    conn = get_connection(":memory:")
    init_db(conn)
    _seed_file(
        conn,
        config,
        "Notes/missing.md",
        has_secrets=None,
        create_on_disk=False,
    )
    conn.commit()
    monkeypatch.setattr(
        "matlock.stages.detect_backfill.scan_document_for_secrets",
        lambda _path: (_ for _ in ()).throw(AssertionError("must not scan missing file")),
    )

    result = run_detect_backfill(config, conn)

    row = get_file(conn, "Notes/missing.md")
    assert result.scanned == 0
    assert result.skipped == 1
    assert row["has_secrets"] is None
    assert row["secret_detection_error"] is None


def test_backfill_retry_errors_rescans_only_error_rows(tmp_path: Path, monkeypatch):
    config = _config(tmp_path)
    conn = get_connection(":memory:")
    init_db(conn)
    _seed_file(conn, config, "Notes/unknown.md", has_secrets=None)
    _seed_file(
        conn,
        config,
        "Notes/error.md",
        has_secrets=1,
        secret_detection_error="old error",
    )
    _seed_file(conn, config, "Notes/known.md", has_secrets=1)
    _seed_file(conn, config, "Notes/deleted.md", has_secrets=None, deleted=1)
    _seed_file(conn, config, "Notes/generated.md", has_secrets=None, is_generated=1)
    conn.commit()

    scanned_paths: list[str] = []

    def scan(path: Path) -> SecretScanResult:
        scanned_paths.append(path.as_posix())
        return SecretScanResult(has_secrets=False, secret_detection_error=None)

    monkeypatch.setattr("matlock.stages.detect_backfill.scan_document_for_secrets", scan)

    result = run_detect_backfill(config, conn, retry_errors=True)

    assert result.scanned == 2
    assert result.retried == 1
    assert result.errors == 0
    assert {Path(path).name for path in scanned_paths} == {"unknown.md", "error.md"}
    assert get_file(conn, "Notes/error.md")["has_secrets"] == 0
    assert get_file(conn, "Notes/error.md")["secret_detection_error"] is None
    assert get_file(conn, "Notes/deleted.md")["has_secrets"] is None
    assert get_file(conn, "Notes/generated.md")["has_secrets"] is None
