"""Split a memory or agent instruction file (and the local .md files it links to) into auditable claims.

MEMORY.md, CLAUDE.md, AGENTS.md, GEMINI.md and the Cursor, Copilot and Windsurf rule files are all Markdown
once their frontmatter is removed (see ``formats``); the fields a format documents become claim metadata.
"""
from __future__ import annotations

import hashlib
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Literal, NamedTuple

from pydantic import BaseModel

from memcore.paths import InputPathError, input_path
from memcore.reading import confinement_root, read_input_text, take

from .corpus import Skipped
from .formats import MARKDOWN, MemoryFormat, Value, detect, frontmatter, metadata_of
from .text import CJK, STOPWORDS, features, sentence_spans, whole_span

LINK = re.compile(r"!?\[([^\]]*)\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
WIKILINK = re.compile(r"\[\[([^\]]+)\]\]")
EMPHASIS = re.compile(r"\*\*|__|`")
FENCE = re.compile(r"^\s*(```|~~~)")
CODE_SPAN = re.compile(r"`+[^`]*`+")
# An ``@path.md`` import (Claude Code, Gemini CLI): at the start of a line or after whitespace, outside code spans.
IMPORT = re.compile(r"(?<!\S)@([^\s`]+?\.md)(?=$|[\s,;:!?)\]]|\.(?:\s|$))")


class Claim(BaseModel):
    id: str
    text: str
    path: str
    line: int
    section: str | None = None
    also_at: list[str] = []
    metadata: dict[str, Value] = {}  # the frontmatter fields of the claim's file that its format documents

    @property
    def location(self) -> str:
        return f"{self.path}:{self.line}"


def claim_record(claim: Claim) -> dict:
    """What ``memfalsify claims`` prints for one claim; ``metadata`` only when its file has some."""
    record = {"memory_id": claim.id, "location": claim.location, "text": claim.text}
    if claim.metadata:
        record["metadata"] = claim.metadata
    return record


def claim_id(text: str) -> str:
    """Stable ID from normalized text. Editing a memory changes its ID, so old reviews no longer apply."""
    normalized = " ".join(text.split()).casefold()
    return "m-" + hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:8]


def _local_md_targets(line: str) -> list[str]:
    targets = [m.group(2).split("#")[0] for m in LINK.finditer(line)]
    targets += [m.group(1) for m in IMPORT.finditer(CODE_SPAN.sub(" ", line))]
    return [t for t in targets if t.endswith(".md") and "://" not in t]


_ENTRY_OPENS = re.compile(r"\s*(?:(?:>|[-*+]|\d+[.)]|\[[ xX]\])\s+)*(?:\*\*|__)?(?=!?\[|@)")


def _is_index_entry(raw: str) -> bool:
    """A block that opens with a link to a local .md file or an ``@file.md`` import points at claims, and what follows
    is a hook for that link (``- [Title](file.md) — hook``), however it is wrapped."""
    opening = _ENTRY_OPENS.match(raw)
    if opening is None:
        return False
    rest = raw[opening.end():]
    link = LINK.match(rest) or IMPORT.match(rest)
    return link is not None and bool(_local_md_targets(link.group()))


def _strip(pattern: re.Pattern, text: str, origin: list[int], keep_group: int | None) -> tuple[str, list[int]]:
    """``pattern.sub`` that keeps only ``keep_group`` of each match and tracks each character's raw offset."""
    out, where, last = [], [], 0
    for match in pattern.finditer(text):
        out.append(text[last:match.start()])
        where += origin[last:match.start()]
        if keep_group is not None:
            start, end = match.span(keep_group)
            out.append(text[start:end])
            where += origin[start:end]
        last = match.end()
    out.append(text[last:])
    where += origin[last:]
    return "".join(out), where


def _expand_markup(raw: str, start: int, end: int, others: list[tuple[int, int]]) -> tuple[int, int]:
    """Grow a content span to the link or emphasis that wraps it, and stop at a neighbor's content.

    A match that also overlaps or touches another sentence (``[A. B.](url)``, or ``****`` between two
    sentences) is left outside. Rollback substitutes this span, so taking the shared markup would keep
    the neighbor's URL or steal its marker.
    """
    while True:
        grew = False
        for pattern in (LINK, WIKILINK, EMPHASIS):
            for match in pattern.finditer(raw):
                ms, me = match.span()
                if me != start and ms != end and not (ms < end and me > start):
                    continue
                if any((ms < oe and me > os) or me == os or ms == oe for os, oe in others):
                    continue
                new_s, new_e = min(start, ms), max(end, me)
                if new_s < start or new_e > end:
                    start, end = new_s, new_e
                    grew = True
        if not grew:
            return start, end


_WRAP = re.compile(r"[ \t]*\n[ \t]*(?:>[ \t]*)*")
_NO_SPACE = re.compile("[㐀-鿿　-〿＀-￯]")  # Chinese characters and punctuation


def _unwrap(text: str, origin: list[int]) -> tuple[str, list[int]]:
    """One line from a block's lines: a line break and what continues the block (indent, quote markers) read as one
    space, or as nothing between two Chinese characters, which take no space."""
    out, where, last = [], [], 0
    for match in _WRAP.finditer(text):
        out.append(text[last:match.start()])
        where += origin[last:match.start()]
        if not (_NO_SPACE.match(text[match.start() - 1:match.start()]) and _NO_SPACE.match(text[match.end():match.end() + 1])):
            out.append(" ")
            where.append(origin[match.start()])
        last = match.end()
    out.append(text[last:])
    where += origin[last:]
    return "".join(out), where


def on_one_line(raw: str) -> str:
    """A raw sentence with its line breaks read as ``_unwrap`` reads them."""
    return _unwrap(raw, list(range(len(raw))))[0]


# Words that make a short piece of a sentence read as saying something. Only such pieces are measured against them.
_SAYS = frozenset("""
is are was were be been am isn't aren't has have had do does did can cannot could may might must shall should will would
use uses used run runs ran need needs require requires live lives call calls return returns add adds make makes get gets
set sets keep keeps see go goes work works take takes read reads write writes put puts avoid prefer
""".split())
_TAG = re.compile(r"</?[A-Za-z][^<>]*>")
_LABEL_OPENS = re.compile(r"(?:when|while|before|after|if|for|to|in|on|with|once|or|and|then|here|examples?|note)\b", re.I)
_LINK_DEFINITION = re.compile(r"\[[^\]]+\]:\s*\S+")


def is_fragment(text: str, position: int = 0) -> bool:
    """Whether a sentence of a block says nothing by itself. ``position``: how many sentences of the block precede it.

    Deliberately narrow, since a dropped claim is never checked: Chinese text always stays, and so does anything that
    reads as a sentence, such as ``Tests use pytest.``. What goes: markup with nothing else (a closing tag, a link
    definition), a label ending in a colon (``Run ty:``, ``When writing comments:``), and a short piece in lower case
    with no number and no word that says anything, cut from a longer sentence at a semicolon or a period
    (``time-based assertions;``). A whole list item stays even so: ``pnpm workspaces`` under ``Stack`` is a fact.
    """
    if CJK.search(text):
        return False
    if _LINK_DEFINITION.fullmatch(text) or len(features(_TAG.sub(" ", text))[0]) < 2:
        return True
    words = text.split()
    digit = re.search(r"\d", text) is not None
    if text.endswith(":") and not digit:
        if len(words) <= 3 or (_LABEL_OPENS.match(text) and "," not in text and len(words) <= 12):
            return True
    return (position > 0 and len(words) <= 4 and text[0].islower() and not digit
            and not any(word.strip(".,;:()'\"").lower() in _SAYS for word in words))


def sentences(raw: str, *, row: bool = False) -> list[tuple[str, int, int]]:
    """(claim text, raw start, raw end) for each claim-sized sentence of one block (see ``blocks``), or of one line.

    The text has links and emphasis removed and the block's lines joined. The raw span covers that sentence plus markup
    that wraps it alone; markup shared with another sentence stays outside the span. A table ``row`` is one statement,
    never cut at a period.
    """
    cleaned, origin = _strip(LINK, raw, list(range(len(raw))), 1)
    cleaned, origin = _strip(WIKILINK, cleaned, origin, 1)
    cleaned, origin = _strip(EMPHASIS, cleaned, origin, None)
    cleaned, origin = _unwrap(cleaned, origin)
    found = []
    spans = [span for span in [whole_span(cleaned)] if span] if row else sentence_spans(cleaned)
    position = 0  # claim-sized sentences so far: a list marker read as a sentence ("3.") is not one
    for start, end in spans:
        text = cleaned[start:end]
        words, _ = features(text)
        if len(words) >= 2:
            if not is_fragment(text, position):
                found.append((text, origin[start], origin[end - 1] + 1))
            position += 1
    content = [(start, end) for _, start, end in found]
    return [(text, *_expand_markup(raw, start, end, content[:i] + content[i + 1:]))
            for i, (text, start, end) in enumerate(found)]


# --- blocks: what a reader of the rendered Markdown takes as one paragraph, list item or table row -----

class Block(NamedTuple):
    first: int  # index of its first line
    last: int  # index of its last line
    raw: str  # those lines, joined by LF; a heading's text for a heading
    kind: Literal["text", "row", "heading"]

    def sentences(self) -> list[tuple[str, int, int]]:
        return sentences(self.raw, row=self.kind == "row")


_QUOTE_PREFIX = re.compile(r"[ \t]*(?:>[ \t]*)+")
_BULLET = re.compile(r"[ \t]*[-*+](?:[ \t]+|$)")
_ORDERED = re.compile(r"[ \t]*(\d{1,9})[.)](?:[ \t]+|$)")
_RULE = re.compile(r"[ \t]*([-*_])[ \t]*(?:\1[ \t]*){2,}")
_UNDERLINE = re.compile(r"[ \t]*(?:=+|-{2,})[ \t]*")
_TABLE_RULE = re.compile(r"[ \t]*\|?[ \t]*:?-+:?[ \t]*(?:\|[ \t]*:?-+:?[ \t]*)*\|?[ \t]*")
_TAGS_ONLY = re.compile(r"[ \t]*(?:</?[A-Za-z][^<>]*>[ \t]*)+")
_HEADING = re.compile(r"^\s*#{1,6}\s+(.*)")
_HARD_BREAK = re.compile(r"(?:[ ]{2,}|\\|<br\s*/?>)$", re.I)
_FINISHED = re.compile(r"[.!?;。！？；][\"')\]*_`]*$")
_LAST_WORD = re.compile(r"([A-Za-z']+)[*_`]*$")
_ENDS_IN_URL = re.compile(r"(?<!\S)[a-z]+://\S+$")
WRAP_WIDTH = 60  # columns: a line this long that stops mid-sentence was wrapped; a shorter one may be a line of its own


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


def _is_wrapped(previous: str, line: str) -> bool:
    """Whether ``line`` goes on with the sentence of ``previous``. Markdown says always, but people also write short
    lines with no marker and mean each on its own (``Project: foo`` over ``Language: Python``). A finished sentence is
    cut at its period either way."""
    if _HARD_BREAK.search(previous):
        return False
    before = previous.strip()
    if _FINISHED.search(before):
        return True
    if before.endswith(":") or _ENDS_IN_URL.search(before):
        return False
    word = _LAST_WORD.search(before)
    if before.endswith((",", "(", "-", "–", "—", "/")) or (word and word.group(1).lower() in STOPWORDS):
        return True
    opening = next((char for char in line if char.isalnum()), "")
    width = _width(before)
    return width >= WRAP_WIDTH or (width >= WRAP_WIDTH // 2 and (opening.islower() or opening.isdigit()))


def blocks(lines: list[str], start: int = 0) -> list[Block]:
    """The blocks of a memory file from line ``start`` on: headings, table rows, paragraphs and list items. What holds
    no claim is left out: blank lines, code, comments, rules, table headers, lines of nothing but tags."""
    found: list[Block] = []
    first: int | None = None  # the open paragraph or list item
    in_quote = in_item = False  # what the open block is
    offset = 0  # where an open list item's text starts
    fenced = in_table = in_list = False

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
            if "-->" not in raw:  # to the comment's end, if it has one: an unclosed comment hides nothing
                index = next((at for at in range(index + 1, len(lines)) if "-->" in lines[at]), index)
            continue
        if heading := _HEADING.match(raw):
            close(index - 1)
            in_table = in_list = False
            found.append(Block(index, index, heading.group(1).strip(), "heading"))
            continue
        if first is not None and not (in_item or in_quote or cut) and _UNDERLINE.fullmatch(raw):  # a Setext heading
            found.append(Block(first, index, " ".join(line.strip() for line in lines[first:index]), "heading"))
            first, in_table, in_list = None, False, False
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
            if not ((cut > 0 and not in_quote) or _LINK_DEFINITION.match(content.strip())) and (
                    (in_item and indent >= offset) or _is_wrapped(previous[_quote_end(previous):], content)):
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


def normalize_memory(text: str) -> str:
    """Drop one leading U+FEFF and turn CR and CRLF into LF. Idempotent."""
    if text.startswith("\ufeff"):
        text = text[1:]
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _read(lines: list[str], start: int, links: list[str]) -> Iterator[tuple[str, int, str | None]]:
    """(claim text, line, section) for each claim, in file order; ``links`` receives the local link targets seen."""
    section = None
    for block in blocks(lines, start):
        if block.kind == "heading":
            section = block.raw
            continue
        targets = _local_md_targets(block.raw)
        links.extend(targets)
        if targets and _is_index_entry(block.raw):
            continue
        for sentence, at, end in block.sentences():
            if not (targets and _local_md_targets(block.raw[at:end])):  # that sentence points at claims
                yield sentence, block.first + block.raw.count("\n", 0, at) + 1, section


def parse_text(text: str, display: str, fmt: MemoryFormat = MARKDOWN) -> tuple[list[Claim], list[str]]:
    """Claims and local link targets of one memory file.

    A single leading U+FEFF is ignored. CR, CRLF and LF are line breaks. Frontmatter is skipped, but lines are numbered
    from the top of the file, so ``path:line`` points at the line where the claim starts in the file as it is. The lines
    of a wrapped paragraph or list item are read as one (see ``blocks``), so a claim can run over several lines."""
    text = normalize_memory(text)
    lines = text.split("\n")
    start, fields = frontmatter(lines)
    metadata = metadata_of(fmt, fields)
    claims, links = [], []
    for sentence, line, section in _read(lines, start, links):
        if not take("claims"):  # within read_limits only: the rest of the file is not parsed
            return claims, links
        claims.append(Claim(id=claim_id(sentence), text=sentence, path=display, line=line, section=section,
                            metadata=dict(metadata)))
    return claims, links


def claim_ids(text: str) -> set[str]:
    """The IDs of the claims in a memory file's text, outside any read budget: checking a patch is not a read."""
    lines = normalize_memory(text).split("\n")
    return {claim_id(sentence) for sentence, _, _ in _read(lines, frontmatter(lines)[0], [])}


def merge_duplicates(claims: list[Claim]) -> list[Claim]:
    """One claim per ID, in first-seen order; later copies are listed in ``also_at``."""
    unique: dict[str, Claim] = {}
    for claim in claims:
        if claim.id in unique:
            unique[claim.id].also_at.append(claim.location)
        else:
            unique[claim.id] = claim
    return list(unique.values())


def _parse_file(root: Path, path: Path, display: str) -> tuple[list[Claim], list[str]]:
    try:
        text = read_input_text(root, path, instructions=True)
    except UnicodeDecodeError as exc:
        raise ValueError(f"{display}: not UTF-8 text") from exc
    return parse_text(text, display, detect(path))


def parse_memory(memory_file: Path, follow_links: bool = True, *,
                 skipped: list[Skipped] | None = None) -> tuple[list[Claim], list[Path]]:
    """Return (claims, files read). Links and ``@file.md`` imports are followed one level deep and never
    outside the memory directory."""
    # Choose the root BEFORE resolving the file: AGENTS.md -> .git/config is not a .git-rooted memory.
    original = Path(memory_file).absolute()
    root = original.parent.resolve()
    walked = root / original.name
    try:
        memory_file = input_path(root, walked, instructions=True)
        claims, links = _parse_file(root, memory_file, original.name)
    except InputPathError as exc:
        if confinement_root() is not None and str(exc) == "path escapes root":
            raise  # MCP validation/read races remain errors, never a successful empty result.
        if skipped is not None:
            skipped.append(Skipped(path=original.name, reason=str(exc)))
        return [], []
    files = [memory_file]
    if follow_links:
        for target in dict.fromkeys(links):
            try:
                if Path(target).is_absolute():
                    raise InputPathError("path escapes memory directory")
                path = input_path(root, root / target, instructions=True)
            except InputPathError as exc:
                if skipped is not None:
                    skipped.append(Skipped(path=target, reason=str(exc)))
                continue
            if path.is_file() and path not in files:
                files.append(path)
                try:
                    claims.extend(_parse_file(root, path, path.relative_to(root).as_posix())[0])
                except InputPathError as exc:
                    files.remove(path)
                    if skipped is not None:
                        skipped.append(Skipped(path=target, reason=str(exc)))
    return merge_duplicates(claims), files
