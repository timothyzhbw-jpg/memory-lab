"""Shared rendering boundaries for untrusted fields in human-facing reports.

Escape every control, format and Unicode separator before applying the destination's syntax.
Call these on fields, never on a complete report: its line breaks and markup belong to the renderer.
"""
from __future__ import annotations

import json
import re
import unicodedata
from html import escape

# Control, format (bidi, zero-width) and line/paragraph separator characters.
UNSAFE_CATEGORIES = ("Cc", "Cf", "Zl", "Zp")
_LINE_CLASSES = frozenset(UNSAFE_CATEGORIES)
_ASCII_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_MARKUP = re.compile(r"[\\`*_{}\[\]#|~&<>]")
_LEADING = re.compile(r"^(?:\s*[-+=]|\s*\d+[.)](?=\s))")
_MARKDOWN_CHARS = re.compile(r"([\\`*_{}\[\]#|~])")
_LEADING_SPACES = re.compile(r"^ +")
_LEADING_MARK = re.compile(r"^(\s*)([-+=])")
_LEADING_NUMBER = re.compile(r"^(\s*\d+)([.)])(?=\s)")
_BACKTICKS = re.compile(r"`+")


def _plain_line(text: str) -> bool:
    """True when ``one_line`` would copy every character unchanged."""
    if text.isascii():
        # ASCII Cc is C0 plus DEL. Format and separator characters are all above ASCII.
        return _ASCII_CONTROL.search(text) is None
    for char in text:
        if unicodedata.category(char) in _LINE_CLASSES:
            return False
    return True


def one_line(text: str) -> str:
    """Untrusted text kept on one line: control, format (bidi, zero-width) and separator characters are escaped."""
    if _plain_line(text):
        return text
    return "".join(c if unicodedata.category(c) not in _LINE_CLASSES
                   else f"\\u{ord(c):04x}" if ord(c) <= 0xFFFF else f"\\U{ord(c):08x}" for c in text)


def markdown_text(text: str, *, preserve_inert_underscores: bool = False) -> str:
    """Literal Markdown text, including in headings and evidence blockquotes."""
    text = one_line(text)
    if _MARKUP.search(text) is None and _LEADING.search(text) is None and not text.startswith(" "):
        return text  # nothing to escape: skip the substitutions
    text = escape(text, quote=False)
    if preserve_inert_underscores:
        # CommonMark does not treat intraword underscores as emphasis. Keep ordinary role names
        # byte-for-byte while escaping underscores that can form markup.
        text = _MARKDOWN_CHARS.sub(
            lambda m: m.group() if m.group() == "_" and m.start() > 0 and m.end() < len(text)
            and text[m.start() - 1].isalnum() and text[m.end()].isalnum() else "\\" + m.group(), text)
    else:
        text = _MARKDOWN_CHARS.sub(r"\\\1", text)
    # Leading spaces would make an evidence blockquote an indented code block: keep them as references.
    text = _LEADING_SPACES.sub(lambda spaces: "&#32;" * len(spaces.group()), text)
    # Block syntax can still be recognized at the start of a one-line quote/detail.
    text = _LEADING_MARK.sub(r"\1\\\2", text)
    return _LEADING_NUMBER.sub(r"\1\\\2", text)


def code_span(text: str, *, pad_backticks: bool = True) -> str:
    """Code spans need a delimiter longer than any backtick run in the data."""
    text = one_line(text)
    if "`" not in text:
        # Padding protects significant edge spaces. A single space on both sides is not significant.
        if text.startswith(" ") and text.endswith(" ") and text.strip():
            return f"` {text} `"
        return f"`{text}`"
    fence = "`" * (1 + max(len(run) for run in _BACKTICKS.findall(text)))
    # Interior backticks need a longer delimiter; edge backticks also require padding.
    pad = " " if pad_backticks or text.startswith("`") or text.endswith("`") else ""
    return f"{fence}{pad}{text}{pad}{fence}"


def table_code(text: str) -> str:
    """A code span in a Markdown table, where pipes still delimit cells."""
    return code_span(text).replace("|", "\\|")


def report_text(text: str) -> str:
    """Literal Markdown, preserving ordinary text that cannot form Markdown syntax.

    Intraword underscores, interior hashes and non-link brackets are inert in CommonMark. Keep
    those unchanged for existing reports; dangerous text uses the same strict escaping as audits.
    """
    if (one_line(text) == text and not re.search(r"[\\`*~&<>|]", text)
            and not re.search(r"(?<!\w)_(?!\s)|(?<!\s)_(?!\w)", text)
            and not re.search(r"^\s*#|\[[^\]]*\]\s*[([]|^\s*\[[^\]]*\]:", text)
            and _LEADING.search(text) is None and not text.startswith(" ")):
        return text
    return markdown_text(text, preserve_inert_underscores=True)


def yaml_value(value) -> str:
    """A single-line YAML scalar or flow list; unsafe/ambiguous strings are JSON-quoted.

    JSON string syntax is valid YAML. Escaped controls remain visible text after YAML parsing.
    Existing unambiguous plain scalars retain their spelling for stable reports.
    """
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return "null"
    if isinstance(value, list):
        return "[" + ", ".join(yaml_value(v) for v in value) + "]"
    if isinstance(value, (int, float)):
        return json.dumps(value, allow_nan=False)
    text = one_line(str(value))
    plain = re.fullmatch(r"[A-Za-z][A-Za-z0-9_ /@().-]*", text)
    reserved = {"true", "false", "null", "yes", "no", "on", "off", "y", "n"}
    if plain and text == text.strip() and text.lower() not in reserved:
        return text
    return json.dumps(text, ensure_ascii=False)
