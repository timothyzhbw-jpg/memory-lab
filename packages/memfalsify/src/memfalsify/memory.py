"""Split a memory or agent instruction file (and the local .md files it links to) into auditable claims.

MEMORY.md, CLAUDE.md, AGENTS.md, GEMINI.md and the Cursor, Copilot and Windsurf rule files are all Markdown
once their frontmatter is removed (see ``formats``); the fields a format documents become claim metadata.
"""
from __future__ import annotations

import hashlib
import re
from bisect import bisect_left, bisect_right
from collections.abc import Iterator
from itertools import chain
from pathlib import Path

from pydantic import BaseModel

from memcore.paths import InputPathError, input_path
from memcore.reading import confinement_root, read_input_text, take

from .blocks import LINK_DEFINITION, Block, blocks
from .corpus import Skipped
from .formats import MARKDOWN, MemoryFormat, Value, detect, frontmatter, metadata_of
from .text import CJK, features, sentence_spans, whole_span

# Bounded, so that a long run of ``[`` or ``[a](`` takes each start a short look and not the rest of the file. An image
# as a data URL can be longer than any address, and it stops at the next ``[``, which it never holds.
LINK = re.compile(r"!?\[([^\]]{0,1000})\]\((data:[^)\s\[]*|[^)\s]{1,2000})(?:\s+\"[^\"]{0,1000}\")?\)")
WIKILINK = re.compile(r"\[\[([^\]]{1,1000})\]\]")
EMPHASIS = re.compile(r"\*\*|__|`")
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


def _expand_markup(markup: list[tuple[int, int]], start: int, end: int, neighbors: tuple[int, int]) -> tuple[int, int]:
    """Grow a content span to the link or emphasis that wraps it, and stop at a neighbor's content. ``markup`` is the
    span of every link and emphasis marker around it, in order: going both ways, a long run of markers takes few passes.
    ``neighbors``: where the sentence before it ends, and where the one after it starts.

    A match that also overlaps or touches another sentence (``[A. B.](url)``, or ``****`` between two
    sentences) is left outside. Rollback substitutes this span, so taking the shared markup would keep
    the neighbor's URL or steal its marker.
    """
    before, after = neighbors
    while True:
        grew = False
        for ms, me in chain(markup, reversed(markup)):
            if me != start and ms != end and not (ms < end and me > start):
                continue
            if ms <= before or me >= after:
                continue
            new_s, new_e = min(start, ms), max(end, me)
            if new_s < start or new_e > end:
                start, end = new_s, new_e
                grew = True
        if not grew:
            return start, end


_WRAP = re.compile(r"\n[ \t]*(?:>[ \t]*)*")
_NO_SPACE = re.compile("[\u3400-\u9fff\u3000-\u303f\uff00-\uffef]")  # Chinese characters and punctuation


def _unwrap(text: str, origin: list[int]) -> tuple[str, list[int]]:
    """One line from a block's lines: a line break and what continues the block (indent, quote markers) read as one
    space, or as nothing between two Chinese characters, which take no space."""
    out, where, last = [], [], 0
    for match in _WRAP.finditer(text):
        before = len(text[last:match.start()].rstrip(" \t")) + last  # any spaces that end the line go too
        out.append(text[last:before])
        where += origin[last:before]
        if not (_NO_SPACE.match(text[before - 1:before]) and _NO_SPACE.match(text[match.end():match.end() + 1])):
            out.append(" ")
            where.append(origin[match.start()])
        last = match.end()
    out.append(text[last:])
    where += origin[last:]
    return "".join(out), where


def on_one_line(raw: str) -> str:
    """A raw sentence with its line breaks read as ``_unwrap`` reads them."""
    return _unwrap(raw, list(range(len(raw))))[0]


_TAG = re.compile(r"</?[A-Za-z][^<>]*>")
_LABEL_OPENS = re.compile(r"(?:when|while|before|after|if|for|to|in|on|with|once|or|and|then|here|examples?|note)\b", re.I)
# What makes a line ending in a colon an instruction, however it opens: ``Never use yarn:``.
_INSTRUCTS = re.compile(r"\b(?:must|shall|should|never|always|cannot|do not|don't|does not|doesn't)\b", re.I)


def is_fragment(text: str) -> bool:
    """Whether a sentence of a block says nothing by itself.

    Deliberately narrow, since a dropped claim is never checked: Chinese text always stays, and so does anything that
    reads as a sentence, such as ``Tests use pytest.``, or an instruction that names a tag: ``Prefer <Link> over <a>.``
    What goes: markup with nothing else (a closing tag, a tag around one word, a link definition) and a label ending in a
    colon (``Run ty:``, ``When writing comments:``), unless it instructs. A whole list item stays even with no verb:
    ``pnpm workspaces`` under ``Stack`` is a fact.
    """
    if CJK.search(text):
        return False
    bare = features(_TAG.sub(" ", text))[0]
    if LINK_DEFINITION.fullmatch(text) or not bare or (len(bare) == 1 and (text.startswith("<") or "</" in text)):
        return True
    words = text.split()
    return (text.endswith(":") and re.search(r"\d", text) is None and _INSTRUCTS.search(text) is None
            and (len(words) <= 3 or (_LABEL_OPENS.match(text) is not None and "," not in text and len(words) <= 12)))


def block_sentences(block: Block) -> list[tuple[str, int, int]]:
    return sentences(block.raw, row=block.kind == "row")


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
    for start, end in spans:
        text = cleaned[start:end]
        words, _ = features(text)
        if len(words) >= 2 and not is_fragment(text):
            found.append((text, origin[start], origin[end - 1] + 1))
    markup = sorted(match.span() for pattern in (LINK, WIKILINK, EMPHASIS) for match in pattern.finditer(raw)) if found else []
    starts = [span[0] for span in markup]
    expanded = []
    for i, (text, start, end) in enumerate(found):
        before = found[i - 1][2] if i else -1
        after = found[i + 1][1] if i + 1 < len(found) else len(raw) + 1
        # Sentences do not overlap, so what would reach past a neighbor reaches the neighbor: this one's own markup stands
        # between the two, and the work over a whole block stays in proportion to its markup.
        nearby = markup[bisect_right(starts, before):bisect_left(starts, after)]
        expanded.append((text, *_expand_markup(nearby, start, end, (before, after))))
    return expanded


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
        line, counted = block.first + 1, 0  # the sentences come in order, so a long block is counted through once
        for sentence, at, end in block_sentences(block):
            line += block.raw.count("\n", counted, at)
            counted = at
            if not (targets and _local_md_targets(block.raw[at:end])):  # that sentence points at claims
                yield sentence, line, section


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
