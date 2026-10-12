"""Hostile text in untrusted fields, rendered the way GitHub renders a report: GitHub Flavored Markdown.

The other report tests render with CommonMark and no linkify. GitHub does more: it turns bare URLs, ``www.``
hosts and e-mail addresses into links (the autolink extension), reads pipe rows as tables, strikes out
``~~text~~`` and lets raw ``<img>`` through its sanitizer. A report is read there, so these tests render the
real reports (memfalsify audit, diff and history; orgmemory replay, explain, audit and changes) with
``cmarkgfm`` and options that mirror GitHub's (autolink, table, strikethrough and tagfilter extensions, raw
HTML kept), with one payload planted in one untrusted field at a time, and check the HTML, not the Markdown:

* no ``<img``, and no tag that is not one the report itself produces (so no raw HTML from the payload);
* no ``<a>`` that the plain-words run of the same report does not have (a link whose target is untrusted text);
* no ``<ol>``, heading, ``<pre>``, ``<hr>`` or ``<table>`` beyond what the plain-words run has;
* every table row has as many cells as its header, and the header is one the plain-words run has;
* the payload's visible text still appears, once for every place the plain words appear: a payload that was
  consumed as syntax (an image, a link, a heading, a list, a table cell) has lost its literal text.

Each test id is ``<surface>-<field>-<payload>``. The payload ``plain`` is a control: ordinary words in the same
place must pass every check, and they prove that the field is shown by that surface. ``cmarkgfm`` is a
test-only dependency (CI installs it), so the module is skipped where it is missing.
"""
from __future__ import annotations

import json
import re
import shutil
from collections import Counter
from hashlib import sha256
from html.parser import HTMLParser
from pathlib import Path

import pytest
from typer.testing import CliRunner

cmarkgfm = pytest.importorskip("cmarkgfm")

from memfalsify.cli import app as memfalsify_app  # noqa: E402
from orgmemory.cli import app as orgmemory_app  # noqa: E402

runner = CliRunner()

# --- rendering: what GitHub does with a report -----------------------------------------------------------------

GFM_EXTENSIONS = ["table", "autolink", "strikethrough", "tagfilter"]


def render(markdown: str) -> str:
    """GitHub Flavored Markdown with raw HTML kept: GitHub sanitizes afterwards, but it keeps ``<img>``."""
    return cmarkgfm.markdown_to_html_with_extensions(
        markdown, extensions=GFM_EXTENSIONS,
        options=cmarkgfm.Options.CMARK_OPT_UNSAFE | cmarkgfm.Options.CMARK_OPT_GITHUB_PRE_LANG)


class Page(HTMLParser):
    """What the HTML contains: start tags, links, tables (header and cells per row) and visible text."""

    def __init__(self, html: str):
        super().__init__(convert_charrefs=True)
        self.tags: Counter = Counter()
        self.links: list[str] = []
        self.tables: list[dict] = []
        self.headers: set[tuple] = set()
        self._chunks: list[str] = []
        self._row: dict | None = None
        self._cell: list[str] | None = None
        self.feed(html)
        self.close()
        self.text = " ".join("".join(self._chunks).split())

    def handle_starttag(self, tag, attrs):
        self.tags[tag] += 1
        self._chunks.append("\0" if tag not in ("code", "em", "strong", "del") else "")
        if tag == "a":
            self.links.append(dict(attrs).get("href") or "")
        elif tag == "table":
            self.tables.append({"rows": [], "columns": None})
        elif tag == "tr":
            self._row = {"th": [], "cells": 0}
        elif tag in ("td", "th") and self._row is not None:
            self._row["cells"] += 1
            self._cell = []
            if tag == "th":
                self._row["th"].append(self._cell)

    def handle_endtag(self, tag):
        self._chunks.append("\0" if tag not in ("code", "em", "strong", "del") else "")
        if tag in ("td", "th"):
            self._cell = None
        elif tag == "tr" and self._row is not None and self.tables:
            self.tables[-1]["rows"].append(self._row)
            if self._row["th"]:
                self.tables[-1]["columns"] = self._row["cells"]
                self.headers.add(tuple(" ".join("".join(cell).split()) for cell in self._row["th"]))
            self._row = None

    def handle_data(self, data):
        self._chunks.append(data)
        if self._cell is not None:
            self._cell.append(data)


# --- payloads --------------------------------------------------------------------------------------------------

PAYLOADS = {
    # Control: ordinary words. Every check must pass on it, and it must be visible in the field.
    "plain": "ordinary words Zq9mark",
    # Brackets inside the link text or the image alt text: a scanner that stops at the first ``]`` misses them.
    # The ``_rel`` pair has no absolute URL, so it fails only if the brackets themselves are not escaped.
    "image_nested": "![a[b]](http://evil.example/p.png)",
    "link_nested": "[a[b]](http://evil.example/)",
    "image_nested_rel": "![a[b]](p.png)",
    "link_nested_rel": "[a[b]](other.md)",
    "image": "![a](http://evil.example/p.png)",
    "link": "[a](http://evil.example/)",
    "ref_def": "[ref]: http://evil.example/ref",
    # Raw HTML.
    "img_tag": "<img src=x onerror=1>",
    "script_tag": "<script>alert(1)</script>",
    "angle_autolink": "<http://evil.example/x>",
    # GFM autolinks: a scheme URL, a ``www.`` host, an e-mail address, an ftp URL.
    "bare_url": "http://evil.example/x",
    "www": "www.evil.example",
    "email": "someone@evil.example",
    "ftp_url": "ftp://evil.example/x",
    # Block syntax at the start of a line: an ordered list (both markers), a heading, a code fence.
    "ol_dot": "1. first",
    "ol_paren": "1) first",
    "heading": "# heading",
    "fence": "```",
    # A pipe row: a table cell that ends early.
    "table_row": "| a | b |",
    "emphasis": "**bold** and ~~struck~~",
}


def cases(pairs):
    """One test id per (surface, field, payload): ``<surface>-<field>-<payload>``."""
    return [pytest.param(surface, field, name, id=f"{surface}-{field}-{name}")
            for surface, fields in pairs.items() for field in fields for name in PAYLOADS]


# --- fixtures for the memfalsify reports -----------------------------------------------------------------------

CLAIM = "The public API rate limit is 100 requests per minute"
OTHER_CLAIM = "The main database runs PostgreSQL 15."
# The claim that changes between the versions in a diff. It is long, so that the diff still pairs the old and the
# new version when a payload removes or reshapes the words at its end.
DB_OLD = "The main database runs PostgreSQL 15 today in the primary region for every production workload"
DB_NEW = DB_OLD.replace("PostgreSQL 15", "PostgreSQL 16")
CACHE = "The cache uses Redis for sessions"
DB_HISTORY = "The main database runs PostgreSQL 16 today"
EVIDENCE_LINE = "The public API rate limit is 60 requests per minute"
AS_OF = "2026-10-10"


def write(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data if isinstance(data, bytes) else data.encode("utf-8"))


def jsonl(*records) -> str:
    return "".join(json.dumps(record) + "\n" for record in records)


def invoke(app, args):
    result = runner.invoke(app, [str(a) for a in args])
    crash = result.exception if not isinstance(result.exception, SystemExit) else None
    assert crash is None, f"unhandled {type(crash).__name__}: {str(crash)[:200]!r}"
    assert result.exit_code == 0, f"exit {result.exit_code}: {result.output[:300]!r}"
    return result.output


def memfalsify_audit_report(root: Path, field: str, text: str) -> str:
    """MEMORY.md, evidence/ and reviews.jsonl; ``text`` replaces the ordinary words of ``field``."""
    def pick(name, ordinary):
        return text if name == field else ordinary

    def tail(*names):
        return f", {text}" if field in names else ""

    memory = root / "MEMORY.md"
    write(memory, f"# Project memory\n\n## Product {pick('section', 'decisions')}\n\n- {CLAIM}{tail('claim')}.\n"
                  f"- {OTHER_CLAIM}\n")
    evidence = root / "evidence"
    line = f"{EVIDENCE_LINE}{tail('review_quote')}"
    write(evidence / "api.md", line + ".\n")
    write(evidence / "limits.md", f"{EVIDENCE_LINE}{tail('candidate_quote')}.\n")
    write(evidence / "db.md", "The main database runs PostgreSQL 16.\n")
    if field == "transcript_cue":  # a recording with a WebVTT transcript bound to its hash
        write(evidence / "standup.mp4", b"recording")
        write(evidence / "standup.mp4.vtt",
              f"WEBVTT\n\nNOTE source_sha256: {sha256(b'recording').hexdigest()}\n\n"
              f"00:00:01.000 --> 00:00:05.000\n{EVIDENCE_LINE}{tail('transcript_cue')}.\n")
    if field == "ocr_line":  # a whiteboard photo with an OCR sidecar bound to its hash
        write(evidence / "board.png", b"photo")
        write(evidence / "board.png.ocr.json", json.dumps({
            "format": "memlab-ocr/1", "source_sha256": sha256(b"photo").hexdigest(),
            "pages": [{"page": 1, "width": 100, "height": 100,
                       "lines": [{"text": f"{EVIDENCE_LINE}{tail('ocr_line')}", "bbox": [1, 2, 30, 40]}]}]}))
    listed = invoke(memfalsify_app, ["claims", memory])
    claims = [json.loads(row) for row in listed.splitlines() if row.startswith("{")]
    memory_id = next(c["memory_id"] for c in claims if c["text"].startswith("The public API"))
    write(root / "reviews.jsonl", jsonl({
        "memory_id": memory_id, "relation": "contradicts", "path": "api.md", "quote": line,
        "reviewer": pick("reviewer", "demo-reviewer"), "note": pick("note", "The limit changed.")}))
    return invoke(memfalsify_app, ["audit", memory, "--evidence", evidence, "--reviews", root / "reviews.jsonl",
                                   "--as-of", AS_OF])


def make_repo(template, root: Path):
    """A second throwaway repository next to the ``git_repo`` fixture's, with the same isolated environment."""
    root.mkdir(parents=True, exist_ok=True)
    repo = type(template)(root, template.hooks)
    repo.git("init", "-q")
    repo.git("config", "--local", "user.name", "Test Author")
    repo.git("config", "--local", "user.email", "test@example.com")
    return repo


def memfalsify_git_report(repo, command: str, field: str, text: str) -> str:
    """A base commit and an uncommitted head version of MEMORY.md (diff), or two commits of it (history)."""
    def tail(name):
        return f", {text}" if field == name else ""

    section = f" {text}" if field == "section" else ""
    if command == "history":
        first = ("# Memory\n\n## Infra\n\n"
                 f"- {CACHE}{tail('claim')}.\n- The main database runs PostgreSQL 15 today.\n")
        second = (f"# Memory\n\n## Infra{section}\n\n- {CACHE}{tail('claim')}.\n"
                  f"- {DB_HISTORY}{tail('claim')}.\n")
        if field == "author":
            repo.git("config", "--local", "user.name", text)
        repo.commit(text if field == "commit_subject" else "first", {"MEMORY.md": first})
        repo.commit(text if field == "commit_subject" else "second", {"MEMORY.md": second})
        if field == "author" and repo.git("log", "-1", "--format=%an").strip() != text:
            pytest.skip("git rewrites this author name (it drops <, > and edge punctuation)")
        return invoke(memfalsify_app, ["history", repo.root / "MEMORY.md"])
    base = ("# Memory\n\n## Infra\n\n"
            f"- {CACHE}{tail('removed')}.\n"
            f"- {DB_OLD}{tail('changed')}.\n"
            "- Deployments go through the staging cluster.\n")
    head = (f"# Memory\n\n## Infra{section}\n\n"
            f"- {DB_NEW}{tail('changed')}.\n"
            "- Deployments go through the staging cluster.\n"
            f"- {CLAIM}{tail('added')}.\n")
    sha = repo.commit("base", {"MEMORY.md": base, "evidence/api.md": f"{EVIDENCE_LINE}{tail('evidence_text')}.\n",
                               "evidence/db.md": "The main database runs PostgreSQL 16.\n"})
    repo.write({"MEMORY.md": head})
    return invoke(memfalsify_app, ["diff", repo.root / "MEMORY.md", "--base", sha, "--evidence",
                                   repo.root / "evidence", "--as-of", AS_OF])


# --- fixtures for the orgmemory reports ------------------------------------------------------------------------

QUOTE = "Feature A is rejected for the November release"
ORG_AS_OF = "2026-11-30T00:00:00Z"


def orgmemory_report(root: Path, command: str, field: str, text: str) -> str:
    """A dataset with real hashes: requests, risks, a dependency, a reject, an approval, an ignored attempt
    and a role change, so that every report has something to say. ``text`` replaces the ordinary words of
    ``field``."""
    def pick(name, ordinary):
        return text if name == field else ordinary

    subject, dependency = pick("subject", "Feature A"), pick("dependency", "Billing migration")
    pm, sales, role = pick("actor", "zhou"), pick("actor_ignored", "wu"), pick("role", "product_manager")
    event_id, evidence_id = pick("event_id", "ev-approve"), "e-main"
    quote = f"{QUOTE}, {text}" if field == "evidence_quote" else QUOTE
    source = "Meeting notes\n" + quote + "\nEnd of notes\n"
    start = len("Meeting notes\n")
    write(root / "sources" / "main.txt", source)
    write(root / "evidence.jsonl", jsonl({
        "id": evidence_id, "path": "sources/main.txt", "sha256": sha256(source.encode("utf-8")).hexdigest(),
        "quote": quote, "start": start, "end": start + len(quote), "source_uri": pick("source_uri",
                                                                                     "synthetic://email/acme/1"),
        "source_kind": "email", "captured_at": "2026-10-05T09:00:00Z"}))
    everyone = [role, "customer", "account_manager", "engineer"]
    write(root / "policy.json", json.dumps({
        "actors": {pm: [role], sales: ["account_manager"], "buyer@acme": ["customer"], "lin": ["engineer"],
                   "chen": ["org_admin"]},
        "permissions": {"request": everyone, "reconsider": everyone, "comment": everyone,
                        "raise_risk": ["engineer", role], "dependency": ["engineer", role],
                        "approve": [role], "reject": [role], "revoke": [role], "manage_roles": ["org_admin"]}}))

    def event(id, day, hour, actor, action, **more):
        return {"id": id, "timestamp": f"2026-10-{day:02d}T{hour:02d}:00:00Z", "actor": actor, "action": action,
                "subject": subject, "evidence_ids": [evidence_id], **more}

    write(root / "events.jsonl", jsonl(
        event("ev-request", 5, 9, "buyer@acme", "request", note="Asked in the quarterly review"),
        event("ev-pm-request", 5, 10, pm, "request"),
        event("ev-risk", 6, 9, "lin", "raise_risk", note=pick("risk_note", "Export reads the billing schema")),
        event("ev-dependency", 6, 10, "lin", "dependency", dependency=dependency),
        event("ev-reject", 7, 9, pm, "reject"),
        event(event_id, 9, 9, pm, "approve"),
        event("ev-late-risk", 10, 9, "lin", "raise_risk", note="A late risk"),
        event("ev-ignored", 10, 10, sales, "approve"),
        event("ev-revoke", 11, 9, pm, "revoke", supersedes="ev-reject"),
        event("ev-reconsider", 12, 9, "buyer@acme", "reconsider")))
    write(root / "policy-log.jsonl", jsonl({
        "id": "pl-leaves", "timestamp": "2026-10-20T09:00:00Z", "actor": "chen", "action": "revoke_role",
        "subject_actor": pm, "role": role, "evidence_ids": [evidence_id],
        "note": pick("policy_note", "Moved to the platform team")}))
    args = {"replay": ["replay", root], "explain": ["explain", root, subject],
            "audit": ["audit", root, "--as-of", ORG_AS_OF],
            "changes": ["changes", root, "--from", "2026-10-01T00:00:00Z", "--to", ORG_AS_OF]}[command]
    return invoke(orgmemory_app, args)


# --- the surfaces: every field listed here is shown by the report, which the ``plain`` control proves ---------

SURFACES = {
    "memfalsify-audit": ["claim", "section", "reviewer", "note", "review_quote", "candidate_quote",
                         "transcript_cue", "ocr_line"],
    "memfalsify-diff": ["added", "changed", "removed", "section", "evidence_text"],
    "memfalsify-history": ["claim", "section", "commit_subject", "author"],
    "orgmemory-replay": ["subject", "dependency", "actor", "actor_ignored", "role", "risk_note", "policy_note",
                         "evidence_quote", "source_uri", "event_id"],
    "orgmemory-explain": ["subject", "dependency", "actor", "actor_ignored", "role", "risk_note", "policy_note",
                          "evidence_quote", "source_uri", "event_id"],
    "orgmemory-audit": ["subject", "dependency", "actor", "actor_ignored", "role", "risk_note", "policy_note",
                        "evidence_quote", "source_uri", "event_id"],
    "orgmemory-changes": ["subject", "dependency", "actor", "actor_ignored", "role", "risk_note", "evidence_quote",
                          "source_uri", "event_id"],
}

# A claim is parsed out of a memory file: the parser ends a claim at ``1.``, drops ``**`` and code spans and keeps
# the rest. What the report shows for such a field is the parsed claim, so that is what must stay visible.
CLAIM_PREFIXES = {
    ("memfalsify-audit", "claim"): [CLAIM],
    ("memfalsify-diff", "added"): [CLAIM],
    ("memfalsify-diff", "changed"): [DB_OLD, DB_NEW],
    ("memfalsify-diff", "removed"): [CACHE],
    ("memfalsify-history", "claim"): [CACHE, DB_HISTORY],
}
# The quote of an evidence line is one sentence of it: it ends at ``1.``.
EVIDENCE_PREFIXES = {("memfalsify-audit", "candidate_quote"): EVIDENCE_LINE,
                     ("memfalsify-diff", "evidence_text"): EVIDENCE_LINE}


def parsed_claim(root: Path, sentence: str) -> str:
    memory = root / "parse" / "MEMORY.md"
    write(memory, f"- {sentence}\n")
    rows = [json.loads(row) for row in invoke(memfalsify_app, ["claims", memory]).splitlines() if row.startswith("{")]
    return " ".join(rows[0]["text"].split()) if rows else ""


def produce(surface: str, field: str, text: str, tmp_path: Path, request, name: str) -> tuple[str, list[str]]:
    """The Markdown report of ``surface`` with ``text`` in ``field``, built in ``tmp_path / name``, and the strings
    it must show verbatim: the text itself, or the parsed claim it ends up in."""
    root = tmp_path / name
    tool, _, command = surface.partition("-")
    if tool == "orgmemory":
        report = orgmemory_report(root, command, field, text)
    elif command == "audit":
        report = memfalsify_audit_report(root, field, text)
    else:
        if shutil.which("git") is None:
            pytest.skip("git is not installed")
        template = request.getfixturevalue("git_repo")
        repo = template if name == "hostile" else make_repo(template, root)
        report = memfalsify_git_report(repo, command, field, text)
    if prefixes := CLAIM_PREFIXES.get((surface, field)):
        shown = [parsed_claim(root, f"{prefix}, {text}.") for prefix in prefixes]
    elif prefix := EVIDENCE_PREFIXES.get((surface, field)):
        shown = [f"{prefix}, " + re.split(r"(?<=[.!?])\s", " ".join(text.split()))[0]]
    else:
        shown = [" ".join(text.split())]
    return report, shown


# --- the properties --------------------------------------------------------------------------------------------

# Elements a report produces by itself. Anything else in the HTML (``<img>``, ``<script>``, ``<b>``, ``<del>``,
# ``<input>``, ...) came from a payload, unless the plain-words run has it too.
REPORT_TAGS = {"p", "ul", "li", "table", "thead", "tbody", "tr", "th", "td", "code", "pre", "blockquote", "strong",
               "em", "br", "h1", "h2", "h3", "h4", "h5", "h6", "hr"}
# Elements that start a block or a link: their number must not grow with the payload.
STRUCTURE_TAGS = ["a", "ol", "h1", "h2", "h3", "h4", "h5", "h6", "pre", "hr", "table", "blockquote"]


def page_problems(page: Page, control: Page, shown: list[str], plain_shown: list[str]) -> list[str]:
    problems = []
    if page.tags["img"]:
        problems.append(f"{page.tags['img']} <img> element(s): the payload became an image or a raw tag")
    if foreign := sorted(set(page.tags) - REPORT_TAGS - set(STRUCTURE_TAGS) - set(control.tags) - {"img"}):
        problems.append("elements no report produces by itself: " + ", ".join(f"<{tag}>" for tag in foreign))
    for tag in STRUCTURE_TAGS:
        if page.tags[tag] > control.tags[tag]:
            problems.append(f"{page.tags[tag] - control.tags[tag]} extra <{tag}> element(s) "
                            f"({page.tags[tag]} against {control.tags[tag]} with plain words)")
    if extra := [href for href in page.links if href not in control.links]:
        problems.append("link(s) the payload created: " + ", ".join(repr(href) for href in extra[:3]))
    for table in page.tables:
        if wrong := sorted({row["cells"] for row in table["rows"]} - {table["columns"]}):
            problems.append(f"table rows with {wrong} cells under a header of {table['columns']}")
    if unknown := sorted(page.headers - control.headers):
        problems.append(f"table header the plain-words run does not have: {unknown[:2]}")
    for text, plain in zip(shown, plain_shown):
        seen, expected = page.text.count(text), control.text.count(plain)
        if text and seen < expected:
            problems.append(f"visible text {text!r} appears {seen} time(s), the plain words {expected}: "
                            "part of it was consumed as Markdown syntax")
    return problems


_CONTROLS: dict = {}  # (surface, field) -> (Page, shown strings) of the plain-words run, built once


def control_run(surface, field, tmp_path, request):
    if (surface, field) not in _CONTROLS:
        report, shown = produce(surface, field, PAYLOADS["plain"], tmp_path, request, "control")
        page = Page(render(report))
        for text in shown:
            assert page.text.count(text), f"{surface} does not show {text!r} for the field {field!r}"
        _CONTROLS[surface, field] = page, shown
    return _CONTROLS[surface, field]


@pytest.mark.parametrize("surface, field, payload", cases(SURFACES))
def test_report_renders_hostile_text_inert_under_gfm(tmp_path, request, surface, field, payload):
    control, plain_shown = control_run(surface, field, tmp_path, request)
    report, shown = produce(surface, field, PAYLOADS[payload], tmp_path, request, "hostile")
    problems = page_problems(Page(render(report)), control, shown, plain_shown)
    assert not problems, "\n".join(problems)


