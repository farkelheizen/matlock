from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent


def test_cli_exits_zero():
    result = subprocess.run(
        [
            sys.executable,
            "scripts/extract_tasks.py",
            "--base-path",
            str(ROOT),
            "test-data/document-1.md",
        ],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
    )
    assert result.returncode == 0, result.stderr


def test_cli_stdout_is_valid_json():
    result = subprocess.run(
        [
            sys.executable,
            "scripts/extract_tasks.py",
            "--base-path",
            str(ROOT),
            "test-data/document-1.md",
        ],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
    )
    data = json.loads(result.stdout)
    assert isinstance(data, dict)


def test_cli_output_has_expected_keys():
    result = subprocess.run(
        [
            sys.executable,
            "scripts/extract_tasks.py",
            "--base-path",
            str(ROOT),
            "test-data/document-1.md",
        ],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
    )
    data = json.loads(result.stdout)
    assert "meta_data" in data
    assert "tasks" in data
    assert "file_path" in data
    assert "created" in data
    assert "modified" in data
    assert "length" in data
    assert "word_count" in data
    assert "sha256" in data


def test_cli_output_includes_file_metadata():
    markdown_path = ROOT / "test-data" / "document-1.md"
    result = subprocess.run(
        [
            sys.executable,
            "scripts/extract_tasks.py",
            "--base-path",
            str(ROOT),
            "test-data/document-1.md",
        ],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
    )
    data = json.loads(result.stdout)
    stat_result = markdown_path.stat()
    created_timestamp = getattr(stat_result, "st_birthtime", stat_result.st_ctime)
    markdown_text = markdown_path.read_text(encoding="utf-8")

    assert data["file_path"] == "test-data/document-1.md"
    assert data["created"] == int(created_timestamp * 1000)
    assert data["modified"] == int(stat_result.st_mtime * 1000)
    assert data["length"] == stat_result.st_size
    assert data["word_count"] == len(re.findall(r"\S+", markdown_text))
    assert data["sha256"] == hashlib.sha256(markdown_path.read_bytes()).hexdigest()


def test_cli_task_ids_include_file_path():
    result_1 = subprocess.run(
        [
            sys.executable,
            "scripts/extract_tasks.py",
            "--base-path",
            str(ROOT),
            "test-data/document-1.md",
        ],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
    )
    result_2 = subprocess.run(
        [
            sys.executable,
            "scripts/extract_tasks.py",
            "--base-path",
            str(ROOT / "test-data"),
            "document-1.md",
        ],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
    )

    assert result_1.returncode == 0, result_1.stderr
    assert result_2.returncode == 0, result_2.stderr

    data_1 = json.loads(result_1.stdout)
    data_2 = json.loads(result_2.stdout)

    assert data_1["tasks"][0]["task_id"] != data_2["tasks"][0]["task_id"]


def test_cli_missing_file_exits_nonzero():
    result = subprocess.run(
        [
            sys.executable,
            "scripts/extract_tasks.py",
            "--base-path",
            str(ROOT),
            "nonexistent.md",
        ],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
    )
    assert result.returncode != 0
