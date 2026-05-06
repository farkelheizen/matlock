"""Tests for matlock/stages/parse.py."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from matlock.config import MatlockConfig
from matlock.db import (
    get_connection,
    get_file,
    get_tasks_for_file,
    init_db,
    upsert_file,
)
from matlock.stages import parse as parse_stage
from matlock.stages.parse import ParseResult, _build_extractor_config, run_parse
from matlock.stages.sync import run_sync


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

_MD_WITH_TASKS = """\
---
status: active
---
# Work

- [ ] Buy milk {due_date: 2026-05-01}
- [x] Send email
"""

_MD_NO_TASKS = """\
# Notes

Just some prose with no checkboxes.
"""

_MD_NESTED = """\
# Project

## Phase 1

- [ ] Task A
  - [ ] Sub-task A1
- [x] Task B
"""


@pytest.fixture(autouse=True)
def _clear_poison_cache() -> None:
    """Ensure parse poison-cache state does not leak across tests."""
    parse_stage._POISON_FILES.clear()


def _make_config(
    base_dir: Path,
    tmp_path: Path,
    task_attributes: dict | None = None,
) -> MatlockConfig:
    return MatlockConfig(
        base_directory=base_dir,
        db_path=tmp_path / "matlock.db",
        output_directory=base_dir / "_output",
        task_attributes=task_attributes or {},
    )


def _conn():
    conn = get_connection(":memory:")
    init_db(conn)
    return conn


def _seed_file(conn, file_path: str, content: str, base_dir: Path) -> None:
    """Write a file to disk and insert a needs_parsing=1 row into the DB."""
    abs_path = base_dir / file_path
    abs_path.parent.mkdir(parents=True, exist_ok=True)
    abs_path.write_text(content, encoding="utf-8")
    upsert_file(conn, {
        "file_path": file_path,
        "sha256": "abc",
        "file_ext": ".md",
        "created": 0,
        "modified": 0,
        "modified_date": "2026-01-01",
        "deleted": 0,
        "length": len(content.encode()),
        "word_count": None,
        "meta_data": None,
        "is_generated": 0,
        "needs_parsing": 1,
    })


# ---------------------------------------------------------------------------
# _build_extractor_config
# ---------------------------------------------------------------------------


class TestBuildExtractorConfig:
    def test_returns_dict_with_headers_and_tasks(self):
        config = MatlockConfig(
            base_directory=Path("/tmp"),
            db_path=Path("/tmp/m.db"),
            output_directory=Path("/tmp/_out"),
        )
        cfg = _build_extractor_config(config)
        assert "headers" in cfg
        assert "tasks" in cfg
        assert "task_text_maxlen" in cfg["tasks"]
        assert "attributes" in cfg["tasks"]

    def test_default_limits(self):
        config = MatlockConfig(
            base_directory=Path("/tmp"),
            db_path=Path("/tmp/m.db"),
            output_directory=Path("/tmp/_out"),
        )
        cfg = _build_extractor_config(config)
        assert cfg["headers"]["header_text_maxlen"] == 200
        assert cfg["tasks"]["task_text_maxlen"] == 500

    def test_task_attributes_included(self):
        from matlock.config import TaskAttributeConfig
        config = MatlockConfig(
            base_directory=Path("/tmp"),
            db_path=Path("/tmp/m.db"),
            output_directory=Path("/tmp/_out"),
            task_attributes={
                "due_date": TaskAttributeConfig(type="date", alias="📅"),
            },
        )
        cfg = _build_extractor_config(config)
        attrs = cfg["tasks"]["attributes"]
        assert "due_date" in attrs
        assert attrs["due_date"]["type"] == "date"
        assert attrs["due_date"]["alias"] == "📅"

    def test_domain_attribute_values_included(self):
        from matlock.config import DomainValueConfig, TaskAttributeConfig
        config = MatlockConfig(
            base_directory=Path("/tmp"),
            db_path=Path("/tmp/m.db"),
            output_directory=Path("/tmp/_out"),
            task_attributes={
                "priority": TaskAttributeConfig(
                    type="domain",
                    values={
                        "high": DomainValueConfig(alias="⏫"),
                        "low": DomainValueConfig(),
                    },
                ),
            },
        )
        cfg = _build_extractor_config(config)
        attrs = cfg["tasks"]["attributes"]
        assert "priority" in attrs
        assert "values" in attrs["priority"]
        assert attrs["priority"]["values"]["high"] == {"alias": "⏫"}
        assert attrs["priority"]["values"]["low"] == {}


# ---------------------------------------------------------------------------
# run_parse — basic insert path
# ---------------------------------------------------------------------------


class TestRunParseInsert:
    def test_parses_flagged_file(self, tmp_path: Path):
        vault = tmp_path / "vault"
        vault.mkdir()
        config = _make_config(vault, tmp_path)
        conn = _conn()
        _seed_file(conn, "note.md", _MD_WITH_TASKS, vault)

        result = run_parse(config, conn)

        assert result.parsed == 1
        assert result.skipped == 0

    def test_tasks_inserted(self, tmp_path: Path):
        vault = tmp_path / "vault"
        vault.mkdir()
        config = _make_config(vault, tmp_path)
        conn = _conn()
        _seed_file(conn, "note.md", _MD_WITH_TASKS, vault)

        result = run_parse(config, conn)

        tasks = get_tasks_for_file(conn, "note.md")
        assert len(tasks) == 2
        assert result.tasks_inserted == 2

    def test_nested_tasks_inserted(self, tmp_path: Path):
        vault = tmp_path / "vault"
        vault.mkdir()
        config = _make_config(vault, tmp_path)
        conn = _conn()
        _seed_file(conn, "note.md", _MD_NESTED, vault)

        result = run_parse(config, conn)

        tasks = get_tasks_for_file(conn, "note.md")
        assert len(tasks) == 3  # Task A, Sub-task A1, Task B
        assert result.tasks_inserted == 3

    def test_needs_parsing_cleared(self, tmp_path: Path):
        vault = tmp_path / "vault"
        vault.mkdir()
        config = _make_config(vault, tmp_path)
        conn = _conn()
        _seed_file(conn, "note.md", _MD_WITH_TASKS, vault)

        run_parse(config, conn)

        row = get_file(conn, "note.md")
        assert row["needs_parsing"] == 0

    def test_word_count_set(self, tmp_path: Path):
        vault = tmp_path / "vault"
        vault.mkdir()
        config = _make_config(vault, tmp_path)
        conn = _conn()
        _seed_file(conn, "note.md", _MD_WITH_TASKS, vault)

        run_parse(config, conn)

        row = get_file(conn, "note.md")
        # word_count is derived from post-frontmatter body only
        assert row["word_count"] is not None
        assert row["word_count"] > 0

    def test_word_count_excludes_frontmatter(self, tmp_path: Path):
        """word_count should not include YAML front-matter tokens."""
        vault = tmp_path / "vault"
        vault.mkdir()
        config = _make_config(vault, tmp_path)
        conn = _conn()

        # A file with a large frontmatter block but minimal body
        content = "---\n" + "\n".join(f"key{i}: value{i}" for i in range(50)) + "\n---\nhello world\n"
        _seed_file(conn, "note.md", content, vault)

        run_parse(config, conn)

        row = get_file(conn, "note.md")
        # Body is "hello world\n" = 2 tokens. Not 100+ from frontmatter.
        assert row["word_count"] == 2

    def test_meta_data_stored_as_json(self, tmp_path: Path):
        vault = tmp_path / "vault"
        vault.mkdir()
        config = _make_config(vault, tmp_path)
        conn = _conn()
        _seed_file(conn, "note.md", _MD_WITH_TASKS, vault)

        run_parse(config, conn)

        row = get_file(conn, "note.md")
        assert row["meta_data"] is not None
        meta = json.loads(row["meta_data"])
        assert isinstance(meta, dict)
        assert meta.get("status") == "active"

    def test_file_with_no_tasks(self, tmp_path: Path):
        vault = tmp_path / "vault"
        vault.mkdir()
        config = _make_config(vault, tmp_path)
        conn = _conn()
        _seed_file(conn, "prose.md", _MD_NO_TASKS, vault)

        result = run_parse(config, conn)

        assert result.parsed == 1
        assert result.tasks_inserted == 0
        tasks = get_tasks_for_file(conn, "prose.md")
        assert tasks == []


# ---------------------------------------------------------------------------
# run_parse — no-op path
# ---------------------------------------------------------------------------


class TestRunParseNoOp:
    def test_no_files_flagged(self, tmp_path: Path):
        vault = tmp_path / "vault"
        vault.mkdir()
        config = _make_config(vault, tmp_path)
        conn = _conn()
        # Seed a file with needs_parsing=0
        _seed_file(conn, "note.md", _MD_WITH_TASKS, vault)
        conn.execute("UPDATE file SET needs_parsing = 0")
        conn.commit()

        result = run_parse(config, conn)

        assert result.parsed == 0
        assert result.skipped == 0
        assert result.tasks_inserted == 0

    def test_second_run_is_noop(self, tmp_path: Path):
        vault = tmp_path / "vault"
        vault.mkdir()
        config = _make_config(vault, tmp_path)
        conn = _conn()
        _seed_file(conn, "note.md", _MD_WITH_TASKS, vault)

        run_parse(config, conn)
        result2 = run_parse(config, conn)

        assert result2.parsed == 0
        assert result2.tasks_inserted == 0


# ---------------------------------------------------------------------------
# run_parse — re-parse / update path
# ---------------------------------------------------------------------------


class TestRunParseReparse:
    def test_stale_tasks_deleted_on_reparse(self, tmp_path: Path):
        vault = tmp_path / "vault"
        vault.mkdir()
        f = vault / "note.md"
        config = _make_config(vault, tmp_path)
        conn = _conn()
        _seed_file(conn, "note.md", _MD_WITH_TASKS, vault)
        run_parse(config, conn)  # inserts 2 tasks

        # Modify file — now has 1 task
        f.write_text("# Work\n\n- [ ] Only task\n", encoding="utf-8")
        conn.execute("UPDATE file SET needs_parsing = 1 WHERE file_path = 'note.md'")
        conn.commit()

        result2 = run_parse(config, conn)

        assert result2.tasks_deleted == 2
        assert result2.tasks_inserted == 1
        tasks = get_tasks_for_file(conn, "note.md")
        assert len(tasks) == 1

    def test_tasks_deleted_count_correct(self, tmp_path: Path):
        vault = tmp_path / "vault"
        vault.mkdir()
        config = _make_config(vault, tmp_path)
        conn = _conn()
        _seed_file(conn, "note.md", _MD_NESTED, vault)  # 3 tasks
        run_parse(config, conn)

        # Trigger re-parse
        conn.execute("UPDATE file SET needs_parsing = 1 WHERE file_path = 'note.md'")
        conn.commit()

        result2 = run_parse(config, conn)

        assert result2.tasks_deleted == 3


# ---------------------------------------------------------------------------
# run_parse — error isolation
# ---------------------------------------------------------------------------


class TestRunParseErrorIsolation:
    def test_missing_file_skipped(self, tmp_path: Path):
        vault = tmp_path / "vault"
        vault.mkdir()
        config = _make_config(vault, tmp_path)
        conn = _conn()
        # Insert a DB row but don't create the file on disk
        upsert_file(conn, {
            "file_path": "ghost.md",
            "sha256": "abc",
            "file_ext": ".md",
            "created": 0,
            "modified": 0,
            "modified_date": "2026-01-01",
            "deleted": 0,
            "length": 0,
            "word_count": None,
            "meta_data": None,
            "is_generated": 0,
            "needs_parsing": 1,
        })

        result = run_parse(config, conn)

        assert result.skipped == 1
        assert result.parsed == 0
        row = get_file(conn, "ghost.md")
        assert row["needs_parsing"] == 1

    def test_good_file_parsed_despite_bad_neighbour(self, tmp_path: Path):
        vault = tmp_path / "vault"
        vault.mkdir()
        config = _make_config(vault, tmp_path)
        conn = _conn()

        # bad: file doesn't exist on disk
        upsert_file(conn, {
            "file_path": "ghost.md",
            "sha256": "abc",
            "file_ext": ".md",
            "created": 0,
            "modified": 0,
            "modified_date": "2026-01-01",
            "deleted": 0,
            "length": 0,
            "word_count": None,
            "meta_data": None,
            "is_generated": 0,
            "needs_parsing": 1,
        })
        # good
        _seed_file(conn, "good.md", _MD_WITH_TASKS, vault)

        result = run_parse(config, conn)

        assert result.parsed == 1
        assert result.skipped == 1
        assert result.tasks_inserted == 2
        assert get_file(conn, "good.md")["needs_parsing"] == 0
        assert get_file(conn, "ghost.md")["needs_parsing"] == 1

    def test_skipped_file_has_no_task_changes(self, tmp_path: Path):
        vault = tmp_path / "vault"
        vault.mkdir()
        config = _make_config(vault, tmp_path)
        conn = _conn()
        # Insert ghost with no disk file
        upsert_file(conn, {
            "file_path": "ghost.md",
            "sha256": "x",
            "file_ext": ".md",
            "created": 0,
            "modified": 0,
            "modified_date": "2026-01-01",
            "deleted": 0,
            "length": 0,
            "word_count": None,
            "meta_data": None,
            "is_generated": 0,
            "needs_parsing": 1,
        })

        run_parse(config, conn)

        tasks = get_tasks_for_file(conn, "ghost.md")
        assert tasks == []

    def test_poison_file_skips_repeated_unchanged_retries(self, tmp_path: Path, caplog):
        vault = tmp_path / "vault"
        vault.mkdir()
        config = _make_config(vault, tmp_path)
        conn = _conn()

        upsert_file(conn, {
            "file_path": "ghost.md",
            "sha256": "poison-sha-1",
            "file_ext": ".md",
            "created": 0,
            "modified": 0,
            "modified_date": "2026-01-01",
            "deleted": 0,
            "length": 0,
            "word_count": None,
            "meta_data": None,
            "is_generated": 0,
            "needs_parsing": 1,
        })

        with caplog.at_level("WARNING"):
            first = run_parse(config, conn)
            second = run_parse(config, conn)

        assert first.skipped == 1
        assert second.skipped == 1
        warning_records = [
            r for r in caplog.records if "parse: skipped ghost.md (extraction error)" in r.message
        ]
        assert len(warning_records) == 1

    def test_poison_file_retries_when_sha_changes(self, tmp_path: Path, caplog):
        vault = tmp_path / "vault"
        vault.mkdir()
        config = _make_config(vault, tmp_path)
        conn = _conn()

        upsert_file(conn, {
            "file_path": "ghost.md",
            "sha256": "poison-sha-1",
            "file_ext": ".md",
            "created": 0,
            "modified": 0,
            "modified_date": "2026-01-01",
            "deleted": 0,
            "length": 0,
            "word_count": None,
            "meta_data": None,
            "is_generated": 0,
            "needs_parsing": 1,
        })

        with caplog.at_level("WARNING"):
            first = run_parse(config, conn)
            conn.execute(
                "UPDATE file SET sha256 = ? WHERE file_path = ?",
                ("poison-sha-2", "ghost.md"),
            )
            conn.commit()
            second = run_parse(config, conn)

        assert first.skipped == 1
        assert second.skipped == 1
        warning_records = [
            r for r in caplog.records if "parse: skipped ghost.md (extraction error)" in r.message
        ]
        assert len(warning_records) == 2


# ---------------------------------------------------------------------------
# run_parse — task column values
# ---------------------------------------------------------------------------


class TestRunParseTaskColumns:
    def test_checked_stored_as_int(self, tmp_path: Path):
        vault = tmp_path / "vault"
        vault.mkdir()
        config = _make_config(vault, tmp_path)
        conn = _conn()
        _seed_file(conn, "note.md", "# H\n\n- [x] Done\n- [ ] Todo\n", vault)
        run_parse(config, conn)
        tasks = get_tasks_for_file(conn, "note.md")
        checked_values = {row["task_text"]: row["checked"] for row in tasks}
        assert checked_values["Done"] == 1
        assert checked_values["Todo"] == 0

    def test_headers_stored_as_json_list(self, tmp_path: Path):
        vault = tmp_path / "vault"
        vault.mkdir()
        config = _make_config(vault, tmp_path)
        conn = _conn()
        _seed_file(conn, "note.md", "# Section\n\n- [ ] A task\n", vault)
        run_parse(config, conn)
        tasks = get_tasks_for_file(conn, "note.md")
        headers = json.loads(tasks[0]["headers"])
        assert isinstance(headers, list)

    def test_errors_stored_as_json_list(self, tmp_path: Path):
        vault = tmp_path / "vault"
        vault.mkdir()
        config = _make_config(vault, tmp_path)
        conn = _conn()
        _seed_file(conn, "note.md", "# H\n\n- [ ] Task\n", vault)
        run_parse(config, conn)
        tasks = get_tasks_for_file(conn, "note.md")
        errors = json.loads(tasks[0]["errors"])
        assert isinstance(errors, list)

    def test_attributes_stored_as_json_dict(self, tmp_path: Path):
        vault = tmp_path / "vault"
        vault.mkdir()
        config = _make_config(vault, tmp_path)
        conn = _conn()
        _seed_file(conn, "note.md", "# H\n\n- [ ] Task\n", vault)
        run_parse(config, conn)
        tasks = get_tasks_for_file(conn, "note.md")
        attrs = json.loads(tasks[0]["attributes"])
        assert isinstance(attrs, dict)

    def test_due_date_populated_from_attribute(self, tmp_path: Path):
        from matlock.config import TaskAttributeConfig
        vault = tmp_path / "vault"
        vault.mkdir()
        config = _make_config(vault, tmp_path, task_attributes={
            "due_date": TaskAttributeConfig(type="date"),
        })
        conn = _conn()
        _seed_file(conn, "note.md", "# H\n\n- [ ] Task {due_date: 2026-06-01}\n", vault)
        run_parse(config, conn)
        tasks = get_tasks_for_file(conn, "note.md")
        assert tasks[0]["due_date"] == "2026-06-01"


# ---------------------------------------------------------------------------
# ParseResult type
# ---------------------------------------------------------------------------


class TestParseResult:
    def test_is_dataclass(self):
        import dataclasses
        assert dataclasses.is_dataclass(ParseResult)

    def test_default_values(self):
        r = ParseResult()
        assert r.parsed == 0
        assert r.skipped == 0
        assert r.tasks_inserted == 0
        assert r.tasks_deleted == 0


# ---------------------------------------------------------------------------
# run_parse via run_sync integration
# ---------------------------------------------------------------------------


class TestSyncThenParse:
    def test_sync_then_parse_produces_tasks(self, tmp_path: Path):
        vault = tmp_path / "vault"
        vault.mkdir()
        (vault / "tasks.md").write_text(_MD_WITH_TASKS, encoding="utf-8")
        config = _make_config(vault, tmp_path)
        conn = get_connection(":memory:")
        init_db(conn)

        run_sync(config, conn)
        result = run_parse(config, conn)

        assert result.parsed == 1
        tasks = get_tasks_for_file(conn, "tasks.md")
        assert len(tasks) == 2

    def test_sync_detects_task_removal_and_parse_cleans_up(self, tmp_path: Path):
        """Full cycle: sync+parse, remove a task, sync+parse again — stale task gone."""
        vault = tmp_path / "vault"
        vault.mkdir()
        md_file = vault / "tasks.md"
        md_file.write_text(_MD_WITH_TASKS, encoding="utf-8")  # 2 tasks
        config = _make_config(vault, tmp_path)
        conn = get_connection(":memory:")
        init_db(conn)

        # First pass
        run_sync(config, conn)
        run_parse(config, conn)
        assert len(get_tasks_for_file(conn, "tasks.md")) == 2

        # User removes one task from the file
        md_file.write_text("# Work\n\n- [ ] Buy milk\n", encoding="utf-8")

        # Second pass
        run_sync(config, conn)   # detects hash change → needs_parsing=1
        result = run_parse(config, conn)

        assert result.tasks_deleted == 2   # both old tasks deleted
        assert result.tasks_inserted == 1  # only the remaining task re-inserted
        tasks = get_tasks_for_file(conn, "tasks.md")
        assert len(tasks) == 1
        assert tasks[0]["task_text"] == "Buy milk"
