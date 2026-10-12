"""Static checks, on any OS, for the mistakes that broke the Windows and Python 3.10/3.11 CI jobs.

Everyone develops on macOS and Windows only runs in CI, about 11 minutes a job, so each of these used to cost
a full CI round. Every failure names file:line and the fix; AGENTS.md ("Windows") has the rules in short.

1. Exact writes. Windows text mode turns each "\\n" written into "\\r\\n". Evidence carries the sha256 of
   its file and character offsets of its quotes, and reports are compared byte for byte, so a fixture written
   with ``Path.write_text`` broke the hash check on Windows only. ``write_text(...)`` and a text-mode
   ``open(..., "w"/"a"/"x"/"+")`` must pass ``newline=""``; tests use ``write_text_exact(path, text)`` from
   conftest.py and ``python scripts/exact_writes.py`` rewrites them. The rule covers every Python file of the
   repository: the packages write bytes today and should keep to that. A call on a line that ends with
   ``# windows-guard: allow`` is a deliberate text-mode write.
2. Servers skip on Windows. memlab-mcp (and memlab-ui) refuse to start without the POSIX ``O_NOFOLLOW`` and
   ``dir_fd`` that confined reads need. A test that builds one, through ``create_server``, ``App``, ``serve``
   or ``StdioServerParameters``, directly or through a helper or fixture of its module, needs
   ``@requires_confinement`` (or a ``pytestmark`` with it), or must set ``confinement_supported`` itself with
   ``monkeypatch.setattr`` so that macOS takes the path Windows takes. That includes a call inside
   ``pytest.raises``: a test expecting another refusal failed on Windows because the platform check came first.
3. f-strings for Python 3.10. A backslash, a comment or the enclosing quote inside an f-string's replacement
   field parses only on 3.12+ (PEP 701). On 3.12+ the tokenizer finds them; on 3.10/3.11 the file does not
   parse, which this test reports too.

Not checked: ``typer.echo(text, nl=False)`` with a str. Four such calls write reports that are not documented
as exact on stdout, and a bytes variable cannot be told from a str here, so the rule would need an allow-list.
``pytest --simulate-windows-newlines`` (conftest.py) catches a str where a test compares stdout bytes.
"""
from __future__ import annotations

import ast
import importlib.util
import io
import sys
import tokenize
from functools import cache
from pathlib import Path

import pytest

from test_source_hygiene import ROOT, python_files

SERVER_PACKAGES = ("memlab_mcp", "memlab_ui")
STARTERS = {"create_server", "App", "serve"}  # each refuses to run without confined reads
STDIO_STARTERS = {"StdioServerParameters"}  # from mcp: memlab-mcp in a subprocess
MARKER = "requires_confinement"
ALLOW = "# windows-guard: allow"  # a deliberate text-mode write (the newline simulation's own test)


@cache
def sources() -> dict[str, str]:
    return {path.relative_to(ROOT).as_posix(): path.read_bytes().decode("utf-8") for path in python_files()
            if "site-packages" not in path.parts}  # a virtualenv without a leading dot


@cache
def trees() -> dict[str, ast.Module | SyntaxError]:
    parsed = {}
    for name, source in sources().items():
        try:
            parsed[name] = ast.parse(source, name)
        except SyntaxError as exc:
            parsed[name] = exc
    return parsed


# --- 1. exact writes ---------------------------------------------------------------------------------------

def _text_write_mode(call: ast.Call) -> bool:
    """A literal text mode that writes: ``open(f, "w")``, ``io.open(f, "a")``, ``path.open("w")``,
    ``os.fdopen(fd, "w+")``. ``codecs.open`` writes in binary mode."""
    func = call.func
    if isinstance(func, ast.Name) and func.id == "open":
        index = 1
    elif isinstance(func, ast.Attribute) and func.attr in ("open", "fdopen"):
        module = func.value.id if isinstance(func.value, ast.Name) else None
        if module == "codecs":
            return False
        index = 1 if func.attr == "fdopen" or module in ("io", "os", "_io") else 0
    else:
        return False
    mode = next((k.value for k in call.keywords if k.arg == "mode"),
                call.args[index] if len(call.args) > index else None)
    if not (isinstance(mode, ast.Constant) and isinstance(mode.value, str)
            and set(mode.value) <= set("rwaxbt+") and mode.value):
        return False
    return "b" not in mode.value and any(c in mode.value for c in "wax+")


def inexact_writes(tree: ast.Module, lines: list[str] = ()) -> list[tuple[int, str]]:
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or any(k.arg == "newline" for k in node.keywords):
            continue
        if lines and ALLOW in lines[node.lineno - 1]:
            continue
        if isinstance(node.func, ast.Attribute) and node.func.attr == "write_text":
            found.append((node.lineno, "write_text adds CR before each LF on Windows"))
        elif _text_write_mode(node):
            found.append((node.lineno, "a text-mode write adds CR before each LF on Windows"))
    return found


def test_text_writes_are_exact_bytes():
    offenders = [f"{name}:{line}: {why}" for name, tree in trees().items() if isinstance(tree, ast.Module)
                 for line, why in inexact_writes(tree, sources()[name].split("\n"))]
    assert not offenders, (
        "write exact bytes: in tests write_text_exact(path, text) from conftest.py (python scripts/exact_writes.py "
        "rewrites these), elsewhere path.write_bytes(text.encode('utf-8')) or newline=''\n" + "\n".join(offenders))


# --- 2. servers skip on Windows ----------------------------------------------------------------------------

def _server_names(tree: ast.Module) -> tuple[set[str], set[str]]:
    """Local names bound to a server constructor, and local names bound to a server package or module."""
    names, modules = set(), set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and not node.level:
            package = node.module.split(".")[0]
            for alias in node.names:
                local = alias.asname or alias.name
                if package in SERVER_PACKAGES:
                    (names if alias.name in STARTERS else modules).add(local)
                elif package == "mcp" and alias.name in STDIO_STARTERS:
                    names.add(local)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] in SERVER_PACKAGES:
                    modules.add(alias.asname or alias.name.split(".")[0])
    return names, modules


def _starts_server(function: ast.AST, names: set[str], modules: set[str]) -> int | None:
    """The line of a call that builds a server; None if there is none. A call inside ``pytest.raises`` counts:
    whether the expected refusal comes before the platform check depends on the server's order of checks."""
    def visit(node: ast.AST) -> int | None:
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id in names:
                return node.lineno
            if isinstance(func, ast.Attribute) and func.attr in STARTERS:
                root = func.value
                while isinstance(root, ast.Attribute):
                    root = root.value
                if isinstance(root, ast.Name) and root.id in modules:
                    return node.lineno
        return next((line for child in ast.iter_child_nodes(node) if (line := visit(child)) is not None), None)
    return next((line for statement in function.body if (line := visit(statement)) is not None), None)


def _fixture_name(function: ast.FunctionDef) -> str | None:
    for decorator in function.decorator_list:
        target = decorator.func if isinstance(decorator, ast.Call) else decorator
        if isinstance(target, ast.Attribute) and target.attr == "fixture":
            named = [k.value.value for k in getattr(decorator, "keywords", ()) if k.arg == "name"
                     and isinstance(k.value, ast.Constant)]
            return named[0] if named else function.name
    return None


def _skipped(decorators: list[ast.expr]) -> bool:
    """``@requires_confinement``, a ``skipif`` on confined reads or the platform, or ``pytest.mark.skip``."""
    for decorator in decorators:
        text = ast.unparse(decorator)
        if MARKER in text or text == "pytest.mark.skip" or text.startswith("pytest.mark.skip("):
            return True
        if text.startswith("pytest.mark.skipif(") and any(
                word in text for word in ("confinement_supported", "os.name", "sys.platform")):
            return True
    return False


def _pytestmark(body: list[ast.stmt]) -> list[ast.expr]:
    for node in body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "pytestmark"
                                                for t in node.targets):
            return list(node.value.elts) if isinstance(node.value, (ast.List, ast.Tuple)) else [node.value]
    return []


def _patches_confinement(function: ast.AST) -> bool:
    return any(isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "setattr"
               and "confinement_supported" in ast.unparse(node) for node in ast.walk(function))


def unskipped_server_tests(tree: ast.Module) -> list[tuple[int, str]]:
    names, modules = _server_names(tree)
    if not names and not modules:
        return []
    functions, classes = [], {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            functions.append(node)
        elif isinstance(node, ast.ClassDef):
            for member in node.body:
                if isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    functions.append(member)
                    classes[member] = node
    # Helpers and fixtures that build a server, to a fixed point (a fixture may use a helper or another fixture).
    helpers, fixtures = set(), set()
    changed = True
    while changed:
        changed = False
        for function in functions:
            fixture = _fixture_name(function)
            if function.name in helpers or (fixture and fixture in fixtures):
                continue
            uses = {a.arg for a in function.args.args} & fixtures
            if uses or _starts_server(function, names | helpers, modules) is not None:
                if fixture:
                    fixtures.add(fixture)
                else:
                    helpers.add(function.name)
                changed = True
    module_skipped = _skipped(_pytestmark(tree.body))
    found = []
    for function in functions:
        if not function.name.startswith("test") or _fixture_name(function):
            continue
        cls = classes.get(function)
        if module_skipped or _skipped(function.decorator_list) or (
                cls is not None and _skipped(cls.decorator_list + _pytestmark(cls.body))):
            continue
        uses = sorted({a.arg for a in function.args.args} & fixtures)
        line = _starts_server(function, names | helpers, modules)
        if (line is not None or uses) and not _patches_confinement(function):
            how = f"line {line} builds a server" if line is not None else f"fixture {', '.join(uses)} builds a server"
            found.append((function.lineno, f"{function.name}: {how}"))
    return found


def test_server_tests_are_skipped_without_confined_reads():
    offenders = [f"{name}:{line}: {why}" for name, tree in trees().items()
                 if Path(name).name.startswith("test_") and isinstance(tree, ast.Module)
                 for line, why in unskipped_server_tests(tree)]
    assert not offenders, (
        "the server refuses to start without POSIX confined reads (Windows): decorate these tests with "
        "@requires_confinement from conftest.py, or monkeypatch confinement_supported in them so every OS takes "
        "the same path\n" + "\n".join(offenders))


# --- 3. f-strings that Python 3.10 parses ------------------------------------------------------------------

def _quote(token: str) -> str:
    body = token.lstrip("rRbBuUfFtT")
    return body[:3] if body[:3] in ('"""', "'''") else body[:1]


def pep701_fstrings(source: str) -> list[tuple[int, str]]:
    """Inside a replacement field: a backslash, a comment, or the quote of an enclosing single-line f-string.
    Python 3.12+ only: older tokenizers see a whole f-string as one STRING token."""
    if sys.version_info < (3, 12):
        return []
    found, stack = [], []  # stack: [quote, open braces] per enclosing f-string
    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        kind, text, line = token.type, token.string, token.start[0]
        in_field = any(depth for _, depth in stack[:-1]) or bool(
            stack and stack[-1][1] and kind != tokenize.FSTRING_MIDDLE)  # FSTRING_MIDDLE there: a format spec
        if in_field:
            quotes = {quote for quote, _ in stack if len(quote) == 1}
            if kind in (tokenize.STRING, tokenize.FSTRING_MIDDLE) and "\\" in text:
                found.append((line, "a backslash inside an f-string replacement field"))
            elif kind == tokenize.COMMENT:
                found.append((line, "a comment inside an f-string replacement field"))
            elif kind in (tokenize.STRING, tokenize.FSTRING_START) and _quote(text)[0] in quotes:
                found.append((line, "the enclosing quote reused inside an f-string replacement field"))
        if kind == tokenize.FSTRING_START:
            stack.append([_quote(text), 0])
        elif kind == tokenize.FSTRING_END:
            stack.pop()
        elif stack and kind == tokenize.OP and text in "{}":
            stack[-1][1] += 1 if text == "{" else -1
    return found


def test_fstrings_parse_on_python_310():
    offenders = []
    for name, tree in trees().items():
        if isinstance(tree, SyntaxError):
            offenders.append(f"{name}:{tree.lineno}: does not parse on Python "
                             f"{sys.version_info.major}.{sys.version_info.minor}: {tree.msg}")
        else:
            offenders += [f"{name}:{line}: {why}" for line, why in pep701_fstrings(sources()[name])]
    assert not offenders, ("Python 3.10 and 3.11 reject these: compute the value before the f-string\n"
                           + "\n".join(offenders))


# --- the rules catch what broke CI -------------------------------------------------------------------------

def test_the_rules_catch_what_broke_ci():
    """Each rule flags the code that failed in CI (or a minimal copy of it) and passes its fix."""
    fixture = '(root / "sources" / "main.txt").write_text(text, encoding="utf-8")\n'  # c92d759, evidence hash
    assert inexact_writes(ast.parse(fixture)) == [(1, "write_text adds CR before each LF on Windows")]
    for call in ('open(p, "a", encoding="utf-8")', 'p.open("w")', 'io.open(p, mode="x")', 'os.fdopen(fd, "w+")'):
        assert len(inexact_writes(ast.parse(call))) == 1, call
    for call in ('p.write_text(t, newline="")', 'p.open("ab")', 'open(p)', 'open(p, "rb")', 'codecs.open(p, "w")',
                 'p.open(mode)', 'p.write_bytes(t.encode())', 'tarfile.open("a.tar", "w")', 'open(p, "w", newline="")'):
        assert inexact_writes(ast.parse(call)) == [], call
    spec = importlib.util.spec_from_file_location("exact_writes", ROOT / "scripts" / "exact_writes.py")
    script = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(script)
    fixed, _ = script.rewrite("from conftest import EXAMPLES\n" + fixture + 'open(p, "a").write(t)\n')
    assert inexact_writes(ast.parse(fixed)) == [] and "write_text_exact(root /" in fixed
    allowed = fixture.rstrip("\n") + "  " + ALLOW + "\n"
    assert inexact_writes(ast.parse(allowed), allowed.split("\n")) == []
    assert script.rewrite(allowed)[0] == allowed  # the script leaves a deliberate text-mode write alone too

    servers = """
from conftest import requires_confinement
from memlab_mcp.server import create_server
from memlab_ui import server as ui_server

def make_app(root):
    return ui_server.App(root)

@pytest.fixture
def app(root):
    return make_app(root)

def test_forgot_the_marker(root):
    call(create_server(root), "memfalsify_claims")

def test_forgot_it_with_a_fixture(app):
    assert get(app, "/")

@requires_confinement
def test_marked(app):
    assert get(app, "/")

def test_refusal(root, monkeypatch):
    monkeypatch.setattr(ui_server, "confinement_supported", lambda: False)
    with pytest.raises(ValueError):
        create_server(root)

def test_create_server_refuses_a_bad_openai_configuration(root, settings, problem):  # as in 96b2d62
    with pytest.raises(ValueError, match=problem):
        create_server(root, allow_model_verifiers=True, **settings)
"""
    assert [why for _, why in unskipped_server_tests(ast.parse(servers))] == [
        "test_forgot_the_marker: line 14 builds a server", "test_forgot_it_with_a_fixture: fixture app builds a server",
        "test_create_server_refuses_a_bad_openai_configuration: line 30 builds a server"]
    assert unskipped_server_tests(ast.parse("pytestmark = requires_confinement\n" + servers)) == []

    if sys.version_info >= (3, 12):
        for bad in ("f\"{'\\\\n'.join(lines)}\"", 'f"{x # note\n}"', 'f"{d["key"]}"', "f\"{f'a\\\\n'}\""):
            assert len(pep701_fstrings(bad + "\n")) == 1, bad
        for good in ('f"{x!r:>{width}}\\n"', 'f"a\\n{b}"', "f\"{d['key']}\"", 'f"""{"a"}"""', 'f"{{x}}\\n"'):
            assert pep701_fstrings(good + "\n") == [], good
    else:
        with pytest.raises(SyntaxError):
            ast.parse("f\"{'\\\\n'.join(lines)}\"\n")
