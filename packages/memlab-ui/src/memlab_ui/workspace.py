"""The one directory the UI may read, the checks that keep every request value inside it, and the lists the
forms offer (agent files, folders, review files, decision logs).

Paths come from query strings, so they are untrusted. They follow the MCP server's rules: relative to the
root, or absolute only when inside it; ``..`` and symlinks only when the resolved path stays inside; no hidden
component or skipped directory except a documented agent instruction location. Pages run inside
``memcore.confined_reads``, so a file swapped for an outward symlink after these checks is still refused when
it is opened. Error messages never repeat a path outside the root or the contents of a rejected file.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from pydantic import ValidationError

from memcore import EvidenceError, resolve_inside, walk_files
from memcore.paths import SKIP_DIRS, InputPathError, input_path
from memcore.reading import ReadLimitError, current_read_limits
from memfalsify import GitError, HistoryError, parse_memory
from memfalsify.discover import discover
from orgmemory import Dataset
from orgmemory.authority import POLICY_LOG

from .i18n import _

DATASET_FILES = ("events.jsonl", "policy.json", "evidence.jsonl")
MEMORY_NAMES = ("MEMORY.md",)
MAX_DEPTH = 4          # folders offered as evidence, below the root
MAX_VALUE = 4096       # characters accepted in one form value


MISMATCH = ("A quote, hash or file named in the reviews or the decision log does not match the files. Reviews must "
            "point into the folder you compare against.")
NOT_FOUND = ("A file named in the reviews or the decision log does not exist. Reviews must point into the folder "
             "you compare against.")
REASONS = (("hidden component", "it is in a hidden folder or is a hidden file"),
           ("skipped directory", "it is in a folder that is never read (node_modules, __pycache__, venv)"),
           ("broken or cyclic symlink", "it is a broken link"))


def reason(skip: str) -> str:
    """A path policy's skip reason in plain words (an English message key)."""
    return next((words for prefix, words in REASONS if skip.startswith(prefix)), skip)


class InputError(ValueError):
    """A request value the UI refuses. The message is safe to show: no outside path, no file contents."""


@dataclass
class AgentFile:
    path: str            # POSIX, relative to the root
    agent: str
    statements: int | None  # None: the file could not be read


@dataclass
class Inventory:
    files: list[AgentFile] = field(default_factory=list)
    directories: list[str] = field(default_factory=list)
    reviews: list[str] = field(default_factory=list)
    datasets: list[str] = field(default_factory=list)
    truncated: bool = False


class Workspace:
    def __init__(self, root: Path | str):
        root = Path(root).resolve()
        if not root.is_dir():
            raise ValueError("the root is not a directory")
        self.root = root

    @property
    def is_git(self) -> bool:
        """The root is the top of a git work tree, so ``changed since`` can stay inside it."""
        return os.path.lexists(self.root / ".git")

    # --- request values -----------------------------------------------------------------------------

    def resolve(self, value: str, what: str, *, instructions: bool = False) -> Path:
        """``what`` names the value in messages, as an English message key such as "The agent file"."""
        label = _(what)
        if not value.strip():
            raise InputError(_("{what} is empty", what=label))
        if "\x00" in value:
            raise InputError(_("{what} contains a NUL character", what=label))
        if len(value) > MAX_VALUE:
            raise InputError(_("{what} is too long", what=label))
        outside = InputError(_("{what} is outside the folder this page can read. Pick one from the list.", what=label))
        missing = InputError(_("{what} cannot be found inside the folder this page can read", what=label))
        try:
            if Path(value).is_absolute():
                path = Path(value).resolve()
                if not path.is_relative_to(self.root):
                    raise outside
            else:
                path = resolve_inside(self.root, value)  # refuses '..' and symlink escapes
        except InputError:
            raise
        except EvidenceError:
            raise outside from None
        except (OSError, RuntimeError, ValueError):
            raise missing from None
        walked = Path(value) if Path(value).is_absolute() else self.root / value
        try:
            input_path(self.root, walked, directory=path.is_dir(), instructions=instructions)
        except InputPathError as exc:
            if str(exc) == "path escapes root":
                raise outside from None
            raise InputError(_("{what} is not read: {reason}", what=label, reason=_(reason(str(exc))))) from None
        except (OSError, RuntimeError, ValueError):
            raise missing from None
        return path

    def inside(self, value: str) -> bool:
        """Whether a request value names a path inside the root (a form may show it back then)."""
        try:
            self.resolve(value, "The agent file", instructions=True)
        except InputError:
            return False
        return True

    def file(self, value: str, what: str, *, instructions: bool = False) -> Path:
        path = self.resolve(value, what, instructions=instructions)
        if not path.exists():
            raise InputError(_("{what} does not exist", what=_(what)))
        if not path.is_file():
            raise InputError(_("{what} is not a file", what=_(what)))
        return path

    def directory(self, value: str, what: str) -> Path:
        path = self.resolve(value, what)
        if not path.exists():
            raise InputError(_("{what} does not exist", what=_(what)))
        if not path.is_dir():
            raise InputError(_("{what} is not a folder", what=_(what)))
        return path

    def dataset(self, value: str) -> Dataset:
        directory = self.directory(value, "The decision log")
        for name in DATASET_FILES:
            target = directory / name
            if not os.path.lexists(target):
                raise InputError(_("The decision log folder has no {name}", name=name))
            self.file(str(target), "The decision log")
        optional = directory / POLICY_LOG
        if os.path.lexists(optional):
            self.file(str(optional), "The decision log")
        try:
            return Dataset.load(directory)
        except (ValueError, OSError) as exc:  # includes pydantic ValidationError and EvidenceError
            raise InputError(self.failure(exc)) from None

    def show(self, path: Path) -> str:
        return path.relative_to(self.root).as_posix() or "."

    def clean(self, message: str) -> str:
        """Paths in a library message, relative to the root."""
        return message.replace(str(self.root) + os.sep, "").replace(str(self.root), ".")

    def history_failure(self, exc: HistoryError) -> str:
        """Git's own diagnostics can name paths outside the root; the rest of a history error is safe."""
        return self.clean(exc.summary if isinstance(exc, GitError) else str(exc))

    def failure(self, exc: Exception) -> str:
        """Library errors can carry rejected file contents or outside paths. Do not echo them."""
        if isinstance(exc, ReadLimitError):
            return _("A file is too large for this page. Use the memfalsify or orgmemory command for it.")
        if isinstance(exc, EvidenceError):
            if "requires --evidence" in str(exc):
                return _("A reviews file needs a folder to compare against.")
            if str(exc).startswith("no evidence passages indexed"):
                return _("No readable files are left in the folder you compare against.")
            return _(MISMATCH)
        if isinstance(exc, ValidationError) or isinstance(exc.__cause__, ValidationError):
            return _("A file has invalid data: check its required fields, types and values.")
        if isinstance(exc, FileNotFoundError):
            return _(NOT_FOUND)
        if isinstance(exc, OSError):
            return _("The files cannot be read. Check that they are readable, regular files.")
        if isinstance(exc, json.JSONDecodeError):
            return _("A file has invalid JSON at line {line}, column {column}.", line=exc.lineno, column=exc.colno)
        message = str(exc)
        if message.startswith(str(self.root) + os.sep):
            for problem in ("invalid JSON", "expected a JSON object"):
                if f": {problem}" in message:
                    return self.clean(message.split(f": {problem}", 1)[0]) + f": {problem}."
        return _("The input files are not valid. Check the memory, reviews and decision log files.")

    # --- what the forms offer -----------------------------------------------------------------------

    def inventory(self, count: bool = True) -> Inventory:
        """Agent files (``memfalsify discover`` plus MEMORY.md files), folders, review files and decision logs.
        With ``count``, each agent file is parsed to count its statements.

        Hidden directories, skipped directories (node_modules, __pycache__, venv), nested git repositories
        and symlinked directories are not searched, as in discovery. Discovery, this walk and the evidence scan
        share the page's directory budget (``read_limits``); ``truncated`` says it ran out."""
        found = Inventory()
        agents: dict[str, str] = {}
        try:
            for item in discover(self.root).files:
                if item.same_as is None:
                    agents[item.path] = item.agent
        except (OSError, ValueError):
            pass  # an unreadable directory: the walk below still offers what it can read

        def skip(error: OSError) -> None:
            return None

        for current, dirnames, filenames in walk_files(self.root, onerror=skip):
            here = Path(current)
            relative = PurePosixPath(*here.relative_to(self.root).parts)
            if relative.parts and ".git" in dirnames + filenames:
                dirnames[:] = []
                continue
            kept = []
            for name in sorted(dirnames):
                if name.startswith(".") or name in SKIP_DIRS or (here / name).is_symlink():
                    continue
                kept.append(name)
            dirnames[:] = kept
            if len(relative.parts) < MAX_DEPTH:
                found.directories += [(relative / name).as_posix() for name in kept]
            names = set(filenames)
            if all(name in names for name in DATASET_FILES):
                found.datasets.append(relative.as_posix() or ".")
            for name in sorted(filenames):
                path = (relative / name).as_posix()
                if name in MEMORY_NAMES and path not in agents and not (here / name).is_symlink():
                    agents[path] = "Agent memory"
                elif name.endswith(".jsonl") and "review" in name.lower():
                    found.reviews.append(path)
        for path in sorted(agents):
            found.files.append(AgentFile(path=path, agent=agents[path],
                                         statements=self.statements(path) if count else None))
        found.directories.sort()
        limits = current_read_limits()
        found.truncated = bool(limits and limits.truncated)
        return found

    def statements(self, relative: str) -> int | None:
        try:
            claims, _unused = parse_memory(self.file(relative, "The agent file", instructions=True))
        except (InputError, ValueError, OSError):
            return None
        return len(claims)
