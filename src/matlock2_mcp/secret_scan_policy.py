"""A search policy that hides content flagged by matlock2-scanner."""

from __future__ import annotations

import re
from typing import Any

from .policy import PolicyDecision

_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class SecretScanPolicy:
    """Hide documents and chunks whose stored scan result is risky or missing.

    Fail-closed by default: content with no scan result, a malformed result, or a
    risk score at or above `threshold` is hidden. Results skipped as `oversize`
    are hidden too, because they were never evaluated. Skips requested by a
    `pre_scan` hook are allowed.

    A document's own result (from its whole body) hides the entire document and
    all of its chunks. Set `use_document_results=False` to decide per chunk, which
    keeps the clean parts of a document searchable.
    """

    def __init__(
        self,
        threshold: int = 5,
        *,
        key: str = "secret_scan",
        hide_unscanned: bool = True,
        hide_skipped_reasons: tuple[str, ...] = ("oversize",),
        use_document_results: bool = True,
    ) -> None:
        if isinstance(threshold, bool) or not isinstance(threshold, int) or not 0 <= threshold <= 10:
            raise ValueError("threshold must be an integer from 0 to 10")
        if not _KEY.match(key):
            raise ValueError("key must be an ASCII identifier")
        self.threshold = threshold
        self.key = key
        self.hide_unscanned = hide_unscanned
        self.hide_skipped_reasons = frozenset(hide_skipped_reasons)
        self.use_document_results = use_document_results

    def rewrite(self, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        return arguments

    def _blocked(self, attributes: dict[str, Any] | None) -> bool:
        if attributes is None:
            return self.hide_unscanned
        result = attributes.get(self.key)
        if result is None:
            return self.hide_unscanned
        if not isinstance(result, dict):
            return True
        risk = result.get("risk_score")
        if isinstance(risk, bool) or not isinstance(risk, int | float):
            return True
        if risk >= self.threshold:
            return True
        return (
            result.get("llm_status") == "skipped"
            and result.get("skip_reason") in self.hide_skipped_reasons
        )

    @staticmethod
    def _decision(hide: bool) -> PolicyDecision:
        return PolicyDecision("hide" if hide else "allow")

    def document(self, record: dict[str, Any]) -> PolicyDecision:
        if not self.use_document_results:
            return PolicyDecision()
        return self._decision(self._blocked(record["attributes"]))

    def chunk(self, record: dict[str, Any]) -> PolicyDecision:
        return self._decision(self._blocked(record["attributes"]))
