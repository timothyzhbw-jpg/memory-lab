"""What a reader of a rendered Markdown file takes as one paragraph, list item or table row.

A memory file is read in these blocks, and so is an evidence file in Markdown or plain text, so that a sentence wrapped
over several lines is one sentence on both sides of a comparison.
"""
from __future__ import annotations

import re
from bisect import bisect_left
from itertools import takewhile
from typing import Literal, NamedTuple

from .formats import frontmatter
from .text import CJK, STOPWORDS

FENCE = re.compile(r"^\s*(```|~~~)")
LINK_DEFINITION = re.compile(r"\[[^\]]+\]:\s*\S+")


class Block(NamedTuple):
    first: int  # index of its first line
    last: int  # index of its last line
    raw: str  # those lines, joined by LF; a heading's text for a heading
    kind: Literal["text", "row", "heading"]


_QUOTE_PREFIX = re.compile(r"[ \t]*(?:>[ \t]*)+")
_BULLET = re.compile(r"[ \t]*[-*+](?:[ \t]+|$)")
_ORDERED = re.compile(r"[ \t]*(\d{1,9})[.)](?:[ \t]+|$)")
_RULE = re.compile(r"[ \t]*([-*_])[ \t]*(?:\1[ \t]*){2,}")
_UNDERLINE = re.compile(r"[ \t]*(?:=+|-{2,})[ \t]*")
# Spaces only after a bar, never on both sides of an optional one: a long run of spaces has one way to be read.
_TABLE_RULE = re.compile(r"[ \t]*(?:\|[ \t]*)?:?-+:?[ \t]*(?:\|[ \t]*:?-+:?[ \t]*)*(?:\|[ \t]*)?")
_TAGS_ONLY = re.compile(r"[ \t]*(?:</?[A-Za-z][^<>]*>[ \t]*)+")
_HEADING = re.compile(r"^\s*#{1,6}\s+(.*)")
_BREAK_TAG = re.compile(r"<br\s*/?>$", re.I)
_FINISHED = re.compile(r"[.!?;。！？；][\"')\]*_`]*$")
_SENTENCE_ENDS = re.compile(r"[.;。；][\"')\]*_`]*$")  # a title can end in ``?`` or ``!``
_ENDS_IN_URL = re.compile(r"(?<!\S)[a-z]+://\S+$")
_KEYED = re.compile(r"[*_`]*[\w /-]{1,24}[*_`]*:[*_`]*\s")  # "Language: Python", each line a key of its own
WRAP_WIDTH = 60  # columns: a line this long that stops mid-sentence was wrapped; a shorter one may be a line of its own
MOST_WRAPPED = 12  # lines one sentence can go on over: past that, it is a log or a listing, which stays line by line


def _width(text: str) -> int:
    return len(text) + len(CJK.findall(text))


def _indent(line: str) -> int:
    width = 0
    for char in line:
        if char == " ":
            width += 1
        elif char == "\t":
            width += 4 - width % 4
        else:
            break
    return width


def _quote_end(line: str) -> int:
    prefix = _QUOTE_PREFIX.match(line)
    return prefix.end() if prefix else 0


def _is_wrapped(previous: str, line: str, ahead: list[str] | None = None) -> bool:
    """Whether ``line`` goes on with the sentence of ``previous``. Markdown says always, but people also write short
    lines with no marker and mean each on its own (``Project: foo`` over ``Language: Python``), and long ones with no
    period (``ahead``: the lines after ``line``). A finished sentence is cut at its period either way."""
    if previous.endswith(("  ", "\\")) or _BREAK_TAG.search(previous[-16:]):  # a hard line break
        return False
    before = previous.strip()
    if _FINISHED.search(before):
        return True
    if _ENDS_IN_URL.search(before) or (_KEYED.match(before[:64]) and _KEYED.match(line.strip()[:64])):
        return False
    width = _width(before)
    if before.endswith(":"):  # a label over its value, or a long sentence wrapped right there
        return width >= WRAP_WIDTH
    if before.endswith((",", "(", "-", "–", "—", "/", "，", "、", "（")) or before.rsplit(None, 1)[-1].strip("*_`").lower() in STOPWORDS:
        return True
    # Something open goes on, however short the line: a long URL or name was often all that moved to the next one.
    if before.count("[") > before.count("]") or before.count("(") > before.count(")") or before.count("`") % 2:
        return True
    tail = line.strip()
    if " " not in tail and _FINISHED.search(tail):  # one word and a period: the end of what the line above began
        return True
    opening = next((char for char in line if char.isalnum()), "")
    if width >= WRAP_WIDTH:
        # A capital after a long line is a new statement, unless a period somewhere ahead says these are sentences.
        return not opening.isupper() or any(_FINISHED.search(next_line.rstrip()) for next_line in (line, *(ahead or ())))
    return width >= WRAP_WIDTH // 2 and (opening.islower() or opening.isdigit())


def blocks(lines: list[str], start: int = 0) -> list[Block]:
    """The blocks of a memory file from line ``start`` on: headings, table rows, paragraphs and list items. What holds
    no claim is left out: blank lines, code, comments, rules, table headers, lines of nothing but tags."""
    found: list[Block] = []
    first: int | None = None  # the open paragraph or list item
    in_quote = in_item = False  # what the open block is
    offset = 0  # where an open list item's text starts
    fenced = in_table = in_list = False
    comment_ends = [at for at, line in enumerate(lines) if "-->" in line]

    def close(last: int) -> None:
        nonlocal first
        if first is not None:
            found.append(Block(first, last, "\n".join(lines[first:last + 1]), "text"))
            first = None

    def table_rule(at: int) -> bool:
        if at >= len(lines):
            return False
        content = lines[at][_quote_end(lines[at]):]
        return "|" in content and _TABLE_RULE.fullmatch(content) is not None

    index = start - 1
    while (index := index + 1) < len(lines):
        raw = lines[index]
        if FENCE.match(raw):
            close(index - 1)
            fenced, in_table = not fenced, False
            continue
        if fenced:
            continue
        cut = _quote_end(raw)
        content = raw[cut:]
        if not content.strip():
            close(index - 1)
            in_table = False
            continue
        if raw.lstrip().startswith("<!--"):
            close(index - 1)
            in_table = False
            end = bisect_left(comment_ends, index)  # to the comment's end, if it has one: an unclosed one hides nothing
            index = comment_ends[end] if end < len(comment_ends) else index
            continue
        if heading := _HEADING.match(raw):
            close(index - 1)
            in_table = in_list = False
            found.append(Block(index, index, heading.group(1).strip(), "heading"))
            continue
        if first is not None and not (in_item or in_quote or cut) and _UNDERLINE.fullmatch(raw):  # a Setext heading
            if "-" in raw and _SENTENCE_ENDS.search(lines[index - 1].rstrip()):  # sentences over a rule: not a title
                close(index - 1)
            else:
                found.append(Block(first, index, " ".join(line.strip() for line in lines[first:index]), "heading"))
                first = None
            in_table = in_list = False
            continue
        if _RULE.fullmatch(content):
            close(index - 1)
            in_table = in_list = False
            continue
        if table_rule(index):
            close(index - 1)
            in_table = True
            continue
        if "|" in content and table_rule(index + 1):  # a table's header and rule: the labels of its columns
            close(index - 1)
            in_table = True
            index += 1
            continue
        if _TAGS_ONLY.fullmatch(content):
            close(index - 1)
            in_table = False
            continue
        bullet = _BULLET.match(content)
        ordered = _ORDERED.match(content)
        if bullet or (ordered and (first is None or in_item or ordered.group(1) == "1")):
            close(index - 1)
            first, in_quote, in_item, in_table, in_list = index, cut > 0, True, False, True
            offset = (bullet or ordered).end() + cut
            continue
        if (in_table and "|" in content) or content.lstrip().startswith("|"):
            close(index - 1)
            found.append(Block(index, index, raw, "row"))
            in_table = True
            continue
        in_table = False
        indent = cut + _indent(content)
        if first is not None:
            previous = lines[index - 1]
            if not ((cut > 0 and not in_quote) or LINK_DEFINITION.match(content.strip())) and (
                    (in_item and indent >= offset)
                    or _is_wrapped(previous[_quote_end(previous):], content,
                                   list(takewhile(str.strip, lines[index + 1:index + 1 + MOST_WRAPPED])))):
                continue
            close(index - 1)
        elif indent >= 4 and not in_list and not cut:
            continue  # an indented code block
        first, in_quote, in_item = index, cut > 0, False
        in_list = in_list and indent > 0
    close(len(lines) - 1)
    return found


def block_at(lines: list[str], number: int) -> Block | None:
    """The block of a memory file (its lines, split as ``parse_text`` splits them) that holds line ``number``, counted
    from 1 as ``Claim.line`` is."""
    for block in blocks(lines, frontmatter(lines)[0]):
        if block.first < number <= block.last + 1:
            return block
    return None


def _joints(lines: list[str], block: Block) -> list[int]:
    """The lines of a block that go on with the next one."""
    open_lines = 0
    for at in range(block.first, block.last):
        open_lines = 0 if _FINISHED.search(lines[at].rstrip()) else open_lines + 1
        if open_lines > MOST_WRAPPED:
            return []
    return list(range(block.first, block.last))


def unwrapped(text: str) -> str:
    """``text`` at the same length, with the line breaks inside each paragraph and list item as spaces: every offset
    still points into the file as it is, and what cuts sentences line by line now reads a wrapped one whole."""
    lines = text.split("\n")
    bare = [line.removesuffix("\r") for line in lines]
    joints = {at for block in blocks(bare) if block.kind == "text" for at in _joints(bare, block)}
    if not joints:
        return text
    return "".join((line[:-1] + "  " if line.endswith("\r") else line + " ") if at in joints else line + "\n"
                   for at, line in enumerate(lines))[:len(text)]
