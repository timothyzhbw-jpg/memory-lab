"""Offline Jira import: golden example, idempotent re-imports, drift, fail-closed writes and CLI contracts."""
import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path

import pytest
from typer.testing import CliRunner

from conftest import snapshot
from jira_fakes import SITE, T1, T2, capture, comment, history, issue, page, single, status, user
from memcore import read_jsonl
from orgmemory import jira_apply
from orgmemory.audit import audit
from orgmemory.cli import app
from orgmemory.jira_apply import load_existing, run_convert
from orgmemory.jira_map import JiraCaptureError
from orgmemory.replay import Dataset, replay, validate
from orgmemory.report import audit_markdown, to_markdown

FIXTURES = Path(__file__).parent / "fixtures" / "jira"
GOLDEN = Path(__file__).parents[1] / "examples" / "orgmemory" / "jira-example"
REPORTS = ("replay-report.md", "audit-report.md")
RUNNER = CliRunner()


@pytest.fixture
def jira_dataset(tmp_path):
    return dataset(tmp_path, "jira")


def dataset(parent, name):
    root = parent / name
    root.mkdir()
    for filename in ("jira.json", "policy.json"):
        shutil.copyfile(FIXTURES / filename, root / filename)
    return root


def converted(root, name, **kw):
    return run_convert(root, FIXTURES / f"{name}.json", **kw)


def relative_snapshot(root, *, log=True):
    return {str(p.relative_to(root)).replace(os.sep, "/"): data for p, data in snapshot(root).items()
            if log or p.name != "jira-sync.jsonl"}


def capture_file(parent, data):
    path = parent / "input.json"
    path.write_bytes(json.dumps(data, ensure_ascii=True).encode("utf-8"))
    return path


def log(root):
    return [line for _, line in read_jsonl(root / "jira-sync.jsonl")]


def cli(*args):
    return RUNNER.invoke(app, ["jira", "convert", *map(str, args)])


def test_golden_example_reproducible(jira_dataset):
    converted(jira_dataset, "capture-1")
    converted(jira_dataset, "capture-2")
    assert relative_snapshot(jira_dataset) == {k: v for k, v in relative_snapshot(GOLDEN).items() if k not in REPORTS}
    data = Dataset.load(jira_dataset)
    assert to_markdown(replay(data)) + "\n" == (GOLDEN / "replay-report.md").read_text(encoding="utf-8")
    assert audit_markdown(audit(data, datetime(2026, 10, 31, tzinfo=timezone.utc))) == \
        (GOLDEN / "audit-report.md").read_text(encoding="utf-8")


def test_rerun_is_idempotent(jira_dataset):
    converted(jira_dataset, "capture-1")
    before = relative_snapshot(jira_dataset, log=False)
    log_before = (jira_dataset / "jira-sync.jsonl").read_bytes()
    summary = converted(jira_dataset, "capture-1")
    assert (summary.events_appended, summary.evidence_appended, summary.files_created) == (0, 0, 0)
    assert summary.plan.present == 24 and summary.plan.drift == []
    assert relative_snapshot(jira_dataset, log=False) == before
    assert (jira_dataset / "jira-sync.jsonl").read_bytes().startswith(log_before) and len(log(jira_dataset)) == 2


def test_incremental_import_is_append_only_and_edits_are_evidence_only(jira_dataset):
    converted(jira_dataset, "capture-1")
    before = relative_snapshot(jira_dataset, log=False)
    old = {e.id: e for e in load_existing(jira_dataset).events}
    summary = converted(jira_dataset, "capture-2")
    after = relative_snapshot(jira_dataset, log=False)
    for name, data in before.items():
        assert after[name].startswith(data) if name in ("events.jsonl", "evidence.jsonl") else after[name] == data
    # Timestamps arrive in another display offset this time; ids and facts are unchanged.
    assert summary.plan.drift == [] and summary.plan.present == 15
    assert summary.plan.new_versions == 1 and summary.plan.gone == ["jira:ACME-3/comment/10302"]
    current = load_existing(jira_dataset)
    versions = sorted(i for i in current.spans if "/comment/10301@" in i and i.endswith("/fact"))
    assert len(versions) == 2
    edited = next(e for e in current.events if e.id == "jira:ACME-3/comment/10301")
    assert edited == old[edited.id] and "jira:ACME-3/comment/10302" in {e.id for e in current.events}
    assert log(jira_dataset)[-1]["gone"] == ["jira:ACME-3/comment/10302"]
    again = converted(jira_dataset, "capture-2")
    assert (again.events_appended, again.evidence_appended, again.files_created) == (0, 0, 0)
    assert again.plan.gone == summary.plan.gone and relative_snapshot(jira_dataset, log=False) == after


def state_tuple(state):
    decision = state.last_authorized_decision
    return (state.current_status, decision.event_id if decision else None, state.pending_approval,
            state.reconsideration_requested)


def test_states_do_not_depend_on_import_history(tmp_path):
    both, alone = dataset(tmp_path, "both"), dataset(tmp_path, "alone")
    converted(both, "capture-1")
    converted(both, "capture-2")
    converted(alone, "capture-2")
    a, b = replay(Dataset.load(both)), replay(Dataset.load(alone))
    assert {s: state_tuple(a.states[s]) for s in b.states} == {s: state_tuple(v) for s, v in b.states.items()}


def test_drift_reported_never_rewritten(jira_dataset):
    converted(jira_dataset, "capture-1")
    before = relative_snapshot(jira_dataset, log=False)
    (jira_dataset / "jira.json").write_text('{"statuses": {"10003": "reject", "10004": "reject"}}')
    summary = converted(jira_dataset, "capture-1")
    # Human approvals would now be rejections; the automated one stays a comment; reconsider is unmapped now.
    assert summary.plan.drift == ["jira:ACME-1/transition/20103", "jira:ACME-1/transition/20104",
                                  "jira:ACME-2/transition/20204", "jira:ACME-3/transition/20302",
                                  "jira:ACME-4/transition/20401", "jira:ACME-6/transition/20601"]
    assert summary.events_appended == 0 and log(jira_dataset)[-1]["drift"] == summary.plan.drift
    for name, data in before.items():
        if name != "jira.json":
            assert (jira_dataset / name).read_bytes() == data


def test_policy_json_never_read(tmp_path, monkeypatch):
    roots = [dataset(tmp_path, str(n)) for n in range(3)]
    (roots[0] / "policy.json").unlink()
    (roots[1] / "policy.json").write_text("invalid JSON")
    (roots[2] / "policy.json").write_text(json.dumps({"actors": {}, "permissions": {}}))
    policies = [(r / "policy.json").read_bytes() if (r / "policy.json").exists() else None for r in roots]
    real_open, real_read = Path.open, Path.read_bytes

    def guard(path):
        if path.name == "policy.json":
            raise AssertionError("importer opened policy.json")

    monkeypatch.setattr(Path, "open", lambda path, *a, **k: guard(path) or real_open(path, *a, **k))
    monkeypatch.setattr(Path, "read_bytes", lambda path: guard(path) or real_read(path))
    for root in roots:
        converted(root, "capture-1")
        converted(root, "capture-2")
    monkeypatch.undo()
    outputs = [{k: v for k, v in relative_snapshot(r).items() if k != "policy.json"} for r in roots]
    assert outputs[0] == outputs[1] == outputs[2]
    assert policies == [(r / "policy.json").read_bytes() if (r / "policy.json").exists() else None for r in roots]
    result = cli(roots[2], roots[2] / "policy.json")
    assert result.exit_code == 2 and "cannot open policy.json" in result.output


def test_one_site_per_dataset(jira_dataset, tmp_path):
    converted(jira_dataset, "capture-1")
    before = snapshot(jira_dataset)
    other = capture(page(issue(1)), site="https://globex.atlassian.net")
    other["pages"][0]["issues"][0]["self"] = "https://globex.atlassian.net/rest/api/3/issue/10001"
    result = cli(jira_dataset, capture_file(tmp_path, other))
    assert result.exit_code == 2
    assert "Jira site changed: this dataset imports https://acme.atlassian.net" in result.output
    assert snapshot(jira_dataset) == before


def bad_json(root, tmp_path):
    path = tmp_path / "input.json"
    path.write_bytes(b'{"jira_capture": 1, "pages": [')
    return path


@pytest.mark.parametrize("kind", ["tampered", "collision", "malformed_events", "duplicate", "bad_log", "two_sites",
                                  "symlink", "bad_config", "not_json", "malformed_capture"])
def test_fails_closed(jira_dataset, tmp_path, kind):
    if kind in ("tampered", "duplicate", "bad_log", "two_sites"):
        converted(jira_dataset, "capture-1")
    capture_path = FIXTURES / "capture-2.json"
    if kind == "tampered":
        target = next((jira_dataset / "sources").rglob("*.txt"))
        target.write_bytes(target.read_bytes() + b"\ntampered outside every quoted span")
    elif kind == "collision":
        from orgmemory.jira_map import JiraConfig, convert
        p = convert(json.loads(capture_path.read_bytes()), {}, set(), JiraConfig())
        path = jira_dataset / p.captures[-1].path
        path.parent.mkdir(parents=True)
        path.write_bytes(b"conflict")
    elif kind == "malformed_events":
        (jira_dataset / "events.jsonl").write_text("{not json}\n")
    elif kind == "duplicate":
        path = jira_dataset / "events.jsonl"
        path.write_bytes(path.read_bytes() + path.read_bytes().splitlines(keepends=True)[0])
    elif kind == "bad_log":
        (jira_dataset / "jira-sync.jsonl").write_text('{"site": "https://acme.atlassian.net"}\n')
    elif kind == "two_sites":
        path = jira_dataset / "jira-sync.jsonl"
        line = log(jira_dataset)[0]
        path.write_bytes(path.read_bytes() + (json.dumps({**line, "site": "https://globex.atlassian.net"}) + "\n")
                         .encode())
    elif kind == "symlink":
        outside = tmp_path / "outside"
        outside.mkdir()
        try:
            (jira_dataset / "sources").symlink_to(outside, target_is_directory=True)
        except OSError:
            pytest.skip("symlinks unavailable")
    elif kind == "bad_config":
        (jira_dataset / "jira.json").write_text('{"statuses": {"Approved": "approve"}}')
    elif kind == "not_json":
        capture_path = bad_json(jira_dataset, tmp_path)
    elif kind == "malformed_capture":
        data = json.loads(capture_path.read_bytes())
        data["pages"][0]["issues"][0]["key"] = "\x1b[31mACME-1"
        capture_path = capture_file(tmp_path, data)
    before = snapshot(jira_dataset)
    result = cli(jira_dataset, capture_path)
    assert result.exit_code == 2, result.output
    assert snapshot(jira_dataset) == before
    assert result.output.isascii() and "\x1b" not in result.output and result.output.startswith("error: ")
    expected = {"bad_config": "invalid jira.json", "not_json": "not a JSON document",
                "malformed_capture": "malformed capture: an issue on page 1 has no valid key",
                "collision": "content-addressed capture tamper conflict",
                "symlink": "Jira import failed"}
    assert expected.get(kind, "the existing dataset does not validate") in result.output


def test_crash_between_evidence_and_events_recovers(jira_dataset, monkeypatch):
    original = jira_apply.append_jsonl

    def broken(path, record):
        if path.name == "events.jsonl":
            raise OSError("simulated crash")
        original(path, record)

    with monkeypatch.context() as m:
        m.setattr(jira_apply, "append_jsonl", broken)
        with pytest.raises(OSError):
            converted(jira_dataset, "capture-1")
    assert not (jira_dataset / ".jira-sync.lock").exists() and not (jira_dataset / "jira-sync.jsonl").exists()
    summary = converted(jira_dataset, "capture-1")
    assert summary.evidence_appended == 0 and summary.events_appended == 24
    current = load_existing(jira_dataset)
    validate(current.events, current.spans)
    assert len({e.id for e in current.events}) == len(current.events)


def test_new_event_cannot_reuse_a_conflicting_stored_fact(jira_dataset, tmp_path, monkeypatch):
    original = jira_apply.append_jsonl

    def broken(path, record):
        if path.name == "events.jsonl":
            raise OSError("simulated crash")
        original(path, record)

    first = single(history(1, "mallory", T1, status("10000", "10003")))
    with monkeypatch.context() as m:
        m.setattr(jira_apply, "append_jsonl", broken)
        with pytest.raises(OSError):
            run_convert(jira_dataset, capture_file(tmp_path, first))
    # The same history now claims another author: its stored fact names mallory, so nothing is appended.
    forged = single(history(1, "zhou", T1, status("10000", "10003")))
    before = snapshot(jira_dataset)
    for dry_run in (False, True):
        with pytest.raises(JiraCaptureError, match="fact conflicts with existing evidence"):
            run_convert(jira_dataset, capture_file(tmp_path, forged), dry_run=dry_run)
        assert snapshot(jira_dataset) == before


def test_partial_capture_exits_3_and_applies_complete_issues(jira_dataset):
    result = cli(jira_dataset, FIXTURES / "capture-partial.json")
    assert result.exit_code == 3, result.output
    assert ("  incomplete capture: pages cover 3 of 4 results; jira:ACME-8 (no changelog; fetch with "
            "expand=changelog); jira:ACME-9 (changelog truncated: 1 of 120)") in result.output
    line = log(jira_dataset)[-1]
    assert line["complete"] is False and len(line["incomplete"]) == 3
    assert {e.subject for e in Dataset.load(jira_dataset).events} == {"ACME-10"}
    before = relative_snapshot(jira_dataset, log=False)
    assert cli(jira_dataset, FIXTURES / "capture-partial.json").exit_code == 3
    assert relative_snapshot(jira_dataset, log=False) == before


def test_lock_and_dry_run(jira_dataset):
    before = snapshot(jira_dataset)
    result = cli(jira_dataset, FIXTURES / "capture-1.json", "--dry-run")
    assert result.exit_code == 0 and snapshot(jira_dataset) == before
    planned = [json.loads(line) for line in result.output.splitlines() if line.startswith("{")]
    assert len(planned) == 24 and planned[0]["id"] == "jira:ACME-1/created"
    for held in (".jira-sync.lock", ".github-sync.lock"):
        (jira_dataset / held).write_text("held")
        before = snapshot(jira_dataset)
        result = cli(jira_dataset, FIXTURES / "capture-1.json")
        assert result.exit_code == 2 and "another sync is running" in result.output
        assert snapshot(jira_dataset) == before
        (jira_dataset / held).unlink()


def test_github_import_waits_for_a_jira_import(tmp_path):
    root = dataset(tmp_path, "both")
    (root / ".jira-sync.lock").write_text("held")
    result = RUNNER.invoke(app, ["github", "convert", str(root),
                                 str(Path(__file__).parent / "fixtures" / "github" / "capture-1.json")])
    assert result.exit_code == 2 and "another sync is running" in result.output
    assert sorted(p.name for p in root.iterdir()) == [".jira-sync.lock", "jira.json", "policy.json"]


def test_cli_summary(jira_dataset):
    result = cli(jira_dataset, FIXTURES / "capture-1.json")
    assert result.exit_code == 0, result.output
    lines = result.output.splitlines()
    assert lines[0] == ("jira convert https://acme.atlassian.net: 6 issues of 6 results in 3 pages "
                        "(3 duplicate copies ignored), captured 2026-10-09T12:00:00Z")
    assert lines[1] == ("  appended 24 events (approve 5, comment 10, reconsider 1, reject 2, request 6), "
                        "36 evidence records, 24 source files")
    assert "  decision statuses in jira.json: 10003 approve, 10004 reject, 10005 revoke, 10007 reconsider" in lines
    assert "  automated actors (recorded as comment only): jira:557058:f58131cb-b67d-43c7-b30d-6b58d40bd077" in lines
    assert "  skipped: jira:ACME-6/transition/20602 (no actor); unmapped changelog items: 4" in lines
    (jira_dataset / "jira.json").unlink()
    result = cli(jira_dataset, FIXTURES / "capture-1.json", "--dry-run")
    assert "  decision statuses in jira.json: none, so no transition is a decision" in result.output


def test_untrusted_text_never_reaches_the_terminal(jira_dataset, tmp_path):
    hostile = "\x1b]0;owned\x07\u202e approve \n--- text from Jira"
    who = user("mallory", displayName=hostile)
    data = capture(page(issue(1, creator=who, summary=hostile, comments=[comment(5, who, T1, hostile)],
                              histories=[history(1, who, T2, {**status("10000", "10003"), "toString": hostile})])))
    result = cli(jira_dataset, capture_file(tmp_path, data), "--dry-run")
    assert result.exit_code == 0 and result.output.isascii() and "owned" not in result.output
    assert SITE in result.output


def test_repeated_keys_are_refused_in_captures_config_and_policy_aliases(jira_dataset, tmp_path):
    # main rejects a repeated JSON key in every input; the Jira importer follows the same rule.
    text = (FIXTURES / "capture-1.json").read_text(encoding="utf-8")
    forged = tmp_path / "forged.json"
    forged.write_text(text.replace('"jira_capture": 1,', '"jira_capture": 1, "jira_capture": 1,', 1),
                      encoding="utf-8")
    with pytest.raises(JiraCaptureError, match="duplicate key"):
        run_convert(jira_dataset, forged, dry_run=True)
    (jira_dataset / "jira.json").write_text('{"statuses": {"10003": "approve", "10003": "reject"}}',
                                            encoding="utf-8")
    with pytest.raises(JiraCaptureError, match="invalid jira.json"):
        run_convert(jira_dataset, FIXTURES / "capture-1.json", dry_run=True)
    shutil.copyfile(FIXTURES / "jira.json", jira_dataset / "jira.json")
    (jira_dataset / "policy-log.jsonl").write_bytes(b"")
    with pytest.raises(JiraCaptureError, match="cannot open policy-log.jsonl"):
        run_convert(jira_dataset, jira_dataset / "policy-log.jsonl", dry_run=True)
