"""Index a directory of evidence files into exact, hashed sentence spans."""
from __future__ import annotations

from collections.abc import Iterable
from fnmatch import fnmatchcase
from pathlib import Path

from pydantic import BaseModel

from memcore import EvidenceSpan, make_span, sha256_bytes
from memcore.reading import read_file_bytes, walk_files

from .text import features, sentence_spans

MEDIA = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".heic", ".bmp", ".tiff", ".mp3", ".wav", ".m4a",
         ".flac", ".ogg", ".mp4", ".mov", ".webm", ".mkv", ".pdf"}
SKIP_DIRS = {"node_modules", "__pycache__", "venv"}
MAX_BYTES = 2_000_000
REPORT_HEADER = "# MemFalsify audit report"
DIFF_HEADER = "# MemFalsify diff report"
HISTORY_HEADER = "# MemFalsify claim history"
ROLLBACK_HEADER = "# MemFalsify rollback patch"
# Anything MemFalsify prints quotes memory; none of it may count as evidence for that memory.
OUTPUT_HEADERS = (REPORT_HEADER, DIFF_HEADER, HISTORY_HEADER, ROLLBACK_HEADER)


class Passage(BaseModel):
    span: EvidenceSpan
    words: frozenset[str]
    numbers: frozenset[str]


class Skipped(BaseModel):
    path: str
    reason: str


def _matches(relative: str, patterns: list[str], directory: bool = False) -> bool:
    """fnmatch against the root-relative POSIX path; a directory also matches as ``dir/`` so ``dir/*`` prunes it."""
    names = (relative, relative + "/") if directory else (relative,)
    return any(fnmatchcase(name, pattern) for name in names for pattern in patterns)


def scan(root: Path, exclude: Iterable[str] = (),
         memory_files: Iterable[Path] = ()) -> tuple[list[Passage], list[Skipped]]:
    """Index ``root``. Files matching an ``exclude`` glob and the memory files themselves are silently left out."""
    root = Path(root).resolve()
    patterns = [p.strip().removeprefix("./") for p in exclude if p.strip()]
    excluded = {Path(p).resolve() for p in memory_files}
    passages, skipped = [], []

    def walk_error(error: OSError) -> None:
        # An inaccessible subtree may contain counterevidence: fail closed.
        raise error

    for directory, dirnames, filenames in walk_files(root, onerror=walk_error):
        base = Path(directory).relative_to(root)
        dirnames[:] = sorted(d for d in dirnames if not d.startswith(".") and d not in SKIP_DIRS
                             and not _matches((base / d).as_posix(), patterns, directory=True))
        for name in dirnames[:]:
            if (Path(directory) / name).is_symlink():
                skipped.append(Skipped(path=(base / name).as_posix(), reason="symlinked directory not followed"))
                dirnames.remove(name)
        for name in sorted(filenames):
            if name.startswith("."):
                continue
            path = Path(directory) / name
            relative = path.relative_to(root).as_posix()
            if _matches(relative, patterns):
                continue
            try:
                resolved = path.resolve()
            except (OSError, RuntimeError):
                if not path.is_symlink():
                    raise
                skipped.append(Skipped(path=relative, reason="broken or cyclic symlink"))
                continue
            if resolved in excluded:
                continue
            if not resolved.is_relative_to(root):
                skipped.append(Skipped(path=relative, reason="symlink escapes evidence root"))
                continue
            if not resolved.is_file():
                broken = path.is_symlink() and not resolved.exists()
                skipped.append(Skipped(path=relative, reason="broken or cyclic symlink" if broken else "not a regular file"))
                continue
            if path.suffix.lower() in MEDIA:
                skipped.append(Skipped(path=relative, reason="media file: multimodal extraction not implemented"))
                continue
            raw = read_file_bytes(resolved)
            if len(raw) > MAX_BYTES:
                skipped.append(Skipped(path=relative, reason="larger than 2 MB"))
                continue
            try:
                if b"\x00" in raw:
                    raise UnicodeDecodeError("utf-8", raw, 0, 1, "binary")
                text = raw.decode("utf-8")
            except UnicodeDecodeError:
                skipped.append(Skipped(path=relative, reason="not UTF-8 text"))
                continue
            if text.startswith(OUTPUT_HEADERS) or text.lstrip().startswith('{\n  "tool": "memfalsify"'):
                skipped.append(Skipped(path=relative, reason="MemFalsify report: a report cannot be its own evidence"))
                continue
            digest = sha256_bytes(raw)
            for start, end in sentence_spans(text):
                words, numbers = features(text[start:end])
                if words:
                    passages.append(Passage(span=make_span(relative, digest, text, start, end,
                                                           source_kind="file"),
                                            words=words, numbers=numbers))
    return passages, skipped
