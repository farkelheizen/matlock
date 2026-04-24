from __future__ import annotations

import pytest
from pydantic import ValidationError

from matlock.models import ParsedMarkdownFile, ParsedMarkdownTask


def make_task(**overrides) -> dict:
    defaults = {
        "checked": False,
        "task_text": "Do the thing",
        "headers": ["H1"],
        "attributes": {},
        "parent_task_id": None,
        "task_id": "abc123",
    }
    defaults.update(overrides)
    return defaults


def test_parsed_task_basic():
    task = ParsedMarkdownTask(**make_task())
    assert task.checked is False
    assert task.task_text == "Do the thing"
    assert task.headers == ["H1"]
    assert task.attributes == {}
    assert task.parent_task_id is None
    assert task.task_id == "abc123"


def test_parsed_task_errors_default():
    task = ParsedMarkdownTask(**make_task())
    assert task.errors == []


def test_parsed_task_overflow_default():
    task = ParsedMarkdownTask(**make_task())
    assert task.overflow is False


def test_parsed_task_twin_index_default():
    task = ParsedMarkdownTask(**make_task())
    assert task.twin_index == 0


def test_parsed_task_checked_true():
    task = ParsedMarkdownTask(**make_task(checked=True))
    assert task.checked is True


def test_parsed_task_with_errors():
    task = ParsedMarkdownTask(**make_task(errors=["some error"]))
    assert task.errors == ["some error"]


def test_parsed_task_with_overflow():
    task = ParsedMarkdownTask(**make_task(overflow=True))
    assert task.overflow is True


def test_parsed_task_with_parent():
    task = ParsedMarkdownTask(**make_task(parent_task_id="parent-id-xyz"))
    assert task.parent_task_id == "parent-id-xyz"


def test_parsed_task_with_twin_index():
    task = ParsedMarkdownTask(**make_task(twin_index=2))
    assert task.twin_index == 2


def test_parsed_document():
    task = ParsedMarkdownTask(**make_task())
    doc = ParsedMarkdownFile(meta_data={"status": "Active"}, tasks=[task], word_count=3)
    assert doc.meta_data == {"status": "Active"}
    assert len(doc.tasks) == 1
    assert doc.tasks[0].task_text == "Do the thing"
    assert doc.word_count == 3


def test_parsed_document_empty_tasks():
    doc = ParsedMarkdownFile(meta_data={}, tasks=[])
    assert doc.tasks == []


def test_parsed_task_missing_required_field():
    with pytest.raises(ValidationError):
        ParsedMarkdownTask(
            checked=False,
            # task_text missing
            headers=[],
            attributes={},
            parent_task_id=None,
            task_id="x",
        )


def test_parsed_task_missing_task_id():
    with pytest.raises(ValidationError):
        ParsedMarkdownTask(
            checked=False,
            task_text="hello",
            headers=[],
            attributes={},
            parent_task_id=None,
            # task_id missing
        )
