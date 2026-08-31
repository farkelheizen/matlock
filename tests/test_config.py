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
    assert isinstance(config["tasks"]["task_text_maxlen"], int)
    assert isinstance(config["headers"]["header_text_maxlen"], int)


def test_config_has_redacted_cache_directory():
    config = load_config()
    assert config["cache"]["redacted_dir"] == "~/.matlock/cache/redacted/"


def test_config_aliases_contains_date_key():
    config = load_config()
    attributes = config["tasks"]["attributes"]
    assert isinstance(attributes, dict)
    assert attributes["due_date"]["alias"] == "📅"


def test_config_all_attributes_have_type():
    config = load_config()
    for attr_name, entry in config["tasks"]["attributes"].items():
        assert "type" in entry, f"Missing 'type' for attribute {attr_name!r}"


def test_config_date_attributes_can_define_alias():
    config = load_config()
    assert config["tasks"]["attributes"]["complete_date"]["alias"] == "✅"


def test_config_domain_attributes_have_values_map():
    config = load_config()
    values = config["tasks"]["attributes"]["priority"]["values"]
    assert isinstance(values, dict)
    assert values["low"]["alias"] == "🔽"
    assert values["medium"]["alias"] == "🔼"
    assert values["high"]["alias"] == "⏫"


def test_config_time_attributes_exist():
    config = load_config()
    attributes = config["tasks"]["attributes"]
    assert attributes["estimate"]["type"] == "time"
    assert attributes["actual"]["type"] == "time"
