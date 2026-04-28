Extractor Porting Guide

Purpose

This document lists the exact files and Python dependencies you need to copy
from this repository to another project in order to use the task extractor
(`extract_tasks_from_markdown`). It also explains minimal integration steps,
import fixes, and optional config.

Files to copy (exact paths)

- `markdown_stuff/extractor.py`
- `markdown_stuff/models.py`
- `markdown_stuff/parser.py`  # for `parse_front_matter`
- `config/default_config.json` (optional but recommended)

Notes:
- If you do not want to copy `parser.py`, provide an alternative
  `parse_front_matter(markdown_text) -> (meta_data: dict, body: str)`
  implementation (see "Minimal alternative" below).
- Keep file names or update imports in `extractor.py` accordingly.

Runtime dependencies (from pyproject.toml)

- Python: `>=3.11,<4.0`
- `marko` (GFM AST parsing): `>=2.2.2,<3.0.0`
- `pytimeparse` (time attribute parsing): `>=1.1.8,<2.0.0`
- `python-frontmatter` (front-matter parsing): `>=1.1.0,<2.0.0`
- `pydantic` (models): `>=2.0.0,<3.0.0`

Install via pip (example):

```bash
python -m pip install "marko>=2.2.2,<3.0.0" "pytimeparse>=1.1.8,<2.0.0" "python-frontmatter>=1.1.0,<2.0.0" "pydantic>=2.0.0,<3.0.0"
```

Or add the above entries to your `pyproject.toml` / dependency manager of choice.

Why these are needed

- `marko` + `marko.ext.gfm` — extractor walks a `marko` AST and relies on the
  GFM `Paragraph.checked` attribute (the checked state lives on the
  `marko.ext.gfm.elements.Paragraph` node). Use the GFM extension.
- `python-frontmatter` — `parser.parse_front_matter` uses `frontmatter.loads` to
  split YAML/TOML front-matter from the document. If you don't copy
  `parser.py`, supply a function with the same signature.
- `pydantic` — `ParsedTask` and `ParsedDocument` are `pydantic.BaseModel`
  classes used by `extract_tasks_from_markdown` as the return types.

Integration steps (minimal)

1. Copy the listed files into your project. Maintain the module path or
   update imports in `extractor.py`:

   - Current imports in `extractor.py`:
     ```py
     from markdown_stuff.models import ParsedDocument, ParsedTask
     from markdown_stuff.parser import parse_front_matter
     ```
   - If you put the files under a package named `myproject.tasks`, change
     the imports to:
     ```py
     from myproject.tasks.models import ParsedDocument, ParsedTask
     from myproject.tasks.parser import parse_front_matter
     ```

2. Copy `config/default_config.json` or create your own config `dict`
   following the same shape. Minimal required keys used by the extractor:

   - `headers.header_text_maxlen`: int
   - `tasks.task_text_maxlen`: int
   - `tasks.attributes`: mapping of attribute name -> type definition

   Example attribute definitions:

   ```json
   {
     "due_date": {"type": "date", "alias": "📅"},
     "priority": {
       "type": "domain",
       "values": {
         "high": {"alias": "⏫"}
       }
     },
     "estimate": {"type": "time"}
   }
   ```

3. Install the runtime dependencies listed above.

4. Usage (example):

```py
import json
from pathlib import Path
from your_package.extractor import extract_tasks_from_markdown

config = json.loads(Path("config/default_config.json").read_text(encoding="utf-8"))
md = Path("notes.md").read_text(encoding="utf-8")

parsed_doc = extract_tasks_from_markdown(md, config)
for t in parsed_doc.tasks:
    print(t.task_id, t.checked, t.task_text, t.attributes)
```

If you want file-path-sensitive `task_id` values that match this repository's
CLI behavior, pass the relative path used to locate the file:

```py
parsed_doc = extract_tasks_from_markdown(md, config, file_path="notes.md")
```

Known curly-brace attributes are parsed according to the config. `time`
attributes use `pytimeparse.parse`, so `{ estimate: 2h }` becomes `7200`.

Minimal alternative `parse_front_matter`

If you don't want `parser.py`'s other utilities, you can provide this
standalone implementation (requires `python-frontmatter`):

```py
import frontmatter

def parse_front_matter(markdown_text: str):
    post = frontmatter.loads(markdown_text)
    return dict(post.metadata), post.content
```

Import path and module resolution notes

- `extractor.py` imports `ParsedTask` / `ParsedDocument` and
  `parse_front_matter` using absolute imports. If you relocate files,
  update those imports to match your package layout (or make them relative
  if you keep files in the same package).
- The extractor assumes `marko`'s GFM extension is available. Do not remove
  `GFM` or the extractor will not observe `checked` on list items.

Optional: Using plain dicts instead of `pydantic`

If you prefer not to depend on `pydantic`, you can modify `extractor.py` to
return plain dictionaries instead of `ParsedTask`/`ParsedDocument` instances.
The core extraction logic is in `walk_ast`, `_process_list_item`, and
`extract_attributes`, so you could adapt the final assembly step to build
`dict` objects.

Testing

- After copying, run a simple smoke test (Python REPL):

```bash
python - <<'PY'
from pathlib import Path
import json
from your_package.extractor import extract_tasks_from_markdown

config = json.loads(Path("config/default_config.json").read_text())
md = Path("test-data/document-1.md").read_text(encoding="utf-8")
print(len(extract_tasks_from_markdown(md, config).tasks))
PY
```

Troubleshooting

- If tasks are not detected as checked/unchecked, verify `marko` + `GFM`
  are installed and that `extractor.py` constructs the parser with
  `Markdown(extensions=[GFM])` (this is done inside `extract_tasks_from_markdown`).
- If attributes are missing, confirm the alias keys in your `aliases` mapping
  exactly match the emoji or strings used in your Markdown.

This extractor originates from the `markdown-stuff` project.
