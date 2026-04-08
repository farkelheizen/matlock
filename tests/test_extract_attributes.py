from __future__ import annotations

import json
from pathlib import Path

import pytest

from markdown_stuff.extractor import extract_attributes


@pytest.fixture
def aliases() -> dict:
    config_path = Path(__file__).parent.parent / "config" / "default_config.json"
    with config_path.open(encoding="utf-8") as f:
        return json.load(f)["aliases"]


def test_simple_literal(aliases):
    text, attrs, errors = extract_attributes("Do the thing ⏫", aliases)
    assert text == "Do the thing"
    assert attrs == {"priority": "High"}
    assert errors == []


def test_date_extraction(aliases):
    text, attrs, errors = extract_attributes("Task 📅 2026-04-10", aliases)
    assert attrs["due_date"] == "2026-04-10"
    assert text == "Task"
    assert errors == []


def test_invalid_date(aliases):
    text, attrs, errors = extract_attributes("Task 📅 not-a-date", aliases)
    assert attrs["due_date"] is None
    assert len(errors) == 1
    assert "not-a-date" in errors[0]


def test_domain_valid(aliases):
    text, attrs, errors = extract_attributes("Task 🏁 delete", aliases)
    assert attrs["on_completion"] == "delete"
    assert errors == []


def test_domain_invalid(aliases):
    text, attrs, errors = extract_attributes("Task 🏁 archive", aliases)
    assert attrs["on_completion"] is None
    assert len(errors) == 1
    assert "archive" in errors[0]


def test_string_extraction(aliases):
    text, attrs, errors = extract_attributes("Task 🆔 abc-123", aliases)
    assert attrs["id"] == "abc-123"
    assert errors == []


def test_multiple_aliases_left_to_right(aliases):
    text, attrs, errors = extract_attributes("Task 📅 2026-04-10 ⏫", aliases)
    assert attrs["due_date"] == "2026-04-10"
    assert attrs["priority"] == "High"
    assert text == "Task"
    assert errors == []


def test_curly_brace_extraction(aliases):
    text, attrs, errors = extract_attributes(
        "Check repos { project: Project 2 }", aliases
    )
    assert attrs["project"] == "Project 2"
    assert text == "Check repos"
    assert errors == []


def test_multiple_curly_brace_pairs(aliases):
    text, attrs, errors = extract_attributes("Task { a: 1 } { b: 2 }", aliases)
    assert attrs["a"] == "1"
    assert attrs["b"] == "2"
    assert errors == []


def test_duplicate_alias_vs_alias(aliases):
    # ⏫ = priority High, 🔼 = priority Medium — both map to "priority"
    text, attrs, errors = extract_attributes("Do thing ⏫ 🔼", aliases)
    # First one wins
    assert attrs["priority"] == "High"
    assert len(errors) == 1
    assert "priority" in errors[0]


def test_duplicate_alias_vs_curly_brace(aliases):
    # ⏫ sets priority first, then curly-brace tries to set it too
    text, attrs, errors = extract_attributes("Do thing ⏫ { priority: Low }", aliases)
    assert attrs["priority"] == "High"
    assert len(errors) == 1
    assert "priority" in errors[0]


def test_mixed_aliases_and_curly_braces(aliases):
    text, attrs, errors = extract_attributes(
        "Do stuff 📅 2026-04-10 { project: Foo } ⏫", aliases
    )
    assert attrs["due_date"] == "2026-04-10"
    assert attrs["project"] == "Foo"
    assert attrs["priority"] == "High"
    assert text == "Do stuff"
    assert errors == []


def test_no_attributes(aliases):
    text, attrs, errors = extract_attributes("Just a plain task", aliases)
    assert text == "Just a plain task"
    assert attrs == {}
    assert errors == []


def test_empty_string(aliases):
    text, attrs, errors = extract_attributes("", aliases)
    assert text == ""
    assert attrs == {}
    assert errors == []
