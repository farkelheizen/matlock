# markdown-stuff

Focused utilities for extracting tasks from Markdown checkbox lists.

## Requirements

- Python 3.11+
- Poetry 2.x

## Install

```bash
poetry install
```

## Extract Tasks

The task extractor parses GFM-style checkbox lists from a Markdown file, tracking
nesting, section headers, inline emoji aliases, and curly-brace attributes.

### CLI

```bash
poetry run python scripts/parse_markdown_file.py --base-path . test-data/document-1.md
```

Use a custom config file:

```bash
poetry run python scripts/parse_markdown_file.py --base-path . test-data/document-1.md --config config/default_config.json
```

Output is printed to stdout as formatted JSON.

The output includes top-level file metadata:

- `file_path`: the CLI path relative to `--base-path`
- `created`: creation time in milliseconds since epoch
- `modified`: modification time in milliseconds since epoch
- `length`: file size in bytes
- `sha256`: SHA-256 hex digest of the source file

### Python API

```python
import json
from pathlib import Path
from markdown_stuff import extract_tasks_from_markdown

config = json.loads(Path("config/default_config.json").read_text(encoding="utf-8"))
md = Path("test-data/document-1.md").read_text(encoding="utf-8")

doc = extract_tasks_from_markdown(md, config, file_path="test-data/document-1.md")

print(doc.meta_data)
for task in doc.tasks:
  print(task.task_id, task.checked, task.task_text)
  print("  headers:", task.headers)
  print("  attributes:", task.attributes)
```

Each task is a `ParsedTask` with these fields:

| Field | Type | Description |
|---|---|---|
| `task_id` | `str` | SHA-256 derived from file path, text, headers, and position |
| `checked` | `bool` | Whether the checkbox is checked |
| `task_text` | `str` | Cleaned task text (aliases and curly-brace blocks removed) |
| `overflow` | `bool` | `True` if `task_text` was truncated |
| `headers` | `list[str]` | Ancestor section headers at parse time |
| `attributes` | `dict` | Extracted alias and curly-brace attributes |
| `errors` | `list[str]` | Any parse warnings for this task |
| `parent_task_id` | `str \| None` | `task_id` of the parent list item, or `None` |
| `twin_index` | `int` | Disambiguator for otherwise-identical tasks |

### Config

The config file (`config/default_config.json`) separates header settings from
task settings and defines known task attributes:

```json
{
  "headers": {
    "header_text_maxlen": 200
  },
  "tasks": {
    "task_text_maxlen": 200,
    "attributes": {
      "due_date": { "type": "date", "alias": "📅" },
      "complete_date": { "type": "date", "alias": "✅" },
      "priority": {
        "type": "domain",
        "values": {
          "low": { "alias": "🔽" },
          "medium": { "alias": "🔼" },
          "high": { "alias": "⏫" }
        }
      },
      "estimate": { "type": "time" },
      "actual": { "type": "time" }
    }
  }
}
```

Curly-brace pairs on the same line are also extracted as free-form attributes:

```markdown
- [ ] Review PR { project: Alpha } { reviewer: alice } { estimate: 2h }
```

Known attributes are parsed according to their configured type. `time`
attributes use `pytimeparse.parse`, so values like `{ estimate: 2h }` and
`{ actual: 50m }` are converted to seconds.

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
  parse_markdown_file.py
test-data/
    document-1.md
    document-2.md
tests/
```