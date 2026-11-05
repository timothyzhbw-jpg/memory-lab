"""``memfalsify init``: write a small demo to audit. It runs offline and never overwrites anything.

The templates ship inside the wheel (``templates/demo/<lang>/``) and are copied as exact bytes. Every target is
checked before the first write, and files are opened in exclusive-create mode, so an existing file or a link
planted at a target stays as it is. If a write fails anyway, what this call created is removed again.
"""
from __future__ import annotations

import json
import os
from importlib import resources
from pathlib import Path

from .memory import claim_id

DEFAULT_DIRECTORY = "memfalsify-demo"
LANGUAGES = ("en", "zh")
FILES = ("MEMORY.md", "evidence/api.md", "evidence/runbook.md", "reviews.jsonl")
# The claim the demo contradicts, and the evidence sentence that contradicts it: the first review a person records.
_CONTRADICTED = {
    "en": ("The public API allows 100 requests per minute.", "The public API allows 60 requests per minute."),
    "zh": ("公开 API 每分钟最多允许 100 次请求。", "公开 API 每分钟最多允许 60 次请求。"),
}


def _template(lang: str, name: str) -> bytes:
    node = resources.files("memfalsify") / "templates" / "demo" / lang
    for part in name.split("/"):
        node = node / part
    return node.read_bytes()


def _refusal(directory: Path) -> list[str]:
    """The targets that are already taken. A link counts, even a broken one: writing through it would land elsewhere."""
    taken = [name for name in FILES if os.path.lexists(directory / name)]
    evidence = directory / "evidence"
    if os.path.lexists(evidence) and (evidence.is_symlink() or not evidence.is_dir()):
        taken.append("evidence")
    return taken


def _missing_directories(directory: Path) -> list[Path]:
    """``directory`` and the parents of it that do not exist yet, outermost first."""
    missing = []
    current = directory.absolute()
    while not os.path.lexists(current):
        missing.append(current)
        current = current.parent
    return missing[::-1]


def create_demo(directory: Path, lang: str = "en") -> list[str]:
    """Write the demo into ``directory`` and return the files written (POSIX, relative to it).

    Raises ``ValueError``, with nothing written, if ``lang`` is unknown, ``directory`` is not a directory or a
    target already exists. ``OSError`` from a failed write removes what this call created first."""
    if lang not in LANGUAGES:
        raise ValueError(f"unknown demo language {lang!r}: use " + " or ".join(LANGUAGES))
    directory = Path(directory)
    contents = {name: _template(lang, name) for name in FILES}
    if os.path.lexists(directory) and not directory.is_dir():
        raise ValueError(f"{directory} exists and is not a directory; nothing was written")
    if taken := _refusal(directory):
        raise ValueError(f"refusing to overwrite {', '.join(taken)} in {directory}; nothing was written. "
                         "Choose a new directory")
    made_directories = _missing_directories(directory)
    evidence = directory / "evidence"
    if not os.path.lexists(evidence):
        made_directories.append(evidence.absolute())
    written: list[Path] = []
    created: list[Path] = []
    try:
        for path in made_directories:
            path.mkdir()
            created.append(path)
        for name, data in contents.items():
            path = directory / name
            with open(path, "xb") as handle:  # exclusive: never over an existing file or a link
                written.append(path)
                handle.write(data)
    except FileExistsError:
        _remove(written, created)
        raise ValueError(f"a target appeared in {directory} while writing; nothing was kept") from None
    except OSError:
        _remove(written, created)
        raise
    return list(FILES)


def _remove(files: list[Path], directories: list[Path]) -> None:
    """Undo a failed ``create_demo``: only what it created, and a directory only if it is empty again."""
    for path in files:
        try:
            path.unlink()
        except OSError:
            pass
    for path in reversed(directories):
        try:
            path.rmdir()
        except OSError:
            pass


def review_line(lang: str = "en") -> str:
    """A ``reviews.jsonl`` record that confirms the demo's contradiction, ready to paste."""
    claim, quote = _CONTRADICTED[lang]
    return json.dumps({"memory_id": claim_id(claim), "relation": "contradicts", "path": "evidence/api.md",
                       "quote": quote, "reviewer": "you"}, ensure_ascii=False)
