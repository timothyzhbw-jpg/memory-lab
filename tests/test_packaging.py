"""Package metadata: each version agrees with ``__version__``, and every pin between the packages admits
the version in this repository, so the five packages install together from one checkout."""
import re
from pathlib import Path

import pytest

tomllib = pytest.importorskip("tomllib")  # Python 3.11+
from packaging.requirements import Requirement  # noqa: E402  (a pytest dependency)

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
