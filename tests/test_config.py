from __future__ import annotations

import json
from pathlib import Path


CONFIG_PATH = Path(__file__).parent.parent / "config" / "default_config.json"


def load_config() -> dict:
    with CONFIG_PATH.open(encoding="utf-8") as f:
        return json.load(f)


def test_config_loads():
    config = load_config()
    assert isinstance(config, dict)


def test_config_maxlens_are_integers():
    config = load_config()
    assert isinstance(config["task_text_maxlen"], int)
    assert isinstance(config["header_text_maxlen"], int)


def test_config_aliases_contains_date_key():
    config = load_config()
    assert isinstance(config["aliases"], dict)
    assert "📅" in config["aliases"]


def test_config_all_aliases_have_attribute_and_type():
    config = load_config()
    for alias, entry in config["aliases"].items():
        assert "attribute" in entry, f"Missing 'attribute' for alias {alias!r}"
        assert "type" in entry, f"Missing 'type' for alias {alias!r}"


def test_config_literal_aliases_have_value():
    config = load_config()
    for alias, entry in config["aliases"].items():
        if entry["type"] == "literal":
            assert "value" in entry, f"Literal alias {alias!r} missing 'value'"


def test_config_domain_aliases_have_allowed_list():
    config = load_config()
    for alias, entry in config["aliases"].items():
        if entry["type"] == "domain":
            assert "allowed" in entry, f"Domain alias {alias!r} missing 'allowed'"
            assert isinstance(entry["allowed"], list)
