import json
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError
from typer.testing import CliRunner

from conftest import snapshot
from memcore import EvidenceError
from orgmemory.cli import app
from orgmemory.models import Event
from orgmemory.replay import Dataset, replay

runner = CliRunner()


def at(text):
    return datetime.fromisoformat(text).replace(tzinfo=timezone.utc)


def with_events(root, *extra, keep=None):
    """Return a Dataset made of the demo events (optionally a subset) plus extra event dicts."""
    data = Dataset.load(root)
    events = [e for e in data.events if keep is None or e.id in keep]
    return Dataset(events + [Event.model_validate(e) for e in extra], data.policy, data.evidence)


def event(id, when, actor, action, **fields):
    return {"id": id, "timestamp": when, "actor": actor, "action": action, "subject": "Feature A",
            "evidence_ids": ["wed"], **fields}


# --- the Monday-Thursday scenario ------------------------------------------------------

def test_thursday_request_does_not_override_wednesday_decision(org_demo):
    state = replay(Dataset.load(org_demo)).states["Feature A"]
    assert state.current_status == "rejected"
    assert state.reconsideration_requested is True
    assert state.pending_approval == ["product_manager"]
    assert state.last_authorized_decision.event_id == "ev-wed-reject"
    assert state.last_authorized_decision.evidence[0].quote.startswith("Feature A is rejected")
    assert [r.event_id for r in state.risks] == ["ev-tue-risk"]
    assert [c.event_id for c in state.pending_changes] == ["ev-thu-sales-approve"]


def test_as_of_cutoffs(org_demo):
    data = Dataset.load(org_demo)
    tuesday = replay(data, at("2026-10-06T23:59:59")).states["Feature A"]
    assert (tuesday.current_status, tuesday.pending_approval, tuesday.reconsideration_requested) == \
        ("proposed", ["product_manager"], False)
    wednesday = replay(data, at("2026-10-07T23:59:59")).states["Feature A"]
    assert (wednesday.current_status, wednesday.pending_approval, wednesday.reconsideration_requested) == \
        ("rejected", [], False)


def test_events_sorted_by_effective_time_not_file_order(org_demo):
    data = Dataset.load(org_demo)
    shuffled = Dataset(list(reversed(data.events)), data.policy, data.evidence)
    assert replay(shuffled).states == replay(data).states


def test_unauthorized_actor_is_logged_not_applied(org_demo):
    trail = {e.event_id: e for e in replay(Dataset.load(org_demo)).audit_log}
    assert trail["ev-thu-sales-approve"].outcome == "unauthorized"
    assert trail["ev-thu-sales-approve"].status_after == "rejected"


def test_unknown_actor_has_no_authority(org_demo):
    data = with_events(org_demo, event("x", "2026-10-09T00:00:00Z", "mallory", "approve"))
    assert replay(data).states["Feature A"].current_status == "rejected"


# --- authorized changes and conflicts ---------------------------------------------------

def test_authorized_reversal_is_applied_and_recorded(org_demo):
    data = with_events(org_demo, event("pm-approve", "2026-10-09T00:00:00Z", "zhou", "approve"))
    state = replay(data).states["Feature A"]
    assert state.current_status == "approved" and state.reconsideration_requested is False
    assert state.pending_approval == [] and state.pending_changes == []
    assert state.conflicts[0].kind == "decision_reversed"
    assert state.decision_history == ["ev-wed-reject", "pm-approve"]


def test_revoke_must_target_current_decision(org_demo):
    bad = with_events(org_demo, event("r", "2026-10-09T00:00:00Z", "zhou", "revoke", supersedes="ev-mon-request"))
    state = replay(bad).states["Feature A"]
    assert state.current_status == "rejected" and state.conflicts[0].kind == "invalid_supersession"
    good = with_events(org_demo, event("r", "2026-10-09T00:00:00Z", "zhou", "revoke", supersedes="ev-wed-reject"))
    state = replay(good).states["Feature A"]
    assert state.current_status == "proposed" and state.pending_approval == ["product_manager"]
    assert state.last_authorized_decision.event_id == "r"


def test_same_time_conflicting_decisions_use_ingestion_order(org_demo):
    data = with_events(org_demo, event("p1", "2026-10-07T15:30:00Z", "zhou", "approve"), keep={"ev-wed-reject"})
    state = replay(data).states["Feature A"]
    assert state.current_status == "approved"
    assert state.conflicts[0].kind == "simultaneous_decisions"


def test_dependency_is_recorded(org_demo):
    data = with_events(org_demo, event("d", "2026-10-06T11:00:00Z", "lin", "dependency", dependency="billing-migration"))
    assert replay(data).states["Feature A"].dependencies == ["billing-migration"]


# --- validation is whole-log and fails closed ----------------------------------------------

@pytest.mark.parametrize("bad", [
    {"action": "dependency"},                       # missing dependency
    {"action": "raise_risk"},                       # missing note
    {"action": "revoke"},                           # missing supersedes
    {"action": "approve", "supersedes": "ev-wed-reject"},
    {"action": "teleport"},
    {"timestamp": "2026-10-09T00:00:00"},           # naive time
    {"evidence_ids": []},
])
def test_malformed_events_rejected(bad):
    with pytest.raises(ValidationError):
        Event.model_validate({**event("x", "2026-10-09T00:00:00Z", "zhou", "approve"), **bad})


def test_malformed_event_after_cutoff_still_fails(org_demo):
    with (org_demo / "events.jsonl").open("a") as handle:
        handle.write(json.dumps(event("late", "2030-01-01T00:00:00Z", "lin", "dependency")) + "\n")
    result = runner.invoke(app, ["replay", str(org_demo), "--as-of", "2026-10-07T23:59:59Z"])
    assert result.exit_code == 2 and "requires 'dependency'" in result.output


def test_unknown_evidence_and_supersedes_rejected(org_demo):
    with pytest.raises(EvidenceError):
        with_events(org_demo, {**event("x", "2026-10-09T00:00:00Z", "zhou", "approve"), "evidence_ids": ["nope"]})
    with pytest.raises(ValueError, match="unknown event"):
        with_events(org_demo, event("x", "2026-10-09T00:00:00Z", "zhou", "revoke", supersedes="ghost"))


def test_duplicate_event_id_rejected(org_demo):
    with pytest.raises(ValueError, match="duplicate"):
        with_events(org_demo, event("ev-wed-reject", "2026-10-09T00:00:00Z", "zhou", "approve"))


def test_tampered_evidence_fails_replay(org_demo):
    (org_demo / "sources" / "2026-10-07-decision-log.txt").write_text("Feature A is approved.\n")
    result = runner.invoke(app, ["replay", str(org_demo)])
    assert result.exit_code == 2 and "hash mismatch" in result.output


# --- CLI ---------------------------------------------------------------------------------

def test_replay_and_explain_are_read_only(org_demo):
    before = snapshot(org_demo)
    replayed = runner.invoke(app, ["replay", str(org_demo)])
    explained = runner.invoke(app, ["explain", str(org_demo), "Feature A"])
    assert replayed.exit_code == explained.exit_code == 0
    assert "current_status: rejected" in replayed.output and "ev-wed-reject" in explained.output
    assert snapshot(org_demo) == before


def test_replay_json(org_demo):
    data = json.loads(runner.invoke(app, ["replay", str(org_demo), "--format", "json"]).output)
    assert data["states"]["Feature A"]["current_status"] == "rejected"


def test_append_validates_then_appends(org_demo):
    log = org_demo / "events.jsonl"
    before = log.read_text()
    new = event("ev-fri-approve", "2026-10-09T09:00:00Z", "zhou", "approve")
    assert runner.invoke(app, ["append", str(org_demo), "--event", json.dumps(new)]).exit_code == 0
    assert log.read_text().startswith(before) and log.read_text().count("\n") == before.count("\n") + 1
    duplicate = runner.invoke(app, ["append", str(org_demo), "--event", json.dumps(new)])
    assert duplicate.exit_code == 2 and log.read_text().count("\n") == before.count("\n") + 1


def test_evidence_add_requires_verbatim_quote(org_demo):
    manifest = (org_demo / "evidence.jsonl").read_text()
    result = runner.invoke(app, ["evidence", "add", str(org_demo), "--id", "fri",
                                 "--path", "sources/2026-10-07-decision-log.txt", "--quote", "Feature A is approved",
                                 "--source-uri", "synthetic://x", "--source-kind", "note",
                                 "--captured-at", "2026-10-09T00:00:00Z"])
    assert result.exit_code == 2 and (org_demo / "evidence.jsonl").read_text() == manifest
