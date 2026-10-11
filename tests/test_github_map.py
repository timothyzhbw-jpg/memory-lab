"""The mapping contract and its authority boundaries, entirely offline."""
import copy
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from memcore import append_jsonl
from memcore.evidence import verify_entry
from github_fakes import SHA, build_capture, comment, ev, issue, item, review, user
from orgmemory.github_map import GithubConfig, actor, convert, render, text_span
from orgmemory.models import Policy
from orgmemory.replay import Dataset, replay

FIXTURES = Path(__file__).parent / "fixtures" / "github"
T0, T1, T2, T3 = [f"2026-10-0{n}T09:00:00Z" for n in range(5, 9)]
BASE = "gh:acme/web#1"
PM = "gh:zhou-pm:5003"
CONFIG = GithubConfig(labels={"approved": "approve", "risk": "raise_risk"})


def recording(n):
    return json.loads((FIXTURES / f"capture-{n}.json").read_bytes())


def plan(capture, config=CONFIG, existing=None, evidence_ids=None):
    return convert(capture, existing or {}, evidence_ids or set(), config)


def single(*timeline, pr=False, **fields):
    return build_capture(item(issue(1, "buyer-acme", T0, pr=pr, **{"title":"A request", **fields}), *timeline))


def materialize(root, p):
    for c in p.captures:
        path = root / c.path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(c.data)
        for entry in c.entries:
            append_jsonl(root / "evidence.jsonl", entry.model_dump(mode="json"))
    for event in p.events:
        append_jsonl(root / "events.jsonl", event.model_dump(mode="json", exclude_none=True))


def outcomes(root, *captures):
    existing, evidence_ids = {}, set()
    for capture in captures:
        p = plan(capture, existing=existing, evidence_ids=evidence_ids)
        materialize(root, p)
        existing.update({e.id: e for e in p.events})
        evidence_ids.update(p.spans)
    return replay(Dataset.load(root))


def close(reason="not_planned", **kw):
    return ev("closed", 10, "zhou-pm", T1, state_reason=reason, **kw)


def dismissed(commit=None, state="approved"):
    dr = {"review_id": 20, "state": state, "dismissal_message": "Withdraw this review"}
    if commit:
        dr["dismissal_commit_id"] = commit
    return ev("review_dismissed", 11, "zhou-pm", T2, dismissed_review=dr)


# Each expected row lists (suffix, action, note, supersedes suffix, dependency).
# All timeline signals here are by zhou-pm, so the expected actor is explicit too.
CASES = [
    ("M1", single(), [("/opened", "request", "GitHub issue opened", None, None)]),
    ("M2", single(pr=True), [("/opened", "request", "GitHub pull request opened", None, None)]),
    ("M3", single(comment(30, "zhou-pm", T1, "LGTM", updated_at=T2)),
     [("/comment/30", "comment", "GitHub comment; edited before capture; editor not exposed by REST", None, None)]),
    ("M4", single(review(20, "zhou-pm", "approved", T1, "yes"), pr=True),
     [("/review/20", "approve", f"GitHub review approved on commit {SHA[:12]}", None, None)]),
    ("M5", single(review(20, "zhou-pm", "changes_requested", T1), pr=True),
     [("/review/20", "reject", f"GitHub review changes_requested on commit {SHA[:12]}", None, None)]),
    ("M6", single(review(20, "zhou-pm", "commented", T1, "words"), review(21, "zhou-pm", "commented", T2), pr=True),
     [("/review/20", "comment", f"GitHub review commented on commit {SHA[:12]}", None, None)]),
    ("M7", single(review(20, "zhou-pm", "dismissed", T1), dismissed(), pr=True),
     [("/review/20", "approve", f"GitHub review approved on commit {SHA[:12]}; dismissed later", None, None),
      ("/event/11", "revoke", "GitHub review 20 dismissed (was approved)", "/review/20", None)]),
    ("M8", single(review(20, "zhou-pm", "approved", None), review(21, "zhou-pm", "pending", T1), pr=True), []),
    ("M9", single(review(20, "zhou-pm", "bogus\nspoof", T1, "untrusted"), pr=True),
     [("/review/20", "comment", "GitHub review state unknown", None, None)]),
    ("M10", single(review(20, "zhou-pm", "approved", T1), dismissed(), pr=True),
     [("/event/11", "revoke", "GitHub review 20 dismissed (was approved)", "/review/20", None)]),
    ("M11", single(review(20, "zhou-pm", "approved", T1), dismissed(SHA), pr=True),
     [("/event/11", "comment", f"GitHub review 20 dismissed by commit {SHA[:12]} (stale review)", None, None)]),
    ("M12", single(review(20, "zhou-pm", "commented", T1, "words"), dismissed(state="commented"), pr=True),
     [("/event/11", "comment", "GitHub review 20 dismissed (was commented)", None, None)]),
    ("M13", single(review(20, "zhou-pm", "changes_requested", T1),
                   ev("review_requested", 11, "zhou-pm", T2, requested_reviewer=user("zhou-pm")), pr=True),
     [("/event/11", "request", f"GitHub review re-requested from {PM} after changes requested", None, None)]),
    ("M14", single(ev("review_requested", 11, "zhou-pm", T1, requested_team={"slug": "team"}), pr=True), []),
    ("M15", single(close()), [("/event/10", "reject", "GitHub issue closed as not_planned", None, None)]),
    ("M16", single(close("completed", commit_id=SHA)),
     [("/event/10", "comment", f"GitHub issue closed as completed by commit {SHA[:12]}", None, None)]),
    ("M17", single(close(), pr=True),
     [("/event/10", "reject", "GitHub pull request closed without merging", None, None)]),
    ("M18", single(ev("merged", 11, "zhou-pm", T1, commit_id=SHA), close(), pr=True), []),
    ("M19", single(ev("merged", 11, "zhou-pm", T1, commit_id=SHA), pr=True),
     [("/event/11", "approve", f"GitHub pull request merged at {SHA[:12]}", None, None)]),
    ("M20", single(close(), ev("reopened", 11, "zhou-pm", T2)),
     [("/event/11", "revoke", f"GitHub issue reopened; withdraws {BASE}/event/10", "/event/10", None),
      ("/event/11/request", "request", "GitHub issue reopened", None, None)]),
    ("M21", single(ev("reopened", 11, "zhou-pm", T1)),
     [("/event/11", "request", "GitHub issue reopened", None, None)]),
    ("M22", single(ev("labeled", 11, "zhou-pm", T1, label={"name": "APPROVED"})),
     [("/event/11", "approve", 'GitHub label "approved" added', None, None)]),
    ("M23", single(ev("labeled", 10, "zhou-pm", T1, label={"name": "approved"}),
                   ev("unlabeled", 11, "zhou-pm", T2, label={"name": "approved"})),
     [("/event/11", "revoke", 'GitHub label "approved" removed', "/event/10", None)]),
    ("M24", single(ev("labeled", 11, "zhou-pm", T1, label={"name": "noise"})), []),
    ("M25", single(ev("blocked_by_added", 11, "zhou-pm", T1,
                       blocked_by={"number": 3, "repository": {"full_name": "Acme/Billing"}})),
     [("/event/11", "dependency", "GitHub blocked-by link added", None, "acme/billing#3")]),
    ("M26", single(ev("blocked_by_removed", 11, "zhou-pm", T1,
                       blocked_by={"number": 3, "repository": {"full_name": "acme/billing"}})),
     [("/event/11", "comment", "GitHub blocked-by link removed", None, None)]),
    ("M27", single(ev("blocking_added", 11, "zhou-pm", T1),
                   ev("alien", 12, "zhou-pm", T2, blocked_by={"number": 3})), []),
]


@pytest.mark.parametrize("row,capture,expected", CASES, ids=[c[0] for c in CASES])
def test_mapping_table(row, capture, expected):
    p = plan(capture)
    if row in ("M1", "M2"):
        actual = p.events
    elif row in ("M10", "M11", "M12", "M13", "M23"):
        actual = [e for e in p.events if e.id == BASE + "/event/11"]
    elif row == "M20":
        actual = [e for e in p.events if e.id.startswith(BASE + "/event/11")]
    elif row == "M18":
        actual = [e for e in p.events if e.id == BASE + "/event/10"]
    else:
        actual = [e for e in p.events if not e.id.endswith("/opened")]
    assert [(e.id, e.action, e.actor, e.subject, e.timestamp, e.supersedes, e.dependency, e.note) for e in actual] == [
        (BASE + suffix, action_, "gh:buyer-acme:5001" if row in ("M1", "M2") else PM,
         "acme/web#1", datetime.fromisoformat((T0 if suffix == "/opened" else
          T2 if row in ("M10", "M11", "M12", "M13", "M20", "M23") or row == "M7" and suffix == "/event/11"
          else T1).replace("Z", "+00:00")), BASE + target if target else None, dependency, note)
        for suffix, action_, note, target, dependency in expected]
    for e in actual:
        assert e.evidence_ids[0].endswith("/fact")
    if row in ("M9", "M11", "M12", "M13", "M15", "M16", "M17", "M19", "M20", "M21", "M22", "M23", "M25", "M26"):
        assert all(len(e.evidence_ids) == 1 for e in actual)
    if row == "M7":
        assert actual[0].evidence_ids[-1] == BASE + "/event/11/fact"


def state_tuple(state):
    return (state.current_status, state.reconsideration_requested, state.pending_approval, state.dependencies,
            state.last_authorized_decision.event_id if state.last_authorized_decision else None,
            [p.event_id for p in state.pending_changes], [c.kind for c in state.conflicts])


def test_replay_outcomes_capture_1(github_dataset):
    result = outcomes(github_dataset, recording(1))
    expected = {
        1: ("rejected", False, [], ["acme/billing#3"], "event/105", ["event/107"], []),
        2: ("approved", False, [], [], "event/207", [], ["decision_reversed"]),
        3: ("proposed", False, [], [], None, [], []),
        4: ("proposed", False, ["product_manager"], [], None, [], []),
        5: ("approved", False, [], [], "review/501", [], []),
        6: ("rejected", False, [], [], "review/601", ["event/602"], []),
        7: ("approved", False, [], [], "review/701", [], []),
        8: ("proposed", False, ["product_manager"], [], None, [], []),
        9: ("proposed", False, ["product_manager"], [], None, [], []),
        10: ("proposed", False, ["product_manager"], [], None, [], []),
        11: ("proposed", False, ["product_manager"], [], None, ["review/1101"], []),
        12: ("proposed", False, [], [], None, [], []),
        13: ("rejected", False, [], [], "event/1301", [], []),
    }
    assert set(result.states) == {f"acme/web#{n}" for n in expected}
    for n, values in expected.items():
        status, reconsider, pending, deps, last, attempts, conflicts = values
        base = f"gh:acme/web#{n}/"
        assert state_tuple(result.states[f"acme/web#{n}"]) == (
            status, reconsider, pending, deps, base + last if last else None, [base + a for a in attempts], conflicts)


def test_replay_outcomes_capture_2(github_dataset):
    result = outcomes(github_dataset, recording(1), recording(2))
    assert state_tuple(result.states['acme/web#1']) == (
        'rejected', True, ['product_manager'], ['acme/billing#3'], 'gh:acme/web#1/event/105',
        ['gh:acme/web#1/event/107', 'gh:acme/web#1/event/108'], [])
    for n, target in ((5, 502), (13, 1302)):
        assert state_tuple(result.states[f'acme/web#{n}']) == (
            'proposed', False, ['product_manager'], [], f'gh:acme/web#{n}/event/{target}', [], [])
    assert state_tuple(result.states['acme/web#3']) == ('proposed', False, [], [], None, [], [])


@pytest.mark.parametrize("state,expected", [("approved", "approve"), ("APPROVED", "approve"),
    ("changes_requested", "reject"), ("CHANGES_REQUESTED", "reject"), ("alien", "comment")])
def test_review_states_case_insensitive(state, expected):
    assert plan(single(review(20, 'zhou-pm', state, T1), pr=True)).events[-1].action == expected


def test_dismissed_review_recovered_regardless_of_timing(github_dataset):
    capture = recording(2)
    p = plan(capture)
    e = next(e for e in p.events if e.id == 'gh:acme/web#5/review/501')
    assert e.action == 'approve'
    assert e.evidence_ids[0].startswith('gh:acme/web#5/review/501@')
    assert e.evidence_ids[-1] == 'gh:acme/web#5/event/502/fact'
    assert next(e for e in p.events if e.id == 'gh:acme/web#5/event/502').action == 'revoke'
    assert outcomes(github_dataset, capture).states['acme/web#5'].current_status == 'proposed'
    existing, evidence_ids = {}, set()
    events, spans = [], {}
    for c in (recording(1), capture):
        batch = plan(c, existing=existing, evidence_ids=evidence_ids)
        events.extend(batch.events)
        spans.update(batch.spans)
        existing.update({e.id: e for e in batch.events})
        evidence_ids.update(batch.spans)
    policy = Policy.model_validate_json((FIXTURES/'policy.json').read_bytes())
    one = replay(Dataset(p.events, policy, p.spans)).states['acme/web#5']
    two = replay(Dataset(events, policy, spans)).states['acme/web#5']
    assert state_tuple(one) == state_tuple(two)


def test_stale_review_auto_dismissal_is_comment(github_dataset):
    p = plan(recording(1))
    e = next(e for e in p.events if e.id == 'gh:acme/web#7/event/702')
    assert (e.action, e.supersedes, len(e.evidence_ids)) == ('comment', None, 1)
    assert outcomes(github_dataset, recording(1)).states['acme/web#7'].current_status == 'approved'


def test_reopen_revokes_and_requests_only_after_decision_close():
    p = plan(single(close(), ev('reopened', 11, 'zhou-pm', T2)))
    assert [(e.id, e.action) for e in p.events[-2:]] == [(BASE+'/event/11', 'revoke'), (BASE+'/event/11/request', 'request')]
    assert p.events[-2].supersedes == BASE+'/event/10'
    p = plan(single(close('completed'), ev('reopened', 11, 'zhou-pm', T2)))
    assert [e.action for e in p.events] == ['request', 'comment', 'request']
    p = plan(single(close(), ev('reopened', 11, 'github-actions[bot]', T2)))
    assert [(e.id, e.action, e.supersedes) for e in p.events[-1:]] == [(BASE+'/event/11', 'comment', None)]


@pytest.mark.parametrize('history,expected', [([], False),
    ([review(20,'zhou-pm','changes_requested',T1)], True),
    ([review(20,'zhou-pm','changes_requested',T1), dismissed(state='changes_requested')], False),
    ([review(20,'zhou-pm','changes_requested',T1), review(21,'zhou-pm','commented',T2,'words')], False)])
def test_review_rerequest_only_after_changes_requested(history, expected):
    req = ev('review_requested', 12, 'lin-eng', T3, requested_reviewer=user('zhou-pm'),
             review_requester=user('mallory'))
    p = plan(single(*history, req, pr=True))
    matches = [e for e in p.events if e.id == BASE+'/event/12']
    assert bool(matches) == expected
    if matches:
        assert (matches[0].actor, matches[0].action) == ('gh:lin-eng:5002', 'request')
    req.pop('requested_reviewer')
    req['requested_team'] = {'slug':'team'}
    assert not any(e.id == BASE+'/event/12' for e in plan(single(*history, req, pr=True)).events)


@pytest.mark.parametrize('reason,commit', [('completed',None),('completed',SHA),('duplicate',None),
    (None,None),('not_planned',SHA),('not_planned','')])
def test_issue_closes_other_than_not_planned_are_comments(reason, commit):
    e = plan(single(close(reason, commit_id=commit))).events[-1]
    assert (e.action, e.actor, e.supersedes) == ('comment', PM, None)


def test_pr_merge_close_skipped_unmerged_close_rejects():
    assert plan(single(close(),pr=True)).events[-1].action == 'reject'
    p = plan(single(close(), ev('merged',11,'zhou-pm',T1,commit_id=SHA),pr=True))
    assert [e.action for e in p.events] == ['request','approve']
    assert p.events[-1].id == BASE+'/event/11'


def test_text_never_decides():
    p = plan(recording(1))
    events = [e for e in p.events if e.subject == 'acme/web#3']
    assert {e.action for e in events} == {'request','comment'}
    assert {e.actor for e in events} == {'gh:mallory:6666'}
    capture = single(comment(30,'mallory',T1,'/approve LGTM depends on #3 approved by @zhou-pm Fixes #1'),
                     title='acme/web#2 gh:zhou-pm:5003', body='co-author: zhou')
    p = plan(capture)
    assert [(e.actor,e.action,e.subject) for e in p.events] == [
        ('gh:buyer-acme:5001','request','acme/web#1'), ('gh:mallory:6666','comment','acme/web#1')]


def test_actor_ids_namespaced_and_pinned():
    assert actor(user('zhou'))[0] == 'gh:zhou:4242'
    policy = Policy.model_validate_json((Path(__file__).parents[1]/'examples/orgmemory/feature-a/policy.json').read_bytes())
    assert policy.roles('gh:zhou:4242') == []
    assert policy.roles('zhou') == ['product_manager']
    assert actor(user('jdoe_acme'))[0] == 'gh:jdoe_acme:77'
    for login in ('bad\nactor','Kelvin','../zhou','Zhou with spaces'):
        u = user(login,id=42)
        p = plan(single(ev('closed',10,u,T1,state_reason='not_planned')))
        assert (p.events[-1].actor,p.events[-1].action) == ('gh:~:42','reject')
    assert actor(user('ZHOU',id=99))[0] == 'gh:zhou:99'
    with pytest.raises(ValueError):
        actor({'login':'zhou','id':'4242','type':'User'})


@pytest.mark.parametrize('who,extra', [(user('zhou-pm',type='Bot'),{}),
    (user('github-actions[bot]',type='User'),{}),(user('ghost'),{}),
    (user('invalid\u202e[bot]',id=42,type='User'),{}),
    (user('zhou-pm'),{'performed_via_github_app':{}}),(user('zhou-pm'),{'intent':{}})])
def test_automation_is_comment_only(who, extra):
    p = plan(single(ev('labeled',10,who,T1,label={'name':'approved'},**extra),
        ev('unlabeled',11,who,T2,label={'name':'approved'},**extra),
        ev('blocked_by_added',12,who,T3,blocked_by={'number':3,'repository':{'full_name':'acme/billing'}},**extra)))
    for e in p.events[1:]:
        assert (e.action,e.supersedes,e.dependency) == ('comment',None,None)
        assert 'automated actor' in e.note
    assert 'instead of approve' in p.events[1].note
    assert 'instead of dependency' in p.events[-1].note


def test_null_actor_skipped_and_reported():
    p = plan(recording(1))
    assert 'gh:acme/web#9/event/991 (no actor)' in p.skipped
    assert not any(e.id == 'gh:acme/web#9/event/991' for e in p.events)
    # A decoy actor on a comment cannot replace its null user.
    c = comment(30,None,T1,'/approve', actor=user('zhou-pm'))
    assert plan(single(c)).skipped == [BASE+'/comment/30 (no actor)']


def test_decision_labels_opt_in():
    c = single(ev('labeled',10,'zhou-pm',T1,label={'name':'APPROVED'}),
               ev('unlabeled',11,'zhou-pm',T2,label={'name':'Approved'}),
               ev('labeled',12,'lin-eng',T3,label={'name':'risk'}))
    assert len(plan(c,GithubConfig()).events) == 1
    p = plan(c)
    assert [e.action for e in p.events] == ['request','approve','revoke','raise_risk']
    assert p.events[2].supersedes == BASE+'/event/10'
    assert p.events[3].note == 'GitHub label "risk" added'
    c['items'][0]['timeline'] += [ev('unlabeled',13,'zhou-pm',T3,label={'name':'risk'})]
    assert plan(c).events[-1].action == 'comment'
    with pytest.raises(ValueError):
        GithubConfig(labels={'Approved':'approve','approved':'reject'})
    with pytest.raises(ValueError):
        GithubConfig.model_validate({'permissions':{}})


def test_dependency_only_from_native_blocked_by():
    c = single(comment(30,'lin-eng',T1,'depends on #3'),
        ev('alien\nspoof',11,'zhou-pm',T1,blocked_by={'number':3}),
        ev('blocked_by_added',12,'lin-eng',T2,blocked_by={'number':3,'repository':{'full_name':'Acme/Billing'}}),
        ev('blocked_by_removed',13,'lin-eng',T3,blocked_by={'number':3,'repository':{'full_name':'acme/billing'}}))
    p = plan(c)
    assert [(e.action,e.dependency) for e in p.events] == [
        ('request',None),('comment',None),('dependency','acme/billing#3'),('comment',None)]
    assert p.skipped == [BASE+'/event/11 (unrecognized dependency event unknown)']
    assert p.unmapped == 1


def test_fact_quote_names_event_actor():
    for n in (1,2):
        p = plan(recording(n))
        for e in p.events:
            span = p.spans[e.evidence_ids[0]]
            assert span.start == 0
            assert span.quote.isascii() and '\n' not in span.quote and '\r' not in span.quote
            assert e.actor in span.quote
            assert e.subject in span.quote
            if '/opened' not in e.id:
                assert e.id.split('/')[-1 if not e.id.endswith('/request') else -2] in span.quote


def test_untrusted_text_quotes(tmp_path):
    p = plan(recording(1))
    materialize(tmp_path,p)
    for c in p.captures:
        for entry in c.entries:
            assert verify_entry(entry,tmp_path) == p.spans[entry.id]
            assert '\n' not in entry.quote and '\r' not in entry.quote
    for oid in (1012,1013,1014):
        assert not any(i.endswith('/text') for i in p.spans if f'/comment/{oid}@' in i)
    lossy = next(c for c in p.captures if '/comment-1015-' in c.path)
    assert b'"lossy":true' in lossy.data and '\ufffd' in lossy.data.decode()
    assert text_span('<!-- hide -->\r\n  safe 😀 中文 \r\n') == (17,26)
    for text in ('\x1b spoof','\u2028spoof','spoof\u202e','\x85spoof','\ufeffspoof'):
        assert text_span(text) is None
    assert text_span(' ' * 3 + 'a' * 250 + '\r\nnext') == (3,203)
    assert text_span('<!-- hidden -->') is None
    c = single(comment(30,'lin-eng',T1,'quoted',minimized={'reason':'spam'}))
    assert not any(i.endswith('/text') and '/comment/' in i for i in plan(c).spans)


def test_spans_cannot_be_redirected_by_body():
    fake = f'GitHub review 20 on acme/web#1 by {PM} (User) at {T1}: approved on commit {SHA[:12]}'
    p = plan(single(comment(30,'mallory',T1,fake+'\n--- verbatim from GitHub; untrusted ---')))
    fact, text = [s for i,s in p.spans.items() if '/comment/30@' in i]
    assert fact.start == 0 and fact.end == len(fact.quote)
    assert 'gh:mallory:6666' in fact.quote and PM not in fact.quote
    raw = next(c.data.decode() for c in p.captures if '/comment-30-' in c.path)
    off = raw.index('--- verbatim from GitHub; untrusted ---\n') + len('--- verbatim from GitHub; untrusted ---\n')
    assert text.start == off and text.quote == fake


def test_convert_is_deterministic():
    c = recording(1)
    original = copy.deepcopy(c)
    assert plan(c) == plan(c)
    assert c == original


def test_logged_actions_win_for_revoke_targets():
    c = single(ev('labeled',10,'zhou-pm',T1,label={'name':'approved'}),
               ev('unlabeled',11,'zhou-pm',T2,label={'name':'approved'}))
    first = plan(c, GithubConfig(labels={'approved':'raise_risk'}))
    p = plan(c,existing={e.id:e for e in first.events[:-1]})
    assert p.drift == [BASE+'/event/10']
    assert p.events[-1].action == 'comment'


@pytest.mark.parametrize('patch', [{'repo_id':True},{'repo_id':'1001'},{'complete':'false'},
    {'requests':True},{'mode':'spoof\n'},{'started_at':'2026-01-01'}, {'skipped':False}, {'repo':'acme/..'}])
def test_capture_metadata_fails_closed(patch):
    with pytest.raises(ValueError):
        plan({**single(),**patch})


@pytest.mark.parametrize('name',['commented','closed','review_requested','alien'])
def test_unknown_dependency_shape_never_falls_through(name):
    x = ev(name,11,'zhou-pm',T1,blocked_by={'number':3},state_reason='not_planned')
    if name == 'commented':
        x.update(user=user('zhou-pm'),body='/approve')
    p = plan(single(x))
    assert [e.action for e in p.events] == ['request']
    assert p.skipped == [f'{BASE}/event/11 (unrecognized dependency event {name})']


@pytest.mark.parametrize('value',['',False,0,'bad\ncommit'])
def test_non_null_auto_dismissal_marker_never_revokes(value):
    d = dismissed()
    d['dismissed_review']['dismissal_commit_id'] = value
    p = plan(single(review(20,'zhou-pm','approved',T1),d,pr=True))
    assert p.events[-1].action == 'comment' and p.events[-1].supersedes is None


def test_actor_fields_cannot_be_replaced_by_decoys():
    opening = issue(1,'mallory',T0,title='Approved by @zhou-pm',closed_by=user('zhou-pm'),merged_by=user('zhou-pm'))
    r = review(20,'mallory','approved',T1,actor=user('zhou-pm'),author_association='OWNER')
    c = comment(30,'mallory',T1,'/approve',actor=user('zhou-pm'))
    closing = ev('closed',10,'mallory',T2,state_reason='not_planned',closed_by=user('zhou-pm'),
                 merged_by=user('zhou-pm'),user=user('zhou-pm'),author_association='OWNER')
    p = plan(build_capture(item(opening,r,c,closing)))
    assert {e.actor for e in p.events} == {'gh:mallory:6666'}
    assert [e.action for e in p.events] == ['request','approve','comment','reject']
    for e in p.events:
        assert e.actor in p.spans[e.evidence_ids[0]].quote


@pytest.mark.parametrize('row,n,subject,suffix,expected', [
    ('M1',1,1,'opened',('request','gh:buyer-acme:5001','2026-10-05T09:00:00Z',None,None,'GitHub issue opened')),
    ('M2',1,2,'opened',('request','gh:lin-eng:5002','2026-10-05T10:00:00Z',None,None,'GitHub pull request opened')),
    ('M3',2,3,'comment/301',('comment','gh:mallory:6666','2026-10-06T11:00:00Z',None,None,
        'GitHub comment; edited before capture; editor not exposed by REST')),
    ('M4',1,2,'review/206',('approve',PM,'2026-10-07T09:00:00Z',None,None,
        'GitHub review approved on commit 54bb654c9e60')),
    ('M5',1,2,'review/203',('reject',PM,'2026-10-06T09:00:00Z',None,None,
        'GitHub review changes_requested on commit 0c5a1f3e9b7d')),
    ('M6',1,11,'review/1102',('comment','gh:wu-sales:5004','2026-10-06T13:30:00Z',None,None,
        'GitHub review commented on commit 11aa11aa11aa')),
    ('M7',2,5,'review/501',('approve',PM,'2026-10-06T14:00:00Z',None,None,
        'GitHub review approved on commit a1b2c3d4e5f6; dismissed later')),
    ('M8',1,11,'review/1104',None),
    ('M10',2,5,'event/502',('revoke',PM,'2026-10-10T10:00:00Z','review/501',None,
        'GitHub review 501 dismissed (was approved)')),
    ('M11',1,7,'event/702',('comment','gh:lin-eng:5002','2026-10-07T08:00:00Z',None,None,
        'GitHub review 701 dismissed by commit 7b7b7b7b7b7b (stale review)')),
    ('M12',1,11,'event/1106',('comment',PM,'2026-10-07T11:00:00Z',None,None,
        'GitHub review 1105 dismissed (was approved)')),
    ('M13',1,2,'event/204',('request','gh:lin-eng:5002','2026-10-06T15:00:00Z',None,None,
        'GitHub review re-requested from gh:zhou-pm:5003 after changes requested')),
    ('M14',1,2,'event/201',None),
    ('M15',1,1,'event/105',('reject',PM,'2026-10-07T15:30:00Z',None,None,'GitHub issue closed as not_planned')),
    ('M16',1,4,'event/401',('comment',PM,'2026-10-06T12:00:00Z',None,None,
        'GitHub issue closed as completed by commit 9f1c2e3d4b5a')),
    ('M18',1,2,'event/208',None),
    ('M19',1,2,'event/207',('approve',PM,'2026-10-07T10:00:00Z',None,None,
        'GitHub pull request merged at 54bb654c9e60')),
    ('M20',2,13,'event/1302',('revoke',PM,'2026-10-10T11:00:00Z','event/1301',None,
        'GitHub issue reopened; withdraws gh:acme/web#13/event/1301')),
    ('M20',2,13,'event/1302/request',('request',PM,'2026-10-10T11:00:00Z',None,None,
        'GitHub issue reopened')),
    ('M21',1,4,'event/402',('request',PM,'2026-10-06T13:00:00Z',None,None,'GitHub issue reopened')),
    ('M22',1,1,'event/107',('approve','gh:wu-sales:5004','2026-10-08T16:00:00Z',None,None,
        'GitHub label "approved" added')),
    ('M24',1,1,'event/101',None),
    ('M25',1,1,'event/103',('dependency','gh:lin-eng:5002','2026-10-06T10:00:00Z',None,'acme/billing#3',
        'GitHub blocked-by link added')),
])
def test_mapping_fixture_ids(row,n,subject,suffix,expected):
    p = plan(recording(n))
    base = f'gh:acme/web#{subject}'
    events = [e for e in p.events if e.id == base+'/'+suffix]
    if expected is None:
        assert events == []
    else:
        action_,aid,stamp,target,dependency,note = expected
        assert [(e.id,e.action,e.actor,e.subject,e.timestamp,e.supersedes,e.dependency,e.note) for e in events] == [
            (base+'/'+suffix,action_,aid,f'acme/web#{subject}',datetime.fromisoformat(stamp.replace('Z','+00:00')),
             base+'/'+target if target else None,dependency,note)]


@pytest.mark.parametrize('dependency', [
    {'number':True,'repository':{'full_name':'acme/billing'}},
    {'number':'3','repository':{'full_name':'acme/billing'}},
    {'number':3,'repository':{'full_name':'acme/../policy'}},
    {'number':3,'repository':{'full_name':'acme/billing\nforged'}},
    {'number':3,'repository':{'full_name':'acme/..'}},
])
def test_dependency_parts_are_validated(dependency):
    p = plan(single(ev('blocked_by_added',11,'lin-eng',T1,blocked_by=dependency)))
    assert [e.action for e in p.events] == ['request']
    assert p.skipped == [BASE+'/event/11 (malformed blocked-by link)']


def test_immutable_event_captures_are_not_rewritten_on_drift():
    c = single(close())
    first = plan(c)
    c['items'][0]['timeline'][0]['actor'] = user('mallory')
    p = plan(c, existing={e.id:e for e in first.events}, evidence_ids=set(first.spans))
    assert p.events == [] and p.captures == []
    assert p.drift == [BASE+'/event/10']


def test_authorized_reopen_of_unauthorized_close_reports_invalid_supersession(github_dataset):
    c = single(ev('closed',10,'mallory',T1,state_reason='not_planned'), ev('reopened',11,'zhou-pm',T2))
    result = outcomes(github_dataset,c)
    state = result.states['acme/web#1']
    assert state.current_status == 'proposed'
    assert [x.kind for x in state.conflicts] == ['invalid_supersession']
    assert state.pending_approval == ['product_manager']
    assert [x.event_id for x in state.pending_changes] == [BASE+'/event/10']


@pytest.mark.parametrize('blocked_by', [None, {}, {'number': 3}])
def test_quarantined_merge_never_turns_automatic_close_into_rejection(blocked_by):
    merge = ev('merged',11,'zhou-pm',T1,commit_id=SHA,blocked_by=blocked_by)
    p = plan(single(merge,close(),pr=True))
    assert [e.action for e in p.events] == ['request']
    assert p.skipped == [BASE+'/event/11 (unrecognized dependency event merged)']
    assert not any('/event/' in i for i in p.spans)


@pytest.mark.parametrize('state', ['approved', 'dismissed'])
@pytest.mark.parametrize('blocked_by', [None, {}, {'number': 3}])
def test_contradictory_dismissal_dependency_shape_is_refused(state, blocked_by):
    d = dismissed()
    d['blocked_by'] = blocked_by
    with pytest.raises(ValueError, match='conflicting dismissal and dependency'):
        plan(single(review(20,'zhou-pm',state,T1),d,pr=True))


@pytest.mark.parametrize('marker', [None, False, {}, 'url', {'url': None}, {'url': 1}])
def test_malformed_pr_marker_cannot_promote_completed_close(marker):
    with pytest.raises(ValueError, match='pull_request is not a PR object'):
        plan(single(close('completed',commit_id=SHA),pull_request=marker))


def test_latest_dismissal_record_supplies_recovered_verdict():
    first = dismissed(state='approved')
    latest = ev('review_dismissed',12,'lin-eng',T3,dismissed_review={
        'review_id':20,'state':'commented','dismissal_commit_id':SHA})
    p = plan(single(review(20,'zhou-pm','dismissed',T1,'Review text'),first,latest,pr=True))
    assert [e.action for e in p.events] == ['request','comment','comment','comment']
    r = next(e for e in p.events if e.id == BASE+'/review/20')
    assert r.note == f'GitHub review commented on commit {SHA[:12]}; dismissed later'
    assert r.evidence_ids[-1] == BASE+'/event/12/fact'


def test_fixture_completed_and_duplicate_closes_are_comments():
    events = {e.id:e for e in plan(recording(1)).events}
    for oid in (401,403,405):
        e = events[f'gh:acme/web#4/event/{oid}']
        assert (e.action,e.actor,e.supersedes) == ('comment',PM,None)


def test_blank_submitted_review_keeps_evidence_without_an_event():
    c = single(review(20,'zhou-pm','commented',T1,''),pr=True)
    p = plan(c)
    assert [e.action for e in p.events] == ['request']
    assert any('/review/20@' in i and i.endswith('/fact') for i in p.spans)
    c['items'][0]['timeline'][0]['body'] = 'Review text before edit'
    first = plan(c)
    c['items'][0]['timeline'][0]['body'] = ''
    p = plan(c,existing={e.id:e for e in first.events},evidence_ids=set(first.spans))
    assert p.events == [] and p.new_versions == 1
    assert any('/review/20@' in i and i.endswith('/fact') for i in p.spans)
