# Markdown Task Extractor: Implementation Plan

> **Reference:** [base-markdown-parser-spec.md](base-markdown-parser-spec.md)
>
> **Branch name:** `feature/task-extractor`
>
> **Instruction:** Implement each step in order. After each step, run `poetry run pytest tests/ -v` and only proceed to the next step if all tests pass. After all steps are complete, commit the work on the branch named above.

---

## Step 0 — Project Setup

### 0a. Add dependencies to `pyproject.toml`

Add `pydantic` and `pytest` to the project. Do NOT modify existing dependencies.

- Add `pydantic (>=2.0.0,<3.0.0)` to `dependencies`.
- Add a `[tool.poetry.group.dev.dependencies]` section (or `[project.optional-dependencies]` dev group) with `pytest (>=8.0.0,<9.0.0)`.

Run:

```bash
poetry lock --no-update
poetry install
```

### 0b. Create the test directory and empty files

Create the following empty files (the contents will be filled in subsequent steps):

```
tests/__init__.py          # empty
tests/conftest.py          # empty for now
markdown_stuff/models.py   # empty
markdown_stuff/extractor.py # empty
config/default_config.json  # empty JSON object {} for now
scripts/extract_tasks.py    # empty
```

### 0c. Verify pytest runs

Run `poetry run pytest tests/ -v`. It should discover zero tests and exit cleanly (exit code 0 or 5 for "no tests collected"). This confirms the toolchain works.

---

## Step 1 — Pydantic Models (`markdown_stuff/models.py`)

### 1a. Implement `ParsedTask`

Create the `ParsedTask` Pydantic `BaseModel` in `markdown_stuff/models.py` with the following fields:

| Field | Type | Default |
|-------|------|---------|
| `checked` | `bool` | *required* |
| `task_text` | `str` | *required* |
| `overflow` | `bool` | `False` |
| `headers` | `list[str]` | *required* |
| `attributes` | `dict[str, Any]` | *required* |
| `errors` | `list[str]` | `[]` (default_factory) |
| `parent_task_id` | `str \| None` | *required* |
| `twin_index` | `int` | `0` |
| `task_id` | `str` | *required* |

No custom validators are needed — all truncation and ID generation happens in the extractor before the model is instantiated.

### 1b. Implement `ParsedDocument`

Create the `ParsedDocument` Pydantic `BaseModel`:

| Field | Type | Default |
|-------|------|---------|
| `meta_data` | `dict[str, Any]` | *required* |
| `tasks` | `list[ParsedTask]` | *required* |

### 1c. Write tests for models (`tests/test_models.py`)

Write tests that:

1. Construct a valid `ParsedTask` with all required fields and verify field values.
2. Verify `errors` defaults to `[]` when omitted.
3. Verify `overflow` defaults to `False` when omitted.
4. Verify `twin_index` defaults to `0` when omitted.
5. Construct a `ParsedDocument` with `meta_data` and a list of `ParsedTask` objects.
6. Verify Pydantic raises `ValidationError` if required fields are missing (e.g., no `task_text`).

**Run tests. All must pass before proceeding.**

---

## Step 2 — Default Config (`config/default_config.json`)

### 2a. Create the default configuration file

Write `config/default_config.json` with the following content:

```json
{
  "task_text_maxlen": 200,
  "header_text_maxlen": 50,
  "aliases": {
    "🆔": {"attribute": "id", "type": "string"},
    "📅": {"attribute": "due_date", "type": "date"},
    "⏳": {"attribute": "scheduled_date", "type": "date"},
    "🏁": {"attribute": "on_completion", "type": "domain", "allowed": ["keep", "delete"]},
    "⏫": {"attribute": "priority", "type": "literal", "value": "High"},
    "🔼": {"attribute": "priority", "type": "literal", "value": "Medium"},
    "🔽": {"attribute": "priority", "type": "literal", "value": "Low"}
  }
}
```

### 2b. Write a config-loading test (`tests/test_config.py`)

Write a test that:

1. Loads `config/default_config.json` with `json.load`.
2. Asserts `task_text_maxlen` and `header_text_maxlen` are integers.
3. Asserts `aliases` is a dict and contains at least the `📅` key.
4. For each alias entry, asserts it has `attribute` and `type` keys.
5. For `literal` type entries, asserts a `value` key exists.
6. For `domain` type entries, asserts an `allowed` key exists and is a list.

**Run tests. All must pass before proceeding.**

---

## Step 3 — Attribute Parser (`markdown_stuff/extractor.py`, part 1)

### 3a. Implement `extract_attributes`

In `markdown_stuff/extractor.py`, implement:

```python
def extract_attributes(raw_text: str, aliases: dict) -> tuple[str, dict[str, Any], list[str]]:
```

**Parameters:**

- `raw_text`: The raw task text string (already extracted from the AST node).
- `aliases`: The `aliases` dict from the config (e.g., `config["aliases"]`).

**Returns:** `(cleaned_text, attributes, errors)`

**Algorithm:**

1. **Find all alias positions.** For each alias key in `aliases`, find its position in `raw_text` using `str.find()`. Collect all found aliases into a list of `(position, alias_key, alias_config)` tuples. Sort by position ascending (left-to-right).

2. **Process sorted aliases.** Iterate through the sorted list:
   - **`literal`**: Record `attributes[config["attribute"]] = config["value"]`. Remove the alias string from `raw_text`. Check for duplicate attribute keys first.
   - **`string` / `date` / `domain`**: The value is the text between this alias and the next alias (or end of string). Use a regex or slice to capture it. `.strip()` the value.
     - For `date`: Validate against `^\d{4}-\d{2}-\d{2}$`. If invalid, log error, set `None`.
     - For `domain`: Check if value is in `config["allowed"]`. If not, log error, set `None`.
   - **Duplicate check**: If `config["attribute"]` is already present in `attributes`, log an error and skip (keep the first value).
   - Remove the alias + value segment from `raw_text`.

3. **Curly-brace attributes.** After all alias processing, apply regex: `\{\s*([^:}]+?)\s*:\s*([^}]*?)\s*\}` (or similar) to find `{ name: value }` pairs. For each match:
   - `.strip()` both name and value.
   - If name already exists in `attributes`, log error and skip.
   - Otherwise, add to `attributes`.
   - Remove the matched segment from `raw_text`.

4. Return `(raw_text.strip(), attributes, errors)`.

### 3b. Write tests (`tests/test_extract_attributes.py`)

Use the default config's `aliases` dict for tests. Write tests covering:

1. **Simple literal extraction:** Input `"Do the thing ⏫"` → text `"Do the thing"`, attributes `{"priority": "High"}`, no errors.
2. **Date extraction:** Input `"Task 📅 2026-04-10"` → `{"due_date": "2026-04-10"}`, text `"Task"`.
3. **Invalid date:** Input `"Task 📅 not-a-date"` → `{"due_date": None}`, one error logged.
4. **Domain extraction (valid):** Input `"Task 🏁 delete"` → `{"on_completion": "delete"}`.
5. **Domain extraction (invalid):** Input `"Task 🏁 archive"` → `{"on_completion": None}`, one error.
6. **String extraction:** Input `"Task 🆔 abc-123"` → `{"id": "abc-123"}`.
7. **Multiple aliases left-to-right:** Input `"Task 📅 2026-04-10 ⏫"` → `{"due_date": "2026-04-10", "priority": "High"}`, text `"Task"`.
8. **Curly-brace extraction:** Input `"Check repos { project: Project 2 }"` → attribute `{"project": "Project 2"}`, text `"Check repos"`.
9. **Multiple curly-brace pairs:** Input `"Task { a: 1 } { b: 2 }"` → `{"a": "1", "b": "2"}`.
10. **Duplicate attribute (alias vs alias):** Two aliases mapping to the same attribute key → error logged, first value kept.
11. **Duplicate attribute (alias vs curly-brace):** Alias sets `"priority"`, then curly-brace also sets `"priority"` → error logged, first value kept.
12. **Mixed aliases and curly-braces:** Input `"Do stuff 📅 2026-04-10 { project: Foo } ⏫"` → all three extracted correctly, text `"Do stuff"`.
13. **No attributes:** Input `"Just a plain task"` → empty attributes, no errors, text unchanged.
14. **Empty string:** Input `""` → empty text, empty attributes, no errors.

**Run tests. All must pass before proceeding.**

---

## Step 4 — Helper Functions (`markdown_stuff/extractor.py`, part 2)

### 4a. Implement `compute_task_id`

```python
def compute_task_id(task_text: str, overflow: bool, headers: list[str], parent_task_id: str | None, twin_index: int) -> str:
```

Build a strictly ordered, minified JSON string:

```python
json.dumps({"task_text": task_text, "overflow": overflow, "headers": headers, "parent_task_id": parent_task_id, "twin_index": twin_index}, separators=(',', ':'), sort_keys=False)
```

> **Key order must be exactly:** `task_text`, `overflow`, `headers`, `parent_task_id`, `twin_index`. Use `json.dumps` on a regular dict (Python 3.7+ preserves insertion order) — do NOT use `sort_keys=True`.

Return the SHA-256 hex digest of the UTF-8 encoded string.

### 4b. Implement `truncate_text`

```python
def truncate_text(text: str, maxlen: int) -> tuple[str, bool]:
```

If `len(text) > maxlen`, return `(text[:maxlen] + "...", True)`. Otherwise return `(text, False)`.

### 4c. Implement `truncate_headers`

```python
def truncate_headers(headers: list[str], maxlen: int) -> list[str]:
```

Return a new list where each header exceeding `maxlen` is truncated to `maxlen` characters with `"..."` appended.

### 4d. Write tests (`tests/test_helpers.py`)

1. **`compute_task_id`**: Given fixed inputs, verify the output is a 64-character hex string. Verify the same inputs always produce the same hash. Verify different inputs produce different hashes.
2. **`truncate_text`**: Text within limit → returns unchanged, `False`. Text exceeding limit → returns truncated + `"..."`, `True`. Text exactly at limit → returns unchanged, `False`.
3. **`truncate_headers`**: Mixed list of short and long headers → only long ones truncated.

**Run tests. All must pass before proceeding.**

---

## Step 5 — AST Walker (`markdown_stuff/extractor.py`, part 3)

### 5a. Implement text extraction from marko nodes

```python
def extract_text_from_node(node) -> str:
```

Recursively walk a marko node's children and concatenate all text content (from `RawText` nodes and similar leaf nodes). Skip any child that is a `marko.block.List` (sub-tasks). Return the concatenated string.

### 5b. Implement header text extraction

```python
def extract_heading_text(heading_node) -> str:
```

Given a `marko.block.Heading` node, extract and return its plain text content by walking its inline children.

### 5c. Implement `walk_ast`

Implement the recursive AST walker. It must accept and manage:

- `node`: The current marko AST node.
- `header_state`: A mutable list of 6 elements (H1–H6), initially all `""`.
- `parent_task_id`: `str | None`.
- `config`: The full config dict.
- `tasks`: A mutable list to accumulate `ParsedTask` objects (flat output).
- `twin_tracker`: A mutable `dict` for twin counting.

**Logic:**

1. If node is `marko.block.Document`, iterate its children recursively.
2. If node is `marko.block.Heading`:
   - Extract text via `extract_heading_text`.
   - Set `header_state[level - 1]` = text.
   - Clear `header_state[level:]` (set to `""`).
3. If node is `marko.block.List`, iterate its `ListItem` children:
   - Find the first `Paragraph` child. Check `getattr(paragraph, 'checked', None)`.
   - If `checked is None`, skip this item (not a task). But still recurse into any child `List` nodes with `parent_task_id=parent_task_id` (in case there are nested tasks under a non-task item).
   - If `checked` is `True` or `False`:
     - Extract text from the `ListItem`'s non-List children via `extract_text_from_node`.
     - Call `extract_attributes(raw_text, config["aliases"])` → `(task_text, attributes, errors)`.
     - Compact headers: `[h for h in header_state if h]`.
     - Apply `truncate_text(task_text, config["task_text_maxlen"])`.
     - Apply `truncate_headers(headers, config["header_text_maxlen"])`.
     - Compute `twin_index` using `twin_tracker` with key `(tuple(headers), parent_task_id, task_text)`.
     - Compute `task_id` via `compute_task_id`.
     - Instantiate `ParsedTask` and append to `tasks`.
     - For any child `List` node inside this `ListItem`, recurse with `parent_task_id=task_id`.
4. For any other block node type (e.g., `Quote`, `Paragraph` at document level), recurse into its children if it has any, to handle nested structures.

### 5d. Write tests (`tests/test_walk_ast.py`)

Use `marko` with GFM to parse Markdown strings into AST nodes, then call the walker.

1. **Single flat task:** `"- [ ] A simple task\n"` → one `ParsedTask`, `checked=False`, `task_text="A simple task"`, empty headers, no parent.
2. **Checked task:** `"- [x] Done task\n"` → `checked=True`.
3. **Nested tasks:** Parse a string with parent and child tasks. Verify parent has `parent_task_id=None`, child has `parent_task_id` equal to parent's `task_id`.
4. **Headers:** Parse `"# H1\n\n- [ ] Task under H1\n\n## H2\n\n- [ ] Task under H2\n"` → first task has `headers=["H1"]`, second has `headers=["H1", "H2"]`.
5. **Header reset:** Parse `"## H2\n\n### H3\n\n## New H2\n\n- [ ] Task\n"` → task has `headers=["New H2"]` (H3 was cleared).
6. **Non-task list items skipped:** `"- plain item\n- [ ] real task\n"` → only one `ParsedTask`.
7. **Multiline continuation:** Parse a task with continuation text. Verify `task_text` includes the continuation.
8. **Twin tracking:** Two tasks with identical text under same headers and parent → `twin_index` 0 and 1 respectively.

**Run tests. All must pass before proceeding.**

---

## Step 6 — Top-Level Entry Point (`markdown_stuff/extractor.py`, part 4)

### 6a. Implement `extract_tasks_from_markdown`

```python
def extract_tasks_from_markdown(md_string: str, config_dict: dict) -> ParsedDocument:
```

1. Call `parse_front_matter(md_string)` → `(meta_data, body)`.
2. Create a marko `Markdown` instance with GFM extension. Call `.parse(body)` to get the AST root.
3. Initialize `header_state = [""] * 6`, `tasks = []`, `twin_tracker = {}`.
4. Call `walk_ast(root, header_state, None, config_dict, tasks, twin_tracker)`.
5. Return `ParsedDocument(meta_data=meta_data, tasks=tasks)`.

### 6b. Write integration tests (`tests/test_extract_tasks.py`)

Use the actual test-data Markdown files and the default config.

1. **document-1.md integration test:**
   - Load `test-data/document-1.md` and `config/default_config.json`.
   - Call `extract_tasks_from_markdown`.
   - Assert `meta_data["status"]` == `"In Progress"`.
   - Assert the correct number of tasks are extracted (8 tasks: "Check repos", "Check repo 1", "Check repo 2", "Commit repo 2", "Issue PR for repo 2", "Find classes to update", "Exclude classes that have no fields" — note: count carefully based on document structure).
   - Assert `"Check repos"` task has `attributes` containing `due_date` and `project`.
   - Assert `"Commit repo 2"` has `checked=True`.
   - Assert `"Check repo 1"` has `parent_task_id` equal to `"Check repos"` task's `task_id`.
   - Assert `"Exclude classes that have no fields"` task's `task_text` includes "Is this still cool?" (multiline continuation).
   - Assert tasks under `## Backlog` have `"Backlog"` in their `headers`.

2. **document-2.md integration test:**
   - Load and extract.
   - Assert exactly 1 task.
   - Assert `meta_data` is populated.
   - Assert the task has `due_date` and `project` attributes.

3. **Empty document:** `extract_tasks_from_markdown("---\ntitle: Test\n---\n\nNo tasks here.\n", config)` → 0 tasks, `meta_data["title"]` == `"Test"`.

4. **No frontmatter:** `extract_tasks_from_markdown("- [ ] A task\n", config)` → 1 task, empty `meta_data`.

**Run tests. All must pass before proceeding.**

---

## Step 7 — CLI Script (`scripts/extract_tasks.py`)

### 7a. Implement the script

Create `scripts/extract_tasks.py` with the following behavior:

- Accept a positional argument: path to a Markdown file (default: `test-data/document-1.md`).
- Accept an optional `--config` argument (default: `config/default_config.json`).
- Load the config JSON file.
- Read the Markdown file.
- Call `extract_tasks_from_markdown(md_string, config_dict)`.
- Print the result as formatted JSON using `ParsedDocument.model_dump()` with `json.dumps(indent=2)`.

### 7b. Write a smoke test (`tests/test_cli_script.py`)

Run the script via `subprocess.run` against `test-data/document-1.md`:

```python
result = subprocess.run(
    ["python", "scripts/extract_tasks.py", "test-data/document-1.md"],
    capture_output=True, text=True
)
```

1. Assert exit code 0.
2. Assert stdout is valid JSON.
3. Assert the parsed JSON has `meta_data` and `tasks` keys.

**Run tests. All must pass before proceeding.**

---

## Step 8 — Update `__init__.py` Exports

### 8a. Update `markdown_stuff/__init__.py`

Add the new public API to `__init__.py`:

```python
from .extractor import extract_tasks_from_markdown
from .models import ParsedTask, ParsedDocument
```

Add these to the `__all__` list.

### 8b. Write an import test (`tests/test_imports.py`)

```python
def test_public_api_imports():
    from markdown_stuff import extract_tasks_from_markdown, ParsedTask, ParsedDocument
    assert callable(extract_tasks_from_markdown)
```

**Run tests. All must pass before proceeding.**

---

## Step 9 — Final Validation & Commit

### 9a. Run full test suite

```bash
poetry run pytest tests/ -v
```

All tests must pass.

### 9b. Create branch and commit

```bash
git checkout -b feature/task-extractor
git add .
git commit -m "feat: implement markdown task extractor

- Add ParsedTask and ParsedDocument Pydantic models (models.py)
- Add attribute extraction engine with alias and curly-brace support (extractor.py)
- Add AST walker for marko GFM with header tracking and twin detection
- Add default config (config/default_config.json)
- Add CLI script (scripts/extract_tasks.py)
- Add pydantic dependency
- Add comprehensive test suite"
```

---

## File Summary

| File | Action | Step |
|------|--------|------|
| `pyproject.toml` | Edit (add pydantic, pytest) | 0a |
| `tests/__init__.py` | Create (empty) | 0b |
| `tests/conftest.py` | Create (empty or with fixtures) | 0b |
| `tests/test_models.py` | Create | 1c |
| `tests/test_config.py` | Create | 2b |
| `tests/test_extract_attributes.py` | Create | 3b |
| `tests/test_helpers.py` | Create | 4d |
| `tests/test_walk_ast.py` | Create | 5d |
| `tests/test_extract_tasks.py` | Create | 6b |
| `tests/test_cli_script.py` | Create | 7b |
| `tests/test_imports.py` | Create | 8b |
| `config/default_config.json` | Create | 2a |
| `markdown_stuff/models.py` | Create | 1a–1b |
| `markdown_stuff/extractor.py` | Create | 3a, 4a–4c, 5a–5c, 6a |
| `markdown_stuff/__init__.py` | Edit (add exports) | 8a |
| `scripts/extract_tasks.py` | Create | 7a |
