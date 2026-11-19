from __future__ import annotations

import pytest

from matlock_audit.llm import parse_audit_response, render_prompt


def test_render_prompt_keeps_literal_braces() -> None:
    assert render_prompt('{"a": 1} {content}', "text {x}") == '{"a": 1} text {x}'


def test_parses_plain_fenced_and_wrapped_json() -> None:
    body = '{"risk_score": 7, "llm_confidence_score": 6, "reason": "r", "flagged_snippets": ["abc"]}'
    for text in (body, f"```json\n{body}\n```", f"Sure! {body} Hope that helps."):
        result = parse_audit_response(text)
        assert result.risk_score == 7 and result.flagged_snippets == ["abc"]


def test_scores_are_clamped_and_coerced() -> None:
    result = parse_audit_response('{"risk_score": 42, "llm_confidence_score": -3.4}')
    assert (result.risk_score, result.llm_confidence_score) == (10, 0)


@pytest.mark.parametrize("text", ["", "no json", "[1, 2]", '{"reason": "x"}', '{"risk_score": "high"}'])
def test_invalid_output_raises(text: str) -> None:
    with pytest.raises(ValueError):
        parse_audit_response(text)
