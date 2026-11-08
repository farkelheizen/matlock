from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from .models import Chunk, Tokenizer


@dataclass(frozen=True)
class _Section:
    headings: tuple[tuple[int, str], ...]
    body: str


_HEADING = re.compile(r"^[ \t]{0,3}(#{1,6})[ \t]+(.+?)\s*#*[ \t]*$")
_SETEXT_HEADING = re.compile(r"^[ \t]*(=+|-+)[ \t]*$")
_FENCE = re.compile(r"^[ \t]{0,3}(`{3,}|~{3,})")


def _sections(markdown: str) -> list[_Section]:
    sections: list[_Section] = []
    heading_stack: list[tuple[int, str]] = []
    body_lines: list[str] = []
    current_headings: tuple[tuple[int, str], ...] = ()
    fence: tuple[str, int] | None = None

    def flush() -> None:
        body = "\n".join(body_lines).strip()
        if body:
            sections.append(_Section(current_headings, body))

    for line in markdown.splitlines():
        fence_match = _FENCE.match(line)
        # A heading-looking line inside a code fence belongs to the current section.
        if fence is not None:
            body_lines.append(line)
            if (
                fence_match is not None
                and fence_match.group(1)[0] == fence[0]
                and len(fence_match.group(1)) >= fence[1]
                and not line[fence_match.end() :].strip()
            ):
                fence = None
            continue
        if fence_match is not None:
            fence = (fence_match.group(1)[0], len(fence_match.group(1)))
            body_lines.append(line)
            continue

        match = _HEADING.match(line)
        setext_match = _SETEXT_HEADING.match(line)
        if match is None and not (
            setext_match is not None and body_lines and body_lines[-1].strip()
        ):
            body_lines.append(line)
            continue

        if match is not None:
            level = len(match.group(1))
            heading = match.group(2).strip()
        else:
            assert setext_match is not None
            level = 1 if setext_match.group(1).startswith("=") else 2
            # The preceding line is the heading, not part of the old section.
            heading = body_lines.pop().strip()

        flush()
        body_lines.clear()
        heading_stack[:] = [item for item in heading_stack if item[0] < level]
        heading_stack.append((level, heading))
        current_headings = tuple(heading_stack)
    flush()
    return sections


def _best_boundary(text: str, start: int, tentative: int) -> int:
    lower_bound = start + max(1, (tentative - start) // 2)
    for separator in ("\n", ". ", "! ", "? ", "; ", ", ", " "):
        boundary = text.rfind(separator, lower_bound, tentative + 1)
        if boundary >= lower_bound:
            return boundary + len(separator)
    return tentative


def _split_text(
    text: str,
    tokenizer: Tokenizer,
    max_tokens: int,
    overlap_tokens: int,
    prefix: str = "",
) -> list[str]:
    if tokenizer.count(prefix + text) <= max_tokens:
        return [text]

    result: list[str] = []
    start = 0
    while start < len(text):
        low, high = start + 1, len(text)
        best = start
        while low <= high:
            middle = (low + high) // 2
            if tokenizer.count(prefix + text[start:middle]) <= max_tokens:
                best = middle
                low = middle + 1
            else:
                high = middle - 1
        if best == start:
            raise ValueError("A single character exceeds the configured token limit")
        end = best if best == len(text) else _best_boundary(text, start, best)
        end = max(start + 1, end)
        result.append(text[start:end].strip())
        if end >= len(text):
            break
        overlap_start = end
        if overlap_tokens:
            # Choose a suffix within the overlap budget, then avoid splitting a word.
            low, high = start, end
            while low < high:
                middle = (low + high) // 2
                if tokenizer.count(text[middle:end]) <= overlap_tokens:
                    high = middle
                else:
                    low = middle + 1
            overlap_start = low
            while overlap_start > start and not text[overlap_start - 1].isspace():
                overlap_start -= 1
            if overlap_start == start:
                overlap_start = end
        start = max(start + 1, overlap_start)
    return [piece for piece in result if piece]


def chunk_markdown(
    relative_path: str,
    markdown: str,
    tokenizer: Tokenizer,
    target_tokens: int = 400,
    overlap_tokens: int = 50,
    max_input_tokens: int = 512,
) -> list[Chunk]:
    if target_tokens < 1 or target_tokens > max_input_tokens:
        raise ValueError("target_tokens must fit within max_input_tokens")
    if overlap_tokens < 0 or overlap_tokens >= target_tokens:
        raise ValueError("overlap_tokens must be nonnegative and less than target_tokens")

    chunks: list[Chunk] = []
    for section in _sections(markdown):
        breadcrumb = "\n".join(
            f"{'#' * level} {heading}" for level, heading in section.headings
        )
        prefix = f"{breadcrumb}\n\n" if breadcrumb else ""
        if tokenizer.count(prefix) >= target_tokens:
            raise ValueError("Heading breadcrumb exceeds the chunk token budget")

        paragraphs = re.split(r"(\n[ \t]*\n+)", section.body)
        blocks: list[str] = []
        pending = ""
        for item in paragraphs:
            candidate = pending + item
            if candidate and tokenizer.count(prefix + candidate) <= target_tokens:
                pending = candidate
                continue
            if pending.strip():
                blocks.extend(
                    _split_text(
                        pending.strip(),
                        tokenizer,
                        target_tokens,
                        overlap_tokens,
                        prefix,
                    )
                )
            pending = item
            if tokenizer.count(prefix + pending.strip()) > target_tokens:
                blocks.extend(
                    _split_text(
                        pending.strip(),
                        tokenizer,
                        target_tokens,
                        overlap_tokens,
                        prefix,
                    )
                )
                pending = ""
        if pending.strip():
            blocks.extend(
                _split_text(
                    pending.strip(),
                    tokenizer,
                    target_tokens,
                    overlap_tokens,
                    prefix,
                )
            )

        for block in blocks:
            content = f"{prefix}{block}".strip()
            if tokenizer.count(content) > max_input_tokens:
                raise ValueError("Chunk exceeds the embedding model input limit")
            ordinal = len(chunks)
            # Stable for a path/ordinal pair, not a content hash.
            digest = hashlib.sha256(
                f"{relative_path}\0{ordinal}".encode("utf-8")
            ).hexdigest()[:24]
            chunks.append(Chunk(digest, ordinal, content))
    return chunks
