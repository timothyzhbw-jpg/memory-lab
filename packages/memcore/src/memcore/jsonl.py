"""Line-oriented JSON storage: one record per line, blank lines ignored."""
from __future__ import annotations

import json
import os
import stat
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from .jsonutil import loads
from .reading import _directory, confinement_supported, open_text


def load_json(data: str | bytes) -> Any:
    """Parse one JSON value.

    Malformed JSON, a repeated object key, bytes that are not UTF-8, and values nested
    too deeply for the parser all raise ``ValueError``. A ``ValueError`` from an
    over-long integer is left as it is. ``JSONDecodeError`` is a ``ValueError``, so it
    is handled before any broader ``ValueError`` handler.
    """
    try:
        return loads(data)
    except RecursionError as exc:
        raise ValueError("invalid JSON: too deeply nested") from exc
    except UnicodeDecodeError as exc:
        raise ValueError("invalid JSON: not UTF-8 text") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON: {exc.msg}") from exc


def read_jsonl(path: Path) -> Iterator[tuple[int, dict]]:
    """Yield (line number, object). Malformed lines, including a repeated key, raise ValueError naming the line.

    A file that is not UTF-8 raises ``ValueError`` naming the path. Nesting that
    overflows the JSON parser does too, instead of ``RecursionError``.
    """
    try:
        with open_text(path) as handle:
            for number, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                try:
                    record = load_json(line)
                except ValueError as exc:
                    raise ValueError(f"{path}:{number}: {exc}") from exc
                if not isinstance(record, dict):
                    raise ValueError(f"{path}:{number}: expected a JSON object")
                yield number, record
    except UnicodeDecodeError as exc:
        raise ValueError(f"{path}: not UTF-8 text") from exc


def append_jsonl(path: Path, record: dict) -> None:
    """Append one UTF-8/LF record. Never rewrites existing bytes."""
    # Open every component without following links, including the final filename.
    path = Path(path).absolute()
    parent = path.parent
    payload = (json.dumps(record, ensure_ascii=False) + "\n").encode("utf-8")
    flags = os.O_RDWR | os.O_CREAT | os.O_APPEND | getattr(os, "O_BINARY", 0)
    directory_fd = None
    if confinement_supported():
        anchor = os.open(parent.anchor, os.O_RDONLY | os.O_DIRECTORY)
        try:
            directory_fd = _directory(parent.parts[1:], anchor)
        finally:
            os.close(anchor)
        flags |= os.O_NOFOLLOW | os.O_NONBLOCK
    else:
        if any(part.is_symlink() for part in (path, *path.parents)):
            raise ValueError("JSONL append target is a symlink")
    try:
        fd = os.open(path.name if directory_fd is not None else parent / path.name,
                     flags, 0o666, **({"dir_fd": directory_fd} if directory_fd is not None else {}))
    finally:
        if directory_fd is not None:
            os.close(directory_fd)
    try:
        current = os.fstat(fd)
        if not stat.S_ISREG(current.st_mode):
            raise ValueError("JSONL append target is not a regular file")
        if not confinement_supported():
            named = (parent / path.name).lstat()
            if (any(part.is_symlink() for part in path.parents)
                    or not stat.S_ISREG(named.st_mode) or not os.path.samestat(named, current)):
                raise ValueError("JSONL append target changed or is a symlink")
        # Use this same pinned descriptor to inspect the separator and append the record.
        prefix = b""
        if current.st_size:
            os.lseek(fd, -1, os.SEEK_END)
            prefix = b"" if os.read(fd, 1) == b"\n" else b"\n"
        with os.fdopen(fd, "r+b") as handle:
            fd = None
            handle.write(prefix + payload)
    finally:
        if fd is not None:
            os.close(fd)
