from __future__ import annotations

from markdown_stuff.extractor import compute_task_id, truncate_headers, truncate_text


def test_compute_task_id_returns_hex_string():
    tid = compute_task_id("My task", False, ["H1"], None, 0)
    assert isinstance(tid, str)
    assert len(tid) == 64
    assert all(c in "0123456789abcdef" for c in tid)


def test_compute_task_id_deterministic():
    tid1 = compute_task_id("My task", False, ["H1"], None, 0)
    tid2 = compute_task_id("My task", False, ["H1"], None, 0)
    assert tid1 == tid2


def test_compute_task_id_different_inputs_differ():
    tid1 = compute_task_id("Task A", False, [], None, 0)
    tid2 = compute_task_id("Task B", False, [], None, 0)
    assert tid1 != tid2


def test_compute_task_id_differs_on_overflow():
    tid1 = compute_task_id("Task", False, [], None, 0)
    tid2 = compute_task_id("Task", True, [], None, 0)
    assert tid1 != tid2


def test_compute_task_id_differs_on_twin_index():
    tid1 = compute_task_id("Task", False, [], None, 0)
    tid2 = compute_task_id("Task", False, [], None, 1)
    assert tid1 != tid2


def test_compute_task_id_differs_on_headers():
    tid1 = compute_task_id("Task", False, [], None, 0)
    tid2 = compute_task_id("Task", False, ["H1"], None, 0)
    assert tid1 != tid2


def test_compute_task_id_differs_on_file_path():
    tid1 = compute_task_id("Task", False, [], None, 0, "one.md")
    tid2 = compute_task_id("Task", False, [], None, 0, "two.md")
    assert tid1 != tid2


def test_truncate_text_within_limit():
    result, truncated = truncate_text("hello", 10)
    assert result == "hello"
    assert truncated is False


def test_truncate_text_exceeds_limit():
    result, truncated = truncate_text("hello world", 5)
    assert result == "hello..."
    assert truncated is True


def test_truncate_text_exactly_at_limit():
    result, truncated = truncate_text("hello", 5)
    assert result == "hello"
    assert truncated is False


def test_truncate_text_empty():
    result, truncated = truncate_text("", 10)
    assert result == ""
    assert truncated is False


def test_truncate_headers_short_headers_unchanged():
    headers = ["H1 title", "H2 title"]
    result = truncate_headers(headers, 50)
    assert result == ["H1 title", "H2 title"]


def test_truncate_headers_long_header_truncated():
    long = "A" * 60
    result = truncate_headers([long], 50)
    assert result == ["A" * 50 + "..."]


def test_truncate_headers_mixed():
    headers = ["Short", "A" * 60, "Also short"]
    result = truncate_headers(headers, 50)
    assert result[0] == "Short"
    assert result[1] == "A" * 50 + "..."
    assert result[2] == "Also short"


def test_truncate_headers_empty_list():
    assert truncate_headers([], 50) == []
