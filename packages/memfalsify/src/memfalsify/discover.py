"""Find the instruction files of every supported agent in a directory tree.

Discovery reads directory listings only, never file contents. The locations searched are the ones each agent
documents (see ``formats`` for the sources). Unreadable directories fail the search rather than silently
hiding files; symlinks are listed only when they lead to a regular file inside the searched directory.
"""
from __future__ import annotations

import os
from collections.abc import Iterable
from pathlib import Path, PurePosixPath
from typing import Literal

from pydantic import BaseModel

from memcore.reading import walk_files

from . import __version__
from .corpus import DISCOVER_HEADER, SKIP_DIRS, Skipped, _matches
from .formats import detect
from .gitstore import CONTROL, printable, show_text

# Searched in every directory that is searched.
NAMES = ("AGENTS.md", "AGENTS.override.md", "CLAUDE.md", "CLAUDE.local.md", "GEMINI.md")
# Agent directories searched in every directory: (path, exact names, name suffix, search subdirectories).
NESTED = (
    ((".claude",), ("CLAUDE.md", "AGENTS.md"), None, False),
    ((".claude", "rules"), (), ".md", True),
    ((".cursor", "rules"), (), ".mdc", True),
    ((".windsurf", "rules"), (), ".md", False),
    ((".devin", "rules"), (), ".md", False),
)
# Only at the top of the searched directory (the repository root).
ROOT_NAMES = (".cursorrules", ".windsurfrules")
ROOT_NESTED = (
    ((".github",), ("copilot-instructions.md",), None, False),
    ((".github", "instructions"), (), ".instructions.md", True),
)

LIMITATIONS = [
    "Only the locations each agent documents are searched; names configured elsewhere (Gemini CLI "
    "`context.fileName`, Codex `project_doc_fallback_filenames`) are not.",
    "Files outside the directory, such as `~/.claude/CLAUDE.md`, `~/.gemini/GEMINI.md` or Windsurf's global "
    "rules, are not searched.",
    "Other hidden directories, `node_modules/`, `__pycache__/` and `venv/` are not searched, and nested git "
    "repositories (submodules, worktrees) are listed as skipped.",
    "Discovery reads the directory, not git's index: untracked and ignored files are listed too.",
]


class InstructionFile(BaseModel):
    path: str  # POSIX, relative to the searched directory
    format: str  # see memfalsify.formats, e.g. "cursor-rule"
    agent: str
    same_as: str | None = None  # another listed path naming the same file (a symlink or hard link)


class Discovery(BaseModel):
    tool: str = "memfalsify"
    version: str = __version__
    mode: Literal["read_only"] = "read_only"
    directory: str
    excluded: list[str] = []
    files: list[InstructionFile]
    skipped: list[Skipped]
    limitations: list[str]

    @property
    def distinct(self) -> list[str]:
        """The listed files, each file once (the paths to audit)."""
        return [f.path for f in self.files if f.same_as is None]


def discover(directory: Path, exclude: Iterable[str] = ()) -> Discovery:
    """The instruction files under ``directory``. ``exclude`` globs (fnmatch on POSIX paths relative to
    ``directory``; ``dir/**`` prunes a directory) leave files out silently."""
    root = Path(directory).resolve()
    if not root.is_dir():
        raise NotADirectoryError(f"not a directory: {directory}")
    patterns = [p.strip().removeprefix("./") for p in exclude if p.strip()]
    found: dict[str, Path] = {}
    skipped: list[Skipped] = []

    def fail(error: OSError) -> None:
        raise error  # an unreadable directory may hold instruction files: fail closed

    def skip(relative: PurePosixPath, reason: str) -> None:
        skipped.append(Skipped(path=printable(relative.as_posix()), reason=reason))

    def prune(here: Path, relative: PurePosixPath, dirnames: list[str]) -> None:
        kept = []
        for name in sorted(dirnames):
            if name.startswith(".") or name in SKIP_DIRS or _matches((relative / name).as_posix(), patterns, True):
                continue
            if (here / name).is_symlink():
                skip(relative / name, "symlinked directory not followed")
                continue
            kept.append(name)
        dirnames[:] = kept

    def consider(relative: PurePosixPath, path: Path) -> None:
        name = relative.as_posix()
        if _matches(name, patterns):
            return
        if CONTROL.search(name):
            skip(relative, "file name contains control characters")
            return
        try:
            resolved = path.resolve(strict=True)
        except (OSError, RuntimeError):
            skip(relative, "broken or cyclic symlink")
            return
        if not resolved.is_relative_to(root):
            skip(relative, "symlink leads outside the searched directory")
        elif not resolved.is_file():
            skip(relative, "not a regular file")
        else:
            found[name] = path

    def search(here: Path, relative: PurePosixPath, parts: tuple[str, ...], names: tuple[str, ...],
               suffix: str | None, recurse: bool) -> None:
        for part in parts:
            here, relative = here / part, relative / part
            if here.is_symlink() and here.is_dir():
                skip(relative, "symlinked directory not followed")
                return
            if not here.is_dir() or _matches(relative.as_posix(), patterns, True):
                return
        for current, dirnames, filenames in walk_files(here, onerror=fail):
            location = relative.joinpath(*Path(current).relative_to(here).parts)
            if recurse:
                prune(Path(current), location, dirnames)
            else:
                dirnames[:] = []
            for name in sorted(filenames):
                if name in names or (suffix and name.endswith(suffix)):
                    consider(location / name, Path(current) / name)

    for current, dirnames, filenames in walk_files(root, onerror=fail):
        here = Path(current)
        relative = PurePosixPath(*here.relative_to(root).parts)
        if relative.parts and ".git" in dirnames + filenames:
            skip(relative, "nested git repository (submodule or worktree) not searched")
            dirnames[:] = []
            continue
        prune(here, relative, dirnames)
        for name in sorted(filenames):
            if name in NAMES or (not relative.parts and name in ROOT_NAMES):
                consider(relative / name, here / name)
        for spec in NESTED + (ROOT_NESTED if not relative.parts else ()):
            search(here, relative, *spec)

    # Each file once: a real file is preferred over a link to it, then the first path in order.
    first: dict[tuple[int, int], str] = {}
    for name in sorted(found, key=lambda n: (found[n].is_symlink(), n)):
        status = os.stat(found[name])
        first.setdefault((status.st_dev, status.st_ino), name)
    files = []
    for name in sorted(found):
        status = os.stat(found[name])
        fmt = detect(PurePosixPath(name))
        same = first[(status.st_dev, status.st_ino)]
        files.append(InstructionFile(path=name, format=fmt.name, agent=fmt.agent,
                                     same_as=None if same == name else same))
    return Discovery(directory=str(directory), excluded=patterns, files=files, skipped=skipped,
                     limitations=LIMITATIONS)


def _cell(text: str) -> str:
    return show_text(text).replace("|", "\\|")


def discovery_to_markdown(report: Discovery) -> str:
    out = [DISCOVER_HEADER, "",
           f"- Directory: `{show_text(report.directory)}`",
           *(["- Excluded: " + ", ".join(f"`{show_text(p)}`" for p in report.excluded)] if report.excluded else []),
           f"- Mode: **{report.mode}** (file names only; no file was read)", ""]
    if report.files:
        out += [f"{len(report.distinct)} instruction file(s).", "", "| File | Agent | Format |", "|---|---|---|"]
        for f in report.files:
            note = f" (same file as `{_cell(f.same_as)}`)" if f.same_as else ""
            out.append(f"| `{_cell(f.path)}` | {_cell(f.agent)} | `{f.format}`{note} |")
    else:
        out.append("No instruction file found.")
    out.append("")
    if report.skipped:
        out += ["## Skipped", ""] + [f"- `{show_text(s.path)}`: {s.reason}" for s in report.skipped] + [""]
    out += ["## Limitations", ""] + [f"- {line}" for line in report.limitations]
    return "\n".join(out) + "\n"
