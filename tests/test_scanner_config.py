from __future__ import annotations

from pathlib import Path

import pytest

from matlock2_ingest.config import ConfigurationError
from matlock2_scanner.config import load_scanner_config
from matlock2_scanner.models import DEFAULT_PROMPT
from tests.scanner_helpers import build_index


def load(tmp_path: Path, toml: str = ""):
    return load_scanner_config(build_index(tmp_path, {"a.md": "# A\nx\n"}, toml))


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
    _, config = load(tmp_path, '[secret_scanner.llm]\nprompt_file = "p.txt"\n')
    assert config.prompt_source == "file" and config.prompt.startswith("Look")


@pytest.mark.parametrize(
    "toml",
    [
        '[secret_scanner]\nenabled_layers = ["magic"]\n',
        "[secret_scanner]\nenabled_layers = []\n",
        '[secret_scanner]\ndomains = ["file"]\n',
        "[secret_scanner]\nbatch_size = 0\n",
        "[secret_scanner]\nmax_chunk_chars = true\n",
        '[secret_scanner]\nattribute_key = "bad key"\n',
        "[secret_scanner]\nsurprise = 1\n",
        '[secret_scanner.llm]\nprompt = "x {content}"\nprompt_file = "p.txt"\n',
        '[secret_scanner.llm]\nprompt = "no placeholder"\n',
        '[secret_scanner.llm]\nprompt_file = "missing.txt"\n',
        '[secret_scanner.hooks]\npre_scan = ["a:b"]\n',
        '[secret_scanner.hooks]\npre_scan = "nocolon"\n',
        '[secret_scanner.hooks]\nunknown_hook = "a:b"\n',
        "[secret_scanner.heuristics]\nplugins = 3\n",
    ],
)
def test_invalid_configuration(tmp_path: Path, toml: str) -> None:
    with pytest.raises(ConfigurationError):
        load(tmp_path, toml)
