"""Readable one-line diagnostics for a terminal.

``display.one_line`` escapes every control, format and separator character, which includes the line breaks of an
ordinary multi-line message (a Pydantic validation error, git's stderr). ``diagnostic_line`` first turns those
ordinary line breaks into spaces, so benign text stays readable, and then applies ``one_line`` to the rest.
"""
from __future__ import annotations

import re

from .display import one_line

# Space, tab, LF, CR, VT and FF. ``str.split`` also treats the C0 information separators U+001C to U+001F, NEL,
# U+2028 and U+2029 as whitespace, but those are line breaks to some readers and must stay visible as escapes,
# not vanish into a space. Text spaces such as U+00A0 and U+3000 are left as typed.
_ORDINARY_WHITESPACE = re.compile(r"[ \t\n\r\v\f]+")


def diagnostic_line(text: str) -> str:
    """Untrusted message text on one line: ordinary whitespace runs become one space, the ends are trimmed,
    and every control, format (bidi, zero-width) and separator character is escaped."""
    return one_line(_ORDINARY_WHITESPACE.sub(" ", text).strip(" "))
