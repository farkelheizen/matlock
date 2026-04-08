def test_public_api_imports():
    from markdown_stuff import extract_tasks_from_markdown, ParsedTask, ParsedDocument

    assert callable(extract_tasks_from_markdown)
    assert ParsedTask is not None
    assert ParsedDocument is not None


def test_all_exports_present():
    import markdown_stuff

    for name in ["parse_ast", "parse_tokens", "parse_front_matter",
                 "extract_tasks_from_markdown", "ParsedTask", "ParsedDocument"]:
        assert hasattr(markdown_stuff, name), f"{name} missing from package"
