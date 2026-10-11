"""JSON parsing that rejects a repeated object key.

``json.loads`` keeps the last value of a repeated key. A file can then show one
value to a person reading the text and another to the program: an ``events.jsonl``
line whose first ``actor`` is the one a reader sees, while replay uses the last,
or a ``policy.json`` that lists an actor twice and grants the second set of roles.
``loads`` uses ``object_pairs_hook`` and raises ``JSONDecodeError`` (message
``duplicate key``) on the second key, including inside nested objects.
"""
from __future__ import annotations

import json
from collections.abc import Callable
from json.decoder import scanstring
from pathlib import Path
from typing import Any


class _DuplicateKey(ValueError):
    """Raised by the hook. ``loads`` turns it into ``JSONDecodeError``."""


def reject_duplicate_keys(pairs: list[tuple[Any, Any]]) -> dict[Any, Any]:
    """``object_pairs_hook`` that refuses a repeated key in a single object."""
    result: dict[Any, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateKey()
        result[key] = value
    return result


def _skip(text: str, index: int) -> int:
    length = len(text)
    while index < length and text[index] in " \t\r\n":
        index += 1
    return index


class _At(Exception):
    def __init__(self, pos: int) -> None:
        self.pos = pos


def _duplicate_key_position(text: str) -> int:
    """Index of the quote that opens the second copy of a repeated key.

    The hook does not receive a document offset, so the line number is found by
    scanning the same text. Acceptance is decided only by the hook; this scan
    exists to name the line.
    """

    def value(index: int, depth: int) -> int:
        if depth > 512:
            raise ValueError("too deep")
        index = _skip(text, index)
        if index >= len(text):
            raise ValueError("truncated")
        char = text[index]
        if char == '"':
            _, end = scanstring(text, index + 1, True)
            return end
        if char == "{":
            return _object(index, depth + 1)
        if char == "[":
            return _array(index, depth + 1)
        if text.startswith("-Infinity", index):
            return index + len("-Infinity")
        for word in ("false", "true", "null", "Infinity", "NaN"):
            if text.startswith(word, index):
                return index + len(word)
        return _number(index)

    def _number(index: int) -> int:
        length = len(text)
        cursor = index
        if cursor < length and text[cursor] == "-":
            cursor += 1
        if cursor >= length or not text[cursor].isdigit():
            raise ValueError("number")
        if text[cursor] == "0":
            cursor += 1
        else:
            while cursor < length and text[cursor].isdigit():
                cursor += 1
        if cursor < length and text[cursor] == ".":
            cursor += 1
            if cursor >= length or not text[cursor].isdigit():
                raise ValueError("fraction")
            while cursor < length and text[cursor].isdigit():
                cursor += 1
        if cursor < length and text[cursor] in "eE":
            cursor += 1
            if cursor < length and text[cursor] in "+-":
                cursor += 1
            if cursor >= length or not text[cursor].isdigit():
                raise ValueError("exponent")
            cursor += 1
            while cursor < length and text[cursor].isdigit():
                cursor += 1
        return cursor

    def _object(index: int, depth: int) -> int:
        index = _skip(text, index + 1)
        if index < len(text) and text[index] == "}":
            return index + 1
        seen: dict[str, None] = {}
        while True:
            index = _skip(text, index)
            if index >= len(text) or text[index] != '"':
                raise ValueError("key")
            key_at = index
            key, index = scanstring(text, index + 1, True)
            if key in seen:
                raise _At(key_at)
            seen[key] = None
            index = _skip(text, index)
            if index >= len(text) or text[index] != ":":
                raise ValueError("colon")
            index = value(index + 1, depth)
            index = _skip(text, index)
            if index >= len(text):
                raise ValueError("truncated")
            if text[index] == "}":
                return index + 1
            if text[index] != ",":
                raise ValueError("comma")
            index += 1

    def _array(index: int, depth: int) -> int:
        index = _skip(text, index + 1)
        if index < len(text) and text[index] == "]":
            return index + 1
        while True:
            index = value(index, depth)
            index = _skip(text, index)
            if index >= len(text):
                raise ValueError("truncated")
            if text[index] == "]":
                return index + 1
            if text[index] != ",":
                raise ValueError("comma")
            index += 1

    try:
        value(_skip(text, 0), 0)
    except _At as found:
        return found.pos
    raise ValueError("no duplicate key")


def loads(data: str | bytes | bytearray, *, parse_constant: Callable[[str], Any] | None = None) -> Any:
    """Parse one JSON document. A repeated key raises ``JSONDecodeError``."""
    kwargs: dict[str, Any] = {"object_pairs_hook": reject_duplicate_keys}
    if parse_constant is not None:
        kwargs["parse_constant"] = parse_constant
    try:
        return json.loads(data, **kwargs)
    except _DuplicateKey:
        if isinstance(data, str):
            text = data
        else:
            raw = bytes(data)
            text = raw.decode(json.detect_encoding(raw))
        try:
            pos = _duplicate_key_position(text)
        except Exception:
            # The hook already rejected the document. A finder failure must not become a traceback.
            pos = 0
        raise json.JSONDecodeError("duplicate key", text, pos) from None


def loads_at(source: str | Path, data: str | bytes | bytearray, *,
             parse_constant: Callable[[str], Any] | None = None) -> Any:
    """Parse ``data`` from ``source``. Errors are ``{source}:{line}: invalid JSON: ...``."""
    try:
        return loads(data, parse_constant=parse_constant)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{source}:{exc.lineno}: invalid JSON: {exc.msg}") from exc
