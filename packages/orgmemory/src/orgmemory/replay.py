"""Deterministic replay of an append-only event log under an authority policy.

The newest message is not automatically the current truth: only events whose
actor holds a permitted role change state, and a repeated request after a
decision is recorded as a reconsideration, not as a new decision.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from memcore import EvidenceError, EvidenceSpan, load_manifest, read_jsonl
from memcore.reading import read_file_text

from .models import DECISIONS, Conflict, Decision, Event, PendingChange, Policy, ReplayResult, Risk, SubjectState, TrailEntry


class Dataset:
    """events.jsonl + policy.json + evidence.jsonl, validated as a whole."""

    def __init__(self, events: list[Event], policy: Policy, evidence: dict[str, EvidenceSpan]):
        self.events, self.policy, self.evidence = events, policy, evidence
        validate(events, evidence)

    @classmethod
    def load(cls, directory: Path | None = None, events: Path | None = None, policy: Path | None = None,
             evidence: Path | None = None) -> "Dataset":
        directory = Path(directory) if directory else Path(".")
        events = events or directory / "events.jsonl"
        policy = policy or directory / "policy.json"
        evidence = evidence or directory / "evidence.jsonl"
        parsed = load_events(events)
        policy_model = Policy.model_validate(json.loads(read_file_text(policy)))
        return cls(parsed, policy_model, load_manifest(evidence))


def load_events(path: Path) -> list[Event]:
    """Parse and validate every line of an events JSONL file; errors name the line."""
    parsed = []
    for number, record in read_jsonl(path):
        try:
            parsed.append(Event.model_validate(record))
        except ValueError as exc:
            raise ValueError(f"{path}:{number}: {exc}") from exc
    return parsed


def validate(events: list[Event], evidence: dict[str, EvidenceSpan]) -> None:
    """Check the whole log, including events after any --as-of cutoff."""
    ids = set()
    for event in events:
        if event.id in ids:
            raise ValueError(f"duplicate event id: {event.id}")
        ids.add(event.id)
        missing = [i for i in event.evidence_ids if i not in evidence]
        if missing:
            raise EvidenceError(f"event {event.id} cites unknown evidence: {', '.join(missing)}")
    for event in events:
        if event.supersedes and event.supersedes not in ids:
            raise ValueError(f"event {event.id} supersedes unknown event {event.supersedes}")


_ACTIONS = ("request", "reconsider", "raise_risk", "dependency", "approve", "reject", "revoke", "comment")


def _decision(event: Event, roles: list[str], evidence: dict[str, EvidenceSpan]) -> Decision:
    # model_construct skips a second validation of spans the manifest already checked.
    return Decision.model_construct(event_id=event.id, actor=event.actor, roles=list(roles), action=event.action,
                                    timestamp=event.timestamp, evidence=[evidence[i] for i in event.evidence_ids])


def replay(dataset: Dataset, as_of: datetime | None = None) -> ReplayResult:
    policy, evidence = dataset.policy, dataset.evidence
    actors = set(policy.actors)
    actors.update(event.actor for event in dataset.events)
    allowed = {(actor, action): policy.allows(actor, action) for actor in actors for action in _ACTIONS}
    roles_of = {actor: policy.roles(actor) for actor in actors}
    decision_roles = policy.decision_roles
    dependencies: dict[str, set[str]] = {}
    # Effective time orders events; ties keep ingestion (file) order.
    ordered = sorted(enumerate(dataset.events), key=lambda pair: (pair[1].timestamp, pair[0]))
    states: dict[str, SubjectState] = {}
    trail: list[TrailEntry] = []
    for _, event in ordered:
        if as_of and event.timestamp > as_of:
            continue
        state = states.get(event.subject)
        if state is None:
            # setdefault would build a SubjectState for every event, then throw it away.
            state = states[event.subject] = SubjectState(subject=event.subject)
        before = state.current_status
        roles = roles_of[event.actor]
        outcome = "applied" if allowed[event.actor, event.action] else "unauthorized"
        if outcome == "unauthorized":
            if event.action in DECISIONS:
                state.pending_changes.append(PendingChange.model_construct(
                    event_id=event.id, actor=event.actor, action=event.action,
                    reason=f"actor roles {roles or 'none'} may not {event.action}"))
        elif event.action in ("approve", "reject"):
            last = state.last_authorized_decision
            status = "approved" if event.action == "approve" else "rejected"
            if last and last.action != "revoke" and last.action != event.action:
                kind = "simultaneous_decisions" if last.timestamp == event.timestamp else "decision_reversed"
                state.conflicts.append(Conflict.model_construct(
                    kind=kind, event_ids=[last.event_id, event.id],
                    detail=f"{last.action} by {last.actor} then {event.action} by {event.actor}"
                    + (" at the same effective time; ingestion order decided" if kind == "simultaneous_decisions" else "")))
            state.current_status = status
            state.last_authorized_decision = _decision(event, roles, evidence)
            state.decision_history.append(event.id)
            state.reconsideration_requested = False
            state.pending_approval = []
            state.pending_changes = []
        elif event.action == "revoke":
            last = state.last_authorized_decision
            if not last or last.event_id != event.supersedes:
                outcome = "invalid_supersession"
                state.conflicts.append(Conflict.model_construct(
                    kind="invalid_supersession", event_ids=[event.id],
                    detail=f"revoke targets {event.supersedes}, but the current decision is "
                    + (last.event_id if last else "none")))
            else:
                state.current_status = "proposed"
                state.last_authorized_decision = _decision(event, roles, evidence)
                state.decision_history.append(event.id)
                state.pending_approval = list(decision_roles)
        elif event.action in ("request", "reconsider"):
            decided = state.last_authorized_decision and state.last_authorized_decision.action != "revoke"
            if decided or event.action == "reconsider":
                state.reconsideration_requested = True
            state.pending_approval = list(decision_roles)
        elif event.action == "raise_risk":
            state.risks.append(Risk.model_construct(event_id=event.id, actor=event.actor, note=event.note))
        elif event.action == "dependency":
            dependencies.setdefault(event.subject, set()).add(event.dependency)
        if outcome == "applied":
            state.last_applied_event = event.id
        trail.append(TrailEntry.model_construct(
            event_id=event.id, timestamp=event.timestamp, actor=event.actor, roles=list(roles),
            action=event.action, subject=event.subject, outcome=outcome, status_before=before,
            status_after=state.current_status, evidence_ids=list(event.evidence_ids)))
    for subject, deps in dependencies.items():
        states[subject].dependencies = sorted(deps)
    return ReplayResult.model_construct(as_of=as_of, events_total=len(dataset.events), events_replayed=len(trail),
                                        states=states, audit_log=trail)
