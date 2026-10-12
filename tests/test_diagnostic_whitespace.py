"""Benign multi-line diagnostics read as one line; control, bidi and separator characters stay escaped.

The terminal message helpers collapse runs of ordinary whitespace (space, tab, LF, CR, VT, FF) to one space and
strip the ends, then escape everything unsafe. A line break is no reason to print ``\\u000a``. Other characters
that ``str.split`` happens to treat as whitespace are not ordinary: C0 information separators (U+001C to U+001F),
NEL, U+2028 and U+2029 are line breaks to some readers, so they are escaped, never collapsed or dropped.
Spaces such as U+00A0 and U+3000 are visible text and stay as typed.
"""
import json
import shutil

import pytest
import typer
from typer.testing import CliRunner

from memfalsify.cli import _fail as memfalsify_fail, _progress as memfalsify_progress, app as memfalsify_app
from memfalsify.gitstore import printable
from memfalsify.precommit import _fail as hook_fail
from orgmemory.cli import _fail as orgmemory_fail, _github_fail, app as orgmemory_app

runner = CliRunner()

BENIGN = "first line\n  second\tline\r\n\r\n\x0b\x0cthird   line  \n"
BENIGN_ONE_LINE = "first line second line third line"
SEPARATORS = "a \n\u2028\n b\x85c\u2029d\x1ce\x1ff"
SEPARATORS_ESCAPED = "a \\u2028 b\\u0085c\\u2029d\\u001ce\\u001ff"
OTHER_UNSAFE = "bidi \u202eevil\u202c zero\u200bwidth esc\x1b[2J nul\x00 del\x7f done"
OTHER_ESCAPED = "bidi \\u202eevil\\u202c zero\\u200bwidth esc\\u001b[2J nul\\u0000 del\\u007f done"
VISIBLE_SPACES = "no\u00a0break\u3000wide  \u2003em"  # U+00A0, U+3000, U+2003 are text; ASCII runs still collapse
VISIBLE_SPACES_SHOWN = "no\u00a0break\u3000wide \u2003em"


def failing(fail):
    app = typer.Typer()

    @app.command()
    def command(text: str):
        fail(text)
    return app


def progress_app():
    app = typer.Typer()

    @app.command()
    def command(text: str):
        memfalsify_progress(text)
    return app


def github_app():
    from orgmemory.github_fetch import GitHubError
    app = typer.Typer()

    @app.command()
    def command(text: str):
        _github_fail(GitHubError(text))
    return app


FAIL_APPS = {
    "memfalsify": (lambda: failing(memfalsify_fail), "error: ", 2),
    "orgmemory": (lambda: failing(orgmemory_fail), "error: ", 2),
    "precommit": (lambda: failing(hook_fail), "error: ", 2),
    "orgmemory-github": (github_app, "error: ", 2),
    "memfalsify-progress": (progress_app, "", 0),
}


@pytest.mark.parametrize("name", FAIL_APPS)
def test_benign_whitespace_collapses_to_one_line(name):
    build, prefix, code = FAIL_APPS[name]
    result = runner.invoke(build(), [BENIGN])
    assert result.exit_code == code
    assert result.output == f"{prefix}{BENIGN_ONE_LINE}\n"


@pytest.mark.parametrize("name", FAIL_APPS)
def test_separators_and_controls_are_escaped_not_collapsed(name):
    build, prefix, code = FAIL_APPS[name]
    result = runner.invoke(build(), [SEPARATORS])
    assert result.exit_code == code and result.output == f"{prefix}{SEPARATORS_ESCAPED}\n"
    result = runner.invoke(build(), [OTHER_UNSAFE])
    assert result.exit_code == code and result.output == f"{prefix}{OTHER_ESCAPED}\n"
    result = runner.invoke(build(), [VISIBLE_SPACES])
    assert result.exit_code == code and result.output == f"{prefix}{VISIBLE_SPACES_SHOWN}\n"


def test_printable_collapses_whitespace_and_escapes_the_rest():
    assert printable(BENIGN) == BENIGN_ONE_LINE
    assert printable(SEPARATORS) == SEPARATORS_ESCAPED
    assert printable(OTHER_UNSAFE) == OTHER_ESCAPED
    assert printable(VISIBLE_SPACES) == VISIBLE_SPACES_SHOWN
    assert printable("") == printable(" \n\t ") == ""
    assert printable("plain text, nothing to do") == "plain text, nothing to do"


@pytest.mark.parametrize("char", ["\x1c", "\x1d", "\x1e", "\x1f", "\x85", "\u2028", "\u2029"])
def test_str_split_whitespace_that_is_a_line_break_to_someone_is_never_dropped(char):
    escaped = f"\\u{ord(char):04x}"
    assert printable(f"a{char}b") == f"a{escaped}b"
    assert printable(f"{char}") == escaped
    assert printable(f"  {char}  ") == escaped
    assert printable(f"a \n{char}\n b") == f"a {escaped} b"


def test_orgmemory_append_invalid_action_prints_a_readable_line(org_demo):
    event = {"id": "ev-bad", "timestamp": "2026-10-09T09:00:00Z", "actor": "zhou", "action": "bless",
             "subject": "Feature A", "evidence_ids": ["wed"]}
    log = org_demo / "events.jsonl"
    before = log.read_bytes()
    result = runner.invoke(orgmemory_app, ["append", str(org_demo), "--event", json.dumps(event)])
    assert result.exit_code == 2 and log.read_bytes() == before
    assert "u000a" not in result.output and result.output.count("\n") == 1
    assert result.output.startswith("error: 1 validation error for Event action Input should be ")


@pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")
def test_memfalsify_diff_outside_a_repository_ends_with_a_readable_line(tmp_path, monkeypatch):
    loose = tmp_path / "loose"
    loose.mkdir()
    (loose / "MEMORY.md").write_bytes(b"- The cache uses Redis.\n")
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path))
    result = runner.invoke(memfalsify_app, ["diff", str(loose / "MEMORY.md"), "--base", "HEAD"])
    assert result.exit_code == 2 and "not inside a git work tree" in result.output
    assert "u000a" not in result.output and result.output.count("\n") == 1
    assert result.output.rstrip("\n").endswith(".git)")
