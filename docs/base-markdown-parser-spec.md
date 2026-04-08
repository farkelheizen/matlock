# Markdown Task Extractor: System Design & Implementation Guide

## Overview

We need to build a Python module that parses Markdown documents to extract a stateful, hierarchical list of tasks along with the document's metadata. The parser will use `markdown_stuff` to extract YAML frontmatter, `marko` (with the `gfm`extension) to traverse the document's Abstract Syntax Tree (AST), `pydantic` for strict data modeling, and data-driven Regex to extract inline task attributes based on a configuration file.

## 1. Technical Stack

- **Frontmatter Parser:** `markdown_stuff` (specifically `parse_front_matter`).
    
- **Markdown Parser:** `marko` (must use `marko.ext.gfm.gfm` for checkbox support).
    
- **Data Modeling:** `pydantic` (BaseModel, field_validators).
    
- **Hashing:** `hashlib` (SHA-256).
    
- **Config Loader:** `json` or `yaml` (Standard library / PyYAML).
    

## 2. Configuration Schema (Attribute Aliases)

Attributes are data-driven via a configuration dictionary (loaded from a JSON/YAML file). The system must dynamically build an extraction engine based on this file.

**Structure:**

- **`Alias`**: The literal string/emoji to search for.
    
- **`Attribute`**: The dictionary key to store the result in.
    
- **`Type`**: How to parse the value.
    
    - `string`, `date`, `domain` expect a value to follow the alias (e.g., `📅 2026-10-25`).
        
    - `literal` means the presence of the alias itself defines the value, and it consumes no subsequent text (e.g., `⏫`automatically sets `priority: "High"`).
        

**Example Config Structure:**

```
{
  "task_text_maxlen": 200,
  "header_text_maxlen": 50,
  "aliases": {
    "🆔": {"attribute": "id", "type": "string"},
    "📅": {"attribute": "due_date", "type": "date"},
    "🏁": {"attribute": "on_completion", "type": "domain", "allowed": ["keep", "delete"]},
    "⏫": {"attribute": "priority", "type": "literal", "value": "High"},
    "🔼": {"attribute": "priority", "type": "literal", "value": "Medium"}
  }
}
```

### Curly-Brace Inline Attributes

In addition to emoji-alias attributes, tasks may contain curly-brace inline attributes using the pattern `{ name: value }`. Spaces are permitted before/after `name`, `:`, and `}`. All curly-brace attribute values are strings. Multiple curly-brace pairs may appear on a single line, but multiline curly-brace attributes are NOT supported. These are extracted alongside alias attributes and merged into the task's `attributes` dict.

Example: `- [ ] Check repos { project: Project 2 }` produces `{"project": "Project 2"}`.

## 3. Data Models (`pydantic`)

### ParsedTask Model

Implement a `ParsedTask` Pydantic model with the following strict requirements:

- **`checked`** (`bool`): True if `[x]`, False if `[ ]`.

- **`task_text`** (`str`): The raw text of the task, MINUS the extracted attributes. Built by concatenating the text content of child nodes. Must be `.strip()`ped.

- **`overflow`** (`bool`): Defaults to `False`. Set to `True` by the extractor if `task_text` was truncated.

- **`headers`** (`List[str]`): Contextual headers above this task.

- **`attributes`** (`dict[str, Any]`): Extracted from the config-driven alias parser and curly-brace inline attributes.

- **`errors`** (`List[str]`): Defaults to `[]`. Collects non-fatal extraction errors (e.g., domain validation failures).

- **`parent_task_id`** (`str | None`): The `task_id` of the parent task, if nested.

- **`twin_index`** (`int`): 0 by default. If multiple tasks with the EXACT SAME `task_text` exist under the exact same `parent_task_id` and `headers`, increment this (1, 2, 3...).

- **`task_id`** (`str`): A generated property.

**Truncation & ID Generation (Performed by the Extractor)**

Truncation is handled by the extraction engine (`extractor.py`), NOT by Pydantic validators. The extractor applies these rules before constructing the model:

1. **Header Truncation:** For each string in `headers`, if `len(header) > header_text_maxlen`, truncate to `header_text_maxlen` and append `"..."`.

2. **Text Truncation:** If `len(task_text) > task_text_maxlen`, truncate it, append `"..."`, and set `overflow = True`.

3. **ID Generation:** After truncation, compute `task_id` as the SHA256 hex digest of a strictly ordered, minified JSON string of: `{"task_text": text, "overflow": bool, "headers": list, "parent_task_id": id, "twin_index": int}`.

### ParsedDocument Model

Implement a `ParsedDocument` Pydantic model (or dataclass) to encapsulate the final result of a processed Markdown file:

- **`meta_data`** (`dict`): The parsed YAML frontmatter properties.

- **`tasks`** (`List[ParsedTask]`): The extracted list of task objects.

## 4. Extraction Engine Logic

### A. Frontmatter & Body Separation

Before building the AST, split the raw Markdown document:

1. Call `markdown_stuff.parse_front_matter(md_string)`.

2. Store the returned metadata dict.

3. Pass the remaining markdown body text to `marko` for parsing via `marko.parse()` directly (with the GFM extension) to obtain raw node objects — NOT the existing `parse_ast()` helper which returns a rendered dict.

### B. The Attribute Parser

Write a function `extract_attributes(raw_text: str, config: dict) -> Tuple[str, dict, List[str]]`.

This function returns the cleaned text, the extracted attributes dict, and a list of error strings.

**Alias Attributes (left-to-right positional matching):**

1. Scan the string left-to-right for all known aliases by position.

2. For `literal` types: Check if the alias exists in the string. If so, add to the `attributes` dict and `.replace()` the alias with `""` in the string.

3. For `value` types (`string`, `date`, `domain`): Use Regex to find the alias and capture the text following it up until the next known alias or the end of the string. Extract the value, add to the dict, and remove that segment from the raw string.

4. For `date` types: Only accept the format `YYYY-MM-DD`. If the value does not match, log an error and set the attribute to `None`.

5. For `domain` types: If the extracted value is not in the `allowed` list, log the error (e.g., `"Invalid value 'xyz' for domain attribute 'on_completion'; allowed: ['keep', 'delete']"`) and set the attribute to `None`.

6. **Duplicate attributes:** If an attribute key has already been set (whether by a previous alias or curly-brace), log an error (e.g., `"Duplicate attribute 'priority' encountered"`) and keep the first value.

**Curly-Brace Inline Attributes:**

7. After alias extraction, scan the remaining text for `{ name: value }` patterns using Regex. Spaces are permitted before/after `name`, `:`, and `}`. All values are strings. Multiple pairs may appear per line; multiline is NOT supported. Extract these into the `attributes` dict and remove the matched segments from the raw string. If a curly-brace key duplicates an existing attribute, log an error and keep the first value.

8. Return the cleaned, trimmed text, the populated attributes dict, and the errors list.

### C. The AST Walker (`marko`)

Write a recursive function `walk_ast(node, current_headers, parent_task_id)` to traverse the `marko` AST (which is generated from the markdown body without frontmatter).

**State Management Requirements:**

1. **Headers:** Maintain an array of size 6 (representing H1-H6).

    - When encountering a `marko.block.Heading` with level $L$, update index $L-1$ with the header's text.

    - CRITICAL: Clear all indexes $> L-1$. (e.g., encountering an H2 clears any existing H3, H4, H5, H6 from state).

    - Pass a compacted version of this list (ignoring empty slots) down the recursion tree.

2. **Twin Tracking:** Maintain a dictionary tracking occurrences to calculate `twin_index`. Key format: `hash((tuple(headers), parent_task_id, cleaned_task_text))`. Increment the integer value for every match.

**Task Detection:**

- When encountering a `marko.block.List`, check its children (`marko.block.ListItem`).

- In Marko's GFM extension, the `checked` attribute lives on the **`Paragraph`** child of a `ListItem` (not on `ListItem` itself). The GFM `Paragraph` sets `checked` to `True`, `False`, or `None`. **Skip list items whose `Paragraph` has `checked is None`** (non-task items).

- Extract the text of the `ListItem` by concatenating the text content of child nodes under the same list item, EXCEPT child `List` nodes (which represent sub-tasks). Multiline continuation text within the same `Paragraph` is included.

- Pass the raw text to the Attribute Parser.

- Apply truncation: if `len(task_text) > task_text_maxlen`, truncate and append `"..."`, setting `overflow = True`. Similarly truncate headers exceeding `header_text_maxlen`.

- Calculate the `twin_index`.

- Compute `task_id` (SHA256 hash).

- Instantiate the `ParsedTask` Pydantic model.

- If the `ListItem` contains another `List` node as a child, recurse into it, passing the newly generated `task_id` down as the `parent_task_id`.

## 5. Expected Output Formats

The primary entry point `extract_tasks_from_markdown(md_string: str, config_dict: dict) -> ParsedDocument` should return a `ParsedDocument` object containing the parsed `meta_data` dictionary and a flat list of `ParsedTask` objects representing the entire document, correctly preserving hierarchical relationships via `parent_task_id`.

A default configuration file will be bundled at `config/default_config.json`. A test script at `scripts/extract_tasks.py` will exercise the extractor against a given Markdown file.

### Module Layout

```
markdown_stuff/
    models.py          # ParsedTask, ParsedDocument (Pydantic models)
    extractor.py       # extract_attributes, walk_ast, extract_tasks_from_markdown
config/
    default_config.json
scripts/
    extract_tasks.py
```

## 6. Open Questions / Concerns (Resolved)

All open questions have been addressed. Decisions are recorded below for reference.

| # | Topic | Decision |
|---|-------|----------|
| 6.1 | Marko node API vs. rendered dict | Use `marko.parse()` directly for raw node objects. Bypass existing `parse_ast()`. |
| 6.2 | Curly-brace syntax | Parse as a separate inline-attribute mechanism (`{ name: value }`). See Section 2 & 4B. |
| 6.3 | Non-task list items | Skip list items where `checked is None`. |
| 6.4 | Inline formatting | Build `task_text` from text content of child nodes. No special formatting preservation needed. |
| 6.5 | Multiline continuation text | Concatenate all text from children under the same list item, excluding sub-task lists. |
| 6.13 | Duplicate attributes | Log as error, keep the first value. |
| 6.14 | `checked` on Paragraph | In marko 2.2.2+ GFM, `checked` is on the `Paragraph` child, not `ListItem`. marko >= 2.2.2 confirmed. |
| 6.6 | Config delivery | Caller loads config. A default config is bundled at `config/default_config.json`. A test script (`scripts/extract_tasks.py`) is provided. |
| 6.7 | Config limits / Pydantic | Renamed to `task_text_maxlen` and `header_text_maxlen`. Truncation is performed by the extractor, NOT by Pydantic validators. |
| 6.8 | Domain validation | Log an error to `errors`, set attribute to `None`. |
| 6.9 | Pydantic dependency | Add `pydantic` v2.x to `pyproject.toml`. `errors` field (`List[str]`) added to `ParsedTask`. |
| 6.10 | Module layout | `models.py`, `extractor.py`, `config/default_config.json`, `scripts/extract_tasks.py`. |
| 6.11 | Date parsing | Accept `YYYY-MM-DD` only. Invalid dates log an error and set attribute to `None`. |
| 6.12 | Extraction ordering | Left-to-right positional matching based on alias position in the string. |

