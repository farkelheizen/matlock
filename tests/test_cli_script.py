from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent


def test_cli_exits_zero():
    result = subprocess.run(
        [sys.executable, "scripts/extract_tasks.py", "test-data/document-1.md"],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
    )
    assert result.returncode == 0, result.stderr


def test_cli_stdout_is_valid_json():
    result = subprocess.run(
        [sys.executable, "scripts/extract_tasks.py", "test-data/document-1.md"],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
    )
    data = json.loads(result.stdout)
    assert isinstance(data, dict)


def test_cli_output_has_expected_keys():
    result = subprocess.run(
        [sys.executable, "scripts/extract_tasks.py", "test-data/document-1.md"],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
    )
    data = json.loads(result.stdout)
    assert "meta_data" in data
    assert "tasks" in data


def test_cli_missing_file_exits_nonzero():
    result = subprocess.run(
        [sys.executable, "scripts/extract_tasks.py", "nonexistent.md"],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
    )
    assert result.returncode != 0
