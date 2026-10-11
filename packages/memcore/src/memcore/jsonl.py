"""Line-oriented JSON storage: one record per line, blank lines ignored."""
from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path


def read_jsonl(path: Path) -> Iterator[tuple[int, dict]]:
    """Yield (line number, object). Malformed lines raise ValueError naming the line."""
    with Path(path).open(encoding="utf-8") as handle:
        for number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{number}: invalid JSON: {exc.msg}") from exc
            if not isinstance(record, dict):
                raise ValueError(f"{path}:{number}: expected a JSON object")
            yield number, record


def append_jsonl(path: Path, record: dict) -> None:
    """Append one record. Never rewrites existing lines."""
    path = Path(path)
    prefix = ""
    if path.exists() and path.stat().st_size:
        with path.open("rb") as handle:
            handle.seek(-1, 2)
            prefix = "" if handle.read(1) == b"\n" else "\n"
    with path.open("a", encoding="utf-8") as handle:
        handle.write(prefix + json.dumps(record, ensure_ascii=False) + "\n")
