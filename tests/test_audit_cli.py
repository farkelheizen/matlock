from __future__ import annotations

import threading
from pathlib import Path

import pytest

pytest.importorskip("detect_secrets")

from matlock_audit.cli import main, run_loop
from tests.audit_helpers import FakeAuditor, attributes, build_index, make_engine


def test_check_config(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    config = build_index(tmp_path)
    assert main(["--config", str(config), "check-config", "--no-llm"]) == 0
    assert "Layers: heuristic" in capsys.readouterr().out


def test_scan_command_heuristics_only(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    config = build_index(tmp_path)
    assert main(["--config", str(config), "scan", "--no-llm"]) == 0
    assert "scanned=4" in capsys.readouterr().out
    assert len(attributes(config)) == 4
    assert main(["--config", str(config), "scan", "--no-llm"]) == 0
    assert "scanned=0" in capsys.readouterr().out


def test_scan_dry_run_and_limit(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    config = build_index(tmp_path)
    assert main(["--config", str(config), "scan", "--no-llm", "--dry-run"]) == 0
    assert "Dry run complete: scanned=4" in capsys.readouterr().out
    assert attributes(config) == {}
    assert main(["--config", str(config), "scan", "--no-llm", "--limit", "1"]) == 0
    assert len(attributes(config)) == 1


@pytest.mark.parametrize(
    "extra",
    [["--limit", "0"], ["--no-llm", "--no-heuristics"]],
)
def test_invalid_arguments_exit_2(tmp_path: Path, extra: list[str]) -> None:
    config = build_index(tmp_path)
    assert main(["--config", str(config), "scan", *extra]) == 2


def test_missing_database_exits_2(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    config = tmp_path / "matlock.toml"
    (tmp_path / "vault").mkdir()
    config.write_text('[ingest]\nvault_path = "./vault"\ndatabase_path = "./none.sqlite3"\n')
    assert main(["--config", str(config), "scan", "--no-llm"]) == 2
    assert "matlock ingest" in capsys.readouterr().err


def test_missing_config_exits_2(tmp_path: Path) -> None:
    assert main(["--config", str(tmp_path / "nope.toml"), "check-config"]) == 2


def test_run_command_with_max_passes(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    config = build_index(tmp_path, audit_toml="[audit]\ninterval_seconds = 1\n")
    assert main(["--config", str(config), "run", "--no-llm", "--max-passes", "2"]) == 0
    out = capsys.readouterr().out
    assert out.count("Scan complete") == 2 and "Scanner stopped." in out


def test_run_loop_picks_up_new_content_and_stops(tmp_path: Path) -> None:
    config = build_index(tmp_path)
    engine = make_engine(config, auditor=FakeAuditor())
    stop = threading.Event()
    reports: list[str] = []

    def emit(line: str) -> None:
        reports.append(line)
        if len(reports) == 2:
            stop.set()

    assert run_loop(engine, 0, stop, emit=emit) == 2
    assert "scanned=4" in reports[0] and "scanned=0" in reports[1]


def test_run_loop_exits_immediately_when_stopped(tmp_path: Path) -> None:
    engine = make_engine(build_index(tmp_path), auditor=None)
    stop = threading.Event()
    stop.set()
    assert run_loop(engine, 60, stop) == 0


def test_scan_file_prints_json_without_touching_the_database(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    import json

    config = build_index(tmp_path)
    target = tmp_path / "candidate.md"
    target.write_text("# Note\nkey = AKIAIOSFODNN7EXAMPLE\n", encoding="utf-8")
    assert main(["--config", str(config), "scan-file", str(target), "--no-llm"]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["domain"] == "document" and output["path"] == str(target)
    assert output["result"]["regex_matches_found"] and output["result"]["risk_score"] >= 8
    assert "AKIAIOSFODNN7EXAMPLE" not in json.dumps(output)
    assert attributes(config) == {}


def test_scan_file_reads_stdin_and_works_without_a_database(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    import io
    import json

    (tmp_path / "vault").mkdir()
    config = tmp_path / "matlock.toml"
    config.write_text('[ingest]\nvault_path = "./vault"\ndatabase_path = "./none.sqlite3"\n')
    monkeypatch.setattr("sys.stdin", io.StringIO("nothing here\n"))
    assert main(["--config", str(config), "scan-file", "-", "--no-llm"]) == 0
    assert json.loads(capsys.readouterr().out)["result"]["risk_score"] == 0
    assert not (tmp_path / "none.sqlite3").exists()


@pytest.mark.parametrize("name", ["missing.md", "binary.bin"])
def test_scan_file_errors_exit_2(tmp_path: Path, name: str) -> None:
    config = build_index(tmp_path)
    (tmp_path / "binary.bin").write_bytes(b"\xff\xfe\x00bad")
    assert main(["--config", str(config), "scan-file", str(tmp_path / name), "--no-llm"]) == 2


def test_scan_file_fail_above_sets_exit_status(tmp_path: Path) -> None:
    config = build_index(tmp_path)
    risky = tmp_path / "risky.md"
    risky.write_text("key = AKIAIOSFODNN7EXAMPLE\n", encoding="utf-8")
    safe = tmp_path / "safe.md"
    safe.write_text("just text\n", encoding="utf-8")
    base = ["--config", str(config), "scan-file"]
    assert main([*base, str(risky), "--no-llm", "--fail-above", "5"]) == 1
    assert main([*base, str(risky), "--no-llm", "--fail-above", "8"]) == 0  # strictly above
    assert main([*base, str(safe), "--no-llm", "--fail-above", "0"]) == 0
    assert main([*base, str(risky), "--no-llm"]) == 0


def _scanned(tmp_path: Path) -> Path:
    config = build_index(tmp_path)
    assert main(["--config", str(config), "scan", "--no-llm"]) == 0
    return config


def test_reset_requires_a_selection(tmp_path: Path) -> None:
    config = _scanned(tmp_path)
    assert main(["--config", str(config), "reset"]) == 2
    assert main(["--config", str(config), "reset", "--all", "--domain", "chunk"]) == 2
    assert main(["--config", str(config), "reset", "--sha256", "xyz"]) == 2
    assert len(attributes(config)) == 4


def test_reset_dry_run_then_all_allows_rescan(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = _scanned(tmp_path)
    capsys.readouterr()
    assert main(["--config", str(config), "reset", "--all", "--dry-run"]) == 0
    assert "Would remove 4" in capsys.readouterr().out and len(attributes(config)) == 4
    assert main(["--config", str(config), "reset", "--all"]) == 0
    assert "Removed 4" in capsys.readouterr().out and attributes(config) == {}
    assert main(["--config", str(config), "scan", "--no-llm"]) == 0
    assert "scanned=4" in capsys.readouterr().out


def test_reset_filters_and_preserves_other_keys(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    import sqlite3

    from matlock_ingest.config import load_config

    config = _scanned(tmp_path)
    attrs = attributes(config)
    risky = next(
        sha for (d, sha), v in attrs.items() if d == "chunk" and v["secret_scan"]["risk_score"] >= 8
    )
    database = load_config(config).database_path
    connection = sqlite3.connect(database)
    connection.execute(
        "UPDATE content_attributes SET attributes_json = json_set(attributes_json, '$.other', 1) "
        "WHERE sha256 = ?", (risky,),
    )
    connection.commit()
    connection.close()
    capsys.readouterr()

    assert main(["--config", str(config), "reset", "--domain", "chunk", "--min-risk", "8"]) == 0
    after = attributes(config)
    assert after[("chunk", risky)] == {"other": 1}  # row kept, only scanner key removed
    assert len(after) == 4
    # The scanner key is gone now, so the same row no longer matches.
    assert main(["--config", str(config), "reset", "--sha256", risky]) == 0
    assert "Removed 0" in capsys.readouterr().out.splitlines()[-1]
    assert main(["--config", str(config), "reset", "--status", "ok"]) == 0
    assert "Removed 0" in capsys.readouterr().out.splitlines()[-1]
    assert main(["--config", str(config), "reset", "--status", "disabled", "--domain", "document"]) == 0
    assert len([k for k in attributes(config) if k[0] == "document"]) == 0
    assert ("chunk", risky) in attributes(config)


def test_reset_deletes_rows_that_only_hold_scanner_results(tmp_path: Path) -> None:
    config = _scanned(tmp_path)
    assert main(["--config", str(config), "reset", "--domain", "chunk"]) == 0
    assert {d for d, _ in attributes(config)} == {"document"}
