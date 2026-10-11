"""Line-oriented JSON storage: one record per line, blank lines ignored."""
from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

from .reading import open_text


def load_json(data: str | bytes):
    """Parse one JSON value.

    Malformed JSON, bytes that are not UTF-8, and values nested too deeply for the
    parser all raise ``ValueError``. A ``ValueError`` from an over-long integer is
    left as it is. ``JSONDecodeError`` is a ``ValueError``, so it is handled before
    any broader ``ValueError`` handler.
    """
    try:
        return json.loads(data)
    except RecursionError as exc:
        raise ValueError("invalid JSON: too deeply nested") from exc
    except UnicodeDecodeError as exc:
        raise ValueError("invalid JSON: not UTF-8 text") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON: {exc.msg}") from exc


def read_jsonl(path: Path) -> Iterator[tuple[int, dict]]:
    """Yield (line number, object). Malformed lines raise ValueError naming the line.

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
    path = Path(path)
    prefix = b""
    if path.exists() and path.stat().st_size:
        with path.open("rb") as handle:
            handle.seek(-1, 2)
            prefix = b"" if handle.read(1) == b"\n" else b"\n"
    with path.open("ab") as handle:
        handle.write(prefix + (json.dumps(record, ensure_ascii=False) + "\n").encode("utf-8"))
