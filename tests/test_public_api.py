"""Pin the supported library surface; intentional API changes update public_api.json and docs."""
import ast
import importlib
import inspect
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
PUBLIC = json.loads((REPO / "tests" / "public_api.json").read_text(encoding="utf-8"))
PACKAGES = tuple(PUBLIC)


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
        if inspect.isfunction(obj) or inspect.isclass(obj):
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
        elif inspect.isclass(obj):
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
    data = config.split("[tool.setuptools.package-data]", 1)[1].split("\n[", 1)[0]
    assert re.search(rf'^{package}\s*=\s*\["py\.typed"\]$', data, re.MULTILINE)


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
