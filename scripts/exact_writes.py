"""Rewrite text-mode writes in the tests to exact bytes, which tests/test_windows_guard.py requires.

Windows text mode turns every "\\n" written into "\\r\\n", so a fixture written with ``Path.write_text`` has a
different sha256 and different offsets there than on macOS and Linux. This script makes the mechanical fix:

* ``p.write_text(text)``, with at most ``encoding="utf-8"`` (and ``errors=None`` or ``newline=None``), becomes
  ``write_text_exact(p, text)`` (UTF-8 bytes, from tests/conftest.py), and the module imports it. The text's
  source is copied as it is; only continuation lines outside string literals may move, to stay aligned.
* a text-mode ``open(..., "w")``, ``io.open``, ``path.open("a", ...)`` or ``os.fdopen(fd, "w")`` gets
  ``newline=""`` (a ``newline=None`` becomes ``newline=""``).

``inexact_writes`` is the definition the guard uses. A call that names its newline as a string literal, a binary
or read-only open, and a call on a line marked ``# windows-guard: allow`` are left alone, so running the script
again changes nothing. Anything else (``errors=``, a non-UTF-8 encoding, ``*args``, a newline held in a
variable) is reported for a human. Usage: ``python scripts/exact_writes.py [--check] [path ...]`` (default:
tests/); ``--check`` rewrites nothing and exits 1 if something would change.
"""
from __future__ import annotations

import argparse
import ast
import io
import re
import sys
import tokenize
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HELPER, HELPER_MODULE = "write_text_exact", "conftest"
FILE_MODE = re.compile(r"[rwaxbt+]+")
UTF8 = {"utf-8", "utf8", "utf_8"}
ALLOW = "# windows-guard: allow"  # a deliberate text-mode write, left as it is (by the guard too)
# The repository's own modules: a new conftest import goes first among them.
LOCAL_MODULES = {"conftest", "memcore", "memfalsify", "orgmemory", "memlab_mcp", "memlab_ui", "github_fakes",
                 "jira_fakes", "orgmemory_reference"}
# Modules whose own ``open`` is something else (tarfile.open, codecs.open, ...), besides the names a file imports.
KNOWN_MODULES = frozenset({"bz2", "codecs", "dbm", "gzip", "io", "_io", "lzma", "os", "shelve", "tarfile",
                           "tempfile", "wave", "webbrowser", "zipfile"})
# The positional index of each writer's mode and newline arguments.
POSITIONS = {"open": (1, 5), "fdopen": (1, 5), "path.open": (0, 4), "write_text": (None, 3)}


def module_names(tree: ast.AST) -> frozenset[str]:
    """Names that refer to a module: ``KNOWN_MODULES`` and every name an ``import`` statement binds."""
    names = set(KNOWN_MODULES)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.asname or alias.name.split(".")[0] for alias in node.names)
    return frozenset(names)


def writer(call: ast.Call, modules: frozenset[str]) -> str | None:
    """Which writer ``call`` is: "write_text" (any ``.write_text``), "open" (``open`` or ``io.open``), "fdopen"
    (``os.fdopen``) or "path.open" (``.open`` on anything but a module); None for any other call."""
    func = call.func
    if isinstance(func, ast.Name):
        return "open" if func.id == "open" else None
    if not isinstance(func, ast.Attribute):
        return None
    if func.attr == "write_text":
        return "write_text"
    owner = func.value.id if isinstance(func.value, ast.Name) else None
    if owner in modules:
        if func.attr == "open" and owner in ("io", "_io"):
            return "open"
        return "fdopen" if func.attr == "fdopen" and owner == "os" else None
    return "path.open" if func.attr == "open" else None


def argument(call: ast.Call, name: str, index: int | None) -> ast.expr | None:
    """The argument ``name`` of ``call``, given by keyword or at positional ``index``; None when absent."""
    for keyword in call.keywords:
        if keyword.arg == name:
            return keyword.value
    return call.args[index] if index is not None and len(call.args) > index else None


def writes_text(call: ast.Call, kind: str) -> bool:
    """``write_text``, or an open whose literal mode writes text: it holds "w", "a", "x" or "+", and "b" is absent."""
    if kind == "write_text":
        return True
    mode = argument(call, "mode", POSITIONS[kind][0])
    if not (isinstance(mode, ast.Constant) and isinstance(mode.value, str) and FILE_MODE.fullmatch(mode.value)):
        return False
    return "b" not in mode.value and any(c in mode.value for c in "wax+")


def exact_newline(call: ast.Call, kind: str) -> bool:
    """Whether the call names its newline as a string literal. "" and "\\n" write each "\\n" as it is, "\\r\\n"
    writes CRLF everywhere: the same bytes on every OS. None, the default, writes CRLF on Windows only."""
    newline = argument(call, "newline", POSITIONS[kind][1])
    return isinstance(newline, ast.Constant) and isinstance(newline.value, str)


def inexact_writes(tree: ast.AST, lines: list[str] | tuple = ()) -> list[tuple[ast.Call, str]]:
    """The text writes whose bytes differ on Windows, with their kind, in source order. Calls on a line marked
    ``ALLOW`` (in ``lines``, the source's lines) are left out."""
    modules = module_names(tree)
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or (lines and ALLOW in lines[node.lineno - 1]):
            continue
        kind = writer(node, modules)
        if kind and writes_text(node, kind) and not exact_newline(node, kind):
            found.append((node, kind))
    return sorted(found, key=lambda item: (item[0].lineno, item[0].col_offset))


class Source:
    def __init__(self, text: str):
        self.text = text
        self.lines = io.StringIO(text, newline="").readlines()  # the line breaks Python counts, kept
        self.starts = [0]
        for line in self.lines:
            self.starts.append(self.starts[-1] + len(line))
        self.in_strings = _string_lines(text)

    def offset(self, lineno: int, col: int) -> int:
        """Character offset of an AST (line, UTF-8 byte column) position."""
        line = self.lines[lineno - 1]
        return self.starts[lineno - 1] + len(line.encode("utf-8")[:col].decode("utf-8"))

    def span(self, node: ast.AST) -> tuple[int, int]:
        return self.offset(node.lineno, node.col_offset), self.offset(node.end_lineno, node.end_col_offset)

    def segment(self, node: ast.AST) -> str:
        start, end = self.span(node)
        return self.text[start:end]

    def column(self, node: ast.AST) -> int:
        return self.offset(node.lineno, node.col_offset) - self.starts[node.lineno - 1]


def _string_lines(text: str) -> set[int]:
    """The lines that begin inside a string literal: a triple-quoted or backslash-continued string, or an
    f-string that spans lines. Their leading spaces are part of the text, so they are never moved."""
    inside: set[int] = set()
    opened: list[int] = []
    fstring_start, fstring_end = getattr(tokenize, "FSTRING_START", -1), getattr(tokenize, "FSTRING_END", -1)
    for token in tokenize.generate_tokens(io.StringIO(text).readline):
        if token.type == tokenize.STRING:
            inside.update(range(token.start[0] + 1, token.end[0] + 1))
        elif token.type == fstring_start:
            opened.append(token.start[0])
        elif token.type == fstring_end:
            inside.update(range(opened.pop() + 1, token.end[0] + 1))
    return inside


def rewrite(text: str, path: str = "<source>") -> tuple[str, list[str]]:
    """The rewritten source and one note per change (or per call left for a human)."""
    tree = ast.parse(text, path)
    src = Source(text)
    edits: list[tuple[int, int, str]] = []
    notes: list[tuple[int, str]] = []
    for node, kind in inexact_writes(tree, src.lines):
        if kind == "write_text":
            new = _exact_call(src, node)
            edit = None if new is None else (*src.span(node), new)
        else:
            edit = _newline_edit(src, node, kind)
        if edit is None:
            notes.append((node.lineno, f"{path}:{node.lineno}: {kind} with arguments this script leaves to a human"))
            continue
        edits.append(edit)
        what = f"write_text -> {HELPER}" if kind == "write_text" else 'text-mode open gets newline=""'
        notes.append((node.lineno, f"{path}:{node.lineno}: {what}"))
    for start, end, new in sorted(edits, reverse=True):
        text = text[:start] + new + text[end:]
    if any(new.startswith(HELPER) for _, _, new in edits) and not _defines_or_imports(tree):
        text = _add_import(text)
    return text, [note for _, note in sorted(notes)]


def _newline_edit(src: Source, call: ast.Call, kind: str) -> tuple[int, int, str] | None:
    """Add ``newline=""`` after the last argument, or turn a literal ``None`` into ``""``."""
    if any(isinstance(arg, ast.Starred) for arg in call.args) or any(k.arg is None for k in call.keywords):
        return None  # *args or **kwargs could hold the newline
    newline = argument(call, "newline", POSITIONS[kind][1])
    if newline is None:
        last = max([*call.args, *(k.value for k in call.keywords)], key=lambda n: (n.end_lineno, n.end_col_offset))
        at = src.offset(last.end_lineno, last.end_col_offset)
        return at, at, ', newline=""'
    if isinstance(newline, ast.Constant) and newline.value is None:
        return (*src.span(newline), '""')
    return None


def _is_none(node: ast.expr | None) -> bool:
    return node is None or (isinstance(node, ast.Constant) and node.value is None)


def _exact_call(src: Source, call: ast.Call) -> str | None:
    """``write_text_exact(receiver, data)``, laid out like the original call and within 120 columns; None when
    the call has arguments that would change the bytes (an encoding other than UTF-8, ``errors``, ...)."""
    data, encoding = argument(call, "data", 0), argument(call, "encoding", 1)
    if (data is None or len(call.args) > 4 or any(isinstance(arg, ast.Starred) for arg in call.args)
            or any(k.arg not in ("data", "encoding", "errors", "newline") for k in call.keywords)
            or not _is_none(argument(call, "errors", 2)) or not _is_none(argument(call, "newline", 3))
            or not (encoding is None or (isinstance(encoding, ast.Constant)
                                         and str(encoding.value).lower() in UTF8))):
        return None
    text, line_start = src.text, src.starts[call.lineno - 1]
    start, end = src.span(call)
    receiver, body = src.segment(call.func.value), src.segment(data)
    first = min(src.offset(n.lineno, n.col_offset) for n in [*call.args, *call.keywords])
    gap = text[text.index("(", src.offset(call.func.end_lineno, call.func.end_col_offset)) + 1:first]
    tail = len(src.lines[call.end_lineno - 1].rstrip("\r\n")) - (end - src.starts[call.end_lineno - 1])
    if "\n" in gap:  # a hanging indent: the arguments go on the following lines
        indent = len(gap) - gap.rindex("\n") - 1
        if data.end_lineno == data.lineno and indent + len(receiver) + 2 + len(body) + 1 + tail <= 120:
            return f"{HELPER}({gap}{receiver}, {body})"
        return f"{HELPER}({gap}{receiver},{gap}{_reindent(src, data, body, indent)})"
    column = start - line_start
    prefix = f"{HELPER}({receiver}, "
    if data.end_lineno > data.lineno:
        return prefix + _reindent(src, data, body, column + len(prefix)) + ")"
    if column + len(prefix) + len(body) + 1 + tail <= 120:
        return f"{prefix}{body})"
    return f"{HELPER}({receiver},\n{' ' * (column + len(HELPER) + 1)}{body})"


def _reindent(src: Source, data: ast.expr, body: str, new_column: int) -> str:
    """``body`` (the source of ``data``) for its new first column: continuation lines aligned at or past its old
    column move with it. Lines that begin inside a string literal, and a hanging indent, stay as they are."""
    old_column = src.column(data)
    shift = new_column - old_column
    first, *rest = body.split("\n")
    moved = [first]
    for number, line in enumerate(rest, start=data.lineno + 1):
        indent = len(line) - len(line.lstrip(" "))
        if shift == 0 or number in src.in_strings or not line.strip() or indent < old_column:
            moved.append(line)
        elif shift > 0:
            moved.append(" " * shift + line)
        else:
            moved.append(line[min(-shift, indent):])
    return "\n".join(moved)


def _defines_or_imports(tree: ast.Module) -> bool:
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == HELPER:
            return True
        if isinstance(node, ast.ImportFrom) and any((a.asname or a.name) == HELPER for a in node.names):
            return True
    return False


def _add_import(text: str) -> str:
    """Join a one-line ``from conftest import ...`` if there is one, else add the import first among the
    repository's own imports (or after the last import)."""
    tree = ast.parse(text)
    imports = [n for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom))]
    lines = io.StringIO(text, newline="").readlines()
    for node in imports:
        if isinstance(node, ast.ImportFrom) and node.module == HELPER_MODULE and node.lineno == node.end_lineno:
            line = lines[node.lineno - 1]
            code, hash_, comment = line.rstrip("\n").partition("  #")
            names = [a.name if not a.asname else f"{a.name} as {a.asname}" for a in node.names] + [HELPER]
            joined = f"from {HELPER_MODULE} import {', '.join(names)}" + (hash_ + comment if hash_ else "")
            if "(" not in code and len(joined) <= 120:
                lines[node.lineno - 1] = joined + "\n"
                return "".join(lines)
    if not imports:
        raise SystemExit("no import to anchor the conftest import on")
    local = [n for n in imports if _root_module(n) in LOCAL_MODULES]
    if local:  # first in the repository's own import group, as the other test modules have it
        anchor = local[0]
        noqa = "  # noqa: E402" if "noqa: E402" in lines[anchor.lineno - 1] else ""
        lines.insert(anchor.lineno - 1, f"from {HELPER_MODULE} import {HELPER}{noqa}\n")
    else:
        lines.insert(imports[-1].end_lineno, f"\nfrom {HELPER_MODULE} import {HELPER}\n")
    return "".join(lines)


def _root_module(node: ast.Import | ast.ImportFrom) -> str:
    if isinstance(node, ast.ImportFrom):
        return "" if node.level else (node.module or "").split(".")[0]
    return node.names[0].name.split(".")[0]


def python_files(paths: list[Path]) -> list[Path]:
    found = []
    for path in paths:
        found += sorted(path.rglob("*.py")) if path.is_dir() else [path]
    return [p for p in found if "__pycache__" not in p.parts]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--check", action="store_true", help="rewrite nothing; exit 1 if something would change")
    parser.add_argument("paths", nargs="*", type=Path, default=[ROOT / "tests"])
    options = parser.parse_args(argv)
    changed = 0
    for path in python_files(options.paths):
        before = path.read_bytes().decode("utf-8")
        name = path.resolve().relative_to(ROOT).as_posix() if path.resolve().is_relative_to(ROOT) else str(path)
        after, notes = rewrite(before, name)
        for note in notes:
            print(note)
        if after != before:
            changed += 1
            compile(after, str(path), "exec")
            if not options.check:
                path.write_bytes(after.encode("utf-8"))
    print(f"{changed} file(s) {'would change' if options.check else 'changed'}")
    return 1 if options.check and changed else 0


if __name__ == "__main__":
    sys.exit(main())
