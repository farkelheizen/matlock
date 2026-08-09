from __future__ import annotations

import re
from typing import Literal

_TOKEN_RE = re.compile(r"\S+")
_HEADER_RE = re.compile(r"(?m)^#{1,6}\s+")
_FENCE_RE = re.compile(r"(?m)^```.*$")


def count_tokens(text: str) -> int:
    """Return a deterministic token count for chunk sizing.

    Search indexing intentionally uses a lightweight local approximation rather
    than a model-specific tokenizer so the core path has no hard dependency on a
    tokenizer package.
    """

    return len(_TOKEN_RE.findall(text))


def chunk_markdown(
    text: str,
    *,
    strategy: Literal["fixed_token", "markdown_header"] = "fixed_token",
    chunk_size: int = 500,
    chunk_overlap: int = 50,
) -> list[str]:
    """Split markdown text into deterministic search chunks."""

    if not text.strip():
        return []
    if strategy != "fixed_token":
        raise ValueError(f"Unsupported search chunking strategy: {strategy}")
    if chunk_overlap >= chunk_size:
        raise ValueError("chunk_overlap must be smaller than chunk_size")

    effective_size = chunk_size - chunk_overlap if chunk_overlap else chunk_size
    base_chunks = _chunk_recursive(text.strip(), effective_size, level=0)
    if not base_chunks:
        return []

    final_chunks = [_protect_code_fences(base_chunks[0])]
    for index in range(1, len(base_chunks)):
        previous_tail = _tail_tokens(base_chunks[index - 1], chunk_overlap)
        combined = _join_with_boundary(previous_tail, base_chunks[index])
        final_chunks.append(_protect_code_fences(combined))
    return final_chunks


def _chunk_recursive(text: str, limit: int, *, level: int) -> list[str]:
    if not text.strip():
        return []
    if count_tokens(text) <= limit:
        return [text.strip()]
    if level >= 3:
        return _slice_by_tokens(text, limit)

    pieces = _split_for_level(text, level)
    if len(pieces) <= 1:
        return _chunk_recursive(text, limit, level=level + 1)

    chunks: list[str] = []
    current = ""
    for piece in pieces:
        if not piece.strip():
            continue
        if count_tokens(piece) > limit:
            if current.strip():
                chunks.append(current.strip())
                current = ""
            chunks.extend(_chunk_recursive(piece, limit, level=level + 1))
            continue

        candidate = piece if not current else current + piece
        if current and count_tokens(candidate) > limit:
            chunks.append(current.strip())
            current = piece
            continue
        current = candidate

    if current.strip():
        chunks.append(current.strip())
    return chunks


def _split_for_level(text: str, level: int) -> list[str]:
    if level == 0:
        return _split_by_headers(text)
    if level == 1:
        return _split_with_separator(text, r"\n\s*\n")
    if level == 2:
        return text.splitlines(keepends=True)
    return [text]


def _split_by_headers(text: str) -> list[str]:
    matches = list(_HEADER_RE.finditer(text))
    if not matches:
        return [text]

    pieces: list[str] = []
    last = 0
    for index, match in enumerate(matches):
        start = match.start()
        if start > last:
            prelude = text[last:start]
            if prelude.strip():
                pieces.append(prelude)
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        section = text[start:end]
        if section.strip():
            pieces.append(section)
        last = end
    return pieces or [text]


def _split_with_separator(text: str, pattern: str) -> list[str]:
    parts = re.split(f"({pattern})", text)
    if len(parts) == 1:
        return [text]

    pieces: list[str] = []
    index = 0
    while index < len(parts):
        piece = parts[index]
        if index + 1 < len(parts):
            piece += parts[index + 1]
            index += 2
        else:
            index += 1
        if piece:
            pieces.append(piece)
    return pieces or [text]


def _slice_by_tokens(text: str, limit: int) -> list[str]:
    spans = [match.span() for match in _TOKEN_RE.finditer(text)]
    if not spans:
        return [text.strip()]

    chunks: list[str] = []
    for start in range(0, len(spans), limit):
        end = min(start + limit, len(spans))
        chunk_start = spans[start][0]
        chunk_end = spans[end - 1][1]
        chunk_text = text[chunk_start:chunk_end].strip()
        if chunk_text:
            chunks.append(chunk_text)
    return chunks


def _tail_tokens(text: str, count: int) -> str:
    if count <= 0:
        return ""
    matches = list(_TOKEN_RE.finditer(text))
    if not matches:
        return ""
    tail = matches[-count:]
    return text[tail[0].start():tail[-1].end()].strip()


def _join_with_boundary(prefix: str, suffix: str) -> str:
    if not prefix:
        return suffix.strip()
    if not suffix:
        return prefix.strip()
    if prefix[-1].isspace() or suffix[0].isspace():
        return (prefix + suffix).strip()
    return (prefix + "\n\n" + suffix).strip()


def _protect_code_fences(text: str) -> str:
    chunk = text.strip()
    fences = _FENCE_RE.findall(chunk)
    if fences and len(fences) % 2 == 1:
        if not chunk.endswith("\n"):
            chunk += "\n"
        chunk += "```"
    return chunk


__all__ = ["chunk_markdown", "count_tokens"]