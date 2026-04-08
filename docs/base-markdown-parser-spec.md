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
  "n_task_limit": 200,
  "r_header_limit": 50,
  "aliases": {
    "🆔": {"attribute": "id", "type": "string"},
    "📅": {"attribute": "due_date", "type": "date"},
    "🏁": {"attribute": "on_completion", "type": "domain", "allowed": ["keep", "delete"]},
    "⏫": {"attribute": "priority", "type": "literal", "value": "High"},
    "🔼": {"attribute": "priority", "type": "literal", "value": "Medium"}
  }
}
```

## 3. Data Models (`pydantic`)

### ParsedTask Model

Implement a `ParsedTask` Pydantic model with the following strict requirements:

- **`checked`** (`bool`): True if `[x]`, False if `[ ]`.
    
- **`task_text`** (`str`): The raw text of the task, MINUS the extracted attributes. Must be `.strip()`ped.
    
- **`overflow`** (`bool`): Defaults to `False`.
    
- **`headers`** (`List[str]`): Contextual headers above this task.
    
- **`attributes`** (`dict[str, Any]`): Extracted from the config-driven parser.
    
- **`parent_task_id`** (`str | None`): The `task_id` of the parent task, if nested.
    
- **`twin_index`** (`int`): 0 by default. If multiple tasks with the EXACT SAME `task_text` exist under the exact same `parent_task_id` and `headers`, increment this (1, 2, 3...).
    
- **`task_id`** (`str`): A generated property.
    

**Pydantic Validation & Truncation Rules (Pre-computation)**

Use a `@model_validator(mode='before')` or `@field_validator` to enforce these rules upon instantiation:

1. **Header Truncation:** For each string in `headers`, if `len(header) > r_header_limit`, truncate to `r_header_limit` and append `"..."`.
    
2. **Text Truncation:** If `len(task_text) > n_task_limit`, truncate it, append `"..."`, and set `overflow = True`.
    
3. **ID Generation:** After truncation, compute `task_id` as the SHA256 hex digest of a strictly ordered, minified JSON string of: `{"task_text": text, "overflow": bool, "headers": list, "parent_task_id": id, "twin_index": int}`.
    

### ParsedDocument Model

Implement a `ParsedDocument` Pydantic model (or dataclass) to encapsulate the final result of a processed Markdown file:

- **`meta_data`** (`dict`): The parsed YAML frontmatter properties.
    
- **`tasks`** (`List[ParsedTask]`): The extracted list of task objects.
    

## 4. Extraction Engine Logic

### A. Frontmatter & Body Separation

Before building the AST, split the raw Markdown document:

1. Call `markdown_stuff.parse_front_matter(md_string)`.
    
2. Store the returned metadata dict.
    
3. Pass the remaining markdown body text to `marko` for parsing.
    

### B. The Attribute Parser

Write a function `extract_attributes(raw_text: str, config: dict) -> Tuple[str, dict]`.

1. Iterate through the configured aliases.
    
2. For `literal` types: Check if the alias exists in the string. If so, add to the `attributes` dict and `.replace()` the alias with `""` in the string.
    
3. For `value` types (`string`, `date`, `domain`): Use Regex to find the alias and capture the text following it up until the next known alias or the end of the string. Extract the value, add to the dict, and remove that segment from the raw string.
    
4. Return the cleaned, trimmed text and the populated attributes dict.
    

### C. The AST Walker (`marko`)

Write a recursive function `walk_ast(node, current_headers, parent_task_id)` to traverse the `marko` AST (which is generated from the markdown body without frontmatter).

**State Management Requirements:**

1. **Headers:** Maintain an array of size 6 (representing H1-H6).
    
    - When encountering a `marko.block.Heading` with level $L$, update index $L-1$ with the header's text.
        
    - CRITICAL: Clear all indexes $> L-1$. (e.g., encountering an H2 clears any existing H3, H4, H5, H6 from state).
        
    - Pass a compacted version of this list (ignoring empty slots) down the recursion tree.
        
2. **Twin Tracking:** Maintain a dictionary tracking occurrences to calculate `twin_index`. Key format: `hash((tuple(headers), parent_task_id, cleaned_task_text))`. Increment the integer value for every match.
    

**Task Detection:**

- When encountering a `marko.block.List`, check its children (`marko.block.ListItem`).
    
- In Marko's GFM extension, `ListItem` nodes have a `checked` boolean attribute.
    
- Extract the text of the `ListItem` (usually contained in a child `Paragraph` node).
    
- Pass the raw text to the Attribute Parser.
    
- Calculate the `twin_index`.
    
- Instantiate the `ParsedTask` Pydantic model.
    
- If the `ListItem` contains another `List` node as a child, recurse into it, passing the newly generated `task_id` down as the `parent_task_id`.
    

## 5. Expected Output Formats

The primary entry point `extract_tasks_from_markdown(md_string: str, config_dict: dict) -> ParsedDocument` should return a `ParsedDocument` object containing the parsed `meta_data` dictionary and a flat list of `ParsedTask` objects representing the entire document, correctly preserving hierarchical relationships via `parent_task_id`.
## 6. Open Questions / Concerns

### 6.1 Marko Node API vs. Rendered AST Dict

The existing `parse_ast()` function in [parser.py](../markdown_stuff/parser.py) uses `ASTRenderer`, which returns a JSON-serializable `dict`. However, the spec's AST walker (Section 4C) references raw marko node types like `marko.block.Heading`, `marko.block.List`, and `marko.block.ListItem` — which are the in-memory objects from `marko.parse()`, **not** the rendered dict output. The new implementation will need to call `marko.parse()` directly (with the GFM extension) to get the actual node tree. Just confirming: we should bypass the existing `parse_ast()` helper and work with raw marko nodes, correct?

### 6.2 Curly-Brace Syntax in Test Data

[document-1.md](../test-data/document-1.md) contains `{ project: Project 2 }` on a task line. This syntax is not covered by the alias-based attribute config schema in Section 2. Should the curly-brace portion be:
- (a) Treated as opaque task text (left in `task_text`),
- (b) Parsed as a separate inline-attribute mechanism beyond the alias system, or
- (c) Ignored / stripped?

### 6.3 Non-Task List Items

The spec assumes list items have checkboxes (`[x]` / `[ ]`). In marko GFM, `ListItem.checked` is `None` for regular (non-task) list items. Should the walker skip list items that lack a checkbox entirely, or should they be captured in some way?

### 6.4 Inline Formatting Within Task Text

Task text may contain inline markup (bold, italic, code spans, links, etc.). When extracting the raw text from a `ListItem`'s child `Paragraph`, should we:
- (a) Flatten all inline nodes to plain text (strip formatting), or
- (b) Preserve the original Markdown source text?

### 6.5 Multiline / Continuation Text in List Items

[document-1.md](../test-data/document-1.md) has a task with continuation text:
```
- [ ] Find classes to update
  - [ ] Exclude classes that have no fields
        Is this still cool?
```
The line "Is this still cool?" is a continuation of the subtask. In marko's AST, this will likely be part of the same `Paragraph` node. Should the full text (including continuation lines) be used as `task_text`, or only the first line?

### 6.6 Config Delivery: File Path vs. Dict

The entry point signature is `extract_tasks_from_markdown(md_string, config_dict)` — it accepts a pre-loaded dict. But the spec also references "loaded from a JSON/YAML file." Should the module also provide a helper to load the config from a file path, or is that the caller's responsibility? Also, should there be a default/bundled config?

### 6.7 Passing Config Limits Into Pydantic Validation

`n_task_limit` and `r_header_limit` live in the config dict, but Pydantic validators need access to these values at model instantiation time. The cleanest approach would be to pass them as fields on the model (not just in an external config). Planned approach: include `n_task_limit` and `r_header_limit` as fields on `ParsedTask` (excluded from serialization and from the `task_id` hash), so validators can reference them via `self` / `values`. Alternatively, we could use Pydantic's `model_config` / validation context. Which approach is preferred?

### 6.8 `domain` Type Validation Behavior

For a `domain`-type alias (e.g., `on_completion` with `allowed: ["keep", "delete"]`), what should happen if the extracted value is not in the allowed list? Options:
- (a) Raise a validation error (strict),
- (b) Silently discard the attribute,
- (c) Store it anyway with a warning.

### 6.9 `pydantic` Dependency

`pydantic` is not currently in [pyproject.toml](../pyproject.toml). Will need to be added. Any version constraints to be aware of? Assuming v2.x.

### 6.10 New Module Location

The spec says to create new files rather than modifying existing ones. Planned structure:
```
markdown_stuff/
    models.py          # ParsedTask, ParsedDocument (Pydantic models)
    extractor.py       # extract_attributes, walk_ast, extract_tasks_from_markdown
```
And a sample config file, e.g.:
```
config/
    default_config.json
```
Plus a script to exercise it:
```
scripts/
    extract_tasks.py
```
Does this layout look appropriate?

### 6.11 Date Parsing for `date` Type Attributes

The config has a `date` type for aliases like `📅`. Should dates be validated/parsed into `datetime.date` objects, or stored as raw strings? If parsed, what formats should be accepted (ISO 8601 only, or more flexible)?

### 6.12 Attribute Extraction Ordering / Greediness

For value-type aliases, the spec says to capture text "up until the next known alias or the end of the string." If multiple value-type aliases appear on the same line, the extraction order matters. Should we process aliases left-to-right based on their position in the string (anchored regex), or iterate the config dict in definition order? Left-to-right positional matching seems more robust.