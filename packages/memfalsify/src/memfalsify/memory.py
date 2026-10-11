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


def _parse_file(path: Path, display: str) -> tuple[list[Claim], list[str]]:
    lines = read_file_text(path).split("\n")
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
        cleaned = EMPHASIS.sub("", WIKILINK.sub(r"\1", LINK.sub(r"\1", raw)))
        for start, end in sentence_spans(cleaned):
            text = cleaned[start:end]
            words, _ = features(text)
            if len(words) >= 2:
                claims.append(Claim(id=claim_id(text), text=text, path=display,
                                    line=index + 1, section=section))
    return claims, links


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
    unique: dict[str, Claim] = {}
    for claim in claims:
        if claim.id in unique:
            unique[claim.id].also_at.append(claim.location)
        else:
            unique[claim.id] = claim
    return list(unique.values()), files
