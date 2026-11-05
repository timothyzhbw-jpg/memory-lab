#!/usr/bin/env python3
"""Rehearse `pip install memory-lab` on this machine, from wheels built here. Nothing is uploaded.

    python scripts/rehearse_install.py [--python PATH] [--extras ui,mcp] [--no-cache] [--keep]

In a new temporary directory it installs the locked build tools in one virtualenv, builds every sdist and wheel, runs
``twine check --strict`` and ``scripts/dists.py check``. Then it makes a second, fresh virtualenv and installs in two
steps. First this repository's wheels with ``--no-index``, chosen by reading the built metadata (``dists.py needs``),
so a copy of the same version on PyPI cannot take their place. Then ``memory-lab[extras]`` from the normal index: pip
keeps what is installed and fetches the third-party dependencies, its report proves that nothing of ours came from
the index, and ``pip check`` proves the pins hold. Last it runs each command's ``--help`` and an audit of a small file,
outside the checkout and with PYTHONPATH cleared. It prints how long each step took.

Needs network access to the package index. CI runs it (the ``packaging`` job in .github/workflows/ci.yml). Start it
with Python 3.11 or newer, because the locked tools have no macOS wheel for 3.10; ``--python`` still rehearses the
install itself on any supported version.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import urlsplit

REPO = Path(__file__).resolve().parent.parent
LOCK = REPO / "scripts" / "publish-requirements.txt"
DISTS = REPO / "scripts" / "dists.py"
OWN = ("memlab-core", "memfalsify", "orgmemory", "memlab-mcp", "memlab-ui", "memory-lab")
COMMANDS = {"ui": "memlab-ui", "mcp": "memlab-mcp"}
MEMORY = b"- The public API rate limit is 100 requests per minute.\n"
EVIDENCE = b"The public API rate limit is 60 requests per minute.\n"
FLAGGED = "POSSIBLE COUNTEREVIDENCE"


def wrongly_sourced(report: dict) -> list[str]:
    """From ``pip install --report`` for the index step: packages of ours, or anything from a local file."""
    wrong = []
    for item in report["install"]:
        name = item["metadata"]["name"].lower().replace("_", "-")
        if name in OWN or urlsplit(item["download_info"]["url"]).scheme == "file":
            wrong.append(name)
    return wrong


def clean_environment(no_cache: bool) -> dict[str, str]:
    env = {key: value for key, value in os.environ.items()
           if key not in ("PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV", "__PYVENV_LAUNCHER__")}
    if no_cache:
        env["PIP_NO_CACHE_DIR"] = "1"
    return env


class Rehearsal:
    def __init__(self, work: Path, env: dict[str, str]) -> None:
        self.work, self.env, self.started, self.times = work, env, time.perf_counter(), []

    def step(self, title: str, *command) -> str:
        began = time.perf_counter()
        print(f"rehearse: {title}", flush=True)
        result = subprocess.run([str(part) for part in command], cwd=self.work, env=self.env, check=False,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        self.times.append((title, time.perf_counter() - began))
        if result.returncode != 0:
            sys.stdout.buffer.write(result.stdout)
            raise SystemExit(f"rehearse: failed with exit code {result.returncode}: {title}")
        return result.stdout.decode("utf-8", "replace")

    def venv(self, title: str, python: str, directory: Path) -> Path:
        self.step(title, python, "-m", "venv", directory)
        return directory / ("Scripts" if os.name == "nt" else "bin")


def rehearse(work: Path, python: str, extras: list[str], no_cache: bool) -> None:
    run = Rehearsal(work, clean_environment(no_cache))
    tools = run.venv("virtualenv for the build tools", sys.executable, work / "tools")
    run.step("install the locked build tools", tools / "python", "-m", "pip", "install", "--quiet", "--require-hashes",
             "--only-binary", ":all:", "-r", LOCK)
    dist = work / "dist"
    run.step("build every sdist and wheel", tools / "python", DISTS, "build", dist)
    run.step("twine check --strict", tools / "python", "-m", "twine", "check", "--strict",
             *sorted(str(path) for path in dist.glob("*/*")))
    version = run.step("read the release version", tools / "python", DISTS, "release").strip()
    run.step("scripts/dists.py check", tools / "python", DISTS, "check", dist, f"--confirm={version}")
    wheels = run.step("read which wheels the install needs", tools / "python", DISTS, "needs", dist,
                      f"--extras={','.join(extras)}").split()

    fresh_began = time.perf_counter()
    clean = run.venv("fresh virtualenv", python, work / "clean")
    run.step("pip install this repository's wheels (--no-index)", clean / "python", "-m", "pip", "install", "--quiet",
             "--no-index", "--no-deps", *wheels)
    requirement = f"memory-lab[{','.join(extras)}]" if extras else "memory-lab"
    report_path = work / "install-report.json"
    run.step(f"pip install {requirement} (third-party dependencies from the index)", clean / "python", "-m", "pip",
             "install", "--quiet", "--report", report_path, requirement)
    report = json.loads(report_path.read_bytes())
    wrong = wrongly_sourced(report)
    if wrong:
        raise SystemExit("rehearse: installed from the wrong place: " + ", ".join(wrong))
    run.step("pip check", clean / "python", "-m", "pip", "check")
    print(f"rehearse: {len(wheels)} wheels from this repository, {len(report['install'])} third-party packages from "
          "the index")

    for command in ["memfalsify", "orgmemory"] + [COMMANDS[extra] for extra in extras if extra in COMMANDS]:
        run.step(f"{command} --help", clean / command, "--help")
    audit = work / "audit"
    (audit / "evidence").mkdir(parents=True)
    (audit / "MEMORY.md").write_bytes(MEMORY)
    (audit / "evidence" / "api.md").write_bytes(EVIDENCE)
    output = run.step("memfalsify audit on a small file", clean / "memfalsify", "audit", audit / "MEMORY.md",
                      "--evidence", audit / "evidence")
    if FLAGGED not in output:
        raise SystemExit("rehearse: the audit did not flag the contradiction in the small file")
    first_report = time.perf_counter() - fresh_began
    if "ui" in extras:
        run.step("the web page's images are installed", clean / "python", "-c",
                 "import importlib.resources as r; assert r.files('memlab_ui').joinpath('static/ui-light.png').is_file()")

    print("rehearse: timings")
    for title, seconds in run.times:
        print(f"rehearse:   {seconds:7.1f} s  {title}")
    print(f"rehearse: {time.perf_counter() - run.started:.1f} s in all; {first_report:.1f} s from the fresh virtualenv "
          "to the first report")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Rehearse installing memory-lab from wheels built here.")
    parser.add_argument("--python", default=sys.executable, help="interpreter for the fresh virtualenv")
    parser.add_argument("--extras", default="ui", help="comma-separated extras of memory-lab (default: ui)")
    parser.add_argument("--no-cache", action="store_true", help="pip downloads everything, as on a new machine")
    parser.add_argument("--keep", action="store_true", help="keep the temporary directory and print its path")
    args = parser.parse_args(argv)
    extras = [extra.strip() for extra in args.extras.split(",") if extra.strip()]
    if args.keep:
        work = Path(tempfile.mkdtemp(prefix="memlab-rehearsal-")).resolve()
        print(f"rehearse: working in {work}")
        rehearse(work, args.python, extras, args.no_cache)
    else:
        with tempfile.TemporaryDirectory(prefix="memlab-rehearsal-") as scratch:
            rehearse(Path(scratch).resolve(), args.python, extras, args.no_cache)
    return 0


if __name__ == "__main__":
    sys.exit(main())
