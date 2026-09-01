from __future__ import annotations

from pathlib import Path

import yaml
from typer.testing import CliRunner

from matlock.cli import app
from matlock.db import get_connection, get_file, init_db, upsert_file
from matlock.redaction import SecretScanResult

runner = CliRunner()


def _write_config(path: Path, base_dir: Path, db_path: Path, cache_dir: Path) -> None:
    cfg = {
        "base_directory": str(base_dir),
        "db_path": str(db_path),
        "output_directory": str(base_dir / "_output"),
        "cache": {"redacted_dir": str(cache_dir)},
    }
    with path.open("w", encoding="utf-8") as fh:
        yaml.dump(cfg, fh)


def _seed_file_row(
    conn,
    file_path: str,
    *,
    has_secrets: int | None,
    deleted: int = 0,
) -> None:
    upsert_file(
        conn,
        {
            "file_path": file_path,
            "sha256": "sha-doc-read",
            "file_ext": ".md",
            "created": 1723200000,
            "modified": 1723286400,
            "modified_date": "2026-08-10T00:00:00Z",
            "deleted": deleted,
            "length": 0,
            "word_count": 0,
            "meta_data": "{}",
            "is_generated": 0,
            "needs_parsing": 0,
            "has_secrets": has_secrets,
            "secret_detection_error": None,
        },
    )


class TestDocReadCommand:
    def test_doc_read_help(self):
        result = runner.invoke(app, ["doc-read", "--help"])

        assert result.exit_code == 0
        assert "doc-read" in result.stdout

    def test_doc_read_clean_file_outputs_raw_content(self, tmp_path: Path):
        vault = tmp_path / "vault"
        vault.mkdir()
        rel = "Notes/clean.md"
        abs_path = vault / rel
        abs_path.parent.mkdir(parents=True, exist_ok=True)
        abs_path.write_text("plain text\n", encoding="utf-8")

        db_path = tmp_path / "matlock.db"
        cfg_path = tmp_path / "config.yaml"
        _write_config(cfg_path, vault, db_path, tmp_path / "cache")

        conn = get_connection(db_path)
        init_db(conn)
        _seed_file_row(conn, rel, has_secrets=0)
        conn.commit()
        conn.close()

        result = runner.invoke(app, ["--config", str(cfg_path), "doc-read", rel])

        assert result.exit_code == 0
        assert result.stdout == "plain text\n"

    def test_doc_read_secret_file_outputs_redacted_content(self, tmp_path: Path, monkeypatch):
        vault = tmp_path / "vault"
        vault.mkdir()
        rel = "Notes/secret.md"
        abs_path = vault / rel
        abs_path.parent.mkdir(parents=True, exist_ok=True)
        abs_path.write_text("token=abc123\n", encoding="utf-8")

        db_path = tmp_path / "matlock.db"
        cfg_path = tmp_path / "config.yaml"
        _write_config(cfg_path, vault, db_path, tmp_path / "cache")

        conn = get_connection(db_path)
        init_db(conn)
        _seed_file_row(conn, rel, has_secrets=1)
        conn.commit()
        conn.close()

        monkeypatch.setattr("matlock.cli.get_redacted_document", lambda _path, _cache_dir: "[REDACTED]\n")

        result = runner.invoke(app, ["--config", str(cfg_path), "doc-read", rel])

        assert result.exit_code == 0
        assert result.stdout == "[REDACTED]\n"

    def test_doc_read_legacy_file_scans_and_persists_state(self, tmp_path: Path, monkeypatch):
        vault = tmp_path / "vault"
        vault.mkdir()
        rel = "Notes/legacy.md"
        abs_path = vault / rel
        abs_path.parent.mkdir(parents=True, exist_ok=True)
        abs_path.write_text("token=abc123\n", encoding="utf-8")

        db_path = tmp_path / "matlock.db"
        cfg_path = tmp_path / "config.yaml"
        _write_config(cfg_path, vault, db_path, tmp_path / "cache")

        conn = get_connection(db_path)
        init_db(conn)
        _seed_file_row(conn, rel, has_secrets=None)
        conn.commit()
        conn.close()

        monkeypatch.setattr(
            "matlock.cli.scan_document_for_secrets",
            lambda _path: SecretScanResult(
                has_secrets=True,
                secret_detection_error=None,
                findings=(),
            ),
        )
        monkeypatch.setattr("matlock.cli.get_redacted_document", lambda _path, _cache_dir: "[REDACTED]\n")

        result = runner.invoke(app, ["--config", str(cfg_path), "doc-read", rel])

        assert result.exit_code == 0
        assert result.stdout == "[REDACTED]\n"

        conn = get_connection(db_path)
        row = get_file(conn, rel)
        conn.close()
        assert row is not None
        assert row["has_secrets"] == 1
        assert row["secret_detection_error"] is None

    def test_doc_read_accepts_vault_contained_absolute_path(self, tmp_path: Path):
        vault = tmp_path / "vault"
        vault.mkdir()
        rel = "Notes/absolute.md"
        abs_path = vault / rel
        abs_path.parent.mkdir(parents=True, exist_ok=True)
        abs_path.write_text("absolute ok\n", encoding="utf-8")

        db_path = tmp_path / "matlock.db"
        cfg_path = tmp_path / "config.yaml"
        _write_config(cfg_path, vault, db_path, tmp_path / "cache")

        conn = get_connection(db_path)
        init_db(conn)
        _seed_file_row(conn, rel, has_secrets=0)
        conn.commit()
        conn.close()

        result = runner.invoke(app, ["--config", str(cfg_path), "doc-read", str(abs_path)])

        assert result.exit_code == 0
        assert result.stdout == "absolute ok\n"

    def test_doc_read_rejects_paths_outside_base_directory(self, tmp_path: Path):
        vault = tmp_path / "vault"
        vault.mkdir()
        outside_file = tmp_path / "outside.md"
        outside_file.write_text("nope\n", encoding="utf-8")

        db_path = tmp_path / "matlock.db"
        cfg_path = tmp_path / "config.yaml"
        _write_config(cfg_path, vault, db_path, tmp_path / "cache")

        result = runner.invoke(app, ["--config", str(cfg_path), "doc-read", str(outside_file)])

        assert result.exit_code == 1
        assert "path must be inside base_directory" in result.stderr

    def test_doc_read_rejects_untracked_or_deleted_paths(self, tmp_path: Path):
        vault = tmp_path / "vault"
        vault.mkdir()

        tracked_deleted_rel = "Notes/deleted.md"
        tracked_deleted_abs = vault / tracked_deleted_rel
        tracked_deleted_abs.parent.mkdir(parents=True, exist_ok=True)
        tracked_deleted_abs.write_text("old content\n", encoding="utf-8")

        untracked_rel = "Notes/untracked.md"
        (vault / untracked_rel).write_text("new\n", encoding="utf-8")

        db_path = tmp_path / "matlock.db"
        cfg_path = tmp_path / "config.yaml"
        _write_config(cfg_path, vault, db_path, tmp_path / "cache")

        conn = get_connection(db_path)
        init_db(conn)
        _seed_file_row(conn, tracked_deleted_rel, has_secrets=0, deleted=1)
        conn.commit()
        conn.close()

        untracked_result = runner.invoke(app, ["--config", str(cfg_path), "doc-read", untracked_rel])
        deleted_result = runner.invoke(
            app,
            ["--config", str(cfg_path), "doc-read", tracked_deleted_rel],
        )

        assert untracked_result.exit_code == 1
        assert "file is not tracked in the database" in untracked_result.stderr

        assert deleted_result.exit_code == 1
        assert "file is marked deleted" in deleted_result.stderr
