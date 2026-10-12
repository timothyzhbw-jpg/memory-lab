"""Package metadata: each version agrees with ``__version__``, and every pin between the packages admits
the version in this repository, so the five packages install together from one checkout."""
import ast
import importlib
import importlib.util
import json
import re
from pathlib import Path


try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10: tomli is a test dependency there (CI, setup-local.sh)
    import tomli as tomllib
from packaging.requirements import Requirement  # a pytest dependency
from packaging.version import Version

REPO = Path(__file__).resolve().parent.parent
# distribution name -> (directory under packages/, import name)
PACKAGES = {"memlab-core": ("memcore", "memcore"), "memfalsify": ("memfalsify", "memfalsify"),
            "orgmemory": ("orgmemory", "orgmemory"), "memlab-mcp": ("memlab-mcp", "memlab_mcp"),
            "memlab-ui": ("memlab-ui", "memlab_ui")}


def _projects() -> dict[str, tuple[dict, str]]:
    found = {}
    for name, (directory, module) in PACKAGES.items():
        root = REPO / "packages" / directory
        project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]
        init = (root / "src" / module / "__init__.py").read_text(encoding="utf-8")
        found[name] = (project, re.search(r'^__version__ = "([^"]+)"$', init, re.MULTILINE).group(1))
    return found


def test_versions_agree_with_the_modules():
    for name, (project, version) in _projects().items():
        assert (project["name"], project["version"]) == (name, version)


def test_pins_between_packages_admit_the_versions_here():
    projects = _projects()
    for name, (project, _) in projects.items():
        requirements = list(project.get("dependencies", []))
        requirements += [r for extra in project.get("optional-dependencies", {}).values() for r in extra]
        for requirement in map(Requirement, requirements):
            if requirement.name in projects:
                version = projects[requirement.name][1]
                assert requirement.specifier.contains(version), f"{name}: {requirement} excludes {version}"


def test_each_package_ships_the_license():
    """setuptools puts packages/<name>/LICENSE into the sdist and wheel; it must be the repository's."""
    license_text = (REPO / "LICENSE").read_bytes()
    for directory, _ in PACKAGES.values():
        assert (REPO / "packages" / directory / "LICENSE").read_bytes() == license_text, directory


def test_changelog_has_an_entry_for_each_current_version():
    changelog = (REPO / "CHANGELOG.md").read_text(encoding="utf-8")
    sections = {part.split("\n", 1)[0]: part for part in changelog.split("\n## ")[1:]}
    for name, (_, version) in _projects().items():
        assert name in sections, f"CHANGELOG.md has no '## {name}' section"
        assert f"\n### {version} - " in sections[name], f"CHANGELOG.md has no {name} {version} entry"


# The names each module defined in released versions of the packages here (from their tags; see _source).
RELEASED_EXPORTS = json.loads((REPO / "tests" / "released_exports.json").read_text(encoding="utf-8"))


def _imports(source: Path, top: str) -> set[tuple[str, str]]:
    """Every (module, name) a package imports from ``top`` with ``from top... import name``."""
    found = set()
    for path in sorted(source.rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ImportFrom) and node.level == 0 and (node.module or "").split(".")[0] == top:
                found.update((node.module, alias.name) for alias in node.names)
    return found


def _floor(requirement: Requirement) -> Version:
    floors = [Version(spec.version) for spec in requirement.specifier if spec.operator in (">=", "==", "~=")]
    assert floors, f"{requirement} has no lower bound"
    return max(floors)


def _missing(directory: str, module: str, requirement: Requirement, current: str) -> list[tuple[str, str]]:
    """The names the package imports from the package ``requirement`` names that the lowest version its pin
    admits does not define. A name can be a submodule (``from memcore import htmlpage``)."""
    floor = _floor(requirement)
    imports = _imports(REPO / "packages" / directory / "src" / module, PACKAGES[requirement.name][1])
    if floor == Version(current):
        def defined(name: str, symbol: str) -> bool:
            return hasattr(importlib.import_module(name), symbol) or _is_module(f"{name}.{symbol}")
    else:
        released = RELEASED_EXPORTS.get(requirement.name, {})
        recorded = [version for version in released if Version(version) == floor]
        assert recorded, f"record {requirement.name} {floor}'s module names in tests/released_exports.json"
        names = released[recorded[0]]  # module -> the top-level names in its source

        def defined(name: str, symbol: str) -> bool:
            return symbol in names.get(name, ()) or f"{name}.{symbol}" in names
    return sorted((name, symbol) for name, symbol in imports if not defined(name, symbol))


def _is_module(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except ModuleNotFoundError:
        return False


def test_each_package_imports_only_what_its_lowest_dependencies_define():
    """A pin that admits an older version of another package here than the code needs installs, then fails
    with ImportError."""
    projects = _projects()
    for name, (directory, module) in PACKAGES.items():
        pins = [Requirement(r) for r in projects[name][0].get("dependencies", [])]
        if name != "memlab-core":
            assert [pin.name for pin in pins].count("memlab-core") == 1, name
        for pin in pins:
            if pin.name in projects:
                assert not _missing(directory, module, pin, projects[pin.name][1]), (name, str(pin))


def test_the_import_check_catches_a_pin_that_is_too_low():
    projects = _projects()
    missing = _missing("memfalsify", "memfalsify", Requirement("memlab-core>=0.3,<0.5"),
                       projects["memlab-core"][1])
    assert ("memcore.reading", "ReadLimitError") in missing and ("memcore.reading", "take") in missing
    missing = _missing("memlab-mcp", "memlab_mcp", Requirement("memfalsify>=0.4,<0.5"), projects["memfalsify"][1])
    assert ("memfalsify", "openai_compat") in missing  # the openai verifier is new in memfalsify 0.5.0


# The dependency metadata of released versions, from their tag (see the file's _source).
RELEASED = json.loads((REPO / "tests" / "released_metadata.json").read_text(encoding="utf-8"))


def test_changed_dependency_metadata_gets_a_new_version():
    """A released version's metadata is on PyPI: a package whose dependencies differ needs a new version."""
    for name, (project, version) in _projects().items():
        released = RELEASED.get(name, {}).get(version)
        if released is None:
            continue  # a version that was never released
        current = {"dependencies": project.get("dependencies", []),
                   "optional-dependencies": project.get("optional-dependencies", {})}
        assert current == released, f"{name} {version} was released with other dependencies: bump its version"


def test_the_released_metadata_check_catches_a_pin_change():
    released = RELEASED["orgmemory"]["0.4.0"]
    assert released["dependencies"][0] == "memlab-core>=0.3,<0.4"
    assert _projects()["orgmemory"][1] != "0.4.0"  # its pin changed, so its version did too
