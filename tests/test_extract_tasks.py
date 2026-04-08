from __future__ import annotations

import json
from pathlib import Path

import pytest

from markdown_stuff.extractor import extract_tasks_from_markdown

ROOT = Path(__file__).parent.parent


@pytest.fixture
def config() -> dict:
    with (ROOT / "config" / "default_config.json").open(encoding="utf-8") as f:
        return json.load(f)


def test_document_1_task_count(config):
    md = (ROOT / "test-data" / "document-1.md").read_text(encoding="utf-8")
    doc = extract_tasks_from_markdown(md, config)
    assert len(doc.tasks) == 7


def test_document_1_metadata(config):
    md = (ROOT / "test-data" / "document-1.md").read_text(encoding="utf-8")
    doc = extract_tasks_from_markdown(md, config)
    assert doc.meta_data["status"] == "In Progress"


def test_document_1_check_repos_attributes(config):
    md = (ROOT / "test-data" / "document-1.md").read_text(encoding="utf-8")
    doc = extract_tasks_from_markdown(md, config)
    task = next(t for t in doc.tasks if "Check repos" in t.task_text)
    assert task.attributes.get("due_date") == "2026-04-10"
    assert task.attributes.get("project") == "Project 2"


def test_document_1_commit_repo_2_checked(config):
    md = (ROOT / "test-data" / "document-1.md").read_text(encoding="utf-8")
    doc = extract_tasks_from_markdown(md, config)
    task = next(t for t in doc.tasks if "Commit repo 2" in t.task_text)
    assert task.checked is True


def test_document_1_check_repo_1_parent(config):
    md = (ROOT / "test-data" / "document-1.md").read_text(encoding="utf-8")
    doc = extract_tasks_from_markdown(md, config)
    parent = next(t for t in doc.tasks if "Check repos" in t.task_text)
    child = next(t for t in doc.tasks if t.task_text == "Check repo 1")
    assert child.parent_task_id == parent.task_id


def test_document_1_multiline_continuation(config):
    md = (ROOT / "test-data" / "document-1.md").read_text(encoding="utf-8")
    doc = extract_tasks_from_markdown(md, config)
    task = next(t for t in doc.tasks if "Exclude classes" in t.task_text)
    assert "Is this still cool?" in task.task_text


def test_document_1_backlog_headers(config):
    md = (ROOT / "test-data" / "document-1.md").read_text(encoding="utf-8")
    doc = extract_tasks_from_markdown(md, config)
    backlog_tasks = [t for t in doc.tasks if "Backlog" in t.headers]
    assert len(backlog_tasks) >= 1


def test_document_2_single_task(config):
    md = (ROOT / "test-data" / "document-2.md").read_text(encoding="utf-8")
    doc = extract_tasks_from_markdown(md, config)
    assert len(doc.tasks) == 1


def test_document_2_metadata(config):
    md = (ROOT / "test-data" / "document-2.md").read_text(encoding="utf-8")
    doc = extract_tasks_from_markdown(md, config)
    assert doc.meta_data.get("status") == "In Progress"


def test_document_2_task_attributes(config):
    md = (ROOT / "test-data" / "document-2.md").read_text(encoding="utf-8")
    doc = extract_tasks_from_markdown(md, config)
    task = doc.tasks[0]
    assert task.attributes.get("due_date") == "2026-04-10"
    assert task.attributes.get("project") == "Project 2"


def test_empty_document_no_tasks(config):
    md = "---\ntitle: Test\n---\n\nNo tasks here.\n"
    doc = extract_tasks_from_markdown(md, config)
    assert len(doc.tasks) == 0
    assert doc.meta_data.get("title") == "Test"


def test_no_frontmatter(config):
    md = "- [ ] A task\n"
    doc = extract_tasks_from_markdown(md, config)
    assert len(doc.tasks) == 1
    assert doc.meta_data == {}
