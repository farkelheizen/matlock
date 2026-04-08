from __future__ import annotations

import hashlib
import json
import re
from typing import Any


def extract_attributes(
    raw_text: str, aliases: dict
) -> tuple[str, dict[str, Any], list[str]]:
    """Extract alias-based and curly-brace attributes from raw task text.

    Returns (cleaned_text, attributes, errors).
    """
    attributes: dict[str, Any] = {}
    errors: list[str] = []

    # --- Step 1: find all alias positions (left-to-right) ---
    # Build a set of all alias strings so we can detect "next alias" boundaries.
    all_alias_strings = list(aliases.keys())

    found: list[tuple[int, str, dict]] = []
    for alias_key, alias_cfg in aliases.items():
        pos = raw_text.find(alias_key)
        if pos != -1:
            found.append((pos, alias_key, alias_cfg))

    # Sort by position ascending
    found.sort(key=lambda t: t[0])

    # --- Step 2: process aliases left-to-right, collecting removals ---
    # We'll collect (start, end) spans to remove from raw_text after processing.
    # Work on a mutable copy as we find boundaries.
    working = raw_text

    # We process in order, but use the *original* raw_text positions for boundary
    # detection, then remove spans from end-to-start to preserve positions.
    removals: list[tuple[int, int]] = []  # (start, end) in original raw_text

    for idx, (pos, alias_key, alias_cfg) in enumerate(found):
        attr_name = alias_cfg["attribute"]
        alias_type = alias_cfg["type"]

        # Determine end of this alias's value region:
        # stop at the next alias, OR at any '{' (curly-brace boundary), whichever is first.
        if idx + 1 < len(found):
            next_alias_pos = found[idx + 1][0]
        else:
            next_alias_pos = len(raw_text)

        alias_end = pos + len(alias_key)

        # Also stop at a '{' (start of curly-brace attribute)
        curly_pos = raw_text.find("{", alias_end)
        if curly_pos == -1:
            curly_pos = len(raw_text)

        next_pos = min(next_alias_pos, curly_pos)
        value_text = raw_text[alias_end:next_pos].strip()

        if alias_type == "literal":
            if attr_name in attributes:
                errors.append(f"Duplicate attribute {attr_name!r} encountered")
            else:
                attributes[attr_name] = alias_cfg["value"]
            # removal: just the alias itself
            removals.append((pos, alias_end))

        else:
            # string / date / domain
            parsed_value: Any = value_text if value_text else None

            if alias_type == "date":
                if parsed_value is not None and not re.fullmatch(
                    r"\d{4}-\d{2}-\d{2}", parsed_value
                ):
                    errors.append(
                        f"Invalid date value {parsed_value!r} for attribute"
                        f" {attr_name!r}; expected YYYY-MM-DD"
                    )
                    parsed_value = None

            elif alias_type == "domain":
                allowed = alias_cfg.get("allowed", [])
                if parsed_value is not None and parsed_value not in allowed:
                    errors.append(
                        f"Invalid value {parsed_value!r} for domain attribute"
                        f" {attr_name!r}; allowed: {allowed}"
                    )
                    parsed_value = None

            if attr_name in attributes:
                errors.append(f"Duplicate attribute {attr_name!r} encountered")
            else:
                attributes[attr_name] = parsed_value

            # removal: alias + value segment (up to next alias start)
            removals.append((pos, next_pos))

    # Apply removals from right-to-left so positions stay valid
    cleaned = raw_text
    for start, end in sorted(removals, key=lambda t: t[0], reverse=True):
        cleaned = cleaned[:start] + cleaned[end:]

    # --- Step 3: curly-brace attributes ---
    curly_pattern = re.compile(r"\{\s*([^:}]+?)\s*:\s*([^}]*?)\s*\}")
    new_cleaned = ""
    last = 0
    for m in curly_pattern.finditer(cleaned):
        key = m.group(1).strip()
        val = m.group(2).strip()
        if key in attributes:
            errors.append(f"Duplicate attribute {key!r} encountered")
        else:
            attributes[key] = val
        new_cleaned += cleaned[last : m.start()]
        last = m.end()
    new_cleaned += cleaned[last:]
    cleaned = new_cleaned

    return cleaned.strip(), attributes, errors


def compute_task_id(
    task_text: str,
    overflow: bool,
    headers: list[str],
    parent_task_id: str | None,
    twin_index: int,
) -> str:
    """Compute a deterministic SHA-256 task ID from the given fields."""
    payload = json.dumps(
        {
            "task_text": task_text,
            "overflow": overflow,
            "headers": headers,
            "parent_task_id": parent_task_id,
            "twin_index": twin_index,
        },
        separators=(",", ":"),
        sort_keys=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def truncate_text(text: str, maxlen: int) -> tuple[str, bool]:
    """Truncate text to maxlen characters, appending '...' if truncated."""
    if len(text) > maxlen:
        return text[:maxlen] + "...", True
    return text, False


def truncate_headers(headers: list[str], maxlen: int) -> list[str]:
    """Truncate each header string to maxlen characters, appending '...' if needed."""
    result = []
    for h in headers:
        if len(h) > maxlen:
            result.append(h[:maxlen] + "...")
        else:
            result.append(h)
    return result


# ---------------------------------------------------------------------------
# AST Walker helpers
# ---------------------------------------------------------------------------

import marko.block as _mblock  # noqa: E402
import marko.inline as _minline  # noqa: E402

from marko import Markdown as _Markdown  # noqa: E402
from marko.ext.gfm import GFM as _GFM  # noqa: E402
from marko.ext.gfm.elements import Paragraph as _GFMParagraph  # noqa: E402

from markdown_stuff.models import ParsedDocument, ParsedTask  # noqa: E402
from markdown_stuff.parser import parse_front_matter  # noqa: E402


def extract_text_from_node(node) -> str:
    """Recursively extract plain text from a marko node.

    Skips child List nodes (those contain sub-tasks, not task text).
    LineBreak nodes are rendered as a single space.
    """
    if isinstance(node, _mblock.List):
        return ""
    if isinstance(node, _minline.RawText):
        return node.children
    if isinstance(node, _minline.LineBreak):
        return " "
    text = ""
    if hasattr(node, "children") and isinstance(node.children, list):
        for child in node.children:
            text += extract_text_from_node(child)
    return text


def extract_heading_text(heading_node) -> str:
    """Extract plain text from a marko Heading node."""
    return extract_text_from_node(heading_node).strip()


def walk_ast(
    node,
    header_state: list[str],
    parent_task_id: str | None,
    config: dict,
    tasks: list,
    twin_tracker: dict,
) -> None:
    """Recursively walk the marko AST and accumulate ParsedTask objects."""
    node_type = type(node).__name__

    if node_type == "Document":
        for child in node.children:
            walk_ast(child, header_state, parent_task_id, config, tasks, twin_tracker)

    elif node_type == "Heading":
        level = node.level
        text = extract_heading_text(node)
        header_state[level - 1] = text
        # Clear all deeper heading levels
        for i in range(level, 6):
            header_state[i] = ""

    elif node_type == "List":
        for item in node.children:
            _process_list_item(
                item, header_state, parent_task_id, config, tasks, twin_tracker
            )

    elif hasattr(node, "children") and isinstance(node.children, list):
        # Other block-level nodes (Quote, etc.) — recurse
        for child in node.children:
            walk_ast(child, header_state, parent_task_id, config, tasks, twin_tracker)


def _process_list_item(
    item,
    header_state: list[str],
    parent_task_id: str | None,
    config: dict,
    tasks: list,
    twin_tracker: dict,
) -> None:
    """Process a single ListItem node."""
    # Find the first Paragraph child to check 'checked'
    first_paragraph = None
    sub_lists: list = []

    for child in item.children:
        if isinstance(child, _mblock.List):
            sub_lists.append(child)
        elif first_paragraph is None and isinstance(child, _GFMParagraph):
            first_paragraph = child

    checked = getattr(first_paragraph, "checked", None)

    if checked is None:
        # Not a task item — still recurse into sub-lists at same parent level
        for sub_list in sub_lists:
            walk_ast(
                sub_list, header_state, parent_task_id, config, tasks, twin_tracker
            )
        return

    # Extract raw text from all non-List children
    raw_text = ""
    for child in item.children:
        if not isinstance(child, _mblock.List):
            raw_text += extract_text_from_node(child)

    raw_text = raw_text.strip()

    # Extract attributes
    task_text, attributes, errors = extract_attributes(raw_text, config["aliases"])

    # Compact headers
    headers: list[str] = [h for h in header_state if h]

    # Truncate
    task_text, overflowed = truncate_text(task_text, config["task_text_maxlen"])
    headers = truncate_headers(headers, config["header_text_maxlen"])

    # Twin index
    twin_key = (tuple(headers), parent_task_id, task_text)
    twin_index = twin_tracker.get(twin_key, 0)
    twin_tracker[twin_key] = twin_index + 1

    # Compute task_id
    task_id = compute_task_id(task_text, overflowed, headers, parent_task_id, twin_index)

    task = ParsedTask(
        checked=bool(checked),
        task_text=task_text,
        overflow=overflowed,
        headers=headers,
        attributes=attributes,
        errors=errors,
        parent_task_id=parent_task_id,
        twin_index=twin_index,
        task_id=task_id,
    )
    tasks.append(task)

    # Recurse into sub-lists
    for sub_list in sub_lists:
        walk_ast(sub_list, header_state, task_id, config, tasks, twin_tracker)


def extract_tasks_from_markdown(md_string: str, config_dict: dict) -> ParsedDocument:
    """Parse a Markdown string and extract tasks, returning a ParsedDocument."""
    meta_data, body = parse_front_matter(md_string)

    md_parser = _Markdown(extensions=[_GFM])
    root = md_parser.parse(body)

    header_state: list[str] = [""] * 6
    tasks: list[ParsedTask] = []
    twin_tracker: dict = {}

    walk_ast(root, header_state, None, config_dict, tasks, twin_tracker)

    return ParsedDocument(meta_data=meta_data, tasks=tasks)
