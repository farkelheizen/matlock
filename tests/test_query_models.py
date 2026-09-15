from __future__ import annotations

from datetime import date

import pytest

from matlock.query_models import (
    AttributeFilterPredicate,
    DatePredicate,
    ProjectQueryResponse,
    ProjectRecord,
    SuperProjectRecord,
    TaskQueryResponse,
    TaskRecord,
    TaskQueryFilters,
    parse_attribute_filter_predicate,
    parse_date_predicate,
)


def test_parse_date_predicate_supports_core_variants() -> None:
    assert parse_date_predicate("2026-01-04") == DatePredicate(field="due_date", operator="=", value=date(2026, 1, 4))
    assert parse_date_predicate("=2026-01-04") == DatePredicate(field="due_date", operator="=", value=date(2026, 1, 4))
    assert parse_date_predicate(">=2026-01-04") == DatePredicate(field="due_date", operator=">=", value=date(2026, 1, 4))
    assert parse_date_predicate("<2026-01-04") == DatePredicate(field="due_date", operator="<", value=date(2026, 1, 4))


def test_parse_date_predicate_rejects_bad_values() -> None:
    with pytest.raises(ValueError):
        parse_date_predicate("bad")
    with pytest.raises(ValueError):
        parse_date_predicate("=2026-99-99")
    with pytest.raises(ValueError):
        parse_date_predicate("!2026-01-01")


def test_task_record_coerces_bool_and_lists() -> None:
    record = TaskRecord.model_validate(
        {
            "task_id": "t-1",
            "file_path": "notes/demo.md",
            "parent_task_id": None,
            "created_date": "2026-01-01",
            "due_date": "2026-01-02",
            "est_comp_date": None,
            "act_comp_date": None,
            "checked": 1,
            "task_text": "Ship the feature",
            "overflow": 0,
            "headers": '["two", "one"]',
            "attributes": '{"severity": "high"}',
            "errors": '["warn"]',
            "twin_index": 2,
            "project_ids": ["alpha", "beta"],
        }
    )

    assert record.checked is True
    assert record.overflow is False
    assert record.headers == ["two", "one"]
    assert record.attributes == {"severity": "high"}
    assert record.errors == ["warn"]
    assert record.project_ids == ["alpha", "beta"]


def test_project_and_super_project_records_model_cleanly() -> None:
    project = ProjectRecord.model_validate(
        {
            "project_id": "p-1",
            "super_project_id": "sp-2",
            "title": "Alpha",
            "home_file": "notes/al.md",
            "priority": "P1",
            "status": "Active",
            "start_date": "2026-01-01",
            "due_date": None,
        }
    )
    assert project.title == "Alpha"

    super_project = SuperProjectRecord.model_validate(
        {"super_project_id": "sp-2", "title": "Foundation", "priority": "P1"}
    )
    assert super_project.super_project_id == "sp-2"


def test_task_query_response_populated_envelope() -> None:
    task = TaskRecord.model_validate(
        {
            "task_id": "t-1",
            "file_path": "notes/demo.md",
            "parent_task_id": None,
            "created_date": "2026-01-01",
            "due_date": None,
            "est_comp_date": None,
            "act_comp_date": None,
            "checked": 0,
            "task_text": "Ship it",
            "overflow": 0,
            "headers": "[]",
            "attributes": "{}",
            "errors": "[]",
            "twin_index": 0,
            "project_ids": [],
        }
    )
    response = TaskQueryResponse(total_matches=5, returned_matches=1, limit=20, offset=0, results=[task])
    assert response.total_matches == 5
    assert response.returned_matches == 1
    assert response.results[0].task_id == "t-1"


def test_task_query_response_count_only_envelope() -> None:
    response = TaskQueryResponse(total_matches=5, returned_matches=0, limit=20, offset=0, results=[])
    assert response.results == []
    assert response.returned_matches == 0
    assert response.total_matches == 5


def test_project_query_response_populated_envelope() -> None:
    project = ProjectRecord.model_validate({"project_id": "p-1"})
    response = ProjectQueryResponse(total_matches=2, returned_matches=1, limit=None, offset=0, results=[project])
    assert response.limit is None
    assert response.results[0].project_id == "p-1"


def test_project_query_response_count_only_envelope() -> None:
    response = ProjectQueryResponse(total_matches=2, returned_matches=0, limit=20, offset=0, results=[])
    assert response.results == []
    assert response.total_matches == 2


def test_parse_attribute_filter_predicate_supports_all_operators() -> None:
    for operator in ("eq", "neq", "gt", "gte", "lt", "lte", "contains"):
        predicate = parse_attribute_filter_predicate(f"owner:{operator}:ops")
        assert predicate == AttributeFilterPredicate(path="$.owner", operator=operator, value="ops")


def test_parse_attribute_filter_predicate_auto_prefixes_bare_path() -> None:
    predicate = parse_attribute_filter_predicate("owner:eq:ops")
    assert predicate.path == "$.owner"


def test_parse_attribute_filter_predicate_preserves_explicit_json_path() -> None:
    predicate = parse_attribute_filter_predicate("$.nested.owner:eq:ops")
    assert predicate.path == "$.nested.owner"


def test_parse_attribute_filter_predicate_coerces_numeric_and_bool_values() -> None:
    assert parse_attribute_filter_predicate("estimate_hours:gte:5").value == 5
    assert parse_attribute_filter_predicate("estimate_hours:gte:5.5").value == 5.5
    assert parse_attribute_filter_predicate("archived:eq:true").value is True
    assert parse_attribute_filter_predicate("archived:eq:false").value is False
    assert parse_attribute_filter_predicate("owner:eq:null").value is None


def test_parse_attribute_filter_predicate_in_operator_splits_and_coerces_list() -> None:
    predicate = parse_attribute_filter_predicate("owner:in:ops,eng,5")
    assert predicate.operator == "in"
    assert predicate.value == ["ops", "eng", 5]


def test_parse_attribute_filter_predicate_rejects_bad_grammar() -> None:
    with pytest.raises(ValueError):
        parse_attribute_filter_predicate("")
    with pytest.raises(ValueError):
        parse_attribute_filter_predicate("owner:eq")
    with pytest.raises(ValueError):
        parse_attribute_filter_predicate(":eq:ops")
    with pytest.raises(ValueError):
        parse_attribute_filter_predicate("owner:bogus:ops")



def test_task_query_filters_accept_default_values() -> None:
    filters = TaskQueryFilters()
    assert filters.checked is None
    assert filters.project_ids == []
    assert filters.super_project_ids == []
