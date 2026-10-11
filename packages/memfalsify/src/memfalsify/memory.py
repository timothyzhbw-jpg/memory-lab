"""Split MEMORY.md (and the local .md files it links to) into individually auditable claims."""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

from pydantic import BaseModel

from memcore.paths import InputPathError, input_path
from memcore.reading import confinement_root, read_input_text

from .corpus import Skipped
from .text import features, sentence_spans

LINK = re.compile(r"!?\[([^\]]*)\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
WIKILINK = re.compile(r"\[\[([^\]]+)\]\]")
EMPHASIS = re.compile(r"\*\*|__|`")
FENCE = re.compile(r"^\s*(```|~~~)")


class Claim(BaseModel):
    id: str
    text: str
    path: str
    line: int
    section: str | None = None
    also_at: list[str] = []

    @property
    def location(self) -> str:
        return f"{self.path}:{self.line}"


def claim_id(text: str) -> str:
    """Stable ID from normalized text. Editing a memory changes its ID, so old reviews no longer apply."""
    normalized = " ".join(text.split()).casefold()
    return "m-" + hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:8]


def _strip_frontmatter(lines: list[str]) -> int:
    if lines and lines[0].strip() == "---":
        for index in range(1, len(lines)):
            if lines[index].strip() == "---":
                return index + 1
    return 0


def _local_md_targets(line: str) -> list[str]:
    targets = [m.group(2).split("#")[0] for m in LINK.finditer(line)]
    return [t for t in targets if t.endswith(".md") and "://" not in t]


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


def sentences(raw: str) -> list[tuple[str, int, int]]:
    """(claim text, raw start, raw end) for each claim-sized sentence of one memory line.

    The text has links and emphasis removed. The raw span covers that sentence plus markup that wraps
    it alone; markup shared with another sentence on the line stays outside the span.
    """
    cleaned, origin = _strip(LINK, raw, list(range(len(raw))), 1)
    cleaned, origin = _strip(WIKILINK, cleaned, origin, 1)
    cleaned, origin = _strip(EMPHASIS, cleaned, origin, None)
    found = []
    for start, end in sentence_spans(cleaned):
        text = cleaned[start:end]
        words, _ = features(text)
        if len(words) >= 2:
            found.append((text, origin[start], origin[end - 1] + 1))
    content = [(start, end) for _, start, end in found]
    return [(text, *_expand_markup(raw, start, end, content[:i] + content[i + 1:]))
            for i, (text, start, end) in enumerate(found)]


def parse_text(text: str, display: str) -> tuple[list[Claim], list[str]]:
    """Claims and local link targets of one memory file's text (newlines already normalized to LF)."""
    lines = text.split("\n")
    claims, links, section, fenced = [], [], None, False
    for index in range(_strip_frontmatter(lines), len(lines)):
        raw = lines[index]
        if FENCE.match(raw):
            fenced = not fenced
            continue
        if fenced or not raw.strip() or raw.lstrip().startswith("<!--"):
            continue
        heading = re.match(r"^\s*#{1,6}\s+(.*)", raw)
        if heading:
            section = heading.group(1).strip()
            continue
        targets = _local_md_targets(raw)
        if targets:
            # An index entry ("- [Title](file.md) — hook") points at claims; it is not one itself.
            links.extend(targets)
            continue
        claims += [Claim(id=claim_id(sentence), text=sentence, path=display, line=index + 1, section=section)
                   for sentence, _, _ in sentences(raw)]
    return claims, links


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
    return parse_text(read_input_text(root, path), display)


def parse_memory(memory_file: Path, follow_links: bool = True, *,
                 skipped: list[Skipped] | None = None) -> tuple[list[Claim], list[Path]]:
    """Return (claims, files read). Links are followed one level deep and never outside the memory directory."""
    # Choose the root BEFORE resolving the file: AGENTS.md -> .git/config is not a .git-rooted memory.
    original = Path(memory_file).absolute()
    root = original.parent.resolve()
    walked = root / original.name
    try:
        memory_file = input_path(root, walked)
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
                path = input_path(root, root / target)
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
