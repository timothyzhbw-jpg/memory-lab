"""`--format html`: one self-contained page per report, every untrusted value escaped, byte-for-byte stable.

Every page is checked by a strict parser: only the tags and attributes the renderers use, one style sheet (the
shared one), a Content-Security-Policy before it, links only to ``#anchors`` that exist or to http(s) URLs
with ``rel="noopener noreferrer"``, no hidden characters left in the text a browser would show, and nothing
but ASCII in the file.
"""
import json
import os
import shutil
import unicodedata
from datetime import date, datetime, timezone
from html.parser import HTMLParser
from pathlib import Path

import pytest
from typer.testing import CliRunner

from conftest import EXAMPLES, snapshot
from memcore import ManifestEntry, htmlpage, locate_quote
from memfalsify.audit import audit as memory_audit
from memfalsify.cli import app as memfalsify_app
from memfalsify.corpus import scan
from memfalsify.history import memory_diff
from memfalsify.htmlreport import diff_to_html, to_html
from memfalsify.memory import claim_id
from orgmemory.audit import audit as decision_audit
from orgmemory.cli import app as orgmemory_app
from orgmemory.htmlreport import audit_html, replay_html
from orgmemory.replay import Dataset, replay

runner = CliRunner()
AS_OF = date(2026, 10, 10)
OCT_31 = datetime(2026, 10, 31, tzinfo=timezone.utc)
POSIX = os.name != "nt"  # Windows forbids < > " in file names

TAGS = {"html", "head", "meta", "title", "style", "body", "main", "h1", "h2", "h3", "h4", "p", "a", "code", "strong",
        "span", "ul", "li", "dl", "dt", "dd", "div", "table", "caption", "thead", "tbody", "tfoot", "tr", "th",
        "td", "blockquote", "section", "article", "time"}
ATTRIBUTES = {"html": {"lang"}, "meta": {"charset", "name", "content", "http-equiv"}, "a": {"href", "rel"},
              "span": {"class"}, "code": {"class"}, "div": {"class"}, "dl": {"class"}, "p": {"class"}, "ul": {"class"},
              "td": {"class"}, "th": {"scope"}, "section": {"id"}, "article": {"class"}, "time": {"datetime"}}
PREFIX = '<!DOCTYPE html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n<meta name="generator" content="'
CSP_TAG = f'<meta http-equiv="Content-Security-Policy" content="{htmlpage.CSP}">'

# Text a hostile memory, evidence file, review, event log or policy may contain.
SCRIPT = "<script>alert(1)</script>"
ATTRIBUTE = '"><img src=x onerror=alert(2)>'
STYLE_BREAK = "</style><style>*{display:none}</style>"
JS_URL = "javascript:alert(3)"
HIDDEN_TEXT = "admin\u202eevil\u202c\u200bzero\U000e0041tag\x1b[31m"
MARKERS = ["U+202E", "U+202C", "U+200B", "U+E0041", "U+001B"]
# Default-ignorable characters whose category is not Cc/Cf/Cs/Zl/Zp. Listed here, not taken from
# htmlpage, so dropping one from the renderer still fails.
INVISIBLE = frozenset({
    0x034F, 0x115F, 0x1160, 0x17B4, 0x17B5, 0x180B, 0x180C, 0x180D, 0x180F, 0x3164, 0xFFA0,
    *range(0xFE00, 0xFE10), *range(0xE0100, 0xE01F0),
})


class Page(HTMLParser):
    """Parse a report page and fail on anything outside what the renderers may produce."""

    def __init__(self, document: str):
        super().__init__(convert_charrefs=True)
        self.stack: list[str] = []
        self.doctype = None
        self.text: list[str] = []
        self.styles: list[str] = []
        self.metas: list[dict] = []
        self.links: list[dict] = []
        self.ids: set[str] = set()
        self.counts: dict[str, int] = {}
        self.feed(document)
        self.close()
        assert self.stack == [], self.stack

    def handle_decl(self, decl):
        assert self.doctype is None and not self.counts
        self.doctype = decl

    def handle_starttag(self, tag, attrs):
        assert tag in TAGS, tag
        names = [name for name, _ in attrs]
        assert len(names) == len(set(names)) and set(names) <= ATTRIBUTES.get(tag, set()), (tag, attrs)
        values = dict(attrs)
        if tag == "meta":
            self.metas.append(values)
        else:
            self.stack.append(tag)
        if tag == "a":
            self.links.append(values)
        if "id" in values:
            assert values["id"] not in self.ids
            self.ids.add(values["id"])
        if tag == "th" and "scope" not in values:
            raise AssertionError("a header cell without scope")
        self.counts[tag] = self.counts.get(tag, 0) + 1

    def handle_startendtag(self, tag, attrs):
        raise AssertionError(f"self-closing <{tag}/>")

    def handle_endtag(self, tag):
        assert self.stack and self.stack[-1] == tag, (self.stack, tag)
        self.stack.pop()

    def handle_data(self, data):
        (self.styles if self.stack and self.stack[-1] == "style" else self.text).append(data)

    def handle_comment(self, data):
        raise AssertionError("comment in a report")

    def handle_pi(self, data):
        raise AssertionError("processing instruction in a report")

    def unknown_decl(self, data):
        raise AssertionError("unknown declaration in a report")

    @property
    def visible(self) -> str:
        return "".join(self.text)


def check(document: str, generator: str) -> Page:
    """Everything a report page must satisfy, whatever data went into it."""
    assert document.isascii()
    assert document.startswith(PREFIX + generator + '">\n' + CSP_TAG + "\n")
    assert document.endswith("</main>\n</body>\n</html>\n")
    page = Page(document)
    assert page.doctype == "DOCTYPE html"
    assert page.styles == ["\n" + htmlpage.STYLE] and page.counts["style"] == 1
    assert {"charset": "utf-8"} in page.metas and {"name": "referrer", "content": "no-referrer"} in page.metas
    assert {"name": "viewport", "content": "width=device-width, initial-scale=1"} in page.metas
    assert document.index(CSP_TAG) < document.index("<style>") < document.index("<body>")
    for link in page.links:
        href = link["href"]
        if href.startswith("#"):
            assert href[1:] in page.ids and "rel" not in link
        else:
            assert htmlpage.safe_url(href) == href and href.lower().startswith(("http://", "https://"))
            assert link["rel"] == "noopener noreferrer"
    hidden = {c for c in page.visible if unicodedata.category(c) in htmlpage.HIDDEN and c not in "\n\t"}
    assert not hidden, sorted(map(hex, map(ord, hidden)))
    invisible = {c for c in page.visible if ord(c) in INVISIBLE}
    assert not invisible, sorted(map(hex, map(ord, invisible)))
    assert page.counts.get("table", 0) == page.counts.get("caption", 0) == page.counts.get("thead", 0)
    assert page.counts["h1"] == 1 and page.counts["main"] == 1
    return page


# --- golden pages for the examples -----------------------------------------------------------------

def memfalsify_page(name: str, monkeypatch, tmp_path) -> str:
    root = shutil.copytree(EXAMPLES / name, tmp_path / name)
    monkeypatch.chdir(root)
    reviews = Path("reviews.jsonl") if Path("reviews.jsonl").exists() else None
    return to_html(memory_audit(Path("MEMORY.md"), Path("evidence"), reviews, as_of=AS_OF))


@pytest.mark.parametrize("name", ["memfalsify", "memfalsify-zh"])
def test_memfalsify_examples_match_their_html_reports(name, monkeypatch, tmp_path):
    page = memfalsify_page(name, monkeypatch, tmp_path)
    assert page.encode("ascii") == (EXAMPLES / name / "report.html").read_bytes()
    check(page, "memfalsify")


@pytest.mark.parametrize("dataset, prefix", [("feature-a", ""), ("github-acme-web", "github-acme-web/")])
def test_orgmemory_examples_match_their_html_reports(dataset, prefix):
    data = Dataset.load(EXAMPLES / "orgmemory" / dataset)
    replayed, audited = replay_html(replay(data)), audit_html(decision_audit(data, OCT_31))
    assert replayed.encode("ascii") == (EXAMPLES / "orgmemory" / f"{prefix}replay-report.html").read_bytes()
    assert audited.encode("ascii") == (EXAMPLES / "orgmemory" / f"{prefix}audit-report.html").read_bytes()
    check(replayed, "orgmemory")
    check(audited, "orgmemory")


def test_example_pages_carry_the_markdown_content(monkeypatch, tmp_path):
    page = Page(memfalsify_page("memfalsify", monkeypatch, tmp_path))
    text = page.visible
    for expected in ("CHALLENGED", "POSSIBLE COUNTEREVIDENCE", "m-27e874e0", "notes/2026-10-07-decision-log.md:3",
                     "86dc52dbbfe2802b7196c7b834bfba22807efead21f2371bea68875fc653ad47", "confirmed by demo-reviewer",
                     "Product manager rejects Feature A (bulk export) for the November release.",
                     "media/whiteboard.png", "Proposed actions (not applied)", "Missing evidence means unverified"):
        assert expected in text
    assert {"status-challenged", "status-supported"} <= page.ids
    data = Dataset.load(EXAMPLES / "orgmemory" / "github-acme-web")
    page = Page(replay_html(replay(data)))
    review = "https://github.com/acme/web/pull/6#pullrequestreview-601"
    assert {"href": review, "rel": "noopener noreferrer"} in page.links
    assert "Decision trail" in page.visible and "subject-13" in page.ids
    page = Page(audit_html(decision_audit(Dataset.load(EXAMPLES / "orgmemory" / "feature-a"), OCT_31)))
    assert "ev-thu-sales-approve" in page.visible and "synthetic://chat/sales/2026-10-08" in page.visible
    assert {"severity-medium"} == {i for i in page.ids if i.startswith("severity-")}
    assert not any(link["href"].startswith("synthetic") for link in page.links)


def test_cli_html_matches_the_examples(monkeypatch, tmp_path):
    root = Path(shutil.copytree(EXAMPLES / "memfalsify", tmp_path / "demo"))
    monkeypatch.chdir(root)
    args = ["audit", "MEMORY.md", "--evidence", "evidence", "--reviews", "reviews.jsonl", "--as-of", "2026-10-10"]
    result = runner.invoke(memfalsify_app, [*args, "--format", "html"])
    assert result.exit_code == 0 and result.output == (root / "report.html").read_text(encoding="utf-8")
    result = runner.invoke(memfalsify_app, [*args, "-f", "html", "--strict", "-o", "out.html"])
    assert result.exit_code == 1 and (root / "out.html").read_bytes() == (root / "report.html").read_bytes()
    org = EXAMPLES / "orgmemory"
    result = runner.invoke(orgmemory_app, ["replay", str(org / "feature-a"), "--format", "html"])
    assert result.exit_code == 0 and result.output == (org / "replay-report.html").read_text(encoding="utf-8")
    result = runner.invoke(orgmemory_app, ["audit", str(org / "feature-a"), "--as-of", "2026-10-31T00:00:00Z",
                                           "-f", "html"])
    assert result.exit_code == 0 and result.output == (org / "audit-report.html").read_text(encoding="utf-8")


def test_html_is_offered_only_where_documented(memfalsify_demo):
    memory = str(memfalsify_demo / "MEMORY.md")
    result = runner.invoke(memfalsify_app, ["history", memory, "--format", "html"])
    assert result.exit_code == 2 and "html" in result.output
    for command in (["claims", memory], ["rollback", memory, "--claim", "m-1", "--to", "HEAD"]):
        result = runner.invoke(memfalsify_app, [*command, "--format", "html"])
        assert result.exit_code == 2 and "No such option" in result.stderr
    result = runner.invoke(memfalsify_app, ["audit", "--help"])
    assert "html" in result.output
    result = runner.invoke(memfalsify_app, ["diff", "--help"])
    assert "html" in result.output
    result = runner.invoke(orgmemory_app, ["replay", "--help"])
    assert "html" in result.output
    result = runner.invoke(orgmemory_app, ["audit", "--help"])
    assert "html" in result.output
    for command in (["explain", "--help"], ["validate", "--help"]):
        result = runner.invoke(orgmemory_app, command)
        assert "--format" not in result.output


def test_html_output_never_overwrites_memory(memfalsify_demo):
    memory = memfalsify_demo / "MEMORY.md"
    before = memory.read_bytes()
    result = runner.invoke(memfalsify_app, ["audit", str(memory), "-f", "html", "-o", str(memory)])
    assert result.exit_code == 2 and memory.read_bytes() == before


def test_html_reports_are_not_evidence(memfalsify_demo):
    """A MemFalsify page quotes the memory; scanned as evidence it would support every claim it lists."""
    report = memory_audit(memfalsify_demo / "MEMORY.md", memfalsify_demo, as_of=AS_OF)
    cited = {e.span.path for f in report.findings for e in f.evidence}
    assert "report.html" not in cited
    assert {"path": "report.html", "reason": "MemFalsify report: a report cannot be its own evidence"} in [
        s.model_dump() for s in report.skipped_evidence]
    other = memfalsify_demo / "evidence" / "page.html"
    other.write_bytes(b"<!DOCTYPE html>\n<p>The public API rate limit is 60 requests per minute.</p>\n")
    passages, skipped = scan(memfalsify_demo / "evidence")
    assert "page.html" in {p.span.path for p in passages} and "page.html" not in {s.path for s in skipped}


# --- hostile input ---------------------------------------------------------------------------------

def hostile_memory(root: Path) -> Path:
    claims = [f"The cache uses Redis {SCRIPT} today.", f"Deploys go to staging {ATTRIBUTE} always.",
              f"The gateway runs at {JS_URL} per {STYLE_BREAK} docs.", f"The {HIDDEN_TEXT} owns payments."]
    (root / "evidence").mkdir(parents=True)
    (root / "MEMORY.md").write_bytes(
        (f"# Notes {SCRIPT}\n\n## Section {ATTRIBUTE}{HIDDEN_TEXT}\n\n" + "".join(f"- {c}\n" for c in claims))
        .encode("utf-8"))
    name = f"{SCRIPT}{ATTRIBUTE}.md".replace("/", "_") if POSIX else "notes.md"
    (root / "evidence" / name).write_bytes(
        "".join(c + "\n" for c in claims[:1] + claims[3:]).replace("today", "today and tomorrow").encode("utf-8"))
    (root / "evidence" / f"{STYLE_BREAK.replace('/', '_')}.png" if POSIX else root / "evidence" / "x.png").write_bytes(
        b"\x89PNG")
    review = {"memory_id": claim_id(claims[0]), "relation": "supports", "path": name, "quote": claims[0][:30],
              "reviewer": f"{ATTRIBUTE}{HIDDEN_TEXT}", "note": f"{STYLE_BREAK} {JS_URL}"}
    (root / "reviews.jsonl").write_bytes((json.dumps(review) + "\n").encode("utf-8"))
    return root


def assert_inert(page: Page, *payloads: str, markers: list[str] = MARKERS) -> None:
    """Hostile text shows up as text, as written (hidden characters as markers), and never as markup."""
    for payload in payloads:
        assert payload in page.visible, payload
    for marker in markers:
        assert marker in page.visible, marker
    assert not any("javascript" in link["href"].lower() for link in page.links)


def test_hostile_memory_evidence_and_reviews_render_as_text(tmp_path):
    root = hostile_memory(tmp_path / "hostile")
    report = memory_audit(root / "MEMORY.md", root / "evidence", root / "reviews.jsonl", as_of=AS_OF)
    assert any(e.confirmed for f in report.findings for e in f.evidence)
    page = check(to_html(report), "memfalsify")
    assert_inert(page, SCRIPT, ATTRIBUTE, STYLE_BREAK, JS_URL)
    if POSIX:
        assert f"{SCRIPT}{ATTRIBUTE}.md".replace("/", "_") in page.visible
    document = to_html(report)
    assert "<script" not in document and "<img" not in document and "<style>" in document
    assert document.count("<style>") == 1 and document.count("</style>") == 1
    # The JSON report keeps the original text; only the page neutralizes it.
    assert "\u202e" in report.model_dump_json()


def test_cli_writes_a_hostile_page_safely(tmp_path):
    root = hostile_memory(tmp_path / "hostile")
    out = tmp_path / "page.html"
    result = runner.invoke(memfalsify_app, ["audit", str(root / "MEMORY.md"), "-e", str(root / "evidence"),
                                            "-r", str(root / "reviews.jsonl"), "--as-of", "2026-10-10",
                                            "--format", "html", "--output", str(out)])
    assert result.exit_code == 0, result.output
    data = out.read_bytes()
    assert data.isascii() and b"\r" not in data
    assert_inert(check(data.decode("ascii"), "memfalsify"), SCRIPT, ATTRIBUTE, STYLE_BREAK, JS_URL)


SOURCES = {
    "js": JS_URL, "data": f"data:text/html,{SCRIPT}", "quote": 'https://example.com/"onmouseover="alert(1)',
    "angle": "https://example.com/<b>", "bidi": "https://exa\u202emple.com/", "space": " https://example.com/",
    "relative": "//example.com/x", "nohost": "http:///x", "synthetic": "synthetic://notes/1",
    "ok": "https://example.com/issues/1?a=1&b=2#c", "upper": "HTTP://EXAMPLE.COM/A",
    # Looks like google.com. The variation selector has no glyph, so it must not become a link.
    "selector": "https://goog\uFE0Fle.com/",
}


def hostile_dataset(root: Path) -> Path:
    """Every free-text field of a dataset carries a payload; evidence cites every kind of source URI."""
    root.mkdir(parents=True)
    file_name = f"{SCRIPT}.txt".replace("/", "_") if POSIX else "source.txt"
    quote = f"Approved {SCRIPT} {ATTRIBUTE} {STYLE_BREAK} {HIDDEN_TEXT}\r\nsecond line"
    (root / file_name).write_bytes(f"Header\n{quote}\n".encode("utf-8"))
    lines = []
    for key, uri in SOURCES.items():
        span = locate_quote(root, file_name, quote)
        entry = ManifestEntry(id=f"{key}{ATTRIBUTE}", path=span.path, sha256=span.sha256, quote=span.quote,
                              start=span.start, end=span.end, source_uri=uri, source_kind=f"kind{SCRIPT}",
                              captured_at="2026-10-01T00:00:00Z")
        lines.append(json.dumps(entry.model_dump(mode="json")) + "\n")
    (root / "evidence.jsonl").write_bytes("".join(lines).encode("utf-8"))
    pm, intruder = f"pm{ATTRIBUTE}", f"sales{HIDDEN_TEXT}"
    policy = {"actors": {pm: [f"product_manager{SCRIPT}"], "eng": ["engineer"]},
              "permissions": {"request": [f"product_manager{SCRIPT}", "engineer"],
                              "approve": [f"product_manager{SCRIPT}"], "raise_risk": ["engineer"],
                              "dependency": ["engineer"]}}
    (root / "policy.json").write_bytes(json.dumps(policy).encode("utf-8"))
    subject = f"Feature {SCRIPT}{HIDDEN_TEXT}\n## forged"
    ids = [f"{key}{ATTRIBUTE}" for key in SOURCES]
    events = [
        {"id": f"r{SCRIPT}", "timestamp": "2026-10-01T09:00:00Z", "actor": pm, "action": "request",
         "subject": subject, "evidence_ids": ids},
        {"id": f"a{STYLE_BREAK}", "timestamp": "2026-10-02T09:00:00Z", "actor": pm, "action": "approve",
         "subject": subject, "evidence_ids": ids},
        {"id": "k", "timestamp": "2026-10-03T09:00:00Z", "actor": "eng", "action": "raise_risk", "subject": subject,
         "evidence_ids": ids[:1], "note": f"{STYLE_BREAK}{JS_URL}{HIDDEN_TEXT}"},
        {"id": "d", "timestamp": "2026-10-03T10:00:00Z", "actor": "eng", "action": "dependency", "subject": subject,
         "evidence_ids": ids[:1], "dependency": f"Other {SCRIPT}"},
        {"id": "x1", "timestamp": "2026-10-04T09:00:00Z", "actor": intruder, "action": "approve",
         "subject": f"Other {SCRIPT}", "evidence_ids": ids[:1]},
        {"id": "x2", "timestamp": "2026-10-04T10:00:00Z", "actor": intruder, "action": "approve",
         "subject": subject, "evidence_ids": ids[:1]},
    ]
    (root / "events.jsonl").write_bytes("".join(json.dumps(e) + "\n" for e in events).encode("utf-8"))
    return root


def test_hostile_dataset_renders_as_text(tmp_path):
    data = Dataset.load(hostile_dataset(tmp_path / "data"))
    report = decision_audit(data, OCT_31)
    assert {f.kind for f in report.findings} >= {"risk_after_approval", "unresolved_dependency", "self_approval",
                                                 "unauthorized_attempts"}
    for page in (check(replay_html(replay(data)), "orgmemory"), check(audit_html(report), "orgmemory")):
        assert_inert(page, SCRIPT, ATTRIBUTE, STYLE_BREAK, JS_URL, "U+000A## forged", "second line")
        hrefs = [link["href"] for link in page.links if not link["href"].startswith("#")]
        assert set(hrefs) == {SOURCES["ok"], SOURCES["upper"]}
        for key in ("data", "quote", "angle", "space", "relative", "nohost", "synthetic"):
            assert SOURCES[key].strip() in page.visible
        assert "U+FE0F" in page.visible and "\uFE0F" not in page.visible
        assert "\r" not in page.visible and "U+000D" not in page.visible  # a CRLF in a quote is one line break


def test_cli_replay_and_audit_html_are_read_only(tmp_path):
    root = hostile_dataset(tmp_path / "data")
    before = snapshot(tmp_path)
    for command in (["replay", str(root)], ["audit", str(root), "--as-of", "2026-10-31T00:00:00Z", "--strict"]):
        result = runner.invoke(orgmemory_app, [*command, "--format", "html"])
        assert result.exit_code == (1 if "--strict" in command else 0), result.output
        assert_inert(check(result.output, "orgmemory"), SCRIPT, ATTRIBUTE, STYLE_BREAK)
    assert snapshot(tmp_path) == before


@pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")
def test_diff_html_escapes_claims_and_is_not_evidence(git_repo):
    git_repo.commit("base", {"MEMORY.md": "# Memory\n\n- The cache uses Redis.\n- The queue runs on Kafka.\n"})
    git_repo.write({"MEMORY.md": "# Memory\n\n- The cache uses Redis\u202e for sessions.\n"
                                 f"- Deploys go to staging {ATTRIBUTE} always {SCRIPT}.\n",
                    "evidence/notes.md": f"Deploys go to staging {ATTRIBUTE} always, says {STYLE_BREAK}.\n"})
    memory, evidence = git_repo.root / "MEMORY.md", git_repo.root / "evidence"
    expected = diff_to_html(memory_diff(memory, "HEAD", evidence_root=evidence, as_of=AS_OF))
    out = evidence / "diff.html"
    result = runner.invoke(memfalsify_app, ["diff", str(memory), "--base", "HEAD", "-e", str(evidence),
                                            "--as-of", "2026-10-10", "--format", "html", "-o", str(out)])
    assert result.exit_code == 0, result.output
    assert out.read_bytes() == expected.encode("ascii")
    page = check(expected, "memfalsify")
    assert_inert(page, ATTRIBUTE, STYLE_BREAK, SCRIPT, "The queue runs on Kafka.", "RedisU+202E for sessions",
                 markers=["U+202E"])
    assert {"change-changed", "change-added", "change-removed"} <= page.ids
    assert "Audit of added and changed claims" in page.visible
    report = memory_diff(memory, "HEAD", evidence_root=evidence, as_of=AS_OF)
    assert any(s.path == "diff.html" and "report" in s.reason for s in report.audit.skipped_evidence)


# --- the building blocks ---------------------------------------------------------------------------

@pytest.mark.parametrize("uri", [SOURCES["ok"], SOURCES["upper"], "http://example.com", "https://example.com:8443/p",
                                 "https://例え.jp/パス"])
def test_safe_urls_become_links(uri):
    assert htmlpage.safe_url(uri) == uri
    assert htmlpage.link(uri).startswith('<a href="') and 'rel="noopener noreferrer">' in htmlpage.link(uri)


@pytest.mark.parametrize("uri", [*(v for k, v in SOURCES.items() if k not in ("ok", "upper")), "", None, 42,
                                 "JaVaScRiPt:alert(1)", "java\tscript:alert(1)", "https://example.com/\nx",
                                 "https://example.com\\@evil.test", "https://[::1", "ftp://example.com/",
                                 "https://example.com/\u200b", "vbscript:msgbox(1)", "https:example.com"])
def test_other_uris_stay_text(uri):
    assert htmlpage.safe_url(uri) is None
    assert "<a " not in htmlpage.link(uri)


def test_text_escapes_markup_and_marks_hidden_characters():
    assert htmlpage.text('<a href="x">&\'') == "&lt;a href=&quot;x&quot;&gt;&amp;&#x27;"
    marked = htmlpage.text("a\u202eb\u2066c\u200dd\x00e\x7ff\u2028g\ud800h\U000e0001i\tj")
    for code in ("202E", "2066", "200D", "0000", "007F", "2028", "D800", "E0001"):
        assert f'<span class="ctl">U+{code}</span>' in marked
    assert "\tj" in marked
    assert htmlpage.text("one\ntwo") == 'one<span class="ctl">U+000A</span>two'
    assert htmlpage.text("one\r\ntwo\rthree", lines=True) == 'one\ntwo<span class="ctl">U+000D</span>three'
    assert '<span class="ctl">U+2029</span>' in htmlpage.text("a\u2029b", lines=True)
    assert htmlpage.text("名前 é ✓") == "名前 é ✓"  # page() turns these into character references
    assert htmlpage.page("t", htmlpage.text("名"), "g").count("&#21517;") == 1


def test_default_ignorable_characters_cannot_hide_or_become_links():
    """Variation selectors and fillers have no glyph but are not control or format characters."""
    for code in sorted(INVISIBLE):
        char = chr(code)
        rendered = htmlpage.text(f"a{char}b")
        assert f"U+{code:04X}" in rendered and char not in rendered
        assert htmlpage.text(f"a{char}b", lines=True) == rendered
        uri = f"https://example.com/{char}"
        assert htmlpage.safe_url(uri) is None and "<a " not in htmlpage.link(uri)
    # A visible combining accent is not one of those characters.
    assert htmlpage.text("e\u0301") == "e\u0301"
    assert htmlpage.safe_url("https://example.com/e\u0301") == "https://example.com/e\u0301"
    document = htmlpage.page("t", "<h1>Report</h1>\n" + htmlpage.link("https://goog\uFE0Fle.com/"), "memfalsify")
    page = check(document, "memfalsify")
    assert page.links == [] and "U+FE0F" in page.visible and "google.com" not in page.visible


def _windows_stdout(message) -> bytes:
    """What a Windows text-mode console writes. ``newline='\\r\\n'`` translates str, not bytes."""
    import io
    raw = io.BytesIO()
    stream = io.TextIOWrapper(raw, encoding="utf-8", newline="\r\n")
    if isinstance(message, (bytes, bytearray)):
        stream.flush()
        stream.buffer.write(message)
        stream.buffer.flush()
    else:
        stream.write(message)
        stream.flush()
    return raw.getvalue()


def test_html_stdout_keeps_lf_when_windows_translates_newlines(tmp_path, monkeypatch):
    import memfalsify.cli as memfalsify_cli
    import orgmemory.cli as orgmemory_cli

    captured = []
    real_echo = memfalsify_cli.typer.echo

    def spy(message=None, nl=True, err=False, **kwargs):
        if not err:
            captured.append((message, nl))
        return real_echo(message, nl=nl, err=err, **kwargs)

    monkeypatch.setattr(memfalsify_cli.typer, "echo", spy)
    root = hostile_memory(tmp_path / "hostile")
    result = runner.invoke(memfalsify_app, ["audit", str(root / "MEMORY.md"), "-e", str(root / "evidence"),
                                            "-r", str(root / "reviews.jsonl"), "--as-of", "2026-10-10",
                                            "--format", "html"])
    assert result.exit_code == 0, result.output
    message, nl = captured[-1]
    assert nl is False and isinstance(message, bytes) and _windows_stdout(message) == message
    assert result.stdout_bytes == message

    captured.clear()
    data = EXAMPLES / "orgmemory" / "feature-a"
    result = runner.invoke(orgmemory_app, ["replay", str(data), "--format", "html"])
    assert result.exit_code == 0, result.output
    message, nl = captured[-1]
    assert nl is False and isinstance(message, bytes) and b"\r" not in _windows_stdout(message)
    assert result.stdout_bytes == message == (EXAMPLES / "orgmemory" / "replay-report.html").read_bytes()
    result = runner.invoke(orgmemory_app, ["audit", str(data), "--as-of", "2026-10-31T00:00:00Z", "-f", "html"])
    assert result.exit_code == 0, result.output
    message, nl = captured[-1]
    assert isinstance(message, bytes) and _windows_stdout(message) == message


def test_style_supports_dark_mode_and_narrow_screens():
    style = htmlpage.STYLE
    assert "@media (prefers-color-scheme: dark)" in style and "color-scheme: light dark" in style
    assert "@media (max-width: 40rem)" in style and ".scroll { overflow-x: auto;" in style
    assert "url(" not in style and "@import" not in style and "@font-face" not in style
