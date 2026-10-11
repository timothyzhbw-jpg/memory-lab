"""Line-oriented JSON storage: one record per line, blank lines ignored."""
from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

from .jsonutil import loads
from .reading import open_text


def read_jsonl(path: Path) -> Iterator[tuple[int, dict]]:
    """Yield (line number, object). Malformed lines, including a repeated key, raise ValueError naming the line."""
    with open_text(path) as handle:
        for number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                record = loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{number}: invalid JSON: {exc.msg}") from exc
            if not isinstance(record, dict):
                raise ValueError(f"{path}:{number}: expected a JSON object")
            yield number, record


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
