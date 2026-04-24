def test_public_api_imports():
    from matlock import (
        ParsedMarkdownFile,
        ParsedMarkdownTask,
        extract_tasks_from_markdown,
        parse_front_matter,
    )

    assert callable(extract_tasks_from_markdown)
    assert callable(parse_front_matter)
    assert ParsedMarkdownTask is not None
    assert ParsedMarkdownFile is not None


def test_all_exports_present():
    import matlock

    for name in [
        "parse_front_matter",
        "extract_tasks_from_markdown",
        "ParsedMarkdownTask",
        "ParsedMarkdownFile",
    ]:
        assert hasattr(matlock, name), f"{name} missing from package"
