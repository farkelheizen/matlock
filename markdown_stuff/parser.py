from __future__ import annotations

from typing import Any

from markdown_it import MarkdownIt
from markdown_it.token import Token
from marko import Markdown
from marko.ast_renderer import ASTRenderer


_TOKEN_PARSER = MarkdownIt()
_AST_PARSER = Markdown(renderer=ASTRenderer)


def parse_tokens(markdown_text: str) -> list[Token]:
    return _TOKEN_PARSER.parse(markdown_text)


def parse_ast(markdown_text: str) -> dict[str, Any]:
    return _AST_PARSER.convert(markdown_text)