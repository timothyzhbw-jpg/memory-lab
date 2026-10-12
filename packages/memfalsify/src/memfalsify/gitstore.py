"""Read memory files from git without checking anything out, from the index, and from the working tree.

Git runs with argument lists (never a shell) and with config that could change its output pinned. Every
user-supplied revision is validated and resolved to a commit ID first. File contents come from the object
database through one ``git cat-file --batch`` process; trees are walked entry by entry, so symlinks and
submodules in history are never followed.

Inside ``memcore.reading.confined_reads`` (the MCP server), git may only read a repository whose work tree,
git directory, common directory and object store all resolve inside the confinement root, without
alternates or symlinks in the object store, and every later git call is pinned to the checked locations.
"""
from __future__ import annotations

import os
import posixpath
import re
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path, PurePath, PureWindowsPath

from memcore.display import code_span as _code_span, report_text
from memcore.paths import InputPathError, component_skip_reason, input_path
from memcore.reading import confinement_root, read_input_text
from memcore.terminal import diagnostic_line

from .corpus import Skipped
from .formats import detect
from .memory import Claim, merge_duplicates, normalize_memory, parse_text

HEX = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")
# C0/C1 controls and the bidi, zero-width and line-separator characters that can disguise text.
CONTROL = re.compile("[\x00-\x1f\x7f-\x9f\u061c\u200b-\u200f\u2028-\u202e\u2066-\u2069\ufeff]")
# Global options: literal pathspecs (file names are not globs) and no config that changes what we parse.
GIT_OPTIONS = ("--literal-pathspecs", "-c", "core.quotePath=false", "-c", "log.showSignature=false",
               "-c", "log.follow=false")


# Variables that would point git at another repository, object store or index than the one it finds
# from the memory file. Dropped inside confined reads, where only the discovered repository is checked.
LOCATION_VARIABLES = ("GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR", "GIT_OBJECT_DIRECTORY",
                      "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_INDEX_FILE", "GIT_NAMESPACE", "GIT_SHALLOW_FILE",
                      "GIT_GRAFT_FILE", "GIT_REPLACE_REF_BASE")


class HistoryError(ValueError):
    """Git is missing, the memory is not in a work tree, or a revision or claim is unusable (exit 2)."""


class GitError(HistoryError):
    """Git could not run or failed. The message ends with git's own diagnostics, which can name paths
    outside the memory's repository; ``summary`` is the message without them."""

    def __init__(self, message: str, summary: str) -> None:
        super().__init__(message)
        self.summary = summary


def printable(text: str) -> str:
    """Untrusted diagnostics on one line: whitespace runs collapse to one space, invisible characters are escaped."""
    return diagnostic_line(text)


def show_text(text: str) -> str:
    """An untrusted report field as single-line literal Markdown."""
    return report_text(text)


def code_span(text: str) -> str:
    """A literal single-line code span, preserving the established delimiter padding."""
    return _code_span(text, pad_backticks=False)


def check_revision(rev: str) -> str:
    """Refuse revisions that git could read as an option or that cannot be shown safely."""
    if not rev or rev.startswith("-") or CONTROL.search(rev):
        raise HistoryError(f"invalid revision {printable(rev)!r}: it must be non-empty, must not start with '-' "
                           "and must not contain control characters")
    return rev


def _env() -> dict[str, str]:
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_OPTIONAL_LOCKS": "0"}
    if confinement_root() is not None:
        for name in LOCATION_VARIABLES:
            env.pop(name, None)
    return env


def _git_executable() -> str:
    exe = shutil.which("git")
    if exe is None:
        raise HistoryError("git was not found on PATH; diff, history and rollback read memory from git")
    return exe


def _run(exe: str, directory: Path, args: list[str]) -> subprocess.CompletedProcess:
    try:
        return subprocess.run([exe, "-C", str(directory), *GIT_OPTIONS, *args], capture_output=True,
                              env=_env(), check=False)
    except OSError as exc:
        raise GitError(f"cannot run git: {exc}", "cannot run git") from exc


def _failure(proc: subprocess.CompletedProcess) -> str:
    return printable(proc.stderr.decode("utf-8", "replace")) or f"exit code {proc.returncode}"


def _resolve(exe: str, directory: Path, rev: str, options: tuple[str, ...] = ()) -> str:
    check_revision(rev)
    proc = _run(exe, directory, [*options, "rev-parse", "--verify", "--quiet", "--end-of-options",
                                 f"{rev}^{{commit}}"])
    commit = proc.stdout.decode("ascii", "replace").strip()
    if proc.returncode or not HEX.fullmatch(commit):
        raise HistoryError(f"{printable(rev)!r} is not a commit in this repository "
                           "(in a shallow clone, fetch more history)")
    return commit


def resolve_commit(directory: Path, rev: str) -> str:
    """The commit ID ``rev`` names in the repository containing ``directory``
    (``git rev-parse --verify --end-of-options REV^{commit}``, after refusing option-like revisions)."""
    return _resolve(_git_executable(), Path(directory), rev)


@dataclass
class Snapshot:
    """The claims of a memory file and the files it links to, at one revision or in the working tree."""
    claims: list[Claim] = field(default_factory=list)
    texts: dict[str, str] = field(default_factory=dict)  # path relative to the memory directory -> LF text
    links: list[str] = field(default_factory=list)  # link targets referenced, whether or not they exist
    skipped: list[Skipped] = field(default_factory=list)

    @property
    def files(self) -> list[str]:
        return list(self.texts)


def link_path(target: str) -> str | None:
    """A linked file as a POSIX path relative to the memory directory, or None if it is not followed."""
    if not target or "\\" in target or target.startswith("/") or PureWindowsPath(target).drive or CONTROL.search(target):
        return None
    normal = posixpath.normpath(target)
    if normal in (".", "..") or normal.startswith(("../", "/")):
        return None
    if component_skip_reason(Path(target), instructions=True):
        return None
    return normal


# git cat-file --batch prints "<oid> <type> <size>\\n" and then that many bytes. The size is
# untrusted. Cap both so a hostile header cannot block on a huge read or a line with no newline.
MAX_GIT_OBJECT = 32 * 1024 * 1024
MAX_GIT_HEADER = 4096


def read_batch_object(stdout) -> tuple[str, str, bytes] | None:
    """One ``git cat-file --batch`` record: ``(oid, type, bytes)``, or None if git reports it missing.

    An object larger than 32MB, or a header longer than 4096 bytes, raises ``HistoryError`` before
    the body is read. A short read is ``HistoryError`` (truncated object).
    """
    header = stdout.readline(MAX_GIT_HEADER + 1)
    if len(header) > MAX_GIT_HEADER:
        raise HistoryError("git cat-file returned a malformed header")
    if not header.endswith(b"\n"):
        raise HistoryError("git cat-file stopped unexpectedly")
    parts = header[:-1].split(b" ")
    if len(parts) != 3 or parts[1] not in (b"blob", b"tree", b"commit", b"tag") or not parts[2].isdigit():
        return None
    size = int(parts[2])
    if size > MAX_GIT_OBJECT:
        raise HistoryError("git cat-file returned an object larger than 32MB")
    data = stdout.read(size)
    if len(data) != size or stdout.read(1) != b"\n":
        raise HistoryError("git cat-file returned a truncated object")
    try:
        return parts[0].decode("ascii"), parts[1].decode("ascii"), data
    except UnicodeDecodeError as exc:
        raise HistoryError("git cat-file returned a malformed header") from exc


def build_snapshot(read: Callable[[str], str | None], name: str, follow_links: bool,
                   directory: PurePath = PurePath(), *, skipped: list[Skipped] | None = None) -> Snapshot:
    """Parse ``name`` and (one level deep) the local .md files it links to, exactly like ``parse_memory``.

    ``directory`` is where the memory file lives; with the names, it gives each file's format."""
    snapshot = Snapshot(skipped=skipped if skipped is not None else [])
    if reason := component_skip_reason(Path(name), instructions=True):
        snapshot.skipped.append(Skipped(path=name, reason=reason + " in walked path"))
        return snapshot
    text = read(name)
    if text is None:
        return snapshot
    snapshot.texts[name] = text
    claims, links = parse_text(text, name, detect(directory / name))
    if follow_links:
        for target in dict.fromkeys(links):
            if reason := component_skip_reason(Path(target), instructions=True):
                snapshot.skipped.append(Skipped(path=target, reason=reason + " in walked path"))
                continue
            relative = link_path(target)
            if relative is None or relative in snapshot.links:
                continue
            snapshot.links.append(relative)
            if relative in snapshot.texts or (linked := read(relative)) is None:
                continue
            snapshot.texts[relative] = linked
            claims += parse_text(linked, relative, detect(directory / relative))[0]
    snapshot.claims = merge_duplicates(claims)
    return snapshot


class Objects:
    """One ``git cat-file --batch`` process for every tree and blob read of a command."""

    def __init__(self, repo: "Repository"):
        try:
            self.proc = subprocess.Popen([repo.git, "-C", str(repo.root), *GIT_OPTIONS, *repo.options,
                                          "cat-file", "--batch"],
                                         stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                         env=_env())
        except OSError as exc:
            raise GitError(f"cannot run git: {exc}", "cannot run git") from exc
        self.trees: dict[tuple[str, str], dict[bytes, tuple[str, str]] | None] = {}
        self.blobs: dict[str, bytes] = {}

    def __enter__(self) -> "Objects":
        return self

    def __exit__(self, *exc) -> None:
        try:
            self.proc.stdin.close()
        except OSError:
            pass
        try:
            self.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait()
        self.proc.stdout.close()

    def _request(self, name: str) -> tuple[str, str, bytes] | None:
        """(object ID, type, content), or None when git reports the object missing."""
        try:
            self.proc.stdin.write(name.encode("utf-8") + b"\n")
            self.proc.stdin.flush()
            return read_batch_object(self.proc.stdout)
        except OSError as exc:
            raise GitError(f"git cat-file failed: {exc}", "git cat-file failed") from exc

    def tree(self, commit: str, directory: str) -> dict[bytes, tuple[str, str]] | None:
        key = (commit, directory)
        if key not in self.trees:
            found = self._request(f"{commit}:{directory}" if directory else f"{commit}^{{tree}}")
            self.trees[key] = _tree_entries(found[2], len(commit) // 2) if found and found[1] == "tree" else None
        return self.trees[key]

    def file(self, commit: str, path: str) -> bytes | None:
        """A regular file's bytes at ``commit``; None if absent, a symlink, a directory or a submodule.

        Trees are looked up entry by entry, so a symlink anywhere on the path is never followed."""
        directory, _, name = path.rpartition("/")
        entries = self.tree(commit, directory)
        entry = entries.get(name.encode("utf-8")) if entries else None
        if entry is None or not entry[0].startswith("100"):
            return None
        mode, oid = entry
        if oid not in self.blobs:
            found = self._request(oid)
            if found is None or found[1] != "blob":
                raise HistoryError(f"git object {oid} ({printable(path)}) is missing from the repository")
            self.blobs[oid] = found[2]
        return self.blobs[oid]

    def blob(self, oid: str) -> bytes:
        """Bytes of one blob. ``oid`` is a full object name, as ``ls-files --stage`` prints it."""
        if oid not in self.blobs:
            if not HEX.fullmatch(oid):
                raise HistoryError("git returned an unexpected object name")
            found = self._request(oid)
            if found is None or found[1] != "blob":
                raise HistoryError(f"git object {oid} is missing from the repository")
            self.blobs[oid] = found[2]
        return self.blobs[oid]


def _index_entry(data: bytes, path: str) -> tuple[str, str] | None:
    """``(mode, oid)`` for an exact index path, or None when that path is not staged.

    An unmerged path (any stage other than 0) is an error: the commit would not have one blob.
    """
    wanted = path.encode("utf-8")
    found: tuple[str, str] | None = None
    for record in data.split(b"\0"):
        if not record:
            continue
        try:
            meta, name = record.split(b"\t", 1)
            mode_b, oid_b, stage_b = meta.split(b" ")
            mode, oid, stage = (mode_b.decode("ascii"), oid_b.decode("ascii"), stage_b.decode("ascii"))
        except (ValueError, UnicodeDecodeError) as exc:
            raise HistoryError("git ls-files returned an unexpected index record") from exc
        if name != wanted:
            continue
        if stage != "0":
            raise HistoryError(f"{printable(path)} is unmerged in the index; resolve the conflict before committing")
        if found is not None or not mode.isdigit() or not HEX.fullmatch(oid):
            raise HistoryError("git ls-files returned an unexpected index record")
        found = (mode, oid)
    return found


def _tree_entries(data: bytes, oid_bytes: int) -> dict[bytes, tuple[str, str]]:
    """Parse a raw tree object: ``<mode> <name>\\0<binary object ID>`` repeated."""
    entries, position = {}, 0
    try:
        while position < len(data):
            space = data.index(b" ", position)
            nul = data.index(b"\0", space)
            oid = data[nul + 1:nul + 1 + oid_bytes]
            entries[data[space + 1:nul]] = (data[position:space].decode("ascii"), oid.hex())
            position = nul + 1 + oid_bytes
    except (ValueError, UnicodeDecodeError) as exc:
        raise HistoryError("git returned a malformed tree object") from exc
    return entries


@dataclass
class Repository:
    git: str
    root: Path  # top level of the work tree
    prefix: str  # memory directory relative to the top level, POSIX, "" or ending in "/"
    memory_dir: Path
    memory_name: str
    options: tuple[str, ...] = ()  # global options for every call, e.g. a pinned --git-dir and --work-tree

    def run(self, *args: str) -> bytes:
        proc = _run(self.git, self.root, [*self.options, *args])
        if proc.returncode:
            raise GitError(f"git {args[0]} failed: {_failure(proc)}", f"git {args[0]} failed")
        return proc.stdout

    def resolve(self, rev: str) -> str:
        return _resolve(self.git, self.root, rev, self.options)

    def head(self) -> str | None:
        """The HEAD commit, or None in a repository without commits."""
        proc = _run(self.git, self.root, [*self.options, "rev-parse", "--verify", "--quiet", "HEAD^{commit}"])
        commit = proc.stdout.decode("ascii", "replace").strip()
        return commit if proc.returncode == 0 and HEX.fullmatch(commit) else None

    def cut_off(self, commit: str) -> bool:
        """True when ``commit`` records a parent this clone does not have.

        ``REV^`` fails both for a real root and for a shallow boundary, and
        ``--is-shallow-repository`` describes the whole clone: a shallow clone can still
        contain the true root (``clone --depth`` that reaches it). A real root's commit
        object has no ``parent`` line; a shallow boundary still records the parent it did
        not fetch. ``--no-replace-objects`` keeps a replace ref from hiding that line.
        ``self.options`` keeps the call pinned to the repository checked under confined reads.
        """
        if _run(self.git, self.root, [*self.options, "rev-parse", "--verify", "--quiet", "--end-of-options",
                                      f"{commit}^"]).returncode == 0:
            return False
        proc = _run(self.git, self.root, [*self.options, "--no-replace-objects", "cat-file", "commit",
                                          "--end-of-options", commit])
        if proc.returncode:
            return True
        header = proc.stdout.split(b"\n\n", 1)[0]
        return any(line.startswith(b"parent ") for line in header.splitlines())

    def at(self, objects: Objects, commit: str, follow_links: bool) -> Snapshot:
        def read(relative: str) -> str | None:
            data = objects.file(commit, self.prefix + relative)
            if data is None:
                return None
            try:
                text = data.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise HistoryError(f"{printable(self.prefix + relative)} at {commit[:12]} is not UTF-8 text") from exc
            return normalize_memory(text)
        return build_snapshot(read, self.memory_name, follow_links, self.memory_dir)

    def worktree_path(self, relative: str) -> Path | None:
        """A memory file in the working tree, confined to the memory directory like ``parse_memory``."""
        try:
            path = input_path(self.memory_dir, self.memory_dir / relative, instructions=True)
        except InputPathError:
            return None
        return path if path.is_file() else None

    def worktree(self, follow_links: bool) -> Snapshot:
        skipped = []
        def read(relative: str) -> str | None:
            try:
                path = input_path(self.memory_dir, self.memory_dir / relative, instructions=True)
                return normalize_memory(read_input_text(self.memory_dir, path, instructions=True)) if path.is_file() else None
            except InputPathError as exc:
                skipped.append(Skipped(path=relative, reason=str(exc)))
                return None
            except UnicodeDecodeError as exc:
                raise HistoryError(f"{printable(relative)} is not UTF-8 text") from exc
        return build_snapshot(read, self.memory_name, follow_links, self.memory_dir, skipped=skipped)

    def index_file(self, objects: Objects, relative: str) -> bytes | None:
        """Bytes of a regular file in the index, or None when it is absent, a symlink or a gitlink.

        The path is the memory-directory-relative name ``build_snapshot`` asks for. Symlink blobs are
        not read, same as ``Objects.file`` for a commit. Callers decode UTF-8 themselves.
        """
        if (not relative or relative in (".", "..") or relative.startswith(("-", "/", "../"))
                or "\\" in relative or CONTROL.search(relative) or posixpath.normpath(relative) != relative):
            raise HistoryError(f"refusing index path {printable(relative)!r}")
        full = self.prefix + relative
        proc = _run(self.git, self.root, [*self.options, "ls-files", "--stage", "-z", "--full-name",
                                           "--end-of-options", "--", full])
        if proc.returncode:
            raise HistoryError(f"git ls-files failed: {_failure(proc)}")
        entry = _index_entry(proc.stdout, full)
        if entry is None or not entry[0].startswith("100"):
            return None
        return objects.blob(entry[1])

    def index(self, objects: Objects, follow_links: bool) -> Snapshot:
        """Claims in the index: the bytes ``git commit`` would record, including linked ``.md`` files."""
        def read(relative: str) -> str | None:
            data = self.index_file(objects, relative)
            if data is None:
                return None
            try:
                text = data.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise HistoryError(f"{printable(self.prefix + relative)} in the index is not UTF-8 text") from exc
            return normalize_memory(text)
        return build_snapshot(read, self.memory_name, follow_links, self.memory_dir)


OUTSIDE = ("the memory file's git repository is not inside the server root: its work tree, git directory, "
           "common directory (for a worktree, the main repository's) and object store must all be inside it")


def _raise(error: OSError) -> None:
    raise error


def _has_symlinks(directory: Path) -> bool:
    """True if anything under ``directory`` is a symlink (git never creates one in an object store)."""
    for parent, directories, files in os.walk(directory, onerror=_raise):
        if any(os.path.islink(os.path.join(parent, name)) for name in directories + files):
            return True
    return False


def _confine(root: Path, toplevel: Path, git_dir: Path, common_dir: Path) -> tuple[str, ...]:
    """Refuse a repository that would make git read outside ``root``; return options that pin git to it."""
    try:
        root = root.resolve()
        objects = common_dir / "objects"
        work_tree, git_dir, common_dir, store = (place.resolve(strict=True)
                                                 for place in (toplevel, git_dir, common_dir, objects))
        inside = all(place.is_relative_to(root) for place in (work_tree, git_dir, common_dir, store))
        alternates = os.path.lexists(store / "info" / "alternates")
        symlinks = inside and _has_symlinks(store)
    except (OSError, RuntimeError):
        raise HistoryError("cannot check where the memory file's git repository is") from None
    if not inside:
        raise HistoryError(OUTSIDE)
    if alternates:
        raise HistoryError("the memory file's git repository borrows objects from another repository "
                           "(objects/info/alternates), which the server does not read")
    if symlinks:
        raise HistoryError("the memory file's git object store contains symlinks, which the server does not follow")
    return ("--git-dir", str(git_dir), "--work-tree", str(work_tree))


def open_repository(memory_file: Path) -> Repository:
    exe = _git_executable()
    given = Path(memory_file).absolute()
    # Resolve parents only. Path.resolve() would follow a symlink in the last component and
    # audit the target's git name. A staged symlink must keep the name the index records;
    # index_file then skips it (modes other than 100644/100755 are not memory text).
    parent = given.parent.resolve()
    try:
        input_path(parent, parent / given.name, instructions=True)
    except InputPathError as exc:
        raise HistoryError(f"memory skipped: {exc}") from None
    if not parent.is_dir():
        raise HistoryError(f"directory not found: {parent}")
    proc = _run(exe, parent, ["rev-parse", "--show-toplevel", "--show-prefix", "--absolute-git-dir",
                              "--git-common-dir"])
    if proc.returncode:
        where = f"{printable(str(memory_file))} is not inside a git work tree"
        raise GitError(f"{where} ({_failure(proc)})", where)
    try:
        # Accept CRLF. A trailing CR would sit on the prefix, fail the control-character
        # check below, and reject every repository. The same CR on the git directory or
        # common directory would pin later calls to a path that does not exist. Piped git
        # normally writes LF.
        lines = [line.rstrip("\r") for line in proc.stdout.decode("utf-8").replace("\r\n", "\n").split("\n")]
    except UnicodeDecodeError as exc:
        raise HistoryError("cannot read the repository location from git") from exc
    if len(lines) != 5 or lines[4] or not all(lines[i] for i in (0, 2, 3)):  # e.g. a path holding a newline
        raise HistoryError("cannot read the repository location from git")
    toplevel, prefix, git_dir, common_dir = lines[:4]
    if CONTROL.search(prefix + given.name):
        raise HistoryError("the memory path contains control characters, which git batch reads cannot carry")
    options: tuple[str, ...] = ()
    if (root := confinement_root()) is not None:
        options = _confine(root, Path(toplevel), Path(git_dir), parent / common_dir)
    return Repository(git=exe, root=Path(toplevel), prefix=prefix, memory_dir=parent, memory_name=given.name,
                      options=options)


def repository_toplevel(start: Path) -> Path:
    """Absolute work-tree root that contains ``start``."""
    directory = Path(start)
    if not directory.is_dir():
        raise HistoryError(f"directory not found: {printable(str(start))}")
    proc = _run(_git_executable(), directory, ["rev-parse", "--show-toplevel"])
    if proc.returncode:
        where = f"{printable(str(start))} is not inside a git work tree"
        raise GitError(f"{where} ({_failure(proc)})", where)
    try:
        text = proc.stdout.decode("utf-8").replace("\r\n", "\n").strip("\n").rstrip("\r")
    except UnicodeDecodeError as exc:
        raise HistoryError("cannot read the repository location from git") from exc
    if not text or "\n" in text or CONTROL.search(text):
        raise HistoryError("cannot read the repository location from git")
    return Path(text)


def list_staged_paths(toplevel: Path) -> list[str]:
    """Work-tree-relative paths that differ between the index and HEAD.

    With no commits yet, every staged path. Names are git's, decoded as UTF-8. Deletions are included;
    the caller decides what an absent index blob means.
    """
    exe = _git_executable()
    root = Path(toplevel)
    head = _run(exe, root, ["rev-parse", "--verify", "--quiet", "HEAD^{commit}"])
    args = ["diff", "--cached", "--name-only", "-z", "--end-of-options"]
    if head.returncode == 0 and HEX.fullmatch(head.stdout.decode("ascii", "replace").strip()):
        args.append("HEAD")
    proc = _run(exe, root, args)
    if proc.returncode:
        raise GitError(f"git diff failed: {_failure(proc)}", "git diff failed")
    paths = []
    for raw in proc.stdout.split(b"\0"):
        if not raw:
            continue
        try:
            paths.append(raw.decode("utf-8"))
        except UnicodeDecodeError as exc:
            raise HistoryError("a staged path is not UTF-8") from exc
    return paths
