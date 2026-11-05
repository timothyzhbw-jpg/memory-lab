"""``memfalsify audit --format brief``: only what needs a look, both quotes, safe on a terminal."""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from conftest import write_text_exact
from typer.testing import CliRunner

from memfalsify import claim_id
from memfalsify.brief import FOOTER, _clip
from memfalsify.cli import app
from memfalsify.corpus import BRIEF_HEADER, is_report


def _project(tmp_path: Path, memory: str, **evidence: str) -> Path:
    write_text_exact(tmp_path / "MEMORY.md", memory)
    for name, text in evidence.items():
        write_text_exact(tmp_path / f"{name}.md", text)
    return tmp_path


def _audit(*arguments: str):
    return CliRunner().invoke(app, ["audit", *arguments, "--format", "brief"])


def _lines(result) -> list[str]:
    return result.stdout.splitlines()


def test_a_flag_shows_both_quotes_and_nothing_else(tmp_path):
    root = _project(tmp_path, "- The public API allows 100 requests per minute.\n- Backups run nightly.\n"
                              "- The design team prefers short release notes.\n",
                    api="The public API allows 60 requests per minute.\n", runbook="Backups run nightly.\n")
    result = _audit(str(root / "MEMORY.md"), "--evidence", str(root))
    assert result.exit_code == 0
    lines = _lines(result)
    assert lines[0] == BRIEF_HEADER
    assert lines[1].endswith("MEMORY.md: 1 possible counterevidence, 1 no evidence, 1 possible support")
    assert "  memory  MEMORY.md:1" in lines and "          The public API allows 100 requests per minute." in lines
    assert "  source  api.md:1" in lines and "          The public API allows 60 requests per minute." in lines
    assert any(line.startswith("  why     ") for line in lines)
    assert "Backups run nightly." not in result.stdout and "design team" not in result.stdout
    assert "1 of 3 claim(s) need a look." in lines and FOOTER in result.stdout


def test_strict_exits_as_it_does_for_every_other_format(tmp_path):
    root = _project(tmp_path, "- The public API allows 100 requests per minute.\n",
                    api="The public API allows 60 requests per minute.\n")
    assert _audit(str(root / "MEMORY.md"), "--evidence", str(root), "--strict").exit_code == 1
    assert _audit(str(root / "MEMORY.md"), "--evidence", str(root)).exit_code == 0


def test_nothing_flagged_says_so_and_that_no_evidence_is_not_wrong(tmp_path):
    root = _project(tmp_path, "- Backups run nightly.\n- The design team prefers short release notes.\n",
                    runbook="Backups run nightly.\n")
    lines = _lines(_audit(str(root / "MEMORY.md"), "--evidence", str(root)))
    assert "Nothing flagged in 2 claim(s). 1 have no evidence, which means unchecked, not wrong." in lines


def test_a_confirmed_review_names_who_confirmed_it(tmp_path):
    root = _project(tmp_path, "- The public API allows 100 requests per minute.\n",
                    api="The public API allows 60 requests per minute.\n")
    memory_id = claim_id("The public API allows 100 requests per minute.")
    review = {"memory_id": memory_id, "relation": "contradicts", "path": "api.md",
              "quote": "The public API allows 60 requests per minute.", "reviewer": "pat"}
    reviews = tmp_path.parent / f"{tmp_path.name}-reviews.jsonl"
    write_text_exact(reviews, json.dumps(review) + "\n")
    result = _audit(str(root / "MEMORY.md"), "--evidence", str(root), "--reviews", str(reviews))
    assert "CHALLENGED · " + memory_id in _lines(result)
    assert "  why     contradicts, confirmed by pat" in _lines(result)


def test_a_path_that_exists_nowhere_is_a_flag_of_its_own(tmp_path):
    (tmp_path / "src").mkdir()
    write_text_exact(tmp_path / "src" / "here.py", "x = 1\n")
    root = _project(tmp_path, "- The parser lives in `src/gone.py` for now.\n")
    lines = _lines(_audit(str(root / "MEMORY.md"), "--evidence", str(root)))
    assert any(line.startswith("MISSING PATH · ") for line in lines)
    assert any(line.startswith("  path    'src/gone.py' is named here") for line in lines)
    assert "1 of 1 claim(s) need a look." in lines
    assert "naming a missing path" in lines[1]


def test_hostile_text_stays_on_its_line():
    assert _clip("a \x1b]0;title\x07 b\u202e\nc") == "a \\u001b]0;title\\u0007 b\\u202e c"


def test_hostile_text_in_a_flag_is_escaped(tmp_path):
    root = _project(tmp_path, "- The public API allows 100 \x07requests\u202e per minute.\n",
                    api="The public API allows 60 \x07requests\u202e per minute.\n")
    result = _audit(str(root / "MEMORY.md"), "--evidence", str(root))
    assert "\x07" not in result.stdout and "\u202e" not in result.stdout
    assert "          The public API allows 60 \\u0007requests\\u202e per minute." in _lines(result)


def test_a_long_quote_is_cut_and_a_path_is_not():
    assert len(_clip("word " * 100)) == 90 and _clip("word " * 100).endswith("…")
    assert _clip("short") == "short"


@pytest.mark.skipif(os.name == "nt", reason="Windows file names cannot hold control characters")
def test_a_file_name_cannot_drive_the_terminal(tmp_path):
    root = _project(tmp_path, "- The public API allows 100 requests per minute.\n")
    write_text_exact(root / "sp\x1b[2Jec.md", "The public API allows 60 requests per minute.\n")
    result = _audit(str(root / "MEMORY.md"), "--evidence", str(root))
    assert "\x1b" not in result.stdout and "\\u001b[2Jec.md:1" in result.stdout


def test_no_file_named_lists_the_files_and_one_file_reads_like_its_own_report(tmp_path, monkeypatch):
    root = _project(tmp_path, "- The public API allows 100 requests per minute.\n",
                    api="The public API allows 60 requests per minute.\n")
    monkeypatch.chdir(root)
    one = _audit()
    assert _lines(one)[1] == "MEMORY.md: 1 possible counterevidence"
    write_text_exact(root / "AGENTS.md", "- Backups run nightly.\n")
    several = _audit()
    lines = _lines(several)
    assert lines[1].startswith("2 file(s): ")
    assert any(line.startswith("== MEMORY.md: ") for line in lines) and any(line.startswith("== AGENTS.md: ") for line in lines)
    assert "1 of 2 claim(s) need a look." in lines


def test_output_writes_the_same_text(tmp_path):
    root = _project(tmp_path, "- The public API allows 100 requests per minute.\n",
                    api="The public API allows 60 requests per minute.\n")
    target = tmp_path / "out" / "brief.txt"
    target.parent.mkdir()
    shown = _audit(str(root / "MEMORY.md"), "--evidence", str(root), "--exclude", "out/**")
    written = _audit(str(root / "MEMORY.md"), "--evidence", str(root), "--exclude", "out/**", "--output", str(target))
    assert written.exit_code == 0 and target.read_bytes().decode("utf-8") == shown.stdout


def test_diff_has_no_brief_format(tmp_path):
    root = _project(tmp_path, "- Backups run nightly.\n")
    result = CliRunner().invoke(app, ["diff", str(root / "MEMORY.md"), "--base", "HEAD", "--format", "brief"])
    assert result.exit_code == 2


def test_what_a_person_dismissed_is_counted(tmp_path):
    claim, quote = "The public API allows 100 requests per minute.", "The public API allows 60 requests per minute."
    root = _project(tmp_path, f"- {claim}\n", api=f"{quote}\n")
    reviews = tmp_path.parent / f"{tmp_path.name}-reviews.jsonl"
    write_text_exact(reviews, json.dumps({"memory_id": claim_id(claim), "relation": "unrelated", "path": "api.md",
                                          "quote": quote, "reviewer": "ada"}) + "\n")
    result = _audit(str(root / "MEMORY.md"), "--evidence", str(root), "--reviews", str(reviews))
    assert result.exit_code == 0
    assert "Nothing flagged in 1 claim(s). 1 have no evidence, which means unchecked, not wrong. " \
           "1 candidate(s) dismissed by a person." in _lines(result)


def test_a_saved_brief_is_no_evidence_for_the_next_audit(tmp_path):
    claim = "The public API allows 100 requests per minute."
    root = _project(tmp_path, f"- {claim}\n", api="The public API allows 60 requests per minute.\n")
    result = _audit(str(root / "MEMORY.md"), "--evidence", str(root))
    assert is_report(result.stdout)
    (root / "api.md").unlink()
    write_text_exact(root / "old-brief.txt", result.stdout)
    again = _audit(str(root / "MEMORY.md"), "--evidence", str(root))
    assert "old-brief.txt" not in again.stdout and "POSSIBLE COUNTEREVIDENCE" not in again.stdout
