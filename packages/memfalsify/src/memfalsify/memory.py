"""Split MEMORY.md (and the local .md files it links to) into individually auditable claims."""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

from pydantic import BaseModel

from memcore import EvidenceError, resolve_inside
from memcore.reading import read_file_text

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


def sentences(raw: str) -> list[tuple[str, int, int]]:
    """(claim text, raw start, raw end) for each claim-sized sentence of one memory line.

    The text has links and emphasis removed; the raw span covers the same sentence with its markup.
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
    return found


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


def _parse_file(path: Path, display: str) -> tuple[list[Claim], list[str]]:
    return parse_text(read_file_text(path), display)


def parse_memory(memory_file: Path, follow_links: bool = True) -> tuple[list[Claim], list[Path]]:
    """Return (claims, files read). Links are followed one level deep and never outside the memory directory."""
    memory_file = Path(memory_file).resolve()
    root = memory_file.parent
    claims, links = _parse_file(memory_file, memory_file.name)
    files = [memory_file]
    if follow_links:
        for target in dict.fromkeys(links):
            try:
                path = resolve_inside(root, target)
            except EvidenceError:
                continue
            if path.is_file() and path not in files:
                files.append(path)
                claims.extend(_parse_file(path, path.relative_to(root).as_posix())[0])
    return merge_duplicates(claims), files
