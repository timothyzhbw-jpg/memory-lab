"""Adversarial review cases: authority, persistence boundaries and malformed captures."""
import copy
import json
from datetime import datetime, timedelta, timezone

import pytest
from typer.testing import CliRunner

from conftest import snapshot
from jira_fakes import SITE, T1, T2, T3, T4, capture, comment, history, issue, page, single, status
from orgmemory import jira_apply
from orgmemory.cli import app
from orgmemory.jira_apply import _stamp, load_existing, run_convert
from orgmemory.jira_map import JiraCaptureError, JiraConfig, _iso, _ts, convert


CONFIG = JiraConfig(statuses={"10003": "approve", "10004": "reject", "10005": "revoke"})


def plan(data, previous=None):
    return convert(data, {e.id: e for e in previous.events} if previous else {},
                   set(previous.spans) if previous else set(), CONFIG)


def saved(tmp_path, data):
    path = tmp_path / "capture.json"
    path.write_bytes(json.dumps(data, ensure_ascii=True).encode("utf-8"))
    return path


@pytest.fixture
def root(tmp_path):
    path = tmp_path / "dataset"
    path.mkdir()
    (path / "jira.json").write_bytes(CONFIG.model_dump_json().encode("utf-8"))
    return path


def test_missing_field_id_cannot_authorize_a_transition():
    item = status("10000", "10003")
    del item["fieldId"]
    result = plan(single(history(1, "zhou", T1, item)))
    assert [e.action for e in result.events] == ["request"]
    assert result.unmapped == 1


@pytest.mark.parametrize("stop_at", ["events.jsonl", "jira-sync.jsonl"])
@pytest.mark.parametrize("dry_run", [False, True])
@pytest.mark.parametrize("creator", ["lin", None])
def test_site_pin_survives_an_interrupted_import(root, tmp_path, monkeypatch, stop_at, dry_run, creator):
    original = jira_apply.append_jsonl

    def interrupted(path, record):
        if path.name == stop_at:
            raise OSError("simulated crash")
        original(path, record)

    with monkeypatch.context() as m:
        m.setattr(jira_apply, "append_jsonl", interrupted)
        with pytest.raises(OSError):
            run_convert(root, saved(tmp_path, single(history(1, "zhou", T1, status("10000", "10003")),
                                                    creator=creator)))
    assert not (root / "jira-sync.jsonl").exists()
    other = capture(page(issue(2, histories=[history(2, "zhou", T2, status("10000", "10004"))])),
                    site="https://evil.atlassian.net")
    other["pages"][0]["issues"][0]["self"] = "https://evil.atlassian.net/rest/api/3/issue/10002"
    before = snapshot(root)
    with pytest.raises(JiraCaptureError, match="Jira site changed"):
        run_convert(root, saved(tmp_path, other), dry_run=dry_run)
    assert snapshot(root) == before


@pytest.mark.parametrize("kind", ["comment", "transition"])
@pytest.mark.parametrize("change", ["move", "reuse"])
def test_issue_identity_is_pinned_without_a_creator(root, tmp_path, kind, change):
    args = {"comments": [comment(1, "lin", T1)]} if kind == "comment" else {
        "histories": [history(1, "zhou", T1, status("10000", "10003"))]}
    run_convert(root, saved(tmp_path, capture(page(issue(1, creator=None, **args)))))
    changed = issue(1, creator=None, project="OPS", **args) if change == "move" else issue(
        2, creator=None, histories=[history(2, "zhou", T2, status("10000", "10004"))])
    if change == "reuse":
        changed["key"] = "ACME-1"
    result = run_convert(root, saved(tmp_path, capture(page(changed))))
    assert result.events_appended == result.evidence_appended == 0
    assert result.plan.skipped and ("moved issue" in result.plan.skipped[0] if change == "move"
                                   else "is issue 10001" in result.plan.skipped[0])


@pytest.mark.parametrize("omission", ["prefix", "cycle", "all"])
def test_a_recapture_cannot_silently_drop_known_transitions(omission):
    moves = [history(1, "zhou", T1, status("10000", "10003")),
             history(2, "zhou", T2, status("10003", "10004")),
             history(3, "zhou", T3, status("10004", "10003"))]
    previous = plan(single(*moves))
    kept = moves[1:] if omission == "prefix" else moves[:1] if omission == "cycle" else []
    result = plan(single(*kept, history(4, "zhou", T4, status("10003", "10005"))), previous)
    assert not result.complete and result.events == [] and result.spans == {}
    assert "previously captured transition" in result.incomplete[0]


@pytest.mark.parametrize("kind", ["created", "comment", "transition"])
def test_event_time_drift_is_reported(kind):
    original = single(history(1, "zhou", T1, status("10000", "10003")), comments=[comment(1, "lin", T1)])
    previous = plan(original)
    changed = copy.deepcopy(original)
    obj = changed["pages"][0]["issues"][0]
    target = obj["fields"] if kind == "created" else obj["fields"]["comment"]["comments"][0] if kind == "comment" \
        else obj["changelog"]["histories"][0]
    target["created"] = T2
    result = plan(changed, previous)
    eid = "jira:ACME-1/created" if kind == "created" else f"jira:ACME-1/{kind}/1"
    assert result.events == [] and eid in result.drift


@pytest.mark.parametrize("node_type", [[], {}, 17, None])
def test_malformed_adf_node_types_fail_with_a_safe_error(node_type):
    with pytest.raises(JiraCaptureError, match="document node type"):
        plan(single(comments=[comment(1, "lin", T1, {"type": node_type, "content": []})]))


def test_revoke_uses_logged_decision_times_despite_capture_drift():
    # Two cycles both return to 10000; reordering them still passes the chain check.
    first = [history(1, "zhou", T1, status("10000", "10003")),
             history(2, "lin", T1, status("10003", "10000")),
             history(3, "zhou", T2, status("10000", "10004")),
             history(4, "lin", T2, status("10004", "10000"))]
    previous = plan(single(*first))
    changed = copy.deepcopy(first)
    changed[0]["created"] = changed[1]["created"] = T3
    result = plan(single(*changed, history(5, "zhou", T4, status("10000", "10005"))), previous)
    revoke, = result.events
    assert revoke.supersedes == "jira:ACME-1/transition/3"
    assert set(result.drift) == {"jira:ACME-1/transition/1", "jira:ACME-1/transition/2"}


def test_year_formatting_does_not_depend_on_platform_strftime():
    class UnpaddedYear(datetime):
        def strftime(self, pattern):
            # Model libc implementations that render %Y without four-digit padding.
            return super().strftime(pattern).replace("0001-", "1-")

    when = UnpaddedYear(1, 1, 1, tzinfo=timezone.utc)
    assert _iso(when) == "0001-01-01T00:00:00.000Z"
    assert _stamp(when) == "0001-01-01T00:00:00Z"


def test_finish_time_is_converted_to_utc_before_labelling_it_z():
    when = datetime(2026, 10, 9, 20, 0, tzinfo=timezone(timedelta(hours=8)))
    assert _stamp(when) == "2026-10-09T12:00:00Z"


@pytest.mark.parametrize("offset", ["+0060", "-1261", "+12:99"])
def test_invalid_timezone_minutes_are_rejected(offset):
    with pytest.raises(JiraCaptureError, match="not a timestamp"):
        _ts("2026-10-05T09:00:00" + offset, "test time")


def test_cli_does_not_claim_nothing_was_appended_after_a_write_failure(root, tmp_path, monkeypatch):
    original = jira_apply.append_jsonl

    def interrupted(path, record):
        original(path, record)
        if path.name == "events.jsonl":
            raise OSError("untrusted \x1b[31m filesystem error")

    monkeypatch.setattr(jira_apply, "append_jsonl", interrupted)
    result = CliRunner().invoke(app, ["jira", "convert", str(root), str(saved(tmp_path, single()))])
    assert result.exit_code == 2 and load_existing(root).events
    assert "nothing appended" not in result.output
    assert result.output.isascii() and "\x1b" not in result.output and "untrusted" not in result.output


@pytest.mark.parametrize("stop_at, count", [("evidence.jsonl", 1), ("evidence.jsonl", 3),
                                           ("events.jsonl", 1), ("events.jsonl", 2)])
def test_retry_after_partial_appends_has_no_duplicate_records(root, tmp_path, monkeypatch, stop_at, count):
    path = saved(tmp_path, single(history(1, "zhou", T1, status("10000", "10003")),
                                  comments=[comment(1, "lin", T1)]))
    original, appended = jira_apply.append_jsonl, 0

    def interrupted(destination, record):
        nonlocal appended
        original(destination, record)
        if destination.name == stop_at:
            appended += 1
            if appended == count:
                raise OSError("simulated crash")

    with monkeypatch.context() as m:
        m.setattr(jira_apply, "append_jsonl", interrupted)
        with pytest.raises(OSError):
            run_convert(root, path)
    run_convert(root, path)
    existing = load_existing(root)
    assert len(existing.events) == 3 and len(existing.spans) == 5
    again = run_convert(root, path)
    assert again.events_appended == again.evidence_appended == again.files_created == 0


def test_missing_transition_in_evidence_is_detected_before_its_event_exists(root, tmp_path, monkeypatch):
    first = single(history(1, "zhou", T1, status("10000", "10003")),
                   history(2, "lin", T2, status("10003", "10000")))
    original = jira_apply.append_jsonl

    def interrupted(path, record):
        if path.name == "events.jsonl":
            raise OSError("simulated crash")
        original(path, record)

    with monkeypatch.context() as m:
        m.setattr(jira_apply, "append_jsonl", interrupted)
        with pytest.raises(OSError):
            run_convert(root, saved(tmp_path, first))
    changed = single(history(3, "zhou", T3, status("10000", "10005")))
    result = run_convert(root, saved(tmp_path, changed))
    assert not result.plan.complete and result.events_appended == result.evidence_appended == 0
    assert "previously captured transition" in result.plan.incomplete[0]


def test_an_older_capture_is_not_missing_transitions_that_happened_later():
    old = single(history(1, "zhou", T1, status("10000", "10003")))
    old["captured_at"] = T1
    newer = plan(single(history(1, "zhou", T1, status("10000", "10003")),
                        history(2, "zhou", T2, status("10003", "10004"))))
    result = plan(old, newer)
    assert result.complete and result.events == [] and result.drift == []


def test_an_older_capture_does_not_report_later_comments_as_deleted():
    old = single()
    old["captured_at"] = T1
    newer = plan(single(comments=[comment(1, "lin", T2)]))
    result = plan(old, newer)
    assert result.complete and result.gone == []


def test_malformed_adf_cli_fails_before_any_dataset_write(root, tmp_path):
    before = snapshot(root)
    data = single(comments=[comment(1, "lin", T1, {"type": {"\x1b[31mowned": []}})])
    result = CliRunner().invoke(app, ["jira", "convert", str(root), str(saved(tmp_path, data))])
    assert result.exit_code == 2 and "document node type" in result.output
    assert result.output.isascii() and "owned" not in result.output and snapshot(root) == before
