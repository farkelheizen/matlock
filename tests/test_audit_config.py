from __future__ import annotations

from pathlib import Path

import pytest

from matlock_ingest.config import ConfigurationError
from matlock_audit.config import load_audit_config
from matlock_audit.models import DEFAULT_PROMPT
from tests.audit_helpers import build_index


def load(tmp_path: Path, toml: str = ""):
    return load_audit_config(build_index(tmp_path, {"a.md": "# A\nx\n"}, toml))


def test_defaults(tmp_path: Path) -> None:
    _, config = load(tmp_path)
    assert config.enabled_layers == ("heuristic", "llm")
    assert config.domains == ("chunk", "document")
    assert config.attribute_key == "secret_scan"
    assert (config.max_chunk_chars, config.max_document_chars) == (4000, 50000)
    assert config.prompt == DEFAULT_PROMPT and config.prompt_source == "default"
    assert config.hooks == {}


def test_prompt_file_is_relative_to_config(tmp_path: Path) -> None:
    (tmp_path / "p.txt").write_text('Look {"a": 1} at {content}', encoding="utf-8")
    _, config = load(tmp_path, '[audit.llm]\nprompt_file = "p.txt"\n')
    assert config.prompt_source == "file" and config.prompt.startswith("Look")


@pytest.mark.parametrize(
    "toml",
    [
        '[audit]\nenabled_layers = ["magic"]\n',
        "[audit]\nenabled_layers = []\n",
        '[audit]\ndomains = ["file"]\n',
        "[audit]\nbatch_size = 0\n",
        "[audit]\nmax_chunk_chars = true\n",
        '[audit]\nattribute_key = "bad key"\n',
        "[audit]\nsurprise = 1\n",
        '[audit.llm]\nprompt = "x {content}"\nprompt_file = "p.txt"\n',
        '[audit.llm]\nprompt = "no placeholder"\n',
        '[audit.llm]\nprompt_file = "missing.txt"\n',
        '[audit.hooks]\npre_scan = ["a:b"]\n',
        '[audit.hooks]\npre_scan = "nocolon"\n',
        '[audit.hooks]\nunknown_hook = "a:b"\n',
        "[audit.heuristics]\nplugins = 3\n",
    ],
)
def test_invalid_configuration(tmp_path: Path, toml: str) -> None:
    with pytest.raises(ConfigurationError):
        load(tmp_path, toml)
