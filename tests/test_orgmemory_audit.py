"""`orgmemory audit`: every finding kind, severities, cutoffs, ordering, exit codes, and read-only behaviour."""
import json
import re
import sys
from datetime import datetime, timezone
from itertools import product

import pytest
from typer.testing import CliRunner

from conftest import EXAMPLES, snapshot
from memcore import EvidenceSpan, ManifestEntry, locate_quote
from orgmemory.audit import KINDS, audit, open_periods
from orgmemory.cli import app
from orgmemory.models import Event, Policy
from orgmemory.replay import Dataset, replay
from orgmemory.report import audit_markdown

runner = CliRunner()
EVERYONE = ["customer", "account_manager", "engineer", "product_manager"]
POLICY = {
    "actors": {"pm": ["product_manager"], "pm2": ["product_manager"], "eng": ["engineer"],
               "cust": ["customer"], "sales": ["account_manager"]},
    "permissions": {"request": EVERYONE, "reconsider": EVERYONE, "comment": EVERYONE,
                    "raise_risk": ["engineer", "product_manager"], "dependency": ["engineer", "product_manager"],
                    "approve": ["product_manager"], "reject": ["product_manager"], "revoke": ["product_manager"]},
}


def t(day, hour=9):
    return f"2026-10-{day:02d}T{hour:02d}:00:00Z"


def at(day, hour=9):
    return datetime(2026, 10, day, hour, tzinfo=timezone.utc)


def ev(id, day, actor, action, subject="S", hour=9, **fields):
    return {"id": id, "timestamp": t(day, hour), "actor": actor, "action": action, "subject": subject,
            "evidence_ids": ["e"], **fields}


def build(root, events, policy=POLICY):
    """Write a minimal dataset: one source file, one evidence record, the policy and the events."""
    root.mkdir(parents=True)
    (root / "note.txt").write_bytes(b"Synthetic note for the audit tests.\n")
    span = locate_quote(root, "note.txt", "Synthetic note")
    entry = ManifestEntry(id="e", path=span.path, sha256=span.sha256, quote=span.quote, start=span.start,
                          end=span.end, source_uri="synthetic://note", source_kind="note",
                          captured_at="2026-10-01T00:00:00Z")
    (root / "evidence.jsonl").write_bytes((json.dumps(entry.model_dump(mode="json")) + "\n").encode("utf-8"))
    (root / "policy.json").write_bytes(json.dumps(policy).encode("utf-8"))
    (root / "events.jsonl").write_bytes("".join(json.dumps(e) + "\n" for e in events).encode("utf-8"))
    return root


def run(tmp_path, events, as_of=None, **kwargs):
    return audit(Dataset.load(build(tmp_path / "data", events)), as_of, **kwargs)


def kinds(report):
    return [(f.kind, f.severity, f.subject or f.actor) for f in report.findings]


# --- a healthy log -------------------------------------------------------------------------------

def test_clean_log_has_no_findings(tmp_path):
    report = run(tmp_path, [ev("r", 1, "cust", "request"), ev("a", 2, "pm", "approve"),
                            ev("c", 3, "sales", "comment")], as_of=at(30))
    assert report.findings == [] and report.summary == {"high": 0, "medium": 0, "low": 0}
    assert list(report.kinds) == sorted(KINDS) and set(report.kinds.values()) == {0}
    markdown = audit_markdown(report)
    assert "| **Total** | 0 | 0 | 0 |" in markdown and "No findings." in markdown


# --- conflicts replay detects ------------------------------------------------------------------

def test_simultaneous_decisions_high_while_current_medium_once_replaced(tmp_path):
    events = [ev("r", 1, "cust", "request"), ev("a", 2, "pm", "approve"), ev("j", 2, "pm2", "reject")]
    finding, = run(tmp_path, events, as_of=at(30)).findings
    assert (finding.kind, finding.severity, finding.subject) == ("simultaneous_decisions", "high", "S")
    assert finding.event_ids == ["a", "j"] and "still current" in finding.detail
    assert [e.evidence[0].quote for e in finding.events] == ["Synthetic note"] * 2

    settled = run(tmp_path / "later", events + [ev("j2", 3, "pm", "reject")], as_of=at(30)).findings
    assert [(f.kind, f.severity) for f in settled] == [("simultaneous_decisions", "medium")]
    assert "a later decision (j2) has replaced it" in settled[0].detail


@pytest.mark.parametrize("second, severity", [("pm", "low"), ("pm2", "medium")])
def test_decision_reversed_severity_depends_on_who_reversed(tmp_path, second, severity):
    report = run(tmp_path, [ev("j", 1, "pm", "reject"), ev("a", 2, second, "approve")], as_of=at(3))
    assert [(f.kind, f.severity, f.event_ids) for f in report.findings] == [("decision_reversed", severity, ["j", "a"])]


def test_invalid_supersession_cites_the_named_and_the_standing_decision(tmp_path):
    report = run(tmp_path, [ev("a1", 1, "pm", "approve"), ev("a2", 2, "pm2", "approve"),
                            ev("x", 3, "pm", "revoke", supersedes="a1")], as_of=at(4))
    finding, = report.findings
    assert (finding.kind, finding.severity) == ("invalid_supersession", "medium")
    assert finding.event_ids == ["a1", "a2", "x"]
    assert finding.detail == "revoke targets a1, but the current decision is a2; the revoke changed nothing"


# --- attempts the policy ignored -----------------------------------------------------------------

def test_unauthorized_attempts_are_grouped_per_actor(tmp_path):
    report = run(tmp_path, [
        ev("s1", 1, "sales", "approve", subject="B"), ev("s2", 2, "sales", "reject", subject="A"),
        ev("c1", 1, "cust", "approve"),
        ev("m1", 1, "mallory", "request", subject="M"), ev("m2", 2, "mallory", "dependency", subject="M",
                                                           dependency="X"),
        ev("k1", 3, "cust", "raise_risk", subject="K", note="customer worry"),
        ev("n1", 1, "nobody", "comment"), ev("n2", 2, "nobody", "comment"),
    ], as_of=at(3))
    attempts = {f.actor: f for f in report.findings if f.kind == "unauthorized_attempts"}
    assert set(attempts) == {"sales", "cust", "mallory"}  # comments are never counted
    assert (attempts["sales"].severity, attempts["sales"].subjects, attempts["sales"].event_ids) == \
        ("high", ["A", "B"], ["s1", "s2"])
    assert attempts["sales"].subject is None
    assert attempts["sales"].detail == ("2 attempt(s) the policy ignored (approve ×1, reject ×1) on 2 subject(s): "
                                        "A, B; roles: account_manager")
    assert (attempts["cust"].severity, attempts["cust"].event_ids) == ("medium", ["c1", "k1"])
    assert attempts["mallory"].severity == "medium" and attempts["mallory"].detail.endswith("roles: none")


@pytest.mark.parametrize("actions, severity", [
    (["request"], "low"), (["request", "raise_risk"], "medium"), (["approve"], "medium"),
    (["approve", "revoke"], "high"),
])
def test_unauthorized_attempt_severity(tmp_path, actions, severity):
    fields = {"raise_risk": {"note": "n"}, "revoke": {"supersedes": "x0"}}
    events = [ev(f"x{i}", 1 + i, "mallory", a, **fields.get(a, {})) for i, a in enumerate(actions)]
    finding, = [f for f in run(tmp_path, events, as_of=at(5)).findings if f.kind == "unauthorized_attempts"]
    assert finding.severity == severity


# --- stale requests ----------------------------------------------------------------------------------

def test_stale_pending_approval_is_measured_at_as_of(tmp_path):
    events = [ev("r1", 1, "cust", "request"), ev("r2", 5, "sales", "request")]
    data = Dataset.load(build(tmp_path / "data", events))
    assert audit(data, at(15, 9)).findings == []                   # exactly 14 days: not more than 14
    finding, = audit(data, at(15, 10)).findings
    assert (finding.kind, finding.severity, finding.event_ids) == ("stale_pending_approval", "medium", ["r1", "r2"])
    assert finding.detail == ("awaiting a decision from product_manager since 2026-10-01T09:00:00+00:00 "
                              "(status proposed): 14d 1h at 2026-10-15T10:00:00+00:00, more than 14 days")
    assert audit(data, at(15, 10), stale_days=30).findings == []
    assert audit(data, at(4)).findings == [] and audit(data, datetime(2026, 9, 1, tzinfo=timezone.utc)).findings == []


def test_without_as_of_staleness_is_measured_now(tmp_path):
    data = Dataset.load(build(tmp_path / "data", [ev("r1", 1, "cust", "request")]))
    report = audit(data, now=at(31))
    assert report.as_of is None and report.reference_time == at(31) and kinds(report) == [
        ("stale_pending_approval", "medium", "S")]
    assert audit(data, now=at(2)).findings == []
    assert audit(data).reference_time.tzinfo is not None


def test_revoke_reopens_the_pending_approval(tmp_path):
    report = run(tmp_path, [ev("a", 1, "pm", "approve"), ev("v", 2, "pm", "revoke", supersedes="a")], as_of=at(20))
    finding, = report.findings
    assert (finding.kind, finding.event_ids) == ("stale_pending_approval", ["v"])


def test_stale_reconsideration_is_reported_once(tmp_path):
    events = [ev("r", 1, "cust", "request"), ev("j", 2, "pm", "reject"), ev("r2", 3, "cust", "request"),
              ev("rc", 5, "sales", "reconsider")]
    report = run(tmp_path, events, as_of=at(20))
    finding, = report.findings
    assert (finding.kind, finding.severity, finding.event_ids) == ("stale_reconsideration", "medium", ["j", "r2", "rc"])
    assert finding.detail.startswith("reconsideration awaiting a decision from product_manager since "
                                     "2026-10-03T09:00:00+00:00 (status rejected): 17d 0h")
    assert run(tmp_path / "early", events, as_of=at(10)).findings == []


def test_reconsideration_stays_open_after_a_revoke(tmp_path):
    report = run(tmp_path, [ev("a", 1, "pm", "approve"), ev("q", 2, "cust", "request"),
                            ev("v", 3, "pm", "revoke", supersedes="a")], as_of=at(20))
    assert replay(Dataset.load(tmp_path / "data"), at(20)).states["S"].reconsideration_requested is True
    finding, = report.findings
    assert (finding.kind, finding.event_ids) == ("stale_reconsideration", ["q", "v"])


def test_open_periods_follow_replay():
    """For every short sequence of decisions and requests, the audit's walk agrees with replay's state."""
    evidence = {"e": EvidenceSpan(path="note.txt", sha256="0" * 64, start=0, end=1, quote="S", line=1, id="e")}
    policy = Policy.model_validate(POLICY)
    actions = ["request", "reconsider", "approve", "reject", "revoke"]
    checked = 0
    for sequence in product(actions, ["pm", "sales"], repeat=3):
        steps = list(zip(sequence[::2], sequence[1::2]))  # (action, actor); only pm may decide
        events, current = [], None
        for index, (action, actor) in enumerate(steps):
            event_id = f"x{index}"
            # A revoke names the decision in force (valid) or, if there is none, itself (invalid).
            fields = {"supersedes": current or event_id} if action == "revoke" else {}
            events.append(Event.model_validate(ev(event_id, 1 + index, actor, action, **fields)))
            if actor == "pm" and (action in ("approve", "reject") or action == "revoke" and current):
                current = event_id
            result = replay(Dataset(events, policy, evidence))
            state = result.states["S"]
            pending, reconsider = open_periods(result.audit_log)
            assert bool(pending) == bool(state.pending_approval), steps[:index + 1]
            assert bool(reconsider) == state.reconsideration_requested, steps[:index + 1]
            checked += 1
    assert checked == 3 * 10 ** 3


@pytest.mark.parametrize("day, pending_ids, reconsider_ids", [
    (1, ["r"], []), (2, ["r", "rc"], ["rc"]), (3, ["r", "rc"], ["rc"]),
    (4, [], []), (5, ["q"], ["q"]), (6, ["q", "v"], ["q"]),
    (7, ["q", "v", "after"], ["q"]), (8, ["q", "v", "after"], ["q"]),
    (9, ["q", "v", "after", "v2"], ["q"]),
    (10, ["q", "v", "after", "v2", "rc2"], ["q", "rc2"]), (11, [], []),
])
def test_open_period_start_events_at_each_cutoff(tmp_path, day, pending_ids, reconsider_ids):
    events = [ev("r", 1, "cust", "request"), ev("rc", 2, "sales", "reconsider"),
              ev("ignored", 3, "sales", "approve"), ev("a", 4, "pm", "approve"),
              ev("q", 5, "cust", "request"), ev("v", 6, "pm", "revoke", supersedes="a"),
              ev("after", 7, "cust", "request"), ev("invalid", 8, "pm", "revoke", supersedes="a"),
              ev("v2", 9, "pm", "revoke", supersedes="v"), ev("rc2", 10, "cust", "reconsider"),
              ev("j", 11, "pm", "reject")]
    data = Dataset.load(build(tmp_path / "data", list(reversed(events))))
    result = replay(data, at(day))
    pending, reconsider = open_periods(result.audit_log)
    assert [e.event_id for e in pending] == pending_ids
    assert [e.event_id for e in reconsider] == reconsider_ids
    assert bool(pending) == bool(result.states["S"].pending_approval)
    assert bool(reconsider) == result.states["S"].reconsideration_requested


def test_pending_and_reconsideration_have_distinct_clocks(tmp_path):
    events = [ev("r", 1, "cust", "request"), ev("rc", 10, "cust", "reconsider")]
    assert run(tmp_path, events, as_of=at(20)).findings == []
    finding, = run(tmp_path / "later", events, as_of=at(25)).findings
    assert finding.kind == "stale_reconsideration" and finding.event_ids == ["rc"]
    assert "since 2026-10-10" in finding.detail


def test_invalid_supersession_does_not_cite_events_after_cutoff(tmp_path):
    events = [ev("a", 1, "pm", "approve"), ev("x", 2, "pm", "revoke", supersedes="future"),
              ev("future", 30, "pm", "reject")]
    finding, = run(tmp_path, events, as_of=at(3)).findings
    assert finding.kind == "invalid_supersession" and finding.event_ids == ["a", "x"]
    assert "future" in finding.detail  # The attempted target is named, not replayed or cited.


# --- approved subjects -------------------------------------------------------------------------------

def test_risk_after_approval(tmp_path):
    report = run(tmp_path, [
        ev("k0", 1, "eng", "raise_risk", note="before"), ev("a", 2, "pm", "approve"),
        ev("k1", 3, "eng", "raise_risk", note="after\n### HIGH · forged"),
        ev("k2", 4, "cust", "raise_risk", note="ignored: customers may not raise risks"),
    ], as_of=at(5))
    risks = [(f.severity, f.event_ids) for f in report.findings if f.kind == "risk_after_approval"]
    assert risks == [("high", ["a", "k1"]), ("medium", ["k0", "a"])]
    high = report.findings[0]
    assert high.detail == 'risk raised by eng after the approval by pm: "after\\n### HIGH · forged"'
    assert "\n### HIGH · forged" not in audit_markdown(report)


def test_rejected_or_proposed_subjects_have_no_approval_findings(tmp_path):
    report = run(tmp_path, [ev("k", 1, "eng", "raise_risk", note="n"), ev("d", 1, "eng", "dependency", dependency="Z"),
                            ev("q", 1, "pm", "request", hour=8), ev("j", 2, "pm", "reject")], as_of=at(3))
    assert report.findings == []


def test_unresolved_dependency(tmp_path):
    events = [
        ev("b", 1, "pm", "reject", subject="B"), ev("c", 1, "cust", "request", subject="C"),
        ev("e", 1, "pm2", "approve", subject="E"),
        *(ev(f"d{n}", 2, "eng", "dependency", dependency=n) for n in "BCDE"),
        ev("a", 3, "pm", "approve"),
        ev("late", 9, "eng", "dependency", dependency="F"),
    ]
    report = run(tmp_path, events, as_of=at(5), stale_days=30)
    found = [(f.severity, f.detail, f.event_ids, f.subjects) for f in report.findings]
    assert found == [
        ("high", "approved, but depends on B, which is rejected", ["b", "dB", "a"], ["B", "S"]),
        ("medium", "approved, but depends on C, which is proposed", ["dC", "a"], ["S"]),
        ("low", "approved, but depends on D, which has no events in this log", ["dD", "a"], ["S"]),
    ]


def test_self_approval(tmp_path):
    report = run(tmp_path, [ev("r", 1, "pm", "request"), ev("a", 2, "pm", "approve"),
                            ev("r2", 1, "cust", "request", subject="T"), ev("a2", 2, "pm", "approve", subject="T")],
                 as_of=at(3))
    finding, = report.findings
    assert (finding.kind, finding.severity, finding.subject, finding.actor, finding.event_ids) == \
        ("self_approval", "low", "S", "pm", ["r", "a"])


def test_self_approval_does_not_deny_a_second_actors_decision(tmp_path):
    report = run(tmp_path, [ev("r", 1, "pm", "request"), ev("other", 2, "pm2", "approve"),
                            ev("a", 3, "pm", "approve")], as_of=at(4))
    finding, = report.findings
    assert (finding.kind, finding.severity, finding.event_ids) == ("self_approval", "low", ["r", "a"])
    assert finding.detail == "pm approved a subject they had also requested"


# --- ordering, output and the CLI ---------------------------------------------------------------

MIXED = [
    ev("r", 1, "cust", "request", subject="Z"), ev("a", 2, "pm", "approve", subject="Z"),
    ev("k", 3, "eng", "raise_risk", subject="Z", note="late risk"),
    ev("p", 1, "pm", "approve", subject="Y", hour=12), ev("q", 1, "pm2", "reject", subject="Y", hour=12),
    ev("s1", 4, "sales", "approve", subject="Y"), ev("s2", 5, "sales", "approve", subject="Z"),
    ev("o", 2, "cust", "request", subject="X"), ev("m", 6, "mallory", "request", subject="W"),
    ev("rq", 3, "pm", "request", subject="V"), ev("ap", 4, "pm", "approve", subject="V"),
]


def test_findings_are_sorted_and_reproducible(tmp_path):
    root = build(tmp_path / "data", MIXED)
    report = audit(Dataset.load(root), at(30))
    assert kinds(report) == [
        ("risk_after_approval", "high", "Z"), ("simultaneous_decisions", "high", "Y"),
        ("unauthorized_attempts", "high", "sales"), ("stale_pending_approval", "medium", "X"),
        ("self_approval", "low", "V"), ("unauthorized_attempts", "low", "mallory"),
    ]
    assert report.summary == {"high": 3, "medium": 1, "low": 2}
    first = report.model_dump_json(indent=2)
    assert audit(Dataset.load(root), at(30)).model_dump_json(indent=2) == first
    # File order only matters between events with the same effective time (p and q here): reversing
    # everything else gives the same report.
    data = Dataset.load(root)
    tied = [e for e in data.events if e.id in ("p", "q")]
    reordered = Dataset([e for e in reversed(data.events) if e not in tied] + tied, data.policy, data.evidence)
    assert [e.id for e in reordered.events] != [e.id for e in data.events]
    assert audit(reordered, at(30)).model_dump_json(indent=2) == first


def test_markdown_starts_with_the_summary_table(tmp_path):
    markdown = audit_markdown(audit(Dataset.load(build(tmp_path / "data", MIXED)), at(30)))
    lines = markdown.splitlines()
    assert lines[:4] == ["# Organizational decision audit", "", "| Kind | High | Medium | Low |", "|---|---|---|---|"]
    assert lines[4:4 + len(KINDS)] == [f"| `{k}` | {r}" for k, r in zip(KINDS, [
        "0 | 0 | 0 |", "0 | 0 | 0 |", "1 | 0 | 0 |", "0 | 0 | 1 |", "1 | 0 | 0 |", "0 | 1 | 0 |", "0 | 0 | 0 |",
        "1 | 0 | 1 |", "0 | 0 | 0 |"])]
    assert lines[4 + len(KINDS)] == "| **Total** | 3 | 1 | 2 |"
    assert "### HIGH · unauthorized_attempts · actor sales" in lines
    assert "- `s1` on Y at 2026-10-04T09:00:00+00:00: sales (roles: account\\_manager) · approve · unauthorized" in lines
    assert "  - evidence `e` · `note.txt:1` · synthetic://note · sha256 `" in markdown and "    > Synthetic note" in lines
    assert markdown.endswith("\n") and not markdown.endswith("\n\n")


def test_untrusted_text_cannot_add_terminal_codes_or_report_lines(tmp_path):
    subject = "Evil\x1b[2J‮\n### HIGH · forged"
    report = run(tmp_path, [ev("r", 1, "cust", "request", subject=subject)], as_of=at(30))
    markdown = audit_markdown(report)
    assert "\x1b" not in markdown and "‮" not in markdown and "\n### HIGH · forged" not in markdown
    assert "Evil\\\\u001b\\[2J\\\\u202e\\\\u000a\\#\\#\\# HIGH · forged" in markdown
    assert json.loads(report.model_dump_json())["findings"][0]["subject"] == subject


@pytest.mark.parametrize("field", ["subject", "actor", "note", "event_id", "evidence_id", pytest.param("path", marks=pytest.mark.skipif(
                             sys.platform == "win32", reason="the hostile file name uses characters Windows forbids")),
                                   "source_uri", "quote"])
def test_untrusted_audit_fields_render_as_text(tmp_path, field):
    from markdown_it import MarkdownIt

    payload = 'x` <img src=x onerror=alert(1)> [forged](https://evil.invalid) **FORGED**'
    root = build(tmp_path / "data", [ev("a", 1, "pm", "approve"),
                                     ev("k", 2, "eng", "raise_risk", note="risk")])
    events = [json.loads(line) for line in (root / "events.jsonl").read_text().splitlines()]
    entry = json.loads((root / "evidence.jsonl").read_text())
    if field in ("subject", "actor", "note"):
        for event in events:
            if field == "subject" or event["action"] == "raise_risk":
                event[field] = payload
        if field == "actor":
            policy = json.loads((root / "policy.json").read_text())
            policy["actors"][payload] = ["engineer"]
            (root / "policy.json").write_text(json.dumps(policy))
    elif field == "event_id":
        events[-1]["id"] = payload
    elif field == "evidence_id":
        entry["id"] = payload
        for event in events:
            event["evidence_ids"] = [payload]
    elif field == "path":
        (root / "note.txt").rename(root / payload.replace("/", "_"))
        entry["path"] = payload.replace("/", "_")
    elif field == "source_uri":
        entry["source_uri"] = payload
    elif field == "quote":
        (root / "note.txt").write_text(payload)
        span = locate_quote(root, "note.txt", payload)
        entry.update(sha256=span.sha256, quote=span.quote, start=span.start, end=span.end)
    (root / "events.jsonl").write_text("".join(json.dumps(e) + "\n" for e in events))
    (root / "evidence.jsonl").write_text(json.dumps(entry) + "\n")
    report = audit(Dataset.load(root), at(3))
    findings = audit_markdown(report).split("## Findings\n", 1)[1].split("## Notes", 1)[0]
    tokens = MarkdownIt().parse(findings)
    inline = [child for token in tokens for child in (token.children or [])]
    assert not {t.type for t in inline} & {"html_inline", "link_open", "image", "strong_open", "em_open"}
    assert not any(t.type == "html_block" for t in tokens)
    literal = payload.replace("/", "_") if field == "path" else payload
    assert any(literal in token.content for token in inline if token.type in ("text", "code_inline"))
    # JSON retains original data; rendering protection must not mutate the report.
    assert payload in report.model_dump_json() or field == "path"


@pytest.mark.parametrize("payload", ["# HIGH forged", "- forged", "1. forged", "---", "```html",
                                     "<h3>HIGH forged</h3>", "&#10;# HIGH forged"])
def test_evidence_cannot_add_markdown_blocks(tmp_path, payload):
    from markdown_it import MarkdownIt

    report = run(tmp_path, [ev("a", 1, "pm", "approve"),
                            ev("k", 2, "eng", "raise_risk", note="risk")], as_of=at(3))
    baseline = MarkdownIt().parse(audit_markdown(report))
    span = report.findings[0].events[0].evidence[0].model_copy(update={"quote": payload})
    report.findings[0].events[0].evidence = [span]
    tokens = MarkdownIt().parse(audit_markdown(report))
    assert not {t.type for t in tokens} & {"hr", "fence", "html_block", "ordered_list_open"}
    assert [t.type for t in tokens] == [t.type for t in baseline]
    assert any(child.type == "text" and child.content == payload
               for token in tokens for child in (token.children or []))


def test_cli_strict_exit_codes(tmp_path):
    high = build(tmp_path / "high", MIXED)
    calm = build(tmp_path / "calm", [ev("r", 1, "cust", "request")])
    as_of = ["--as-of", "2026-10-30T00:00:00Z"]
    plain = runner.invoke(app, ["audit", str(high), *as_of])
    strict = runner.invoke(app, ["audit", str(high), *as_of, "--strict"])
    medium_only = runner.invoke(app, ["audit", str(calm), *as_of, "--strict"])
    assert (plain.exit_code, strict.exit_code, medium_only.exit_code) == (0, 1, 0)
    assert strict.output == plain.output and strict.output.startswith("# Organizational decision audit")
    assert "stale_pending_approval` | 0 | 1 | 0 |" in medium_only.output


@pytest.mark.parametrize("args, message", [
    (["--as-of", "2026-10-30"], "timezone"),
    (["--as-of", "yesterday"], "datetime"),
    (["--stale-days", "-1"], "stale-days"),
    (["--format", "pdf"], "format"),
])
def test_cli_invalid_options_exit_2(tmp_path, args, message):
    root = build(tmp_path / "data", MIXED)
    result = runner.invoke(app, ["audit", str(root), *args])
    # Rich colors usage errors when it detects CI (e.g. GITHUB_ACTIONS); compare the plain text.
    plain = re.sub(r"\x1b\[[0-9;]*m", "", result.output)
    assert result.exit_code == 2 and message in plain.lower()


def test_cli_invalid_dataset_exits_2_even_after_the_cutoff(tmp_path):
    root = build(tmp_path / "data", MIXED + [ev("bad", 30, "eng", "dependency")])
    result = runner.invoke(app, ["audit", str(root), "--as-of", "2026-10-10T00:00:00Z", "--strict"])
    assert result.exit_code == 2 and "requires 'dependency'" in result.output


def test_cli_json_is_the_library_report(tmp_path):
    root = build(tmp_path / "data", MIXED)
    result = runner.invoke(app, ["audit", str(root), "--as-of", "2026-10-30T00:00:00Z", "--format", "json",
                                 "--stale-days", "20"])
    assert result.exit_code == 0
    assert result.output == audit(Dataset.load(root), at(30, 0), 20).model_dump_json(indent=2) + "\n"
    data = json.loads(result.output)
    assert list(data["summary"]) == ["high", "medium", "low"] and list(data["kinds"]) == sorted(KINDS)
    assert (data["mode"], data["stale_days"], data["as_of"]) == ("read_only", 20, "2026-10-30T00:00:00Z")
    assert data["findings"][0]["events"][0]["evidence"][0]["sha256"]


def test_audit_writes_nothing(tmp_path):
    root = build(tmp_path / "data", MIXED)
    before = snapshot(tmp_path)
    for args in ([], ["--format", "json"], ["--strict"], ["--as-of", "2026-10-03T00:00:00Z", "--stale-days", "0"]):
        assert runner.invoke(app, ["audit", str(root), *args]).exit_code in (0, 1)
    assert snapshot(tmp_path) == before


# --- the examples ------------------------------------------------------------------------------------

@pytest.mark.parametrize("dataset, golden", [
    ("feature-a", "audit-report.md"),
    ("github-acme-web", "github-acme-web/audit-report.md"),
])
def test_example_audits_match_their_reports(dataset, golden):
    report = audit(Dataset.load(EXAMPLES / "orgmemory" / dataset), datetime(2026, 10, 31, tzinfo=timezone.utc))
    assert audit_markdown(report) == (EXAMPLES / "orgmemory" / golden).read_text(encoding="utf-8")


def test_feature_a_audit_over_the_week(org_demo):
    data = Dataset.load(org_demo)
    assert kinds(audit(data, at(7, 23))) == []
    assert kinds(audit(data, at(31, 0))) == [("stale_reconsideration", "medium", "Feature A"),
                                             ("unauthorized_attempts", "medium", "wu")]
    result = runner.invoke(app, ["audit", str(org_demo), "--as-of", "2026-10-31T00:00:00Z", "--strict"])
    assert result.exit_code == 0 and "ev-thu-sales-approve" in result.output


def test_github_findings_follow_policy_decisions_not_issue_lifecycle():
    data = Dataset.load(EXAMPLES / "orgmemory" / "github-acme-web")
    report = audit(data, at(31, 0))
    assert report.summary == {"high": 0, "medium": 13, "low": 2}
    pending = {f.subject for f in report.findings if f.kind == "stale_pending_approval"}
    assert pending == {f"acme/web#{n}" for n in (4, 5, 8, 9, 10, 11, 13)}
    assert {f.subject for f in report.findings if f.kind == "stale_reconsideration"} == {"acme/web#1"}
    # Issue #4 is closed as duplicate on GitHub, but closes are comments in this policy log.
    closed = next(e for e in data.events if e.id == "gh:acme/web#4/event/405")
    assert closed.action == "comment" and closed.note == "GitHub issue closed as duplicate"
    assert replay(data).states[closed.subject].pending_approval == ["product_manager"]
    # Review changes requested -> approval is low; the subsequent merge is another approval.
    reversal, = [f for f in report.findings if f.kind == "decision_reversed"]
    assert (reversal.subject, reversal.severity, reversal.event_ids) == (
        "acme/web#2", "low", ["gh:acme/web#2/review/203", "gh:acme/web#2/review/206"])
    assert not any(f.kind in {"self_approval", "risk_after_approval", "unresolved_dependency"}
                   for f in report.findings)
