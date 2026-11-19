from __future__ import annotations

from typing import Any

from .models import Finding


def mask(value: str) -> str:
    """Reduce a sensitive value to a short non-reversible preview."""
    value = value.strip()
    return f"{value[:2]}***" if len(value) > 4 else "***"


_ENTROPY_LIMITS = {"Base64HighEntropyString": 4.5, "HexHighEntropyString": 3.0}
# Line-based filters only. The allowlist filter is deliberately absent: scanned
# content must not be able to opt out with an inline "pragma: allowlist secret".
_FILTERS = [
    "detect_secrets.filters.heuristic." + name
    for name in (
        "is_indirect_reference",
        "is_likely_id_string",
        "is_not_alphanumeric_string",
        "is_potential_uuid",
        "is_prefixed_with_dollar_sign",
        "is_sequential_string",
        "is_templated_secret",
    )
]


def _settings(plugins: tuple[str, ...]) -> dict[str, Any]:
    from detect_secrets.core.plugins import util

    names = plugins or sorted(c.__name__ for c in util.get_mapping_from_secret_type_to_class().values())
    return {
        "plugins_used": [
            {"name": name, **({"limit": _ENTROPY_LIMITS[name]} if name in _ENTROPY_LIMITS else {})}
            for name in names
        ],
        "filters_used": [{"path": path} for path in _FILTERS],
    }


class HeuristicScanner:
    """Layer 1: detect-secrets over in-memory text (never touches files or baselines)."""

    def __init__(self, plugins: tuple[str, ...] = ()) -> None:
        try:
            from detect_secrets.core.scan import _process_line_based_plugins
            from detect_secrets.settings import transient_settings
        except ImportError as exc:
            raise ImportError(
                "The heuristic layer requires detect-secrets. "
                "Install it with: pip install 'matlock[audit]'"
            ) from exc
        # Private helper: the public scanners read files or run in eager "adhoc" mode.
        self._process = _process_line_based_plugins
        self._transient = transient_settings
        self._config = _settings(plugins)

    def scan(self, text: str) -> list[Finding]:
        lines = list(enumerate(text.splitlines(), start=1))
        with self._transient(self._config):
            return [
                Finding(
                    type=secret.type,
                    line=secret.line_number,
                    preview=mask(secret.secret_value or ""),
                    source="detect-secrets",
                )
                for secret in self._process(lines, "in-memory")
            ]


def detect_secrets_available() -> bool:
    try:
        import detect_secrets  # noqa: F401
    except ImportError:
        return False
    return True


def validate_plugins(plugins: tuple[str, ...]) -> None:
    """Fail early on unknown plugin names; requires detect-secrets."""
    from detect_secrets.settings import get_plugins, transient_settings

    with transient_settings(_settings(plugins)):
        list(get_plugins())
