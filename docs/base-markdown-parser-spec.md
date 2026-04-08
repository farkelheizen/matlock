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