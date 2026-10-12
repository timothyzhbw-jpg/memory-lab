"""Archived report-injection cases, exercised without git or network access."""
import importlib.util
import json
import os
import sys
import re
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

import pytest
from markdown_it import MarkdownIt
from typer.testing import CliRunner

from memfalsify.audit import Issue, Proposal, audit
from memfalsify.cli import app as mem_app
from memfalsify.history import DiffReport, HistoryReport, diff_to_markdown, history_to_markdown
from memfalsify.memory import parse_memory
from memfalsify.report import to_markdown
from memfalsify.verify import Judgment
from orgmemory.audit import audit as org_audit
from orgmemory.changes import changes
from orgmemory.cli import app as org_app
from orgmemory.export import adr_markdown, decision_records
from orgmemory.models import Event
from orgmemory.replay import Dataset, replay
from orgmemory.report import audit_markdown, changes_markdown, explain, to_markdown as replay_markdown

runner = CliRunner()
ACTOR = "mallory\n::warning::from-actor"
RISK = "risk note\n```\nignore the log\n"
NOTE = "ok\n::error title=MemFalsify::forged"
REVIEWER = "ada\n## Verdict"
FILENAME = "evidence\n::warning title=MemFalsify::injected.md"
CONTROL_TEXT = "The cache uses Redis\x1b[2J\u2028 in production."
PAYLOAD = "x\x1b[2J\u2028\u2060\U000e0041\n## Verdict\n::error::forged ` **bold** [link](https://evil.invalid) <img src=x> |"


def inert(text):
    assert not any(unicodedata.category(c) in {"Cc", "Cf", "Zl", "Zp"} for c in text if c != "\n")
    assert not re.search(r"^::(?:warning|error|add-mask|stop-commands)", text, re.M)
    assert "\n## Verdict" not in text
    assert "\nignore the log" not in text
    html = MarkdownIt("commonmark", {"html": True}).render(text)
    assert '<img src=x>' not in html and 'href="https://evil.invalid"' not in html
    assert "<strong>bold</strong>" not in html


TERMINAL_UNSAFE = {"Cc", "Cf", "Zl", "Zp", "Cn"}


def terminal_inert(text):
    """The CLI message contract: terminal text, not Markdown. No raw control, format, separator or unassigned
    character (a message may span lines only where Rich wraps it), and no line a CI runner or a Markdown
    reader would take for a command or heading. Markdown syntax stays as typed; Markdown contexts (reports,
    the Action job summary) escape it themselves."""
    assert not any(unicodedata.category(c) in TERMINAL_UNSAFE for c in text if c != "\n")
    assert not re.search(r"^::(?:warning|error|add-mask|stop-commands)", text, re.M)
    assert "\n## Verdict" not in text
    assert "\nignore the log" not in text


@pytest.fixture
def memory_case(tmp_path):
    memory = tmp_path / "MEMORY.md"
    memory.write_text("- " + CONTROL_TEXT + "\n", encoding="utf-8")
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    quote = "The cache uses Redis in production."
    filename = FILENAME if os.name != "nt" else "evidence.md"
    (evidence / filename).write_text(quote, encoding="utf-8")
    claims, _ = parse_memory(memory)
    reviews = tmp_path / "reviews.jsonl"
    reviews.write_text(json.dumps({"memory_id": claims[0].id, "relation": "supports", "path": filename,
                                  "quote": quote, "reviewer": REVIEWER, "note": NOTE}) + "\n", encoding="utf-8")
    return memory, evidence, reviews


@pytest.mark.parametrize("case", ["memory_controls", "review_note", "reviewer_heading", "evidence_filename"])
def test_archived_memfalsify_cases(memory_case, case):
    memory, evidence, reviews = memory_case
    text = to_markdown(audit(memory, evidence, reviews))
    if case == "memory_controls":
        assert "\x1b" not in text and "\u2028" not in text
        assert "u001b" in text and "u2028" in text
    elif case == "review_note":
        assert "\n::error title=MemFalsify::forged" not in text
        assert "u000a::error title=MemFalsify::forged" in text
    elif case == "reviewer_heading":
        assert "\n## Verdict" not in text
        assert "u000a\\#\\# Verdict" in text
    else:
        if os.name == "nt":
            pytest.skip("Windows forbids newlines in file names")
        assert "\n::warning title=MemFalsify::injected" not in text
        assert "u000a::warning title=MemFalsify::injected" in text


def test_audit_terminal_and_written_markdown(memory_case, tmp_path):
    memory, evidence, reviews = memory_case
    args = ["audit", str(memory), "--evidence", str(evidence), "--reviews", str(reviews)]
    result = runner.invoke(mem_app, args)
    assert result.exit_code == 0, result.output
    inert(result.output)
    output = tmp_path / "report.md"
    result = runner.invoke(mem_app, args + ["--output", str(output)])
    assert result.exit_code == 0, result.output
    inert(output.read_text(encoding="utf-8"))
    # Claims only exposes JSONL; shielding must preserve the original parsed text.
    claims = runner.invoke(mem_app, ["claims", str(memory)])
    assert claims.exit_code == 0
    inert(claims.output)
    assert json.loads(claims.output.splitlines()[0])["text"] == parse_memory(memory)[0][0].text


@pytest.fixture
def org_case(org_demo):
    data = Dataset.load(org_demo)
    event = Event(id="hostile", timestamp="2026-10-09T00:00:00Z", actor=ACTOR,
                  action="approve", subject="Feature A", evidence_ids=["wed"])
    risks = [e.model_copy(update={"note": RISK}) if e.action == "raise_risk" else e for e in data.events]
    return Dataset(risks + [event], data.policy, data.evidence)


@pytest.mark.parametrize("view", ["replay", "explain"])
@pytest.mark.parametrize("case", ["actor", "yaml_fence"])
def test_archived_orgmemory_cases(org_case, view, case):
    result = replay(org_case)
    text = replay_markdown(result) if view == "replay" else explain(result, "Feature A")
    if case == "actor":
        assert "\n::warning::from-actor" not in text
        assert "u000a::warning::from-actor" in text
    else:
        assert text.splitlines().count("```") == 1
        assert "\nignore the log" not in text
        assert "u000a" in text
    inert(text)


@pytest.mark.parametrize("view,field", [
    (view, field) for view in ("audit", "diff", "history")
    for field in ("text", "location", "section", "metadata", "reviewer", "rationale", "quote", "opinion",
                  "issue", "proposal", "verifier", "limit", "path", "commit")
    if (view != "history" and field != "commit") or
       (view == "history" and field in {"text", "location", "section", "metadata", "limit", "path", "commit"})
])
def test_memfalsify_all_markdown_fields(memory_case, monkeypatch, view, field):
    memory, evidence, reviews = memory_case
    report = audit(memory, evidence, reviews)
    f = report.findings[0]
    if field in {"text", "location", "section"}:
        setattr(f, field, PAYLOAD)
    elif field == "metadata":
        f.metadata = {PAYLOAD: PAYLOAD}
    elif field in {"reviewer", "rationale"}:
        setattr(f.evidence[0], field, PAYLOAD)
    elif field == "quote":
        f.evidence[0].span = f.evidence[0].span.model_copy(update={"quote": PAYLOAD})
    elif field == "opinion":
        f.evidence[0].opinions = [Judgment(relation="supports", confidence=1, rationale=PAYLOAD, verifier=PAYLOAD)]
    elif field == "issue":
        f.issues = [Issue(kind="duplicate", detail=PAYLOAD)]
    elif field == "proposal":
        f.proposals = [Proposal(action=PAYLOAD, reason=PAYLOAD)]
    elif field == "verifier":
        report.verifier = PAYLOAD
    elif field == "limit":
        report.limitations = [PAYLOAD]
    elif field == "path":
        report.memory_file = report.evidence_root = PAYLOAD
        report.files_read = report.excluded = [PAYLOAD]
    if view == "audit":
        text = to_markdown(report)
        monkeypatch.setattr("memfalsify.cli.audit", lambda *a, **kw: report)
        args = ["audit", str(memory)]
    elif view == "diff":
        diff = DiffReport(memory_file=report.memory_file, audit=report, base={"rev": "base", "files": []},
                          head={"rev": "working tree", "files": []}, summary={"added": 1, "changed": 0},
                          changes=[], limitations=[PAYLOAD] if field == "limit" else [])
        text = diff_to_markdown(diff)
        monkeypatch.setattr("memfalsify.cli.memory_diff", lambda *a, **kw: diff)
        args = ["diff", str(memory), "--base", "HEAD"]
    else:
        history = HistoryReport(memory_file=report.memory_file, files=report.files_read, head=None,
                                commits_scanned=0, max_commits=10, complete=True,
                                claims=[dict(memory_id=f.memory_id, text=f.text, location=f.location,
                                             section=f.section, metadata=f.metadata,
                                             introduced={"commit": "a" * 40, "subject": PAYLOAD if field == "commit" else "first",
                                                         "author": PAYLOAD if field == "commit" else "ada"}, last_changed=None)],
                                removed=[], limitations=report.limitations)
        text = history_to_markdown(history)
        monkeypatch.setattr("memfalsify.cli.memory_history", lambda *a, **kw: history)
        args = ["history", str(memory)]
    inert(text)
    result = runner.invoke(mem_app, args)
    assert result.exit_code == 0, result.output
    inert(result.output)


@pytest.mark.parametrize("view", ["replay", "explain", "audit", "changes", "export"])
def test_orgmemory_all_markdown_formats(org_case, monkeypatch, tmp_path, view):
    # In-memory models exercise arbitrary valid untrusted fields without changing hashed inputs.
    org_case = Dataset([e.model_copy(update={"subject": PAYLOAD, **({"note": PAYLOAD} if e.actor == "zhou" else {})})
                        for e in org_case.events], org_case.policy, dict(org_case.evidence))
    span = org_case.evidence["wed"]
    org_case.evidence["wed"] = span.model_copy(update={"quote": PAYLOAD, "path": PAYLOAD, "source_uri": PAYLOAD})
    result = replay(org_case)
    cutoff = datetime(2026, 10, 11, tzinfo=timezone.utc)
    monkeypatch.setattr("orgmemory.cli._load", lambda *a: org_case)
    if view == "replay":
        text = replay_markdown(result)
        args = ["replay", str(tmp_path)]
    elif view == "explain":
        text = explain(result, PAYLOAD)
        args = ["explain", str(tmp_path), PAYLOAD]
    elif view == "audit":
        text = audit_markdown(org_audit(org_case, cutoff))
        args = ["audit", str(tmp_path), "--as-of", cutoff.isoformat()]
    elif view == "changes":
        start = datetime(2026, 10, 5, tzinfo=timezone.utc)
        text = changes_markdown(changes(org_case, start, cutoff))
        args = ["changes", str(tmp_path), "--from", start.isoformat(), "--to", cutoff.isoformat()]
    else:
        _, records = decision_records(org_case)
        text = "\n".join(adr_markdown(r) for r in records)
        out = tmp_path.parent / (tmp_path.name + "-adr")
        args = ["export", str(tmp_path), "--out", str(out)]
    inert(text)
    cli = runner.invoke(org_app, args)
    assert cli.exit_code == 0, cli.output
    inert(cli.output)
    if view == "export":
        for path in out.glob("*.md"):
            inert(path.read_text(encoding="utf-8"))


@pytest.mark.parametrize("package", ["memfalsify", "orgmemory", "precommit"])
def test_cli_errors_are_single_line_and_escape_controls(package):
    from memfalsify.cli import _fail as mem_fail
    from memfalsify.precommit import _fail as hook_fail
    from orgmemory.cli import _fail as org_fail
    app = __import__("typer").Typer()
    fail = {"memfalsify": mem_fail, "orgmemory": org_fail, "precommit": hook_fail}[package]
    @app.command()
    def command():
        fail(PAYLOAD)
    result = runner.invoke(app, [])
    assert result.exit_code == 2
    terminal_inert(result.output)
    # One line: ordinary line breaks become spaces; every control, format and separator character is a
    # visible \u escape; the rest is untouched.
    assert result.output == ("error: x\\u001b[2J\\u2028\\u2060\\U000e0041 ## Verdict ::error::forged "
                            "` **bold** [link](https://evil.invalid) <img src=x> |\n")


@pytest.mark.parametrize("package", ["memfalsify", "orgmemory", "precommit"])
def test_cli_errors_keep_windows_paths_literal(package):
    from memfalsify.cli import _fail as mem_fail
    from memfalsify.precommit import _fail as hook_fail
    from orgmemory.cli import _fail as org_fail
    app = __import__("typer").Typer()
    fail = {"memfalsify": mem_fail, "orgmemory": org_fail, "precommit": hook_fail}[package]
    @app.command()
    def command():
        fail(r"cannot read C:\Users\ada\notes_v2\MEMORY.md or \\server\share\[draft]\*.md")
    result = runner.invoke(app, [])
    assert result.exit_code == 2
    assert result.output == "error: cannot read C:\\Users\\ada\\notes_v2\\MEMORY.md or \\\\server\\share\\[draft]\\*.md\n"


def test_action_stdout_and_summary(memory_case, tmp_path, monkeypatch, capsys):
    spec = importlib.util.spec_from_file_location("report_security_action", Path(__file__).parents[1] / "action/memfalsify_action.py")
    action = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(action)
    memory, evidence, reviews = memory_case
    monkeypatch.chdir(tmp_path)
    original = action.run_cli
    monkeypatch.setattr(action, "run_cli", lambda args: original(args[:-2] + ["--reviews", str(reviews)] + args[-2:]))
    env = {"MEMFALSIFY_MEMORY": memory.name, "MEMFALSIFY_EVIDENCE": str(evidence), "MEMFALSIFY_STRICT": "false",
           "RUNNER_TEMP": str(tmp_path / "runner"), "GITHUB_STEP_SUMMARY": str(tmp_path / "summary.md")}
    assert action.main(env) == 0
    inert(capsys.readouterr().out)
    inert(Path(env["GITHUB_STEP_SUMMARY"]).read_text(encoding="utf-8"))


def test_action_missing_name_summary_and_diagnostics(tmp_path, monkeypatch, capsys):
    spec = importlib.util.spec_from_file_location("missing_security_action", Path(__file__).parents[1] / "action/memfalsify_action.py")
    action = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(action)
    monkeypatch.chdir(tmp_path)
    action.annotate("error", PAYLOAD)
    output = capsys.readouterr().out
    assert "\x1b" not in output and "\u2028" not in output
    env = {"MEMFALSIFY_MEMORY": '"' + PAYLOAD + '"', "RUNNER_TEMP": str(tmp_path / "runner"),
           "GITHUB_STEP_SUMMARY": str(tmp_path / "summary.md")}
    assert action.main(env) == 0
    inert(Path(env["GITHUB_STEP_SUMMARY"]).read_text(encoding="utf-8"))


@pytest.mark.parametrize("char", ["\x00", "\t", "\r", "\n", "\x1b", "\x7f", "\x85", "\u2028", "\u2029",
                                  "\u2060", "\u0890", "\U000e0041"])
def test_shared_display_escapes_all_unicode_categories(char):
    from memcore.display import code_span, markdown_text, one_line, report_text, yaml_value
    from memcore.sidecar import one_line as sidecar_one_line
    from orgmemory.report import _one_line, _safe

    payload = "before" + char + "after"
    escaped = "before" + (f"\\u{ord(char):04x}" if ord(char) <= 65535 else f"\\U{ord(char):08x}") + "after"
    assert one_line(payload) == escaped
    assert sidecar_one_line is _one_line is one_line and _safe is markdown_text
    for display in (code_span, markdown_text, report_text, yaml_value):
        inert(display(payload))
    assert json.loads(yaml_value(payload)) == escaped


@pytest.mark.parametrize("value", ["true", "false", "null", "yes", "ON", "1", "2026-10-11", "[x]", "- item", "# x",
                                   "`", "a:b", " x ", RISK, PAYLOAD])
def test_yaml_values_are_quoted_and_single_line(value):
    from memcore.display import one_line, yaml_value
    text = yaml_value(value)
    assert "\n" not in text and json.loads(text) == one_line(value)


@pytest.mark.parametrize("payload", ["id`|forged\n::error::injected", "id|forged"])
def test_replay_table_fields_cannot_create_cells(org_case, payload):
    org_case.events = [e.model_copy(update={"id": payload, "actor": "mallory|fake"})
                       if e.id == "hostile" else e for e in org_case.events]
    text = explain(replay(org_case), "Feature A")
    html = MarkdownIt("commonmark", {"html": True}).enable("table").render(text)
    rows = re.findall(r"<tr>.*?</tr>", html, re.S)
    assert rows and all(row.count("<td>") == 6 for row in rows[1:])
    inert(text)


@pytest.mark.parametrize("target,args", [(mem_app, ["audit"]), (mem_app, ["claims"]), (org_app, ["replay"]),
                                        (org_app, ["explain"])])
def test_cli_argument_errors_keep_injected_names_inert(target, args):
    result = runner.invoke(target, args + [PAYLOAD] + (["subject"] if args == ["explain"] else []))
    assert result.exit_code == 2
    terminal_inert(result.output)
    out = result.output
    # Click quotes the value with repr(); a build that does not leaves it to SafeTyperGroup's \uXXXX escapes.
    assert "\\x1b[2J" in out or "\\u001b[2J" in out
    assert "\\n::error::forged" in out or "\\u000a::error::forged" in out
    assert len([line for line in out.splitlines() if "Verdict" in line]) == 1  # the payload's line break stayed escaped


def test_safe_group_escapes_click_error_text_without_touching_paths():
    import typer
    from memcore.cli import SafeTyperGroup
    try:  # newer Typer vendors Click
        from typer._click.exceptions import ClickException
    except ImportError:
        from click import ClickException
    app = typer.Typer(cls=SafeTyperGroup)

    @app.command()
    def plain():
        raise ClickException("cannot read C:\\Users\\ada\\MEMORY.md\x1b[2J\u2028 <b>")

    @app.command()
    def parameter(name: str):
        raise typer.BadParameter("bad C:\\Users\\ada\x85 [x]", param_hint="name")

    result = runner.invoke(app, ["plain"])
    assert result.exit_code == 1
    terminal_inert(result.output)
    assert "cannot read C:\\Users\\ada\\MEMORY.md\\u001b[2J\\u2028 <b>" in result.output
    result = runner.invoke(app, ["parameter", "x"])
    assert result.exit_code == 2
    terminal_inert(result.output)
    assert "bad C:\\Users\\ada\\u0085 [x]" in result.output


@pytest.mark.skipif(sys.platform == "win32", reason="Windows forbids newlines in paths")
def test_output_path_diagnostics_are_escaped(memory_case, tmp_path):
    memory, evidence, reviews = memory_case
    output = tmp_path / "out\n::warning::written.md"
    result = runner.invoke(mem_app, ["audit", str(memory), "--output", str(output)])
    assert result.exit_code == 0, result.output
    terminal_inert(result.output)  # a status line, not a report
    assert result.output.count("\n") == 1 and "u000a::warning::written.md" in result.output


# --- Markdown fields: nested brackets, bare list markers, autolinks; ZWJ/ZWNJ in real text ----------------------

def _render(markdown, gfm=False):
    """HTML for a document: CommonMark, or cmark-gfm (GitHub's renderer, with autolinks) when ``gfm``."""
    if gfm:
        cmarkgfm = pytest.importorskip("cmarkgfm")  # test-only; the GFM autolink extension is not CommonMark
        return cmarkgfm.github_flavored_markdown_to_html(markdown)
    return MarkdownIt("commonmark", {"html": True}).render(markdown)


def _visible(html):
    import html as html_lib
    return html_lib.unescape(re.sub(r"<[^>]*>", "", html)).strip()


def _no_markup(html):
    for tag in ("<a ", "<img", "<ol", "<ul", "<h1", "<h2", "<pre", "<table", "<em", "<strong"):
        assert tag not in html, html


@pytest.mark.parametrize("gfm", [False, True], ids=["commonmark", "gfm"])
@pytest.mark.parametrize("value", ["![a[b]](http://evil.example/p.png)", "[a[b]](http://evil.example/)",
                                   "x [a [b] c](javascript:alert(1)) y", "![a[b]][ref]", "[a[b]][ref] and ![c][ref]",
                                   "[a][ref]", "![a](http://evil.example/p.png)", "[a\\]b](http://evil.example/)",
                                   "[ref]: http://evil.example/", "[a[b]]: http://evil.example/", "  [a]: x"])
def test_links_and_images_with_nested_brackets_do_not_survive(value, gfm):
    from memcore.display import markdown_text, report_text
    for display in (markdown_text, report_text):
        shown = display(value)
        assert not re.search(r"(?<!\\)\[", shown), shown  # every opening bracket is escaped
        # Even where the document defines the reference the field points at, it stays the text it was.
        html = _render(f"[ref]: http://evil.example/ref\n\n> {shown}\n\n{shown}\n", gfm)
        _no_markup(html)
        assert _visible(html).count(value.strip()) == 2, html


@pytest.mark.parametrize("gfm", [False, True], ids=["commonmark", "gfm"])
@pytest.mark.parametrize("value, escaped", [("1.", "1\\."), ("1)", "1\\)"), ("12.", "12\\."), ("0)", "0\\)"),
                                            ("1. ", "1\\. "), ("1.5", "1.5"), ("1.item", "1.item"),
                                            ("3 items", "3 items"), ("v1.", "v1.")])
def test_ordered_list_markers_are_escaped_even_with_nothing_after_them(value, escaped, gfm):
    from memcore.display import markdown_text, report_text
    for display in (markdown_text, report_text):
        assert display(value) == escaped
        shown = display(value)
        for document in (shown, "para\n\n" + shown, f"> {shown}", f"- {shown}\n- {shown}"):
            assert "<ol" not in _render(document, gfm), document


AUTOLINKS = [
    ("http://evil.example/p?a=1", "http\\://evil.example/p?a=1"),
    ("https://evil.example", "https\\://evil.example"),
    ("see ftp://evil.example/x now", "see ftp\\://evil.example/x now"),
    ("HTTP://EVIL.EXAMPLE", "HTTP\\://EVIL.EXAMPLE"),
    ("www.evil.example", "www\\.evil.example"),
    ("WWW.Evil.example/path", "WWW\\.Evil.example/path"),
    ("(see www.evil.example)", "(see www\\.evil.example)"),
    # cmark-gfm joins escaped characters back into one text node before it looks for addresses, so no backslash
    # escape breaks an e-mail autolink: the address becomes a code span (the same text, in code style).
    ("mallory@evil.example", "`mallory@evil.example`"),
    ("mailto:m.x+y@evil-site.example", "mailto:`m.x+y@evil-site.example`"),
    ("ask a@b.co, then c_d@e.example", "ask `a@b.co`, then `c_d@e.example`"),
]


@pytest.mark.parametrize("value, escaped", AUTOLINKS)
def test_untrusted_urls_hosts_and_addresses_are_escaped(value, escaped):
    from memcore.display import markdown_text, report_text
    assert markdown_text(value) == escaped == report_text(value)


@pytest.mark.parametrize("gfm", [False, True], ids=["commonmark", "gfm"])
@pytest.mark.parametrize("value", [v for v, _ in AUTOLINKS] + ["**www.evil.example", "_ http://evil.example"])
def test_untrusted_urls_hosts_and_addresses_render_as_plain_text(value, gfm):
    from memcore.display import markdown_text, report_text
    for display in (markdown_text, report_text):
        shown = display(value)
        for document in (shown, f"> {shown}", f"| {shown} |\n|---|\n| {shown} |", f"## {shown}"):
            html = _render(document, gfm)
            assert "<a " not in html and "<img" not in html, html
        assert _visible(_render(shown, gfm)) == value


@pytest.mark.parametrize("value", ["@mention", "a@b", "user @ host", "version 1.2.3", "awww.", "www", "http",
                                   "3:1//", "plain words", "C: not a url", "slack://x.example/y", "synthetic://note",
                                   "sftp://x.example", "xhttp://x.example", "file://host/path"])
def test_text_that_cannot_autolink_is_unchanged(value):
    from memcore.display import markdown_text, report_text
    assert markdown_text(value) == value == report_text(value)


@pytest.mark.parametrize("text", ["\u0645\u06cc\u200c\u062e\u0648\u0627\u0647\u0645", "\u0915\u094d\u200d\u0937",
                                  "\u0d28\u0d4d\u200d", "\U0001f468\u200d\U0001f469\u200d\U0001f467",
                                  "\u2764\ufe0f\u200d\U0001f525", "\u0dc1\u0dca\u200d\u0dbb"])
def test_zwj_and_zwnj_inside_real_text_are_kept(text):
    from memcore.display import code_span, markdown_text, one_line, report_text, yaml_value
    assert one_line(text) == text == markdown_text(text) == report_text(text)
    assert code_span(text) == f"`{text}`"
    assert json.loads(yaml_value(text)) == text


@pytest.mark.parametrize("char", ["\u200c", "\u200d"])
@pytest.mark.parametrize("template", ["a@b", "@x", "x @y", "x\u00a0@y", "a@@b", "\u0645@\u2060\u0646",
                                      "\u0645@\u200b\u0646", "\u0645@\u200f\u0646", "\u0645\u200e@\u0646",
                                      "\u0645\u0646\u202e@", "\u0645\u0646\ufeff@\u0647"])
def test_zwj_and_zwnj_that_join_nothing_are_still_escaped(char, template):
    from memcore.display import one_line
    shown = one_line(template.replace("@", char))
    assert char not in shown and f"\\u{ord(char):04x}" in shown
    assert not any(unicodedata.category(c) in {"Cc", "Cf", "Zl", "Zp"} for c in shown)


@pytest.mark.parametrize("char", ["\u200b", "\u200e", "\u200f", "\u061c", "\u202a", "\u202b", "\u202c", "\u202d",
                                  "\u202e", "\u2060", "\u2066", "\u2067", "\u2068", "\u2069", "\ufeff", "\u00ad",
                                  "\u180e", "\u0890", "\u2028", "\u2029", "\x85", "\x1b", "\U000e0041"])
def test_every_other_format_control_and_separator_stays_escaped_between_letters(char):
    from memcore.display import one_line
    escaped = f"\\u{ord(char):04x}" if ord(char) <= 0xFFFF else f"\\U{ord(char):08x}"
    assert one_line("\u0645" + char + "\u0646") == "\u0645" + escaped + "\u0646"
