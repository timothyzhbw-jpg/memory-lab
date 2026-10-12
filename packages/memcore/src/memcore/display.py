"""Shared rendering boundaries for untrusted fields in human-facing reports.

Escape every control, format and Unicode separator before applying the destination's syntax.
Call these on fields, never on a complete report: its line breaks and markup belong to the renderer.
"""
from __future__ import annotations

import json
import re
import unicodedata
from html import escape

# Control, format (bidi, zero-width) and line/paragraph separator characters, plus unassigned code points.
# The unassigned ("Cn") rule depends on the Unicode version of the running interpreter: an older Python
# (3.10 ships Unicode 13) sees a newer format character such as U+0890 as unassigned, and it must not pass
# through either, so the same text can print an extra escape on an older interpreter. Accepted trade-off:
# failing closed on a character this interpreter cannot classify beats letting an unknown invisible one through.
UNSAFE_CATEGORIES = ("Cc", "Cf", "Zl", "Zp", "Cn")
_LINE_CLASSES = frozenset(UNSAFE_CATEGORIES)
# ZWNJ and ZWJ are format characters but cannot reorder text, and real text needs them (Persian, Indic
# scripts, emoji sequences). They pass when they join something: after a visible non-ASCII character and not
# followed by another invisible one. Everywhere else (after a space or ASCII, at the start, in a run of
# invisible characters) they are escaped like any other format character. Bidi marks are never allowed.
_JOINERS = frozenset("\u200c\u200d")
_ASCII_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_MARKUP = re.compile(r"[\\`*_{}\[\]#|~&<>]")
_LEADING = re.compile(r"^(?:\s*[-+=]|\s*\d+[.)](?=\s|$))")
_MARKDOWN_CHARS = re.compile(r"([\\`*_{}\[\]#|~])")
_LEADING_SPACES = re.compile(r"^ +")
_LEADING_MARK = re.compile(r"^(\s*)([-+=])")
_LEADING_NUMBER = re.compile(r"^(\s*\d+)([.)])(?=\s|$)")
_BACKTICKS = re.compile(r"`+")
# GFM turns bare http, https and ftp URLs (any other scheme stays text), ``www.`` hosts and e-mail addresses
# into links. A backslash before the colon or the dot stops the first two and renders as the same text.
# cmark-gfm joins escaped characters back into one text node before it looks for addresses, so nothing breaks
# an e-mail address in the text: it becomes a code span. ``_AUTOLINK`` is a superset of what the three
# patterns change (the fast-path test).
_URL_COLON = re.compile(r"(?<![A-Za-z])((?:https?|ftp)):(?=//)", re.IGNORECASE)
_WWW_DOT = re.compile(r"(?<![A-Za-z0-9])(www)\.", re.IGNORECASE)
_EMAIL = re.compile(r"[A-Za-z0-9._+-]+@[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)+")
_AUTOLINK = re.compile(r"(?<![A-Za-z])(?:https?|ftp)://|[A-Za-z0-9._+-]@[A-Za-z0-9]|(?<![A-Za-z0-9])www\.",
                       re.IGNORECASE)
# A link or image needs ``](`` or ``][`` (nested brackets included); a reference definition is ``[label]:``
# at the start of a line, and its label may hold escaped brackets.
_LINK_SYNTAX = re.compile(r"\]\s*[(\[]|^\s*\[.*\]:")


def _joins(text: str, index: int) -> bool:
    """True when the joiner at ``index`` sits in real text: after a visible non-ASCII character, and not
    before another control, format or separator character."""
    if index == 0:
        return False
    before = text[index - 1]
    if before.isascii() or before.isspace() or unicodedata.category(before) in _LINE_CLASSES:
        return False
    return index + 1 == len(text) or unicodedata.category(text[index + 1]) not in _LINE_CLASSES


def _plain_line(text: str) -> bool:
    """True when ``one_line`` would copy every character unchanged."""
    if text.isascii():
        # ASCII Cc is C0 plus DEL. Format and separator characters are all above ASCII.
        return _ASCII_CONTROL.search(text) is None
    for index, char in enumerate(text):
        if unicodedata.category(char) in _LINE_CLASSES and not (char in _JOINERS and _joins(text, index)):
            return False
    return True


def one_line(text: str) -> str:
    """Untrusted text kept on one line: control, format (bidi, zero-width) and separator characters are escaped.

    ZWNJ and ZWJ inside real text (Persian, Indic scripts, emoji sequences) are kept; see ``_JOINERS``.
    """
    if _plain_line(text):
        return text
    return "".join(c if unicodedata.category(c) not in _LINE_CLASSES or (c in _JOINERS and _joins(text, i))
                   else f"\\u{ord(c):04x}" if ord(c) <= 0xFFFF else f"\\U{ord(c):08x}" for i, c in enumerate(text))


def json_shield(text: str) -> str:
    """Make serialized JSON/JSONL inert while preserving its values and layout.

    JSON serializers already escape C0 characters inside strings. Preserve JSON whitespace
    between tokens, and escape every remaining unsafe category with valid JSON escapes,
    including surrogate pairs for supplementary format characters.
    """
    return "".join(c if c in "\t\r\n" or unicodedata.category(c) not in _LINE_CLASSES
                   else json.dumps(c, ensure_ascii=True)[1:-1] for c in text)


def markdown_text(text: str, *, preserve_inert_underscores: bool = False) -> str:
    """Literal Markdown text, including in headings and evidence blockquotes."""
    text = one_line(text)
    if (_MARKUP.search(text) is None and _LEADING.search(text) is None and not text.startswith(" ")
            and _AUTOLINK.search(text) is None):
        return text  # nothing to escape: skip the substitutions
    text, addresses = _hide_addresses(text)
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
    text = _LEADING_NUMBER.sub(r"\1\\\2", text)
    return _restore_addresses(_break_urls(text), addresses)


def _hide_addresses(text: str) -> tuple[str, list[str]]:
    """``text`` with each e-mail address replaced by a NUL (``one_line`` left none in it), and the addresses."""
    return _EMAIL.sub("\x00", text), _EMAIL.findall(text)


def _break_urls(text: str) -> str:
    """Bare URLs and www hosts stay text once their colon or dot is escaped; the escape renders as the same character."""
    return _WWW_DOT.sub(r"\1\\.", _URL_COLON.sub(r"\1\\:", text))


def _restore_addresses(text: str, addresses: list[str]) -> str:
    """Put the addresses removed by ``_hide_addresses`` back as code spans."""
    pieces = text.split("\x00")
    return pieces[0] + "".join(f"`{address}`{piece}" for address, piece in zip(addresses, pieces[1:]))


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

    Intraword underscores, interior hashes and brackets that cannot form a link are inert in CommonMark.
    Keep those unchanged for existing reports; dangerous text uses the same strict escaping as audits.
    A bare URL, www host or e-mail address (GFM would link it) is the one thing changed in otherwise plain text.
    """
    if (one_line(text) == text and not re.search(r"[\\`*~&<>|]", text)
            and not re.search(r"(?<!\w)_(?!\s)|(?<!\s)_(?!\w)", text)
            and not re.search(r"^\s*#", text) and _LINK_SYNTAX.search(text) is None
            and _LEADING.search(text) is None and not text.startswith(" ")):
        if _AUTOLINK.search(text) is None:
            return text
        text, addresses = _hide_addresses(text)
        return _restore_addresses(_break_urls(text), addresses)
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
