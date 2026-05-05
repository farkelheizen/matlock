"""Tests for matlock/stages/sync.py."""
from __future__ import annotations

import time
from pathlib import Path

import pytest

from matlock.config import MatlockConfig
from matlock.db import get_connection, get_file, get_tasks_for_file, init_db, upsert_file, upsert_task
from matlock.stages.sync import SyncResult, _hash_file, _walk_vault, run_sync


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_config(base_dir: Path, tmp_path: Path, ignore_dirs: list[str] | None = None) -> MatlockConfig:
    """Build a minimal MatlockConfig pointing at *base_dir*."""
    output_dir = base_dir / "_output"
    return MatlockConfig(
        base_directory=base_dir,
        db_path=tmp_path / "matlock.db",
        output_directory=output_dir,
        ignore_dirs=ignore_dirs or [],
    )


def _memory_conn(config: MatlockConfig):
    """Open an in-memory SQLite connection, initialise schema, and return it."""
    conn = get_connection(":memory:")
    init_db(conn)
    return conn


# ---------------------------------------------------------------------------
# _hash_file
# ---------------------------------------------------------------------------


class TestHashFile:
    def test_returns_64_char_hex(self, tmp_path: Path):
        f = tmp_path / "note.md"
        f.write_text("hello", encoding="utf-8")
        digest = _hash_file(f)
        assert len(digest) == 64
        assert all(c in "0123456789abcdef" for c in digest)

    def test_different_content_different_hash(self, tmp_path: Path):
        a = tmp_path / "a.md"
        b = tmp_path / "b.md"
        a.write_text("hello", encoding="utf-8")
        b.write_text("world", encoding="utf-8")
        assert _hash_file(a) != _hash_file(b)

    def test_same_content_same_hash(self, tmp_path: Path):
        a = tmp_path / "a.md"
        b = tmp_path / "b.md"
        a.write_text("same", encoding="utf-8")
        b.write_text("same", encoding="utf-8")
        assert _hash_file(a) == _hash_file(b)

    def test_lowercase_hex(self, tmp_path: Path):
        f = tmp_path / "x.md"
        f.write_bytes(bytes(range(256)))
        digest = _hash_file(f)
        assert digest == digest.lower()


# ---------------------------------------------------------------------------
# _walk_vault
# ---------------------------------------------------------------------------


class TestWalkVault:
    def test_finds_md_files(self, tmp_path: Path):
        (tmp_path / "a.md").write_text("a")
        (tmp_path / "b.md").write_text("b")
        (tmp_path / "c.txt").write_text("c")
        results = _walk_vault(tmp_path, tmp_path / "_out", [])
        names = {p.name for p in results}
        assert names == {"a.md", "b.md"}

    def test_finds_nested_md_files(self, tmp_path: Path):
        sub = tmp_path / "sub"
        sub.mkdir()
        (sub / "note.md").write_text("n")
        results = _walk_vault(tmp_path, tmp_path / "_out", [])
        assert any(p.name == "note.md" for p in results)

    def test_skips_output_directory(self, tmp_path: Path):
        out = tmp_path / "_output"
        out.mkdir()
        (out / "gen.md").write_text("gen")
        (tmp_path / "real.md").write_text("real")
        results = _walk_vault(tmp_path, out, [])
        names = {p.name for p in results}
        assert "gen.md" not in names
        assert "real.md" in names

    def test_skips_ignore_dirs(self, tmp_path: Path):
        hidden = tmp_path / ".obsidian"
        hidden.mkdir()
        (hidden / "config.md").write_text("cfg")
        (tmp_path / "real.md").write_text("real")
        results = _walk_vault(tmp_path, tmp_path / "_out", [".obsidian"])
        names = {p.name for p in results}
        assert "config.md" not in names
        assert "real.md" in names

    def test_case_insensitive_extension(self, tmp_path: Path):
        (tmp_path / "upper.MD").write_text("u")
        results = _walk_vault(tmp_path, tmp_path / "_out", [])
        assert any(p.name == "upper.MD" for p in results)

    def test_empty_vault(self, tmp_path: Path):
        results = _walk_vault(tmp_path, tmp_path / "_out", [])
        assert results == []

    def test_nonexistent_output_dir_is_fine(self, tmp_path: Path):
        """output_directory need not exist on disk."""
        (tmp_path / "a.md").write_text("a")
        results = _walk_vault(tmp_path, tmp_path / "nonexistent_out", [])
        assert len(results) == 1


# ---------------------------------------------------------------------------
# run_sync — insert path
# ---------------------------------------------------------------------------


class TestRunSyncInsert:
    def test_inserts_new_files(self, tmp_path: Path):
        vault = tmp_path / "vault"
        vault.mkdir()
        (vault / "a.md").write_text("hello")
        (vault / "b.md").write_text("world")
        config = _make_config(vault, tmp_path)
        conn = _memory_conn(config)

        result = run_sync(config, conn)

        assert result.inserted == 2
        assert result.updated == 0
        assert result.unchanged == 0
        assert result.deleted == 0

    def test_inserted_files_need_parsing(self, tmp_path: Path):
        vault = tmp_path / "vault"
        vault.mkdir()
        (vault / "note.md").write_text("content")
        config = _make_config(vault, tmp_path)
        conn = _memory_conn(config)

        run_sync(config, conn)

        row = get_file(conn, "note.md")
        assert row is not None
        assert row["needs_parsing"] == 1

    def test_relative_posix_path_stored(self, tmp_path: Path):
        vault = tmp_path / "vault"
        sub = vault / "sub"
        sub.mkdir(parents=True)
        (sub / "note.md").write_text("content")
        config = _make_config(vault, tmp_path)
        conn = _memory_conn(config)

        run_sync(config, conn)

        row = get_file(conn, "sub/note.md")
        assert row is not None

    def test_file_ext_stored_lowercase(self, tmp_path: Path):
        vault = tmp_path / "vault"
        vault.mkdir()
        (vault / "note.md").write_text("x")
        config = _make_config(vault, tmp_path)
        conn = _memory_conn(config)
        run_sync(config, conn)
        row = get_file(conn, "note.md")
        assert row["file_ext"] == ".md"

    def test_length_stored(self, tmp_path: Path):
        vault = tmp_path / "vault"
        vault.mkdir()
        content = "hello world"
        (vault / "note.md").write_text(content, encoding="utf-8")
        config = _make_config(vault, tmp_path)
        conn = _memory_conn(config)
        run_sync(config, conn)
        row = get_file(conn, "note.md")
        assert row["length"] == len(content.encode("utf-8"))

    def test_sha256_stored(self, tmp_path: Path):
        vault = tmp_path / "vault"
        vault.mkdir()
        f = vault / "note.md"
        f.write_text("content")
        config = _make_config(vault, tmp_path)
        conn = _memory_conn(config)
        run_sync(config, conn)
        row = get_file(conn, "note.md")
        assert row["sha256"] == _hash_file(f)


# ---------------------------------------------------------------------------
# run_sync — no-change path
# ---------------------------------------------------------------------------


class TestRunSyncUnchanged:
    def test_unchanged_on_second_run(self, tmp_path: Path):
        vault = tmp_path / "vault"
        vault.mkdir()
        (vault / "a.md").write_text("hello")
        config = _make_config(vault, tmp_path)
        conn = _memory_conn(config)

        run_sync(config, conn)
        result2 = run_sync(config, conn)

        assert result2.inserted == 0
        assert result2.updated == 0
        assert result2.unchanged == 1
        assert result2.deleted == 0

    def test_unchanged_does_not_reset_word_count(self, tmp_path: Path):
        """word_count set by parse stage must survive unchanged sync runs."""
        vault = tmp_path / "vault"
        vault.mkdir()
        (vault / "note.md").write_text("content")
        config = _make_config(vault, tmp_path)
        conn = _memory_conn(config)

        run_sync(config, conn)
        # Simulate parse stage setting word_count
        conn.execute("UPDATE file SET word_count = 42 WHERE file_path = 'note.md'")
        conn.commit()

        run_sync(config, conn)

        row = get_file(conn, "note.md")
        assert row["word_count"] == 42


# ---------------------------------------------------------------------------
# run_sync — update path
# ---------------------------------------------------------------------------


class TestRunSyncUpdate:
    def test_updates_changed_file(self, tmp_path: Path):
        vault = tmp_path / "vault"
        vault.mkdir()
        f = vault / "note.md"
        f.write_text("original")
        config = _make_config(vault, tmp_path)
        conn = _memory_conn(config)

        run_sync(config, conn)
        # Clear needs_parsing to verify it gets reset
        conn.execute("UPDATE file SET needs_parsing = 0")
        conn.commit()

        f.write_text("modified content")
        result2 = run_sync(config, conn)

        assert result2.updated == 1
        assert result2.inserted == 0
        row = get_file(conn, "note.md")
        assert row["needs_parsing"] == 1
        assert row["sha256"] == _hash_file(f)

    def test_preserves_word_count_on_update(self, tmp_path: Path):
        """word_count from the parse stage is preserved when a file is re-synced."""
        vault = tmp_path / "vault"
        vault.mkdir()
        f = vault / "note.md"
        f.write_text("original")
        config = _make_config(vault, tmp_path)
        conn = _memory_conn(config)

        run_sync(config, conn)
        conn.execute("UPDATE file SET word_count = 99 WHERE file_path = 'note.md'")
        conn.commit()

        f.write_text("modified")
        run_sync(config, conn)

        row = get_file(conn, "note.md")
        assert row["word_count"] == 99


# ---------------------------------------------------------------------------
# run_sync — force flag
# ---------------------------------------------------------------------------


class TestRunSyncForce:
    def test_force_marks_all_needs_parsing(self, tmp_path: Path):
        vault = tmp_path / "vault"
        vault.mkdir()
        (vault / "a.md").write_text("a")
        (vault / "b.md").write_text("b")
        config = _make_config(vault, tmp_path)
        conn = _memory_conn(config)

        run_sync(config, conn)
        conn.execute("UPDATE file SET needs_parsing = 0")
        conn.commit()

        result = run_sync(config, conn, force=True)

        assert result.updated == 2
        assert result.unchanged == 0
        rows = conn.execute("SELECT needs_parsing FROM file").fetchall()
        assert all(r["needs_parsing"] == 1 for r in rows)


# ---------------------------------------------------------------------------
# run_sync — soft-delete path
# ---------------------------------------------------------------------------


class TestRunSyncDelete:
    def test_soft_deletes_missing_file(self, tmp_path: Path):
        vault = tmp_path / "vault"
        vault.mkdir()
        f = vault / "note.md"
        f.write_text("content")
        config = _make_config(vault, tmp_path)
        conn = _memory_conn(config)

        run_sync(config, conn)
        f.unlink()
        result2 = run_sync(config, conn)

        assert result2.deleted == 1
        row = get_file(conn, "note.md")
        assert row["deleted"] == 1

    def test_deleted_file_tasks_are_removed(self, tmp_path: Path):
        """When a file disappears from disk, its tasks are deleted from the task table."""
        vault = tmp_path / "vault"
        vault.mkdir()
        f = vault / "note.md"
        f.write_text("- [ ] A task\n")
        config = _make_config(vault, tmp_path)
        conn = _memory_conn(config)

        run_sync(config, conn)
        # Manually seed a task row for that file (simulating a prior parse)
        upsert_task(conn, {
            "task_id": "task-1",
            "file_path": "note.md",
            "parent_task_id": None,
            "created_date": None,
            "due_date": None,
            "est_comp_date": None,
            "act_comp_date": None,
            "checked": 0,
            "task_text": "A task",
            "overflow": 0,
            "headers": "[]",
            "attributes": "{}",
            "errors": "[]",
            "twin_index": 0,
        })
        conn.commit()
        assert len(get_tasks_for_file(conn, "note.md")) == 1

        f.unlink()
        run_sync(config, conn)

        assert len(get_tasks_for_file(conn, "note.md")) == 0

    def test_deleted_file_not_re_deleted(self, tmp_path: Path):
        """Deleted files should not appear in subsequent delete counts."""
        vault = tmp_path / "vault"
        vault.mkdir()
        f = vault / "note.md"
        f.write_text("content")
        config = _make_config(vault, tmp_path)
        conn = _memory_conn(config)

        run_sync(config, conn)
        f.unlink()
        run_sync(config, conn)  # marks deleted
        result3 = run_sync(config, conn)  # should not re-count

        assert result3.deleted == 0


# ---------------------------------------------------------------------------
# run_sync — exclusion rules
# ---------------------------------------------------------------------------


class TestRunSyncExclusions:
    def test_skips_output_directory(self, tmp_path: Path):
        vault = tmp_path / "vault"
        out = vault / "_output"
        out.mkdir(parents=True)
        (out / "gen.md").write_text("generated")
        (vault / "real.md").write_text("real")
        config = _make_config(vault, tmp_path)
        conn = _memory_conn(config)

        result = run_sync(config, conn)

        assert result.inserted == 1
        assert get_file(conn, "real.md") is not None
        assert get_file(conn, "_output/gen.md") is None

    def test_skips_ignore_dirs(self, tmp_path: Path):
        vault = tmp_path / "vault"
        hidden = vault / ".obsidian"
        hidden.mkdir(parents=True)
        (hidden / "cfg.md").write_text("cfg")
        (vault / "real.md").write_text("real")
        config = _make_config(vault, tmp_path, ignore_dirs=[".obsidian"])
        conn = _memory_conn(config)

        result = run_sync(config, conn)

        assert result.inserted == 1
        assert get_file(conn, "real.md") is not None
        assert get_file(conn, ".obsidian/cfg.md") is None


# ---------------------------------------------------------------------------
# run_sync — SyncResult type
# ---------------------------------------------------------------------------


class TestSyncResult:
    def test_is_dataclass(self):
        import dataclasses
        assert dataclasses.is_dataclass(SyncResult)

    def test_default_values(self):
        r = SyncResult()
        assert r.inserted == 0
        assert r.updated == 0
        assert r.unchanged == 0
        assert r.deleted == 0
