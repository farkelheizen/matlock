"""Example host-level search policy hooks."""

from __future__ import annotations

from typing import Any

from matlock_mcp.policy import PolicyDecision


class ExampleSearchPolicy:
    """Cap search requests and hide records explicitly marked by the example."""

    max_limit = 25
    hidden_marker = "mcp_example_hidden"

    def rewrite(self, _tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        rewritten = dict(arguments)
        limit = rewritten.get("limit")
        if type(limit) is int:
            rewritten["limit"] = min(limit, self.max_limit)
        return rewritten

    def document(self, record: dict[str, Any]) -> PolicyDecision:
        metadata = record.get("metadata")
        return self._decision(
            isinstance(metadata, dict) and metadata.get(self.hidden_marker) is True
        )

    def chunk(self, record: dict[str, Any]) -> PolicyDecision:
        attributes = record.get("attributes")
        return self._decision(
            isinstance(attributes, dict) and attributes.get(self.hidden_marker) is True
        )

    @staticmethod
    def _decision(hide: bool) -> PolicyDecision:
        return PolicyDecision("hide" if hide else "allow")
