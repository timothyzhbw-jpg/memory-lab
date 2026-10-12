"""Offline integration, persistence, crash recovery and CLI contracts."""
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

import pytest
from typer.testing import CliRunner

from conftest import snapshot, write_text_exact
from github_fakes import FakeClock, FakeTransport, build_capture, ev, issue, item, reply, review, routes_from_capture
from memcore import read_jsonl
from orgmemory import github_apply, github_fetch
from orgmemory.cli import app
from orgmemory.github_apply import load_existing, next_since, pinned_repo_id, run_convert, run_sync
from orgmemory.github_map import GithubConfig, convert
from orgmemory.replay import Dataset, replay, validate

FIXTURES = Path(__file__).parent / 'fixtures' / 'github'
GOLDEN = Path(__file__).parents[1] / 'examples' / 'orgmemory' / 'github-acme-web'
# Rendered reports next to the dataset; tests/test_html.py and test_orgmemory_audit.py check them.
REPORTS = ('replay-report.md', 'audit-report.md', 'replay-report.html', 'audit-report.html')
RUNNER = CliRunner()
TOKEN = 'ghp_TESTTOKEN'


def recording(n):
    return json.loads((FIXTURES / f'capture-{n}.json').read_bytes())


def converted(root, n, **kw):
    return run_convert(root, FIXTURES / f'capture-{n}.json', **kw)


def relative_snapshot(root, *, log=True):
    return {str(p.relative_to(root)):data for p,data in snapshot(root).items()
            if log or p.name != 'github-sync.jsonl'}


def dataset(parent, name):
    root = parent / name
    root.mkdir()
    for filename in ('github.json','policy.json'):
        shutil.copyfile(FIXTURES / filename, root / filename)
    return root


def capture_file(root, capture):
    path = root / 'input.json'
    write_text_exact(path, json.dumps(capture,ensure_ascii=True))
    return path


def client(capture, **kw):
    clock = FakeClock()
    transport = FakeTransport(routes_from_capture(capture))
    return github_fetch.Client(transport,TOKEN,sleep=clock.sleep,clock=clock.clock,**kw), transport


def sync(root, capture, **kw):
    c, transport = client(capture)
    return run_sync(root,'acme/web',c,now=lambda:datetime(2026,10,11,4,tzinfo=timezone.utc),**kw), transport


def log(root):
    return [line for _,line in read_jsonl(root / 'github-sync.jsonl')]


def test_golden_example_reproducible(github_dataset):
    converted(github_dataset,1)
    converted(github_dataset,2)
    Dataset.load(github_dataset)
    assert relative_snapshot(github_dataset) == {
        k:v for k,v in relative_snapshot(GOLDEN).items() if k not in REPORTS + ('changes-report.md',)}
    from orgmemory.report import to_markdown
    assert to_markdown(replay(Dataset.load(github_dataset)))+'\n' == (GOLDEN/'replay-report.md').read_text(encoding='utf-8')


def test_rerun_is_idempotent(github_dataset):
    converted(github_dataset,1)
    before = relative_snapshot(github_dataset,log=False)
    log_before = (github_dataset/'github-sync.jsonl').read_bytes()
    summary = converted(github_dataset,1)
    assert (summary.events_appended,summary.evidence_appended,summary.files_created) == (0,0,0)
    assert relative_snapshot(github_dataset,log=False) == before
    assert len(log(github_dataset)) == 2
    assert (github_dataset/'github-sync.jsonl').read_bytes().startswith(log_before)


@pytest.mark.parametrize('window,new_actor,new_state', [
    ('blank','zhou-pm','approved'),
    ('crash','zhou-pm','approved'),
    ('crash','mallory','changes_requested'),
])
def test_new_event_cannot_reuse_conflicting_stored_fact(github_dataset,tmp_path,monkeypatch,
                                                       window,new_actor,new_state):
    stamp = '2026-10-06T09:00:00Z'
    state, body = ('commented','') if window == 'blank' else ('approved','Approved.')
    capture = build_capture(item(issue(1,'buyer-acme','2026-10-05T09:00:00Z',pr=True),
                                 review(20,'mallory',state,stamp,body)))
    path = capture_file(tmp_path,capture)
    if window == 'blank':
        run_convert(github_dataset,path)
    else:
        original = github_apply.append_jsonl
        def interrupted(path, record):
            if path.name == 'events.jsonl':
                raise OSError('simulated crash')
            original(path,record)
        with monkeypatch.context() as m:
            m.setattr(github_apply,'append_jsonl',interrupted)
            with pytest.raises(OSError):
                run_convert(github_dataset,path)
    stored = load_existing(github_dataset)
    fact = next(s for i,s in stored.spans.items() if '/review/20@' in i and i.endswith('/fact'))
    assert 'by gh:mallory:6666' in fact.quote
    capture['items'][0]['timeline'] = [review(20,new_actor,new_state,stamp,body)]
    path = capture_file(tmp_path,capture)
    before = snapshot(github_dataset)
    for dry_run in (False,True):
        with pytest.raises(ValueError,match='fact conflicts with existing evidence'):
            run_convert(github_dataset,path,dry_run=dry_run)
        assert snapshot(github_dataset) == before
    c, transport = client(capture)
    saved = tmp_path/'saved.json'
    with pytest.raises(ValueError,match='fact conflicts with existing evidence'):
        run_sync(github_dataset,'acme/web',c,save_capture=saved)
    assert transport.calls and not saved.exists()
    assert snapshot(github_dataset) == before


@pytest.mark.parametrize('dismissal_time', ['2026-10-06T09:00:00Z','2026-10-05T10:00:00Z'])
@pytest.mark.parametrize('previously_synced', [False,True])
def test_dismissal_before_review_fails_closed_for_both_histories(github_dataset,tmp_path,
                                                               dismissal_time,previously_synced):
    stamp = '2026-10-06T09:00:00Z'
    opening = issue(1,'buyer-acme','2026-10-05T09:00:00Z',pr=True)
    if previously_synced:
        first = build_capture(item(opening,review(20,'zhou-pm','approved',stamp,'Approved.')))
        run_convert(github_dataset,capture_file(tmp_path,first))
    dismissal = ev('review_dismissed',11,'zhou-pm',dismissal_time,
                   dismissed_review={'review_id':20,'state':'approved'})
    capture = build_capture(item(opening,dismissal,review(20,'zhou-pm','dismissed',stamp,'Approved.')))
    path = capture_file(tmp_path,capture)
    before = snapshot(github_dataset)
    with pytest.raises(ValueError,match='dismissal precedes its submitted review'):
        run_convert(github_dataset,path)
    assert snapshot(github_dataset) == before


def test_incremental_run_is_append_only(github_dataset):
    converted(github_dataset,1)
    before = relative_snapshot(github_dataset,log=False)
    converted(github_dataset,2)
    after = relative_snapshot(github_dataset,log=False)
    for name,data in before.items():
        assert after[name].startswith(data) if name in ('events.jsonl','evidence.jsonl') else after[name] == data
    Dataset.load(github_dataset)


def test_edit_is_evidence_only_and_deletion_reported(github_dataset):
    converted(github_dataset,1)
    old = load_existing(github_dataset)
    old_comment = next(e for e in old.events if e.id == 'gh:acme/web#3/comment/301')
    summary = converted(github_dataset,2)
    assert summary.plan.new_versions == 1
    assert summary.plan.gone == ['gh:acme/web#3/comment/302']
    assert not any(e.id == old_comment.id for e in summary.plan.events)
    versions = [s for s in load_existing(github_dataset).spans if '/comment/301@' in s and s.endswith('/fact')]
    assert len(versions) == 2
    current = load_existing(github_dataset)
    assert next(e for e in current.events if e.id == old_comment.id) == old_comment
    assert any(e.id == 'gh:acme/web#3/comment/302' for e in current.events)
    assert log(github_dataset)[-1]['gone'] == summary.plan.gone
    before = relative_snapshot(github_dataset,log=False)
    again = converted(github_dataset,2)
    assert (again.events_appended,again.evidence_appended,again.files_created) == (0,0,0)
    assert again.plan.gone == summary.plan.gone
    assert before == relative_snapshot(github_dataset,log=False)


def state_tuple(state):
    return (state.current_status,state.last_authorized_decision.event_id if state.last_authorized_decision else None,
            state.pending_approval,state.reconsideration_requested,state.dependencies)


def test_states_do_not_depend_on_sync_history(tmp_path):
    first, alone = dataset(tmp_path,'first'), dataset(tmp_path,'alone')
    converted(first,1)
    converted(first,2)
    converted(alone,2)
    a,b = replay(Dataset.load(first)), replay(Dataset.load(alone))
    assert {s:state_tuple(a.states[s]) for s in b.states} == {s:state_tuple(v) for s,v in b.states.items()}


def test_policy_json_never_read(tmp_path,monkeypatch):
    roots = [dataset(tmp_path,str(n)) for n in range(3)]
    (roots[0]/'policy.json').unlink()
    write_text_exact(roots[1]/'policy.json', 'invalid JSON')
    cap = recording(1)
    actors = {u['login']:['product_manager'] for i in cap['items']
              for u in [i['issue']['user']] if u}
    write_text_exact(roots[2]/'policy.json', json.dumps({'actors':actors,'permissions':{}}))
    policies = [(root/'policy.json').read_bytes() if (root/'policy.json').exists() else None for root in roots]
    real_open = Path.open
    def guarded(path,*args,**kwargs):
        if path.name == 'policy.json':
            raise AssertionError('importer opened policy.json')
        return real_open(path,*args,**kwargs)
    with monkeypatch.context() as m:
        m.setattr(Path,'open',guarded)
        for root in roots:
            converted(root,1)
            converted(root,2)
    outputs = [{k:v for k,v in relative_snapshot(root).items() if k != 'policy.json'} for root in roots]
    assert outputs[0] == outputs[1] == outputs[2]
    assert policies == [(root/'policy.json').read_bytes() if (root/'policy.json').exists() else None for root in roots]


@pytest.mark.parametrize('kind',['tampered','collision','malformed_events','string_id','duplicate','bad_log','symlink'])
def test_fails_closed(github_dataset,kind,tmp_path,monkeypatch):
    if kind in ('tampered','duplicate','bad_log'):
        converted(github_dataset,1)
    if kind == 'tampered':
        target = next((github_dataset/'sources').rglob('*.txt'))
        target.write_bytes(target.read_bytes() + b'\ntampered outside every quoted span')
    elif kind == 'collision':
        p = convert(recording(1),{},set(),GithubConfig(labels={'approved':'approve','risk':'raise_risk'}))
        path = github_dataset/p.captures[-1].path
        path.parent.mkdir(parents=True)
        path.write_bytes(b'conflict')
    elif kind == 'malformed_events':
        write_text_exact(github_dataset/'events.jsonl', '{not json}\n')
    elif kind == 'duplicate':
        path = github_dataset/'events.jsonl'
        with path.open('ab') as handle:
            handle.write(path.read_bytes().splitlines(keepends=True)[0])
    elif kind == 'bad_log':
        write_text_exact(github_dataset/'github-sync.jsonl', '{"repo_id":"1001"}\n')
    elif kind == 'symlink':
        outside = tmp_path/'outside'
        outside.mkdir()
        (github_dataset/'sources').symlink_to(outside,target_is_directory=True)
    cap = recording(1)
    if kind == 'string_id':
        cap['items'][0]['timeline'][1]['id'] = '102'
    path = capture_file(tmp_path,cap)
    before = snapshot(github_dataset)
    result = RUNNER.invoke(app,['github','convert',str(github_dataset),str(path)])
    assert result.exit_code == 2, result.output
    assert snapshot(github_dataset) == before
    if kind in ('tampered','duplicate','bad_log','malformed_events'):
        transport = FakeTransport({})
        monkeypatch.setattr(github_fetch,'PersistentTransport',lambda:transport)
        monkeypatch.setattr(github_fetch,'token_from_env',lambda:None)
        result = RUNNER.invoke(app,['github','sync',str(github_dataset),'acme/web'])
        assert result.exit_code == 2 and transport.calls == []
        assert snapshot(github_dataset) == before


@pytest.mark.parametrize('body,line', [
    ('{"id": 1, "id": 2}\n', 1),
    ('{\n  "repo": {\n    "id": 1,\n    "id": 2\n  }\n}\n', 4),
])
def test_duplicate_key_in_capture_exits_2(github_dataset, tmp_path, body, line):
    path = tmp_path / 'capture.json'
    path.write_bytes(body.encode())
    before = snapshot(github_dataset)
    result = RUNNER.invoke(app, ['github', 'convert', str(github_dataset), str(path)])
    assert result.exit_code == 2, result.output
    assert f'{path}:{line}: invalid JSON: duplicate key' in result.output
    assert snapshot(github_dataset) == before


@pytest.mark.parametrize('body,line', [
    ('{"labels": {"approved": "approve", "approved": "reject"}}\n', 1),
    ('{\n  "labels": {\n    "approved": {\n      "a": "approve",\n      "a": "reject"\n    }\n  }\n}\n', 5),
])
def test_duplicate_key_in_github_json_exits_2(github_dataset, body, line):
    path = github_dataset / 'github.json'
    path.write_bytes(body.encode())
    before = snapshot(github_dataset)
    result = RUNNER.invoke(app, ['github', 'convert', str(github_dataset), str(FIXTURES / 'capture-1.json')])
    assert result.exit_code == 2, result.output
    shown = path.resolve()
    assert f'{shown}:{line}: invalid JSON: duplicate key' in result.output
    assert snapshot(github_dataset) == before


def test_duplicate_key_in_sync_log_exits_2(github_dataset, tmp_path):
    converted(github_dataset, 1)
    log = github_dataset / 'github-sync.jsonl'
    text = log.read_bytes()
    log.write_bytes(text + b'{"repo": "acme/web", "repo": "other"}\n')
    before = snapshot(github_dataset)
    path = tmp_path / 'capture.json'
    path.write_bytes(b'{}\n')
    result = RUNNER.invoke(app, ['github', 'convert', str(github_dataset), str(path)])
    assert result.exit_code == 2, result.output
    line_number = text.count(b"\n") + 1
    assert f'{log.resolve()}:{line_number}: invalid JSON: duplicate key' in result.output
    assert snapshot(github_dataset) == before


def test_crash_between_evidence_and_events_recovers(github_dataset,monkeypatch):
    original = github_apply.append_jsonl
    def broken(path,record):
        if path.name == 'events.jsonl':
            raise OSError('simulated crash')
        original(path,record)
    with monkeypatch.context() as m:
        m.setattr(github_apply,'append_jsonl',broken)
        with pytest.raises(OSError):
            converted(github_dataset,1)
    assert not (github_dataset/'.github-sync.lock').exists()
    assert not (github_dataset/'github-sync.jsonl').exists()
    summary = converted(github_dataset,1)
    assert summary.evidence_appended == 0 and summary.events_appended == 53
    current = load_existing(github_dataset)
    validate(current.events,current.spans)
    assert len({e.id for e in current.events}) == len(current.events)
    assert len(current.spans) == sum(1 for _ in read_jsonl(github_dataset/'evidence.jsonl'))


def test_drift_reported_never_rewritten(github_dataset):
    converted(github_dataset,1)
    before = relative_snapshot(github_dataset,log=False)
    write_text_exact(github_dataset/'github.json', '{"labels":{"approved":"reject","risk":"raise_risk"}}')
    summary,_ = sync(github_dataset,recording(1),full=True)
    # Bot approve and reject both become comment, so only the human action drifts.
    assert summary.plan.drift == ['gh:acme/web#1/event/107']
    for name,data in before.items():
        if name != 'github.json':
            assert (github_dataset/name).read_bytes() == data


def test_repo_id_pin(github_dataset,tmp_path):
    converted(github_dataset,1)
    before = snapshot(github_dataset)
    capture = recording(1)
    capture['repo_id'] = 1002
    path = capture_file(tmp_path,capture)
    result = RUNNER.invoke(app,['github','convert',str(github_dataset),str(path)])
    assert result.exit_code == 2 and 'repository identity changed' in result.output
    assert snapshot(github_dataset) == before
    c,transport = client(capture)
    with pytest.raises(github_fetch.GitHubError,match='identity changed'):
        run_sync(github_dataset,'acme/web',c)
    assert len(transport.calls) == 1 and snapshot(github_dataset) == before


def test_lock_and_dry_run(github_dataset):
    before = snapshot(github_dataset)
    summary = converted(github_dataset,1,dry_run=True)
    assert summary.events_appended == 53 and snapshot(github_dataset) == before
    write_text_exact(github_dataset/'.github-sync.lock', 'held')
    before = snapshot(github_dataset)
    result = RUNNER.invoke(app,['github','convert',str(github_dataset),str(FIXTURES/'capture-1.json')])
    assert result.exit_code == 2 and 'another sync is running' in result.output
    assert snapshot(github_dataset) == before
    converted(github_dataset,1,dry_run=True)
    assert snapshot(github_dataset) == before


def test_cursor_rules():
    def line(start,**fields):
        return dict(repo='acme/web',source='sync',mode='full',complete=True,started_at=start,repo_id=1001,**fields)
    rows = [line('2026-10-09T12:00:00Z'),
            {**line('2026-10-10T12:00:00Z'),'mode':'incremental'},
            {**line('2026-10-11T12:00:00Z'),'mode':'targeted'},
            {**line('2026-10-12T12:00:00Z'),'source':'convert'},
            {**line('2026-10-13T12:00:00Z'),'complete':False},
            {**line('2026-10-14T12:00:00Z'),'repo':'acme/other'}]
    assert next_since(rows,'acme/web') == datetime(2026,10,10,11,55,tzinfo=timezone.utc)
    assert next_since(rows[2:],'acme/web') is None
    assert pinned_repo_id(rows,'acme/web') == 1001
    with pytest.raises(ValueError):
        pinned_repo_id(rows+[dict(repo='acme/web',repo_id=1002)],'acme/web')


def test_since_with_cursor_exits_2(github_dataset,monkeypatch):
    sync(github_dataset,recording(1))
    transport = FakeTransport({})
    monkeypatch.setattr(github_fetch,'PersistentTransport',lambda:transport)
    monkeypatch.setattr(github_fetch,'token_from_env',lambda:None)
    before = snapshot(github_dataset)
    result = RUNNER.invoke(app,['github','sync',str(github_dataset),'acme/web','--since','2026-10-11T00:00:00Z'])
    assert result.exit_code == 2 and 'use --full or --issue' in result.output
    assert not transport.calls and snapshot(github_dataset) == before


def test_partial_run_cursor_unchanged_and_clean_rerun(github_dataset,monkeypatch):
    sync(github_dataset,build_capture(started_at="2026-10-09T12:00:00Z"))
    previous = next_since(log(github_dataset),'acme/web')
    # This recording's query has the cursor from the preceding empty sync.
    cap = recording(2)
    cap['since'] = previous.strftime('%Y-%m-%dT%H:%M:%SZ')
    routes = routes_from_capture(cap)
    routes['GET /repos/acme/web/issues/5/timeline?per_page=100'] = reply(
        {'message':'API rate limit exceeded'},status=403,headers={'retry-after':'99999'})
    transport = FakeTransport(routes)
    monkeypatch.setattr(github_fetch,'PersistentTransport',lambda:transport)
    monkeypatch.setattr(github_fetch,'token_from_env',lambda:TOKEN)
    result = RUNNER.invoke(app,['github','sync',str(github_dataset),'acme/web','--max-wait','0'])
    assert result.exit_code == 3, result.output
    assert log(github_dataset)[-1]['complete'] is False
    assert next_since(log(github_dataset),'acme/web') == previous
    Dataset.load(github_dataset)
    transport = FakeTransport(routes_from_capture(cap))
    monkeypatch.setattr(github_fetch,'PersistentTransport',lambda:transport)
    result = RUNNER.invoke(app,['github','sync',str(github_dataset),'acme/web'])
    assert result.exit_code == 0, result.output
    current = load_existing(github_dataset)
    assert len(current.events) == len({e.id for e in current.events})
    assert next_since(current.sync_log,'acme/web') == datetime(2026,10,11,3,55,tzinfo=timezone.utc)
    Dataset.load(github_dataset)


def test_fetch_convert_round_trip():
    capture = recording(1)
    c,_ = client(capture)
    fetched = github_fetch.fetch(c,'acme/web',since=None,issues=[],pinned_repo_id=None)
    assert fetched['items'] == capture['items']
    config = GithubConfig.model_validate_json((FIXTURES/'github.json').read_bytes())
    assert convert(fetched,{},set(),config).events == convert(capture,{},set(),config).events


def test_no_network_and_no_token_leak(github_dataset,tmp_path,monkeypatch):
    monkeypatch.setattr(github_fetch,'token_from_env',lambda:TOKEN)
    monkeypatch.setattr(github_fetch,'PersistentTransport',lambda:FakeTransport(routes_from_capture(recording(1))))
    saved = tmp_path/'saved.json'
    result = RUNNER.invoke(app,['github','sync',str(github_dataset),'acme/web','--save-capture',str(saved)])
    assert result.exit_code == 0, result.output
    assert TOKEN not in result.output and 'possibly private' in result.output
    for data in list(snapshot(github_dataset).values())+[saved.read_bytes()]:
        assert TOKEN.encode() not in data
    # An API error echoing credentials is scrubbed by the client.
    routes = {'GET /repos/acme/web':reply({'message':TOKEN+'\n\x1b[31m bad'},status=403)}
    monkeypatch.setattr(github_fetch,'PersistentTransport',lambda:FakeTransport(routes))
    result = RUNNER.invoke(app,['github','sync',str(github_dataset),'acme/web','--full'])
    assert result.exit_code == 2 and TOKEN not in result.output and '\x1b' not in result.output


def test_cli_sync_and_convert(github_dataset,tmp_path,monkeypatch):
    before = snapshot(github_dataset)
    result = RUNNER.invoke(app,['github','convert',str(github_dataset),str(FIXTURES/'capture-1.json'),'--dry-run'])
    assert result.exit_code == 0, result.output
    assert snapshot(github_dataset) == before
    events = [json.loads(line) for line in result.output.splitlines() if line.startswith('{')]
    assert len(events) == 53
    assert 'actors in new events' in result.output and 'gh:github-actions[bot]:41898282' in result.output
    result = RUNNER.invoke(app,['github','convert',str(github_dataset),str(FIXTURES/'capture-1.json')])
    assert result.exit_code == 0, result.output
    assert 'appended 53 events' in result.output and 'gh:zhou-pm:5003' in result.output
    other = dataset(tmp_path,'sync')
    transport = FakeTransport(routes_from_capture(recording(1)))
    monkeypatch.setattr(github_fetch,'PersistentTransport',lambda:transport)
    monkeypatch.setattr(github_fetch,'token_from_env',lambda:None)
    saved = tmp_path/'dry-capture.json'
    before = snapshot(other)
    result = RUNNER.invoke(app,['github','sync',str(other),'Acme/Web','--dry-run','--save-capture',str(saved)])
    assert result.exit_code == 0 and snapshot(other) == before and saved.exists()
    assert 'unauthenticated' in result.output and 'possibly private' in result.output
    transport.calls.clear()
    result = RUNNER.invoke(app,['github','sync',str(other),'acme/web','--save-capture',str(saved)])
    assert result.exit_code == 2 and transport.calls == []


@pytest.mark.parametrize('options',[['--full','--since','2026-10-09T00:00:00Z'], ['--issue','1','--full'],
    ['--issue','1','--since','2026-10-09T00:00:00Z'],['--since','2026-10-09'],['--issue','0'],['--max-wait','-1']])
def test_cli_invalid_options_before_requests(github_dataset,monkeypatch,options):
    transport = FakeTransport({})
    monkeypatch.setattr(github_fetch,'PersistentTransport',lambda:transport)
    monkeypatch.setattr(github_fetch,'token_from_env',lambda:None)
    before = snapshot(github_dataset)
    result = RUNNER.invoke(app,['github','sync',str(github_dataset),'acme/web',*options])
    assert result.exit_code == 2 and not transport.calls
    assert before == snapshot(github_dataset)


def test_empty_sync_creates_logs_and_targeted_does_not_move_cursor(github_dataset):
    summary,_ = sync(github_dataset,build_capture())
    assert (summary.events_appended,summary.evidence_appended,summary.files_created) == (0,0,0)
    assert (github_dataset/'events.jsonl').read_bytes() == b''
    assert (github_dataset/'evidence.jsonl').read_bytes() == b''
    before = next_since(log(github_dataset),'acme/web')
    cap = recording(2)
    _,transport = sync(github_dataset,cap,issues=[5])
    assert next_since(log(github_dataset),'acme/web') == before
    assert [k for k in transport.keys if '/issues?' in k or '/pulls?' in k] == []
    converted(github_dataset,1)
    assert next_since(log(github_dataset),'acme/web') == before


@pytest.mark.parametrize('name',['policy.json','policy-log.jsonl','github.json','events.jsonl','evidence.jsonl','github-sync.jsonl',
    '.github-sync.lock','sources/github/acme/web/raw.json'])
def test_save_capture_cannot_overwrite_managed_files(github_dataset,name):
    if name == 'policy.json':
        (github_dataset/name).unlink()
    before = snapshot(github_dataset)
    c,transport = client(recording(1))
    with pytest.raises(ValueError):
        run_sync(github_dataset,'acme/web',c,save_capture=github_dataset/name,dry_run=True)
    assert transport.calls == [] and snapshot(github_dataset) == before


@pytest.mark.parametrize('name', ['policy.json', 'policy-log.jsonl'])
def test_save_capture_refuses_the_target_of_a_dangling_policy_alias(github_dataset,tmp_path,name):
    policy = github_dataset/name
    policy.unlink(missing_ok=True)
    target = tmp_path/'policy-target.json'
    policy.symlink_to(target)
    before = snapshot(github_dataset)
    c,transport = client(recording(1))
    with pytest.raises(ValueError, match='overlaps managed dataset files'):
        run_sync(github_dataset,'acme/web',c,save_capture=target,dry_run=True)
    assert transport.calls == [] and snapshot(github_dataset) == before
    assert policy.is_symlink() and not target.exists()


def test_orphan_temporary_file_does_not_block_recovery(github_dataset):
    path = github_dataset/'sources/github/acme/web/1/.issue-1-orphan.tmp'
    path.parent.mkdir(parents=True)
    path.write_bytes(b'partial write')
    converted(github_dataset,1)
    assert path.read_bytes() == b'partial write'
    Dataset.load(github_dataset)


def test_crash_after_event_prefix_recovers(github_dataset,monkeypatch):
    original = github_apply.append_jsonl
    count = 0
    def broken(path,record):
        nonlocal count
        if path.name == 'events.jsonl':
            count += 1
            if count == 28:
                raise OSError('simulated crash after 27 events')
        original(path,record)
    with monkeypatch.context() as m:
        m.setattr(github_apply,'append_jsonl',broken)
        with pytest.raises(OSError):
            converted(github_dataset,1)
    old = (github_dataset/'events.jsonl').read_bytes()
    summary = converted(github_dataset,1)
    assert summary.events_appended == 26 and summary.evidence_appended == 0
    assert (github_dataset/'events.jsonl').read_bytes().startswith(old)
    data = Dataset.load(github_dataset)
    assert len(data.events) == 53


@pytest.mark.parametrize('policy_name',['policy.json','policy-log.jsonl'])
@pytest.mark.parametrize('alias',['events.jsonl','evidence.jsonl','github-sync.jsonl','github.json',
    'capture.json','evidence-source'])
def test_policy_aliases_are_refused_without_opening(github_dataset,monkeypatch,alias,policy_name):
    from memcore import ManifestEntry, append_jsonl, sha256_bytes
    policy = github_dataset/policy_name
    if policy_name == 'policy-log.jsonl':
        policy.write_bytes(b'{}\n')
    if alias == 'github.json':
        (github_dataset/alias).unlink()
    if alias == 'evidence-source':
        data = policy.read_bytes()
        entry = ManifestEntry(id='policy-evidence',path=policy_name,sha256=sha256_bytes(data),quote=data.decode(),
            start=0,end=len(data.decode()),source_uri='test:policy',source_kind='test',captured_at='2026-10-09T12:00:00Z')
        append_jsonl(github_dataset/'evidence.jsonl',entry.model_dump(mode='json'))
    else:
        (github_dataset/alias).symlink_to(policy)
    real_open = Path.open
    def guarded(path,*args,**kw):
        if path.resolve() == policy.resolve():
            raise AssertionError('policy opened through an alias')
        return real_open(path,*args,**kw)
    with monkeypatch.context() as m:
        m.setattr(Path,'open',guarded)
        with pytest.raises(ValueError):
            run_convert(github_dataset,github_dataset/'capture.json' if alias == 'capture.json'
                        else FIXTURES/'capture-1.json',dry_run=True)
    c,transport = client(recording(1))
    if alias != 'capture.json':
        with pytest.raises(ValueError):
            run_sync(github_dataset,'acme/web',c,dry_run=True)
        assert transport.calls == []


@pytest.mark.parametrize('written',[1,3,20])
def test_crash_during_evidence_prefix_recovers(github_dataset,monkeypatch,tmp_path,written):
    original = github_apply.append_jsonl
    count = 0
    def broken(path,record):
        nonlocal count
        if path.name == 'evidence.jsonl':
            count += 1
            if count == written+1:
                raise OSError('simulated crash during evidence append')
        original(path,record)
    with monkeypatch.context() as m:
        m.setattr(github_apply,'append_jsonl',broken)
        with pytest.raises(OSError):
            converted(github_dataset,1)
    converted(github_dataset,1)
    clean = dataset(tmp_path,'clean')
    converted(clean,1)
    # Evidence lines may be split across runs, but each id and source is identical.
    actual,expected = load_existing(github_dataset),load_existing(clean)
    assert actual.events == expected.events and actual.spans == expected.spans
    assert (github_dataset/'events.jsonl').read_bytes() == (clean/'events.jsonl').read_bytes()
    assert relative_snapshot(github_dataset,log=False) == relative_snapshot(clean,log=False)


@pytest.mark.parametrize('status',[200,403])
def test_escaped_credential_reflection_is_refused(github_dataset,tmp_path,monkeypatch,status):
    escaped = ''.join('\\u%04x' % ord(ch) for ch in TOKEN)
    body = ('{"message":"'+escaped+'"}').encode()
    assert TOKEN.encode() not in body
    transport = FakeTransport({'GET /repos/acme/web':reply(body,status=status)})
    monkeypatch.setattr(github_fetch,'PersistentTransport',lambda:transport)
    monkeypatch.setattr(github_fetch,'token_from_env',lambda:TOKEN)
    saved = tmp_path/'saved.json'
    before = snapshot(github_dataset)
    result = RUNNER.invoke(app,['github','sync',str(github_dataset),'acme/web','--save-capture',str(saved)])
    assert result.exit_code == 2 and TOKEN not in result.output and 'reflected the credential' in result.output
    assert snapshot(github_dataset) == before and not saved.exists()
