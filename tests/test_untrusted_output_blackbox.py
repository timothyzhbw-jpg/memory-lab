"""Black-box checks that hostile text in untrusted fields cannot act on a terminal, a CI log or a Markdown reader.

The tests drive the real command line (memfalsify, its pre-commit hook and orgmemory through Typer's CliRunner)
and the GitHub Action's ``main`` with a fake runner environment. A payload is planted in one untrusted field at
a time (a memory sentence, a heading, a reviewer, an evidence quote or file name, an event actor, a subject, a
note, a source URI, imported GitHub or Jira text, ...) and every human-facing output is checked for properties
that do not depend on how the tool chooses to escape:

* no control character except LF (C0, DEL, C1), and no line or paragraph separator, bidi control or
  zero-width character;
* no line that a workflow runner would read as a command (``::...``, ``##[...``), except the annotations
  the Action itself emits, which keep the payload on their one line;
* no heading, code fence or YAML line created by the payload;
* in orgmemory's YAML state blocks, every value is a valid scalar, and text from the payload that is more than
  plain words sits only in a quoted scalar.

Exact wording is never asserted: a tool may drop, escape or replace a character as it likes. HTML and JSON
output are out of scope. Each test id is ``<field>-<payload>`` (``<group>-<field>-<payload>`` where a function
covers several commands); the function name names the surface. The payload ``plain`` is a control: ordinary
words in the same place must pass every check, and succeed unless the field is an error path.
"""
from __future__ import annotations

import csv
import importlib.util
import io
import json
import os
import re
import shlex
import shutil
import unicodedata
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from conftest import write_text_exact
from memfalsify.cli import app as memfalsify_app
from memfalsify.precommit import app as precommit_app
from orgmemory.cli import app as orgmemory_app

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures"
_spec = importlib.util.spec_from_file_location("memfalsify_action_blackbox", ROOT / "action" / "memfalsify_action.py")
action = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(action)

runner = CliRunner()
requires_git = pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")

# --- payloads ------------------------------------------------------------------------------------------------

MARK = "Zq9mark"  # in every line a payload creates; plain letters, so no escaping rewrites it
PAYLOADS = {
    # Control: ordinary words. Every check must pass on it.
    "plain": "ordinary words " + MARK,
    # Terminal: clear screen, an OSC 8 hyperlink with a bell terminator, a C1 control sequence introducer.
    "esc": ("start " + MARK + " \x1b[2J\x1b]8;;http://evil.example/" + MARK + "\x07click\x1b]8;;\x07 \x9b31m end"),
    # A carriage return that overwrites the line, then CRLF and a heading.
    "cr": "start " + MARK + " \roverwritten " + MARK + "\r\n## Injected heading " + MARK,
    # LF followed by forged workflow commands (GitHub annotations) and a heading.
    "lf_commands": ("start " + MARK + "\n::error title=X::forged " + MARK + "\n::warning::x " + MARK
                    + "\n::stop-commands::token " + MARK + "\n## Injected heading " + MARK),
    # The older workflow command syntax.
    "legacy_command": "start " + MARK + "\n##[error]forged " + MARK,
    # U+2028, U+2029 and NEL are line breaks to many readers and editors.
    "separators": ("start " + MARK + "\u2028## Injected heading " + MARK + "\u2029::error title=X::forged " + MARK
                   + "\x85::warning::x " + MARK),
    # Right-to-left override, left-to-right isolate and RLM, with their closers.
    "bidi": "start " + MARK + " \u202eevil\u202c \u2066nested\u2069 \u200f" + MARK + " end",
    # Zero-width space, non-joiner, joiner, word joiner and byte-order mark.
    "zero_width": "st\u200bart " + MARK + " \u200c\u200d\u2060\ufeff end",
    # A line that closes a fenced code block, then a heading.
    "fence": "start " + MARK + "\n```\n## Injected heading " + MARK + "\n```",
    # Things that end a YAML plain scalar, start a list item, or start a comment.
    "yaml": "x: " + MARK + "\n- injected: true # " + MARK + "\n# Injected heading " + MARK,
}
# Single lines that stay dangerous after an importer keeps only the first line of a comment as a quote.
LINE_PAYLOADS = {
    "line_command": "::error title=X::forged " + MARK,
    "line_legacy_command": "##[error]forged " + MARK,
    "line_heading": "## Injected heading " + MARK,
    "line_fence": "```",
    "line_yaml": "x: " + MARK + " # - injected: true",
}
IMPORT_PAYLOADS = {**PAYLOADS, **LINE_PAYLOADS}


def cases(fields, payloads=PAYLOADS):
    """One test id per (field, payload): ``<field>-<payload>``."""
    return [pytest.param(field, name, id=f"{field}-{name}") for field in fields for name in payloads]


def grouped_cases(groups, payloads=PAYLOADS):
    """One test id per (group, field, payload): ``<group>-<field>-<payload>``; ``groups`` maps group to fields."""
    return [pytest.param(group, field, name, id=f"{group}-{field}-{name}")
            for group, fields in groups.items() for field in fields for name in payloads]


def safe_name(text: str) -> str:
    """A payload as one file name component."""
    return text.replace("/", "_").replace("\\", "_").replace("\x00", "_")


def exits(payload: str, field: str, errors=()) -> tuple:
    """Exit codes a run may end with: the control payload must succeed, unless the field is an error path."""
    return (0, 1, 2) if payload != "plain" or field in errors else (0, 1)


# --- the properties --------------------------------------------------------------------------------------------

UNSAFE_CATEGORIES = {"Cc", "Cf", "Zl", "Zp"}  # control, format (bidi, zero-width), line and paragraph separators
INJECTED_HEADING = re.compile(r"^\s*#{1,6}[ \t]*Injected heading")
# A code fence: three or more backticks (with no backtick in the rest of the line, or it is a code span) or tildes.
FENCE = re.compile(r"^\s*(?:`{3,}[^`]*|~{3,}.*)$")
ANNOTATION = re.compile(r"^::(?:error|warning|notice) title=MemFalsify::[^\n]*$")
ACTION_LINE = re.compile(r"^(?:MemFalsify: |MemFalsify CLI: )")
STATE_KEYS = ("subject", "current_status", "reconsideration_requested", "pending_approval", "dependencies",
              "open_risks", "last_authorized_decision", "unauthorized_change_attempts")
PLAIN_START = set("[]{},#&*!|>'\"%@`")
PLAIN_WORDS = re.compile(r"[\w .@()/\-\ufffd]*")  # payload text that is harmless as an unquoted scalar


def snippet(line: str) -> str:
    return repr(line if len(line) <= 90 else line[:87] + "...")


def hidden_characters(text: str) -> list[str]:
    return sorted(f"U+{ord(c):04X}" for c in set(text) if c != "\n" and unicodedata.category(c) in UNSAFE_CATEGORIES)


def plain_scalar_problem(text: str, in_list: bool = False) -> str | None:
    """Why ``text`` is not a safe plain (unquoted) YAML scalar, or None. ``in_list``: inside a ``[a, b]`` list."""
    if MARK in text and not PLAIN_WORDS.fullmatch(text):
        return "text from the payload is not in a quoted scalar"
    if not text or text != text.strip():
        return "empty or space-padded plain scalar"
    if text[0] in PLAIN_START or text[:2] in ("- ", "? ", ": ") or text == "-":
        return "plain scalar starts with a YAML indicator"
    if ": " in text or " #" in text or text.endswith(":"):
        return "plain scalar contains a YAML key or comment indicator"
    if in_list and any(c in text for c in "[{}"):
        return "plain scalar contains a flow indicator"
    if any(unicodedata.category(c) in UNSAFE_CATEGORIES for c in text):
        return "plain scalar holds a control character"
    return None


def quoted_end(value: str, pos: int) -> int | None:
    """Index just past the quoted scalar that starts at ``pos`` (double quotes with backslash escapes, or single
    quotes with ``''`` for a quote), or None when it is not closed on this line."""
    quote, pos = value[pos], pos + 1
    while pos < len(value):
        if quote == '"' and value[pos] == "\\":
            pos += 2
        elif value[pos] == quote:
            if quote == "'" and value.startswith("''", pos):
                pos += 2
                continue
            return pos + 1
        else:
            pos += 1
    return None


def yaml_value_problems(value: str) -> list[str]:
    """Check the right-hand side of one state-block line: a plain scalar, a quoted one (either style), or a flow
    list of those. Whether the escapes inside a quoted scalar are valid is left to PyYAML, when it is installed."""
    problems: list[str] = []

    def scalar(pos: int, in_list: bool) -> int:
        if value.startswith(("'", '"'), pos):
            end = quoted_end(value, pos)
            if end is None:
                problems.append(f"a quoted scalar is never closed: {snippet(value[pos:])}")
                return len(value)
            return end
        stop = pos
        while stop < len(value) and not (in_list and value[stop] in ",]"):
            stop += 1
        problem = plain_scalar_problem(value[pos:stop], in_list)
        if problem:
            problems.append(f"{problem}: {snippet(value[pos:stop])}")
        return stop

    if value == "[]":
        return problems
    if value.startswith("["):
        pos = 1
        while True:
            pos = scalar(pos, True)
            if problems:
                break
            if value.startswith("]", pos) and pos + 1 == len(value):
                break
            if value.startswith(", ", pos):
                pos += 2
                continue
            problems.append(f"list not closed after its last item: {snippet(value[pos:])}")
            break
        return problems
    end = scalar(0, False)
    if not problems and end != len(value):
        problems.append(f"text after the scalar: {snippet(value[end:])}")
    return problems


def state_block_problems(lines: list[str]) -> list[str]:
    """The ```yaml state block of one subject: eight known keys in order, each with a safe value."""
    problems: list[str] = []
    if len(lines) != len(STATE_KEYS):
        problems.append(f"state block has {len(lines)} lines, not {len(STATE_KEYS)}: {snippet(' | '.join(lines))}")
    for expected, line in zip(STATE_KEYS, lines):
        key, sep, value = line.partition(": ")
        if key != expected or not sep:
            problems.append(f"state block line should be '{expected}: ...': {snippet(line)}")
            continue
        problems += [f"{key}: {p}" for p in yaml_value_problems(value)]
    try:
        import yaml  # optional: when PyYAML is installed, a real parser has the last word
    except ImportError:
        return problems
    try:
        loaded = yaml.safe_load("\n".join(lines))
    except yaml.YAMLError as exc:
        return problems + [f"PyYAML cannot parse the state block: {str(exc).splitlines()[0]}"]
    if not isinstance(loaded, dict) or list(loaded) != list(STATE_KEYS):
        problems.append("PyYAML reads other keys than the state block's: "
                        + (str(list(loaded)) if isinstance(loaded, dict) else repr(loaded)))
    return problems


def yaml_block_problems(lines: list[str]) -> list[str]:
    """Fences must pair up as ```yaml ... ``` around well-formed state blocks, and appear nowhere else."""
    problems, block = [], None
    for number, line in enumerate(lines, 1):
        if block is None:
            if line == "```yaml":
                block = []
            elif FENCE.match(line):
                problems.append(f"line {number}: code fence outside a state block: {snippet(line)}")
        elif line == "```":
            problems += [f"line {number}: {p}" for p in state_block_problems(block)]
            block = None
        else:
            if FENCE.match(line):
                problems.append(f"line {number}: code fence inside a state block: {snippet(line)}")
            block.append(line)
    if block is not None:
        problems.append("a ```yaml block is never closed")
    return problems


def text_problems(text: str, *, yaml_blocks: bool = False, payload_free_headings: bool = False,
                  action_stdout: bool = False) -> list[str]:
    """Everything wrong with one human-facing output (empty when it is inert)."""
    problems = []
    if hidden := hidden_characters(text):
        problems.append("raw control, bidi, zero-width or separator characters: " + ", ".join(hidden))
    lines = text.split("\n")
    for number, line in enumerate(lines, 1):
        stripped = line.lstrip()
        if stripped.startswith("::") and not (action_stdout and ANNOTATION.match(line)):
            problems.append(f"line {number} reads as a workflow command: {snippet(line)}")
        if stripped.startswith("##["):
            problems.append(f"line {number} reads as a legacy workflow command: {snippet(line)}")
        if INJECTED_HEADING.match(line):
            problems.append(f"line {number} is a heading the payload created: {snippet(line)}")
        if payload_free_headings and MARK in line and stripped.startswith("#"):
            problems.append(f"line {number} is a heading made of payload text: {snippet(line)}")
        if action_stdout and line and not (ACTION_LINE.match(line) or ANNOTATION.match(line)):
            problems.append(f"line {number} is not a line the Action prints: {snippet(line)}")
    if yaml_blocks:
        problems += yaml_block_problems(lines)
    else:
        problems += [f"line {n} is a code fence: {snippet(line)}" for n, line in enumerate(lines, 1)
                     if FENCE.match(line)]
    return problems


def csv_problems(text: str) -> list[str]:
    """``orgmemory export --format csv``: one header row and one row per decided subject, each on one line."""
    problems = text_problems(text)
    rows = list(csv.reader(io.StringIO(text)))
    if text.count("\n") != len(rows):
        problems.append(f"{len(rows)} CSV rows span {text.count(chr(10))} lines: a cell holds a line break")
    return problems


def assert_no_problems(problems: list[str]) -> None:
    assert not problems, "\n".join(problems[:12]) + (f"\n... {len(problems) - 12} more" if len(problems) > 12 else "")


def assert_inert(outputs: dict[str, str], *, csv: bool = False, **options) -> None:
    """Every output passes; ``options`` are for ``text_problems``."""
    problems = []
    for label, text in outputs.items():
        found = csv_problems(text) if csv else text_problems(text, **options)
        problems += [f"[{label}] {p}" for p in found]
    assert_no_problems(problems)


def invoke(app, args):
    return runner.invoke(app, [os.fspath(a) if isinstance(a, Path) else str(a) for a in args])


def assert_exit(result, allowed) -> None:
    crash = result.exception if not isinstance(result.exception, SystemExit) else None
    assert crash is None, f"unhandled {type(crash).__name__}: {str(crash)[:200]!r}"
    assert result.exit_code in allowed, f"exit {result.exit_code}: {result.output[:300]!r}"


def make_dirs(path: Path) -> None:
    try:
        path.mkdir(parents=True, exist_ok=True)
    except (OSError, ValueError) as exc:
        pytest.skip(f"this file system cannot create the directory {path.name!r}: {exc}")


def write(path: Path, data) -> None:
    make_dirs(path.parent)
    try:
        path.write_bytes(data if isinstance(data, bytes) else data.encode("utf-8"))
    except (OSError, ValueError) as exc:
        pytest.skip(f"this file system cannot create the file {path.name!r}: {exc}")


def jsonl(*records) -> str:
    return "".join(json.dumps(record) + "\n" for record in records)


# --- MemFalsify ---------------------------------------------------------------------------------------------------

CLAIM = "The public API rate limit is 100 requests per minute"
OTHER_CLAIM = "The main database runs PostgreSQL 15."
EVIDENCE_LINE = "The public API rate limit is 60 requests per minute"
OTHER_EVIDENCE = "The main database runs PostgreSQL 16.\n"

MF_CONTENT_FIELDS = ["claim", "section", "frontmatter", "memory_name", "evidence_text", "evidence_name",
                     "candidate_name", "skipped_name", "transcript_cue", "media_name", "ocr_line"]
MF_REVIEW_FIELDS = ["reviewer", "note", "review_quote"]
MF_ERROR_FIELDS = ["review_memory_id", "review_path", "review_relation", "review_key", "review_locator",
                   "review_malformed", "as_of_option", "evidence_option", "exclude_option", "memory_missing",
                   "reviews_missing"]
MF_ERRORS = {"review_memory_id", "review_path", "review_relation", "review_key", "review_locator",
             "review_malformed", "as_of_option", "evidence_option", "memory_missing", "reviews_missing"}


def build_workspace(root: Path, field: str, payload: str, *, reviews: bool = True) -> SimpleNamespace:
    """MEMORY.md, evidence/ and reviews.jsonl in ``root``; ``payload`` goes into ``field``."""
    make_dirs(root)

    def pick(name, ordinary):
        return payload if name == field else ordinary

    def tail(*names):
        return f", {payload}" if field in names else ""

    body = f"## Product {pick('section', 'decisions')}\n\n- {CLAIM}{tail('claim')}.\n- {OTHER_CLAIM}\n"
    if field == "frontmatter":
        memory = root / ".cursor" / "rules" / "api.mdc"
        write(memory, f"---\ndescription: {payload}\nglobs: src/**\n---\n\n{body}")
    else:
        memory = root / (f"MEMORY-{safe_name(payload)}.md" if field == "memory_name" else "MEMORY.md")
        write(memory, f"# Project memory\n\n{body}")
    evidence = root / "evidence"
    make_dirs(evidence)
    api = f"api-{safe_name(payload)}.md" if field == "evidence_name" else "api.md"
    line = f"{EVIDENCE_LINE}{tail('review_quote')}"
    write(evidence / api, line + ".\n")
    limits = f"limits-{safe_name(payload)}.md" if field == "candidate_name" else "limits.md"
    write(evidence / limits, f"{EVIDENCE_LINE}{tail('evidence_text')}.\n")
    write(evidence / "db.md", OTHER_EVIDENCE)
    if field == "skipped_name":
        write(evidence / f"clip-{safe_name(payload)}.mp4", b"\x00\x01\x02")
    if field in ("transcript_cue", "media_name"):  # a recording with a WebVTT transcript bound to its hash
        stem, recording = (f"standup-{safe_name(payload)}" if field == "media_name" else "standup"), b"recording"
        write(evidence / f"{stem}.mp4", recording)
        write(evidence / f"{stem}.mp4.vtt",
              f"WEBVTT\n\nNOTE source_sha256: {sha256(recording).hexdigest()}\n\n"
              f"00:00:01.000 --> 00:00:05.000\n{EVIDENCE_LINE}{tail('transcript_cue')}.\n")
    if field == "ocr_line":  # a whiteboard photo with an OCR sidecar bound to its hash
        write(evidence / "board.png", b"photo")
        write(evidence / "board.png.ocr.json", json.dumps({
            "format": "memlab-ocr/1", "source_sha256": sha256(b"photo").hexdigest(),
            "pages": [{"page": 1, "width": 100, "height": 100,
                       "lines": [{"text": f"{EVIDENCE_LINE}, {payload}", "bbox": [1, 2, 30, 40]}]}]}))
    ws = SimpleNamespace(root=root, memory=memory, evidence=evidence, reviews=None)
    if not reviews:
        return ws
    listed = invoke(memfalsify_app, ["claims", memory])
    claims = [json.loads(row) for row in listed.output.splitlines() if row.startswith("{")]
    memory_id = next((c["memory_id"] for c in claims if c["text"].startswith("The public API")), "m-00000000")
    record = {"memory_id": memory_id, "relation": "contradicts", "path": api,
              "quote": line.split("\n")[0] if field == "review_quote" else EVIDENCE_LINE,
              "reviewer": pick("reviewer", "demo-reviewer"), "note": pick("note", "The limit changed.")}
    if field == "review_memory_id":
        record["memory_id"] = payload
    elif field == "review_path":
        record["path"] = payload
    elif field == "review_relation":
        record["relation"] = payload
    elif field == "review_key":
        record[payload] = "x"
    elif field == "review_locator":
        record["locator"] = payload
    text = jsonl(record)
    if field == "review_malformed":
        text += payload + "\n"
    ws.reviews = root / "reviews.jsonl"
    write(ws.reviews, text)
    return ws


def audit_command(ws: SimpleNamespace, field: str, payload: str) -> list:
    args = ["audit", ws.memory, "--evidence", ws.evidence, "--as-of", "2026-10-10"]
    if ws.reviews:
        args += ["--reviews", ws.reviews]
    if field == "as_of_option":
        args[args.index("--as-of") + 1] = payload
    elif field == "evidence_option":
        args[args.index("--evidence") + 1] = ws.root / f"missing-{payload}"
    elif field == "exclude_option":
        args += ["--exclude", payload]
    elif field == "memory_missing":
        args[1] = ws.root / f"missing-{payload}.md"
    elif field == "reviews_missing":
        args[args.index("--reviews") + 1] = ws.root / f"missing-{payload}.jsonl"
    return args


@pytest.mark.parametrize("field, payload", cases(MF_CONTENT_FIELDS + MF_REVIEW_FIELDS + MF_ERROR_FIELDS))
def test_memfalsify_audit_report_is_inert(tmp_path, field, payload):
    ws = build_workspace(tmp_path / "ws", field, PAYLOADS[payload])
    result = invoke(memfalsify_app, audit_command(ws, field, PAYLOADS[payload]))
    assert_exit(result, exits(payload, field, MF_ERRORS))
    assert_inert({"output": result.output}, payload_free_headings=True)


@pytest.mark.parametrize("field, payload", cases(["claim", "section", "frontmatter", "memory_name", "memory_missing"]))
def test_memfalsify_claims_output_is_inert(tmp_path, field, payload):
    ws = build_workspace(tmp_path / "ws", field, PAYLOADS[payload], reviews=False)
    memory = ws.root / f"missing-{PAYLOADS[payload]}.md" if field == "memory_missing" else ws.memory
    result = invoke(memfalsify_app, ["claims", memory])
    assert_exit(result, exits(payload, field, {"memory_missing"}))
    assert_inert({"output": result.output}, payload_free_headings=True)
    if result.exit_code == 0:
        rows = [row for row in result.output.split("\n") if row]
        assert all(row.startswith("{") and isinstance(json.loads(row), dict) for row in rows), rows[:3]


@pytest.mark.parametrize("field, payload", cases(["rule_name", "dir_name", "repo_name", "exclude_option"]))
def test_memfalsify_discover_output_is_inert(tmp_path, field, payload):
    root, hostile = tmp_path / "repo", PAYLOADS[payload]
    write(root / "AGENTS.md", "- The cache uses Redis.\n")
    rule = safe_name(hostile) if field == "rule_name" else "api"
    write(root / ".cursor" / "rules" / f"{rule}.mdc", "---\nglobs: src/**\n---\n\n- The cache uses Redis.\n")
    write(root / (safe_name(hostile) if field == "dir_name" else "web") / "CLAUDE.md", "- The cache uses Redis.\n")
    nested = safe_name(hostile) if field == "repo_name" else "vendor"
    make_dirs(root / nested / ".git")
    write(root / nested / "AGENTS.md", "- The cache uses Redis.\n")
    result = invoke(memfalsify_app, ["discover", root] + (["--exclude", hostile] if field == "exclude_option" else []))
    assert_exit(result, exits(payload, field))
    assert_inert({"output": result.output}, payload_free_headings=True)


# diff, history, rollback and the pre-commit hooks read git, so they run against a throwaway repository (the
# git_repo fixture is in conftest.py).

GIT_CONTENT_FIELDS = ["added", "changed", "removed", "section", "evidence_text", "evidence_name"]


def build_repo(repo, field: str, payload: str, *, stage: bool = False) -> SimpleNamespace:
    """A base commit and an uncommitted head version of MEMORY.md; ``payload`` goes into ``field``."""
    def tail(name):
        return f", {payload}" if field == name else ""

    name = f"MEMORY-{safe_name(payload)}.md" if field == "memory_name" else "MEMORY.md"
    section = f" {payload}" if field == "section" else ""
    changed = tail("changed")
    base = ("# Memory\n\n## Infra\n\n"
            f"- The cache uses Redis for sessions{tail('removed')}.\n"
            f"- The main database runs PostgreSQL 15 today{changed}.\n"
            "- Deployments go through the staging cluster.\n")
    head = (f"# Memory\n\n## Infra{section}\n\n"
            f"- The main database runs PostgreSQL 16 today{changed}.\n"
            "- Deployments go through the staging cluster.\n"
            f"- {CLAIM}{tail('added')}.\n")
    api = f"api-{safe_name(payload)}.md" if field == "evidence_name" else "api.md"
    files = {f"evidence/{api}": f"{EVIDENCE_LINE}{tail('evidence_text')}.\n", "evidence/db.md": OTHER_EVIDENCE,
             name: base}
    try:
        sha = repo.commit("base", files)
    except (AssertionError, OSError, ValueError) as exc:
        pytest.skip(f"git cannot store this file name: {str(exc)[:80]!r}")
    repo.write({name: head})
    if stage:
        repo.git("add", "-A")
    return SimpleNamespace(memory=repo.root / name, name=name, base=sha, root=repo.root)


@requires_git
@pytest.mark.parametrize("field, payload", cases(GIT_CONTENT_FIELDS + ["memory_name", "base_ref", "base_option",
                                                                      "head_option"]))
def test_memfalsify_diff_report_is_inert(git_repo, monkeypatch, field, payload):
    hostile = PAYLOADS[payload]
    ws = build_repo(git_repo, field, hostile)
    monkeypatch.chdir(ws.root)
    args = ["diff", ws.memory, "--base", ws.base, "--evidence", ws.root / "evidence", "--as-of", "2026-10-10"]
    if field == "base_ref":
        args[3] = "".join(c for c in hostile if c.isalnum() or ord(c) > 0x7F) or "ref"
        try:  # a ref name may hold C1, bidi and zero-width characters, which are what is tested here
            git_repo.git("tag", args[3], ws.base)
        except AssertionError:
            pytest.skip("git refuses this ref name")
    elif field == "base_option":
        args[3] = hostile
    elif field == "head_option":
        args += ["--head", hostile]
    result = invoke(memfalsify_app, args)
    assert_exit(result, exits(payload, field, {"memory_name", "base_option", "head_option"}))
    assert_inert({"output": result.output}, payload_free_headings=True)


@requires_git
@pytest.mark.parametrize("field, payload", cases(["claim", "section", "memory_name", "commit_subject", "author"]))
def test_memfalsify_history_report_is_inert(git_repo, monkeypatch, field, payload):
    hostile = PAYLOADS[payload]
    name = f"MEMORY-{safe_name(hostile)}.md" if field == "memory_name" else "MEMORY.md"
    section = f" {hostile}" if field == "section" else ""
    tail = f", {hostile}" if field == "claim" else ""
    monkeypatch.chdir(git_repo.root)
    try:
        if field == "author":
            git_repo.git("config", "--local", "user.name", hostile)
        git_repo.commit(hostile if field == "commit_subject" else "first",
                        {name: f"# Memory\n\n## Infra\n\n- The cache uses Redis for sessions{tail}.\n"
                               "- The main database runs PostgreSQL 15 today.\n"})
        git_repo.commit(hostile if field == "commit_subject" else "second",
                        {name: f"# Memory\n\n## Infra{section}\n\n- The cache uses Redis for sessions{tail}.\n"
                               f"- The main database runs PostgreSQL 16 today{tail}.\n"})
    except (AssertionError, OSError, ValueError) as exc:
        pytest.skip(f"git cannot take this value: {str(exc)[:80]!r}")
    result = invoke(memfalsify_app, ["history", git_repo.root / name])
    assert_exit(result, exits(payload, field, {"memory_name"}))
    assert_inert({"output": result.output}, payload_free_headings=True)


@requires_git
@pytest.mark.parametrize("field, payload", cases(["claim_option", "to_option"]))
def test_memfalsify_rollback_messages_are_inert(git_repo, monkeypatch, field, payload):
    ws = build_repo(git_repo, "", "")
    monkeypatch.chdir(ws.root)
    claim = PAYLOADS[payload] if field == "claim_option" else "m-1a2b3c4d"
    target = PAYLOADS[payload] if field == "to_option" else ws.base
    result = invoke(memfalsify_app, ["rollback", ws.memory, "--claim", claim, "--to", target])
    assert_exit(result, (0, 2))
    assert_inert({"output": result.output}, payload_free_headings=True)


@requires_git
@pytest.mark.parametrize("hook, field, payload", grouped_cases({
    hook: GIT_CONTENT_FIELDS + ["exclude_option", "as_of_option"] for hook in ("diff", "audit")}))
def test_precommit_hook_report_is_inert(git_repo, monkeypatch, hook, field, payload):
    hostile = PAYLOADS[payload]
    ws = build_repo(git_repo, field, hostile, stage=True)
    monkeypatch.chdir(ws.root)
    args = [hook, ws.name, "--evidence", "evidence", "--as-of", hostile if field == "as_of_option" else "2026-10-10",
            "--no-strict"]
    if field == "exclude_option":
        args += ["--exclude", hostile]
    result = invoke(precommit_app, args)
    assert_exit(result, exits(payload, field, {"as_of_option"}))
    assert_inert({"output": result.output}, payload_free_headings=True)


# --- the GitHub Action --------------------------------------------------------------------------------------------

ACTION_FIELDS = ["claim", "section", "memory_name", "evidence_text", "evidence_name", "candidate_name", "skipped_name",
                 "exclude_option", "as_of_option", "evidence_option", "memory_input", "memory_outside", "diff_base",
                 "auto_rule_name", "auto_frontmatter", "transcript_cue", "media_name", "ocr_line"]
ACTION_ERRORS = {"as_of_option", "evidence_option", "diff_base"}


def action_environment(tmp_path: Path, workspace: Path, **inputs) -> dict:
    runner_dir = tmp_path / "runner"
    runner_dir.mkdir()
    env = {"RUNNER_TEMP": str(runner_dir), "GITHUB_STEP_SUMMARY": str(runner_dir / "summary.md"),
           "GITHUB_OUTPUT": str(runner_dir / "output"), "GITHUB_WORKSPACE": str(workspace),
           "GITHUB_REPOSITORY": "acme/web", "GITHUB_EVENT_NAME": "pull_request", "GITHUB_ACTIONS": "true"}
    for name in ("GITHUB_STEP_SUMMARY", "GITHUB_OUTPUT"):
        write_text_exact(Path(env[name]), "")
    env.update({f"MEMFALSIFY_{name.upper()}": value for name, value in inputs.items()})
    return env


def run_action(env: dict, capsys, allowed) -> dict[str, str]:
    """Run the Action; its stdout and stderr, its job summary and the report file it names."""
    code = action.main(env)
    captured = capsys.readouterr()
    assert code in allowed, f"exit code {code}: {captured.out[:300]!r}"
    outputs = {"stdout": captured.out, "stderr": captured.err,
               "job summary": Path(env["GITHUB_STEP_SUMMARY"]).read_text(encoding="utf-8")}
    named = dict(line.split("=", 1) for line in Path(env["GITHUB_OUTPUT"]).read_text(encoding="utf-8").splitlines()
                 if "=" in line)
    if "report" in named and Path(named["report"]).is_file():
        outputs["report file"] = Path(named["report"]).read_text(encoding="utf-8")
    return outputs


def assert_action_outputs(outputs: dict[str, str]) -> None:
    """The log lines may carry the Action's own annotations; the summary and report are Markdown."""
    problems = []
    for label, text in outputs.items():
        logged = label in ("stdout", "stderr")
        problems += [f"[{label}] {p}"
                     for p in text_problems(text, action_stdout=logged, payload_free_headings=True)]
    assert_no_problems(problems)


@pytest.mark.parametrize("field, payload", cases(ACTION_FIELDS))
def test_action_stdout_and_job_summary_are_inert(tmp_path, monkeypatch, capsys, field, payload):
    hostile = PAYLOADS[payload]
    auto = field.startswith("auto_")
    ws = build_workspace(tmp_path / "ws", "" if auto else field, hostile, reviews=False)
    if auto:
        rule = safe_name(hostile) if field == "auto_rule_name" else "extra"
        head = f"---\ndescription: {hostile}\n---\n\n" if field == "auto_frontmatter" else ""
        write(ws.root / ".cursor" / "rules" / f"{rule}.mdc", head + f"- {CLAIM}.\n")
    monkeypatch.chdir(ws.root)
    inputs = {"memory": shlex.join([ws.memory.relative_to(ws.root).as_posix()]), "evidence": "evidence",
              "as_of": "2026-10-10", "strict": "true"}
    if auto:
        inputs["memory"] = "auto"
    elif field == "memory_input":
        inputs["memory"] = hostile
    elif field == "memory_outside":  # a path that leaves the workspace is skipped with a warning that names it
        inputs["memory"] = shlex.join(["../" + safe_name(hostile)])
    elif field == "exclude_option":
        inputs["exclude"] = hostile
    elif field == "as_of_option":
        inputs["as_of"] = hostile
    elif field == "evidence_option":
        inputs["evidence"] = f"missing-{hostile}"
    elif field == "diff_base":
        inputs["diff_base"] = hostile
    outputs = run_action(action_environment(tmp_path, ws.root, **inputs), capsys, exits(payload, field, ACTION_ERRORS))
    assert_action_outputs(outputs)


@requires_git
@pytest.mark.parametrize("field, payload", cases(GIT_CONTENT_FIELDS))
def test_action_diff_mode_is_inert(git_repo, monkeypatch, capsys, field, payload):
    ws = build_repo(git_repo, field, PAYLOADS[payload])
    monkeypatch.chdir(ws.root)
    env = action_environment(git_repo.root.parent, ws.root, memory=ws.name, evidence="evidence",
                             as_of="2026-10-10", diff_base=ws.base)
    assert_action_outputs(run_action(env, capsys, exits(payload, field)))


# --- orgmemory ----------------------------------------------------------------------------------------------------

QUOTE = "Feature A is rejected for the November release"
ORG_FIELDS = ["subject", "dependency", "actor", "actor_gh", "actor_ignored", "actor_unknown", "role", "risk_note",
              "event_note", "id_decision", "id_ignored", "id_old", "evidence_id", "evidence_quote", "evidence_name",
              "source_uri", "source_uri_gh", "source_kind", "policy_note", "policy_change_id"]


def build_org(root: Path, field: str | None = None, payload: str | None = None) -> SimpleNamespace:
    """A dataset with real hashes: a request, risks, a dependency, a reject, an approval, ignored attempts, a
    revoke that names the wrong decision, a reconsideration and a role change, so that every report kind has
    something to say. ``payload`` replaces the ordinary text of ``field``."""
    def pick(name, ordinary):
        return payload if name == field else ordinary

    root.mkdir(parents=True, exist_ok=True)
    n = SimpleNamespace(
        subject=pick("subject", "Feature A"), dependency=pick("dependency", "Billing migration"),
        pm=pick("actor", "zhou"), sales=pick("actor_ignored", "wu"), unknown=pick("actor_unknown", "mallory"),
        role=pick("role", "product_manager"), evidence_id=pick("evidence_id", "e-main"),
        id_decision=pick("id_decision", "ev-approve"), id_ignored=pick("id_ignored", "ev-ignored"),
        id_old=pick("id_old", "ev-reject"), policy_id=pick("policy_change_id", "pl-leaves"),
        customer="buyer@acme", engineer="lin", admin="chen", root=root)
    if field == "actor_gh":
        n.pm = f"gh:{payload}:5003"
    quote = f"{QUOTE}, {payload}" if field == "evidence_quote" else QUOTE
    name = safe_name(payload) if field == "evidence_name" else "main"
    uri = pick("source_uri", "synthetic://email/acme/1")
    if field == "source_uri_gh":
        uri = f"https://github.com/acme/web/issues/12#issuecomment-{payload}"
    text = "Meeting notes\n" + quote + "\nEnd of notes\n"
    write(root / "sources" / f"{name}.txt", text)
    start = len("Meeting notes\n")
    write(root / "evidence.jsonl", jsonl({
        "id": n.evidence_id, "path": f"sources/{name}.txt", "sha256": sha256(text.encode("utf-8")).hexdigest(),
        "quote": quote, "start": start, "end": start + len(quote), "source_uri": uri,
        "source_kind": pick("source_kind", "email"), "captured_at": "2026-10-05T09:00:00Z"}))
    everyone = [n.role, "customer", "account_manager", "engineer"]
    write(root / "policy.json", json.dumps({
        "actors": {n.pm: [n.role], n.sales: ["account_manager"], n.customer: ["customer"],
                   n.engineer: ["engineer"], n.admin: ["org_admin"]},
        "permissions": {"request": everyone, "reconsider": everyone, "comment": everyone,
                        "raise_risk": ["engineer", n.role], "dependency": ["engineer", n.role],
                        "approve": [n.role], "reject": [n.role], "revoke": [n.role],
                        "manage_roles": ["org_admin"]}}))

    def event(id, day, hour, actor, action, **more):
        return {"id": id, "timestamp": f"2026-10-{day:02d}T{hour:02d}:00:00Z", "actor": actor, "action": action,
                "subject": n.subject, "evidence_ids": [n.evidence_id], **more}

    note = {"note": payload} if field == "event_note" else {}  # also on decisions, ignored attempts and revokes

    write(root / "events.jsonl", jsonl(
        event("ev-request", 5, 9, n.customer, "request", note=pick("event_note", "Asked in the quarterly review")),
        event("ev-pm-request", 5, 10, n.pm, "request"),
        event("ev-risk", 6, 9, n.engineer, "raise_risk", note=pick("risk_note", "Export reads the billing schema")),
        event("ev-dependency", 6, 10, n.engineer, "dependency", dependency=n.dependency),
        event(n.id_old, 7, 9, n.pm, "reject"),
        event(n.id_decision, 9, 9, n.pm, "approve", **note),
        event("ev-late-risk", 10, 9, n.engineer, "raise_risk", note="A late risk"),
        event(n.id_ignored, 10, 10, n.sales, "approve", **note),
        event("ev-ignored-2", 10, 11, n.unknown, "reject"),
        event("ev-revoke", 11, 9, n.pm, "revoke", supersedes=n.id_old, **note),
        event("ev-reconsider", 12, 9, n.customer, "reconsider")))
    write(root / "policy-log.jsonl", jsonl({
        "id": n.policy_id, "timestamp": "2026-10-20T09:00:00Z", "actor": n.admin, "action": "revoke_role",
        "subject_actor": n.pm, "role": n.role, "evidence_ids": [n.evidence_id],
        "note": pick("policy_note", "Moved to the platform team")}))
    return n


ORG_AS_OF = "2026-11-30T00:00:00Z"
ORG_COMMANDS = {
    "replay": lambda n: ["replay", n.root],
    "explain": lambda n: ["explain", n.root, n.subject],
    "audit": lambda n: ["audit", n.root, "--as-of", ORG_AS_OF],
    "changes": lambda n: ["changes", n.root, "--from", "2026-10-01T00:00:00Z", "--to", ORG_AS_OF],
}


@pytest.mark.parametrize("command, field, payload", grouped_cases({command: ORG_FIELDS for command in ORG_COMMANDS}))
def test_orgmemory_report_is_inert(tmp_path, command, field, payload):
    n = build_org(tmp_path / "dataset", field, PAYLOADS[payload])
    result = invoke(orgmemory_app, ORG_COMMANDS[command](n))
    assert_exit(result, exits(payload, field))
    assert_inert({"output": result.output}, yaml_blocks=command in ("replay", "explain"))


@pytest.mark.parametrize("field, payload", cases(ORG_FIELDS))
def test_orgmemory_export_adr_is_inert(tmp_path, field, payload):
    n = build_org(tmp_path / "dataset", field, PAYLOADS[payload])
    out = tmp_path / "adr"
    result = invoke(orgmemory_app, ["export", n.root, "--out", out])
    assert_exit(result, exits(payload, field))
    outputs = {"output": result.output}
    if out.is_dir():
        outputs.update({f"file {path.name}": path.read_text(encoding="utf-8") for path in sorted(out.iterdir())})
    assert_inert(outputs)


@pytest.mark.parametrize("field, payload", cases(ORG_FIELDS))
def test_orgmemory_export_csv_is_inert(tmp_path, field, payload):
    n = build_org(tmp_path / "dataset", field, PAYLOADS[payload])
    result = invoke(orgmemory_app, ["export", n.root, "--format", "csv"])
    assert_exit(result, exits(payload, field))
    assert_inert({"output": result.output}, csv=result.exit_code == 0)


@pytest.mark.parametrize("field, payload", cases(["actor_unknown"]))
def test_orgmemory_validate_is_inert(tmp_path, field, payload):
    n = build_org(tmp_path / "dataset", field, PAYLOADS[payload])
    result = invoke(orgmemory_app, ["validate", n.root])
    assert_exit(result, exits(payload, field))
    assert_inert({"output": result.output})


# Options that carry text into a message: a time that does not parse, a path that is refused.

ORG_OPTIONS = {"replay": ["as_of"], "explain": ["as_of"], "audit": ["as_of"], "changes": ["from", "to"],
               "export": ["as_of", "out_inside_dataset"]}


@pytest.mark.parametrize("command, option, payload", grouped_cases(ORG_OPTIONS))
def test_orgmemory_option_errors_are_inert(tmp_path, command, option, payload):
    n = build_org(tmp_path / "dataset")
    hostile = PAYLOADS[payload]
    args = {"replay": ["replay", n.root], "explain": ["explain", n.root, n.subject], "audit": ["audit", n.root],
            "changes": ["changes", n.root, "--from", "2026-10-01T00:00:00Z", "--to", ORG_AS_OF],
            "export": ["export", n.root, "--out", tmp_path / "adr"]}[command]
    if option == "out_inside_dataset":
        args[args.index("--out") + 1] = n.root / safe_name(hostile)
    elif option in ("from", "to"):
        args[args.index(f"--{option}") + 1] = hostile
    else:
        args += ["--as-of", hostile]
    result = invoke(orgmemory_app, args)
    assert_exit(result, (2,))
    assert_inert({"output": result.output})


# Usage errors: the command line itself is the untrusted text, and the framework prints it back.

USAGE_PAYLOADS = {**PAYLOADS, "rich_markup": ("start " + MARK + " [link=http://evil.example/" + MARK
                                              + "]click[/link] [bold red]x[/] [/unknown]")}


@pytest.mark.parametrize("tool, kind, payload", grouped_cases({
    tool: ["command", "option", "format", "number"] for tool in ("memfalsify", "orgmemory")}, USAGE_PAYLOADS))
def test_cli_usage_errors_are_inert(tmp_path, tool, kind, payload):
    hostile = USAGE_PAYLOADS[payload]
    if tool == "memfalsify":
        ws = build_workspace(tmp_path / "ws", "", "", reviews=False)
        app, target, number = memfalsify_app, ["audit", ws.memory], "--stale-days"
    else:
        app, target, number = orgmemory_app, ["audit", build_org(tmp_path / "dataset").root], "--stale-days"
    args = {"command": [hostile], "option": [f"--{hostile}"], "format": [*target, "--format", hostile],
            "number": [*target, number, hostile]}[kind]
    result = invoke(app, args)
    assert_exit(result, (2,))
    assert_inert({"output": result.output})


# Corrupt datasets: the error message names the offending text.

ORG_LOAD_FIELDS = ["events_raw_line", "event_action", "event_extra_key", "policy_extra_key", "policy_permission_key",
                   "evidence_hash_id", "evidence_span_id", "evidence_unknown_id", "policy_log_role",
                   "policy_log_actor", "duplicate_event_id", "unknown_subject"]


def corrupt_org(root: Path, field: str, payload: str) -> list:
    """Break a dataset in the way ``field`` names; returns the command that should now report the problem."""
    n = build_org(root)

    def rewrite(name, change):
        records = [json.loads(line) for line in (root / name).read_text(encoding="utf-8").splitlines()]
        change(records)
        write(root / name, jsonl(*records))

    def retarget(records):
        for record in records:
            record["evidence_ids"] = [payload]

    if field == "events_raw_line":
        with open(root / "events.jsonl", "a", encoding="utf-8", newline="") as handle:
            handle.write(payload + "\n")
    elif field == "event_action":
        rewrite("events.jsonl", lambda records: records[0].update(action=payload))
    elif field == "event_extra_key":
        rewrite("events.jsonl", lambda records: records[0].update({payload: "x"}))
    elif field == "policy_extra_key":
        policy = json.loads((root / "policy.json").read_text(encoding="utf-8"))
        policy[payload] = "x"
        write(root / "policy.json", json.dumps(policy))
    elif field == "policy_permission_key":
        policy = json.loads((root / "policy.json").read_text(encoding="utf-8"))
        policy["permissions"][payload] = ["engineer"]
        write(root / "policy.json", json.dumps(policy))
    elif field in ("evidence_hash_id", "evidence_span_id"):
        damage = {"sha256": "0" * 64} if field == "evidence_hash_id" else {"start": 0, "end": 5}
        rewrite("evidence.jsonl", lambda records: records[0].update(id=payload, **damage))
        rewrite("events.jsonl", retarget)
        rewrite("policy-log.jsonl", retarget)
    elif field == "evidence_unknown_id":
        rewrite("events.jsonl", lambda records: records[0].update(evidence_ids=[payload]))
    elif field == "policy_log_role":
        rewrite("policy-log.jsonl", lambda records: records[0].update(role=payload))
    elif field == "policy_log_actor":
        rewrite("policy-log.jsonl", lambda records: records[0].update(actor=payload))
    elif field == "duplicate_event_id":
        rewrite("events.jsonl", lambda records: [r.update(id=payload) for r in records[:2]])
    elif field == "unknown_subject":
        return ["explain", root, payload]
    return ["validate", root]


@pytest.mark.parametrize("field, payload", cases(ORG_LOAD_FIELDS))
def test_orgmemory_error_messages_are_inert(tmp_path, field, payload):
    command = corrupt_org(tmp_path / "dataset", field, PAYLOADS[payload])
    result = invoke(orgmemory_app, command)
    assert_exit(result, (2,))
    assert_inert({command[0]: result.output})
    if command[0] == "validate":  # the same dataset through a second loader
        replayed = invoke(orgmemory_app, ["replay", command[1]])
        assert_exit(replayed, (2,))
        assert_inert({"replay": replayed.output}, yaml_blocks=True)


# Commands that echo what they just wrote or refused.

ORG_WRITE_FIELDS = {
    "append": ["new_event_id", "new_event_action", "new_event_key"],
    "policy": ["change_id", "change_role", "change_actor", "change_subject_actor"],
    "evidence": ["evidence_add_id", "evidence_add_quote", "evidence_add_path"],
}
ORG_WRITE_ERRORS = {"new_event_action", "new_event_key", "change_role", "change_actor", "change_subject_actor",
                    "evidence_add_quote", "evidence_add_path"}


def write_command(n: SimpleNamespace, kind: str, field: str, payload: str) -> list:
    if kind == "append":
        event = {"id": "ev-new", "timestamp": "2026-10-21T09:00:00Z", "actor": n.customer, "action": "comment",
                 "subject": n.subject, "evidence_ids": [n.evidence_id]}
        if field == "new_event_id":
            event["id"] = payload
        elif field == "new_event_action":
            event["action"] = payload
        else:
            event[payload] = "x"
        return ["append", n.root, "--event", json.dumps(event)]
    if kind == "policy":
        args = {"id": "pl-grant", "actor": n.admin, "subject_actor": n.sales, "role": n.role}
        args[{"change_id": "id", "change_role": "role", "change_actor": "actor",
              "change_subject_actor": "subject_actor"}[field]] = payload
        return ["policy", "grant", n.root, "--id", args["id"], "--timestamp", "2026-10-21T09:00:00Z",
                "--actor", args["actor"], "--subject-actor", args["subject_actor"], "--role", args["role"],
                "--evidence-id", n.evidence_id]
    values = {"id": "e-extra", "path": "sources/main.txt", "quote": "Feature A is rejected"}
    values[field.removeprefix("evidence_add_")] = payload
    return ["evidence", "add", n.root, "--id", values["id"], "--path", values["path"], "--quote", values["quote"],
            "--source-uri", "synthetic://email/acme/2", "--source-kind", "email",
            "--captured-at", "2026-10-05T09:00:00Z"]


@pytest.mark.parametrize("kind, field, payload", grouped_cases(ORG_WRITE_FIELDS))
def test_orgmemory_write_commands_echo_inert_text(tmp_path, kind, field, payload):
    n = build_org(tmp_path / "dataset")
    result = invoke(orgmemory_app, write_command(n, kind, field, PAYLOADS[payload]))
    assert_exit(result, exits(payload, field, ORG_WRITE_ERRORS))
    assert_inert({"output": result.output})


# --- imported GitHub and Jira text ----------------------------------------------------------------------------------

GITHUB_FIELDS = ["issue_title", "issue_body", "comment_body", "review_body", "login"]
JIRA_FIELDS = ["summary", "description", "comment_body", "display_name"]


def adf(*lines: str) -> dict:
    return {"type": "doc", "version": 1,
            "content": [{"type": "paragraph", "content": [{"type": "text", "text": line}]} for line in lines]}


def replace_everywhere(node, old, new):
    """In place: every string equal to ``old`` becomes ``new``."""
    items = node.items() if isinstance(node, dict) else enumerate(node) if isinstance(node, list) else ()
    for key, value in list(items):
        if value == old:
            node[key] = new
        else:
            replace_everywhere(value, old, new)


def github_capture(field: str, payload: str) -> dict:
    capture = json.loads((FIXTURES / "github" / "capture-1.json").read_text(encoding="utf-8"))
    first, second = capture["items"][0], capture["items"][1]
    if field == "issue_title":
        first["issue"]["title"] = payload
    elif field == "issue_body":
        first["issue"]["body"] = payload
    elif field == "comment_body":
        for event in first["timeline"]:
            if event["event"] == "commented":
                event["body"] = payload
    elif field == "review_body":
        for event in second["timeline"]:
            if event["event"] == "reviewed":
                event["body"] = payload
    elif field == "login":
        replace_everywhere(first, "buyer-acme", payload)
    return capture


def jira_capture(field: str, payload: str) -> dict:
    capture = json.loads((FIXTURES / "jira" / "capture-1.json").read_text(encoding="utf-8"))
    for page in capture["pages"]:
        for issue in page["issues"]:
            if issue["key"] != "ACME-1":
                continue
            fields = issue["fields"]
            if field == "summary":
                fields["summary"] = payload
            elif field == "description":
                fields["description"] = adf(payload)
            elif field == "comment_body":
                for comment in fields["comment"]["comments"]:
                    comment["body"] = adf(payload)
            elif field == "display_name":
                replace_everywhere(issue, "Buyer (Acme Retail)", payload)
    return capture


IMPORTS = {
    "github": SimpleNamespace(capture=github_capture, subject="acme/web#1", files=("policy.json", "github.json"),
                              command=["github", "convert"]),
    "jira": SimpleNamespace(capture=jira_capture, subject="ACME-1", files=("policy.json", "jira.json"),
                            command=["jira", "convert"]),
}


@pytest.mark.parametrize("source, field, payload", grouped_cases({"github": GITHUB_FIELDS, "jira": JIRA_FIELDS},
                                                                 IMPORT_PAYLOADS))
def test_imported_text_stays_inert_in_every_report(tmp_path, source, field, payload):
    importer = IMPORTS[source]
    dataset = tmp_path / "dataset"
    dataset.mkdir()
    for name in importer.files:
        shutil.copyfile(FIXTURES / source / name, dataset / name)
    capture = tmp_path / "capture.json"
    write_text_exact(capture, json.dumps(importer.capture(field, IMPORT_PAYLOADS[payload])))
    converted = invoke(orgmemory_app, [*importer.command, dataset, capture])
    assert_exit(converted, (0, 2, 3))
    outputs = {"convert": converted.output}
    if converted.exit_code == 0:
        outputs["replay"] = invoke(orgmemory_app, ["replay", dataset]).output
        outputs["explain"] = invoke(orgmemory_app, ["explain", dataset, importer.subject]).output
        outputs["audit"] = invoke(orgmemory_app, ["audit", dataset, "--as-of", ORG_AS_OF]).output
        outputs["changes"] = invoke(orgmemory_app, ["changes", dataset, "--from", "2026-10-01T00:00:00Z",
                                                    "--to", ORG_AS_OF]).output
        exported = tmp_path / "adr"
        invoke(orgmemory_app, ["export", dataset, "--out", exported])
        if exported.is_dir():
            outputs.update({f"adr {p.name}": p.read_text(encoding="utf-8") for p in sorted(exported.iterdir())})
    problems = []
    for label, text in outputs.items():
        problems += [f"[{label}] {p}" for p in text_problems(text, yaml_blocks=label in ("replay", "explain"))]
    assert_no_problems(problems)
