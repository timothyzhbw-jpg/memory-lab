"""Machinery shared by the platform importers (GitHub, Jira). Nothing here knows a platform.

- Dataset paths are resolved inside the dataset and never name `policy.json` (importers are policy-blind).
- One importer writes at a time: each holds its own lock file and refuses to run while another importer's
  lock exists.
- Source files are created once, content-addressed, and never overwritten.
- A source file is an ASCII fact line, an ASCII-escaped JSON record, a fixed separator and the untrusted
  text. Quote spans are computed by construction, never by searching the text.
"""
from __future__ import annotations

import json
import os
import re
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from memcore import ManifestEntry
from memcore.evidence import resolve_inside

UNSAFE = re.compile(r"[\x00-\x08\x0a-\x1f\x7f-\x9f\u200b-\u200f\u2028-\u202e\u2060-\u2069\ufeff\ufff9-\ufffb]")
SURROGATE = re.compile(r"[\ud800-\udfff]")
LOCKS = (".github-sync.lock", ".jira-sync.lock")
LOCK_HELD = "another sync is running; delete the lock if not"


@dataclass(frozen=True)
class Capture:
    """One rendered source file and the manifest entries that quote it."""
    path: str
    data: bytes
    entries: tuple[ManifestEntry, ...]


def dataset_path(dataset: Path, relative: str, importer: str) -> Path:
    """Refuse policy aliases as well as escapes, before opening anything."""
    path = resolve_inside(dataset, relative)
    if path == (dataset / "policy.json").resolve():
        raise ValueError(f"{importer} importer cannot open policy.json")
    return path


@contextmanager
def importer_lock(dataset: Path, name: str, importer: str) -> Iterator[None]:
    """Create this importer's lock, then make sure no other importer holds one.

    Creating first and checking second means two importers that start together both give up, never both run.
    """
    path = dataset_path(dataset, name, importer)
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        raise ValueError(LOCK_HELD) from None
    os.close(fd)
    try:
        if any(dataset_path(dataset, other, importer).exists() for other in LOCKS if other != name):
            raise ValueError(LOCK_HELD)
        yield
    finally:
        path.unlink()


def create_only(path: Path, data: bytes) -> None:
    """Private create-only temporary file, then atomic rename under the dataset lock."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        if path.exists():
            if path.read_bytes() != data:
                raise ValueError("content-addressed capture tamper conflict")
        else:
            os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def render(fact: str, record: dict, verbatim: str, separator: str) -> tuple[str, int]:
    """Capture text: fact line, ASCII JSON record, separator, verbatim section. Returns (text, verbatim offset)."""
    if not fact.isascii() or "\n" in fact:
        raise ValueError("a fact line must be a single ASCII line")
    clean = SURROGATE.sub("\ufffd", verbatim)
    if clean != verbatim:
        record = {**record, "lossy": True}
    head = f"{fact}\n{json.dumps(record, sort_keys=True, ensure_ascii=True, separators=(',', ':'))}\n{separator}\n"
    return head + clean, len(head)


def text_span(verbatim: str) -> tuple[int, int] | None:
    """Offsets of the first meaningful line, trimmed and capped at 200 characters; None if unsafe to quote."""
    pos = 0
    for line in verbatim.split("\n"):
        body = line.strip()
        if body and not body.startswith("<!--"):
            if UNSAFE.search(line.removesuffix("\r")):
                return None
            start = pos + len(line) - len(line.lstrip())
            return start, start + len(body[:200].rstrip())
        pos += len(line) + 1
    return None
