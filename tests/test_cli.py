from __future__ import annotations

from pathlib import Path

import pytest

from matlock.cli import main


def _config(tmp_path: Path) -> Path:
    vault = tmp_path / "vault"
    vault.mkdir()
    config = tmp_path / "matlock.toml"
    config.write_text(f'[ingest]\nvault_path = "{vault}"\n', encoding="utf-8")
    return config


def test_unified_cli_help_lists_domain_commands(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["--help"]) == 0
    output = capsys.readouterr().out
    assert "ingest" in output and "watch" in output and "audit" in output


def test_unified_cli_dispatches_ingest_config_check(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = _config(tmp_path)
    assert main(["--config", str(config), "check-config"]) == 0
    assert "Configuration valid" in capsys.readouterr().out


def test_unified_cli_dispatches_audit_config_check(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = _config(tmp_path)
    assert main([
        "--config", str(config), "audit", "check-config", "--no-llm", "--no-heuristics"
    ]) == 0
    output = capsys.readouterr().out
    assert "Audit configuration valid" in output and "Layers: none" in output


def test_unified_cli_does_not_keep_ingest_scan_alias() -> None:
    with pytest.raises(SystemExit) as exc_info:
        main(["scan"])
    assert exc_info.value.code == 2
