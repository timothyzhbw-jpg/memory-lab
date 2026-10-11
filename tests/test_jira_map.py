"""The Jira mapping contract and its authority boundaries, entirely offline."""
import copy
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from jira_fakes import (ACCOUNTS, SITE, T1, T2, T3, T4, T5, aid, capture, comment, history, issue, page, single,
                        status, user)
from memcore import append_jsonl
from memcore.evidence import verify_entry
from orgmemory.jira_map import SEP, JiraCaptureError, JiraConfig, _ts, adf_text, convert
from orgmemory.replay import Dataset, replay

FIXTURES = Path(__file__).parent / "fixtures" / "jira"
CONFIG = JiraConfig(statuses={"10003": "approve", "10004": "reject", "10005": "revoke", "10007": "reconsider"})
BASE = "jira:ACME-1"


def recording(name):
    return json.loads((FIXTURES / f"{name}.json").read_bytes())


def plan(capture, config=CONFIG, existing=None, evidence_ids=None):
    return convert(capture, existing or {}, evidence_ids or set(), config)


def rows(p):
    return [(e.id, e.action, e.actor, e.supersedes) for e in p.events]


def materialize(root, p):
    for c in p.captures:
        path = root / c.path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(c.data)
        for entry in c.entries:
            append_jsonl(root / "evidence.jsonl", entry.model_dump(mode="json"))
    for event in p.events:
        append_jsonl(root / "events.jsonl", event.model_dump(mode="json", exclude_none=True))


# --- mapping ---------------------------------------------------------------------------------------

def test_mapping_rules():
    p = plan(single(
        history(1, "lin", T1, status("10000", "10002"),
                {"field": "assignee", "fieldtype": "jira", "fieldId": "assignee", "from": None, "to": "x"}),
        history(2, "zhou", T2, status("10002", "10003")),
        history(3, "zhou", T3, status("10003", "10005")),
        history(4, "zhou", T4, status("10005", "10004")),
        history(5, "buyer", T5, status("10004", "10007")),
        history(6, "zhou", "2026-10-10T09:00:00.000+0000", status("10007", "10005")),
        comments=[comment(100, "buyer", T1, "Please hurry.")]))
    assert rows(p) == [
        (f"{BASE}/created", "request", aid("lin"), None),
        (f"{BASE}/comment/100", "comment", aid("buyer"), None),
        (f"{BASE}/transition/1", "comment", aid("lin"), None),
        (f"{BASE}/transition/2", "approve", aid("zhou"), None),
        (f"{BASE}/transition/3", "revoke", aid("zhou"), f"{BASE}/transition/2"),
        (f"{BASE}/transition/4", "reject", aid("zhou"), None),
        (f"{BASE}/transition/5", "reconsider", aid("buyer"), None),
        (f"{BASE}/transition/6", "revoke", aid("zhou"), f"{BASE}/transition/4"),
    ]
    notes = {e.id.rsplit("/", 1)[-1]: e.note for e in p.events}
    assert notes["created"] == "Jira issue created" and notes["100"] == "Jira comment"
    assert notes["1"] == "Jira transition from status 10000 to 10002"
    assert notes["2"] == "Jira transition from status 10002 to 10003, mapped to approve in jira.json"
    assert notes["3"] == ("Jira transition from status 10003 to 10005, mapped to revoke in jira.json; "
                          f"withdraws {BASE}/transition/2")
    assert all(e.subject == "ACME-1" for e in p.events)
    assert p.unmapped == 1 and p.statuses == ["10000", "10002", "10003", "10004", "10005", "10007"]
    assert p.complete and p.skipped == []


def test_revoke_without_a_decision_is_a_comment_and_targets_come_from_the_log():
    p = plan(single(history(1, "zhou", T1, status("10000", "10005"))))
    assert rows(p)[1:] == [(f"{BASE}/transition/1", "comment", aid("zhou"), None)]
    assert p.events[1].note.endswith("mapped to revoke in jira.json; no decision to withdraw")
    # The log, not this run's configuration, says whether an earlier transition was a decision.
    decided = single(history(1, "zhou", T1, status("10000", "10003")), history(2, "zhou", T2, status("10003", "10005")))
    first = plan(decided, config=JiraConfig())
    existing = {e.id: e for e in first.events}
    again = plan(decided, existing=existing, evidence_ids=set(first.spans))
    # Transition 1 would now be an approve: drift. Transition 2 is still a comment, because the logged
    # transition 1 is no decision it could withdraw.
    assert again.events == [] and again.drift == [f"{BASE}/transition/1"]
    logged_approve = {**existing, f"{BASE}/transition/1": existing[f"{BASE}/transition/1"].model_copy(
        update={"action": "approve"})}
    del logged_approve[f"{BASE}/transition/2"]
    revoke, = plan(decided, existing=logged_approve, evidence_ids=set(first.spans)).events
    assert revoke.supersedes == f"{BASE}/transition/1"


def test_actor_comes_from_one_structured_field():
    impostor = user("mallory", displayName="Zhou (Product)")
    mention = {"type": "doc", "content": [{"type": "paragraph", "content": [
        {"type": "mention", "attrs": {"id": ACCOUNTS["zhou"], "text": "@Zhou (Product)"}},
        {"type": "text", "text": " approved this, moving on"}]}]}
    edited = comment(100, "mallory", T1, mention, updateAuthor=user("zhou"), updated=T2)
    p = plan(capture(page(issue(1, creator="lin", reporter=user("zhou"), comments=[edited],
                                histories=[history(1, impostor, T3, status("10000", "10003"))]))))
    assert [(e.id, e.actor) for e in p.events] == [
        (f"{BASE}/created", aid("lin")), (f"{BASE}/comment/100", aid("mallory")),
        (f"{BASE}/transition/1", aid("mallory"))]
    assert p.events[1].note == f"Jira comment; edited before capture by {aid('zhou')}"
    assert p.events[2].action == "approve"  # replay decides that mallory's approval changes nothing


def test_text_never_decides():
    p = plan(recording("capture-1"))
    acme3 = [e for e in p.events if e.subject == "ACME-3"]
    assert [(e.id.split("/", 1)[1], e.action, e.actor) for e in acme3] == [
        ("created", "request", aid("mallory")), ("comment/10300", "comment", aid("mallory")),
        ("comment/10301", "comment", aid("mallory")), ("comment/10302", "comment", aid("mallory")),
        ("transition/20302", "approve", aid("mallory"))]
    # The custom field named "status" (history 20301) is not a transition.
    assert not any("20301" in e.id for e in p.events)
    assert aid("zhou") not in {e.actor for e in acme3}


@pytest.mark.parametrize("kind", ["app", "unknown", None, "robot"])
def test_automation_is_comment_only(kind):
    bot = user("automation", accountType=kind) if kind else {k: v for k, v in user("automation").items()
                                                             if k != "accountType"}
    p = plan(single(history(1, "zhou", T1, status("10000", "10003")), history(2, bot, T2, status("10003", "10005")),
                    history(3, bot, T3, status("10005", "10004")), comments=[comment(100, bot, T1, "Rule ran.")]))
    by_id = {e.id: e for e in p.events}
    label = kind if kind in ("app", "unknown") else "other"
    for hid, would in ((2, "revoke"), (3, "reject")):
        event = by_id[f"{BASE}/transition/{hid}"]
        assert (event.action, event.supersedes) == ("comment", None)
        assert event.note == f"automated actor (account type {label}); recorded as comment instead of {would}"
    assert by_id[f"{BASE}/comment/100"].note == "Jira comment"
    assert p.automated == {aid("automation")}
    fact = p.spans[by_id[f"{BASE}/transition/2"].evidence_ids[0]].quote
    assert f"by {aid('automation')} ({label})" in fact


def test_an_automated_decision_is_no_revoke_target():
    p = plan(single(history(1, "automation", T1, status("10000", "10003")),
                    history(2, "zhou", T2, status("10003", "10005"))))
    assert rows(p)[1:] == [(f"{BASE}/transition/1", "comment", aid("automation"), None),
                           (f"{BASE}/transition/2", "comment", aid("zhou"), None)]


def test_deleted_and_anonymous_authors():
    former = user("former", active=False, displayName="Former user")
    p = plan(capture(page(
        issue(1, histories=[history(1, former, T1, status("10000", "10003")),
                            history(2, None, T2, status("10003", "10004")),
                            history(3, user("unknown"), T3, status("10004", "10002")),
                            history(4, user("not an id"), T4, status("10002", "10006"))],
              comments=[comment(100, former, T1, "Fine by me.")]),
        issue(2, creator=None, comments=[comment(200, "lin", T1)]))))
    assert rows(p) == [
        (f"{BASE}/created", "request", aid("lin"), None),
        (f"{BASE}/comment/100", "comment", aid("former"), None),
        (f"{BASE}/transition/1", "approve", aid("former"), None),
        ("jira:ACME-2/comment/200", "comment", aid("lin"), None),
    ]
    assert p.skipped == [f"{BASE}/transition/{n} (no actor)" for n in (2, 3, 4)] + ["jira:ACME-2/created (no actor)"]
    # A deleted author is a fact about the data, not a gap in the capture.
    assert p.complete


def test_statuses_map_by_id_so_renames_do_not_matter():
    renamed = single(history(1, "zhou", T1, status("10000", "10004", toString="Rejected")),
                     history(2, "zhou", T2, status("10004", "10003", fromString="Won't Do")),
                     history(3, "zhou", T3, status("10003", "10004", toString="Won't Do")))
    assert [e.action for e in plan(renamed).events] == ["request", "reject", "approve", "reject"]
    for bad in ({"statuses": {"Approved": "approve"}}, {"statuses": {"10003": "raise_risk"}},
                {"statuses": {"010003": "approve"}}, {"labels": {}}):
        with pytest.raises(ValidationError):
            JiraConfig.model_validate(bad)
    assert [e.action for e in plan(renamed, config=JiraConfig()).events] == ["request"] + ["comment"] * 3


def test_only_the_system_status_field_is_a_transition():
    forged = [{**status("10000", "10003"), "fieldtype": "custom", "fieldId": "customfield_10050"},
              {**status("10000", "10003"), "fieldId": "customfield_10051"},
              {**status("10000", "10003"), "field": "Status"}]
    p = plan(single(history(1, "zhou", T1, *forged)))
    assert rows(p) == [(f"{BASE}/created", "request", aid("lin"), None)] and p.unmapped == 3


# --- pages, duplicates and completeness ------------------------------------------------------------

def test_pages_merge_in_any_order_and_identical_duplicates_are_ignored():
    original = recording("capture-1")
    p = plan(original)
    assert (p.pages, p.issues, p.total, p.duplicates, p.complete) == (3, 6, 6, 3, True)
    shuffled = copy.deepcopy(original)
    shuffled["pages"].reverse()
    q = plan(shuffled)
    assert rows(q) == rows(p) and [c.data for c in q.captures] == [c.data for c in p.captures]
    single_copy = copy.deepcopy(original)
    del single_copy["pages"][2]
    assert rows(plan(single_copy)) == rows(p) and plan(single_copy).duplicates == 0


def test_a_duplicate_with_different_content_fails_closed():
    changed = recording("capture-1")
    changed["pages"][2] = copy.deepcopy(changed["pages"][2])
    changed["pages"][2]["issues"][1]["fields"]["summary"] = "Edited between two page requests"
    with pytest.raises(JiraCaptureError, match="ACME-2 appears twice with different content"):
        plan(changed)


@pytest.mark.parametrize("pages, reasons", [
    ([page(issue(1), issue(2), total=3)], ["pages cover 2 of 3 results"]),
    ([page(issue(1), total=2), page(issue(2), start=1, total=3)],
     ["pages disagree on the result total", "pages cover 2 of 3 results"]),
    ([page(issue(1), issue(2), total=1)], ["pages extend past the result total"]),
    ([page(issue(1), issue(2)), page(issue(3), start=1, total=2)], ["the result order changed between pages"]),
    ([page(issue(1), issue(2)), page(issue(3), start=2, total=2)], ["pages extend past the result total"]),
])
def test_incomplete_pagination_is_reported_and_present_issues_still_planned(pages, reasons):
    p = plan(capture(*pages))
    assert p.incomplete == reasons and not p.complete
    assert {e.subject for e in p.events} == {f"ACME-{i['key'][-1]}" for pg in pages for i in pg["issues"]}


def truncated_changelog(i):
    i["changelog"]["total"] = 101


def offset_changelog(i):
    i["changelog"]["startAt"] = 100


def no_changelog(i):
    del i["changelog"]


def no_comment_field(i):
    del i["fields"]["comment"]


def truncated_comments(i):
    i["fields"]["comment"]["total"] = 5


def wrong_current_status(i):
    i["fields"]["status"]["id"] = "10006"


@pytest.mark.parametrize("damage, reason", [
    (no_changelog, "no changelog; fetch with expand=changelog"),
    (truncated_changelog, "changelog truncated: 2 of 101"),
    (offset_changelog, "changelog truncated: 2 of 2"),
    (no_comment_field, "no comment field; fetch with the documented field list"),
    (truncated_comments, "comments truncated: 1 of 5"),
    (wrong_current_status, "changelog does not end at the current status"),
])
def test_an_issue_without_its_whole_history_is_skipped_whole(damage, reason):
    broken = issue(2, histories=[history(1, "zhou", T1, status("10000", "10003")),
                                 history(2, "zhou", T2, status("10003", "10005"))],
                   comments=[comment(200, "lin", T1)])
    damage(broken)
    p = plan(capture(page(issue(1), broken)))
    assert {e.subject for e in p.events} == {"ACME-1"} and not any("ACME-2" in s for s in p.spans)
    assert p.incomplete == [f"jira:ACME-2 ({reason})"] and not p.complete


def test_a_gap_in_the_status_history_skips_the_issue():
    gap = issue(1, histories=[history(1, "zhou", T1, status("10000", "10002")),
                              history(2, "zhou", T2, status("10003", "10004"))])
    p = plan(capture(page(gap)))
    assert p.events == [] and p.incomplete == ["jira:ACME-1 (status history has a gap before history 2)"]


def test_partial_fixture():
    p = plan(recording("capture-partial"))
    assert p.incomplete == ["pages cover 3 of 4 results", "jira:ACME-8 (no changelog; fetch with expand=changelog)",
                            "jira:ACME-9 (changelog truncated: 1 of 120)"]
    assert {e.subject for e in p.events} == {"ACME-10"}


# --- evidence ------------------------------------------------------------------------------------------

def test_fact_quote_names_event_actor_and_object():
    for name in ("capture-1", "capture-2"):
        p = plan(recording(name))
        for event in p.events:
            fact = p.spans[event.evidence_ids[0]]
            assert fact.start == 0 and fact.quote.isascii() and f" by {event.actor} (" in fact.quote
            assert event.id.rsplit("/", 1)[-1] in fact.quote or event.id.endswith("/created")
            assert event.subject in fact.quote


def test_untrusted_text_quotes(tmp_path):
    bodies = {101: "line one\r\nline two", 102: "\x1b[31mred", 103: "safe\u202etxt.exe", 104: "a\u2028b",
              105: "<!-- template -->\n\u4f60\u597d \U0001f642", 106: "bad \ud800 half", 107: "", 108: "  \n\t\n"}
    p = plan(single(comments=[comment(cid, "lin", T1, text) for cid, text in bodies.items()]))
    root = tmp_path / "data"
    root.mkdir()
    materialize(root, p)
    for c in p.captures:
        for entry in c.entries:
            verify_entry(entry, root)
    quotes = {e.id.rsplit("/", 1)[-1]: [p.spans[i].quote for i in e.evidence_ids[1:]] for e in p.events}
    assert quotes["101"] == ["line one"]
    assert quotes["102"] == quotes["103"] == quotes["104"] == quotes["107"] == quotes["108"] == []
    assert quotes["105"] == ["\u4f60\u597d \U0001f642"]
    assert quotes["106"] == ["bad \ufffd half"]
    lossy = next(c for c in p.captures if "comment-106-" in c.path).data.decode("utf-8")
    assert '"lossy":true' in lossy.splitlines()[1]
    for spans in quotes.values():
        assert not any("\n" in q or "\r" in q for q in spans)


def test_spans_cannot_be_redirected_by_body():
    p = plan(recording("capture-1"))
    event = next(e for e in p.events if e.id == "jira:ACME-3/comment/10302")
    fact, text = (p.spans[i] for i in event.evidence_ids)
    data = next(c.data for c in p.captures if c.path == fact.path).decode("utf-8")
    header = data.index(SEP + "\n") + len(SEP) + 1
    assert (fact.start, fact.end) == (0, len(data.splitlines()[0])) and "comment 10302 on ACME-3" in fact.quote
    assert text.start == header and text.quote.startswith("Jira transition 20204 on ACME-2")
    assert data.count(SEP) == 2  # the body's copy of the separator is just text


def test_convert_is_deterministic():
    assert plan(recording("capture-1")) == plan(recording("capture-1"))


def test_replay_outcomes(tmp_path):
    root = tmp_path / "data"
    root.mkdir()
    (root / "policy.json").write_bytes((FIXTURES / "policy.json").read_bytes())
    first = plan(recording("capture-1"))
    materialize(root, first)
    states = replay(Dataset.load(root)).states

    def summary(subject):
        s = states[subject]
        return (s.current_status, s.reconsideration_requested, s.pending_approval,
                s.last_authorized_decision.event_id if s.last_authorized_decision else None,
                [c.event_id for c in s.pending_changes])

    assert summary("ACME-1") == ("rejected", True, ["product_manager"], "jira:ACME-1/transition/20102",
                                 ["jira:ACME-1/transition/20103"])
    assert summary("ACME-2") == ("approved", False, [], "jira:ACME-2/transition/20204", [])
    assert summary("ACME-3") == ("proposed", False, [], None, ["jira:ACME-3/transition/20302"])
    assert summary("ACME-4") == ("approved", False, [], "jira:ACME-4/transition/20401", [])
    assert summary("ACME-5") == ("rejected", False, [], "jira:ACME-5/transition/20501", [])
    assert summary("ACME-6") == ("proposed", False, ["product_manager"], None, ["jira:ACME-6/transition/20601"])
    second = plan(recording("capture-2"), existing={e.id: e for e in first.events}, evidence_ids=set(first.spans))
    materialize(root, second)
    states = replay(Dataset.load(root)).states
    assert summary("ACME-1") == ("approved", False, [], "jira:ACME-1/transition/20105", [])
    assert [c.kind for c in states["ACME-1"].conflicts] == ["decision_reversed"]
    assert summary("ACME-4") == ("proposed", False, ["product_manager"], "jira:ACME-4/transition/20402", [])
    assert summary("ACME-7") == ("rejected", False, [], "jira:ACME-7/transition/20701", [])


# --- parsing -------------------------------------------------------------------------------------------

def test_timestamps_are_normalized_to_utc_milliseconds():
    assert _ts("2026-10-05T17:00:00.123+0800", "t") == _ts("2026-10-05T09:00:00.123Z", "t") \
        == _ts("2026-10-05T09:00:00.123456+00:00", "t")
    assert _ts("2026-10-05T09:00:00+0000", "t").microsecond == 0
    for bad in ("2026-10-05", "2026-10-05T09:00:00", "2026-10-05T09:00:00.000+2500", "9999-12-31T23:00:00-0100",
                "\uff12\uff10\uff12\uff16-10-05T09:00:00Z", 1791691200, None):
        with pytest.raises(JiraCaptureError, match="not a timestamp"):
            _ts(bad, "t")


def test_adf_text_keeps_text_mentions_and_links_in_order():
    value = {"type": "doc", "content": [
        {"type": "heading", "attrs": {"level": 2}, "content": [{"type": "text", "text": "Plan"}]},
        {"type": "paragraph", "content": [
            {"type": "text", "text": "Ask "}, {"type": "mention", "attrs": {"id": "x", "text": "@Lin"}},
            {"type": "text", "text": " "}, {"type": "emoji", "attrs": {"shortName": ":ok:"}},
            {"type": "hardBreak"}, {"type": "inlineCard", "attrs": {"url": "https://example.com/a"}}]},
        {"type": "codeBlock", "content": [{"type": "text", "text": "x = 1\ny = 2"}]},
        {"type": "mediaSingle", "content": [{"type": "media", "attrs": {"id": "m"}}]},
        {"type": "unknownNode", "content": [{"type": "text", "text": "kept"}, "not a node", 7]}]}
    assert adf_text(value, "x") == "Plan\nAsk @Lin :ok:\nhttps://example.com/a\nx = 1\ny = 2\nkept"
    assert adf_text("plain *wiki* text", "x") == "plain *wiki* text" and adf_text(None, "x") == ""
    deep = {"type": "doc", "content": []}
    node = deep
    for _ in range(500):
        child = {"type": "blockquote", "content": []}
        node["content"].append(child)
        node = child
    node["content"].append({"type": "text", "text": "too deep"})
    assert adf_text(deep, "x") == ""
    with pytest.raises(JiraCaptureError):
        adf_text(["not", "a", "document"], "x")


def mutate(path, value):
    def apply(c):
        target = c
        for step in path[:-1]:
            target = target[step]
        if value is DELETE:
            del target[path[-1]]
        else:
            target[path[-1]] = value
    return apply


DELETE = object()
FIRST = ("pages", 0, "issues", 0)


@pytest.mark.parametrize("change", [
    mutate(("jira_capture",), 2), mutate(("jira_capture",), "1"), mutate(("site",), "https://acme.example.com"),
    mutate(("site",), "http://acme.atlassian.net"), mutate(("site",), "https://acme.atlassian.net.evil.com"),
    mutate(("captured_at",), "yesterday"), mutate(("pages",), []), mutate(("pages",), {"issues": []}),
    mutate(("pages", 0, "startAt"), "0"), mutate(("pages", 0, "total"), -1), mutate(("pages", 0, "issues"), {}),
    mutate((*FIRST, "key"), "acme-1"), mutate((*FIRST, "key"), "ACME-1\n"), mutate((*FIRST, "id"), 10001),
    mutate((*FIRST, "self"), f"{SITE}/rest/api/2/issue/10001"),
    mutate((*FIRST, "self"), "https://other.atlassian.net/rest/api/3/issue/10001"),
    mutate((*FIRST, "fields"), None), mutate((*FIRST, "fields", "created"), "2026-10-05 09:00"),
    mutate((*FIRST, "fields", "status"), {"name": "To Do"}), mutate((*FIRST, "fields", "summary"), {"x": 1}),
    mutate((*FIRST, "fields", "description"), ["x"]),
    mutate((*FIRST, "changelog", "histories", 0, "id"), "20,1"),
    mutate((*FIRST, "changelog", "histories", 0, "items"), None),
    mutate((*FIRST, "changelog", "histories", 0, "items", 0, "to"), 10003),
    mutate((*FIRST, "changelog", "histories", 0, "author"), "zhou"),
    mutate((*FIRST, "changelog", "total"), 0),
    mutate((*FIRST, "fields", "comment", "comments", 0, "created"), None),
])
def test_malformed_captures_fail_closed(change):
    c = single(history(1, "zhou", T1, status("10000", "10003")), comments=[comment(100, "lin", T1)])
    change(c)
    with pytest.raises(JiraCaptureError) as caught:
        plan(c)
    assert str(caught.value).isascii() and "\n" not in str(caught.value)


def test_structural_duplicates_fail_closed():
    twice = single(history(1, "zhou", T1, status("10000", "10002")), history(1, "zhou", T2, status("10002", "10003")))
    with pytest.raises(JiraCaptureError, match="history 1 appears twice"):
        plan(twice)
    with pytest.raises(JiraCaptureError, match="comment 5 appears twice"):
        plan(single(comments=[comment(5, "lin", T1), comment(5, "lin", T2)]))
    with pytest.raises(JiraCaptureError, match="changes the status twice"):
        plan(single(history(1, "zhou", T1, status("10000", "10002"), status("10002", "10003"))))
    other = issue(2)
    other["key"] = "ACME-1"
    with pytest.raises(JiraCaptureError, match="ACME-1 names two issue ids"):
        plan(capture(page(issue(1), other)))


def test_an_issue_known_under_another_key_is_skipped():
    first = plan(single())
    moved = issue(1, project="OPS", histories=[history(1, "zhou", T1, status("10000", "10003"))])
    p = plan(capture(page(moved)), existing={e.id: e for e in first.events}, evidence_ids=set(first.spans))
    assert p.events == [] and p.skipped == ["jira:OPS-1 (issue 10001 is ACME-1 in this dataset; "
                                            "a moved issue is not followed)"]
    reused = issue(2)
    reused["key"] = "ACME-1"
    q = plan(capture(page(reused)), existing={e.id: e for e in first.events}, evidence_ids=set(first.spans))
    assert q.events == [] and q.skipped == ["jira:ACME-1 (ACME-1 is issue 10001 in this dataset)"]
