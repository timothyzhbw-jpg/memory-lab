"""Rewrite text-mode writes in the tests to exact bytes, which tests/test_windows_guard.py requires.

Windows text mode turns every "\\n" written into "\\r\\n", so a fixture written with ``Path.write_text`` has a
different sha256 and different offsets there than on macOS and Linux. This script makes the mechanical fix:

* ``p.write_text(text)`` and ``p.write_text(text, encoding="utf-8")`` become ``write_text_exact(p, text)``
  (UTF-8 bytes, from tests/conftest.py), and the module imports it;
* a text-mode ``open(..., "w")`` / ``path.open("a", ...)`` / ``os.fdopen(fd, "w")`` gets ``newline=""``.

Calls that already pass ``newline=``, and binary or read-only opens, are left alone, so running it again
changes nothing. A ``write_text`` call with any other keyword (``errors=``, a non-UTF-8 encoding) is
reported, not rewritten. Usage: ``python scripts/exact_writes.py [--check] [path ...]`` (default: tests/);
``--check`` rewrites nothing and exits 1 if something would change.
"""
from __future__ import annotations

import argparse
import ast
import io
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HELPER, HELPER_MODULE = "write_text_exact", "conftest"
FILE_MODE = re.compile(r"[rwaxbt+]+")
UTF8 = {"utf-8", "utf8", "utf_8"}
# The repository's own modules: a new conftest import goes first among them.
LOCAL_MODULES = {"conftest", "memcore", "memfalsify", "orgmemory", "memlab_mcp", "memlab_ui", "github_fakes",
                 "jira_fakes", "orgmemory_reference"}


def text_write_mode(call: ast.Call) -> bool:
    """A literal text mode that writes: open(f, "w"), path.open("a"), os.fdopen(fd, "w+")."""
    func = call.func
    if isinstance(func, ast.Name) and func.id == "open":
        index = 1
    elif isinstance(func, ast.Attribute) and func.attr in ("open", "fdopen"):
        if isinstance(func.value, ast.Name) and func.value.id == "codecs":
            return False  # codecs.open writes in binary mode and takes no newline argument
        index = 0 if func.attr == "open" and not _is_io_module(func.value) else 1
    else:
        return False
    mode = next((k.value for k in call.keywords if k.arg == "mode"),
                call.args[index] if len(call.args) > index else None)
    if not (isinstance(mode, ast.Constant) and isinstance(mode.value, str) and FILE_MODE.fullmatch(mode.value)):
        return False
    return "b" not in mode.value and any(c in mode.value for c in "wax+")


def _is_io_module(node: ast.expr) -> bool:
    return isinstance(node, ast.Name) and node.id in ("io", "os", "_io")


def has_newline(call: ast.Call) -> bool:
    return any(k.arg == "newline" for k in call.keywords)


class Source:
    def __init__(self, text: str):
        self.text = text
        self.lines = io.StringIO(text, newline="").readlines()  # the line breaks Python counts, kept
        self.starts = [0]
        for line in self.lines:
            self.starts.append(self.starts[-1] + len(line))

    def offset(self, lineno: int, col: int) -> int:
        """Character offset of an AST (line, UTF-8 byte column) position."""
        line = self.lines[lineno - 1]
        return self.starts[lineno - 1] + len(line.encode("utf-8")[:col].decode("utf-8"))

    def span(self, node: ast.AST) -> tuple[int, int]:
        return self.offset(node.lineno, node.col_offset), self.offset(node.end_lineno, node.end_col_offset)

    def segment(self, node: ast.AST) -> str:
        start, end = self.span(node)
        return self.text[start:end]


def rewrite(text: str, path: str = "<source>") -> tuple[str, list[str]]:
    """The rewritten source and one note per change (or per call left for a human)."""
    tree = ast.parse(text, path)
    src = Source(text)
    edits: list[tuple[int, int, str]] = []
    notes: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or has_newline(node):
            continue
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr == "write_text":
            data = node.args[0] if node.args else next((k.value for k in node.keywords if k.arg == "data"), None)
            other = [k for k in node.keywords if k.arg != "data" and not (
                k.arg == "encoding" and isinstance(k.value, ast.Constant) and str(k.value.value).lower() in UTF8)]
            if data is None or other or len(node.args) > 2 or (len(node.args) == 2 and not (
                    isinstance(node.args[1], ast.Constant) and str(node.args[1].value).lower() in UTF8)):
                notes.append((node.lineno, f"{path}:{node.lineno}: write_text with other arguments; fix by hand"))
                continue
            edits.append((*src.span(node), _exact_call(src, node, data)))
            notes.append((node.lineno, f"{path}:{node.lineno}: write_text -> {HELPER}"))
        elif text_write_mode(node):
            last = max([*node.args, *(k.value for k in node.keywords)], key=lambda n: (n.end_lineno, n.end_col_offset))
            at = src.offset(last.end_lineno, last.end_col_offset)
            edits.append((at, at, ', newline=""'))
            notes.append((node.lineno, f"{path}:{node.lineno}: text-mode open gets newline=\"\""))
    for start, end, new in sorted(edits, reverse=True):
        text = text[:start] + new + text[end:]
    if any(new.startswith(HELPER) for _, _, new in edits) and not _defines_or_imports(tree):
        text = _add_import(text)
    return text, [note for _, note in sorted(notes)]


def _exact_call(src: Source, call: ast.Call, data: ast.expr) -> str:
    """``write_text_exact(receiver, data)``, laid out like the original call and within 120 columns."""
    text, line_start = src.text, src.starts[call.lineno - 1]
    start, end = src.span(call)
    receiver, body = src.segment(call.func.value), src.segment(data)
    data_start = src.offset(data.lineno, data.col_offset)
    gap = text[text.index("(", src.offset(call.func.end_lineno, call.func.end_col_offset)) + 1:data_start]
    tail = len(src.lines[call.end_lineno - 1].rstrip("\r\n")) - (end - src.starts[call.end_lineno - 1])
    if "\n" in gap:  # a hanging indent: the arguments go on the following lines
        indent = len(gap) - gap.rindex("\n") - 1
        if data.end_lineno == data.lineno and indent + len(receiver) + 2 + len(body) + 1 + tail <= 120:
            return f"{HELPER}({gap}{receiver}, {body})"
        return f"{HELPER}({gap}{receiver},{gap}{body})"
    column, data_column = start - line_start, data_start - line_start
    prefix = f"{HELPER}({receiver}, "
    if data.end_lineno > data.lineno:  # keep continuation lines aligned under the first line of the text
        return prefix + _reindent(body, data_column, column + len(prefix) - data_column) + ")"
    if column + len(prefix) + len(body) + 1 + tail <= 120:
        return f"{prefix}{body})"
    return f"{HELPER}({receiver},\n{' ' * (column + len(HELPER) + 1)}{body})"


def _reindent(body: str, column: int, shift: int) -> str:
    """Move the continuation lines aligned at or past the text's old ``column`` by ``shift``; a hanging indent
    inside a bracket stays where it is."""
    first, *rest = body.split("\n")
    moved = []
    for line in rest:
        indent = len(line) - len(line.lstrip(" "))
        if indent < column or not line.strip():
            moved.append(line)
        elif shift >= 0:
            moved.append(" " * shift + line)
        else:
            moved.append(line[min(-shift, indent):])
    return "\n".join([first, *moved])


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
