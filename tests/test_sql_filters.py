from __future__ import annotations

from matlock.sql_filters import build_json_path_filter_clause


def test_scalar_operators_compile_to_json_extract() -> None:
    for operator, sql_operator in (
        ("eq", "="),
        ("neq", "!="),
        ("gt", ">"),
        ("gte", ">="),
        ("lt", "<"),
        ("lte", "<="),
    ):
        clause, params = build_json_path_filter_clause(
            path="$.owner", operator=operator, value="ops", json_column="t.attributes"
        )
        assert clause == f"json_extract(t.attributes, ?) {sql_operator} ?"
        assert params == ["$.owner", "ops"]


def test_scalar_operator_unwraps_single_element_list() -> None:
    _, params = build_json_path_filter_clause(
        path="$.owner", operator="eq", value=["ops"], json_column="t.attributes"
    )
    assert params == ["$.owner", "ops"]


def test_scalar_operator_empty_list_coerces_to_none() -> None:
    _, params = build_json_path_filter_clause(
        path="$.owner", operator="eq", value=[], json_column="t.attributes"
    )
    assert params == ["$.owner", None]


def test_in_operator_compiles_to_json_each_exists() -> None:
    clause, params = build_json_path_filter_clause(
        path="$.owner", operator="in", value=["ops", "eng"], json_column="t.attributes"
    )
    assert "json_each" in clause
    assert "EXISTS" in clause
    assert params == ["$.owner", "$.owner", "$.owner", "ops", "eng"]


def test_contains_operator_wraps_scalar_value_in_list() -> None:
    clause, params = build_json_path_filter_clause(
        path="$.tags", operator="contains", value="db", json_column="f.meta_data"
    )
    assert "json_each" in clause
    assert params == ["$.tags", "$.tags", "$.tags", "db"]


def test_json_column_is_parameterized() -> None:
    clause, _ = build_json_path_filter_clause(
        path="$.owner", operator="eq", value="ops", json_column="t.attributes"
    )
    assert "t.attributes" in clause
