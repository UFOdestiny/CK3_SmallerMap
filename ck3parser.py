"""Small, source-preserving parser for Paradox/Clausewitz script files.

The parser deliberately does not convert scripts to dictionaries: duplicate keys are
valid in CK3 data and must not be lost.  It records brace blocks and scalar assignments
with source offsets so callers can remove exact blocks without reformatting game files.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import re
from typing import Iterable, Iterator, Sequence


TITLE_RE = re.compile(r"^[ekdcb]_[A-Za-z0-9_-]+$")


@dataclass(frozen=True)
class Token:
    text: str
    start: int
    end: int
    depth: int
    line: int


@dataclass
class Assignment:
    key: str
    value: str
    key_token: Token
    value_token: Token


@dataclass
class Block:
    key: str | None
    key_token: Token | None
    open_token: Token
    close_token: Token | None = None
    parent: "Block | None" = None
    children: list["Block"] = field(default_factory=list)

    @property
    def inner_depth(self) -> int:
        return self.open_token.depth + 1

    @property
    def start(self) -> int:
        return self.key_token.start if self.key_token else self.open_token.start

    @property
    def end(self) -> int:
        if self.close_token is None:
            raise ValueError("Unclosed block")
        return self.close_token.end

    def walk(self) -> Iterator["Block"]:
        yield self
        for child in self.children:
            yield from child.walk()


class ParseError(ValueError):
    pass


@dataclass
class Document:
    source: str
    tokens: list[Token]
    roots: list[Block]

    def blocks(self) -> Iterator[Block]:
        for root in self.roots:
            yield from root.walk()

    def title_blocks(self) -> Iterator[Block]:
        return (block for block in self.blocks() if block.key and TITLE_RE.fullmatch(block.key))

    def direct_assignments(self, block: Block) -> list[Assignment]:
        """Return scalar ``key = value`` assignments directly inside *block*."""
        depth = block.inner_depth
        start = block.open_token.end
        end = block.close_token.start if block.close_token else len(self.source)
        candidates = [
            token for token in self.tokens
            if token.depth == depth and start <= token.start < end
        ]
        result: list[Assignment] = []
        index = 0
        while index + 2 < len(candidates):
            key, equals, value = candidates[index:index + 3]
            if equals.text == "=" and value.text not in {"{", "}"}:
                # A named/prefixed block such as ``color = hsv { ... }`` is not scalar.
                after = candidates[index + 3] if index + 3 < len(candidates) else None
                if after is None or after.text != "{":
                    result.append(Assignment(key.text, value.text, key, value))
                    index += 3
                    continue
            index += 1
        return result

    def direct_value(self, block: Block, key: str) -> str | None:
        values = [item.value for item in self.direct_assignments(block) if item.key == key]
        return values[-1] if values else None

    def removal_range(self, block: Block) -> tuple[int, int]:
        """Expand a block range to whole lines when doing so is source-safe."""
        start, end = block.start, block.end
        line_start = self.source.rfind("\n", 0, start) + 1
        if self.source[line_start:start].strip() == "":
            start = line_start
        newline = self.source.find("\n", end)
        line_end = len(self.source) if newline < 0 else newline + 1
        tail = self.source[end:(len(self.source) if newline < 0 else newline)]
        if tail.strip() == "" or tail.lstrip().startswith("#"):
            end = line_end
        return start, end


def lex(source: str) -> list[Token]:
    tokens: list[Token] = []
    index = 0
    depth = 0
    line = 1
    length = len(source)
    while index < length:
        char = source[index]
        if char in " \t\r":
            index += 1
            continue
        if char == "\n":
            line += 1
            index += 1
            continue
        if char == "#":
            newline = source.find("\n", index)
            index = length if newline < 0 else newline
            continue
        start, token_line = index, line
        if char == '"':
            index += 1
            escaped = False
            while index < length:
                current = source[index]
                if current == "\n":
                    line += 1
                if current == '"' and not escaped:
                    index += 1
                    break
                escaped = current == "\\" and not escaped
                if current != "\\":
                    escaped = False
                index += 1
            else:
                raise ParseError(f"Unclosed string at line {token_line}")
            tokens.append(Token(source[start:index], start, index, depth, token_line))
            continue
        if char == "{":
            tokens.append(Token(char, start, start + 1, depth, token_line))
            depth += 1
            index += 1
            continue
        if char == "}":
            depth -= 1
            if depth < 0:
                raise ParseError(f"Unexpected closing brace at line {token_line}")
            tokens.append(Token(char, start, start + 1, depth, token_line))
            index += 1
            continue
        if char == "=":
            tokens.append(Token(char, start, start + 1, depth, token_line))
            index += 1
            continue
        while index < length and source[index] not in " \t\r\n#{}=\"":
            index += 1
        if index == start:
            # Quotes embedded in unquoted atoms are invalid but keeping the byte as a
            # token gives a useful, deterministic parse rather than an infinite loop.
            index += 1
        tokens.append(Token(source[start:index], start, index, depth, token_line))
    if depth:
        raise ParseError(f"Unclosed brace(s): depth is {depth} at end of file")
    return tokens


def parse(source: str) -> Document:
    tokens = lex(source)
    roots: list[Block] = []
    stack: list[Block] = []
    for index, token in enumerate(tokens):
        if token.text == "{":
            equals_index: int | None = None
            cursor = index - 1
            while cursor >= 0 and tokens[cursor].depth == token.depth:
                if tokens[cursor].text == "=":
                    equals_index = cursor
                    break
                if tokens[cursor].text in {"{", "}"}:
                    break
                cursor -= 1
            key_token = tokens[equals_index - 1] if equals_index and equals_index > 0 else None
            parent = stack[-1] if stack else None
            block = Block(
                key=key_token.text if key_token else None,
                key_token=key_token,
                open_token=token,
                parent=parent,
            )
            if parent:
                parent.children.append(block)
            else:
                roots.append(block)
            stack.append(block)
        elif token.text == "}":
            if not stack:
                raise ParseError(f"Unexpected closing brace at line {token.line}")
            stack.pop().close_token = token
    if stack:
        raise ParseError(f"Unclosed block beginning at line {stack[-1].open_token.line}")
    return Document(source=source, tokens=tokens, roots=roots)


def read_document(path: Path) -> Document:
    raw = path.read_bytes()
    try:
        source = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        # A small number of vanilla CK3 data files still contain Windows-1252
        # punctuation.  Decode them losslessly; generated overrides are emitted as
        # UTF-8 with BOM, which Clausewitz accepts consistently.
        source = raw.decode("cp1252")
    return parse(source)


def apply_edits(source: str, edits: Iterable[tuple[int, int, str]]) -> str:
    """Apply non-overlapping source edits, raising on accidental overlap."""
    ordered = sorted(edits, key=lambda item: (item[0], item[1]), reverse=True)
    original_length = len(source)
    last_start = original_length + 1
    for start, end, replacement in ordered:
        if not (0 <= start <= end <= original_length):
            raise ValueError(f"Invalid edit range {start}:{end}")
        if end > last_start:
            raise ValueError(f"Overlapping edit range {start}:{end}")
        source = source[:start] + replacement + source[end:]
        last_start = start
    return source


def nearest_title_parent(block: Block) -> Block | None:
    parent = block.parent
    while parent is not None:
        if parent.key and TITLE_RE.fullmatch(parent.key):
            return parent
        parent = parent.parent
    return None


def scalar_assignment_edits(
    document: Document,
    key: str,
    value_replacements: dict[str, str],
    allowed_ranges: Sequence[tuple[int, int]] | None = None,
) -> list[tuple[int, int, str]]:
    """Build token-safe edits for scalar assignments anywhere in a document."""
    ranges = (
        allowed_ranges
        if allowed_ranges is not None
        else [(0, len(document.source))]
    )
    edits: list[tuple[int, int, str]] = []
    tokens = document.tokens
    for index in range(len(tokens) - 2):
        left, equals, right = tokens[index:index + 3]
        if left.text != key or equals.text != "=" or not (
            left.depth == equals.depth == right.depth
        ):
            continue
        replacement = value_replacements.get(right.text)
        if replacement is None:
            continue
        if any(start <= left.start and right.end <= end for start, end in ranges):
            edits.append((right.start, right.end, replacement))
    return edits
