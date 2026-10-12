"""``pytest --simulate-windows-newlines``: write text the way Windows does, on macOS and Linux.

On Windows a text stream that writes with the default ``newline=None`` turns each "\\n" into "\\r\\n". CPython
fixes that when it builds the stream, from a constant compiled into its ``io`` module, so patching
``os.linesep`` changes nothing. The option instead passes ``newline="\\r\\n"`` wherever Windows would use the
default, which writes exactly the bytes Windows writes:

* ``open()``, ``io.open()`` and ``Path.open()`` (so also ``Path.write_text()``, ``os.fdopen()`` and
  ``tempfile.NamedTemporaryFile``/``TemporaryFile``) in a text mode that only writes: "w", "a" or "x";
* the stdout and stderr of ``CliRunner`` (Typer's and click's), which are such streams.
  ``result.stdout_bytes`` then holds what a Windows run holds; ``result.output`` drops the CR again, as it
  does on Windows.

Unchanged, as on Windows under pytest: ``sys.stdout``, ``sys.stderr`` and ``capsys`` (pytest's capture
writes with ``newline=""`` on every OS). Out of scope: child processes, text modes that also read ("w+",
"r+"), any ``io.TextIOWrapper`` built directly (``tempfile.SpooledTemporaryFile`` builds its own, so it still
writes LF here), and encodings (the ``windows_console`` fixture covers a cp1252 console).
"""
from __future__ import annotations

import builtins
import importlib
import io
import pathlib
from typing import Callable

import pytest

OPTION = "--simulate-windows-newlines"


def windows_newline(mode: object, newline: str | None) -> str | None:
    """The newline a Windows text stream opened with ``mode`` writes when ``newline`` keeps its default."""
    if newline is None and isinstance(mode, str) and "b" not in mode and "+" not in mode and (
            "w" in mode or "a" in mode or "x" in mode):
        return "\r\n"
    return newline


def simulate() -> Callable[[], None]:
    """Patch the writers listed in the module docstring; returns the function that undoes it."""
    patch = pytest.MonkeyPatch()
    real_open, real_path_open = io.open, pathlib.Path.open

    def open_(file, mode="r", buffering=-1, encoding=None, errors=None, newline=None, closefd=True, opener=None):
        return real_open(file, mode, buffering, encoding, errors, windows_newline(mode, newline), closefd, opener)

    def path_open(self, mode="r", buffering=-1, encoding=None, errors=None, newline=None):
        # Python 3.10's Path.open calls the io.open it bound at import, so it is patched on its own.
        return real_path_open(self, mode, buffering, encoding, errors, windows_newline(mode, newline))

    patch.setattr(builtins, "open", open_)
    patch.setattr(io, "open", open_)
    patch.setattr(pathlib.Path, "open", path_open)
    # Typer 0.2x has its own copy of click's CliRunner; older Typer uses click's.
    for name in ("typer.testing", "click.testing"):
        wrapper = getattr(_imported(name), "_NamedTextIOWrapper", None)
        if wrapper is not None:
            patch.setattr(wrapper, "__init__", _windows_init(wrapper.__init__))
    return patch.undo


def _windows_init(real_init):
    def init(self, buffer, name, mode, **kwargs):
        kwargs["newline"] = windows_newline(mode, kwargs.get("newline"))
        real_init(self, buffer, name, mode, **kwargs)
    return init


def _imported(name: str):
    try:
        return importlib.import_module(name)
    except ImportError:
        return None
