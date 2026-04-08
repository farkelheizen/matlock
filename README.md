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

## Extract Tasks

The task extractor parses GFM-style checkbox lists from a Markdown file, tracking
nesting, section headers, inline emoji aliases, and curly-brace attributes.

### CLI

```bash
poetry run python scripts/extract_tasks.py test-data/document-1.md
```

Use a custom config file:

```bash
poetry run python scripts/extract_tasks.py my-notes.md --config config/default_config.json
```

Output is printed to stdout as formatted JSON.

### Python API

```python
import json
from pathlib import Path
from markdown_stuff import extract_tasks_from_markdown

config = json.loads(Path("config/default_config.json").read_text(encoding="utf-8"))
md = Path("test-data/document-1.md").read_text(encoding="utf-8")

doc = extract_tasks_from_markdown(md, config)

print(doc.meta_data)       # front-matter fields (if any)
for task in doc.tasks:
    print(task.task_id, task.checked, task.task_text)
    print("  headers:", task.headers)
    print("  attributes:", task.attributes)
```

Each task is a `ParsedTask` with these fields:

| Field | Type | Description |
|---|---|---|
| `task_id` | `str` | SHA-256 derived from text, headers, and position |
| `checked` | `bool` | Whether the checkbox is checked |
| `task_text` | `str` | Cleaned task text (aliases and curly-brace blocks removed) |
| `overflow` | `bool` | `True` if `task_text` was truncated |
| `headers` | `list[str]` | Ancestor section headers at parse time |
| `attributes` | `dict` | Extracted alias and curly-brace attributes |
| `errors` | `list[str]` | Any parse warnings for this task |
| `parent_task_id` | `str \| None` | `task_id` of the parent list item, or `None` |
| `twin_index` | `int` | Disambiguator for otherwise-identical tasks |

### Config

The config file (`config/default_config.json`) controls text length limits and
which emoji aliases map to which attributes:

```json
{
  "task_text_maxlen": 200,
  "header_text_maxlen": 50,
  "aliases": {
    "📅": {"attribute": "due_date", "type": "date"},
    "⏳": {"attribute": "scheduled_date", "type": "date"},
    "🆔": {"attribute": "id", "type": "string"},
    "🏁": {"attribute": "on_completion", "type": "domain", "allowed": ["keep", "delete"]},
    "⏫": {"attribute": "priority", "type": "literal", "value": "High"},
    "🔼": {"attribute": "priority", "type": "literal", "value": "Medium"},
    "🔽": {"attribute": "priority", "type": "literal", "value": "Low"}
  }
}
```

Curly-brace pairs on the same line are also extracted as free-form attributes:

```markdown
- [ ] Review PR { project: Alpha } { reviewer: alice }
```

## Project Layout

```text
config/
    default_config.json
markdown_stuff/
    __init__.py
    extractor.py
    models.py
    parser.py
scripts/
    extract_tasks.py
    parse_with_markdownit.py
    parse_with_marko.py
    parse_with_frontmatter.py
test-data/
    document-1.md
    document-2.md
tests/
```

## Generating Parsed Documents

```shell
poetry run python scripts/parse_with_markdownit.py test-data/document-1.md > test-data/parsed/markdownit-document-1.txt
poetry run python scripts/parse_with_marko.py test-data/document-1.md > test-data/parsed/marko-document-1.txt
poetry run python scripts/parse_with_markdownit.py test-data/document-2.md > test-data/parsed/markdownit-document-2.txt
poetry run python scripts/parse_with_marko.py test-data/document-2.md > test-data/parsed/marko-document-2.txt
```