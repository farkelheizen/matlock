from __future__ import annotations

from matlock.search.chunking import chunk_markdown, count_tokens
from matlock.search.frontmatter_template import render_frontmatter_template


def test_fixed_token_chunking_prefers_header_boundaries_with_overlap():
    markdown = """# One

alpha beta gamma

# Two

delta epsilon zeta
"""

    chunks = chunk_markdown(markdown, chunk_size=6, chunk_overlap=1)

    assert len(chunks) == 2
    assert chunks[0].startswith("# One")
    assert chunks[1].startswith("gamma")
    assert all(count_tokens(chunk) <= 6 for chunk in chunks)


def test_fixed_token_chunking_closes_unbalanced_code_fences():
    markdown = """```python
one
two
three
four
five
```"""

    chunks = chunk_markdown(markdown, chunk_size=3, chunk_overlap=0)

    assert chunks[0].startswith("```python")
    assert chunks[0].endswith("```")


def test_frontmatter_template_missing_values_resolve_to_empty_strings():
    rendered = render_frontmatter_template(
        "[{db.project_id}|{fm.status}|{sys.file_name}|{fm.missing}|{unknown.value}]",
        frontmatter={"status": "active"},
        system={"file_name": "note.md"},
        database={"project_id": "p1"},
    )

    assert rendered == "[p1|active|note.md||]"