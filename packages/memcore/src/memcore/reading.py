"""Read helpers with optional descriptor-based confinement for the MCP server.

Ordinary library/CLI callers keep their existing path semantics. Within
``confined_reads``, every open walks from a pinned root descriptor and refuses
symlinks at the syscall boundary, including files replaced after validation.
"""
from __future__ import annotations

import errno
import os
import stat
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path

from .paths import InputPathError, input_path


_ROOT: ContextVar[tuple[Path, int] | None] = ContextVar("memlab_read_root", default=None)
_CONFINEMENT_SUPPORTED = hasattr(os, "O_NOFOLLOW") and os.open in os.supports_dir_fd


def confinement_supported() -> bool:
    return _CONFINEMENT_SUPPORTED


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
def confined_reads(root: Path):
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
        resolved = input_path(base, path)
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
        return handle.read()


def read_file_text(path: Path, encoding: str = "utf-8") -> str:
    with _open(path, encoding) as handle:
        return handle.read()


def read_input_bytes(root: Path, path: Path) -> bytes:
    """Read a policy-checked input, with descriptor confinement where supported."""
    resolved = input_path(root, path)
    if confinement_root() is not None:
        # Never replace the MCP root with a narrower path that may have been swapped outside it.
        return read_file_bytes(resolved)
    if confinement_supported():
        with confined_reads(root):
            return read_file_bytes(resolved)
    # Windows has no O_NOFOLLOW. Pin the handle and verify its identity before reading.
    with resolved.open("rb") as handle:
        input_path(root, resolved)
        current = resolved.lstat()
        if not stat.S_ISREG(current.st_mode) or not os.path.samestat(current, os.fstat(handle.fileno())):
            raise ValueError("input changed or is not a regular file")
        return handle.read()


def read_input_text(root: Path, path: Path, encoding: str = "utf-8") -> str:
    return read_input_bytes(root, path).decode(encoding).replace("\r\n", "\n").replace("\r", "\n")


def open_text(path: Path, encoding: str = "utf-8"):
    return _open(path, encoding)


def _walk_directory(path: Path, descriptor: int, onerror):
    """Yield before descent, keeping the directory pinned while callers prune."""
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


def walk_files(path: Path, onerror=None):
    """Walk from a pinned descriptor so directory swaps cannot disclose outside filenames."""
    root = _ROOT.get()
    if root is None:
        yield from os.walk(path, onerror=onerror)
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
