"""Skill files stay aligned with the CLIs they teach.

Frontmatter must stay valid for Claude Code and Codex (name, description).
Every memfalsify, orgmemory and memlab-mcp command path and ``--flag`` in a
skill must appear in that CLI's ``--help`` tree, so the skills cannot drift.
"""
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SKILLS = ROOT / "skills"
CLI_NAMES = ("memfalsify", "orgmemory", "memlab-mcp")
MODULES = {"memfalsify": "memfalsify", "orgmemory": "orgmemory", "memlab-mcp": "memlab_mcp"}
SRC = [
    ROOT / "packages" / "memcore" / "src",
    ROOT / "packages" / "memfalsify" / "src",
    ROOT / "packages" / "orgmemory" / "src",
    ROOT / "packages" / "memlab-mcp" / "src",
]
# A CLI token, not a path segment such as benchmarks/memfalsify/run.py.
CLI_TOKEN = re.compile(r"(?<![/\w])(memfalsify|orgmemory|memlab-mcp)\b")
INVOCATION = re.compile(
    r"(?<![/\w])(memfalsify|orgmemory|memlab-mcp)\b((?:\s+[a-z][a-z0-9-]*)*)")
FLAG = re.compile(r"--[a-zA-Z0-9][a-zA-Z0-9-]*")
NAME = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
FRONTMATTER = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n", re.DOTALL)


def parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """Single-line ``name`` and ``description`` fields, the shape both agents accept."""
    match = FRONTMATTER.match(text)
    if match is None:
        raise ValueError("SKILL.md must start with --- name/description --- frontmatter")
    fields: dict[str, str] = {}
    for line in match.group(1).splitlines():
        if not line.strip():
            continue
        if line.startswith((" ", "\t")) or ":" not in line:
            raise ValueError(f"frontmatter line is not a single-line field: {line!r}")
        key, value = line.split(":", 1)
        key, value = key.strip(), value.strip()
        if not key or not value:
            raise ValueError(f"frontmatter field is empty: {line!r}")
        fields[key] = value
    for required in ("name", "description"):
        if required not in fields:
            raise ValueError(f"frontmatter is missing {required}")
    if NAME.fullmatch(fields["name"]) is None:
        raise ValueError(f"skill name {fields['name']!r} is not a lowercase hyphenated token")
    if len(fields["description"]) > 1024:
        raise ValueError("description is longer than 1024 characters")
    return fields, text[match.end():]


def blocks(body: str) -> list[tuple[str, bool]]:
    """Prose paragraphs and fenced blocks. A fence is all code; prose is not."""
    parts: list[tuple[str, bool]] = []
    fence: list[str] | None = None
    buf: list[str] = []

    def flush() -> None:
        if buf:
            parts.append(("\n".join(buf), False))
            buf.clear()

    for line in body.splitlines():
        if fence is not None:
            if line.startswith("```"):
                parts.append(("\n".join(fence), True))
                fence = None
            else:
                fence.append(line)
            continue
        if line.startswith("```"):
            flush()
            fence = []
            continue
        if not line.strip():
            flush()
            continue
        buf.append(line)
    flush()
    if fence is not None:
        parts.append(("\n".join(fence), True))
    return parts


def code_pieces(text: str, fenced: bool) -> list[str]:
    if fenced:
        return [text]
    return re.findall(r"`([^`]*)`", text)


def invocations(text: str) -> list[tuple[str, tuple[str, ...]]]:
    found = []
    for match in INVOCATION.finditer(text):
        words = tuple(match.group(2).split())
        found.append((match.group(1), words))
    return found


def command_names(help_text: str) -> list[str]:
    """Names from a Typer Commands panel. Continuation lines are indented past the bar."""
    names: list[str] = []
    collecting = False
    for line in help_text.splitlines():
        if not collecting:
            heading = line.strip().rstrip(":")
            if heading == "Commands" or ("Commands" in line and any(mark in line for mark in "╭+")):
                collecting = True
            continue
        stripped = line.strip()
        if names and (stripped.startswith(("╰", "╭", "+")) or set(stripped) <= set("+-")):
            break
        match = re.search(r"[│|]\s{0,2}([a-z][a-z0-9-]*)\s{2,}\S", line)
        if match is None and "│" not in line and "|" not in line:
            match = re.match(r"^\s{0,2}([a-z][a-z0-9-]*)\s{2,}\S", line)
        if match:
            names.append(match.group(1))
    return names


def run_help(module: str, args: tuple[str, ...]) -> str:
    env = os.environ.copy()
    env["TERMINAL_WIDTH"] = "220"
    env["_TYPER_FORCE_DISABLE_TERMINAL"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    paths = [str(path) for path in SRC]
    if env.get("PYTHONPATH"):
        paths.append(env["PYTHONPATH"])
    env["PYTHONPATH"] = os.pathsep.join(paths)
    completed = subprocess.run(
        [sys.executable, "-m", module, *args, "--help"],
        check=False, capture_output=True, cwd=ROOT, env=env, encoding="utf-8")
    if completed.returncode != 0:
        raise AssertionError(
            f"{module} {' '.join(args)} --help exited {completed.returncode}\n{completed.stderr}")
    return completed.stdout


class CliHelp:
    def __init__(self, commands: set[tuple[str, ...]], flags: set[str]):
        self.commands = commands
        self.flags = flags


def build_index() -> dict[str, CliHelp]:
    index = {}
    for name in CLI_NAMES:
        module = MODULES[name]
        commands: set[tuple[str, ...]] = {()}
        flags: set[str] = set()
        queue = [()]
        while queue:
            path = queue.pop()
            text = run_help(module, path)
            flags.update(FLAG.findall(text))
            for command in command_names(text):
                child = path + (command,)
                if child not in commands:
                    commands.add(child)
                    queue.append(child)
        index[name] = CliHelp(commands, flags)
    return index


@pytest.fixture(scope="module")
def index() -> dict[str, CliHelp]:
    return build_index()


def scoped_flags(text: str, fenced: bool, pieces: list[str]) -> list[str]:
    """Flags of these CLIs. A path such as ``benchmarks/memfalsify/run.py --check`` is not one."""
    found: list[str] = []
    for piece in pieces:
        if not invocations(piece) and "/" in piece:
            continue
        found.extend(FLAG.findall(piece))
    if not fenced:
        prose = re.sub(r"`[^`]*`", " ", text)
        found.extend(FLAG.findall(prose))
    return list(dict.fromkeys(found))


def drift(body: str, index: dict[str, CliHelp]) -> list[str]:
    """Command paths and flags in ``body`` that the live CLI help does not list."""
    problems = []
    previous: set[str] = set()
    for text, fenced in blocks(body):
        pieces = code_pieces(text, fenced)
        calls = [call for piece in pieces for call in invocations(piece)]
        for cli, path in calls:
            if path not in index[cli].commands:
                shown = " ".join((cli, *path))
                problems.append(f"unknown command `{shown}`")
        scope = {cli for cli, _ in calls} or previous
        for flag in scoped_flags(text, fenced, pieces):
            if not scope:
                problems.append(f"{flag} is not tied to memfalsify, orgmemory or memlab-mcp")
            elif not any(flag in index[cli].flags for cli in scope):
                owners = ", ".join(sorted(scope))
                problems.append(f"{flag} is not in {owners} --help")
        if calls:
            previous = {cli for cli, _ in calls}
    return problems


def test_cli_help_index_sees_shipped_commands(index):
    """The help walker itself still sees the commands these skills are about."""
    assert ("audit",) in index["memfalsify"].commands
    assert ("claims",) in index["memfalsify"].commands
    assert "--exclude" in index["memfalsify"].flags
    assert "--no-follow-links" in index["memfalsify"].flags
    assert ("evidence", "add") in index["orgmemory"].commands
    assert ("github", "sync") in index["orgmemory"].commands
    assert ("github", "convert") in index["orgmemory"].commands
    assert "--save-capture" in index["orgmemory"].flags
    assert "--dry-run" in index["orgmemory"].flags
    assert index["memlab-mcp"].commands == {()}
    assert "--root" in index["memlab-mcp"].flags
    assert "--allow-model-verifiers" in index["memlab-mcp"].flags


def test_drift_checker_catches_unknown_commands_and_flags(index):
    bad = drift("`memfalsify diff --history`\n", index)
    assert any("diff" in item for item in bad)
    assert any("--history" in item for item in bad)
    assert drift("`memfalsify audit --exclude 'vendor/**'`\n", index) == []
    assert drift("`benchmarks/memfalsify/run.py --check`\n", index) == []


def test_skill_frontmatter_and_mentions_match_cli_help(index):
    skills = sorted(SKILLS.glob("*/SKILL.md"))
    assert [path.parent.name for path in skills] == ["memfalsify", "orgmemory"]
    for path in skills:
        text = path.read_text(encoding="utf-8")
        meta, body = parse_frontmatter(text)
        assert meta["name"] == path.parent.name
        problems = drift(body, index)
        assert not problems, path.name + ":\n" + "\n".join(problems)


def test_frontmatter_rejects_a_missing_description():
    with pytest.raises(ValueError, match="description"):
        parse_frontmatter("---\nname: memfalsify\n---\n\nBody.\n")
