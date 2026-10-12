"""Output paths that untrusted text could still reach in the wrong shape.

* ``memfalsify rollback`` without ``--output`` shows the patch with control and bidi characters escaped on a
  terminal, and writes exact bytes to a pipe or a file (so ``git apply`` works).
* No untrusted value starts an output line (a log runner reads ``::error ...`` at the start of a line as a
  command): orgmemory's audit, its CSV export, and a multi-line quote in an HTML report.
* orgmemory's ``validate`` prints Windows-style paths with single backslashes.
"""
from __future__ import annotations

import csv
import io
import json
import os
import shutil
from hashlib import sha256
from html.parser import HTMLParser

import pytest
from typer.testing import CliRunner

from conftest import write_text_exact
import memfalsify.cli as memfalsify_cli
from memcore import htmlpage
from memfalsify.cli import app as memfalsify_app
from memfalsify.memory import parse_text
from orgmemory.cli import app as orgmemory_app

runner = CliRunner()
requires_git = pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")
FORGED = "::error title=X::forged Zq9mark"


def cli(app, *args):
    return runner.invoke(app, [os.fspath(a) for a in args])


def command_lines(text: str) -> list[str]:
    """Lines a workflow runner would read as a command."""
    return [line for line in text.split("\n") if line.lstrip().startswith("::")]


# --- orgmemory dataset -----------------------------------------------------------------------------------------

def dataset(root, *, actor="pm", subject="Feature A", sales="sales", unknown=None):
    """A request and an approval by ``actor`` (a self-approval), plus ``unknown`` acting without a role."""
    root.mkdir(parents=True)
    quote = "Feature A is approved for the November release"
    text = "Notes\n" + quote + "\nEnd\n"
    (root / "sources").mkdir()
    (root / "sources" / "main.txt").write_bytes(text.encode("utf-8"))  # exact bytes: the hash covers them
    start = len("Notes\n")
    write_text_exact(root / "evidence.jsonl", json.dumps({
        "id": "e1", "path": "sources/main.txt", "sha256": sha256(text.encode("utf-8")).hexdigest(), "quote": quote,
        "start": start, "end": start + len(quote), "source_uri": "synthetic://email/1", "source_kind": "email",
        "captured_at": "2026-10-05T09:00:00Z"}) + "\n")
    actors = {actor: ["product_manager"], sales: ["account_manager"]}
    write_text_exact(root / "policy.json", json.dumps({
        "actors": actors,
        "permissions": {"request": ["product_manager", "account_manager"], "comment": ["product_manager"],
                        "approve": ["product_manager"], "reject": ["product_manager"],
                        "revoke": ["product_manager"], "reconsider": ["product_manager"],
                        "raise_risk": ["product_manager"], "dependency": ["product_manager"]}}))

    def event(id, hour, who, action):
        return {"id": id, "timestamp": f"2026-10-05T{hour:02d}:00:00Z", "actor": who, "action": action,
                "subject": subject, "evidence_ids": ["e1"]}

    events = [event("r1", 9, actor, "request"), event("a1", 10, actor, "approve")]
    if unknown is not None:
        events.append(event("u1", 11, unknown, "approve"))
    write_text_exact(root / "events.jsonl", "".join(json.dumps(e) + "\n" for e in events))
    return root


# --- finding 3: no untrusted value starts an output line -------------------------------------------------------

def test_self_approval_detail_does_not_start_with_the_actor(tmp_path):
    root = dataset(tmp_path / "ds", actor=FORGED)
    result = cli(orgmemory_app, "audit", root, "--as-of", "2026-12-01T00:00:00Z")
    assert result.exit_code == 0, result.output
    assert "self_approval" in result.output and FORGED in result.output
    assert command_lines(result.output) == []


def test_self_approval_detail_in_json_names_the_actor_after_fixed_text(tmp_path):
    root = dataset(tmp_path / "ds", actor="pm")
    result = cli(orgmemory_app, "audit", root, "--as-of", "2026-12-01T00:00:00Z", "--format", "json")
    assert result.exit_code == 0, result.output
    detail, = [f["detail"] for f in json.loads(result.output)["findings"] if f["kind"] == "self_approval"]
    assert detail == "approved by pm, who had also requested the subject"


def test_csv_export_quotes_a_subject_that_looks_like_a_command(tmp_path):
    root = dataset(tmp_path / "ds", subject=FORGED)
    result = cli(orgmemory_app, "export", root, "--format", "csv")
    assert result.exit_code == 0, result.output
    assert command_lines(result.output) == []
    rows = list(csv.reader(io.StringIO(result.output)))
    assert len(rows) == 2 and result.output.count("\n") == 2  # still one header row and one row
    assert rows[1][0] == FORGED  # the cell decodes to the exact subject
    assert len(rows[1]) == len(rows[0])


def test_csv_export_of_ordinary_subjects_is_unchanged(tmp_path):
    result = cli(orgmemory_app, "export", dataset(tmp_path / "ds"), "--format", "csv")
    assert result.exit_code == 0, result.output
    assert result.output.splitlines()[1].startswith("Feature A,")  # no quotes added


@pytest.mark.parametrize("subject", [" " + FORGED, "\u00a0" + FORGED, FORGED.replace(",", ";") + ",x"])
def test_csv_export_quotes_every_command_shape(tmp_path, subject):
    result = cli(orgmemory_app, "export", dataset(tmp_path / "ds", subject=subject), "--format", "csv")
    assert result.exit_code == 0, result.output
    assert command_lines(result.output) == []
    assert list(csv.reader(io.StringIO(result.output)))[1][0] == subject


class Text(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.quotes: list[str] = []
        self._in = False

    def handle_starttag(self, tag, attrs):
        if tag == "blockquote":
            self._in = True
            self.quotes.append("")

    def handle_endtag(self, tag):
        if tag == "blockquote":
            self._in = False

    def handle_data(self, data):
        if self._in:
            self.quotes[-1] += data


def test_html_quote_writes_line_breaks_as_character_references():
    cue = "first line\n" + FORGED + "\r\nlast line\n::warning::x"
    html = htmlpage.quote(cue)
    assert "\n" not in html and "\r" not in html
    assert html.count("&#10;") == 3  # a CRLF is one break, as before
    parser = Text()
    parser.feed(html)
    assert parser.quotes == [cue.replace("\r\n", "\n")]  # the same text, so the same layout (pre-wrap)


def test_html_quote_of_one_line_is_unchanged():
    assert htmlpage.quote("a <b> & c") == "<blockquote>a &lt;b&gt; &amp; c</blockquote>"


def test_html_audit_report_has_no_command_line_from_a_multi_line_cue(tmp_path):
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    write_text_exact(
        evidence / "call.srt",
        "1\n00:00:02,000 --> 00:00:06,500\nCustomer: Your retry budget is 3 attempts per request, right?\n"
        + FORGED + "\n\n2\n00:00:06,500 --> 00:00:08,000\nSupport: Yes, 3 attempts per request.\n")
    write_text_exact(tmp_path / "MEMORY.md", "# Memory\n\n- The retry budget is 3 attempts per request.\n")
    result = cli(memfalsify_app, "audit", tmp_path / "MEMORY.md", "--evidence", evidence, "--format", "html",
                 "--as-of", "2026-10-10")
    assert result.exit_code in (0, 1), result.output
    assert command_lines(result.output) == []
    assert "&#10;" in result.output or "Zq9mark" not in result.output  # the cue was quoted with its break kept
    parser = Text()
    parser.feed(result.output)
    assert any("\n" + FORGED in quote or quote.endswith(FORGED) for quote in parser.quotes)


# --- finding 5: validate prints paths unchanged -----------------------------------------------------------------

def test_validate_prints_a_windows_style_actor_with_single_backslashes(tmp_path):
    root = dataset(tmp_path / "ds", unknown="C:\\Users\\x\\file_name_[1]")
    result = cli(orgmemory_app, "validate", root)
    assert result.exit_code == 0, result.output
    note, = [line for line in result.output.splitlines() if line.startswith("note: actors without any role")]
    assert note.endswith(": C:\\Users\\x\\file_name_[1]")


def test_validate_escapes_control_characters_in_an_actor(tmp_path):
    root = dataset(tmp_path / "ds", unknown="evil\x1b[2J\u202e\n::error::x")
    result = cli(orgmemory_app, "validate", root)
    assert result.exit_code == 0, result.output
    assert all(ord(c) >= 32 or c == "\n" for c in result.output) and "\u202e" not in result.output
    assert command_lines(result.output) == []
    assert "evil\\u001b[2J\\u202e\\u000a::error::x" in result.output


# --- finding 2: rollback to a terminal ---------------------------------------------------------------------------

HOSTILE_BASE = ("# Memory\n\n- The cache uses Redis for sessions \x1b[2J\x1b]8;;http://evil.example/\x07click\u202e"
                " evil\u202c.\n")
HEAD = "# Memory\n\n- The cache uses Redis for sessions.\n"
CLEAN_BASE = "# Memory\n\n- The cache uses Memcached for sessions.\n"


def rollback_setup(git_repo, base_text):
    base = git_repo.commit("base", {"MEMORY.md": base_text})
    git_repo.write({"MEMORY.md": HEAD})
    claim = parse_text(HEAD, "MEMORY.md")[0][0].id
    return base, claim, git_repo.root / "MEMORY.md"


def hidden(text: str) -> list[str]:
    import unicodedata
    return sorted({f"U+{ord(c):04X}" for c in text if c != "\n" and unicodedata.category(c) in {"Cc", "Cf", "Zl", "Zp"}})


@requires_git
def test_rollback_to_a_terminal_shows_the_patch_escaped(git_repo, monkeypatch):
    base, claim, memory = rollback_setup(git_repo, HOSTILE_BASE)
    monkeypatch.setattr(memfalsify_cli, "_stdout_is_terminal", lambda: True)
    result = cli(memfalsify_app, "rollback", memory, "--claim", claim, "--to", base)
    assert result.exit_code == 0, result.output
    shown = result.stdout_bytes.decode("utf-8")
    assert hidden(shown) == []
    assert "\\u001b[2J" in shown and "\\u202e" in shown and "\\u0007" in shown
    assert shown.startswith("# MemFalsify rollback patch") and "\n@@" in shown  # the patch's own layout
    assert "note:" in result.stderr and "--output" in result.stderr


@requires_git
def test_rollback_to_a_terminal_leaves_a_clean_patch_exactly_as_it_is(git_repo, monkeypatch):
    base, claim, memory = rollback_setup(git_repo, CLEAN_BASE)
    piped = cli(memfalsify_app, "rollback", memory, "--claim", claim, "--to", base).stdout_bytes
    monkeypatch.setattr(memfalsify_cli, "_stdout_is_terminal", lambda: True)
    result = cli(memfalsify_app, "rollback", memory, "--claim", claim, "--to", base)
    assert result.stdout_bytes == piped and b"Memcached" in piped
    assert "note:" not in result.stderr


@requires_git
def test_rollback_to_a_pipe_keeps_the_exact_bytes_and_git_apply_works(git_repo):
    base, claim, memory = rollback_setup(git_repo, HOSTILE_BASE)
    result = cli(memfalsify_app, "rollback", memory, "--claim", claim, "--to", base)  # CliRunner: not a terminal
    assert result.exit_code == 0, result.output
    patch = result.stdout_bytes
    assert b"\x1b[2J" in patch and "\u202e".encode() in patch and b"\x07" in patch
    path = git_repo.root.parent / "piped.patch"
    path.write_bytes(patch)
    git_repo.git("apply", "--check", str(path))
    git_repo.git("apply", str(path))
    assert git_repo.read("MEMORY.md") == HOSTILE_BASE.encode("utf-8")


@requires_git
@pytest.mark.parametrize("terminal", [False, True])
def test_rollback_output_file_keeps_the_exact_bytes(git_repo, monkeypatch, terminal):
    base, claim, memory = rollback_setup(git_repo, HOSTILE_BASE)
    piped = cli(memfalsify_app, "rollback", memory, "--claim", claim, "--to", base).stdout_bytes
    monkeypatch.setattr(memfalsify_cli, "_stdout_is_terminal", lambda: terminal)
    out = git_repo.root.parent / "restore.patch"
    result = cli(memfalsify_app, "rollback", memory, "--claim", claim, "--to", base, "--output", out)
    assert result.exit_code == 0, result.output
    assert result.stdout_bytes == b""  # nothing on stdout
    assert out.read_bytes() == piped
    git_repo.git("apply", "--check", str(out))
