"""Package metadata: each version agrees with ``__version__``, and every pin between the packages admits
the version in this repository, so the packages install together from one checkout. The metadata is complete
enough for PyPI, and the memlab meta-distribution installs the others without modules of its own."""
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
META = "memlab"  # packages/memlab: no module, so no __version__ and no import checks


def _meta() -> dict:
    return tomllib.loads((REPO / "packages" / META / "pyproject.toml").read_text(encoding="utf-8"))


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
    for directory in [directory for directory, _ in PACKAGES.values()] + [META]:
        assert (REPO / "packages" / directory / "LICENSE").read_bytes() == license_text, directory


def test_changelog_has_an_entry_for_each_current_version():
    changelog = (REPO / "CHANGELOG.md").read_text(encoding="utf-8")
    sections = {part.split("\n", 1)[0]: part for part in changelog.split("\n## ")[1:]}
    for name, (_, version) in _projects().items():
        assert name in sections, f"CHANGELOG.md has no '## {name}' section"
        assert f"\n### {version} - " in sections[name], f"CHANGELOG.md has no {name} {version} entry"
    assert META in sections, f"CHANGELOG.md has no '## {META}' section"
    assert f"\n### {_meta()['project']['version']} - " in sections[META]


# --- the memlab meta-distribution -----------------------------------------------------------------------

def _next_minor(version: str) -> str:
    release = Version(version).release
    return f"{release[0]}.{release[1] + 1}"


def test_meta_distribution_holds_no_modules():
    config = _meta()
    assert config["project"]["name"] == META
    assert config["tool"]["setuptools"]["packages"] == []
    assert sorted(path.name for path in (REPO / "packages" / META).iterdir()
                  if path.name not in ("build", "__pycache__") and not path.name.endswith(".egg-info")
                  ) == ["LICENSE", "README.md", "pyproject.toml"]


def test_meta_distribution_installs_the_two_tools_and_offers_the_extras():
    project = _meta()["project"]
    assert [Requirement(r).name for r in project["dependencies"]] == ["memfalsify", "orgmemory"]
    extras = {name: [Requirement(r) for r in values] for name, values in project["optional-dependencies"].items()}
    assert sorted(extras) == ["all", "jev", "llm", "mcp", "openai", "ui"]
    assert [(r.name, r.extras) for r in extras["ui"]] == [("memlab-ui", set())]
    assert [(r.name, r.extras) for r in extras["mcp"]] == [("memlab-mcp", set())]
    memfalsify_extras = _projects()["memfalsify"][0]["optional-dependencies"]
    for name in ("llm", "jev", "openai", "all"):  # each passes through to the memfalsify extra of that name
        assert name in memfalsify_extras
        assert [(r.name, r.extras) for r in extras[name]] == [("memfalsify", {name})]


def test_meta_distribution_pins_admit_exactly_one_minor_of_each_package():
    """CHANGELOG.md's rule, and the versions here: the floor is in this minor, the next minor is out."""
    project = _meta()["project"]
    projects = _projects()
    requirements = project["dependencies"] + [r for extra in project["optional-dependencies"].values() for r in extra]
    for requirement in map(Requirement, requirements):
        version = projects[requirement.name][1]
        assert requirement.specifier.contains(version), f"{requirement} excludes {version}"
        assert not requirement.specifier.contains(_next_minor(version)), f"{requirement} admits another minor"
        assert Version(version).release[:2] == _floor(requirement).release[:2], f"{requirement} admits another minor"


# --- ready for PyPI -----------------------------------------------------------------------------------------

def _all_projects() -> dict[str, dict]:
    return {**{name: project for name, (project, _) in _projects().items()}, META: _meta()["project"]}


def _ci_python_versions() -> set[str]:
    workflow = (REPO / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    return set(re.findall(r'"(3\.\d+)"', re.search(r"^\s+python-version: \[(.*)\]$", workflow, re.MULTILINE).group(1)))


def test_each_package_has_the_metadata_pypi_shows():
    for name, project in _all_projects().items():
        assert project["description"] and project["keywords"], name
        assert project["readme"] == "README.md" and project["requires-python"] == ">=3.10", name
        assert project["license"] == "Apache-2.0", name
        assert {"Homepage", "Documentation", "Repository", "Issues", "Changelog"} <= set(project["urls"]), name
        assert all(url.startswith("https://github.com/timothyzhbw-jpg/memory-lab") for url in project["urls"].values())


def test_classifiers_name_the_python_versions_ci_tests():
    tested = _ci_python_versions()
    assert tested >= {"3.10", "3.13"}
    for name, project in _all_projects().items():
        classified = {c.rsplit(" :: ", 1)[1] for c in project["classifiers"]
                      if re.fullmatch(r"Programming Language :: Python :: 3\.\d+", c)}
        assert classified == tested, name


def test_license_stays_an_spdx_expression():
    """PEP 639: setuptools refuses a License classifier next to the license expression."""
    for name, project in _all_projects().items():
        assert not [c for c in project["classifiers"] if c.startswith("License ::")], name


def test_servers_that_need_posix_say_so_in_their_classifiers():
    for name, project in _all_projects().items():
        systems = {c for c in project["classifiers"] if c.startswith("Operating System ::")}
        if name in ("memlab-mcp", "memlab-ui"):
            assert systems == {"Operating System :: MacOS", "Operating System :: POSIX :: Linux"}, name
        else:
            assert systems == {"Operating System :: OS Independent"}, name


def test_package_readmes_link_only_absolute_urls():
    """PyPI renders the README away from the repository: a relative link or image would break there."""
    for directory in [directory for directory, _ in PACKAGES.values()] + [META]:
        text = (REPO / "packages" / directory / "README.md").read_text(encoding="utf-8")
        links = (re.findall(r"\]\(([^)\s]+)", text) + re.findall(r"(?:src|href)=[\"']([^\"']+)", text)
                 + re.findall(r"^\s{0,3}\[[^\]]+\]:\s*(\S+)", text, re.MULTILINE))
        assert links, directory
        assert [link for link in links if not link.startswith("https://")] == [], directory


def test_dists_table_lists_every_package_here():
    spec = importlib.util.spec_from_file_location("dists", REPO / "scripts" / "dists.py")
    dists = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(dists)
    expected = {name: directory for name, (directory, _) in PACKAGES.items()} | {META: META}
    assert dists.PACKAGES == expected and dists.RELEASE == META
    assert sorted(path.name for path in (REPO / "packages").iterdir() if (path / "pyproject.toml").is_file()) == \
        sorted(expected.values())


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


def test_memlab_ui_asks_for_a_memfalsify_newer_than_the_one_it_outgrew():
    """memlab-ui imports dismissal_line and can_dismiss, which memfalsify 0.5.0 lacks. While both say 0.5.0 here the pin
    cannot tell them apart, so this waits for the bump: then memlab-ui's floor has to move with it."""
    projects = _projects()
    pins = [Requirement(r) for r in projects["memlab-ui"][0]["dependencies"]]
    floor = _floor(next(pin for pin in pins if pin.name == "memfalsify"))
    assert Version(projects["memfalsify"][1]) == Version("0.5.0") or floor > Version("0.5.0")


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
