# Frozen copy of packages/orgmemory/src/orgmemory/replay.py from integration-0.4 before replay-perf (06d8bd1).
# The differential tests compare the optimized library with it. Do not edit it to match the library.
"""Deterministic replay of an append-only event log under an authority policy.

The newest message is not automatically the current truth: only events whose
actor holds a permitted role change state, and a repeated request after a
decision is recorded as a reconsideration, not as a new decision. Roles are
the ones the actor held at the event's effective time (``authority.py``).
"""
from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from pathlib import Path

from memcore import EvidenceError, EvidenceSpan, load_manifest, read_jsonl
from memcore.jsonutil import loads_at
from memcore.reading import read_file_text

from .authority import POLICY_LOG, Authority
from .models import (DECISIONS, Conflict, Decision, Event, PendingChange, Policy, PolicyChange, ReplayResult, Risk,
                     SubjectState, TrailEntry)


class Dataset:
    """events.jsonl + policy.json + evidence.jsonl, and the optional policy-log.jsonl, validated as a whole.

    ``policy_log`` is None when the dataset has no policy-log.jsonl.
    """

    def __init__(self, events: list[Event], policy: Policy, evidence: dict[str, EvidenceSpan],
                 policy_log: list[PolicyChange] | None = None) -> None:
        self.events, self.policy, self.evidence, self.policy_log = events, policy, evidence, policy_log
        validate(events, evidence, policy_log or ())
        self.authority = Authority(policy, policy_log)

    @classmethod
    def load(cls, directory: Path | None = None, events: Path | None = None, policy: Path | None = None,
             evidence: Path | None = None, policy_log: Path | None = None) -> "Dataset":
        directory = Path(directory) if directory else Path(".")
        events = events or directory / "events.jsonl"
        policy = policy or directory / "policy.json"
        evidence = evidence or directory / "evidence.jsonl"
        policy_log = policy_log or directory / POLICY_LOG
        parsed = load_events(events)
        try:
            raw_policy = read_file_text(policy)
        except UnicodeDecodeError as exc:
            raise ValueError(f"{policy}: not UTF-8 text") from exc
        parsed_policy = loads_at(policy, raw_policy)
        try:
            policy_model = Policy.model_validate(parsed_policy)
        except RecursionError as exc:
            raise ValueError(f"{policy}: invalid JSON: too deeply nested") from exc
        # A dangling symlink is not "no log": reading it fails closed.
        changes = load_policy_log(policy_log) if policy_log.exists() or policy_log.is_symlink() else None
        return cls(parsed, policy_model, load_manifest(evidence), changes)


def _load_lines(path: Path, model) -> list:
    parsed = []
    for number, record in read_jsonl(path):
        try:
            parsed.append(model.model_validate(record))
        except RecursionError as exc:
            raise ValueError(f"{path}:{number}: invalid JSON: too deeply nested") from exc
        except ValueError as exc:
            raise ValueError(f"{path}:{number}: {exc}") from exc
    return parsed


def load_events(path: Path) -> list[Event]:
    """Parse and validate every line of an events JSONL file; errors name the line."""
    return _load_lines(path, Event)


def load_policy_log(path: Path) -> list[PolicyChange]:
    """Parse and validate every line of a policy-log.jsonl file; errors name the line."""
    return _load_lines(path, PolicyChange)


def validate(events: list[Event], evidence: dict[str, EvidenceSpan], policy_log: Iterable[PolicyChange] = ()) -> None:
    """Check the whole log, including events after any --as-of cutoff.

    Policy-log entries need unique IDs, distinct from event IDs, and known evidence. ``gh:`` IDs are reserved
    for the GitHub importer, which never reads the policy log, so its events cannot collide with an entry.
    Whether each entry is authorized and consistent is checked by ``Authority``, which also needs the policy.
    """
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
    changes = set()
    for change in policy_log:
        if change.id in changes:
            raise ValueError(f"duplicate policy-log id: {change.id}")
        if change.id in ids:
            raise ValueError(f"policy-log id {change.id} is also an event id")
        if change.id.startswith("gh:"):
            raise ValueError(f"policy-log id {change.id}: the gh: prefix is reserved for GitHub events")
        changes.add(change.id)
        missing = [i for i in change.evidence_ids if i not in evidence]
        if missing:
            raise EvidenceError(f"policy-log entry {change.id} cites unknown evidence: {', '.join(missing)}")


def validation_summary(data: Dataset) -> list[str]:
    """What `orgmemory validate` (and the MCP tool) prints for a valid dataset."""
    authority = data.authority
    lines = [f"ok: {len(data.events)} events, {len(data.evidence)} evidence records"]
    if authority.dated:
        start = f"as of {authority.baseline.isoformat()}" if authority.baseline else "from the beginning of the log"
        lines.append(f"roles: policy.json {start}, then {len(authority.applied)} role change(s) in {POLICY_LOG}")
    unknown = sorted({e.actor for e in data.events} - data.policy.actors.keys() - authority.granted())
    if unknown:
        lines.append(f"note: actors without any role (their events cannot change state): {', '.join(unknown)}")
    early = sum(authority.before_baseline(e.timestamp) for e in data.events)
    if early:
        lines.append(f"note: {early} event(s) before the policy baseline {authority.baseline.isoformat()}, "
                     "when no actor held a role")
    return lines


def _decision(event: Event, roles: list[str], evidence: dict[str, EvidenceSpan]) -> Decision:
    return Decision(event_id=event.id, actor=event.actor, roles=roles, action=event.action,
                    timestamp=event.timestamp, evidence=[evidence[i] for i in event.evidence_ids])


def _refusal(authority: Authority, event: Event, roles: list[str]) -> str:
    if authority.before_baseline(event.timestamp):
        return (f"before the policy baseline {authority.baseline.isoformat()} no actor holds a role, "
                f"so {event.actor} may not {event.action}")
    return f"actor roles {roles or 'none'} may not {event.action}"


def replay(dataset: Dataset, as_of: datetime | None = None) -> ReplayResult:
    policy, evidence, authority = dataset.policy, dataset.evidence, dataset.authority
    # Effective time orders events; ties keep ingestion (file) order. Each event is judged by the roles its
    # actor held at its effective time, so a later grant or revoke_role never changes an earlier outcome.
    ordered = sorted(enumerate(dataset.events), key=lambda pair: (pair[1].timestamp, pair[0]))
    states: dict[str, SubjectState] = {}
    trail: list[TrailEntry] = []
    for _, event in ordered:
        if as_of and event.timestamp > as_of:
            continue
        state = states.setdefault(event.subject, SubjectState(subject=event.subject))
        before = state.current_status
        roles = authority.roles(event.actor, event.timestamp)
        outcome = "applied" if set(roles) & set(policy.permitted(event.action)) else "unauthorized"
        if outcome == "unauthorized":
            if event.action in DECISIONS:
                state.pending_changes.append(PendingChange(
                    event_id=event.id, actor=event.actor, action=event.action,
                    reason=_refusal(authority, event, roles)))
        elif event.action in ("approve", "reject"):
            last = state.last_authorized_decision
            status = "approved" if event.action == "approve" else "rejected"
            if last and last.action != "revoke" and last.action != event.action:
                kind = "simultaneous_decisions" if last.timestamp == event.timestamp else "decision_reversed"
                state.conflicts.append(Conflict(kind=kind, event_ids=[last.event_id, event.id],
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
                state.conflicts.append(Conflict(kind="invalid_supersession", event_ids=[event.id],
                                                detail=f"revoke targets {event.supersedes}, but the current decision is "
                                                + (last.event_id if last else "none")))
            else:
                state.current_status = "proposed"
                state.last_authorized_decision = _decision(event, roles, evidence)
                state.decision_history.append(event.id)
                state.pending_approval = policy.decision_roles
        elif event.action in ("request", "reconsider"):
            decided = state.last_authorized_decision and state.last_authorized_decision.action != "revoke"
            if decided or event.action == "reconsider":
                state.reconsideration_requested = True
            state.pending_approval = policy.decision_roles
        elif event.action == "raise_risk":
            state.risks.append(Risk(event_id=event.id, actor=event.actor, note=event.note))
        elif event.action == "dependency":
            state.dependencies = sorted(set(state.dependencies) | {event.dependency})
        if outcome == "applied":
            state.last_applied_event = event.id
        trail.append(TrailEntry(event_id=event.id, timestamp=event.timestamp, actor=event.actor,
                                roles=roles, action=event.action, subject=event.subject,
                                outcome=outcome, status_before=before, status_after=state.current_status,
                                evidence_ids=event.evidence_ids))
    return ReplayResult(as_of=as_of, events_total=len(dataset.events), events_replayed=len(trail),
                        states=states, audit_log=trail, dated_policy=authority.summary(evidence, as_of))
