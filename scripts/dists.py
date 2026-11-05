#!/usr/bin/env python3
"""Build and check the files that .github/workflows/publish.yml uploads. This script never uploads anything.

    python scripts/dists.py build DIST               one sdist and one wheel per package, in DIST/<distribution>/
    python scripts/dists.py check DIST --confirm V   stop (exit 1) if anything built should not go to PyPI
    python scripts/dists.py needs DIST --extras ui   the wheels that memlab[ui] installs from this repository
    python scripts/dists.py release                  the release version: memlab's, in this checkout

``build`` needs the tools in scripts/publish-requirements.txt. It copies each package to a temporary directory first,
so it leaves the checkout clean and a stale ``*.egg-info`` from an editable install cannot reach an sdist.

``check`` compares what is inside the built files with this checkout: the name and version in each file, the pins
between the packages, the licence, ``py.typed``, the web page's images, and the README links, which must work on
pypi.org. The version in ``--confirm`` must equal the built memlab's, which names the release. See
docs/publishing.md.
"""
from __future__ import annotations

import argparse
import importlib.util
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from email.parser import BytesParser
from hashlib import sha256
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10: build depends on tomli there
    import tomli as tomllib
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

REPO = Path(__file__).resolve().parent.parent
# Distribution -> directory under packages/, in upload order: each comes after everything it depends on, so the
# index never offers a package whose pins nothing can satisfy. publish.yml uploads in this order.
PACKAGES = {"memlab-core": "memcore", "memfalsify": "memfalsify", "orgmemory": "orgmemory",
            "memlab-mcp": "memlab-mcp", "memlab-ui": "memlab-ui", "memlab": "memlab"}
RELEASE = "memlab"  # holds no modules, and its version names the release
MODULES = {"memlab-core": "memcore", "memfalsify": "memfalsify", "orgmemory": "orgmemory",
           "memlab-mcp": "memlab_mcp", "memlab-ui": "memlab_ui"}
IMAGES = {"memlab-ui": ("memlab_ui/static/memory-lab-icon-32.png", "memlab_ui/static/memory-lab-icon-64.png",
                        "memlab_ui/static/ui-dark.png", "memlab_ui/static/ui-light.png")}
VERSION = re.compile(r"[0-9][0-9A-Za-z.!+-]*")
_LINK = re.compile(r"\]\(([^)\s]+)|(?:src|href)=[\"']([^\"']+)|^\s{0,3}\[[^\]]+\]:\s*(\S+)", re.MULTILINE)


def _display():
    """memcore.display, by path: the build tools' environment holds neither memlab-core nor pydantic."""
    spec = importlib.util.spec_from_file_location("memcore_display",
                                                  REPO / "packages" / "memcore" / "src" / "memcore" / "display.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


display = _display()


def build(out: Path, repo: Path = REPO) -> None:
    ignore = shutil.ignore_patterns("build", "dist", "*.egg-info", "__pycache__", "*.pyc")
    for name, directory in PACKAGES.items():
        with tempfile.TemporaryDirectory() as scratch:
            source = shutil.copytree(repo / "packages" / directory, Path(scratch) / directory, ignore=ignore)
            subprocess.run([sys.executable, "-m", "build", "--no-isolation", "--outdir", str(out / name), str(source)],
                           check=True)


def relative_links(markdown: str) -> list[str]:
    """Link and image targets that only work next to the file in this repository, not on pypi.org."""
    targets = [next(filter(None, groups)) for groups in _LINK.findall(markdown)]
    return [target for target in targets if not re.match(r"https?://|#", target)]


def expected_versions(repo: Path = REPO) -> dict[str, str]:
    return {name: tomllib.loads((repo / "packages" / directory / "pyproject.toml").read_text(encoding="utf-8"))
            ["project"]["version"] for name, directory in PACKAGES.items()}


def _wheel(path: Path) -> tuple[object, list[str]]:
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        metadata = next(name for name in names if name.endswith(".dist-info/METADATA"))
        return BytesParser().parsebytes(archive.read(metadata)), names


def _sdist(path: Path) -> tuple[object, list[str]]:
    with tarfile.open(path) as archive:
        members = archive.getnames()
        top = members[0].split("/")[0]
        handle = archive.extractfile(f"{top}/PKG-INFO")
        return BytesParser().parsebytes(handle.read()), [member.removeprefix(f"{top}/") for member in members]


def _contents(name: str, kind: str, names: list[str]) -> list[str]:
    """What this file must hold, wherever it is short."""
    problems = []
    module = MODULES.get(name)
    prefix = "src/" if kind == "sdist" else ""
    required = [f"{prefix}{module}/py.typed"] if module else []
    required += [prefix + image for image in IMAGES.get(name, ())]
    if kind == "sdist":
        required += ["LICENSE", "README.md", "pyproject.toml", "PKG-INFO"]
    elif not any(entry.endswith(".dist-info/licenses/LICENSE") for entry in names):
        problems.append(f"{name}: the {kind} holds no LICENSE")
    problems += [f"{name}: the {kind} holds no {entry}" for entry in required if entry not in names]
    if name == RELEASE and kind == "wheel":
        problems += [f"{name}: the wheel should hold no modules, but has {entry}" for entry in names
                     if ".dist-info/" not in entry]
    return problems


def problems_in(out: Path, confirm: str, repo: Path = REPO) -> tuple[list[str], dict[str, tuple[str, list[Path]]]]:
    """Every reason not to publish, and what was found: distribution -> (version, files)."""
    problems: list[str] = []
    found: dict[str, tuple[str, list[Path]]] = {}
    expected = expected_versions(repo)
    built: dict[str, object] = {}
    requires: list[tuple[str, str, str]] = []
    if out.is_dir():
        problems += [f"unexpected entry in the output directory: {entry.name}" for entry in sorted(out.iterdir())
                     if entry.name not in PACKAGES]
    for name in PACKAGES:
        directory = out / name
        files = sorted(directory.iterdir()) if directory.is_dir() else []
        wheels = [file for file in files if file.name.endswith(".whl")]
        sdists = [file for file in files if file.name.endswith(".tar.gz")]
        problems += [f"{name}: unexpected file {file.name}" for file in files if file not in wheels + sdists]
        if len(wheels) != 1 or len(sdists) != 1:
            problems.append(f"{name}: expected one wheel and one sdist, found {len(wheels)} and {len(sdists)}")
            continue
        for kind, path, read in (("wheel", wheels[0], _wheel), ("sdist", sdists[0], _sdist)):
            try:
                metadata, names = read(path)
            except (OSError, EOFError, KeyError, StopIteration, tarfile.TarError, zipfile.BadZipFile):
                problems.append(f"{name}: the {kind} {path.name} cannot be read")
                continue
            built[name] = metadata
            requires += [(name, kind, line) for line in metadata.get_all("Requires-Dist") or ()]
            if canonicalize_name(str(metadata["Name"])) != canonicalize_name(name):
                problems.append(f"{name}: the {kind} says its name is {metadata['Name']}")
            if metadata["Version"] != expected[name]:
                problems.append(f"{name}: the {kind} holds version {metadata['Version']}, the checkout {expected[name]}")
            if metadata["Description-Content-Type"] != "text/markdown":
                problems.append(f"{name}: the {kind} does not mark its README as Markdown")
            problems += [f"{name}: the README link {link} would break on PyPI"
                         for link in relative_links(str(metadata.get_payload()))]
            problems += _contents(name, kind, names)
        if name in built:
            found[name] = (str(built[name]["Version"]), [wheels[0], sdists[0]])
    for name, kind, line in requires:  # pip reads the wheel's and an sdist install reads the sdist's, so check both
        requirement = Requirement(line)
        pinned = canonicalize_name(requirement.name)
        if pinned in found and not requirement.specifier.contains(found[pinned][0]):
            problems.append(f"{name}: its pin {requirement} excludes the built {pinned} {found[pinned][0]} ({kind})")
    if not VERSION.fullmatch(confirm):
        problems.append("the confirm input is not a version number")
    elif RELEASE in found and confirm != found[RELEASE][0]:
        problems.append(f"the confirm input differs from the built {RELEASE} version {found[RELEASE][0]}: nothing "
                        "may be uploaded; type that version, or stop here if it is not the release you meant")
    return problems, found


def closure(out: Path, name: str, extras: tuple[str, ...] = ()) -> list[Path]:
    """The wheels from this repository that installing ``name[extras]`` needs, read from the built metadata."""
    wheels: dict[str, Path] = {}
    seen, pending = set(), [(name, frozenset(extras))]
    while pending:
        current, wanted = pending.pop()
        if (current, wanted) in seen:
            continue
        seen.add((current, wanted))
        wheels[current] = next((out / current).glob("*.whl"))
        metadata, _ = _wheel(wheels[current])
        for line in metadata.get_all("Requires-Dist") or ():
            requirement = Requirement(line)
            pinned = canonicalize_name(requirement.name)
            if pinned in PACKAGES and (requirement.marker is None or any(
                    requirement.marker.evaluate({"extra": extra}) for extra in wanted)):
                pending.append((pinned, frozenset(requirement.extras)))
    return [wheels[package] for package in PACKAGES if package in wheels]


def summary(found: dict[str, tuple[str, list[Path]]]) -> str:
    """A Markdown table for the job summary: what would go to PyPI."""
    rows = ["| Distribution | Version | File | SHA-256 |", "|---|---|---|---|"]
    for name, (version, files) in found.items():
        for file in files:
            digest = sha256(file.read_bytes()).hexdigest()
            rows.append(f"| {display.table_code(name)} | {display.table_code(version)} "
                        f"| {display.table_code(file.name)} | {display.table_code(digest)} |")
    return "\n".join(rows) + "\n"


def check(out: Path, confirm: str, repo: Path = REPO, environ: dict[str, str] | None = None) -> int:
    environ = os.environ if environ is None else environ
    problems, found = problems_in(out, confirm, repo)
    for problem in problems:
        print(f"dists: refused: {display.one_line(problem)}")
    if not problems:
        for name, (version, _) in found.items():
            print(f"dists: {display.one_line(name)} {display.one_line(version)}: ok")
    target = environ.get("GITHUB_STEP_SUMMARY")
    if target and not problems:
        with open(target, "ab") as handle:
            handle.write(("## Built files\n\n" + summary(found)).encode("utf-8"))
    return 1 if problems else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build and check the distributions. This script never uploads.")
    commands = parser.add_subparsers(dest="command", required=True)
    build_command = commands.add_parser("build")
    build_command.add_argument("out", type=Path)
    check_command = commands.add_parser("check")
    check_command.add_argument("out", type=Path)
    check_command.add_argument("--confirm", required=True)
    needs_command = commands.add_parser("needs")
    needs_command.add_argument("out", type=Path)
    needs_command.add_argument("--extras", default="")
    release_command = commands.add_parser("release")
    args = parser.parse_args(argv)
    if args.command == "build":
        build(args.out.resolve())
        return 0
    if args.command == "needs":
        extras = tuple(extra.strip() for extra in args.extras.split(",") if extra.strip())
        print("\n".join(str(wheel) for wheel in closure(args.out.resolve(), RELEASE, extras)))
        return 0
    if args.command == "release":
        print(expected_versions()[RELEASE])
        return 0
    return check(args.out.resolve(), args.confirm)


if __name__ == "__main__":
    sys.exit(main())
