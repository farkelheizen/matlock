from __future__ import annotations

from typing import Any

import frontmatter


def parse_front_matter(markdown_text: str) -> tuple[dict[str, Any], str]:
    """Parse YAML/TOML front-matter and return (metadata, content).

    Returns the parsed front-matter mapping and the remaining Markdown body.
    """
    post = frontmatter.loads(markdown_text)
    return dict(post.metadata), post.content