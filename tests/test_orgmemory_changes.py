"""`orgmemory changes`: every change kind, the period's boundaries, agreement with replay, ordering, escaping,
the CLI and the example reports."""
import json
import re
from datetime import datetime, timezone
from itertools import product

import pytest
from typer.testing import CliRunner

from conftest import EXAMPLES, snapshot, write_text_exact
from memcore import EvidenceSpan, ManifestEntry, locate_quote
from orgmemory.changes import KINDS, changes
from orgmemory.cli import app
from orgmemory.models import Event, Policy
from orgmemory.replay import Dataset, replay
from orgmemory.report import changes_markdown

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
    (root / "note.txt").write_bytes(b"Synthetic note for the changes tests.\n")
    span = locate_quote(root, "note.txt", "Synthetic note")
    entry = ManifestEntry(id="e", path=span.path, sha256=span.sha256, quote=span.quote, start=span.start,
                          end=span.end, source_uri="synthetic://note", source_kind="note",
                          captured_at="2026-10-01T00:00:00Z")
    (root / "evidence.jsonl").write_bytes((json.dumps(entry.model_dump(mode="json")) + "\n").encode("utf-8"))
    (root / "policy.json").write_bytes(json.dumps(policy).encode("utf-8"))
    (root / "events.jsonl").write_bytes("".join(json.dumps(e) + "\n" for e in events).encode("utf-8"))
    return root


def run(tmp_path, events, start, end, policy=POLICY):
    return changes(Dataset.load(build(tmp_path / "data", events, policy)), start, end)


def kinds(report, subject="S"):
    found, = [s for s in report.subjects if s.subject == subject]
    return [(c.kind, c.event_ids) for c in found.changes]


# --- each kind of change ---------------------------------------------------------------------------

def test_feature_a_week(org_demo):
    report = changes(Dataset.load(org_demo), at(5, 23), at(8, 23))
    item, = report.subjects
    assert (item.subject, item.status_before, item.status_after) == ("Feature A", "proposed", "rejected")
    assert [(c.kind, c.event_ids) for c in item.changes] == [
        ("decision", ["ev-wed-reject"]),
        ("pending_approval_opened", ["ev-thu-request"]),
        ("pending_approval_resolved", ["ev-mon-request", "ev-wed-reject"]),
        ("reconsideration_opened", ["ev-thu-request"]),
        ("risk_raised", ["ev-tue-risk"]),
        ("unauthorized_attempt", ["ev-thu-sales-approve"]),
    ]
    decision = item.changes[0]
    assert (decision.detail, decision.status_before, decision.status_after) == ("proposed → rejected", "proposed",
                                                                                "rejected")
    cited, = decision.events
    assert (cited.actor, cited.roles, cited.action, cited.outcome) == ("zhou", ["product_manager"], "reject", "applied")
    assert cited.evidence[0].quote.startswith("Feature A is rejected")
    assert item.changes[1].detail == ("pending approval from product_manager since 2026-10-08T08:45:00+00:00; "
                                      "still open at the end of the period")
    assert item.changes[2].detail == ("pending approval from product_manager since 2026-10-05T09:12:00+00:00; "
                                      "closed by reject by zhou at 2026-10-07T15:30:00+00:00")
    assert item.changes[4].detail == 'raised by lin: "Bulk export reads the billing schema, which is mid-migration"'
    assert item.changes[5].detail == "ignored: roles account_manager may not approve"
    assert all(c.status_before is None for c in item.changes[1:])
    assert (report.events_total, report.events_before, report.events_in_period) == (5, 1, 4)
    assert (report.subjects_total, report.subjects_in_period, report.subjects_changed) == (1, 1, 1)
    assert list(report.summary) == list(KINDS)
    assert report.summary["decision"] == 1 and sum(report.summary.values()) == 6


def test_a_decision_that_confirms_the_status_is_listed(tmp_path):
    report = run(tmp_path, [ev("a", 1, "pm", "approve"), ev("a2", 3, "pm2", "approve")], at(2), at(4))
    change, = report.subjects[0].changes
    assert (change.kind, change.status_before, change.status_after) == ("decision", "approved", "approved")
    assert change.detail == "approved → approved; the status is unchanged, and this is now the authorizing decision"


def test_revoke_is_a_decision_and_reopens_the_pending_approval(tmp_path):
    report = run(tmp_path, [ev("a", 1, "pm", "approve"), ev("v", 3, "pm", "revoke", supersedes="a")], at(2), at(4))
    assert kinds(report) == [("decision", ["v"]), ("pending_approval_opened", ["v"])]
    assert report.subjects[0].changes[0].detail == "approved → proposed"


def test_periods_opened_and_closed_inside_the_period(tmp_path):
    events = [ev("r", 2, "cust", "request"), ev("a", 3, "pm", "approve"), ev("q", 4, "cust", "request"),
              ev("rc", 5, "sales", "reconsider"), ev("j", 6, "pm", "reject")]
    report = run(tmp_path, events, at(1), at(7))
    assert kinds(report) == [
        ("decision", ["a"]), ("decision", ["j"]),
        ("pending_approval_opened", ["r"]), ("pending_approval_opened", ["q", "rc"]),
        ("pending_approval_resolved", ["r", "a"]), ("pending_approval_resolved", ["q", "j"]),
        ("reconsideration_opened", ["q", "rc"]), ("reconsideration_resolved", ["q", "j"]),
        ("conflict", ["a", "j"]),
    ]
    opened = report.subjects[0].changes[3]
    assert opened.detail.endswith("; closed by reject by pm at 2026-10-06T09:00:00+00:00")
    assert report.subjects[0].status_before is None and report.subjects[0].status_after == "rejected"


def test_a_pending_approval_open_before_and_after_is_not_a_change(tmp_path):
    report = run(tmp_path, [ev("r", 1, "cust", "request"), ev("r2", 3, "sales", "request"),
                            ev("c", 3, "eng", "comment")], at(2), at(4))
    assert report.subjects == [] and report.events_in_period == 2 and report.subjects_in_period == 1


def test_new_risks_dependencies_and_conflicts_only(tmp_path):
    events = [
        ev("k0", 1, "eng", "raise_risk", note="old"), ev("d0", 1, "eng", "dependency", dependency="B"),
        ev("j", 1, "pm", "reject"), ev("a", 1, "pm2", "approve", hour=10),
        ev("k1", 3, "eng", "raise_risk", note="new\n### HIGH · forged"),
        ev("d1", 3, "eng", "dependency", dependency="B"), ev("d2", 3, "eng", "dependency", dependency="C"),
        ev("d3", 3, "pm", "dependency", dependency="Z", hour=10),
        ev("j2", 4, "pm", "reject"),
        ev("z", 1, "pm", "approve", subject="Z"),
    ]
    report = run(tmp_path, events, at(2), at(5))
    assert kinds(report) == [("decision", ["j2"]), ("risk_raised", ["k1"]), ("dependency_added", ["d2"]),
                             ("dependency_added", ["d3"]), ("conflict", ["a", "j2"])]
    by_kind = {(c.kind, c.event_ids[-1]): c.detail for c in report.subjects[0].changes}
    assert by_kind[("risk_raised", "k1")] == 'raised by eng: "new\\n### HIGH · forged"'
    assert by_kind[("dependency_added", "d2")] == ("now depends on C, which has no events in this log up to the end "
                                                   "of the period")
    assert by_kind[("dependency_added", "d3")] == "now depends on Z, which is approved at the end of the period"
    assert by_kind[("conflict", "j2")] == "decision_reversed: approve by pm2 then reject by pm"
    assert [s.subject for s in report.subjects] == ["S"]  # Z had no events in the period


def test_invalid_revoke_is_a_conflict_not_a_decision(tmp_path):
    report = run(tmp_path, [ev("a", 1, "pm", "approve"), ev("x", 3, "pm", "revoke", supersedes="a2"),
                            ev("a2", 1, "pm", "approve", subject="T")], at(2), at(4))
    change, = report.subjects[0].changes
    assert (change.kind, change.event_ids) == ("conflict", ["x"])
    assert change.detail == "invalid_supersession: revoke targets a2, but the current decision is a"
    assert change.events[0].outcome == "invalid_supersession"


def test_unauthorized_attempts_but_not_comments(tmp_path):
    report = run(tmp_path, [ev("s", 3, "sales", "approve"), ev("m", 3, "mallory", "reject", hour=10),
                            ev("k", 3, "cust", "raise_risk", note="n", hour=11),
                            ev("c", 3, "mallory", "comment", hour=12)], at(2), at(4))
    assert kinds(report) == [("unauthorized_attempt", ["s"]), ("unauthorized_attempt", ["m"]),
                             ("unauthorized_attempt", ["k"])]
    assert [c.detail for c in report.subjects[0].changes] == [
        "ignored: roles account_manager may not approve", "ignored: an actor without a role may not reject",
        "ignored: roles customer may not raise_risk"]
    # The subject is new in the period, so it is listed with no prior status.
    assert (report.subjects[0].status_before, report.subjects[0].status_after) == (None, "proposed")


def test_a_new_subject_is_listed_even_without_changes(tmp_path):
    report = run(tmp_path, [ev("c", 3, "cust", "comment"), ev("c0", 1, "cust", "comment", subject="T"),
                            ev("c1", 3, "cust", "comment", subject="T")], at(2), at(4))
    assert [(s.subject, s.status_before, s.status_after, s.changes) for s in report.subjects] == [
        ("S", None, "proposed", [])]
    assert "| S | no events | proposed | none |" in changes_markdown(report)


def test_no_pending_approval_without_a_decision_role(tmp_path):
    policy = {"actors": {"pm": ["approver"], "cust": ["customer"]},
              "permissions": {"request": ["customer"], "approve": ["approver"]}}
    report = run(tmp_path, [ev("r", 3, "cust", "request"), ev("a", 4, "pm", "approve")], at(2), at(5), policy)
    assert kinds(report) == [("decision", ["a"])]
    assert replay(Dataset.load(tmp_path / "data"), at(3, 12)).states["S"].pending_approval == []


# --- the period ---------------------------------------------------------------------------------------

def test_period_excludes_its_start_and_includes_its_end(tmp_path):
    events = [ev("r", 2, "cust", "request"), ev("a", 3, "pm", "approve"), ev("k", 4, "eng", "raise_risk", note="n")]
    report = run(tmp_path, events, at(2), at(4))
    assert kinds(report) == [("decision", ["a"]), ("pending_approval_resolved", ["r", "a"]), ("risk_raised", ["k"])]
    assert (report.events_before, report.events_in_period) == (1, 2)


def test_an_empty_period(tmp_path):
    root = build(tmp_path / "data", [ev("r", 2, "cust", "request")])
    report = changes(Dataset.load(root), at(2), at(2))
    assert report.subjects == [] and report.events_in_period == 0 and set(report.summary.values()) == {0}
    markdown = changes_markdown(report)
    assert "| **Total** | | | none |" in markdown and "No changes in the period." in markdown
    with pytest.raises(ValueError, match="must not start after it ends"):
        changes(Dataset.load(root), at(3), at(2))


def test_events_after_the_period_are_not_read_into_it(tmp_path):
    report = run(tmp_path, [ev("r", 2, "cust", "request"), ev("a", 9, "pm", "approve")], at(1), at(5))
    change, = report.subjects[0].changes
    assert (change.kind, change.detail) == (
        "pending_approval_opened",
        "pending approval from product_manager since 2026-10-02T09:00:00+00:00; still open at the end of the period")


def test_changes_agree_with_replay_at_both_ends():
    """For every short sequence and every split, the changes account for the difference between the replays."""
    evidence = {"e": EvidenceSpan(path="note.txt", sha256="0" * 64, start=0, end=1, quote="S", line=1, id="e")}
    policy = Policy.model_validate(POLICY)
    actions = ["request", "reconsider", "approve", "reject", "revoke"]
    checked = 0
    for sequence in product(actions, ["pm", "sales"], repeat=3):
        steps = list(zip(sequence[::2], sequence[1::2]))
        events, current = [], None
        for index, (action, actor) in enumerate(steps):
            fields = {"supersedes": current or f"x{index}"} if action == "revoke" else {}
            events.append(Event.model_validate(ev(f"x{index}", 1 + index, actor, action, **fields)))
            if actor == "pm" and (action in ("approve", "reject") or action == "revoke" and current):
                current = f"x{index}"
        data = Dataset(events, policy, evidence)
        for start, end in product(range(0, 4), repeat=2):
            if start > end:
                continue
            report = changes(data, at(1 + start, 0), at(1 + end, 0))
            old = replay(data, at(1 + start, 0)).states.get("S")
            new = replay(data, at(1 + end, 0)).states.get("S")
            listed = report.subjects[0].changes if report.subjects else []
            count = {kind: sum(c.kind == kind for c in listed) for kind in KINDS}
            for field, kind in (("pending_approval", "pending_approval"),
                                ("reconsideration_requested", "reconsideration")):
                was = bool(old and getattr(old, field))
                now = bool(new and getattr(new, field))
                assert was + count[f"{kind}_opened"] - count[f"{kind}_resolved"] == now, (steps, start, end)
            statuses = [old.current_status if old else "proposed"]
            for change in listed:
                if change.kind == "decision":
                    assert change.status_before == statuses[-1], (steps, start, end)
                    statuses.append(change.status_after)
            assert statuses[-1] == (new.current_status if new else "proposed"), (steps, start, end)
            checked += 1
    assert checked == 1000 * 10


def test_report_is_reproducible_and_ignores_file_order_except_ties(tmp_path):
    events = [
        ev("r", 1, "cust", "request", subject="Z"), ev("a", 3, "pm", "approve", subject="Z"),
        ev("k", 4, "eng", "raise_risk", subject="Z", note="late risk"),
        ev("p", 3, "pm", "approve", subject="Y", hour=12), ev("q", 3, "pm2", "reject", subject="Y", hour=12),
        ev("s1", 4, "sales", "approve", subject="Y"), ev("o", 3, "cust", "request", subject="X"),
    ]
    root = build(tmp_path / "data", events)
    first = changes(Dataset.load(root), at(2), at(5))
    assert [s.subject for s in first.subjects] == ["X", "Y", "Z"]
    assert changes(Dataset.load(root), at(2), at(5)).model_dump_json() == first.model_dump_json()
    data = Dataset.load(root)
    tied = [e for e in data.events if e.id in ("p", "q")]
    reordered = Dataset([e for e in reversed(data.events) if e not in tied] + tied, data.policy, data.evidence)
    assert changes(reordered, at(2), at(5)).model_dump_json() == first.model_dump_json()


# --- Markdown --------------------------------------------------------------------------------------------

def test_markdown_starts_with_the_summary_table(org_demo):
    lines = changes_markdown(changes(Dataset.load(org_demo), at(5, 23), at(8, 23))).splitlines()
    assert lines[:4] == ["# Organizational decision changes", "",
                         "| Subject | Status before | Status after | Changes |", "|---|---|---|---|"]
    assert lines[4] == ("| Feature A | proposed | rejected | `decision` ×1, `pending_approval_opened` ×1, "
                        "`pending_approval_resolved` ×1, `reconsideration_opened` ×1, `risk_raised` ×1, "
                        "`unauthorized_attempt` ×1 |")
    assert lines[5].startswith("| **Total** | | | `decision` ×1, ")
    assert "- Period: after 2026-10-05T23:00:00+00:00, up to and including 2026-10-08T23:00:00+00:00" in lines
    assert "Status: proposed → rejected" in lines and "### unauthorized_attempt" in lines
    assert "- `ev-thu-sales-approve` at 2026-10-08T11:20:00+00:00: wu (roles: account\\_manager) · approve · " \
           "unauthorized" in lines


PAYLOAD = 'x` <img src=x onerror=alert(1)> [forged](https://evil.invalid) **FORGED**'


@pytest.mark.parametrize("field", ["subject", "actor", "note", "dependency", "event_id", "evidence_id", "source_uri",
                                   "quote"])
def test_untrusted_fields_render_as_text(tmp_path, field):
    from markdown_it import MarkdownIt

    root = build(tmp_path / "data", [ev("r", 1, "cust", "request"), ev("a", 3, "pm", "approve"),
                                     ev("k", 3, "eng", "raise_risk", note="risk", hour=10),
                                     ev("d", 3, "eng", "dependency", dependency="B", hour=11),
                                     ev("s", 3, "sales", "reject", hour=12)])
    events = [json.loads(line) for line in (root / "events.jsonl").read_text().splitlines()]
    entry = json.loads((root / "evidence.jsonl").read_text())
    if field in ("subject", "actor"):
        for event in events:
            if field == "subject" or event["action"] == "approve":
                event[field] = PAYLOAD
        if field == "actor":
            policy = json.loads((root / "policy.json").read_text())
            policy["actors"][PAYLOAD] = ["product_manager"]
            write_text_exact(root / "policy.json", json.dumps(policy))
    elif field in ("note", "dependency"):
        for event in events:
            if event.get(field):
                event[field] = PAYLOAD
    elif field == "event_id":
        events[1]["id"] = PAYLOAD
    elif field == "evidence_id":
        entry["id"] = PAYLOAD
        for event in events:
            event["evidence_ids"] = [PAYLOAD]
    elif field == "source_uri":
        entry["source_uri"] = PAYLOAD
    elif field == "quote":
        (root / "note.txt").write_bytes((PAYLOAD + "\n### HIGH forged\x1b[2J\u202e").encode("utf-8"))
        span = locate_quote(root, "note.txt", PAYLOAD + "\n### HIGH forged\x1b[2J\u202e")
        entry.update(sha256=span.sha256, quote=span.quote, start=span.start, end=span.end)
    write_text_exact(root / "events.jsonl", "".join(json.dumps(e) + "\n" for e in events))
    write_text_exact(root / "evidence.jsonl", json.dumps(entry) + "\n")
    report = changes(Dataset.load(root), at(2), at(4))
    markdown = changes_markdown(report)
    assert "\x1b" not in markdown and "\u202e" not in markdown and "\n### HIGH forged" not in markdown
    tokens = MarkdownIt().parse(markdown)
    assert not any(t.type in ("html_block", "hr", "fence") for t in tokens)
    sections = MarkdownIt().parse(markdown.split("\n## ", 1)[1].split("\n## Notes", 1)[0])
    inline = [child for token in sections for child in (token.children or [])]
    assert not {t.type for t in inline} & {"html_inline", "link_open", "image", "strong_open", "em_open"}
    assert any(PAYLOAD in token.content for token in inline if token.type in ("text", "code_inline"))
    table = MarkdownIt().enable("table").parse(markdown.split("\n- Period", 1)[0])
    cells = [child for token in table for child in (token.children or [])]
    assert not {t.type for t in cells} & {"html_inline", "link_open", "image", "em_open"}
    assert sum(t.type == "th_open" for t in table) == 4  # the subject cannot add table columns
    assert PAYLOAD in report.model_dump_json()  # escaping is for Markdown only; JSON keeps the data


# --- the CLI -------------------------------------------------------------------------------------------------

def test_cli_json_is_the_library_report(tmp_path):
    root = build(tmp_path / "data", [ev("r", 1, "cust", "request"), ev("a", 3, "pm", "approve")])
    result = runner.invoke(app, ["changes", str(root), "--from", "2026-10-02T00:00:00Z", "--to",
                                 "2026-10-04T00:00:00+08:00", "--format", "json"])
    assert result.exit_code == 0, result.output
    expected = changes(Dataset.load(root), at(2, 0), datetime.fromisoformat("2026-10-04T00:00:00+08:00"))
    assert result.output == expected.model_dump_json(indent=2) + "\n"
    data = json.loads(result.output)
    assert (data["mode"], data["from_time"], data["to_time"]) == ("read_only", "2026-10-02T00:00:00Z",
                                                                  "2026-10-04T00:00:00+08:00")
    assert list(data["summary"]) == list(KINDS)
    assert data["subjects"][0]["changes"][0]["events"][0]["evidence"][0]["sha256"]
    markdown = runner.invoke(app, ["changes", str(root), "--from", "2026-10-02T00:00:00Z", "--to",
                                   "2026-10-04T00:00:00+08:00"])
    assert markdown.exit_code == 0 and markdown.output == changes_markdown(expected)


@pytest.mark.parametrize("args, message", [
    (["--from", "2026-10-02", "--to", "2026-10-04T00:00:00Z"], "--from must be an iso 8601 time with a timezone"),
    (["--from", "2026-10-02T00:00:00Z", "--to", "tomorrow"], "--to must be an iso 8601 time with a timezone"),
    (["--from", "2026-10-05T00:00:00Z", "--to", "2026-10-04T00:00:00Z"], "--from must not be after --to"),
    (["--to", "2026-10-04T00:00:00Z"], "missing option '--from'"),
    (["--from", "2026-10-02T00:00:00Z"], "missing option '--to'"),
    (["--from", "2026-10-02T00:00:00Z", "--to", "2026-10-04T00:00:00Z", "--format", "html"], "format"),
])
def test_cli_invalid_options_exit_2(tmp_path, args, message):
    root = build(tmp_path / "data", [ev("r", 1, "cust", "request")])
    result = runner.invoke(app, ["changes", str(root), *args])
    plain = re.sub(r"\x1b\[[0-9;]*m", "", result.output)
    assert result.exit_code == 2 and message in plain.lower(), plain
    assert "Traceback" not in plain


def test_cli_invalid_dataset_exits_2_even_after_the_period(tmp_path):
    root = build(tmp_path / "data", [ev("r", 1, "cust", "request"), ev("bad", 30, "eng", "dependency")])
    result = runner.invoke(app, ["changes", str(root), "--from", "2026-10-01T00:00:00Z",
                                 "--to", "2026-10-10T00:00:00Z"])
    assert result.exit_code == 2 and "requires 'dependency'" in result.output


def test_changes_write_nothing(tmp_path):
    root = build(tmp_path / "data", [ev("r", 1, "cust", "request"), ev("a", 3, "pm", "approve")])
    before = snapshot(tmp_path)
    for args in ([], ["--format", "json"]):
        result = runner.invoke(app, ["changes", str(root), "--from", "2026-10-01T00:00:00Z",
                                     "--to", "2026-10-10T00:00:00Z", *args])
        assert result.exit_code == 0
    assert snapshot(tmp_path) == before


# --- the examples ------------------------------------------------------------------------------------------

GOLDEN = [
    ("feature-a", "changes-report.md", "2026-10-05T23:59:59Z", "2026-10-08T23:59:59Z"),
    ("github-acme-web", "github-acme-web/changes-report.md", "2026-10-05T23:59:59Z", "2026-10-10T23:59:59Z"),
]


@pytest.mark.parametrize("dataset, golden, start, end", GOLDEN)
def test_example_changes_match_their_reports(dataset, golden, start, end):
    """The golden reports are exact bytes of `orgmemory changes` with these options."""
    expected = (EXAMPLES / "orgmemory" / golden).read_bytes()
    parse = datetime.fromisoformat
    report = changes(Dataset.load(EXAMPLES / "orgmemory" / dataset), parse(start.replace("Z", "+00:00")),
                     parse(end.replace("Z", "+00:00")))
    assert changes_markdown(report).encode("utf-8") == expected
    before = snapshot(EXAMPLES)
    result = runner.invoke(app, ["changes", str(EXAMPLES / "orgmemory" / dataset), "--from", start, "--to", end])
    assert result.exit_code == 0 and result.stdout_bytes == expected
    assert snapshot(EXAMPLES) == before


def test_github_changes_follow_policy_decisions_not_issue_lifecycle():
    report = changes(Dataset.load(EXAMPLES / "orgmemory" / "github-acme-web"), at(5, 23), at(10, 23))
    status = {s.subject: (s.status_before, s.status_after) for s in report.subjects}
    assert status == {"acme/web#1": ("proposed", "rejected"), "acme/web#11": ("proposed", "proposed"),
                      "acme/web#13": ("proposed", "proposed"), "acme/web#2": ("proposed", "approved"),
                      "acme/web#5": ("proposed", "proposed"), "acme/web#6": ("proposed", "rejected"),
                      "acme/web#7": ("proposed", "approved")}
    # Issue #4 was closed and reopened on GitHub, but closes are comments in this policy log: no change.
    assert "acme/web#4" not in status
    attempts = {c.event_ids[0] for s in report.subjects for c in s.changes if c.kind == "unauthorized_attempt"}
    assert attempts == {"gh:acme/web#1/event/107", "gh:acme/web#1/event/108", "gh:acme/web#2/review/205",
                        "gh:acme/web#11/review/1101", "gh:acme/web#6/event/602"}
