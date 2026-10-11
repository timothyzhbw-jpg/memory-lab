"""Machinery shared by the platform importers (GitHub, Jira). Nothing here knows a platform.

- Dataset paths are resolved inside the dataset and never name `policy.json` or `policy-log.jsonl`
  (importers are policy-blind).
- One importer writes at a time. On POSIX each holds an exclusive lock on the dataset directory itself, so
  deleting or replacing a marker cannot admit a second writer. Each also creates its own lock file and
  refuses to run while another importer's lock file exists.
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

from .authority import POLICY_LOG

UNSAFE = re.compile(r"[\x00-\x08\x0a-\x1f\x7f-\x9f\u200b-\u200f\u2028-\u202e\u2060-\u2069\ufeff\ufff9-\ufffb]")
SURROGATE = re.compile(r"[\ud800-\udfff]")
LOCKS = (".github-sync.lock", ".jira-sync.lock")
LOCK_HELD = "another sync is running; delete the lock if not"
POLICY_FILES = ("policy.json", POLICY_LOG)
# Directory locks need flock and dir_fd; tests switch them off to exercise the marker files alone.
DIRECTORY_LOCKS = os.name == "posix"


@dataclass(frozen=True)
class Capture:
    """One rendered source file and the manifest entries that quote it."""
    path: str
    data: bytes
    entries: tuple[ManifestEntry, ...]


def policy_alias(dataset: Path, path: Path) -> str | None:
    """The policy file ``path`` names (directly or through a symlink), or None."""
    resolved = Path(path).resolve()
    return next((name for name in POLICY_FILES if resolved == (Path(dataset) / name).resolve()), None)


def dataset_path(dataset: Path, relative: str, importer: str) -> Path:
    """Refuse policy aliases as well as escapes, before opening anything."""
    path = resolve_inside(dataset, relative)
    if name := policy_alias(dataset, path):
        raise ValueError(f"{importer} importer cannot open {name}")
    return path


@contextmanager
def importer_lock(dataset: Path, name: str, importer: str) -> Iterator[None]:
    """Lock the dataset directory (POSIX), create this importer's lock file, then make sure no other
    importer's lock file exists.

    Creating first and checking second means two importers that start together both give up, never both run,
    where directory locks are unavailable. The marker is created without following a symlink, and removed only
    if it is still the file this importer created; a marker deleted meanwhile is not an error.
    """
    dataset = Path(dataset).resolve()
    if name in POLICY_FILES:
        raise ValueError(f"{importer} importer cannot open {name}")
    path = dataset / name
    directory_fd, fd = None, None
    try:
        if DIRECTORY_LOCKS:
            import fcntl
            # Lock the directory inode: deleting/replacing the marker cannot admit a second writer.
            directory_fd = os.open(dataset, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                fcntl.flock(directory_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise ValueError(LOCK_HELD) from None
        try:
            fd = os.open(path.name if directory_fd is not None else path,
                         os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600,
                         **({"dir_fd": directory_fd} if directory_fd is not None else {}))
        except FileExistsError:
            raise ValueError(LOCK_HELD) from None
        if any((dataset / other).exists() or (dataset / other).is_symlink() for other in LOCKS if other != name):
            raise ValueError(LOCK_HELD)
        # On Windows, retaining this descriptor prevents deletion of the marker.
        yield
    finally:
        try:
            if fd is not None:
                held = os.fstat(fd)
                os.close(fd)
                try:
                    current = os.stat(path.name, dir_fd=directory_fd, follow_symlinks=False) \
                        if directory_fd is not None else path.lstat()
                    if os.path.samestat(current, held):
                        if directory_fd is not None:
                            os.unlink(path.name, dir_fd=directory_fd)
                        else:
                            path.unlink(missing_ok=True)
                except FileNotFoundError:
                    pass
        finally:
            if directory_fd is not None:
                os.close(directory_fd)


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
