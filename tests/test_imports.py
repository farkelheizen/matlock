def test_public_api_imports():
    from markdown_stuff import (
        ParsedDocument,
        ParsedTask,
        extract_tasks_from_markdown,
        parse_front_matter,
    )

    assert callable(extract_tasks_from_markdown)
    assert callable(parse_front_matter)
    assert ParsedTask is not None
    assert ParsedDocument is not None


def test_all_exports_present():
    import markdown_stuff

    for name in [
        "parse_front_matter",
        "extract_tasks_from_markdown",
        "ParsedTask",
        "ParsedDocument",
    ]:
        assert hasattr(markdown_stuff, name), f"{name} missing from package"
