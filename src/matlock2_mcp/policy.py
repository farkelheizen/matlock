"""Trusted host policy interfaces for read-only search."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Protocol


@dataclass(frozen=True)
class PolicyDecision:
    action: Literal["allow", "hide", "null"] = "allow"
    disclose_metadata: bool = True
    disclose_source: bool = True
    disclose_attributes: bool = True


class SearchPolicy(Protocol):
    def rewrite(self, tool: str, arguments: dict[str, Any]) -> dict[str, Any]: ...

    def document(self, record: dict[str, Any]) -> PolicyDecision: ...

    def chunk(self, record: dict[str, Any]) -> PolicyDecision: ...
