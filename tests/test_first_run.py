"""The first five minutes of MemFalsify: ``audit`` with no MEMORY argument, ``init``, ``evidence from-git``.

``tests/fixtures/audit-explicit`` holds the output of the v0.5.0 code for runs that name the memory file; those
runs must not change. The demo's outcomes are checked in both languages, and git history is treated as hostile.
"""
import inspect
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import pytest
from typer.testing import CliRunner

from conftest import snapshot, write_text_exact
from memcore import locate_quote
from memcore.display import one_line
from memcore.reading import read_limits
from memfalsify import AuditReport, AuditSet, __version__, audit_directory, claim_id
from memfalsify import auditdir, demo, gitevidence, ownfiles
from memfalsify.audit import ATTENTION, MODEL_NOTE
from memfalsify.auditdir import select_memories
from memfalsify.cli import app
from memfalsify.corpus import MAX_BYTES, is_report, scan
from memfalsify.gitevidence import date_argument, file_names, parse_log, render
from memfalsify.htmlreport import set_to_html
from memfalsify.report import set_to_markdown
from memfalsify.verify import Judgment, VerifierStats

REPO = Path(__file__).resolve().parent.parent
EXAMPLES = REPO / "examples"
GOLDEN = Path(__file__).parent / "fixtures" / "audit-explicit"
QUICKSTART = REPO / "docs" / "quickstart.md"
QUICKSTART_ZH = REPO / "docs" / "quickstart.zh-CN.md"
runner = CliRunner()
needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")
posix_only = pytest.mark.skipif(os.name == "nt", reason="needs POSIX file names, permissions or a bash shell")


def write(root: Path, files: dict[str, str]) -> None:
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        write_text_exact(path, text)


# --- naming the memory file changes nothing ------------------------------------------------------------------

EXPLICIT = {
    "with-reviews.md": ["MEMORY.md", "--evidence", "evidence", "--reviews", "reviews.jsonl", "--as-of", "2026-10-10"],
    "with-reviews.json": ["MEMORY.md", "--evidence", "evidence", "--reviews", "reviews.jsonl", "--as-of", "2026-10-10",
                          "--format", "json"],
    "with-reviews.html": ["MEMORY.md", "--evidence", "evidence", "--reviews", "reviews.jsonl", "--as-of", "2026-10-10",
                          "--format", "html"],
    "no-evidence.md": ["MEMORY.md", "--as-of", "2026-10-10"],
    "evidence-is-cwd.json": ["MEMORY.md", "--evidence", ".", "--no-follow-links", "--as-of", "2026-10-10",
                             "--format", "json"],
}


def _golden(name: str) -> bytes:
    return (GOLDEN / name).read_bytes().replace(b'"version": "0.5.0"', f'"version": "{__version__}"'.encode())


@pytest.mark.parametrize("name", EXPLICIT)
def test_runs_that_name_the_file_match_v050_byte_for_byte(name, memfalsify_demo, monkeypatch):
    monkeypatch.chdir(memfalsify_demo)
    shown = runner.invoke(app, ["audit", *EXPLICIT[name]])
    assert shown.exit_code == 0, shown.output
    # .output drops the CR a Windows text stream adds; the exact bytes are the --output run below.
    assert shown.stdout.encode("utf-8") == _golden(name)
    assert shown.stderr == ""
    written = memfalsify_demo.parent / ("out-" + name)
    saved = runner.invoke(app, ["audit", *EXPLICIT[name], "--output", str(written)])
    assert saved.exit_code == 0, saved.output
    assert written.read_bytes() == _golden(name)
    assert saved.stdout == ""


def test_strict_on_a_named_file_is_unchanged(memfalsify_demo, monkeypatch):
    monkeypatch.chdir(memfalsify_demo)
    assert runner.invoke(app, ["audit", "MEMORY.md", "--evidence", "evidence", "--strict"]).exit_code == 1
    assert runner.invoke(app, ["audit", "MEMORY.md", "--strict"]).exit_code == 0
    assert runner.invoke(app, ["audit", "missing.md"]).exit_code == 2


# --- audit with no MEMORY argument: which files --------------------------------------------------------------

AGENT_TREE = {
    "AGENTS.md": "# Rules\n\n- The public API allows 100 requests per minute.\n- Deploys use the staging cluster.\n",
    "CLAUDE.md": "# Claude\n\n@AGENTS.md\n\n- Use tabs in the Makefile only.\n- Deploys use the staging cluster.\n",
    "MEMORY.md": "# Memory\n\n- [Notes](notes.md)\n- The billing service runs in Frankfurt.\n",
    "notes.md": "- The nightly export stops at midnight on Sundays.\n",
    ".cursor/rules/api.mdc": "---\nglobs: src/**\n---\n- Handlers return typed errors in src.\n",
    "packages/web/AGENTS.md": "- The web package targets evergreen browsers.\n",
    "docs/api.md": "The public API allows 60 requests per minute.\n",
    "docs/MEMORY.md": "- A nested memory file is project data, not this project's memory.\n",
    "node_modules/dep/CLAUDE.md": "- A dependency's instructions are not ours.\n",
}
AGENT_FILES = [".cursor/rules/api.mdc", "AGENTS.md", "CLAUDE.md", "MEMORY.md", "packages/web/AGENTS.md"]


@pytest.fixture
def agent_tree(tmp_path, monkeypatch):
    root = tmp_path / "project"
    write(root, AGENT_TREE)
    monkeypatch.chdir(root)
    return root


def audit_here(*args: str):
    return runner.invoke(app, ["audit", "--as-of", "2026-10-10", *args])


def audit_set(*args: str) -> AuditSet:
    result = audit_here("--format", "json", *args)
    assert result.exit_code == 0, result.output
    return AuditSet.model_validate_json(result.stdout)


def test_selection_is_discovery_plus_top_level_memory_in_path_order(agent_tree):
    assert select_memories(Path("."))[0] == AGENT_FILES
    assert audit_set().files == AGENT_FILES


def test_selection_excludes_and_keeps_each_file_once(agent_tree, make_symlink):
    assert select_memories(Path("."), exclude=["packages/**", "MEMORY.md"])[0] == [".cursor/rules/api.mdc", "AGENTS.md",
                                                                                  "CLAUDE.md"]
    (agent_tree / "MEMORY.md").unlink()
    make_symlink(agent_tree / "MEMORY.md", "AGENTS.md")
    assert "MEMORY.md" not in select_memories(Path("."))[0]


def test_a_memory_link_leading_outside_is_passed_over_and_said(agent_tree, make_symlink, tmp_path):
    (agent_tree / "MEMORY.md").unlink()
    write(tmp_path, {"outside.md": "- A secret outside the project stays there.\n"})
    make_symlink(agent_tree / "MEMORY.md", tmp_path / "outside.md")
    names, passed_over = select_memories(Path("."))
    assert "MEMORY.md" not in names
    assert [s.path for s in passed_over] == ["MEMORY.md"]
    result = audit_here()
    assert result.exit_code == 0 and "A secret outside" not in result.output
    assert "1 place(s) not searched" in result.stderr
    assert "## Not searched for memory files" in result.stdout


def test_the_note_says_which_files_on_one_line(agent_tree):
    result = audit_here()
    lines = result.stderr.splitlines()
    assert lines[0] == ("note: auditing 5 files against .: .cursor/rules/api.mdc, AGENTS.md, CLAUDE.md, MEMORY.md, "
                        "packages/web/AGENTS.md")
    assert re.fullmatch(r"note: \d+ claim\(s\) in 5 file\(s\): .*Nothing was changed\..*", lines[-1])
    assert len(lines) == 2


def test_a_long_list_is_shortened_in_the_note_but_not_in_the_report(tmp_path, monkeypatch):
    write(tmp_path, {f"p{n:02d}/AGENTS.md": f"- Package {n:02d} ships its own release notes.\n" for n in range(12)})
    monkeypatch.chdir(tmp_path)
    result = audit_here()
    assert "and 4 more" in result.stderr
    assert "`p11/AGENTS.md`" in result.stdout


def test_nothing_found_exits_two_with_one_line_suggesting_init(tmp_path, monkeypatch):
    write(tmp_path, {"README.md": "Nothing for an agent here.\n"})
    monkeypatch.chdir(tmp_path)
    result = audit_here()
    assert result.exit_code == 2 and result.stdout == ""
    assert len(result.stderr.strip().splitlines()) == 1
    assert "memfalsify init" in result.stderr
    assert audit_directory(Path(".")).files == []


def test_excluding_everything_says_so(agent_tree):
    result = audit_here("--exclude", "*")
    assert result.exit_code == 2 and "--exclude applied" in result.stderr


def test_a_hostile_directory_name_is_passed_over_and_stays_inert(tmp_path, monkeypatch):
    write(tmp_path, {"AGENTS.md": "- The nightly export stops at midnight on Sundays.\n",
                     "sub/CLAUDE.md": "- The billing service runs in Frankfurt.\n",
                     "pkg\u202eevil/AGENTS.md": "- The evil package ships on Fridays only.\n"})
    monkeypatch.chdir(tmp_path)
    result = audit_here()
    assert "\u202e" not in result.stderr and "1 place(s) not searched" in result.stderr
    assert "\u202e" not in result.stdout and "\\u202e" in result.stdout and "Fridays" not in result.stderr


def test_an_unreadable_file_is_named_and_fails_closed(agent_tree):
    (agent_tree / "packages/web/AGENTS.md").write_bytes(b"\xff\xfe not text\n")
    result = audit_here()
    assert result.exit_code == 2 and result.stdout == ""
    assert "packages/web/AGENTS.md" in result.stderr and "--exclude" in result.stderr
    assert audit_here("--exclude", "packages/**").exit_code == 0


# --- audit with no MEMORY argument: what it compares ---------------------------------------------------------

def test_one_file_reads_exactly_like_naming_it(tmp_path, monkeypatch):
    write(tmp_path, {"MEMORY.md": "- The public API allows 100 requests per minute.\n",
                     "docs/api.md": "The public API allows 60 requests per minute.\n"})
    monkeypatch.chdir(tmp_path)
    for fmt in ("markdown", "html"):
        assert audit_here("--format", fmt).stdout == audit_here("MEMORY.md", "--evidence", ".", "--format", fmt).stdout
    alone = json.loads(audit_here("MEMORY.md", "--evidence", ".", "--format", "json").stdout)
    assert json.loads(audit_here("--format", "json").stdout)["reports"] == [alone]


def test_several_files_match_their_own_audits_where_files_stand_alone(tmp_path, monkeypatch):
    write(tmp_path, {"AGENTS.md": "- The public API allows 100 requests per minute.\n",
                     "sub/CLAUDE.md": "- The nightly export stops at midnight on Sundays.\n",
                     "docs/api.md": "The public API allows 60 requests per minute.\n"})
    monkeypatch.chdir(tmp_path)
    together = audit_set()
    alone = AuditReport.model_validate_json(audit_here("AGENTS.md", "--evidence", ".", "--format", "json").stdout)
    assert together.reports[0].findings == alone.findings
    nested = AuditReport.model_validate_json(audit_here("sub/CLAUDE.md", "--evidence", ".", "--format", "json").stdout)
    assert [f.status for f in together.reports[1].findings] == [f.status for f in nested.findings]
    assert together.reports[1].findings[0].location == "sub/CLAUDE.md:1"  # relative to where you ran it


def test_a_claim_in_several_files_is_reported_once_under_the_first(agent_tree):
    result = audit_set()
    by_file = {report.memory_file: report for report in result.reports}
    shared = [f for report in result.reports for f in report.findings if "staging cluster" in f.text]
    assert len(shared) == 1 and shared[0].location == "AGENTS.md:4"
    assert any(i.kind == "duplicate" and "CLAUDE.md:6" in i.detail for i in shared[0].issues)
    assert all("staging" not in f.text for f in by_file["CLAUDE.md"].findings)


def test_claims_of_a_chosen_file_stay_under_it_and_linked_notes_go_to_the_linker(agent_tree):
    by_file = {report.memory_file: report for report in audit_set().reports}
    assert by_file["CLAUDE.md"].files_read == ["CLAUDE.md", "AGENTS.md"]
    assert not [f for f in by_file["CLAUDE.md"].findings if "requests per minute" in f.text]
    assert any("requests per minute" in f.text for f in by_file["AGENTS.md"].findings)
    assert any(f.location == "notes.md:1" for f in by_file["MEMORY.md"].findings)
    assert by_file["MEMORY.md"].files_read == ["MEMORY.md", "notes.md"]


def test_no_follow_links_is_honoured(agent_tree):
    by_file = {report.memory_file: report for report in audit_set("--no-follow-links").reports}
    assert by_file["MEMORY.md"].files_read == ["MEMORY.md"]
    assert not any("export" in f.text for report in by_file.values() for f in report.findings)


def test_each_files_evidence_leaves_out_only_itself_and_what_it_links(agent_tree):
    by_file = {report.memory_file: report for report in audit_set().reports}
    api = next(f for f in by_file["AGENTS.md"].findings if "requests per minute" in f.text)
    assert api.status == "candidate_counterevidence"
    assert {e.span.path for e in api.evidence} == {"docs/api.md"}
    # The nested AGENTS.md may be cited by others, but no file is ever its own evidence.
    for report in by_file.values():
        for finding in report.findings:
            assert not {e.span.path for e in finding.evidence} & set(report.files_read)
    assert "node_modules/dep/CLAUDE.md" not in [e.span.path for report in by_file.values() for f in report.findings
                                                 for e in f.evidence]


def test_two_agent_files_that_disagree_show_up(tmp_path, monkeypatch):
    write(tmp_path, {"AGENTS.md": "- The service listens on port 8000 in production.\n",
                     "CLAUDE.md": "- The service listens on port 9000 in production.\n"})
    monkeypatch.chdir(tmp_path)
    assert audit_here("--strict").exit_code == 1
    reports = audit_set().reports
    assert reports[0].findings[0].evidence[0].span.path == "CLAUDE.md"
    assert any(i.kind == "internal_conflict" for i in reports[0].findings[0].issues)


def test_strict_means_the_same_thing_across_files(tmp_path, monkeypatch):
    write(tmp_path, {"AGENTS.md": "- The nightly export stops at midnight on Sundays.\n",
                     "sub/AGENTS.md": "- The public API allows 100 requests per minute.\n",
                     "docs/api.md": "The public API allows 60 requests per minute.\n"})
    monkeypatch.chdir(tmp_path)
    assert audit_here().exit_code == 0
    strict = audit_here("--strict", "--format", "json")
    assert strict.exit_code == 1
    assert ATTENTION & json.loads(strict.stdout)["summary"].keys()
    (tmp_path / "docs/api.md").unlink()
    assert audit_here("--strict").exit_code == 0


def test_evidence_can_still_be_chosen_and_must_exist(agent_tree):
    result = audit_set("--evidence", "docs")
    assert result.evidence_root == "docs"
    assert audit_here("--evidence", "missing").exit_code == 2
    (agent_tree / "afile").write_bytes(b"x")
    assert audit_here("--evidence", "afile").exit_code == 2


def test_exclude_applies_to_files_and_evidence(agent_tree):
    result = audit_set("--exclude", "docs/**")
    assert result.excluded == ["docs/**"]
    assert not any(e.span.path.startswith("docs/") for report in result.reports for f in report.findings
                   for e in f.evidence)


def test_an_exclude_that_matches_nothing_changes_nothing(tmp_path, monkeypatch):
    write(tmp_path, {"CLAUDE.md": "@AGENTS.md\n\n- Deploys use the staging cluster.\n",
                     "AGENTS.md": "- The nightly export stops at midnight on Sundays.\n"})
    monkeypatch.chdir(tmp_path)
    assert audit_here().exit_code == 0
    result = audit_here("--exclude", "vendor/**", "--format", "json")
    assert result.exit_code == 0, result.output
    assert AuditSet.model_validate_json(result.stdout).excluded == ["vendor/**"]
    (tmp_path / "empty").mkdir()
    assert audit_here("--evidence", "empty", "--exclude", "vendor/**").exit_code == 0


def test_an_exclude_that_empties_the_evidence_still_fails(tmp_path, monkeypatch):
    write(tmp_path, {"MEMORY.md": "- The public API allows 100 requests per minute.\n",
                     "docs/api.md": "The public API allows 60 requests per minute.\n"})
    monkeypatch.chdir(tmp_path)
    result = audit_here("--evidence", "docs", "--exclude", "api.md")
    assert result.exit_code == 2 and "--exclude" in result.stderr


def test_a_named_file_keeps_its_exclude_check(tmp_path, monkeypatch):
    write(tmp_path, {"MEMORY.md": "- The public API allows 100 requests per minute.\n"})
    monkeypatch.chdir(tmp_path)
    result = audit_here("MEMORY.md", "--evidence", ".", "--exclude", "vendor/**")
    assert result.exit_code == 2 and "no evidence passages indexed with --exclude" in result.stderr


def test_reviews_work_across_files_and_unknown_ids_still_fail(agent_tree):
    claim = "The billing service runs in Frankfurt."
    write(agent_tree, {"docs/infra.md": "The billing service was moved to Dublin.\n"})
    line = {"memory_id": claim_id(claim), "relation": "contradicts", "path": "docs/infra.md",
            "quote": "moved to Dublin", "reviewer": "ada"}
    write(agent_tree, {"reviewed.jsonl": json.dumps(line) + "\n"})
    result = audit_set("--reviews", "reviewed.jsonl")
    assert result.summary["challenged"] == 1
    assert next(r for r in result.reports if r.memory_file == "MEMORY.md").summary["challenged"] == 1
    assert "reviewed.jsonl" not in {e.span.path for r in result.reports for f in r.findings for e in f.evidence}
    write(agent_tree, {"reviewed.jsonl": json.dumps({**line, "memory_id": "m-00000000"}) + "\n"})
    stale = audit_here("--reviews", "reviewed.jsonl")
    assert stale.exit_code == 2 and "unknown memory_id" in stale.stderr


def test_review_records_are_not_evidence_and_the_note_says_how_to_apply_them(tmp_path, monkeypatch):
    claim = "The public API allows 100 requests per minute."
    write(tmp_path, {"MEMORY.md": f"- {claim}\n", "docs/api.md": "The public API allows 60 requests per minute.\n",
                     "reviews.jsonl": json.dumps({"memory_id": claim_id(claim), "relation": "supports",
                                                  "path": "docs/api.md", "quote": "The public API allows 100",
                                                  "reviewer": "ada"}) + "\n"})
    monkeypatch.chdir(tmp_path)
    result = audit_here()
    assert "reviews.jsonl holds reviews" in result.stderr and "--reviews reviews.jsonl" in result.stderr
    report = audit_set().reports[0]
    assert {e.span.path for f in report.findings for e in f.evidence} == {"docs/api.md"}
    assert [s.path for s in report.skipped_evidence] == ["reviews.jsonl"]
    assert "review records" in report.skipped_evidence[0].reason


@pytest.mark.parametrize("name,text,reason", [
    ("reviews.jsonl", '{"memory_id": "m", "relation": "supports", "path": "p", "quote": "q", "reviewer": "r"}\n',
     ownfiles.REVIEWS),
    ("events.jsonl", '{"id": "e", "timestamp": "t", "actor": "a", "action": "x", "subject": "s", "evidence_ids": []}\n',
     ownfiles.DECISION_LOG),
    ("policy.json", '{"actors": {}, "permissions": {}}\n', ownfiles.DECISION_LOG),
    ("replay.md", "# Organizational memory replay\n\nThe public API allows 60 requests per minute.\n",
     ownfiles.GENERATED),
])
def test_memory_labs_own_files_are_left_out(tmp_path, monkeypatch, name, text, reason):
    write(tmp_path, {"MEMORY.md": "- The public API allows 100 requests per minute.\n", name: text})
    monkeypatch.chdir(tmp_path)
    assert ownfiles.own_files(tmp_path) == [(name, reason)]
    assert ownfiles.own_files(tmp_path, exclude=[name]) == []
    report = audit_set().reports[0]
    assert not report.findings[0].evidence
    assert [s.path for s in report.skipped_evidence] == [name]
    # Naming the file keeps the old behaviour: the file is ordinary evidence there.
    named = AuditReport.model_validate_json(audit_here("MEMORY.md", "--evidence", ".", "--format", "json").stdout)
    assert name not in [s.path for s in named.skipped_evidence]


def test_recognising_own_files_never_reads_past_the_scan_limit(tmp_path):
    """A JSON dump larger than memory must not be read whole to learn that it is not a report."""
    (tmp_path / "data.jsonl").write_bytes(b'{"a": 1}\n' + b" " * (3 * MAX_BYTES))
    with read_limits(total_bytes=10 ** 12) as limits:  # within limits every byte read is counted
        assert ownfiles.own_files(tmp_path) == []
    assert 0 < limits.bytes_read <= ownfiles.HEAD + MAX_BYTES + 2


def test_recognition_still_works_up_to_the_limit_and_by_the_first_line(tmp_path):
    review = {"memory_id": "m", "relation": "supports", "path": "p", "quote": "q", "reviewer": "r"}
    report = {"tool": "orgmemory", "mode": "read_only"}
    (tmp_path / "reviews.jsonl").write_bytes(json.dumps(review).encode("utf-8") + b"\n" + b" " * (2 * MAX_BYTES))
    (tmp_path / "replay.json").write_bytes(
        json.dumps({**report, "padding": "x" * (2 * ownfiles.HEAD)}).encode("utf-8"))
    (tmp_path / "huge.json").write_bytes(json.dumps({**report, "padding": "x" * MAX_BYTES}).encode("utf-8"))
    assert ownfiles.own_files(tmp_path) == [("replay.json", ownfiles.GENERATED), ("reviews.jsonl", ownfiles.REVIEWS)]


def test_a_json_file_over_the_limit_is_listed_as_too_large(tmp_path, monkeypatch):
    write(tmp_path, {"MEMORY.md": "- The public API allows 100 requests per minute.\n"})
    (tmp_path / "data.json").write_bytes(b'{"a": 1,' + b" " * MAX_BYTES + b"}")
    monkeypatch.chdir(tmp_path)
    assert audit_set().reports[0].skipped_evidence[0].reason == "larger than 2 MB"


def nested(depth: int, opening: str = '{"a":', closing: str = "}") -> str:
    return opening * depth + "0" * (opening == '{"a":') + closing * depth


@pytest.mark.parametrize("name,text", [
    ("payload.json", nested(10000)),
    ("short.json", nested(1200)),
    ("records.jsonl", nested(10000) + "\n"),
    ("clip.ocr.json", nested(10000)),
    ("importer.json", '{"importer": ' + nested(900, "[", "]") + "}"),
    ("importer-deeper.json", '{"importer": ' + nested(5000, "[", "]") + "}"),
])
def test_deeply_nested_json_is_ordinary_evidence_not_a_crash(tmp_path, monkeypatch, name, text):
    write(tmp_path, {"CLAUDE.md": "- The public API allows 100 requests per minute.\n", name: text})
    monkeypatch.chdir(tmp_path)
    assert ownfiles.own_files(tmp_path) == []
    result = audit_here()
    assert result.exit_code == 0, repr(result.exception)
    assert "# MemFalsify audit report" in result.stdout


@pytest.mark.parametrize("depth", [900, 1200, 10000])
def test_the_web_page_copy_survives_deep_nesting_too(tmp_path, depth):
    internal = pytest.importorskip("memlab_ui.internal")
    write(tmp_path, {"payload.json": nested(depth)})
    assert internal.internal_files(tmp_path) == []


def test_own_file_recognition_stays_in_step_with_the_web_page():
    internal = pytest.importorskip("memlab_ui.internal")
    assert inspect.getsource(internal.classify) == inspect.getsource(ownfiles.classify)
    assert inspect.getsource(internal._object) == inspect.getsource(ownfiles._object)
    for name in ("_REVIEW", "_EVENT", "_ROLE_CHANGE", "_MANIFEST", "_CLAIM", "_ORG_REPORTS", "_ORG_HTML", "_ADR", "_CSV",
                 "_IMPORTER_SETTINGS", "REVIEWS", "DECISION_LOG", "GENERATED", "HEAD"):
        assert getattr(internal, name) == getattr(ownfiles, name), name


@pytest.mark.parametrize("fmt", ["markdown", "json", "html"])
def test_a_report_of_several_files_is_never_evidence_for_the_next_run(agent_tree, fmt):
    out = agent_tree / f"report.{fmt}"
    first = audit_here("--format", fmt, "--output", str(out))
    assert first.exit_code == 0, first.output
    assert is_report(out.read_bytes().decode("utf-8"))
    again = audit_set()
    assert all(not e.span.path.startswith("report.") for r in again.reports for f in r.findings for e in f.evidence)
    assert any(s.path == out.name and "report" in s.reason for r in again.reports for s in r.skipped_evidence)


@pytest.mark.parametrize("name", ["MEMORY.md", "notes.md", ".cursor/rules/api.mdc", "packages/web/AGENTS.md"])
def test_output_never_lands_on_a_memory_file(agent_tree, name):
    before = (agent_tree / name).read_bytes()
    result = audit_here("--output", name)
    assert result.exit_code == 2
    assert (agent_tree / name).read_bytes() == before


def hard_link(path: Path, target: Path) -> None:
    try:
        os.link(target, path)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"no hard links here: {exc}")


LINKED_INSTRUCTIONS = "- The deploy freeze starts on Friday.\n"


@pytest.fixture
def skipped_instructions(tmp_path, monkeypatch, make_symlink):
    """A chosen instruction file that parsing skips: its link leads out of its own directory."""
    write(tmp_path, {"instructions.md": LINKED_INSTRUCTIONS})
    (tmp_path / ".github").mkdir()
    make_symlink(tmp_path / ".github" / "copilot-instructions.md", Path("..") / "instructions.md")
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.mark.parametrize("target", [".github/copilot-instructions.md", "instructions.md"])
def test_output_never_lands_on_a_chosen_file_parsing_skipped(skipped_instructions, target):
    assert audit_set().files == [".github/copilot-instructions.md"]
    assert audit_set().reports[0].files_read == []
    result = audit_here("--output", target)
    assert result.exit_code == 2 and "refusing to overwrite" in result.stderr
    assert (skipped_instructions / "instructions.md").read_bytes() == LINKED_INSTRUCTIONS.encode("utf-8")


def test_a_named_file_parsing_skipped_is_protected_too(skipped_instructions):
    name = ".github/copilot-instructions.md"
    result = runner.invoke(app, ["audit", name, "--output", name])
    assert result.exit_code == 2 and "refusing to overwrite" in result.stderr
    assert (skipped_instructions / "instructions.md").read_bytes() == LINKED_INSTRUCTIONS.encode("utf-8")


@pytest.mark.parametrize("name", ["CLAUDE.md", "report.md"])
def test_output_never_lands_on_a_memory_file_through_a_hard_link(tmp_path, monkeypatch, name):
    original = b"- The public API allows 100 requests per minute.\n"
    (tmp_path / "AGENTS.md").write_bytes(original)
    hard_link(tmp_path / name, tmp_path / "AGENTS.md")
    monkeypatch.chdir(tmp_path)
    result = audit_here("--output", name)
    assert result.exit_code == 2 and "refusing to overwrite" in result.stderr
    assert (tmp_path / "AGENTS.md").read_bytes() == original


def test_a_named_file_is_protected_through_a_hard_link(tmp_path, monkeypatch):
    original = b"- The public API allows 100 requests per minute.\n"
    (tmp_path / "CLAUDE.md").write_bytes(original)
    hard_link(tmp_path / "copy.md", tmp_path / "CLAUDE.md")
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["audit", "CLAUDE.md", "--output", "copy.md"])
    assert result.exit_code == 2 and (tmp_path / "CLAUDE.md").read_bytes() == original


def test_output_never_goes_through_a_memory_link_the_search_passed_over(agent_tree, make_symlink, tmp_path):
    (agent_tree / "MEMORY.md").unlink()
    write(tmp_path, {"outside.md": "- A file outside the project.\n"})
    make_symlink(agent_tree / "MEMORY.md", tmp_path / "outside.md")
    assert audit_here("--output", "MEMORY.md").exit_code == 2
    assert (tmp_path / "outside.md").read_bytes() == b"- A file outside the project.\n"
    (agent_tree / "MEMORY.md").unlink()
    make_symlink(agent_tree / "MEMORY.md", agent_tree / "missing.md")  # a broken one
    assert audit_here("--output", "MEMORY.md").exit_code == 2
    assert not (agent_tree / "missing.md").exists()


def test_output_never_goes_through_a_linked_note_parsing_skipped(tmp_path, monkeypatch, make_symlink):
    write(tmp_path, {"outside.md": "- A note outside the project.\n",
                     "project/MEMORY.md": "- [Notes](notes.md)\n- The billing service runs in Frankfurt.\n"})
    make_symlink(tmp_path / "project" / "notes.md", tmp_path / "outside.md")
    monkeypatch.chdir(tmp_path / "project")
    assert audit_here("--output", "notes.md").exit_code == 2
    assert (tmp_path / "outside.md").read_bytes() == b"- A note outside the project.\n"


def test_output_elsewhere_still_works_with_a_skipped_file_present(skipped_instructions):
    result = audit_here("--output", "report.md")
    assert result.exit_code == 0, result.output
    assert (skipped_instructions / "report.md").read_bytes().startswith(b"# MemFalsify audit report")


@pytest.fixture
def cycle_raises(monkeypatch):
    """``Path.resolve`` on a cyclic link as Python 3.10 has it; on later versions it does not raise."""
    real = Path.resolve

    def resolve(self, strict=False):
        if self.name == "loop":
            raise RuntimeError("Symlink loop from 'loop'")
        return real(self, strict)

    monkeypatch.setattr(Path, "resolve", resolve)


@pytest.mark.parametrize("named", [(), ("MEMORY.md",)])
@pytest.mark.parametrize("simulate", [False, True])
def test_a_cyclic_link_as_output_is_a_clean_error(agent_tree, make_symlink, request, named, simulate):
    make_symlink(agent_tree / "loop", "loop")
    if simulate:
        request.getfixturevalue("cycle_raises")
    result = audit_here(*named, "--output", "loop")
    assert result.exit_code == 2 and "cannot write the report" in result.stderr, repr(result.exception)


def test_a_named_output_that_cannot_be_written_is_a_clean_error(agent_tree):
    result = audit_here("MEMORY.md", "--output", "no/such/directory/report.md")
    assert result.exit_code == 2 and "cannot write the report" in result.stderr, repr(result.exception)


def test_an_output_that_cannot_be_written_is_a_clean_error(agent_tree):
    result = audit_here("--output", "no/such/directory/report.md")
    assert result.exit_code == 2 and "cannot write the report" in result.stderr


def test_a_review_file_behind_a_link_out_of_the_root_is_not_read(tmp_path, make_symlink):
    write(tmp_path, {"outside/reviews.jsonl": '{"memory_id": "m", "relation": "supports", "path": "p", "quote": "q", '
                                              '"reviewer": "r"}\n', "root/plain.md": "Some text.\n"})
    make_symlink(tmp_path / "root" / "reviews.jsonl", tmp_path / "outside" / "reviews.jsonl")
    assert ownfiles.own_files(tmp_path / "root") == []


def test_memory_files_are_only_read(agent_tree):
    before = snapshot(agent_tree)
    assert audit_here().exit_code == 0
    assert snapshot(agent_tree) == before


# --- how several files read ----------------------------------------------------------------------------------

def test_markdown_has_totals_a_file_table_and_one_section_per_file(agent_tree):
    text = audit_here().stdout
    assert text.startswith("# MemFalsify audit report\n")
    assert text.count("# MemFalsify audit report") == 1
    assert text.count("## Limitations") == 1
    positions = [text.index(f"## `{name}`") for name in AGENT_FILES]
    assert positions == sorted(positions)
    assert "| File | Memories | Need attention |" in text
    assert "| `AGENTS.md` | 2 | 1 |" in text
    assert "- Also read: `AGENTS.md`" in text
    limitations = text[text.index("## Limitations"):].splitlines()
    assert len(limitations) == len(set(limitations))


def test_a_file_with_nothing_of_its_own_says_so(tmp_path, monkeypatch):
    write(tmp_path, {"AGENTS.md": "- The nightly export stops at midnight on Sundays.\n", "CLAUDE.md": "@AGENTS.md\n"})
    monkeypatch.chdir(tmp_path)
    assert "No claims of its own" in audit_here().stdout
    assert "No claims of its own" in audit_here("--format", "html").stdout


@pytest.mark.parametrize("fmt", ["markdown", "html", "json"])
def test_a_place_not_searched_is_in_the_report_even_with_one_file(tmp_path, monkeypatch, make_symlink, fmt):
    write(tmp_path, {"AGENTS.md": "- The nightly export stops at midnight on Sundays.\n"})
    make_symlink(tmp_path / "MEMORY.md", tmp_path / "missing.md")
    monkeypatch.chdir(tmp_path)
    result = audit_here("--format", fmt)
    assert result.exit_code == 0
    assert "1 place(s) not searched: see the report" in result.stderr
    if fmt == "json":
        assert [s["path"] for s in json.loads(result.stdout)["not_searched"]] == ["MEMORY.md"]
    else:
        assert "Not searched for memory files" in result.stdout and "MEMORY.md" in result.stdout


def test_json_is_one_object_with_a_stable_shape_at_any_count(agent_tree, tmp_path, monkeypatch):
    many = json.loads(audit_here("--format", "json").stdout)
    monkeypatch.chdir(tmp_path)
    write(tmp_path, {"solo/MEMORY.md": "- The nightly export stops at midnight on Sundays.\n"})
    monkeypatch.chdir(tmp_path / "solo")
    one = json.loads(audit_here("--format", "json").stdout)
    assert list(many) == list(one) == ["tool", "version", "mode", "files", "evidence_root", "excluded", "verifier",
                                       "as_of", "summary", "reports", "not_searched"]
    assert many["tool"] == "memfalsify" and many["mode"] == "read_only"
    assert sum(many["summary"].values()) == sum(len(r["findings"]) for r in many["reports"])
    assert [r["memory_file"] for r in many["reports"]] == many["files"]


def test_html_is_one_self_contained_page_with_unique_ids(agent_tree):
    page = audit_here("--format", "html").stdout
    assert page.count("<html") == 1 and page.isascii()
    ids = re.findall(r'\bid="([^"]+)"', page)
    assert ids and len(ids) == len(set(ids))
    for href in re.findall(r'href="#([^"]+)"', page):
        assert href in ids
    for name in AGENT_FILES:
        assert f"<code>{name}</code>" in page


def test_hostile_memory_text_stays_inert_in_every_format(tmp_path, monkeypatch):
    evil = "The\x1b[31m API\u202e limit <script>x</script> | is `100` requests\n- [click](http://evil.example) per minute."
    write(tmp_path, {"AGENTS.md": f"- {evil}\n", "CLAUDE.md": f"- Also {evil}\n",
                     "docs/api.md": "The API limit is 60 requests per minute.\n"})
    monkeypatch.chdir(tmp_path)
    for fmt in ("markdown", "json", "html"):
        result = audit_here("--format", fmt)
        assert result.exit_code == 0, result.output
        for stream in (result.stdout, result.stderr):
            assert "\x1b" not in stream and "\u202e" not in stream
        assert "<script>" not in result.stdout.replace("&lt;script&gt;", "") or fmt != "html"


# --- memfalsify init -----------------------------------------------------------------------------------------

OUTCOMES = {"candidate_counterevidence": 1, "insufficient_evidence": 1, "candidate_support": 1}
TEMPLATES = REPO / "packages" / "memfalsify" / "src" / "memfalsify" / "templates" / "demo"


@pytest.fixture
def in_tmp(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.mark.parametrize("lang", demo.LANGUAGES)
def test_the_demo_shows_the_three_outcomes_with_both_quotes(in_tmp, monkeypatch, lang):
    created = runner.invoke(app, ["init", "demo", "--lang", lang])
    assert created.exit_code == 0, created.output
    monkeypatch.chdir(in_tmp / "demo")
    result = audit_set()
    assert result.files == ["MEMORY.md"] and result.summary == OUTCOMES
    findings = {f.status: f for f in result.reports[0].findings}
    assert set(findings) == set(OUTCOMES)
    assert all(not f.issues for f in findings.values())  # nothing distracts from the three outcomes
    claim, quote = demo._CONTRADICTED[lang]
    contradicted = findings["candidate_counterevidence"]
    assert contradicted.text == claim and contradicted.evidence[0].span.quote == quote
    assert contradicted.evidence[0].span.path == "evidence/api.md"
    assert findings["candidate_support"].evidence[0].span.path == "evidence/runbook.md"
    assert not findings["insufficient_evidence"].evidence
    assert result.reports[0].skipped_evidence == []
    text = audit_here().stdout
    assert claim in text and quote in text and "POSSIBLE COUNTEREVIDENCE" in text and "NO EVIDENCE" in text


@pytest.mark.parametrize("lang", demo.LANGUAGES)
def test_the_printed_review_line_confirms_the_contradiction(in_tmp, monkeypatch, lang):
    printed = runner.invoke(app, ["init", "demo", "--lang", lang]).stdout
    line = demo.review_line(lang)
    assert line in printed
    monkeypatch.chdir(in_tmp / "demo")
    with open("reviews.jsonl", "ab") as handle:
        handle.write((line + "\n").encode("utf-8"))
    result = audit_set("--reviews", "reviews.jsonl")
    assert result.summary == {"challenged": 1, "insufficient_evidence": 1, "candidate_support": 1}
    assert audit_here("--reviews", "reviews.jsonl", "--strict").exit_code == 1


@pytest.mark.parametrize("fmt", ["markdown", "json", "html"])
def test_chinese_text_and_names_reach_a_cp1252_console_as_utf8(tmp_path, monkeypatch, windows_console, fmt):
    monkeypatch.chdir(tmp_path)
    assert runner.invoke(app, ["init", "项目", "--lang", "zh"]).exit_code == 0
    assert windows_console[0].getvalue().count("项目".encode("utf-8")) >= 2
    monkeypatch.chdir(tmp_path / "项目")
    write(tmp_path / "项目", {"子目录/AGENTS.md": "- 夜间导出在周日午夜停止。\n"})
    before = len(windows_console[0].getvalue())
    result = audit_here("--format", fmt)
    assert result.exit_code == 0, result.output
    assert windows_console[0].getvalue()[before:] == result.stdout_bytes
    assert b"\r" not in result.stdout_bytes
    assert "子目录/AGENTS.md".encode("utf-8") in windows_console[1].getvalue()


def test_init_prints_the_exact_next_commands(in_tmp):
    printed = runner.invoke(app, ["init", "new demo"]).stdout
    assert f"  cd {shlex.quote('new demo')}\n  memfalsify audit\n" in printed
    assert "  memfalsify audit --reviews reviews.jsonl\n" in printed
    for name in demo.FILES:
        assert name in printed


def test_init_prints_a_cd_that_works_for_a_leading_dash(in_tmp):
    result = runner.invoke(app, ["init", "--", "-weird"])
    assert result.exit_code == 0, result.output
    assert "  cd ./-weird\n" in result.stdout
    assert (in_tmp / "-weird" / "MEMORY.md").is_file()


def test_init_promises_the_three_outcomes_only_in_a_directory_of_its_own(in_tmp, monkeypatch):
    fresh = runner.invoke(app, ["init", "new"]).stdout
    assert "You should see one claim" in fresh
    (in_tmp / "empty").mkdir()
    assert "You should see one claim" in runner.invoke(app, ["init", "empty"]).stdout
    write(in_tmp, {"project/CLAUDE.md": "- Deploys use the staging cluster.\n- The billing service runs in Dublin.\n"})
    monkeypatch.chdir(in_tmp / "project")
    printed = runner.invoke(app, ["init", "."]).stdout
    assert "You should see one claim" not in printed
    assert "  memfalsify audit MEMORY.md --evidence evidence\n" in printed
    assert audit_set().files == ["CLAUDE.md", "MEMORY.md"]
    alone = AuditReport.model_validate_json(audit_here("MEMORY.md", "--evidence", "evidence", "--format", "json").stdout)
    assert alone.summary == OUTCOMES


def test_init_defaults_to_a_new_directory(in_tmp):
    assert runner.invoke(app, ["init"]).exit_code == 0
    assert sorted(p.relative_to(in_tmp / demo.DEFAULT_DIRECTORY).as_posix()
                  for p in (in_tmp / demo.DEFAULT_DIRECTORY).rglob("*") if p.is_file()) == sorted(demo.FILES)


def test_init_copies_the_templates_as_exact_bytes(in_tmp):
    assert runner.invoke(app, ["init", "en"]).exit_code == 0
    for name in demo.FILES:
        assert (in_tmp / "en" / name).read_bytes() == (TEMPLATES / "en" / name).read_bytes()
    assert (in_tmp / "en" / "reviews.jsonl").read_bytes() == b""
    assert b"\r" not in (in_tmp / "en" / "MEMORY.md").read_bytes()


def test_init_in_the_current_directory_and_a_nested_new_one(in_tmp):
    printed = runner.invoke(app, ["init", "."]).stdout
    assert "  cd " not in printed and (in_tmp / "MEMORY.md").is_file()
    assert runner.invoke(app, ["init", "a/b/c"]).exit_code == 0
    assert (in_tmp / "a/b/c/evidence/api.md").is_file()


@pytest.mark.parametrize("taken", demo.FILES)
def test_init_refuses_to_overwrite_and_writes_nothing(in_tmp, taken):
    write(in_tmp, {f"demo/{taken}": "mine\n"})
    before = snapshot(in_tmp)
    result = runner.invoke(app, ["init", "demo"])
    assert result.exit_code == 2 and result.stdout == ""
    assert taken in result.stderr and "nothing was written" in result.stderr
    assert snapshot(in_tmp) == before


def test_init_refuses_a_link_at_a_target_even_a_broken_one(in_tmp, make_symlink):
    (in_tmp / "demo").mkdir()
    make_symlink(in_tmp / "demo" / "MEMORY.md", in_tmp / "elsewhere.md")
    result = runner.invoke(app, ["init", "demo"])
    assert result.exit_code == 2
    assert not (in_tmp / "elsewhere.md").exists()
    assert sorted(p.name for p in (in_tmp / "demo").iterdir()) == ["MEMORY.md"]


def test_init_refuses_a_linked_or_blocked_evidence_directory(in_tmp, make_symlink):
    write(in_tmp, {"elsewhere/keep.md": "mine\n", "file/evidence": "not a directory\n"})
    (in_tmp / "linked").mkdir()
    make_symlink(in_tmp / "linked" / "evidence", in_tmp / "elsewhere", directory=True)
    for directory in ("linked", "file"):
        result = runner.invoke(app, ["init", directory])
        assert result.exit_code == 2 and "evidence" in result.stderr
        assert not (in_tmp / directory / "MEMORY.md").exists()
    assert [p.name for p in (in_tmp / "elsewhere").iterdir()] == ["keep.md"]


def test_init_refuses_a_file_as_the_directory(in_tmp):
    write(in_tmp, {"demo": "a file\n"})
    assert runner.invoke(app, ["init", "demo"]).exit_code == 2
    assert (in_tmp / "demo").read_bytes() == b"a file\n"


def test_init_keeps_other_files_and_rejects_an_unknown_language(in_tmp):
    write(in_tmp, {"demo/keep.md": "mine\n"})
    assert runner.invoke(app, ["init", "demo"]).exit_code == 0
    assert (in_tmp / "demo/keep.md").read_bytes() == b"mine\n"
    assert runner.invoke(app, ["init", "other", "--lang", "fr"]).exit_code == 2
    assert not (in_tmp / "other").exists()
    with pytest.raises(ValueError, match="unknown demo language"):
        demo.create_demo(in_tmp / "other", "fr")


def test_init_undoes_what_it_created_when_a_write_fails(in_tmp, monkeypatch):
    write(in_tmp, {"demo/reviews.jsonl": "mine\n"})
    monkeypatch.setattr(demo, "_refusal", lambda directory: [])  # a file appearing after the check
    result = runner.invoke(app, ["init", "demo/"])
    assert result.exit_code == 2
    assert sorted(p.relative_to(in_tmp).as_posix() for p in in_tmp.rglob("*")) == ["demo", "demo/reviews.jsonl"]
    assert (in_tmp / "demo/reviews.jsonl").read_bytes() == b"mine\n"


def test_templates_are_declared_for_the_wheel():
    try:
        import tomllib
    except ModuleNotFoundError:
        tomllib = pytest.importorskip("tomli")
    config = tomllib.loads((REPO / "packages/memfalsify/pyproject.toml").read_text(encoding="utf-8"))
    patterns = config["tool"]["setuptools"]["package-data"]["memfalsify"]
    package = TEMPLATES.parent.parent
    declared = {path for pattern in patterns for path in package.glob(pattern) if path.is_file()}
    templates = {path for path in TEMPLATES.rglob("*") if path.is_file()}
    assert templates and templates <= declared
    for lang in demo.LANGUAGES:
        assert {p.relative_to(TEMPLATES / lang).as_posix() for p in templates if (TEMPLATES / lang) in p.parents} == set(
            demo.FILES)


def test_the_wheel_really_holds_the_templates(tmp_path):
    """Builds the wheel offline from a copy, when setuptools is installed; the CI test jobs install it."""
    setuptools = pytest.importorskip("setuptools")
    if tuple(int(part) for part in setuptools.__version__.split(".")[:1]) < (77,):
        pytest.skip("packages/memfalsify needs setuptools>=77")
    source = shutil.copytree(REPO / "packages" / "memfalsify", tmp_path / "memfalsify",
                             ignore=shutil.ignore_patterns("build", "*.egg-info", "__pycache__"))
    out = tmp_path / "wheel"
    out.mkdir()
    subprocess.run([sys.executable, "-c",
                    "import sys; from setuptools import build_meta; print(build_meta.build_wheel(sys.argv[1]))", str(out)],
                   cwd=source, check=True, capture_output=True)
    wheel = next(out.glob("*.whl"))
    with zipfile.ZipFile(wheel) as archive:
        names = set(archive.namelist())
    for lang in demo.LANGUAGES:
        for name in demo.FILES:
            assert f"memfalsify/templates/demo/{lang}/{name}" in names


# --- memfalsify evidence from-git ----------------------------------------------------------------------------

def from_git(*args: str):
    return runner.invoke(app, ["evidence", "from-git", *args])


@pytest.fixture
def history(git_repo, monkeypatch):
    monkeypatch.chdir(git_repo.root)
    return git_repo


def commit_bytes(repo, message: bytes) -> str:
    """A commit whose message is exactly these bytes, whatever git's cleanup would do to them."""
    path = repo.root.parent / "message.bin"
    path.write_bytes(message)
    repo.git("commit", "-q", "--allow-empty", "--cleanup=verbatim", "-F", str(path))
    return repo.git("rev-parse", "HEAD").strip()


@needs_git
def test_one_file_per_commit_named_by_short_hash(history, tmp_path):
    first = history.commit("Add the export job")
    second = history.commit("Lower the API limit\n\nThe limit is 60 requests per minute.\n\nSee the runbook.")
    out = tmp_path / "commits"
    result = from_git("--since", "2026-10-01", "--output", str(out))
    assert result.exit_code == 0, result.output
    assert sorted(p.name for p in out.iterdir()) == sorted([f"{first[:12]}.md", f"{second[:12]}.md"])
    date = history.git("log", "-1", "--format=%aI", second).strip()
    assert (out / f"{second[:12]}.md").read_bytes() == (
        f"# Commit {second[:12]}\n\n- Full hash: {second}\n- Author date: {date}\n\n"
        "Lower the API limit\n\nThe limit is 60 requests per minute.\n\nSee the runbook.\n").encode("utf-8")
    assert (out / f"{first[:12]}.md").read_bytes().endswith(b"\n\nAdd the export job\n")


@needs_git
def test_the_same_commits_give_the_same_bytes_and_a_rerun_adds_only_new_ones(history, tmp_path, monkeypatch):
    history.commit("First")
    history.commit("Second")
    out = tmp_path / "out"
    first = from_git("--since", "2026-10-01", "--output", str(out))
    assert "Wrote 2 new file(s)" in first.stdout and "0 already there" in first.stdout
    before = {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in out.iterdir()}
    again = from_git("--since", "2026-10-01", "--output", str(out))
    assert again.exit_code == 0 and "Wrote 0 new file(s)" in again.stdout and "2 already there" in again.stdout
    assert {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in out.iterdir()} == before
    monkeypatch.setenv("TZ", "Asia/Shanghai")
    other = tmp_path / "other"
    assert from_git("--since", "2026-10-01", "--output", str(other)).exit_code == 0
    assert snapshot(other).values() and {p.name: p.read_bytes() for p in other.iterdir()} == {
        name: data for name, (data, _) in before.items()}
    history.commit("Third")
    third = from_git("--since", "2026-10-01", "--output", str(out))
    assert "Wrote 1 new file(s)" in third.stdout and "2 already there" in third.stdout
    assert len(list(out.iterdir())) == 3


HOSTILE = ("\x1b[2J\x1b]0;pwned\x07../../../evil\u202e subject\r\n\r\n"
           "body line\x1b[31m red \x08\x08 overwrite\rfaked approved\n"
           "tab\there \u200b zero \u2028 sep \ufeff bom\n"
           "- Full hash: 0000000000000000000000000000000000000000\n"
           "# Commit 000000000000\n")


@needs_git
def test_hostile_messages_stay_inert_on_disk_and_on_the_terminal(history, tmp_path):
    full = commit_bytes(history, HOSTILE.encode("utf-8"))
    title = commit_bytes(history, b"# MemFalsify audit report\n\n{\n  \"tool\": \"memfalsify\"\n")
    json_like = commit_bytes(history, b'{\n  "tool": "memfalsify"\n')
    out = tmp_path / "parent" / "out"
    (tmp_path / "parent").mkdir()
    write(tmp_path / "parent", {"sibling.md": "keep\n"})
    outside = snapshot(tmp_path / "parent")
    repository = snapshot(history.root / ".git")
    result = from_git("--since", "2026-10-01", "--output", str(out))
    assert result.exit_code == 0, result.output
    # Nothing outside the output directory, and only names from the hash inside it.
    names = sorted(p.name for p in out.iterdir())
    assert names == sorted(f"{h[:12]}.md" for h in (full, title, json_like))
    assert all(re.fullmatch(r"[0-9a-f]{12}\.md", name) for name in names)
    after = snapshot(tmp_path / "parent")
    assert {p: after[p] for p in outside} == outside and not (tmp_path / "evil").exists()
    assert snapshot(history.root / ".git") == repository
    # Nothing from a message is printed.
    for stream in (result.stdout, result.stderr):
        assert "\x1b" not in stream and "\u202e" not in stream and "pwned" not in stream and "evil" not in stream
    # On disk: valid UTF-8, the header first, every control character a visible escape.
    data = (out / f"{full[:12]}.md").read_bytes().decode("utf-8")
    assert data.startswith(f"# Commit {full[:12]}\n\n- Full hash: {full}\n")
    assert one_line(data.replace("\n", "").replace("\t", "")) == data.replace("\n", "").replace("\t", "")
    for raw in ("\x1b", "\x07", "\x08", "\r", "\u202e", "\u200b", "\u2028", "\ufeff"):
        assert raw not in data
    for escape in ("\\u001b", "\\u0007", "\\u0008", "\\u202e", "\\u200b", "\\u2028", "\\ufeff"):
        assert escape in data
    assert "\ttab" not in data and "tab\there" in data  # a tab stays
    assert "overwrite\nfaked approved" in data  # a lone CR becomes a line break: both halves are visible
    # A message cannot make the file look like a report, so it stays evidence.
    passages, skipped = scan(out)
    assert skipped == []
    assert {p.span.path for p in passages} == set(names)
    assert all(not is_report((out / name).read_bytes().decode("utf-8")) for name in names)


@needs_git
def test_a_stored_quote_is_exact_and_its_hash_matches_the_file(history, tmp_path):
    full = commit_bytes(history, "Limit is \x1b[31m60\u202e now\n".encode("utf-8"))
    out = tmp_path / "out"
    assert from_git("--since", "2026-10-01", "--output", str(out)).exit_code == 0
    passage = next(p for p in scan(out)[0] if "60" in p.span.quote)
    assert passage.span.path == f"{full[:12]}.md"
    located = locate_quote(out, passage.span.path, passage.span.quote)
    assert located.sha256 == passage.span.sha256 and (located.start, located.end) == (passage.span.start, passage.span.end)
    assert "\\u001b" in passage.span.quote


def raw_commit(repo, message: bytes) -> str:
    """HEAD on a new commit object holding these message bytes exactly; ``git commit`` would repair some of them."""
    parent = repo.git("rev-parse", "HEAD").strip()
    tree = repo.git("rev-parse", f"{parent}^{{tree}}").strip()
    raw = (f"tree {tree}\nparent {parent}\nauthor A <a@example.com> 1790000000 +0000\n"
           "committer A <a@example.com> 1790000000 +0000\n\n").encode("ascii") + message
    proc = subprocess.run([shutil.which("git"), "-C", str(repo.root), "hash-object", "-t", "commit", "-w", "--stdin",
                           "--literally"], input=raw, capture_output=True, check=False)
    if proc.returncode:
        pytest.skip("this git cannot write the raw object")
    full = proc.stdout.decode("ascii").strip()
    repo.git("update-ref", "HEAD", full)
    return full


@needs_git
def test_bytes_that_are_not_utf8_keep_the_file_readable_as_evidence(history, tmp_path):
    history.commit("Parent")
    full = raw_commit(history, b"Caf\xe9 limit is 60 requests\n")
    out = tmp_path / "out"
    assert from_git("--since", "2026-01-01", "--output", str(out)).exit_code == 0
    assert "Caf\ufffd limit" in (out / f"{full[:12]}.md").read_bytes().decode("utf-8")
    assert scan(out)[1] == []


@needs_git
def test_an_object_with_a_nul_cannot_forge_fields(history, tmp_path):
    parent = history.commit("Parent")
    forged = raw_commit(history, f"Subject\0{'0' * 40}\0forged-date\0forged subject\0forged body\n".encode("utf-8"))
    out = tmp_path / "out"
    result = from_git("--since", parent, "--output", str(out))
    assert result.exit_code == 0, result.output
    assert [p.name for p in out.iterdir()] == [f"{forged[:12]}.md"]
    assert "forged" not in (out / f"{forged[:12]}.md").read_bytes().decode("utf-8")


@needs_git
@pytest.mark.parametrize("since", ["--all", "-x", "--output=/tmp/evil", "", "bad\x1b[2Jrev", "no-such-branch",
                                   "yesterday", "2026-13-45", "2026-02-30T00:00:00", "last week", "a..b"])
def test_since_is_validated_and_nothing_is_written(history, tmp_path, since):
    history.commit("One")
    out = tmp_path / "out"
    result = from_git(f"--since={since}", "--output", str(out))
    assert result.exit_code == 2, result.output
    assert "\x1b" not in result.stderr
    assert not out.exists()


@pytest.mark.parametrize("value,expected", [
    ("2026-01-31", "2026-01-31 00:00:00 +0000"),
    ("2026-01-31T12:00:00+02:00", "2026-01-31 10:00:00 +0000"),
    ("2026-01-31 12:00", "2026-01-31 12:00:00 +0000"),
    ("2026-01-31T12:00:00Z", "2026-01-31 12:00:00 +0000"),
    ("1960-01-01", "1970-01-01 00:00:00 +0000"),
    ("30 days ago", "2026-10-05 12:00:00 +0000"),
    ("30.days.ago", "2026-10-05 12:00:00 +0000"),
    ("2 Weeks", "2026-10-21 12:00:00 +0000"),
    ("36 hours", "2026-11-03 00:00:00 +0000"),
    ("1 month ago", "2026-10-04 12:00:00 +0000"),
    ("13 months ago", "2025-10-04 12:00:00 +0000"),
    ("3.years.ago", "2023-11-04 12:00:00 +0000"),
    ("100000 days ago", "1970-01-01 00:00:00 +0000"),  # git would misread this without a word
    ("999999 years ago", "1970-01-01 00:00:00 +0000"),
    ("999999 weeks ago", "1970-01-01 00:00:00 +0000"),
    ("v1.0", None),
    ("HEAD~2", None),
    ("20260131", None),
    ("main", None),
])
def test_date_argument_spells_dates_out_and_leaves_the_rest(value, expected):
    assert date_argument(value, now=datetime(2026, 11, 4, 12, tzinfo=timezone.utc)) == expected


def test_a_month_back_lands_on_a_real_day():
    assert date_argument("1 month ago", now=datetime(2026, 3, 31, tzinfo=timezone.utc)) == "2026-02-28 00:00:00 +0000"
    assert date_argument("2 months", now=datetime(2024, 4, 30, tzinfo=timezone.utc)) == "2024-02-29 00:00:00 +0000"


@needs_git
def test_since_as_a_date_is_midnight_utc_on_the_committer_date(history, tmp_path):
    history.commit("Old")
    env = {"GIT_COMMITTER_DATE": "2026-10-03T00:00:00+00:00", "GIT_AUTHOR_DATE": "2026-10-03T00:00:00+00:00"}
    subprocess.run([shutil.which("git"), "-C", str(history.root), "-c", "commit.gpgsign=false", "commit", "-q",
                    "--allow-empty", "-m", "On the day"], env={**os.environ, **env}, check=True)
    day = tmp_path / "day"
    result = from_git("--since", "2026-10-03", "--output", str(day))
    assert result.exit_code == 0 and "2026-10-03 00:00:00 +0000" in result.stderr
    assert len(list(day.iterdir())) == 1  # the commit at exactly midnight counts, the older one does not
    later = tmp_path / "later"
    assert from_git("--since", "2026-10-03T00:00:01Z", "--output", str(later)).exit_code == 0
    assert list(later.iterdir()) == []
    everything = tmp_path / "everything"
    assert from_git("--since", "1960-01-01", "--output", str(everything)).exit_code == 0
    assert len(list(everything.iterdir())) == 2
    relative = tmp_path / "relative"
    assert from_git("--since", "100000 days ago", "--output", str(relative)).exit_code == 0
    assert len(list(relative.iterdir())) == 2


def commit_on(repo, message: str, when: str) -> str:
    """A commit with this author and committer date, whatever the order of the history."""
    env = {**os.environ, "GIT_COMMITTER_DATE": when, "GIT_AUTHOR_DATE": when}
    subprocess.run([shutil.which("git"), "-C", str(repo.root), "-c", "commit.gpgsign=false", "commit", "-q",
                    "--allow-empty", "-m", message], env=env, check=True)
    return repo.git("rev-parse", "HEAD").strip()


@needs_git
def test_a_commit_with_an_old_date_hides_nothing_behind_it(history, tmp_path):
    behind = commit_on(history, "Behind the old one", "2026-11-03T00:00:00+00:00")
    old = commit_on(history, "Dated long ago", "2026-01-01T00:00:00+00:00")
    head = commit_on(history, "Newest", "2026-11-04T00:00:00+00:00")
    out = tmp_path / "out"
    result = from_git("--since", "2026-11-01", "--output", str(out))
    assert result.exit_code == 0, result.output
    assert sorted(p.name for p in out.iterdir()) == sorted([f"{behind[:12]}.md", f"{head[:12]}.md"])
    assert not (out / f"{old[:12]}.md").exists()
    cut = tmp_path / "cut"
    limited = from_git("--since", "2026-11-01", "--output", str(cut), "--max-commits", "1")
    assert [p.name for p in cut.iterdir()] == [f"{head[:12]}.md"] and "stopped at --max-commits 1" in limited.stderr


@needs_git
def test_an_old_dated_head_hides_nothing_either(history, tmp_path):
    behind = commit_on(history, "Behind the old one", "2026-11-03T00:00:00+00:00")
    commit_on(history, "Dated long ago", "2026-01-01T00:00:00+00:00")
    out = tmp_path / "out"
    assert from_git("--since", "2026-11-01", "--output", str(out)).exit_code == 0
    assert [p.name for p in out.iterdir()] == [f"{behind[:12]}.md"]


@needs_git
def test_the_date_window_keeps_git_order_across_a_merge(history, tmp_path):
    base = commit_on(history, "Base", "2026-01-01T00:00:00+00:00")
    side = commit_on(history, "Side", "2026-11-02T00:00:00+00:00")
    history.git("checkout", "-q", "-b", "other", base)
    other = commit_on(history, "Other", "2026-11-03T00:00:00+00:00")
    stamp = "2026-11-04T00:00:00+00:00"
    subprocess.run([shutil.which("git"), "-C", str(history.root), "-c", "commit.gpgsign=false", "merge", "-q",
                    "--no-edit", "--no-ff", side], env={**os.environ, "GIT_COMMITTER_DATE": stamp,
                                                        "GIT_AUTHOR_DATE": stamp}, check=True)
    merged = history.git("rev-parse", "HEAD").strip()
    out = tmp_path / "out"
    assert from_git("--since", "2026-11-01", "--output", str(out), "--max-commits", "2").exit_code == 0
    assert sorted(p.name for p in out.iterdir()) == sorted([f"{merged[:12]}.md", f"{other[:12]}.md"])


@needs_git
@pytest.mark.parametrize("since", ["2100-01-01", "2200-06-01", "9999-12-31", "9999-12-31T00:00:00Z"])
def test_a_date_far_in_the_future_selects_nothing(history, tmp_path, since):
    history.commit("One")
    out = tmp_path / "out"
    result = from_git("--since", since, "--output", str(out))
    assert result.exit_code == 0 and "Wrote 0 new file(s)" in result.stdout, result.output
    assert list(out.iterdir()) == [] and since[:4] in result.stderr


@needs_git
def test_a_date_before_1970_selects_a_commit_from_the_first_day(history, tmp_path):
    early = commit_on(history, "At the start of time", "@1 +0000")
    out = tmp_path / "out"
    assert from_git("--since", "1960-01-01", "--output", str(out)).exit_code == 0
    assert [p.name for p in out.iterdir()] == [f"{early[:12]}.md"]


@pytest.mark.parametrize("since", ["9999-12-31T23:59:59-23:00", "0000-01-01"])
def test_a_date_out_of_range_is_refused(tmp_path, since):
    with pytest.raises(ValueError, match="not a valid date"):
        date_argument(since)


@needs_git
def test_since_as_a_revision_means_later_commits_up_to_head(history, tmp_path):
    first = history.commit("One")
    history.git("tag", "v1")
    second = history.commit("Two")
    third = history.commit("Three")
    for since in ("v1", first, first[:10]):
        out = tmp_path / since
        result = from_git("--since", since, "--output", str(out))
        assert result.exit_code == 0, result.output
        assert sorted(p.name for p in out.iterdir()) == sorted([f"{second[:12]}.md", f"{third[:12]}.md"])
        assert f"commit {first[:12]}" in result.stderr


@needs_git
def test_max_commits_keeps_the_newest_and_says_it_stopped(history, tmp_path):
    history.commit("One")
    second = history.commit("Two")
    third = history.commit("Three")
    out = tmp_path / "out"
    result = from_git("--since", "2026-10-01", "--output", str(out), "--max-commits", "2")
    assert sorted(p.name for p in out.iterdir()) == sorted([f"{second[:12]}.md", f"{third[:12]}.md"])
    assert "stopped at --max-commits 2" in result.stderr
    exact = tmp_path / "exact"
    assert "stopped" not in from_git("--since", "2026-10-01", "--output", str(exact), "--max-commits", "3").stderr
    assert from_git("--since", "2026-10-01", "--output", str(out), "--max-commits", "0").exit_code == 2


@needs_git
def test_repo_option_reads_another_directory(history, tmp_path, monkeypatch):
    full = history.commit("Elsewhere")
    monkeypatch.chdir(tmp_path)
    out = tmp_path / "out"
    assert from_git("--since", "2026-10-01", "--output", str(out), "--repo", str(history.root)).exit_code == 0
    assert (out / f"{full[:12]}.md").is_file()


@needs_git
def test_output_inside_the_git_directory_is_refused(history, tmp_path):
    history.commit("One")
    before = snapshot(history.root / ".git")
    for target in (".git", ".git/evidence", ".git/objects/pack/x"):
        result = from_git("--since", "2026-10-01", "--output", target)
        assert result.exit_code == 2 and "inside the git directory" in result.stderr
    assert snapshot(history.root / ".git") == before


@needs_git
def test_a_link_to_the_git_directory_is_refused_too(history, make_symlink):
    history.commit("One")
    make_symlink(history.root / "sneaky", history.root / ".git", directory=True)
    before = snapshot(history.root / ".git")
    assert from_git("--since", "2026-10-01", "--output", "sneaky/out").exit_code == 2
    assert snapshot(history.root / ".git") == before


@needs_git
def test_a_file_with_other_content_stops_the_run_with_nothing_written(history, tmp_path):
    first = history.commit("One")
    second = history.commit("Two")
    out = tmp_path / "out"
    write(out, {f"{second[:12]}.md": "mine\n"})
    result = from_git("--since", "2026-10-01", "--output", str(out))
    assert result.exit_code == 2 and "different content" in result.stderr and result.stdout == ""
    assert sorted(p.name for p in out.iterdir()) == [f"{second[:12]}.md"]
    assert (out / f"{second[:12]}.md").read_bytes() == b"mine\n"
    assert not (out / f"{first[:12]}.md").exists()


@needs_git
def test_a_case_variant_of_the_git_directory_is_refused(history):
    history.commit("One")
    if not (history.root / ".GIT").exists():
        pytest.skip("this filesystem is case-sensitive: .GIT is another directory")
    before = snapshot(history.root / ".git")
    result = from_git("--since", "2026-10-01", "--output", ".GIT/evil")
    assert result.exit_code == 2 and "inside the git directory" in result.stderr
    assert snapshot(history.root / ".git") == before


@needs_git
@pytest.mark.parametrize("simulate", [False, True])
def test_a_cyclic_link_as_output_directory_is_a_clean_error(history, make_symlink, request, simulate):
    history.commit("One")
    make_symlink(history.root / "loop", "loop")
    if simulate:
        request.getfixturevalue("cycle_raises")
    result = from_git("--since", "2026-10-01", "--output", "loop")
    assert result.exit_code == 2, repr(result.exception)


@needs_git
def test_a_link_at_a_target_is_not_followed(history, tmp_path, make_symlink):
    full = history.commit("One")
    out = tmp_path / "out"
    out.mkdir()
    victim = tmp_path / "victim.txt"
    victim.write_bytes(b"precious\n")
    make_symlink(out / f"{full[:12]}.md", victim)
    assert from_git("--since", "2026-10-01", "--output", str(out)).exit_code == 2
    assert victim.read_bytes() == b"precious\n"
    os.unlink(out / f"{full[:12]}.md")
    dangling = tmp_path / "dangling.txt"
    make_symlink(out / f"{full[:12]}.md", dangling)
    assert from_git("--since", "2026-10-01", "--output", str(out)).exit_code == 2
    assert not dangling.exists()


@needs_git
@posix_only
def test_repository_config_and_hooks_never_run(history, tmp_path):
    full = history.commit("One")
    marker = tmp_path / "ran"
    script = tmp_path / "script.sh"
    write_text_exact(script, f"#!/bin/sh\ntouch {shlex.quote(str(marker))}\n")
    script.chmod(0o755)
    hooks = history.root / ".git" / "hooks"
    hooks.mkdir(exist_ok=True)
    for hook in ("post-checkout", "post-commit", "pre-commit", "post-index-change", "reference-transaction"):
        shutil.copy(script, hooks / hook)
    for key in ("core.fsmonitor", "core.pager", "core.sshCommand", "diff.external", "gpg.program", "core.editor"):
        history.git("config", "--local", key, str(script))
    history.git("config", "--local", "log.showSignature", "true")
    history.git("config", "--local", "format.pretty", "%an")
    history.git("config", "--local", "log.date", "unix")
    history.git("config", "--local", "core.abbrev", "20")
    before = snapshot(history.root / ".git")
    out = tmp_path / "out"
    result = from_git("--since", "2026-10-01", "--output", str(out))
    assert result.exit_code == 0, result.output
    assert not marker.exists()
    assert snapshot(history.root / ".git") == before
    assert [p.name for p in out.iterdir()] == [f"{full[:12]}.md"]
    assert history.git("status", "--porcelain") == ""


@needs_git
def test_replace_refs_do_not_change_what_a_hash_says(history, tmp_path):
    real = history.commit("Rate limit is 60")
    history.git("commit", "-q", "--allow-empty", "--amend", "-m", "Rate limit is 100")
    fake = history.git("rev-parse", "HEAD").strip()
    history.git("replace", real, fake)
    history.git("update-ref", "HEAD", real)
    out = tmp_path / "out"
    assert from_git("--since", "2026-10-01", "--output", str(out)).exit_code == 0
    assert b"Rate limit is 60" in (out / f"{real[:12]}.md").read_bytes()


@needs_git
def test_errors_outside_a_repository_and_in_an_empty_one(tmp_path, monkeypatch, git_repo):
    outside = tmp_path / "plain"
    outside.mkdir()
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path))
    result = from_git("--since", "2026-10-01", "--output", str(tmp_path / "o1"), "--repo", str(outside))
    assert result.exit_code == 2 and "not inside a git repository" in result.stderr
    empty = from_git("--since", "2026-10-01", "--output", str(tmp_path / "o2"), "--repo", str(git_repo.root))
    assert empty.exit_code == 2 and "no commits" in empty.stderr
    assert from_git("--since", "2026-10-01", "--output", str(tmp_path / "o3"), "--repo",
                    str(tmp_path / "missing")).exit_code == 2


@needs_git
def test_no_commits_in_the_window_still_makes_a_usable_directory(history, tmp_path):
    history.commit("One")
    out = tmp_path / "out"
    result = from_git("--since", "2030-01-01", "--output", str(out))
    assert result.exit_code == 0 and "Wrote 0 new file(s)" in result.stdout
    assert out.is_dir()


@needs_git
def test_the_files_work_with_evidence(history, tmp_path, monkeypatch):
    full = history.commit("Lower the limit\n\nThe public API allows 60 requests per minute.")
    out = tmp_path / "commits"
    printed = from_git("--since", "2026-10-01", "--output", str(out)).stdout
    assert "memfalsify audit --evidence " in printed
    project = tmp_path / "project"
    write(project, {"MEMORY.md": "- The public API allows 100 requests per minute.\n"})
    monkeypatch.chdir(project)
    run = audit_here("--evidence", str(out), "--format", "json")
    report = AuditSet.model_validate_json(run.stdout).reports[0]
    assert report.findings[0].status == "candidate_counterevidence"
    assert report.findings[0].evidence[0].span.path == f"{full[:12]}.md"
    assert audit_here("MEMORY.md", "--evidence", str(out), "--strict").exit_code == 1


@needs_git
def test_the_printed_command_works_from_where_the_memory_files_are(history, tmp_path, monkeypatch):
    history.commit("Lower the limit\n\nThe public API allows 60 requests per minute.")
    printed = from_git("--since", "2026-10-01", "--output", "commits").stdout
    hint = printed.strip().splitlines()[-1]
    assert "memory files" in hint
    command = shlex.split(hint.split(": ", 1)[1])
    assert command[:3] == ["memfalsify", "audit", "--evidence"]
    project = tmp_path / "project"
    write(project, {"MEMORY.md": "- The public API allows 100 requests per minute.\n"})
    monkeypatch.chdir(project)
    result = runner.invoke(app, command[1:])
    assert result.exit_code == 0, result.output
    assert "POSSIBLE COUNTEREVIDENCE" in result.stdout


@needs_git
def test_a_stored_escape_in_a_quote_goes_into_reviews_from_the_json_report(history, tmp_path, monkeypatch):
    """The quick start sends a person to ``--format json`` for this: the quote there is ready to paste."""
    full = commit_bytes(history, b"The public API allows \x1b[31m60 requests per minute.\n")
    out = tmp_path / "commits"
    assert from_git("--since", "2026-10-01", "--output", str(out)).exit_code == 0
    claim = "The public API allows 100 requests per minute."
    project = tmp_path / "project"
    write(project, {"MEMORY.md": f"- {claim}\n"})
    monkeypatch.chdir(project)
    shown = audit_here("--evidence", str(out), "--format", "json").stdout
    pasted = re.search(r'"quote": ("[^\n]*"),?\n', shown).group(1)
    assert "\\\\u001b" in pasted
    line = (f'{{"memory_id": "{claim_id(claim)}", "relation": "contradicts", "path": "{full[:12]}.md", '
            f'"quote": {pasted}, "reviewer": "you"}}\n')
    write(project, {"reviewed.jsonl": line})
    result = audit_set("--evidence", str(out), "--reviews", "reviewed.jsonl")
    assert result.summary == {"challenged": 1}


@posix_only
@pytest.mark.parametrize("script,message", [("echo 'not a timestamp line'", "cannot parse"),
                                            (f"echo '12 {'a' * 39}'", "cannot parse"),
                                            ("exit 3", "failed with exit code 3")])
def test_a_rev_list_that_misbehaves_is_a_clean_error(tmp_path, script, message):
    fake = tmp_path / "fake-git"
    write_text_exact(fake, f"#!/bin/sh\n{script}\n")
    fake.chmod(0o755)
    with pytest.raises(ValueError, match=message):
        gitevidence._commits_since(str(fake), tmp_path, "a" * 40, 0.0, 5)


def zero_file_index(monkeypatch) -> None:
    """Some filesystems report no file index. Only same_file sees this: a stat result rebuilt from its tuple loses
    the fields the standard library reads on Windows, so other callers keep the real one."""
    class NoFileIndex:
        def __getattr__(self, name):
            return getattr(os, name)

        def stat(self, path, *args, **kwargs):
            fields = list(os.stat(path, *args, **kwargs))
            fields[1] = fields[2] = 0  # st_ino, st_dev
            return os.stat_result(tuple(fields))

    # memfalsify.discover the attribute is the function; the module is in sys.modules
    monkeypatch.setattr(sys.modules["memfalsify.discover"], "os", NoFileIndex())


def test_a_zero_file_index_does_not_make_every_output_a_memory_file(tmp_path, monkeypatch):
    write(tmp_path, {"MEMORY.md": "- The nightly export stops at midnight on Sundays.\n", "report.md": "old\n"})
    monkeypatch.chdir(tmp_path)
    zero_file_index(monkeypatch)
    written = audit_here("--output", "report.md")
    assert written.exit_code == 0, written.output
    assert audit_here("--output", "MEMORY.md").exit_code == 2


@needs_git
def test_a_zero_file_index_does_not_put_every_output_in_the_git_directory(history, tmp_path, monkeypatch):
    history.commit("One")
    zero_file_index(monkeypatch)
    assert from_git("--since", "2026-10-01", "--output", str(tmp_path / "out")).exit_code == 0
    assert from_git("--since", "2026-10-01", "--output", ".git/evil").exit_code == 2


def test_parse_log_refuses_what_is_not_four_fields_of_a_hash():
    good = "a" * 40
    assert parse_log(f"{good}\0date\0subject\0body\n\0".encode()) == [
        gitevidence.Commit(good, "date", "subject", "body\n")]
    assert parse_log(b"") == []
    for bad in (b"not-a-hash\0d\0s\0b\0", f"{good}\0d\0s\0".encode(), f"{good}\0d\0s\0b\0extra\0".encode()):
        with pytest.raises(ValueError, match="cannot parse"):
            parse_log(bad)


def test_commits_sharing_a_short_hash_both_keep_a_file():
    one, two, other = "ab" * 6 + "0" * 28, "ab" * 6 + "1" * 28, "cd" * 20
    assert file_names([one, two, other]) == {one: f"{one}.md", two: f"{two}.md", other: f"{other[:12]}.md"}


def test_a_very_long_message_is_cut_with_a_note():
    commit = gitevidence.Commit("a" * 40, "2026-10-01T00:00:00+00:00", "Subject",
                                "x" * (gitevidence.MAX_MESSAGE_BYTES + 10))
    data = render(commit)
    assert len(data) < gitevidence.MAX_MESSAGE_BYTES + 400
    assert data.endswith(b"Read the commit.]\n")
    assert render(gitevidence.Commit("a" * 40, "", "", "")).endswith(b"- Author date: unknown\n\n(no subject)\n")


def test_the_evidence_group_is_ready_for_more_sources():
    assert "from-git" in runner.invoke(app, ["evidence", "--help"]).output
    assert "roadmap" in runner.invoke(app, ["evidence", "from-git", "--help"]).output
    assert runner.invoke(app, ["evidence", "from-github"]).exit_code == 2


# --- the quick start ---------------------------------------------------------------------------------------

def _blocks(path: Path) -> list[str]:
    return re.findall(r"^```bash\n(.*?)^```$", path.read_text(encoding="utf-8"), flags=re.M | re.S)


def test_the_quick_start_says_the_same_in_both_languages():
    english, chinese = _blocks(QUICKSTART), _blocks(QUICKSTART_ZH)
    assert english and english == chinese
    assert demo.review_line("en") in QUICKSTART.read_text(encoding="utf-8")
    assert claim_id(demo._CONTRADICTED["en"][0]) in QUICKSTART_ZH.read_text(encoding="utf-8")
    assert "quickstart.zh-CN.md" in QUICKSTART.read_text(encoding="utf-8")
    assert "quickstart.md" in QUICKSTART_ZH.read_text(encoding="utf-8")


@posix_only
def test_the_quick_start_blocks_marked_with_an_exit_code_run_in_order(tmp_path):
    bin_dir = Path(sys.executable).parent
    if not (bin_dir / "memfalsify").is_file():
        pytest.skip(f"no memfalsify next to {sys.executable}")
    blocks = [block for block in _blocks(QUICKSTART) if block.startswith("# exit ")]
    assert len(blocks) == 3
    config = tmp_path / "gitconfig"
    config.write_bytes(b"")
    env = {**os.environ, "PATH": str(bin_dir) + os.pathsep + os.environ.get("PATH", ""), "HOME": str(tmp_path),
           "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": str(config), "GIT_CEILING_DIRECTORIES": str(tmp_path),
           "PYTHONUTF8": "1"}
    for name in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"):
        env.pop(name, None)
    parts = []
    for block in blocks:
        expected = block.splitlines()[0].removeprefix("# exit ").strip()
        parts += [block, f'test "$?" = {expected} || {{ echo "block failed: {expected}"; exit 9; }}']
    script = tmp_path / "run.sh"
    write_text_exact(script, "\n".join(parts) + "\n")
    proc = subprocess.run(["bash", str(script)], cwd=tmp_path, env=env, capture_output=True, encoding="utf-8",
                          errors="replace", timeout=120, check=False)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "POSSIBLE COUNTEREVIDENCE" in proc.stdout and "NO EVIDENCE" in proc.stdout
    assert "CHALLENGED" in proc.stdout
    commits = list((tmp_path / "history" / "commits").iterdir())
    assert len(commits) == 1 and re.fullmatch(r"[0-9a-f]{12}\.md", commits[0].name)
    # The page's last step audits the demo against those commits, from the directory with the memory file.
    assert f"`{commits[0].name}:" in proc.stdout
    assert "no memory or instruction file" not in proc.stderr
    assert "not searched" not in proc.stderr  # nothing in the page's steps sets off a warning to explain


@pytest.mark.parametrize("page", [QUICKSTART, QUICKSTART_ZH])
def test_the_quick_start_says_only_what_holds(page):
    """Each of these sentences was false once: the page promised more than the commands do."""
    prose = page.read_text(encoding="utf-8")
    assert "audit --evidence commits" not in prose  # from history/ there is no memory file to audit
    assert "audit --evidence ../history/commits" in prose
    assert ".venv\\Scripts\\Activate.ps1" in prose  # a Windows venv has no bin/activate
    for old in ("You can copy it into `reviews.jsonl` as it is", "可以直接复制到 `reviews.jsonl`",
                "takes two commands", "只要两条命令", "Everything here runs offline", "这里的一切都离线运行"):
        assert old not in prose
    assert any("reviews.jsonl" in line and "\\uXXXX" in line and "--format json" in line for line in prose.splitlines())


def test_ci_builds_the_wheel_the_test_above_needs_setuptools_for():
    workflow = (REPO / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    installs = [line for line in workflow.splitlines() if "run: python -m pip install -e packages/memcore" in line]
    assert len(installs) == 2 and all('"setuptools>=77"' in line for line in installs)


class CountingVerifier:
    """A model verifier in shape: it reports requests, so reports show API usage per file."""
    name = "fake-model"

    def __init__(self):
        self.requests = 0

    def judge(self, claim: str, passage: str) -> Judgment:
        self.requests += 1
        return Judgment(verifier=self.name, relation="contradicts", rationale="the fake says so", confidence=0.9)

    def usage(self) -> list[VerifierStats]:
        return [VerifierStats(verifier=self.name, requests=self.requests, input_tokens=10 * self.requests)]


def test_model_verifiers_work_across_files_with_usage_per_file(tmp_path, monkeypatch):
    write(tmp_path, {"AGENTS.md": "- The public API allows 100 requests per minute.\n",
                     "sub/CLAUDE.md": "- The billing service runs in Frankfurt.\n",
                     "docs/api.md": "The public API allows 60 requests per minute.\n",
                     "docs/billing.md": "The billing service runs in Dublin.\n"})
    monkeypatch.chdir(tmp_path)
    verifier = CountingVerifier()
    result = audit_directory(Path("."), verifier=verifier, min_coverage=0.2, max_judged=8, max_concurrency=2)
    assert result.verifier == "fake-model" and verifier.requests >= 2
    stats = [report.verifier_stats[0].requests for report in result.reports]
    assert sum(stats) == verifier.requests and all(stats)
    markdown, page = set_to_markdown(result), set_to_html(result)
    assert markdown.count("API usage of `fake-model`") == 2
    assert page.count("fake-model</code>: ") == 2
    assert MODEL_NOTE in markdown


def test_auditdir_shares_one_evidence_scan(agent_tree, monkeypatch):
    calls = []
    real = auditdir.scan

    def counted(*args, **kwargs):
        calls.append(args)
        return real(*args, **kwargs)

    monkeypatch.setattr(auditdir, "scan", counted)
    assert audit_directory(Path(".")).files == AGENT_FILES
    assert len(calls) == 1
