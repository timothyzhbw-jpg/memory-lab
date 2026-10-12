"""memlab-ui: every page on the repository's examples, path confinement, Host checks, escaping of hostile text,
no writes, model-verifier gating and the HTTP side, all offline.

Most tests call ``App.handle`` directly. The ``served`` tests start the real ``http.server`` on an ephemeral
port in a thread and talk to it over loopback only (the suite's no-network guard stays on for every other
address). The UI refuses to start without POSIX confined reads, so those tests are skipped where it is missing.
"""
import ast
import fnmatch
import hashlib
import html
import importlib
import json
import re
import shutil
import socket
import threading
import time
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import quote as urlquote

import pytest
from typer.testing import CliRunner

from conftest import EXAMPLES, requires_confinement, snapshot, write_text_exact
from memcore import confinement_root, reading
from memfalsify import ATTENTION, LexicalVerifier, VerifierError, audit, parse_memory
from memlab_ui import create_server, i18n
from memlab_ui import check as ui_check
from memlab_ui import decisions as ui_decisions
from memlab_ui import home as ui_home
from memlab_ui import pages as ui_pages
from memlab_ui import server as ui_server
from memlab_ui.pages import STATIC
from memlab_ui.server import CSP, App
from orgmemory import Dataset, changes, replay
from orgmemory import audit as org_audit

# Captured at import, before the suite's autouse guard replaces them for each test.
_CONNECT = socket.socket.connect
_CREATE_CONNECTION = socket.create_connection
PORT = 8765
HOST = f"127.0.0.1:{PORT}"
SECRET = "outside-the-root secret: do not read"
HEADER_CSP = ("default-src 'none'; img-src 'self'; style-src 'unsafe-inline'; form-action 'self'; base-uri 'none'; "
              "frame-ancestors 'none'")
# The only things a page loads: the logo (light and dark) and the favicons, all from /static/.
LOADED = ['<link rel="icon" type="image/png" sizes="32x32" href="/static/memory-lab-icon-32.png">',
          '<link rel="icon" type="image/png" sizes="64x64" href="/static/memory-lab-icon-64.png">',
          '<source srcset="/static/ui-dark.png" media="(prefers-color-scheme: dark)">',
          '<img src="/static/ui-light.png" alt="Memory Lab" width="145" height="24">']
MEMORY, EVIDENCE, REVIEWS = "memfalsify/MEMORY.md", "memfalsify/evidence", "memfalsify/reviews.jsonl"
runner = CliRunner()


@pytest.fixture
def root(tmp_path):
    """A project folder with copies of the examples, next to files the UI must never read."""
    base = tmp_path / "root"
    shutil.copytree(EXAMPLES / "memfalsify", base / "memfalsify")
    shutil.copytree(EXAMPLES / "orgmemory" / "feature-a", base / "feature-a")
    shutil.copytree(EXAMPLES / "orgmemory" / "github-acme-web", base / "github-acme-web")
    write_text_exact(base / "AGENTS.md", "# Agents\n\n- Run the tests with pytest.\n"
                                         "- The public API rate limit is 100 requests per minute.\n")
    write_text_exact(tmp_path / "outside.md", f"- {SECRET}.\n")
    shutil.copytree(EXAMPLES / "orgmemory" / "feature-a", tmp_path / "outside-log")
    return base


def make_app(root, **options) -> App:
    app = App(root, **options)
    app.port = PORT
    return app


@pytest.fixture
def app(root):
    return make_app(root)


def get(app, target, *, host=HOST, method="GET", client="127.0.0.1"):
    headers = [] if host is None else [("Host", host)]
    return app.handle(method, target, headers, client)


def body(response) -> str:
    return response.body.decode("ascii")


def text(response) -> str:
    """The visible words: no style, no tags, entities decoded, whitespace collapsed."""
    page = re.sub(r"<style>.*?</style>", " ", body(response), flags=re.S)
    page = re.sub(r"</?(?:code|mark|strong|time)\b[^>]*>", "", page)  # inline: no word break
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", page))).strip()


def without_logo(page: str) -> str:
    for markup in LOADED:
        page = page.replace(markup, "")
    return page


def link(path, **params) -> str:
    return path + "?" + "&".join(f"{key}={urlquote(value, safe='')}" for key, value in params.items())


# --- pages on the examples ---------------------------------------------------------------------------------

@requires_confinement
def test_home_explains_and_lists_the_agent_files(app, root):
    response = get(app, "/")
    assert response.status == 200
    shown = text(response)
    assert "Is your AI assistant's memory still true?" in shown
    assert "Nothing is changed and nothing leaves this computer." in shown
    assert "Which file should I check?" in shown and "Start here" in shown
    assert "Pick a file." in shown and "Press Check." in shown and "Look at what's flagged." in shown
    assert "Agent files in this project" in shown and "See team decisions" in shown
    claims, _ = parse_memory(root / MEMORY)
    assert re.search(rf"{re.escape(MEMORY)} Agent memory {len(claims)} Check", shown)
    assert re.search(r"AGENTS\.md Codex and other AGENTS\.md readers 2 Check", shown)
    assert 'href="/check?file=memfalsify%2FMEMORY.md"' in body(response)
    assert '<form class="card composer" method="get" action="/check">' in body(response)


@requires_confinement
def test_check_form_offers_files_folders_and_reviews(app):
    response = get(app, "/check")
    page = body(response)
    assert response.status == 200
    assert '<select id="f-file" name="file" required>' in page
    assert '<option value="." selected>The whole project (recommended)</option>' in page
    assert '<option value="memfalsify/evidence">Only memfalsify/evidence/</option>' in page
    assert '<option value="memfalsify/reviews.jsonl">memfalsify/reviews.jsonl</option>' in page
    assert "<details><summary>More options</summary>" in page  # folded until an option is used
    assert 'id="results"' not in page
    assert '"Changed since" works when the project folder is a git repository.' in html.unescape(page)
    assert 'name="verifier"' not in page


@requires_confinement
def test_check_shows_the_lexical_audit_in_plain_words(app, root):
    response = get(app, link("/check", file=MEMORY, evidence=EVIDENCE, reviews=REVIEWS, as_of="2026-10-10"))
    assert response.status == 200
    expected = audit(root / MEMORY, root / EVIDENCE, root / REVIEWS, as_of=date(2026, 10, 10))
    flagged = [f for f in expected.findings if f.status in ATTENTION]
    fine = [f for f in expected.findings if f.status in ("supported", "candidate_support")]
    unknown = [f for f in expected.findings if f.status == "insufficient_evidence"]
    shown, page = text(response), body(response)
    assert f"{len(flagged)} things in MEMORY.md may be out of date" in shown
    assert f"Look at these {len(flagged)} Look at these Something in your project seems to disagree." in shown
    assert (f"Similar text found {len(fine)} Similar text found Your project has a passage that looks alike. "
            "It's a word match, not proof.") in shown
    assert "Probably fine" not in shown
    assert f"Can't tell {len(unknown)} Can't tell Nothing in your project talks about it." in shown
    assert page.count('<article class="card finding">') == len(flagged)
    for finding in expected.findings:
        assert finding.text in shown
    assert "Your agent file says · infra.md:6" in shown
    assert "Your docs say · api.md:3" in shown
    assert "<mark>100</mark>" in page and "<mark>60</mark>" in page  # the differing values
    assert "The numbers are different." in shown
    assert "demo-reviewer confirmed this in a review." in shown
    assert "A reviewer confirmed the source is right: update or remove this line in MEMORY.md." in shown
    assert f"Show the {len(fine) + len(unknown)} other statements" in shown
    assert "the quick check compares words" in shown and "nothing was changed" in shown
    assert "1 file was not read" in shown  # the whiteboard photo


@requires_confinement
def test_check_compares_with_the_whole_project_by_default(app):
    response = get(app, "/check?file=AGENTS.md")
    shown = text(response)
    assert response.status == 200
    assert "compared with the whole project" in shown
    assert "1 thing in AGENTS.md may be out of date" in shown
    assert "Your docs say · api.md:3" in shown


@requires_confinement
def test_reviews_that_do_not_fit_the_folder_are_a_plain_error(app):
    response = get(app, link("/check", file=MEMORY, reviews=REVIEWS))
    shown = text(response)
    assert response.status == 400
    assert "We couldn't run this check" in shown and "Reviews must point into the folder" in shown
    assert 'name="file"' in body(response)  # the form stays, with the choices made
    assert "Traceback" not in shown


@pytest.fixture
def repo(git_repo):
    git_repo.commit("start", {"CLAUDE.md": "# Notes\n\n- The API rate limit is 60 requests per minute.\n"
                                           "- Run the tests with pytest.\n- Old rule to drop.\n",
                              "docs/api.md": "The API rate limit is 60 requests per minute.\n"})
    git_repo.write({"CLAUDE.md": "# Notes\n\n- The API rate limit is 100 requests per minute.\n"
                                 "- Run the tests with pytest.\n- Deploy only on Tuesdays.\n"})
    return git_repo


@requires_confinement
@pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")
def test_changed_since_runs_the_diff_inside_a_git_root(repo, monkeypatch):
    app = make_app(repo.root)
    assert 'name="since"' in body(get(app, "/check"))
    before = snapshot(repo.root)
    response = get(app, "/check?file=CLAUDE.md&since=HEAD")
    shown = text(response)
    assert response.status == 200, shown
    assert "1 thing in CLAUDE.md may be out of date" in shown
    assert "Since HEAD: 1 added, 1 changed, 1 removed, 1 unchanged (not checked again)" in shown
    assert "Changed" in shown and "New" in shown
    assert "It used to say · CLAUDE.md:3 The API rate limit is 60 requests per minute." in shown
    assert "1 statement was removed" in shown and "Old rule to drop." in shown
    assert "<mark>100</mark>" in body(response)
    assert snapshot(repo.root) == before
    for since, message in (("-x", "must be a branch, tag or commit name"),
                           ("no-such-branch", "")):
        refused = get(app, link("/check", file="CLAUDE.md", since=since))
        assert refused.status == 400 and message in text(refused)
        assert "fatal:" not in text(refused) and str(repo.root.parent) not in text(refused).replace(
            str(repo.root), "")
    monkeypatch.setattr(ui_check, "MAX_STATEMENTS", 2)
    too_many = get(app, "/check?file=CLAUDE.md&since=HEAD")
    assert too_many.status == 400 and "more than 2 statements" in text(too_many)


@requires_confinement
def test_changed_since_needs_a_git_root(app):
    response = get(app, link("/check", file=MEMORY, since="main"))
    assert response.status == 400
    assert "works only when the project folder is a git repository" in text(response)


@requires_confinement
def test_decisions_page_lists_the_logs(app):
    response = get(app, "/decisions")
    page = body(response)
    assert response.status == 200
    assert '<option value="feature-a" selected>feature-a</option>' in page  # the first log opens directly
    assert '<option value="github-acme-web">github-acme-web</option>' in page
    assert 'class="topics"' in page and "Feature A" in text(response)


@requires_confinement
def test_decisions_explain_a_topic_like_the_replay(app, root):
    response = get(app, "/decisions?dataset=feature-a&as_of=2026-10-31")
    shown, page = text(response), body(response)
    assert response.status == 200
    data = Dataset.load(root / "feature-a")
    cutoff = datetime(2026, 10, 31, 23, 59, 59, tzinfo=timezone.utc)
    state = replay(data, cutoff).states["Feature A"]
    findings = org_audit(data, cutoff).findings
    assert state.current_status == "rejected"
    assert f"{len(findings)} things need attention." in shown
    assert "Feature A Rejected Rejected by zhou (product manager) on 2026-10-07 15:30 UTC" in shown
    assert '<blockquote class="big-quote">Feature A is rejected for the November release' in page
    assert "Waiting for a decision from product manager. Someone asked to reconsider." in shown
    assert "Bulk export reads the billing schema, which is mid-migration" in shown
    assert "How we got here" in shown
    assert "buyer@acme (customer) asked for it Noted" in shown
    assert "zhou (product manager) rejected it Counted" in shown
    assert "wu (account manager) approved it Not counted: account manager can't approve" in shown
    assert page.count('<li class="counted">') == 1 and page.count('<li class="uncounted">') == 4
    assert "wu tried 1 time to do something their role does not allow (approve) on Feature A." in shown
    assert "and no decision has followed for more than 14 days" in shown
    assert '<nav class="topics" aria-label="Topics">' in page and 'aria-current="true"' in page


@requires_confinement
def test_every_topic_link_opens_its_topic(app, root):
    page = body(get(app, "/decisions?dataset=github-acme-web"))
    links = re.findall(r'<li><a href="(/decisions\?[^"]+)#detail"', page)
    states = replay(Dataset.load(root / "github-acme-web")).states
    assert len(links) == len(states) == 13
    for target, subject in zip(links, states):
        response = get(app, html.unescape(target))
        assert response.status == 200
        assert f'<h2 id="detail-title" class="flush subject-title">{html.escape(subject)} ' in body(response)


@requires_confinement
def test_changes_between_two_dates(app, root):
    response = get(app, "/decisions?dataset=feature-a&from=2026-10-05&to=2026-10-08")
    shown = text(response)
    assert response.status == 200
    report = changes(Dataset.load(root / "feature-a"), datetime(2026, 10, 5, 23, 59, 59, tzinfo=timezone.utc),
                     datetime(2026, 10, 8, 23, 59, 59, tzinfo=timezone.utc))
    assert f"{report.events_in_period} events in this period; 1 topic changed." in shown
    assert "Feature A Waiting → Rejected" in shown
    assert "Decided: Waiting → Rejected" in shown
    assert "Started waiting for a decision" in shown and "Someone asked to reconsider" in shown
    assert "Not counted: someone tried to decide without the right role" in shown
    for query, message in (("from=2026-10-09&to=2026-10-05", "must not be after"),
                           ("from=2026-10-05", "Fill in both dates"),
                           ("from=yesterday&to=2026-10-05", "must be a date")):
        refused = get(app, f"/decisions?dataset=feature-a&{query}")
        assert refused.status == 400 and message in text(refused)


@requires_confinement
def test_unknown_topic_and_bad_dates(app):
    response = get(app, "/decisions?dataset=feature-a&subject=Nope")
    assert response.status == 404 and "Unknown topic" in text(response)
    response = get(app, "/decisions?dataset=feature-a&as_of=soon")
    assert response.status == 400 and "must be a date" in text(response)
    response = get(app, link("/check", file=MEMORY, as_of="10/10/2026"))
    assert response.status == 400 and "must be a date like 2026-10-10" in text(response)


# --- confinement ---------------------------------------------------------------------------------------

@requires_confinement
def test_paths_that_leave_the_root_are_refused(app, root, tmp_path, make_symlink):
    make_symlink(root / "link.md", tmp_path / "outside.md")
    (tmp_path / "outside-dir").mkdir()
    write_text_exact(tmp_path / "outside-dir" / "notes.md", f"{SECRET}\n")
    make_symlink(root / "linked-dir", tmp_path / "outside-dir", directory=True)
    make_symlink(root / "linked-log", tmp_path / "outside-log", directory=True)
    outside = str(tmp_path / "outside.md")
    cases = [("/check", "file", value) for value in
             (outside, "../outside.md", "memfalsify/../../outside.md", "link.md")]
    cases += [("/check", "evidence", value) for value in ("..", str(tmp_path), "linked-dir", "memfalsify/../..")]
    cases += [("/check", "reviews", value) for value in ("../outside.md", "link.md", outside)]
    cases += [("/decisions", "dataset", value) for value in
              ("..", "../outside-log", str(tmp_path / "outside-log"), "linked-log")]
    for path, field, value in cases:
        params = {"file": MEMORY, field: value} if path == "/check" else {field: value}
        response = get(app, link(path, **params))
        assert response.status == 400, (field, value)
        page = body(response)
        assert "outside the folder this page can read" in page, (field, value)
        assert SECRET not in page and "outside.md" not in page and "outside-log" not in page
        assert "outside-dir" not in page and "linked-" not in page.split('id="results"')[0].split("<form")[0]


@requires_confinement
def test_hidden_and_skipped_folders_are_not_read(app, root):
    (root / ".git").mkdir()
    write_text_exact(root / ".git" / "config", f"[core]\n# {SECRET}\n")
    (root / "node_modules").mkdir()
    write_text_exact(root / "node_modules" / "CLAUDE.md", f"- {SECRET}.\n")
    for target in ("/check?file=.git/config", "/check?file=node_modules/CLAUDE.md",
                   f"/check?file={MEMORY}&evidence=.git"):
        response = get(app, target)
        assert response.status == 400 and SECRET not in body(response)
        assert "is not read" in text(response)
    assert "node_modules" not in body(get(app, "/"))


@requires_confinement
def test_pages_read_inside_confined_reads(app, root, monkeypatch):
    seen = []

    original = ui_check.audit_claims

    def spy(*args, **kwargs):
        seen.append(confinement_root())
        return original(*args, **kwargs)

    monkeypatch.setattr(ui_check, "audit_claims", spy)
    assert get(app, link("/check", file=MEMORY, evidence=EVIDENCE)).status == 200
    assert seen == [root.resolve()]


# --- the Host header, methods and the HTTP side --------------------------------------------------------------

@requires_confinement
@pytest.mark.parametrize("host", [None, "", "evil.example", f"evil.example:{PORT}", "127.0.0.1:1",
                                  f"localhost.:{PORT}", f"127.0.0.1:{PORT}.evil.example", f"[::1]:{PORT}",
                                  f"127.0.0.2:{PORT}", "127.0.0.1", "localhost"])
def test_other_host_names_are_refused(app, host):
    response = get(app, "/", host=host)
    assert response.status == 403
    assert "answers only at http://127.0.0.1 or http://localhost" in text(response)
    assert "evil" not in body(response)
    assert "Agent files in this project" not in text(response)


@requires_confinement
def test_loopback_names_with_the_port_are_accepted(app):
    for host in (HOST, f"localhost:{PORT}", f"LocalHost:{PORT}"):
        assert get(app, "/", host=host).status == 200
    two = app.handle("GET", "/", [("Host", HOST), ("Host", "evil.example")], "127.0.0.1")
    assert two.status == 403
    assert get(app, "/", client="10.0.0.5").status == 403


@requires_confinement
def test_only_get_and_head(app):
    for method in ("POST", "PUT", "DELETE", "PATCH", "OPTIONS"):
        response = get(app, "/check", method=method)
        assert response.status == 405 and ("Allow", "GET, HEAD") in response.headers
    assert get(app, "/", method="HEAD").status == 200


@requires_confinement
def test_bad_requests_and_unknown_pages(app):
    cases = {"/check?file=a&file=b": 400, "/check?file=" + "a" * 5000: 400, "http://evil.example/": 400,
             "/check?" + "&".join(f"k{i}=v" for i in range(40)): 400, "/nope": 404, "/static/": 404,
             "/static/../server.py": 404, "/static/mark-128.png": 404, "/static/py.typed": 404,
             "/check/../decisions": 404}
    for target, status in cases.items():
        response = get(app, target)
        assert response.status == status, target
        assert "evil" not in body(response) and "aaaa" not in body(response)


@requires_confinement
def test_security_headers_and_a_strict_page(app):
    for target in ("/", "/check", f"/check?file={MEMORY}", "/decisions?dataset=feature-a", "/nope"):
        response = get(app, target)
        headers = dict(response.headers)
        assert headers["Content-Security-Policy"] == HEADER_CSP
        assert headers["X-Content-Type-Options"] == "nosniff"
        assert headers["X-Frame-Options"] == "DENY"
        assert headers["Referrer-Policy"] == "no-referrer"
        assert headers["Cache-Control"] == "no-store"
        assert headers["Content-Type"] == "text/html; charset=utf-8"
        page = body(response)
        assert f'<meta http-equiv="Content-Security-Policy" content="{CSP}">' in page
        assert CSP == "default-src 'none'; img-src 'self'; style-src 'unsafe-inline'; form-action 'self'; base-uri 'none'"
        assert re.findall(r"<(?:img|link|source|picture|video|audio|object|embed)\b[^>]*>", page) == [
            *LOADED[:2], "<picture>", *LOADED[2:]]
        lowered = without_logo(page).lower()
        for loaded in ("<script", "<link", "<img", "<iframe", "src=", "srcset=", "@import", "url(", "@font-face"):
            assert loaded not in lowered, (target, loaded)
        assert not re.search(r'action="(?!/)', page)  # forms submit to this server only
        assert all(form == 'method="get"' for form in re.findall(r'<form[^>]*?(method="[a-z]+")', page))


@requires_confinement
def test_pages_are_ascii_and_their_forms_and_tables_are_labelled(app):
    targets = ("/", "/check", link("/check", file=MEMORY, evidence=EVIDENCE, reviews=REVIEWS, as_of="2026-10-10"),
               "/decisions", "/decisions?dataset=feature-a&from=2026-10-05&to=2026-10-08")
    for target in targets:
        page = body(get(app, target))  # body() decodes as ASCII: it fails on any other byte
        assert '<html lang="en">' in page and '<meta name="viewport"' in page
        assert '<a class="skip" href="#main">Skip to content</a>' in page and '<main id="main"' in page
        assert 'aria-current="page"' in page or target == "/nope"
        for ident in re.findall(r'<(?:select|input) id="([^"]+)"', page):
            assert f'<label for="{ident}"' in page or f'for="{ident}"' in page, (target, ident)
        assert '<input type="hidden"' not in page or "#changes" in page
        for table in re.findall(r"<table>.*?</table>", page, re.S):
            assert "<caption>" in table and 'scope="col"' in table
        assert "@media (prefers-color-scheme: dark)" in page and "@media (max-width: 760px)" in page


# --- untrusted text ----------------------------------------------------------------------------------------

HOSTILE = '<script>alert(1)</script> "><img src=x onerror=alert(1)> \u202eevil\u200b [x](javascript:alert(1))'


def manifest_entry(directory: Path, ident: str, name: str, quote_text: str, uri: str) -> dict:
    data = (directory / name).read_bytes()
    start = data.decode("utf-8").index(quote_text)
    return {"id": ident, "path": name, "sha256": hashlib.sha256(data).hexdigest(), "quote": quote_text,
            "start": start, "end": start + len(quote_text), "source_uri": uri, "source_kind": "chat",
            "captured_at": "2026-10-01T00:00:00Z"}


@pytest.fixture
def hostile(root):
    folder = root / "hostile"
    (folder / "docs").mkdir(parents=True)
    write_text_exact(folder / "CLAUDE.md", f"- The API limit is 100 requests per minute {HOSTILE}.\n")
    write_text_exact(folder / "docs" / "api.md", f"The API limit is 60 requests per minute {HOSTILE}.\n")
    write_text_exact(folder / 'x"><svg onload=alert(1)>-reviews.jsonl', "")
    log = root / "hostile-log"
    log.mkdir()
    write_text_exact(log / "said.txt", f"header\n{HOSTILE} approve it\n")
    subject, actor, role = f"<b>Topic</b> {HOSTILE}", '"><b>mallory</b>', "<i>pm</i>"
    entry = manifest_entry(log, "e1", "said.txt", f"{HOSTILE} approve it", "javascript:alert(1)")
    write_text_exact(log / "evidence.jsonl", json.dumps(entry) + "\n")
    write_text_exact(log / "policy.json", json.dumps({"actors": {actor: [role]},
                                                      "permissions": {"approve": [role], "reject": [role],
                                                                      "raise_risk": [role], "request": [role]}}))
    events = [{"id": "ev-1", "timestamp": "2026-10-01T00:00:00Z", "actor": actor, "action": "raise_risk",
               "subject": subject, "note": HOSTILE, "evidence_ids": ["e1"]},
              {"id": "ev-2", "timestamp": "2026-10-02T00:00:00Z", "actor": actor, "action": "approve",
               "subject": subject, "evidence_ids": ["e1"]},
              {"id": "ev-3", "timestamp": "2026-10-03T00:00:00Z", "actor": "nobody<script>", "action": "reject",
               "subject": subject, "evidence_ids": ["e1"]}]
    write_text_exact(log / "events.jsonl", "".join(json.dumps(e) + "\n" for e in events))
    return subject


@requires_confinement
def test_hostile_text_stays_text(app, hostile):
    targets = ["/", "/check", "/check?file=hostile/CLAUDE.md", "/check?file=hostile/CLAUDE.md&evidence=hostile",
               "/decisions", "/decisions?dataset=hostile-log", link("/decisions", dataset="hostile-log", subject=hostile),
               "/decisions?dataset=hostile-log&from=2026-09-30&to=2026-10-05"]
    for target in targets:
        response = get(app, target)
        assert response.status == 200, (target, text(response))
        page = without_logo(body(response)).lower()
        assert "<script" not in page and "<img" not in page and "<b>" not in page and "<i>" not in page, target
        assert 'href="javascript' not in page and "onerror=alert" not in page.replace("onerror=alert(1)&gt;", "")
    page = body(get(app, "/check?file=hostile/CLAUDE.md&evidence=hostile"))
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in page
    assert '<span class="ctl">U+202E</span>' in page and '<span class="ctl">U+200B</span>' in page
    form = body(get(app, "/check"))
    assert '<option value="hostile/x&quot;&gt;&lt;svg onload=alert(1)&gt;-reviews.jsonl">' in form
    assert "<svg onload" not in form
    decided = body(get(app, link("/decisions", dataset="hostile-log", subject=hostile)))
    assert "&quot;&gt;&lt;b&gt;mallory&lt;/b&gt;" in decided and "&lt;i&gt;pm&lt;/i&gt;" in decided
    assert "Not counted: someone without a role can&#x27;t reject" in decided
    assert "javascript:alert(1)" in decided  # the source URI, shown as text, never as a link


# --- nothing is written; model verifiers stay off ------------------------------------------------------------

@requires_confinement
def test_no_page_writes_anything(app, root, tmp_path):
    before = snapshot(tmp_path)
    names = sorted(str(p) for p in tmp_path.rglob("*"))
    targets = ["/", "/check", link("/check", file=MEMORY, evidence=EVIDENCE, reviews=REVIEWS, as_of="2026-10-10"),
               "/check?file=AGENTS.md", "/decisions", "/decisions?dataset=feature-a&as_of=2026-10-31",
               "/decisions?dataset=feature-a&from=2026-10-05&to=2026-10-08", "/decisions?dataset=github-acme-web"]
    for subject in replay(Dataset.load(root / "github-acme-web")).states:
        targets.append(link("/decisions", dataset="github-acme-web", subject=subject))
    for target in targets:
        assert get(app, target).status == 200, target
        assert get(app, target, method="HEAD").status == 200
    for method in ("POST", "PUT", "DELETE"):
        get(app, "/check?file=AGENTS.md", method=method)
    assert snapshot(tmp_path) == before
    assert sorted(str(p) for p in tmp_path.rglob("*")) == names


@requires_confinement
@pytest.mark.parametrize("verifier", ["llm", "jev", "dual", "cascade"])
def test_model_verifiers_are_refused_without_the_flag(app, monkeypatch, verifier):
    real = ui_check.build_verifier

    def never(choice, *args, **kwargs):
        assert choice.value == "lexical", "a model verifier was built"
        return real(choice, *args, **kwargs)

    monkeypatch.setattr(ui_check, "build_verifier", never)
    response = get(app, link("/check", file=MEMORY, evidence=EVIDENCE, verifier=verifier))
    assert response.status == 403
    assert "Model checks are switched off" in text(response) and "--allow-model-verifiers" in text(response)
    assert get(app, link("/check", file=MEMORY, verifier="lexical")).status == 200
    assert get(app, link("/check", file=MEMORY, verifier="bogus")).status == 400


@requires_confinement
def test_the_flag_offers_model_verifiers(root, monkeypatch):
    app = make_app(root, allow_model_verifiers=True)
    form = body(get(app, link("/check", key=app.key)))
    assert '<select id="f-verifier" name="verifier"' in form
    assert "Claude (sends text to Anthropic; costs money)" in form
    assert "Model checks are switched on for this session" in text(get(app, "/"))
    built = []

    class Failing(LexicalVerifier):
        name = "llm"

        def judge(self, claim, passage):
            raise VerifierError(f"credentials rejected: {SECRET}")

    def fake(choice, llm_model, jev_model):
        built.append(choice.value)
        return Failing()

    monkeypatch.setattr(ui_check, "build_verifier", fake)
    response = get(app, link("/check", file=MEMORY, evidence=EVIDENCE, verifier="llm", key=app.key))
    assert built == ["llm"]
    assert response.status == 400 and "The model check failed" in text(response)
    assert SECRET not in body(response)


@requires_confinement
def test_the_openai_verifier_is_not_offered(root, monkeypatch):
    """It needs a model and a provider or base URL, which the page has no inputs for (docs/ui.md)."""
    assert "openai" in ui_check.VerifierChoice.__members__ and "openai" not in ui_check.VERIFIERS
    monkeypatch.setattr(ui_check, "build_verifier", lambda *args, **kwargs: pytest.fail("a verifier was built"))
    for app in (make_app(root), make_app(root, allow_model_verifiers=True)):
        key = {"key": app.key} if app.key else {}
        assert 'value="openai"' not in body(get(app, link("/check", **key)))
        response = get(app, link("/check", file=MEMORY, evidence=EVIDENCE, verifier="openai", **key))
        assert response.status == 400 and "Pick how to compare from the list." in text(response)


@requires_confinement
def test_unexpected_errors_show_no_traceback(app, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError(f"boom {SECRET} /etc/passwd")

    monkeypatch.setattr(ui_check, "audit_claims", boom)
    response = get(app, f"/check?file={MEMORY}")
    page = body(response)
    assert response.status == 500 and "Something went wrong" in page
    for leak in (SECRET, "Traceback", "RuntimeError", "/etc/passwd", "boom"):
        assert leak not in page


# --- logo, favicon and languages ---------------------------------------------------------------------------

PACKAGE = Path(ui_server.__file__).resolve().parent


@requires_confinement
def test_logo_and_favicon_ship_as_package_data_and_are_linked(app):
    page = body(get(app, "/"))
    brand = re.search(r'<a class="brand" href="/">(.*?)</a>', page).group(1)
    assert brand == ('<picture><source srcset="/static/ui-dark.png" media="(prefers-color-scheme: dark)">'
                     '<img src="/static/ui-light.png" alt="Memory Lab" width="145" height="24"></picture>')
    for markup in LOADED[:2]:
        assert markup in page
    shipped = sorted(p.name for p in (PACKAGE / "static").iterdir())
    assert shipped == sorted(STATIC) == ["memory-lab-icon-32.png", "memory-lab-icon-64.png", "ui-dark.png",
                                         "ui-light.png"]
    pyproject = (PACKAGE.parent.parent / "pyproject.toml").read_text(encoding="utf-8")
    data = pyproject.split("[tool.setuptools.package-data]", 1)[1].split("\n[", 1)[0]
    patterns = re.findall(r'"([^"]+)"', re.search(r"^memlab_ui\s*=\s*\[([^\]]*)\]", data, re.M).group(1))
    for name in shipped:
        assert any(fnmatch.fnmatch(f"static/{name}", pattern) for pattern in patterns), name
        data_bytes = (PACKAGE / "static" / name).read_bytes()
        assert data_bytes.startswith(b"\x89PNG\r\n\x1a\n") and len(data_bytes) < 64 * 1024
        served = get(app, f"/static/{name}")
        assert served.status == 200 and served.body == data_bytes
        assert dict(served.headers)["Content-Type"] == "image/png"
        assert dict(served.headers)["X-Content-Type-Options"] == "nosniff"
    favicon = get(app, "/favicon.ico")
    assert favicon.status == 200 and favicon.body == (PACKAGE / "static" / "memory-lab-icon-32.png").read_bytes()
    assert get(app, "/static/ui-light.png", host="evil.example").status == 403


@requires_confinement
def test_language_switch_keeps_the_page_and_its_fields(app):
    page = body(get(app, f"/check?file={MEMORY}"))
    assert '<html lang="en">' in page
    switch = re.search(r'<p class="lang" role="group" aria-label="Language">(.*?)</p>', page).group(1)
    assert '<span lang="en" aria-current="true">English</span> / ' in switch
    assert '<a href="/check?file=memfalsify%2FMEMORY.md&amp;lang=zh" lang="zh-CN" hreflang="zh-CN">' in switch
    assert '<svg width="14" height="14"' in switch
    chinese = body(get(app, f"/check?file={MEMORY}&lang=zh"))
    assert '<html lang="zh-CN">' in chinese
    assert '<a href="/check?file=memfalsify%2FMEMORY.md" lang="en" hreflang="en">English</a>' in chinese
    assert '<input type="hidden" name="lang" value="zh">' in chinese


@requires_confinement
def test_chinese_pages_say_the_same_things(app, root):
    shown = text(get(app, "/?lang=zh"))
    assert "你的 AI 助手记住的东西还对吗？" in shown and "不会修改任何文件，也不会有任何内容离开这台电脑。" in shown
    assert "要检查哪个文件？" in shown and "本项目中的 Agent 文件" in shown and "检查 Agent 文件" in shown
    assert "智能体" not in shown
    response = get(app, link("/check", file=MEMORY, evidence=EVIDENCE, reviews=REVIEWS, as_of="2026-10-10",
                             lang="zh"))
    shown = text(response)
    assert response.status == 200
    assert "MEMORY.md 中有 4 处可能已过时" in shown and "你的 Agent 文件写道" in shown and "两边的数字不同。" in shown
    assert "需要看一下" in shown and "找到相似说法" in shown and "项目里有相似的句子。只是字词相近，不代表一定正确。" in shown
    assert "<mark>100</mark>" in body(response)
    shown = text(get(app, "/decisions?dataset=feature-a&lang=zh"))
    assert "拒绝人 zhou (product manager)" in shown and "不算数：account manager无权批准" in shown
    assert "事情经过" in shown and "两个日期之间的变化" in shown
    refused = get(app, link("/check", file="../outside.md", lang="zh"))
    assert refused.status == 400 and "Agent 文件不在本页面可读取的文件夹内。" in text(refused)
    assert get(app, link("/check", file=MEMORY, verifier="llm", lang="zh")).status == 403
    for page in (body(get(app, target)) for target in ("/?lang=zh", f"/check?file={MEMORY}&lang=zh",
                                                       "/decisions?dataset=github-acme-web&lang=zh")):
        for target in re.findall(r'<a href="(/[^"]*)"(?! lang="en")', page):
            if not target.startswith("/static/"):
                assert "lang=zh" in target, target  # every page link keeps the language


@requires_confinement
def test_every_message_has_a_chinese_translation(app, root, hostile, request):
    i18n.MISSING.clear()
    targets = ["/", "/check", link("/check", file=MEMORY, evidence=EVIDENCE, reviews=REVIEWS, as_of="2026-10-10"),
               "/check?file=AGENTS.md", link("/check", file=MEMORY, reviews=REVIEWS), "/check?file=hostile/CLAUDE.md",
               link("/check", file=MEMORY, as_of="x"), link("/check", file="../x"), "/check?file=.git/config",
               link("/check", file=MEMORY, verifier="llm"), link("/check", file=MEMORY, verifier="bogus"),
               link("/check", file=MEMORY, since="main"), "/check?file=nope.md", "/check?file=memfalsify",
               "/decisions", "/decisions?dataset=feature-a&as_of=2026-10-31&from=2026-10-05&to=2026-10-08",
               "/decisions?dataset=github-acme-web", "/decisions?dataset=hostile-log",
               "/decisions?dataset=feature-a&subject=Nope", "/decisions?dataset=feature-a&from=2026-10-09&to=2026-10-01",
               "/decisions?dataset=feature-a&from=2026-10-01", "/decisions?dataset=memfalsify", "/nope"]
    for target in targets:
        get(app, target + ("&" if "?" in target else "?") + "lang=zh")
    models = make_app(root, allow_model_verifiers=True)
    get(models, "/check?lang=zh")
    get(models, "/?lang=zh")
    if shutil.which("git"):
        git_app = make_app(request.getfixturevalue("repo").root)
        for target in ("/check?file=CLAUDE.md&since=HEAD", "/check?file=CLAUDE.md&since=-x", "/check"):
            assert get(git_app, target + ("&" if "?" in target else "?") + "lang=zh").status in (200, 400)
    assert not i18n.MISSING, sorted(i18n.MISSING)


def test_message_constants_are_all_translated():
    """Every literal message passed to a translating helper, and every module-level message table."""
    helpers = {"_", "t", "n_", "plural", "fill"}
    for path in sorted(p for p in PACKAGE.glob("*.py") if p.name != "__main__.py"):
        module = importlib.import_module(f"memlab_ui.{path.stem}")
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in helpers):
                continue
            args = node.args[1:3] if node.func.id in ("n_", "plural") else node.args[:1]
            for arg in args:
                value = arg.value if isinstance(arg, ast.Constant) else getattr(module, arg.id, None) \
                    if isinstance(arg, ast.Name) else None
                if isinstance(value, str):
                    assert value in i18n.ZH, (path.name, node.lineno, value)
    tables = [ui_check.PILL, ui_check.WHY, ui_check.ISSUES, ui_check.TILES, ui_check.VERIFIERS, ui_check.ADVICE,
              ui_decisions.STATUS, ui_decisions.ACTION, ui_decisions.CAN, ui_decisions.DECIDED, ui_decisions.SEVERITY,
              ui_decisions.CHANGE, ui_decisions.FINDING, ui_home.STEPS, ui_home.CARDS, ui_pages.NAV]
    keys = {"attention", "fine", "grey", "approved", "rejected", "waiting", "home", "check", "decisions",
            "opposing terms", "different values", "opposite modality", "same subject, but only one side is negated"}
    for table in tables:
        for entry in (table.values() if isinstance(table, dict) else table):
            for value in (entry if isinstance(entry, tuple) else (entry,)):
                if value not in keys and not value.startswith("/"):
                    assert value in i18n.ZH, value
    for english, chinese in i18n.ZH.items():
        assert set(re.findall(r"\{\w+\}", english)) == set(re.findall(r"\{\w+\}", chinese)), english


# --- fixes from the security review and the walkthrough ------------------------------------------------------

@requires_confinement
@pytest.mark.parametrize("extra", [[("Origin", "https://evil.example")], [("Origin", "null")],
                                   [("Origin", f"http://localhost:{PORT}")],
                                   [("Origin", f"http://{HOST}"), ("Origin", f"http://{HOST}")],
                                   [("Sec-Fetch-Site", "cross-site")], [("Sec-Fetch-Site", "same-site")],
                                   [("Sec-Fetch-Site", "same-origin"), ("Origin", "https://evil.example")]])
def test_cross_site_requests_are_refused_before_any_work(root, monkeypatch, extra):
    app = make_app(root, allow_model_verifiers=True)

    def never(*args, **kwargs):
        raise AssertionError("a cross-site request ran a check")

    monkeypatch.setattr(ui_check, "build_verifier", never)
    monkeypatch.setattr(ui_check, "audit_claims", never)
    target = link("/check", file=MEMORY, evidence=EVIDENCE, verifier="llm")
    response = app.handle("GET", target, [("Host", HOST), *extra], "127.0.0.1")
    assert response.status == 403
    assert "came from another website" in text(response) and "evil" not in body(response)


@requires_confinement
def test_same_origin_typed_and_linked_requests_still_work(app):
    for extra in ([], [("Sec-Fetch-Site", "none")], [("Sec-Fetch-Site", "same-origin")],
                  [("Origin", f"http://{HOST}")], [("Sec-Fetch-Site", "same-origin"), ("Origin", f"http://{HOST}")]):
        assert app.handle("GET", "/", [("Host", HOST), *extra], "127.0.0.1").status == 200, extra
    other = app.handle("GET", "/", [("Host", f"localhost:{PORT}"), ("Origin", f"http://localhost:{PORT}")],
                       "127.0.0.1")
    assert other.status == 200


@requires_confinement
def test_at_most_two_pages_are_computed_at_once(app):
    assert app.pages.acquire(blocking=False) and app.pages.acquire(blocking=False)
    try:
        busy = get(app, f"/check?file={MEMORY}")
        assert busy.status == 503 and ("Retry-After", "5") in busy.headers
        assert "busy with other pages" in text(busy)
        assert get(app, "/static/ui-light.png").status == 200  # images are not computed
    finally:
        app.pages.release()
        app.pages.release()
    assert get(app, f"/check?file={MEMORY}").status == 200


@requires_confinement
def test_page_budgets_are_reported_as_incomplete(app, root, monkeypatch):
    monkeypatch.setattr(ui_server, "DIRECTORIES", 3)
    shown = text(get(app, "/"))
    assert "This project is large, so only part of it was read. Results may be incomplete." in shown
    monkeypatch.setattr(ui_server, "DIRECTORIES", 20_000)
    assert "This project is large" not in text(get(app, "/"))
    # Enough for the agent file, the evidence's first bytes and half of the evidence itself.
    evidence = sum(f.stat().st_size for f in (root / EVIDENCE).rglob("*") if f.is_file() and f.suffix != ".png")
    memory = sum((root / "memfalsify" / name).stat().st_size for name in ("MEMORY.md", "infra.md"))
    monkeypatch.setattr(ui_server, "TOTAL_BYTES", memory + evidence + evidence // 2)
    response = get(app, link("/check", file=MEMORY, evidence=EVIDENCE))
    assert response.status == 200 and "This project is large" in text(response)
    assert "not read: this page's reading budget was used up" in text(response)
    monkeypatch.setattr(ui_server, "TOTAL_BYTES", 64_000_000)
    monkeypatch.setattr(ui_server, "FILE_BYTES", 100)
    refused = get(app, link("/check", file=MEMORY, evidence=EVIDENCE))
    assert refused.status == 400 and "A file is too large for this page." in text(refused)
    monkeypatch.setattr(ui_server, "FILE_BYTES", 8_000_000)
    monkeypatch.setattr(ui_check, "MAX_STATEMENTS", 3)
    limited = get(app, link("/check", file=MEMORY, evidence=EVIDENCE))
    assert "Only the first 3 of 10 statements were checked." in text(limited)
    assert body(limited).count("<h4>") == 3


@requires_confinement
def test_evidence_files_over_the_limit_are_never_read_whole(app, root, monkeypatch):
    (root / "memfalsify" / "evidence" / "huge.md").write_bytes(b"The rate limit is 60.\n" * 120_000)
    sizes = []
    original = reading._read_all

    def spy(handle, max_bytes=None):
        data = original(handle, max_bytes)
        sizes.append(len(data))
        return data

    monkeypatch.setattr(reading, "_read_all", spy)
    shown = text(get(app, link("/check", file=MEMORY, evidence=EVIDENCE)))
    assert "huge.md larger than 2 MB" in shown
    assert max(sizes) < 2_000_000


@requires_confinement
def test_no_contradictions_headline_says_what_was_not_checked(app, root):
    write_text_exact(root / "notes.md", "- The office plant is a fern.\n- Lunch is at noon.\n")
    shown = text(get(app, "/check?file=notes.md"))
    assert "No contradictions found in notes.md · 2 statements couldn't be checked" in shown
    assert "looks out of date" not in shown
    zh = text(get(app, "/check?file=notes.md&lang=zh"))
    assert "notes.md 里没有发现矛盾，另有 2 条无法核对" in zh


@requires_confinement
def test_memory_labs_own_files_are_left_out_by_content(app, root):
    response = get(app, link("/check", file=MEMORY, reviews=REVIEWS))  # the whole project, with the reviews
    page = body(response)
    assert '{&quot;memory_id&quot;' not in page and '{"memory_id"' not in page
    # Ordinary CSV and JSON Lines files are evidence; Memory Lab's own files are left out, whatever their name.
    write_text_exact(root / "limits.csv", "service,limit\npublic API rate limit,60 requests per minute\n")
    write_text_exact(root / "notes.jsonl", '{"text": "The public API rate limit is 60 requests per minute."}\n')
    shutil.copy(root / REVIEWS, root / "renamed.txt")
    shutil.copy(EXAMPLES / "orgmemory" / "adr" / "feature-a.csv", root / "decisions.csv")
    shutil.copy(EXAMPLES / "orgmemory" / "audit-report.md", root / "audit.md")
    shutil.copytree(EXAMPLES / "orgmemory" / "adr" / "feature-a", root / "adr")
    shown = text(get(app, "/check?file=AGENTS.md"))
    assert "Your project says · limits.csv:2" in shown or "limits.csv:2" in shown
    assert "notes.jsonl:1" in shown
    for path, reason in (("renamed.txt", "review records"), ("memfalsify/reviews.jsonl", "review records"),
                         ("feature-a/events.jsonl", "decision log"), ("feature-a/evidence.jsonl", "decision log"),
                         ("feature-a/policy.json", "decision log"), ("github-acme-web/github.json", "decision log"),
                         ("github-acme-web/github-sync.jsonl", "decision log"),
                         ("decisions.csv", "report generated by Memory Lab"),
                         ("audit.md", "report generated by Memory Lab"),
                         ("github-acme-web/audit-report.html", "report generated by Memory Lab")):
        assert f"{path} {reason}" in shown, path
    adr = next((root / "adr").iterdir()).name
    assert f"adr/{adr} report generated by Memory Lab" in shown
    assert "Your docs say · api.md:3" in shown  # prose evidence still counts
    zh = text(get(app, "/check?file=AGENTS.md&lang=zh"))
    assert "renamed.txt 审阅记录" in zh and "feature-a/events.jsonl 决策日志" in zh


@requires_confinement
def test_technical_facts_are_folded_into_details(app):
    page = body(get(app, link("/check", file=MEMORY, evidence=EVIDENCE, reviews=REVIEWS, as_of="2026-10-10")))
    outside = re.sub(r'<details class="details">.*?</details>', "", page, flags=re.S)
    assert "sha256" not in outside.lower() and "m-27e874e0" not in outside
    assert "SHA-256" in page and "Statement ID" in page and "<summary>Details</summary>" in page
    decided = body(get(app, "/decisions?dataset=github-acme-web&subject=acme%2Fweb%231"))
    outside = re.sub(r'<details class="details">.*?</details>', "", decided, flags=re.S)
    assert "https://" not in outside and "sha256" not in outside.lower() and "gh:acme/web#1/" not in outside
    assert "https://" in decided and "Event ID" in decided
    feature = body(get(app, "/decisions?dataset=feature-a"))
    assert "synthetic://" not in re.sub(r'<details class="details">.*?</details>', "", feature, flags=re.S)
    zh = body(get(app, "/decisions?dataset=feature-a&lang=zh"))
    assert "<summary>&#35814;&#24773;</summary>" in zh  # 详情


@requires_confinement
def test_chinese_decisions_have_no_english_sentence_fragments(app):
    for target in ("/decisions?dataset=feature-a&as_of=2026-10-31&from=2026-10-05&to=2026-10-08&lang=zh",
                   "/decisions?dataset=github-acme-web&lang=zh"):
        shown = text(get(app, target))
        for english in ("attempt(s)", "the policy ignored", "unauthorized_attempts", "pending approval from",
                        "raised by", "since 20", "Not counted", "approved it", "asked for it"):
            assert english not in shown, (target, english)
    shown = text(get(app, "/decisions?dataset=feature-a&lang=zh"))
    assert "zhou (product manager)" in shown and "wu 曾 1 次 尝试做其角色不允许的事（批准），涉及 Feature A。" in shown


def test_a_date_covers_the_whole_day():
    end = ui_decisions.moment("2026-10-07", "x")
    assert end == datetime(2026, 10, 7, 23, 59, 59, 999_999, tzinfo=timezone.utc)
    assert datetime(2026, 10, 7, 23, 59, 59, 500_000, tzinfo=timezone.utc) <= end
    assert datetime(2026, 10, 8, tzinfo=timezone.utc) > end
    assert ui_decisions.moment("2026-10-07T12:00:00Z", "x") == datetime(2026, 10, 7, 12, tzinfo=timezone.utc)


@requires_confinement
def test_language_links_are_built_from_known_pages_and_fields(app):
    for target in ("/\\evil.example/", "/nope?file=x&next=http://evil.example", "//evil.example/"):
        page = body(get(app, target))
        assert not [h for h in re.findall(r'href="([^"]*)"', page) if "evil" in h or "\\" in h], target
        switch = re.search(r'<p class="lang".*?</p>', page, re.S)
        assert switch is None or re.findall(r'href="([^"]*)"', switch.group(0)) == ["/?lang=zh"], target
    page = body(get(app, f"/check?file={MEMORY}&next=http://evil.example&lang=zh"))
    switch = re.search(r'<p class="lang".*?</p>', page, re.S).group(0)
    assert re.findall(r'href="([^"]*)"', switch) == ["/check?file=memfalsify%2FMEMORY.md"]


@requires_confinement
def test_malformed_targets_are_a_plain_bad_request(app):
    for target in ("http://[secret/", "http://[::1", "/check?file=%ZZ"):
        response = get(app, target)
        assert response.status in (200, 400), target
        assert "secret" not in body(response)
    assert get(app, "http://[secret/").status == 400


@requires_confinement
def test_sidebar_shows_the_folder_name_with_the_path_as_a_tooltip(app, root):
    page = body(get(app, "/"))
    assert f'<p class="mono" title="{html.escape(str(root.resolve()), quote=True)}">root</p>' in page


# --- round 3 ------------------------------------------------------------------------------------------------

@requires_confinement
def test_model_checks_need_the_key_printed_in_the_terminal(root, monkeypatch):
    app = make_app(root, allow_model_verifiers=True)
    built = []

    def fake(choice, llm_model, jev_model):
        built.append(choice.value)
        return LexicalVerifier()

    monkeypatch.setattr(ui_check, "build_verifier", fake)
    for key in (None, "", "guess", app.key[:-1], "\u043a\u043b\u044e\u0447", app.key + "\u00e9", "%FF"):
        params = {"file": MEMORY, "evidence": EVIDENCE, "verifier": "llm", **({"key": key} if key is not None else {})}
        response = get(app, link("/check", **params))
        assert response.status == 403 and built == [], key  # never a 500, whatever the key's characters
        assert "need the address with the key" in text(response) and 'id="results"' not in body(response)
        page = body(response)
        assert app.key not in page and 'name="key"' not in page and 'name="verifier"' not in page
    assert get(app, "/check?file=" + urlquote(MEMORY) + "&verifier=llm&key=%FF%FE").status == 403
    keyed = get(app, link("/check", file=MEMORY, evidence=EVIDENCE, verifier="llm", key=app.key))
    assert built == ["llm"] and 'id="results"' in body(keyed)
    page = body(keyed)
    assert f'<input type="hidden" name="key" value="{app.key}">' in page  # forms keep it ...
    links = [html.unescape(h) for h in re.findall(r'<a href="(/[^"#]*)', page) if not h.startswith("/static/")]
    assert links and all(f"key={app.key}" in h for h in links), links  # ... and so do links
    assert make_app(root, allow_model_verifiers=True).key != app.key  # a new run, a new key
    lexical = get(app, link("/check", file=MEMORY, evidence=EVIDENCE))  # quick checks need no key
    assert 'id="results"' in body(lexical) and app.key not in body(lexical)
    assert make_app(root).key is None and 'name="key"' not in body(get(make_app(root), "/check"))


@requires_confinement
def test_agent_files_over_256_kb_are_not_parsed(app, root, monkeypatch):
    (root / "huge").mkdir()
    write_text_exact(root / "huge" / "CLAUDE.md", "- The API answers in 1 second.\n" * 9000)  # 270 KB
    calls = []
    monkeypatch.setattr(ui_check, "parse_memory", lambda *a, **k: calls.append(a) or ([], []))
    refused = get(app, "/check?file=huge/CLAUDE.md")
    assert refused.status == 400 and "larger than 256 KB" in text(refused) and calls == []
    monkeypatch.undo()
    assert re.search(r"huge/CLAUDE\.md Claude Code can't read Check", text(get(app, "/")))


@requires_confinement
def test_passages_running_out_marks_the_page_incomplete(app, root, monkeypatch):
    folder = root / "two"
    folder.mkdir()
    write_text_exact(folder / "a.md", "The public API rate limit is 60 requests per minute.\n")
    write_text_exact(folder / "b.md", "The public API rate limit is 70 requests per minute.\n")
    monkeypatch.setattr(ui_server, "ITEMS", {"claims": 5_000, "passages": 1, "comparisons": 2_000_000})
    shown = text(get(app, link("/check", file="AGENTS.md", evidence="two")))
    assert "b.md not read: this page's reading budget was used up" in shown
    assert "This project is large" in shown


@requires_confinement
def test_internal_conflicts_draw_from_the_comparison_budget(app, root, monkeypatch):
    write_text_exact(root / "conflicts.md", "".join(f"- Service {i} is approved.\n- Service {i} is rejected.\n"
                                                    for i in range(30)))
    assert "May disagree with another statement" in text(get(app, "/check?file=conflicts.md&evidence=memfalsify"))
    monkeypatch.setattr(ui_server, "ITEMS", {"claims": 5_000, "passages": 5_000, "comparisons": 5})
    shown = text(get(app, "/check?file=conflicts.md&evidence=memfalsify"))
    assert "This project is large" in shown


@requires_confinement
def test_json_reports_are_recognised_from_the_whole_file(app, root):
    from orgmemory.cli import app as org_app
    result = runner.invoke(org_app, ["replay", str(EXAMPLES / "orgmemory" / "github-acme-web"), "--format", "json"])
    assert result.exit_code == 0
    write_text_exact(root / "replay.json", result.output)
    assert (root / "replay.json").stat().st_size > 16 * 1024  # larger than the first bytes the page looks at
    shown = text(get(app, "/check?file=AGENTS.md"))
    assert "replay.json report generated by Memory Lab" in shown


@requires_confinement
def test_paths_and_subjects_keep_their_spaces(app, root):
    write_text_exact(root / " spaced.md ", "- The office plant is a fern.\n")
    response = get(app, link("/check", file=" spaced.md "))
    assert response.status == 200 and 'id="results"' in body(response)
    assert "No contradictions found in spaced.md" in text(response)  # text() collapses the spaces
    log = root / "spaced-log"
    shutil.copytree(EXAMPLES / "orgmemory" / "feature-a", log)
    events = (log / "events.jsonl").read_text(encoding="utf-8").replace('"Feature A"', '" Feature A "')
    write_text_exact(log / "events.jsonl", events)
    page = body(get(app, "/decisions?dataset=spaced-log"))
    target = html.unescape(re.search(r'<li><a href="(/decisions\?[^"]+)#detail"', page).group(1))
    assert "subject=+Feature+A+" in target
    detail = get(app, target)
    assert detail.status == 200 and "Unknown topic" not in text(detail)
    assert get(app, "/check?file=" + urlquote(MEMORY) + "&as_of=%202026-10-10%20").status == 200  # dates trim


@requires_confinement
def test_amplification_budgets_are_reported(app, root, monkeypatch):
    dense = root / "dense"
    dense.mkdir()
    write_text_exact(dense / "notes.md", "".join(f"The API limit {i} is {i} requests. " for i in range(400)))
    monkeypatch.setattr(ui_server, "ITEMS", {"claims": 5_000, "passages": 50, "comparisons": 2_000_000})
    shown = text(get(app, link("/check", file="AGENTS.md", evidence="dense")))
    assert "This project is large" in shown
    monkeypatch.setattr(ui_server, "ITEMS", {"claims": 5_000, "passages": 5_000, "comparisons": 10})
    assert "This project is large" in text(get(app, link("/check", file="AGENTS.md", evidence="dense")))
    monkeypatch.setattr(ui_server, "ITEMS", {"claims": 3, "passages": 5_000, "comparisons": 2_000_000})
    limited = text(get(app, link("/check", file=MEMORY, evidence=EVIDENCE)))
    assert "Only the first 3 statements were checked: the file has more than 3." in limited
    monkeypatch.setattr(ui_server, "ITEMS", {"claims": 5_000, "passages": 5_000, "comparisons": 2_000_000})
    monkeypatch.setattr(ui_server, "DEPTH", 1)
    deep = root / "a" / "b" / "c"
    deep.mkdir(parents=True)
    write_text_exact(deep / "CLAUDE.md", "- Deep rule.\n")
    shown = text(get(app, "/"))
    assert "This project is large" in shown and "a/b/c/CLAUDE.md" not in shown


@requires_confinement
@pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")
def test_changed_since_bounds_the_old_version_too(git_repo, monkeypatch):
    git_repo.commit("big", {"CLAUDE.md": "".join(f"- Rule {i} applies to service {i}.\n" for i in range(40))})
    git_repo.write({"CLAUDE.md": "- Rule 1 applies to service 1.\n- One new rule for the API.\n"})
    app = make_app(git_repo.root)
    monkeypatch.setattr(ui_server, "ITEMS", {"claims": 20, "passages": 5_000, "comparisons": 2_000_000})
    refused = get(app, "/check?file=CLAUDE.md&since=HEAD")
    assert refused.status == 400 and "too many to compare here" in text(refused)
    monkeypatch.setattr(ui_server, "TOTAL_BYTES", 300)  # the old version's git object alone is larger
    monkeypatch.setattr(ui_server, "ITEMS", {"claims": 5_000, "passages": 5_000, "comparisons": 2_000_000})
    too_large = get(app, "/check?file=CLAUDE.md&since=HEAD")
    assert too_large.status == 400 and "A file is too large for this page." in text(too_large)
    monkeypatch.setattr(ui_server, "TOTAL_BYTES", 64_000_000)
    assert get(app, "/check?file=CLAUDE.md&since=HEAD").status == 200


@pytest.mark.parametrize("confined", [True, False])
def test_help_names_the_hard_link_exception(monkeypatch, confined):
    monkeypatch.setattr(ui_server, "confinement_supported", lambda: confined)  # --help never builds a server
    result = runner.invoke(ui_server.app, ["--help"], env={"COLUMNS": "200"})
    assert result.exit_code == 0 and "hard-linked" in result.output and "nothing outside it is read" not in result.output


# --- starting the server -----------------------------------------------------------------------------------

@pytest.mark.parametrize("confined", [True, False])
@pytest.mark.parametrize("host", ["0.0.0.0", "::", "localhost", "192.168.1.2", ""])
def test_other_hosts_are_refused(tmp_path, host, monkeypatch, confined):
    # The host is refused whatever the platform supports, whichever check runs first.
    monkeypatch.setattr(ui_server, "confinement_supported", lambda: confined)
    with pytest.raises(ValueError, match="serves only on 127.0.0.1"):
        create_server(tmp_path, 0, host=host)
    result = runner.invoke(ui_server.app, ["--root", str(tmp_path), "--host", host or "x", "--port", "0"])
    assert result.exit_code == 2 and "serves only on 127.0.0.1" in result.output


def test_refuses_to_start_without_confined_reads(tmp_path, monkeypatch):
    monkeypatch.setattr(ui_server, "confinement_supported", lambda: False)
    with pytest.raises(ValueError, match="confined reads"):
        create_server(tmp_path, 0)
    result = runner.invoke(ui_server.app, ["--root", str(tmp_path), "--port", "0"])
    assert result.exit_code == 2 and "confined reads" in result.output


@requires_confinement
def test_cli_prints_the_address_and_stops_on_ctrl_c(tmp_path, monkeypatch):
    def interrupted(self, poll_interval=0.5):
        raise KeyboardInterrupt

    monkeypatch.setattr(ui_server.Server, "serve_forever", interrupted)
    result = runner.invoke(ui_server.app, ["--root", str(tmp_path), "--port", "0"])
    assert result.exit_code == 0, result.output
    assert re.search(r"Memory Lab is open at http://127\.0\.0\.1:\d+/\n", result.output)
    assert "never changes a file" in result.output and "key=" not in result.output
    keyed = runner.invoke(ui_server.app, ["--root", str(tmp_path), "--port", "0", "--allow-model-verifiers"])
    assert keyed.exit_code == 0, keyed.output
    assert re.search(r"Memory Lab is open at http://127\.0\.0\.1:\d+/\?key=[A-Za-z0-9_-]{32}\n", keyed.output)


@requires_confinement
def test_cli_reports_a_busy_port(tmp_path):
    busy = create_server(tmp_path, 0)
    try:
        result = runner.invoke(ui_server.app, ["--root", str(tmp_path), "--port", str(busy.server_address[1])])
    finally:
        busy.server_close()
    assert result.exit_code == 2 and "cannot listen on 127.0.0.1" in result.output


@pytest.fixture
def loopback(monkeypatch):
    """Real sockets, to 127.0.0.1 only."""
    def connect(sock, address):
        assert address[0] == "127.0.0.1", address
        return _CONNECT(sock, address)

    def create_connection(address, *args, **kwargs):
        assert address[0] == "127.0.0.1", address
        return _CREATE_CONNECTION(address, *args, **kwargs)

    monkeypatch.setattr(socket.socket, "connect", connect)
    monkeypatch.setattr(socket, "create_connection", create_connection)


@pytest.fixture
def served(root, loopback):
    server = create_server(root, 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()
    server.server_close()
    thread.join(5)


def exchange(server, request: bytes) -> tuple[int, dict, bytes]:
    with socket.create_connection(("127.0.0.1", server.server_address[1]), timeout=10) as connection:
        connection.sendall(request)
        data = b""
        while chunk := connection.recv(65536):
            data += chunk
    head, _, payload = data.partition(b"\r\n\r\n")
    lines = head.decode("latin-1").split("\r\n")
    headers = {}
    for line in lines[1:]:
        name, _, value = line.partition(":")
        headers[name.strip().lower()] = value.strip()
    return int(lines[0].split()[1]), headers, payload


def request(server, method="GET", target="/", host=None, extra=b"") -> tuple[int, dict, bytes]:
    host = f"127.0.0.1:{server.server_address[1]}" if host is None else host
    line = f"{method} {target} HTTP/1.1\r\n".encode()
    head = (f"Host: {host}\r\n".encode() if host else b"") + b"Connection: close\r\n" + extra
    return exchange(server, line + head + b"\r\n")


@requires_confinement
def test_served_over_loopback_only(served, root, capsys):
    assert served.server_address[0] == "127.0.0.1"
    status, headers, payload = request(served, target=f"/check?file={MEMORY}&evidence={EVIDENCE}")
    assert status == 200 and b"may be out of date" in payload
    assert headers["content-security-policy"] == HEADER_CSP
    assert headers["x-frame-options"] == "DENY" and headers["x-content-type-options"] == "nosniff"
    assert headers["server"] == "memlab-ui"  # no Python version
    assert int(headers["content-length"]) == len(payload)
    status, _, payload = request(served, host=f"localhost:{served.server_address[1]}")
    assert status == 200
    status, _, payload = request(served, host="evil.example")
    assert status == 403 and b"evil" not in payload
    status, _, _ = exchange(served, b"GET / HTTP/1.0\r\n\r\n")  # no Host header at all
    assert status == 403
    status, headers, payload = request(served, method="HEAD")
    assert status == 200 and payload == b"" and int(headers["content-length"]) > 0
    status, headers, _ = request(served, method="POST", extra=b"Content-Length: 0\r\n")
    assert status == 405 and headers["allow"] == "GET, HEAD"
    status, headers, payload = request(served, method="BREW")
    assert status == 405 and headers["allow"] == "GET, HEAD" and headers["content-security-policy"] == HEADER_CSP
    assert b"BREW" not in payload
    status, headers, _ = exchange(served, b"GET /\x1b[31m HTTP/1.1 junk\r\n\r\n")
    assert status == 400 and headers["content-security-policy"] == HEADER_CSP
    request(served, target="/check?file=%1b%5b31mquery-secret")
    log = capsys.readouterr().err
    assert "memlab-ui: GET /check 200" in log and "memlab-ui: GET / 403" in log
    assert "query-secret" not in log and "evil" not in log and "\x1b" not in log and "BREW" not in log


@requires_confinement
def test_served_rejects_http_0_9_and_odd_targets_with_full_headers(served, capsys):
    status, headers, payload = exchange(served, b"GET /\r\n\r\n")
    assert status == 400 and headers["content-security-policy"] == HEADER_CSP
    assert headers["x-frame-options"] == "DENY"
    status, headers, payload = exchange(served, b"GET http://[secret/ HTTP/1.1\r\nHost: 127.0.0.1:"
                                        + str(served.server_address[1]).encode() + b"\r\n\r\n")
    assert status == 400 and headers["content-security-policy"] == HEADER_CSP and b"secret" not in payload
    status, headers, _ = request(served, target="/static/ui-light.png")
    assert status == 200 and headers["x-frame-options"] == "DENY" and headers["referrer-policy"] == "no-referrer"
    assert headers["cache-control"] == "max-age=3600"
    err = capsys.readouterr().err
    assert "Traceback" not in err and "secret" not in err and "memlab-ui: GET - 400" in err


def closed_by_peer(connection, seconds: float) -> bool:
    """Whether the server closes ``connection`` within ``seconds`` while we keep sending a byte at a time."""
    started = time.monotonic()
    while time.monotonic() - started < seconds:
        try:
            connection.sendall(b"X")
            connection.settimeout(0.3)
            if connection.recv(100) == b"":
                return True
        except TimeoutError:
            continue
        except OSError:
            return True
    return False


@requires_confinement
def test_served_caps_connections_and_the_time_for_headers(root, loopback, monkeypatch):
    monkeypatch.setattr(ui_server, "MAX_CONNECTIONS", 2)
    monkeypatch.setattr(ui_server, "HEADER_DEADLINE", 1.0)
    server = create_server(root, 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    address = ("127.0.0.1", server.server_address[1])
    try:
        slow = [socket.create_connection(address, timeout=10) for _ in range(2)]
        for connection in slow:
            connection.sendall(b"GET / HTTP/1.1\r\n")
        time.sleep(0.2)
        extra = socket.create_connection(address, timeout=10)
        started = time.monotonic()
        try:
            assert extra.recv(100) == b""  # closed at once: no thread for a third connection
        except ConnectionResetError:
            pass
        assert time.monotonic() - started < 1
        extra.close()
        # Each byte arrives well within the per-read timeout, but the headers never finish: the deadline ends it.
        assert closed_by_peer(slow[0], 4)
        for connection in slow:
            connection.close()
        time.sleep(1.2)
        status, _headers, _payload = exchange(server, b"GET / HTTP/1.1\r\nHost: 127.0.0.1:"
                                              + str(address[1]).encode() + b"\r\n\r\n")
        assert status == 200  # both slots were given back
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)
