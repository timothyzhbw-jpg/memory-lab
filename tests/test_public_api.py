"""Pin the supported library surface; intentional API changes update public_api.json and docs."""
import ast
import importlib
import inspect
import json
import os
import re
import subprocess
import sys
import typing
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
PUBLIC = json.loads((REPO / "tests" / "public_api.json").read_text(encoding="utf-8"))
PACKAGES = tuple(PUBLIC)


def _is_class(obj) -> bool:
    """A real class. On Python 3.10, inspect.isclass is also true for a generic alias such as Transport."""
    return inspect.isclass(obj) and typing.get_origin(obj) is None


@pytest.mark.parametrize("package", PACKAGES)
def test_exports_are_deliberate_importable_and_documented(package):
    module = importlib.import_module(package)
    assert module.__all__ == PUBLIC[package]
    assert len(module.__all__) == len(set(module.__all__))
    imported = {}
    exec(f"from {package} import *", imported)
    assert set(imported) - {"__builtins__"} == set(PUBLIC[package])
    for name in PUBLIC[package]:
        obj = getattr(module, name)
        assert imported[name] is obj
        # Both references must give each export its own entry, not merely mention a substring.
        for reference in ("api.md", "api.zh-CN.md"):
            docs = (REPO / "docs" / reference).read_text(encoding="utf-8")
            assert f"| `{package}.{name}` |" in docs, (reference, package, name)
        # Re-export the actual implementation, preserving defining-module imports.
        if inspect.isfunction(obj) or _is_class(obj):
            defining = importlib.import_module(obj.__module__)
            assert getattr(defining, obj.__name__) is obj


@pytest.mark.parametrize("package", PACKAGES)
def test_public_signatures_have_annotations(package):
    module = importlib.import_module(package)
    for name in PUBLIC[package]:
        obj = getattr(module, name)
        callables = []
        if inspect.isfunction(obj):
            callables.append(obj)
        elif _is_class(obj):
            # Inspect explicit methods only, not generated Pydantic/dataclass constructors.
            for method_name, method in vars(obj).items():
                if method_name.startswith("_") and method_name not in (
                    "__init__", "__enter__", "__exit__", "__call__"
                ):
                    continue
                if isinstance(method, (classmethod, staticmethod)):
                    method = method.__func__
                if isinstance(method, property):
                    method = method.fget
                if inspect.isfunction(method) and method.__module__ == obj.__module__:
                    callables.append(method)
        for method in callables:
            signature = inspect.signature(method)
            assert signature.return_annotation is not inspect.Signature.empty, (package, name, method)
            for parameter in signature.parameters.values():
                if parameter.name not in ("self", "cls"):
                    assert parameter.annotation is not inspect.Parameter.empty, (package, name, parameter)


def _assert_example_uses_public_names(source, location):
    """Check direct imports and qualified uses, including imported aliases."""
    tree = ast.parse(source)
    aliases = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            package = node.module.split(".")[0]
            if package not in PUBLIC:
                continue
            assert node.module == package, (location, node.module)
            for imported in node.names:
                assert imported.name in PUBLIC[package], (location, package, imported.name)
        elif isinstance(node, ast.Import):
            for imported in node.names:
                package = imported.name.split(".")[0]
                if package in PUBLIC:
                    assert imported.name == package, (location, imported.name)
                    aliases[imported.asname or package] = package
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            package = aliases.get(node.value.id)
            if package:
                assert node.attr in PUBLIC[package], (location, package, node.attr)


def test_readme_examples_use_only_public_names():
    readmes = sorted(REPO.glob("README*.md"))
    for directory in ("packages", "benchmarks", "examples", "skills"):
        readmes.extend(sorted((REPO / directory).rglob("README*.md")))
    checked = 0
    for readme in readmes:
        text = readme.read_text(encoding="utf-8")
        for source in re.findall(r"```(?:python|py)\s*\n(.*?)```", text, re.DOTALL):
            _assert_example_uses_public_names(source, readme.relative_to(REPO))
            checked += 1
    assert checked > 0  # Avoid passing vacuously if Markdown fence matching changes.


@pytest.mark.parametrize("source", [
    "from memfalsify.audit import audit",
    "from memfalsify import corpus",
    "import orgmemory.replay",
    "import memfalsify as mf\nmf.cli.app()",
])
def test_readme_checker_rejects_internal_names(source):
    with pytest.raises(AssertionError):
        _assert_example_uses_public_names(source, "synthetic example")


@pytest.mark.parametrize("package", PACKAGES)
def test_typed_markers_are_explicit_package_data(package):
    directory = "memlab-mcp" if package == "memlab_mcp" else package
    root = REPO / "packages" / directory
    assert (root / "src" / package / "py.typed").is_file()
    config = (root / "pyproject.toml").read_text(encoding="utf-8")
    # Keep this check compatible with Python 3.10 without a TOML-parser dependency.
    # py.typed must be listed. Additional package-data files are allowed; an exact
    # ["py.typed"] array would fail when a package later ships schemas or prompts.
    data = config.split("[tool.setuptools.package-data]", 1)[1].split("\n[", 1)[0]
    match = re.search(rf"^{re.escape(package)}\s*=\s*\[([^\]]*)\]", data, re.MULTILINE)
    assert match, data
    assert re.search(r"""['"]py\.typed['"]""", match.group(1))


def _documented_signatures(text):
    """Last Python fence under each package heading is the signature block."""
    parts = re.split(r"^### `(memcore|memfalsify|orgmemory|memlab_mcp)`\s*$", text, flags=re.M)
    found = {}
    index = 1
    while index < len(parts) - 1:
        package, body = parts[index], parts[index + 1]
        fences = re.findall(r"```python\n(.*?)```", body, re.S)
        signatures = {}
        for line in fences[-1].splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                signatures[line.split("(", 1)[0]] = line
        found[package] = signatures
        index += 2
    return found


def _annotation_matches(func, live, node):
    if node is None:
        assert live in (inspect.Signature.empty, inspect.Parameter.empty)
        return
    text = ast.unparse(node)
    if isinstance(live, str):
        assert re.sub(r"\s+", "", live) == re.sub(r"\s+", "", text), (func, live, text)
        return
    assert eval(text, func.__globals__) == live, (func, text, live)


def _default_matches(func, param, node):
    if node is None:
        assert param.default is inspect.Parameter.empty, (func, param.name)
        return
    if isinstance(node, ast.Constant) or isinstance(node, (ast.Tuple, ast.List, ast.Set, ast.Dict)):
        assert param.default == ast.literal_eval(ast.unparse(node)), (func, param.name)
        return
    if isinstance(node, ast.Name) and node.id == "None":
        assert param.default is None, (func, param.name)
        return
    if isinstance(node, ast.Name) and callable(param.default):
        assert getattr(param.default, "__name__", None) == node.id, (func, param.name)
        return
    value = eval(ast.unparse(node), func.__globals__)
    assert param.default == value or param.default is value, (func, param.name, value)


def test_documented_signatures_match_code_in_both_languages():
    """Signature fences track parameters and literal defaults without forbidding new names elsewhere."""
    english = _documented_signatures((REPO / "docs" / "api.md").read_text(encoding="utf-8"))
    chinese = _documented_signatures((REPO / "docs" / "api.zh-CN.md").read_text(encoding="utf-8"))
    assert english == chinese
    for package, signatures in english.items():
        module = importlib.import_module(package)
        functions = [name for name in module.__all__ if inspect.isfunction(getattr(module, name))]
        assert set(signatures) == set(functions), package
        for name in functions:
            func = getattr(module, name)
            documented = ast.parse("def " + signatures[name] + ":\n    pass").body[0]
            assert isinstance(documented, ast.FunctionDef)
            signature = inspect.signature(func)
            assert not documented.args.posonlyargs, name
            positional = [(arg.arg, arg.annotation) for arg in documented.args.args]
            keyword = [(arg.arg, arg.annotation) for arg in documented.args.kwonlyargs]
            vararg = documented.args.vararg.arg if documented.args.vararg else None
            kwarg = documented.args.kwarg.arg if documented.args.kwarg else None
            code_positional = [param.name for param in signature.parameters.values()
                               if param.kind is inspect.Parameter.POSITIONAL_OR_KEYWORD]
            code_keyword = [param.name for param in signature.parameters.values()
                            if param.kind is inspect.Parameter.KEYWORD_ONLY]
            code_vararg = next((param.name for param in signature.parameters.values()
                                if param.kind is inspect.Parameter.VAR_POSITIONAL), None)
            code_kwarg = next((param.name for param in signature.parameters.values()
                               if param.kind is inspect.Parameter.VAR_KEYWORD), None)
            assert [item[0] for item in positional] == code_positional, name
            assert [item[0] for item in keyword] == code_keyword, name
            assert vararg == code_vararg and kwarg == code_kwarg, name
            positional_defaults = [None] * (len(documented.args.args) - len(documented.args.defaults))
            positional_defaults += list(documented.args.defaults)
            annotations = {item[0]: item[1] for item in positional + keyword}
            defaults = dict(zip((item[0] for item in positional), positional_defaults))
            defaults.update(zip((item[0] for item in keyword), documented.args.kw_defaults))
            if documented.args.vararg is not None:
                annotations[vararg] = documented.args.vararg.annotation
            if documented.args.kwarg is not None:
                annotations[kwarg] = documented.args.kwarg.annotation
            for param in signature.parameters.values():
                _annotation_matches(func, param.annotation, annotations.get(param.name))
                if param.kind not in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD):
                    _default_matches(func, param, defaults.get(param.name))
            _annotation_matches(func, signature.return_annotation, documented.returns)


def test_attention_statuses_cannot_be_mutated():
    from memfalsify import ATTENTION
    assert isinstance(ATTENTION, frozenset)
    assert ATTENTION == {"challenged", "conflicted", "candidate_counterevidence", "disputed"}
    summary = {"challenged": 1, "supported": 2}
    assert ATTENTION & summary.keys()
    assert not (ATTENTION & {"supported": 0}.keys())
    with pytest.raises(AttributeError):
        ATTENTION.add("supported")


def test_public_signature_defaults_do_not_capture_the_environment(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_should_not_appear_in_signature")
    for package in PACKAGES:
        module = importlib.import_module(package)
        for name in module.__all__:
            obj = getattr(module, name)
            functions = []
            if inspect.isfunction(obj):
                functions.append(obj)
            elif _is_class(obj):
                functions.append(obj.__init__)
            for func in functions:
                rendered = str(inspect.signature(func))
                assert "ghp_should_not_appear_in_signature" not in rendered, (package, name)
                for param in inspect.signature(func).parameters.values():
                    assert param.default is not os.environ, (package, name, param.name)


def test_offline_public_api_does_not_import_model_sdks():
    # A fresh interpreter catches accidental eager imports even when the test suite already
    # loaded SDKs. Refuse network access and SDK imports while exercising default entry points.
    script = """
import importlib.abc
import socket
import sys
from pathlib import Path

class RejectSDK(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split(".")[0] in {"anthropic", "typesafe_sdk"}:
            raise AssertionError("eager SDK import: " + fullname)

sys.meta_path.insert(0, RejectSDK())
def no_network(*args, **kwargs):
    raise AssertionError("offline API attempted network access")
socket.create_connection = no_network
from memfalsify import audit, parse_memory
from orgmemory import Dataset, replay
claims, files = parse_memory(Path("examples/memfalsify/MEMORY.md"))
assert claims and files
assert audit(Path("examples/memfalsify/MEMORY.md")).findings
assert replay(Dataset.load(Path("examples/orgmemory/feature-a"))).states
"""
    env = dict(os.environ, PYTHONPATH=os.pathsep.join(str(REPO / "packages" / directory / "src")
                   for directory in ("memcore", "memfalsify", "orgmemory")))
    result = subprocess.run([sys.executable, "-c", script], cwd=REPO, env=env,
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
