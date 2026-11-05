"""Index a directory of evidence files into exact, hashed spans.

Text files become sentence spans. Transcript and OCR sidecars (``memcore.sidecar``) become one span per cue
or OCR line, which also records its position in the media file. Media files themselves are never read as
text: they are hashed only to check the sidecars that describe them.
"""
from __future__ import annotations

import gc
import re
from bisect import bisect_left
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from fnmatch import fnmatchcase
from pathlib import Path, PurePath
from typing import TypeVar

from pydantic import BaseModel

from memcore import EvidenceSpan, MediaRef, SidecarError, load_sidecar, sha256_bytes, sidecar_format
from memcore.paths import InputPathError, input_path
from memcore.reading import ReadLimitError, available, read_input_bytes, take, walk_files
from memcore.sidecar import LABELS, MEDIA_SUFFIXES

from .text import features, sentence_spans

MEDIA = MEDIA_SUFFIXES
NO_SIDECAR = "media file: no usable transcript (.vtt, .srt) or OCR sidecar (.ocr.json) next to it"
MAX_BYTES = 2_000_000
BUDGET_SKIPPED = "not read: the read budget for this request is used up"
REPORT_HEADER = "# MemFalsify audit report"
DIFF_HEADER = "# MemFalsify diff report"
HISTORY_HEADER = "# MemFalsify claim history"
ROLLBACK_HEADER = "# MemFalsify rollback patch"
DISCOVER_HEADER = "# MemFalsify instruction files"
# Anything MemFalsify prints is about memory; none of it may count as evidence for that memory.
OUTPUT_HEADERS = (REPORT_HEADER, DIFF_HEADER, HISTORY_HEADER, ROLLBACK_HEADER, DISCOVER_HEADER)
# HTML reports start with a doctype and name MemFalsify as their generator at the top of <head>.
HTML_GENERATOR = "memfalsify"
HTML_MARKER = f'<meta name="generator" content="{HTML_GENERATOR}">'


def is_report(text: str) -> bool:
    """Whether ``text`` is MemFalsify output (Markdown, JSON or HTML)."""
    return (text.startswith(OUTPUT_HEADERS) or text.lstrip().startswith('{\n  "tool": "memfalsify"')
            or (text.startswith("<!DOCTYPE html>") and HTML_MARKER in text[:300]))


@dataclass(slots=True)
class Passage:
    """One indexed sentence or cue and its terms. A large repository has millions of them and a report cites a few,
    so this holds what an ``EvidenceSpan`` is made of and builds the span when asked for."""
    path: str
    digest: str
    quote: str
    start: int
    line: int
    media: MediaRef | None
    words: frozenset[str]
    numbers: frozenset[str]

    @property
    def location(self) -> str:
        return f"{self.path}:{self.line}"

    @property
    def span(self) -> EvidenceSpan:
        return EvidenceSpan(path=self.path, sha256=self.digest, start=self.start, end=self.start + len(self.quote),
                            quote=self.quote, line=self.line, source_kind="file", media=self.media)


class Skipped(BaseModel):
    path: str
    reason: str


def _matches(relative: str, patterns: list[str], directory: bool = False) -> bool:
    """fnmatch against the root-relative POSIX path; a directory also matches as ``dir/`` so ``dir/*`` prunes it."""
    names = (relative, relative + "/") if directory else (relative,)
    return any(fnmatchcase(name, pattern) for name in names for pattern in patterns)


def _newlines(text: str) -> list[int]:
    """Where the newlines are, for ``_passage``. Counting from the start of the file per passage is quadratic."""
    return [match.start() for match in re.finditer("\n", text)]


def _passage(relative: str, digest: str, text: str, newlines: list[int], start: int, end: int,
             media: MediaRef | None = None) -> Passage | None:
    quote = text[start:end]
    words, numbers = features(quote)
    if not words or not take("passages"):  # take: within read_limits only, a passage budget
        return None
    return Passage(relative, digest, quote, start, bisect_left(newlines, start) + 1, media, words, numbers)


def scan(root: Path, exclude: Iterable[str] = (),
         memory_files: Iterable[Path] = ()) -> tuple[list[Passage], list[Skipped]]:
    """Index ``root``. Files matching an ``exclude`` glob and the memory files themselves are silently left out.

    A media file is listed as skipped unless a sidecar bound to it was indexed. Sidecars that cannot be used
    (malformed, stale, unbound or ambiguous) are listed as skipped with the reason."""
    root = Path(root).resolve()
    patterns = [p.strip().removeprefix("./") for p in exclude if p.strip()]
    excluded = {Path(p).resolve() for p in memory_files}
    passages, skipped = [], []
    hashes: dict[Path, str] = {}

    def walk_error(error: OSError) -> None:
        # An inaccessible subtree may contain counterevidence: fail closed.
        raise error

    for directory, dirnames, filenames in walk_files(root, onerror=walk_error):
        base = Path(directory).relative_to(root)
        prefix = base.as_posix() + "/" if base.parts else ""
        dirnames[:] = sorted(d for d in dirnames
                             if not _matches((base / d).as_posix(), patterns, directory=True))
        for name in dirnames[:]:
            # Report pruned directories without enumerating their sensitive contents.
            try:
                input_path(root, Path(directory) / name, directory=True)
            except InputPathError as exc:
                # Preserve the existing diagnostic for untraversed directory symlinks.
                reason = "symlinked directory not followed" if str(exc) == "path escapes root" else str(exc)
                skipped.append(Skipped(path=(base / name).as_posix(), reason=reason))
                dirnames.remove(name)
                continue
            if (Path(directory) / name).is_symlink():
                skipped.append(Skipped(path=(base / name).as_posix(), reason="symlinked directory not followed"))
                dirnames.remove(name)
        # Media files are decided after their directory's sidecars, so entries stay in name order.
        local: list[Skipped] = []
        media: dict[str, str] = {}  # media file name -> relative path
        bound: set[str] = set()  # names of media files a sidecar was checked against and indexed for
        for name in sorted(filenames):
            relative = prefix + name
            if _matches(relative, patterns):
                continue
            path = Path(directory) / name
            try:
                resolved = input_path(root, path)
            except InputPathError as exc:
                reason = "symlink escapes evidence root" if str(exc) == "path escapes root" else str(exc)
                local.append(Skipped(path=relative, reason=reason))
                continue
            if resolved in excluded:
                continue
            if not resolved.is_file():
                broken = path.is_symlink() and not resolved.exists()
                local.append(Skipped(path=relative, reason="broken or cyclic symlink" if broken else "not a regular file"))
                continue
            if path.suffix.lower() in MEDIA:
                media[name] = relative
                local.append(Skipped(path=relative, reason=NO_SIDECAR))
                continue
            if not available("passages"):
                local.append(Skipped(path=relative, reason=BUDGET_SKIPPED))
                continue
            try:
                # The limit holds while reading: a larger file is refused after at most MAX_BYTES + 1 bytes.
                raw = read_input_bytes(root, resolved, max_bytes=MAX_BYTES)
            except InputPathError as exc:
                local.append(Skipped(path=relative, reason=str(exc)))
                continue
            except ReadLimitError as exc:
                local.append(Skipped(path=relative, reason=BUDGET_SKIPPED if exc.budget else "larger than 2 MB"))
                continue
            try:
                if b"\x00" in raw:
                    raise UnicodeDecodeError("utf-8", raw, 0, 1, "binary")
                text = raw.decode("utf-8")
            except UnicodeDecodeError:
                local.append(Skipped(path=relative, reason="not UTF-8 text"))
                continue
            if is_report(text):
                local.append(Skipped(path=relative, reason="MemFalsify report: a report cannot be its own evidence"))
                continue
            digest = sha256_bytes(raw)
            newlines = _newlines(text)
            fmt = sidecar_format(name)
            if fmt is not None:
                try:
                    sidecar = load_sidecar(root, relative, text, siblings=filenames, hashes=hashes)
                except SidecarError as exc:
                    local.append(Skipped(path=relative, reason=f"{LABELS[fmt]}: {exc}"))
                    continue
                if sidecar.parsed.note:
                    local.append(Skipped(path=relative, reason=f"{LABELS[fmt]}: {sidecar.parsed.note}"))
                if sidecar.media_sha256 is not None:
                    bound.add(PurePath(sidecar.media).name)
                passages += [p for unit in sidecar.units
                             if (p := _passage(relative, digest, text, newlines, unit.start, unit.end,
                                               sidecar.ref(unit)))]
                continue
            passages += [p for start, end in sentence_spans(text)
                         if (p := _passage(relative, digest, text, newlines, start, end))]
        covered = {media[name] for name in bound if name in media}
        skipped += [s for s in local if not (s.reason == NO_SIDECAR and s.path in covered)]
    return passages, skipped


T = TypeVar("T")


@contextmanager
def out_of_collection(build: Callable[[], T]) -> Iterator[T]:
    """``build()``, kept out of the cycle collector's sight for the length of a ``with`` block.

    Passages hold no reference cycles, and on a large repository walking millions of them again and again, to free
    nothing, took a quarter of the audit. So the collector stays off while they are built, and then they are frozen:
    it runs as usual for everything made after that, such as a model verifier's API client."""
    enabled = gc.isenabled()
    gc.disable()
    try:
        built = build()
        gc.freeze()
    finally:
        if enabled:
            gc.enable()
    try:
        yield built
    finally:
        gc.unfreeze()
