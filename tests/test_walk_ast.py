from __future__ import annotations

import json
from pathlib import Path

import pytest
from marko import Markdown
from marko.ext.gfm import GFM

from matlock.extractor import walk_ast


@pytest.fixture
def config() -> dict:
    config_path = Path(__file__).parent.parent / "config" / "default_config.json"
    with config_path.open(encoding="utf-8") as f:
        return json.load(f)


def parse_and_walk(md_text: str, config: dict) -> list:
    md = Markdown(extensions=[GFM])
    root = md.parse(md_text)
    tasks = []
    walk_ast(root, [""] * 6, None, config, tasks, {})
    return tasks


def test_single_flat_task(config):
    tasks = parse_and_walk("- [ ] A simple task\n", config)
    assert len(tasks) == 1
    t = tasks[0]
    assert t.checked is False
    assert t.task_text == "A simple task"
    assert t.headers == []
    assert t.parent_task_id is None


def test_checked_task(config):
    tasks = parse_and_walk("- [x] Done task\n", config)
    assert len(tasks) == 1
    assert tasks[0].checked is True


def test_nested_tasks(config):
    md = "- [ ] Parent\n  - [ ] Child\n"
    tasks = parse_and_walk(md, config)
    assert len(tasks) == 2
    parent = next(t for t in tasks if t.task_text == "Parent")
    child = next(t for t in tasks if t.task_text == "Child")
    assert parent.parent_task_id is None
    assert child.parent_task_id == parent.task_id


def test_tasks_under_headers(config):
    md = "# H1\n\n- [ ] Task under H1\n\n## H2\n\n- [ ] Task under H2\n"
    tasks = parse_and_walk(md, config)
    assert len(tasks) == 2
    t1 = next(t for t in tasks if "H1" in t.task_text or t.headers == ["H1"])
    t2 = next(t for t in tasks if "H2" in t.headers)
    assert t1.headers == ["H1"]
    assert t2.headers == ["H1", "H2"]


def test_header_reset(config):
    md = "## H2\n\n### H3\n\n## New H2\n\n- [ ] Task\n"
    tasks = parse_and_walk(md, config)
    assert len(tasks) == 1
    # H3 should be cleared when New H2 is encountered
    assert tasks[0].headers == ["New H2"]
    assert "H3" not in tasks[0].headers


def test_non_task_list_items_skipped(config):
    tasks = parse_and_walk("- plain item\n- [ ] real task\n", config)
    assert len(tasks) == 1
    assert tasks[0].task_text == "real task"


def test_multiline_continuation(config):
    md = "- [ ] Find classes\n  - [ ] Exclude fields\n        Is this still cool?\n"
    tasks = parse_and_walk(md, config)
    subtask = next(t for t in tasks if "Exclude" in t.task_text)
    assert "Is this still cool?" in subtask.task_text


def test_twin_tracking(config):
    md = "- [ ] Same task\n- [ ] Same task\n"
    tasks = parse_and_walk(md, config)
    assert len(tasks) == 2
    indices = sorted(t.twin_index for t in tasks)
    assert indices == [0, 1]
    # Twin tasks must have different task_ids
    assert tasks[0].task_id != tasks[1].task_id


def test_deep_nesting(config):
    md = "- [ ] Level 1\n  - [ ] Level 2\n    - [x] Level 3\n"
    tasks = parse_and_walk(md, config)
    assert len(tasks) == 3
    l1 = next(t for t in tasks if "Level 1" in t.task_text)
    l2 = next(t for t in tasks if "Level 2" in t.task_text)
    l3 = next(t for t in tasks if "Level 3" in t.task_text)
    assert l1.parent_task_id is None
    assert l2.parent_task_id == l1.task_id
    assert l3.parent_task_id == l2.task_id


def test_empty_document(config):
    tasks = parse_and_walk("", config)
    assert tasks == []


def test_no_tasks_only_text(config):
    tasks = parse_and_walk("# Heading\n\nSome paragraph text.\n", config)
    assert tasks == []
