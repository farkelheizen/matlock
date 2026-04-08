# markdown-stuff

Small utilities for parsing Markdown into DOM-like structures in Python.

The project includes two parsing backends:

- `markdown-it-py` for token-based parsing
- `marko` for AST-style parsing

## Requirements

- Python 3.11+
- Poetry 2.x

## Install

```bash
poetry install
```

## Run The CLI Test Scripts

Print the full `markdown-it-py` token tree:

```bash
poetry run python scripts/parse_with_markdownit.py
```

Print the full `marko` AST tree:

```bash
poetry run python scripts/parse_with_marko.py
```

Use a specific file with either script:

```bash
poetry run python scripts/parse_with_markdownit.py test-data/document-1.md
poetry run python scripts/parse_with_marko.py test-data/document-1.md
```

Each script prints the complete parser output to the CLI in formatted JSON.

Print front-matter metadata and body:

```bash
poetry run python scripts/parse_with_frontmatter.py
```

Or for a specific file:

```bash
poetry run python scripts/parse_with_frontmatter.py test-data/document-1.md
```

## Usage

Parse a Markdown string into tokens:

```python
from markdown_stuff import parse_tokens

tokens = parse_tokens("# Title\n\nA short paragraph.")

for token in tokens:
    print(token.type, token.tag)
```

Parse a Markdown string into an AST-like dictionary:

```python
from markdown_stuff import parse_ast

document = parse_ast("# Title\n\nA short paragraph.")
print(document["children"][0]["element"])
```

Parse one of the sample files in `test-data`:

```python
from pathlib import Path

from markdown_stuff import parse_ast, parse_tokens

markdown_text = Path("test-data/document-1.md").read_text(encoding="utf-8")

ast = parse_ast(markdown_text)
tokens = parse_tokens(markdown_text)

print(ast["element"])
print(len(tokens))
```

## Project Layout

```text
markdown_stuff/
    __init__.py
    parser.py
scripts/
    parse_with_markdownit.py
    parse_with_marko.py
test-data/
    document-1.md
```