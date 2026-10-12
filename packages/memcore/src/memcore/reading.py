"""Read helpers with optional descriptor-based confinement for the MCP server.

Ordinary library/CLI callers keep their existing path semantics. Within
``confined_reads``, every open walks from a pinned root descriptor and refuses
symlinks at the syscall boundary, including files replaced after validation.

Within ``read_limits`` (the web UI sets one per request), reads are charged in bytes, before any decoding:
whole-file reads stop at a per-file and a total byte budget, and ``open_text`` streams charge every byte as it
is read, so a file that grows cannot pass the limits. Walks stop after a number of directories, a depth and a
number of directory entries, and callers draw named budgets (claims, passages, comparisons) with ``take``.
Outside ``read_limits`` nothing is bounded and every read returns what it did before.
"""
from __future__ import annotations

import errno
import hashlib
import io
import os
import stat
from collections.abc import Callable, Iterator, Mapping
from contextlib import AbstractContextManager, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO, TextIO

from .paths import InputPathError, input_path


_ROOT: ContextVar[tuple[Path, int] | None] = ContextVar("memlab_read_root", default=None)
_CONFINEMENT_SUPPORTED = hasattr(os, "O_NOFOLLOW") and os.open in os.supports_dir_fd


def confinement_supported() -> bool:
    return _CONFINEMENT_SUPPORTED


class ReadLimitError(ValueError):
    """A read refused by a size limit before anything past the limit was read. ``budget`` is True when the
    total of ``read_limits``, not a limit on the one file, refused it."""

    def __init__(self, message: str, *, budget: bool = False) -> None:
        super().__init__(message)
        self.budget = budget


@dataclass
class ReadLimits:
    """Bounds set with ``read_limits``. ``truncated`` turns true when a walk stopped early, a read was refused
    because ``total_bytes`` was used up, or a named budget ran out; ``exhausted`` says which."""
    file_bytes: int | None = None
    total_bytes: int | None = None
    directories: int | None = None
    depth: int | None = None
    entries: int | None = None
    items: dict[str, int] = field(default_factory=dict)
    bytes_read: int = 0
    directories_walked: int = 0
    entries_listed: int = 0
    used: dict[str, int] = field(default_factory=dict)
    exhausted: set[str] = field(default_factory=set)
    truncated: bool = False

    def stop(self, what: str) -> None:
        self.truncated = True
        self.exhausted.add(what)


_LIMITS: ContextVar[ReadLimits | None] = ContextVar("memlab_read_limits", default=None)


@contextmanager
def read_limits(file_bytes: int | None = None, total_bytes: int | None = None, directories: int | None = None,
                *, depth: int | None = None, entries: int | None = None,
                items: Mapping[str, int] | None = None) -> Iterator[ReadLimits]:
    """Bound the reads and walks in this execution context: at most ``file_bytes`` bytes from one file and
    ``total_bytes`` from all files together, at most ``directories`` directories and ``entries`` directory
    entries over all walks, at most ``depth`` levels below a walk's start, and the named ``items`` budgets
    that ``take`` draws from. Media hashing (``file_sha256``) streams within the same byte limits."""
    limits = ReadLimits(file_bytes=file_bytes, total_bytes=total_bytes, directories=directories, depth=depth,
                        entries=entries, items=dict(items or {}))
    token = _LIMITS.set(limits)
    try:
        yield limits
    finally:
        _LIMITS.reset(token)


def current_read_limits() -> ReadLimits | None:
    """The active ``read_limits``, or None outside one."""
    return _LIMITS.get()


def take(kind: str, count: int = 1) -> int:
    """Draw up to ``count`` units from the named budget ``kind`` and return how many were granted: all of
    them outside ``read_limits`` or when it sets no such budget. Granting fewer marks the limits truncated."""
    limits = _LIMITS.get()
    if limits is None or kind not in limits.items:
        return count
    granted = max(0, min(count, limits.items[kind] - limits.used.get(kind, 0)))
    limits.used[kind] = limits.used.get(kind, 0) + granted
    if granted < count:
        limits.stop(kind)
    return granted


def available(kind: str) -> bool:
    """Whether the named budget ``kind`` has anything left (always True outside ``read_limits``). False marks the
    limits truncated: the caller is about to leave something out."""
    limits = _LIMITS.get()
    if limits is None or kind not in limits.items or limits.used.get(kind, 0) < limits.items[kind]:
        return True
    limits.stop(kind)
    return False


def charge(size: int) -> None:
    """Charge ``size`` bytes read some other way (such as a git object) before reading them: ``ReadLimitError``
    when they exceed the per-file limit or what is left of the total."""
    limits = _LIMITS.get()
    if limits is not None:
        _refuse_size(size, None, limits)
        _refuse_total(size, limits)
        limits.bytes_read += size


def _refuse_size(size: int, max_bytes: int | None, limits: ReadLimits | None) -> None:
    """Refuse a file of ``size`` bytes over the limits' per-file cap (which marks them truncated) or over the
    caller's own ``max_bytes`` (a deliberate skip, which does not)."""
    if limits is not None and limits.file_bytes is not None and size > limits.file_bytes:
        limits.stop("bytes")
        raise ReadLimitError(f"larger than {limits.file_bytes} bytes")
    if max_bytes is not None and size > max_bytes:
        raise ReadLimitError(f"larger than {max_bytes} bytes")


def _refuse_total(more: int, limits: ReadLimits | None) -> None:
    """Refuse reading ``more`` bytes when that would pass the total budget."""
    if limits is not None and limits.total_bytes is not None and more > limits.total_bytes - limits.bytes_read:
        limits.stop("bytes")
        raise ReadLimitError("the read budget is used up", budget=True)


def _consumed(size: int, count: int, max_bytes: int | None, limits: ReadLimits | None) -> None:
    """Charge ``size`` bytes just read (``count`` from this file so far), then refuse if they passed a limit:
    a failed read is charged too, and marks the limits truncated."""
    if limits is not None:
        limits.bytes_read += size
    _refuse_size(count, max_bytes, limits)
    if limits is not None and limits.total_bytes is not None and limits.bytes_read > limits.total_bytes:
        limits.stop("bytes")
        raise ReadLimitError("the read budget is used up", budget=True)


def _bounds(max_bytes: int | None) -> tuple[ReadLimits | None, int | None]:
    """The active limits and the per-file cap: the smaller of ``max_bytes`` and the limits' ``file_bytes``."""
    limits = _LIMITS.get()
    caps = [cap for cap in (max_bytes, limits.file_bytes if limits else None) if cap is not None]
    return limits, min(caps) if caps else None


def _read_all(handle: IO[bytes], max_bytes: int | None = None) -> bytes:
    """A binary file's bytes, unless it is larger than ``max_bytes`` or the active limits allow. The size is
    checked with fstat first, and at most one byte past the bound is read, so a file that grows during the read
    is refused too, without reading the rest; the bytes it did read are charged."""
    limits, cap = _bounds(max_bytes)
    if cap is None and (limits is None or limits.total_bytes is None):
        return handle.read()
    size = os.fstat(handle.fileno()).st_size
    _refuse_size(size, max_bytes, limits)
    _refuse_total(size, limits)
    bounds = [b for b in (cap, limits.total_bytes - limits.bytes_read if limits and limits.total_bytes is not None
                          else None) if b is not None]
    data = handle.read(min(bounds) + 1)
    _consumed(len(data), len(data), max_bytes, limits)
    return data


class _Charged(io.RawIOBase):
    """A binary stream that charges every byte against the active limits as it is read, never handing out more
    than one byte past what they allow; bytes read before a refusal are charged too."""

    def __init__(self, handle: IO[bytes], limits: ReadLimits) -> None:
        super().__init__()
        self.handle, self.limits, self.count = handle, limits, 0

    def readable(self) -> bool:
        return True

    def readinto(self, buffer) -> int:
        limits = self.limits
        allowed = [b for b in (None if limits.file_bytes is None else limits.file_bytes - self.count,
                               None if limits.total_bytes is None else limits.total_bytes - limits.bytes_read)
                   if b is not None]
        view = memoryview(buffer)
        if allowed:
            view = view[:max(0, min(allowed)) + 1]
        data = self.handle.read(len(view))
        size = len(data)
        view[:size] = data
        self.count += size
        _consumed(size, self.count, None, limits)
        return size


def _walk_allowed() -> bool:
    """Count one directory against the active limits; False once the directory budget is used up."""
    limits = _LIMITS.get()
    if limits is None or limits.directories is None:
        return True
    if limits.directories_walked >= limits.directories:
        limits.stop("directories")
        return False
    limits.directories_walked += 1
    return True


def _trim_entries(dirnames: list[str], filenames: list[str]) -> None:
    """Count an os.walk listing against the entry budget like a confined listing, keeping only what it allows
    (directories first)."""
    limits = _LIMITS.get()
    if limits is None or limits.entries is None:
        return
    left = max(0, limits.entries - limits.entries_listed)
    total = len(dirnames) + len(filenames)
    if total > left:
        limits.stop("entries")
        keep_dirs = min(len(dirnames), left)
        dirnames[:] = dirnames[:keep_dirs]
        filenames[:] = filenames[:left - keep_dirs]
    limits.entries_listed += min(total, left)


def _deeper_allowed(level: int) -> bool:
    """Whether a walk may enter a directory ``level`` levels below its start."""
    limits = _LIMITS.get()
    if limits is None or limits.depth is None or level <= limits.depth:
        return True
    limits.stop("depth")
    return False


def _entry_allowed() -> bool:
    """Count one directory entry against the active limits; False once the entry budget is used up."""
    limits = _LIMITS.get()
    if limits is None or limits.entries is None:
        return True
    if limits.entries_listed >= limits.entries:
        limits.stop("entries")
        return False
    limits.entries_listed += 1
    return True


def confinement_root() -> Path | None:
    """The root of the active ``confined_reads`` context, or None outside one.

    Readers that cannot use descriptor-relative opens (such as a git subprocess) check their own
    locations against it."""
    active = _ROOT.get()
    return active[0] if active is not None else None


def _directory(parts: tuple[str, ...], start: int) -> int:
    """Return an owned descriptor; never traverse a symlink, even during a rename race."""
    current = os.dup(start)
    try:
        for part in parts:
            following = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=current)
            os.close(current)
            current = following
        return current
    except BaseException:
        os.close(current)
        raise


@contextmanager
def confined_reads(root: Path) -> Iterator[None]:
    """Bind confinement to this execution context; no process-wide patches or disk copies."""
    if not confinement_supported():
        raise ValueError("confined reads require POSIX O_NOFOLLOW and dir_fd support")
    root = Path(root).absolute()
    anchor = os.open(root.anchor, os.O_RDONLY | os.O_DIRECTORY)
    try:
        descriptor = _directory(root.parts[1:], anchor)
    finally:
        os.close(anchor)
    token = _ROOT.set((root, descriptor))
    try:
        yield
    finally:
        _ROOT.reset(token)
        os.close(descriptor)


@contextmanager
def _open(path: Path, encoding: str | None = None):
    root = _ROOT.get()
    mode = "r" if encoding is not None else "rb"
    if root is None:
        with Path(path).open(mode, encoding=encoding) as handle:
            yield handle
        return
    base, root_fd = root
    # Accept existing in-root symlinks by resolving them first. O_NOFOLLOW on
    # every subsequent component closes the check/open race on that resolution.
    try:
        # Callers decide what to read; agent instruction files in hidden directories are memory inputs.
        resolved = input_path(base, path, instructions=True)
    except InputPathError as exc:
        if str(exc) == "path escapes root":
            raise ValueError("read path is outside the server root") from None
        raise
    relative = resolved.relative_to(base)
    directory_fd = _directory(relative.parts[:-1], root_fd)
    try:
        # NONBLOCK also prevents a regular file swapped for a FIFO from hanging
        # before fstat can reject it. All handles are opened strictly read-only.
        descriptor = os.open(relative.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                             dir_fd=directory_fd)
    finally:
        os.close(directory_fd)
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise ValueError("input is not a regular file")
        handle = os.fdopen(descriptor, mode, encoding=encoding)
    except BaseException:
        os.close(descriptor)
        raise
    with handle:
        yield handle


def read_file_bytes(path: Path) -> bytes:
    with _open(path) as handle:
        return _read_all(handle)


def read_file_text(path: Path, encoding: str = "utf-8") -> str:
    if _LIMITS.get() is None:
        with _open(path, encoding) as handle:
            return handle.read()
    # Within limits, charge bytes, not decoded characters; universal newlines as in text mode.
    with _open(path) as handle:
        return _read_all(handle).decode(encoding).replace("\r\n", "\n").replace("\r", "\n")


def read_head(path: Path, size: int) -> bytes:
    """At most the first ``size`` bytes of a file, charged against the active limits before they are read
    (for recognising a file by its content without reading all of it)."""
    with _open(path) as handle:
        limits = _LIMITS.get()
        if limits is not None:
            size = min(size, os.fstat(handle.fileno()).st_size)
            _refuse_total(size, limits)
        data = handle.read(size)
        if limits is not None:
            limits.bytes_read += len(data)
        return data


def _read_bounded(path: Path, max_bytes: int | None) -> bytes:
    with _open(path) as handle:
        return _read_all(handle, max_bytes)


def read_input_bytes(root: Path, path: Path, *, instructions: bool = False, max_bytes: int | None = None) -> bytes:
    """Read a policy-checked input, with descriptor confinement where supported. With ``max_bytes``, a larger
    file raises ``ReadLimitError`` after reading at most ``max_bytes + 1`` bytes."""
    resolved = input_path(root, path, instructions=instructions)
    if confinement_root() is not None:
        # Never replace the MCP root with a narrower path that may have been swapped outside it.
        return _read_bounded(resolved, max_bytes)
    if confinement_supported():
        with confined_reads(root):
            return _read_bounded(resolved, max_bytes)
    # Windows has no O_NOFOLLOW. Pin the handle and verify its identity before reading.
    with resolved.open("rb") as handle:
        input_path(root, resolved, instructions=instructions)
        current = resolved.lstat()
        if not stat.S_ISREG(current.st_mode) or not os.path.samestat(current, os.fstat(handle.fileno())):
            raise ValueError("input changed or is not a regular file")
        return _read_all(handle, max_bytes)


def read_input_text(root: Path, path: Path, encoding: str = "utf-8", *, instructions: bool = False) -> str:
    return read_input_bytes(root, path, instructions=instructions).decode(encoding).replace("\r\n", "\n").replace("\r", "\n")


def open_text(path: Path, encoding: str = "utf-8") -> AbstractContextManager[TextIO]:
    return _open_counted(path, encoding)


@contextmanager
def _open_counted(path: Path, encoding: str):
    """A text handle for streaming. Within ``read_limits`` it decodes a binary stream that charges every byte as
    it is read (after an fstat check), so neither the file's size at open nor its growth can pass the limits."""
    limits = _LIMITS.get()
    if limits is None or (limits.file_bytes is None and limits.total_bytes is None):
        with _open(path, encoding) as handle:
            yield handle
        return
    with _open(path) as handle:
        size = os.fstat(handle.fileno()).st_size
        _refuse_size(size, None, limits)
        _refuse_total(size, limits)
        with io.TextIOWrapper(io.BufferedReader(_Charged(handle, limits)), encoding=encoding) as text:
            yield text


def _listing(descriptor: int, onerror) -> tuple[list[str], list[str], set[str]] | None:
    """One directory's (directories, files, names safe to descend), or None if it cannot be listed. Within
    ``read_limits`` the listing stops when the entry budget is used up."""
    try:
        entries = []
        with os.scandir(descriptor) as iterator:
            for entry in iterator:
                if not _entry_allowed():
                    break
                entries.append(entry)
    except OSError as error:
        if onerror is None:
            raise
        onerror(error)
        return None

    directories, files, descend = [], [], set()
    for entry in entries:
        is_link = False
        try:
            is_link = entry.is_symlink()
            is_directory = entry.is_dir()
        except OSError as error:
            # A known cyclic link belongs in files for the reader to report.
            if not (is_link and error.errno == errno.ELOOP):
                if onerror is None:
                    raise
                onerror(error)
            # If the callback returns, retain unknown entries like os.walk does.
            files.append(entry.name)
            continue
        if is_directory:
            directories.append(entry.name)
            if not is_link:
                descend.add(entry.name)
        else:
            files.append(entry.name)
    return directories, files, descend


_DONE = object()


def _walk_directory(path: Path, descriptor: int, onerror):
    """Yield before descent, keeping each directory pinned while callers prune. The walk is iterative and
    depth-first in the same order as a recursive one, so a deep tree meets ``read_limits``' depth instead of
    Python's recursion limit; each open level holds one descriptor, closed when the walk leaves it."""
    if not _walk_allowed():
        return
    listing = _listing(descriptor, onerror)
    if listing is None:
        return
    directories, files, descend = listing
    yield path, directories, files
    stack = [(path, descriptor, iter(directories), descend, False)]
    try:
        while stack:
            here, fd, names, safe, _owned = stack[-1]
            name = next(names, _DONE)
            if name is _DONE:
                stack.pop()
                if _owned:
                    os.close(fd)
                continue
            if name not in safe:
                continue  # Directory symlinks are listed but never opened.
            if not _deeper_allowed(len(stack)):
                continue
            try:
                child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            except OSError as error:
                if onerror is None:
                    raise
                onerror(error)
                continue
            try:
                listing = _listing(child, onerror) if _walk_allowed() else None
            except BaseException:
                os.close(child)
                raise
            if listing is None:
                os.close(child)
                continue
            directories, files, descend = listing
            stack.append((here / name, child, iter(()), descend, True))
            yield here / name, directories, files
            stack[-1] = (here / name, child, iter(directories), descend, True)
    finally:
        for _here, fd, _names, _safe, owned in stack:
            if owned:
                os.close(fd)


def walk_files(path: Path, onerror: Callable[[OSError], None] | None = None
               ) -> Iterator[tuple[str | Path, list[str], list[str]]]:
    """Walk from a pinned descriptor so directory swaps cannot disclose outside filenames."""
    root = _ROOT.get()
    if root is None:
        start = Path(path)
        for current, dirnames, filenames in os.walk(path, onerror=onerror):
            if not _walk_allowed():
                return
            _trim_entries(dirnames, filenames)
            yield current, dirnames, filenames
            if dirnames and not _deeper_allowed(len(Path(current).relative_to(start).parts) + 1):
                dirnames[:] = []
        return
    base, root_fd = root
    resolved = Path(path).resolve()
    if not resolved.is_relative_to(base):
        raise ValueError("directory is outside the server root")
    try:
        descriptor = _directory(resolved.relative_to(base).parts, root_fd)
    except OSError as error:
        if onerror is None:
            raise
        onerror(error)
        return
    try:
        yield from _walk_directory(resolved, descriptor, onerror)
    finally:
        os.close(descriptor)


def file_sha256(path: Path, chunk_size: int = 1 << 20) -> str:
    """SHA-256 of a file's bytes, read in chunks so a large media file never sits in memory. Within
    ``read_limits`` the per-file and total byte limits apply, checked with fstat first and charged per chunk."""
    digest = hashlib.sha256()
    limits = _LIMITS.get()
    with _open(path) as handle:
        if limits is not None:
            size = os.fstat(handle.fileno()).st_size
            _refuse_size(size, None, limits)
            _refuse_total(size, limits)
        count = 0
        while chunk := handle.read(chunk_size):
            count += len(chunk)
            if limits is not None:
                _consumed(len(chunk), count, None, limits)
            digest.update(chunk)
    return digest.hexdigest()


def list_files(directory: Path) -> list[str]:
    """Names of the entries in one directory that are not directories, listed like ``walk_files``."""
    def fail(error: OSError) -> None:
        raise error

    for _, _, files in walk_files(directory, onerror=fail):
        return files
    return []


def entry_names(directory: Path) -> list[str]:
    """Every name in one directory, including directories and symlinks, without descending.

    Names are the directory entries as stored. Callers that look a file up by name must use this rather
    than ``exists``: a case-insensitive filesystem would otherwise open ``clip.mp4`` for ``Clip.mp4``,
    while Linux would not, and the binding would depend on the operating system."""
    def fail(error: OSError) -> None:
        raise error

    for _, directories, files in walk_files(directory, onerror=fail):
        return [*directories, *files]
    return []
