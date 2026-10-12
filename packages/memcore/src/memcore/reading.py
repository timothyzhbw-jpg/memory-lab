"""Read helpers with optional descriptor-based confinement for the MCP server.

Ordinary library/CLI callers keep their existing path semantics. Within
``confined_reads``, every open walks from a pinned root descriptor and refuses
symlinks at the syscall boundary, including files replaced after validation.

Within ``read_limits`` (the web UI sets one per request), whole-file reads stop at a per-file and a
total byte budget, ``open_text`` refuses a file larger than what is left, and walks stop after a
number of directories. Outside it nothing is bounded, as before.
"""
from __future__ import annotations

import errno
import hashlib
import os
import stat
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
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
    """Bounds set with ``read_limits``. ``truncated`` turns true when a walk stopped at ``directories`` or a
    read was refused because ``total_bytes`` was used up, so callers can say their view is incomplete."""
    file_bytes: int | None = None
    total_bytes: int | None = None
    directories: int | None = None
    bytes_read: int = 0
    directories_walked: int = 0
    truncated: bool = False


_LIMITS: ContextVar[ReadLimits | None] = ContextVar("memlab_read_limits", default=None)


@contextmanager
def read_limits(file_bytes: int | None = None, total_bytes: int | None = None,
                directories: int | None = None) -> Iterator[ReadLimits]:
    """Bound the reads and walks in this execution context: at most ``file_bytes`` from one file, at most
    ``total_bytes`` from all files together, at most ``directories`` directories over all walks. Media
    hashing (``file_sha256``) streams and is not counted."""
    limits = ReadLimits(file_bytes=file_bytes, total_bytes=total_bytes, directories=directories)
    token = _LIMITS.set(limits)
    try:
        yield limits
    finally:
        _LIMITS.reset(token)


def current_read_limits() -> ReadLimits | None:
    """The active ``read_limits``, or None outside one."""
    return _LIMITS.get()


def _charge(size: int, cap: int | None, limits: ReadLimits | None) -> None:
    """Refuse ``size`` bytes over ``cap`` or over what is left of the total budget."""
    if cap is not None and size > cap:
        raise ReadLimitError(f"larger than {cap} bytes")
    if limits is not None and limits.total_bytes is not None and size > limits.total_bytes - limits.bytes_read:
        limits.truncated = True
        raise ReadLimitError("the read budget is used up", budget=True)


def _read_all(handle: IO, max_bytes: int | None = None):
    """The whole file, unless it is larger than ``max_bytes`` or the active limits allow. The size is checked
    with fstat first, and at most one unit past the bound is read, so a file that grows during the read is
    refused too, without reading the rest."""
    limits = _LIMITS.get()
    caps = [cap for cap in (max_bytes, limits.file_bytes if limits else None) if cap is not None]
    cap = min(caps) if caps else None
    if cap is None and (limits is None or limits.total_bytes is None):
        return handle.read()
    _charge(os.fstat(handle.fileno()).st_size, cap, limits)
    bounds = [b for b in (cap, limits.total_bytes - limits.bytes_read if limits and limits.total_bytes is not None
                          else None) if b is not None]
    data = handle.read(min(bounds) + 1)
    _charge(len(data), cap, limits)
    if limits is not None:
        limits.bytes_read += len(data)
    return data


def _walk_allowed() -> bool:
    """Count one directory against the active limits; False once the directory budget is used up."""
    limits = _LIMITS.get()
    if limits is None or limits.directories is None:
        return True
    if limits.directories_walked >= limits.directories:
        limits.truncated = True
        return False
    limits.directories_walked += 1
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
    with _open(path, encoding) as handle:
        return _read_all(handle)


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
    """A text handle for streaming; within ``read_limits`` the file's size is charged when it opens."""
    with _open(path, encoding) as handle:
        limits = _LIMITS.get()
        if limits is not None and (limits.file_bytes is not None or limits.total_bytes is not None):
            size = os.fstat(handle.fileno()).st_size
            _charge(size, limits.file_bytes, limits)
            limits.bytes_read += size
        yield handle


def _walk_directory(path: Path, descriptor: int, onerror):
    """Yield before descent, keeping the directory pinned while callers prune."""
    if not _walk_allowed():
        return
    try:
        with os.scandir(descriptor) as iterator:
            entries = list(iterator)
    except OSError as error:
        if onerror is None:
            raise
        onerror(error)
        return

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

    yield path, directories, files
    for name in directories:
        if name not in descend:
            continue  # Directory symlinks are listed but never opened.
        try:
            child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                            dir_fd=descriptor)
        except OSError as error:
            if onerror is None:
                raise
            onerror(error)
            continue
        try:
            yield from _walk_directory(path / name, child, onerror)
        finally:
            os.close(child)


def walk_files(path: Path, onerror: Callable[[OSError], None] | None = None
               ) -> Iterator[tuple[str | Path, list[str], list[str]]]:
    """Walk from a pinned descriptor so directory swaps cannot disclose outside filenames."""
    root = _ROOT.get()
    if root is None:
        for entry in os.walk(path, onerror=onerror):
            if not _walk_allowed():
                return
            yield entry
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
    """SHA-256 of a file's bytes, read in chunks so a large media file never sits in memory."""
    digest = hashlib.sha256()
    with _open(path) as handle:
        while chunk := handle.read(chunk_size):
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
