from __future__ import annotations

import json
from pathlib import Path

import pytest

from markdown_stuff.extractor import extract_attributes


@pytest.fixture
def attribute_defs() -> dict:
    config_path = Path(__file__).parent.parent / "config" / "default_config.json"
    with config_path.open(encoding="utf-8") as f:
        return json.load(f)["tasks"]["attributes"]


def test_domain_alias_maps_to_literal_value(attribute_defs):
    text, attrs, errors = extract_attributes("Do the thing ⏫", attribute_defs)
    assert text == "Do the thing"
    assert attrs == {"priority": "high"}
    assert errors == []


def test_date_extraction(attribute_defs):
    text, attrs, errors = extract_attributes("Task 📅 2026-04-10", attribute_defs)
    assert attrs["due_date"] == "2026-04-10"
    assert text == "Task"
    assert errors == []


def test_complete_date_extraction(attribute_defs):
    text, attrs, errors = extract_attributes("Task ✅ 2026-04-11", attribute_defs)
    assert attrs["complete_date"] == "2026-04-11"
    assert text == "Task"
    assert errors == []


def test_invalid_date(attribute_defs):
    text, attrs, errors = extract_attributes("Task 📅 not-a-date", attribute_defs)
    assert attrs["due_date"] is None
    assert len(errors) == 1
    assert "not-a-date" in errors[0]


def test_domain_valid(attribute_defs):
    text, attrs, errors = extract_attributes("Task { priority: low }", attribute_defs)
    assert attrs["priority"] == "low"
    assert errors == []


def test_domain_invalid(attribute_defs):
    text, attrs, errors = extract_attributes("Task { priority: urgent }", attribute_defs)
    assert attrs["priority"] is None
    assert len(errors) == 1
    assert "urgent" in errors[0]


def test_time_extraction(attribute_defs):
    text, attrs, errors = extract_attributes("Task { estimate: 2h }", attribute_defs)
    assert attrs["estimate"] == 7200
    assert text == "Task"
    assert errors == []


def test_invalid_time(attribute_defs):
    text, attrs, errors = extract_attributes("Task { actual: definitely }", attribute_defs)
    assert attrs["actual"] is None
    assert text == "Task"
    assert len(errors) == 1
    assert "definitely" in errors[0]


def test_multiple_aliases_left_to_right(attribute_defs):
    text, attrs, errors = extract_attributes("Task 📅 2026-04-10 ⏫", attribute_defs)
    assert attrs["due_date"] == "2026-04-10"
    assert attrs["priority"] == "high"
    assert text == "Task"
    assert errors == []


def test_curly_brace_extraction(attribute_defs):
    text, attrs, errors = extract_attributes(
        "Check repos { project: Project 2 }", attribute_defs
    )
    assert attrs["project"] == "Project 2"
    assert text == "Check repos"
    assert errors == []


def test_multiple_curly_brace_pairs(attribute_defs):
    text, attrs, errors = extract_attributes(
        "Task { estimate: 1h } { reviewer: alice }", attribute_defs
    )
    assert attrs["estimate"] == 3600
    assert attrs["reviewer"] == "alice"
    assert errors == []


def test_duplicate_alias_vs_alias(attribute_defs):
    text, attrs, errors = extract_attributes("Do thing ⏫ 🔼", attribute_defs)
    # First one wins
    assert attrs["priority"] == "high"
    assert len(errors) == 1
    assert "priority" in errors[0]


def test_duplicate_alias_vs_curly_brace(attribute_defs):
    text, attrs, errors = extract_attributes(
        "Do thing ⏫ { priority: low }", attribute_defs
    )
    assert attrs["priority"] == "high"
    assert len(errors) == 1
    assert "priority" in errors[0]


def test_mixed_aliases_and_curly_braces(attribute_defs):
    text, attrs, errors = extract_attributes(
        "Do stuff 📅 2026-04-10 { project: Foo } { estimate: 50m } ⏫",
        attribute_defs,
    )
    assert attrs["due_date"] == "2026-04-10"
    assert attrs["project"] == "Foo"
    assert attrs["estimate"] == 3000
    assert attrs["priority"] == "high"
    assert text == "Do stuff"
    assert errors == []


def test_no_attributes(attribute_defs):
    text, attrs, errors = extract_attributes("Just a plain task", attribute_defs)
    assert text == "Just a plain task"
    assert attrs == {}
    assert errors == []


def test_empty_string(attribute_defs):
    text, attrs, errors = extract_attributes("", attribute_defs)
    assert text == ""
    assert attrs == {}
    assert errors == []
