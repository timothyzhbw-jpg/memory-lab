# Frozen copy of packages/orgmemory/src/orgmemory/audit.py from integration-0.4 before replay-perf (06d8bd1).
# The differential tests compare the optimized library with it. Do not edit it to match the library.
"""Decision audit: where an organization's decision process needs attention.

Everything here is derived from ``replay()``: the replayed state of each subject, the conflicts replay
records, its audit trail and the role changes it applied. The audit adds no event semantics and reads
nothing but the dataset, so a finding only restates what the log and the policy contain. Severities follow
fixed rules (README.md, "Audit the decision process"), and findings are sorted by severity, kind, subject,
actor and replay order.
"""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from itertools import groupby
from typing import Literal, Optional, get_args

from pydantic import BaseModel

from memcore import EvidenceSpan

from .models import DECISIONS, Conflict, DatedPolicy, ReplayResult, RoleChange, SubjectState, TrailEntry, omit_empty
from .replay import Dataset, replay

Severity = Literal["high", "medium", "low"]
Kind = Literal["decision_reversed", "invalid_supersession", "risk_after_approval", "role_changed_after_decision",
               "self_approval", "simultaneous_decisions", "stale_pending_approval", "stale_reconsideration",
               "unauthorized_attempts", "unresolved_dependency"]
KINDS: tuple[str, ...] = tuple(sorted(get_args(Kind)))
# Kinds only a dataset with dated roles (policy-log.jsonl or a policy.json as_of) can have. Other datasets
# do not list them, so their reports stay what they were before dated roles existed.
DATED_KINDS: tuple[str, ...] = ("role_changed_after_decision",)
SEVERITIES: tuple[str, ...] = get_args(Severity)
DEFAULT_STALE_DAYS = 14


def report_kinds(dated: bool) -> tuple[str, ...]:
    """The kinds a report lists, sorted by name."""
    return KINDS if dated else tuple(k for k in KINDS if k not in DATED_KINDS)


class CitedEvent(BaseModel):
    """One event a finding relies on, with the evidence that event cites."""
    event_id: str
    timestamp: datetime
    subject: str
    actor: str
    roles: list[str]
    action: str
    outcome: str
    evidence: list[EvidenceSpan]


def cited(entry: TrailEntry, evidence: dict[str, EvidenceSpan]) -> CitedEvent:
    """A replayed event with the evidence it cites."""
    return CitedEvent(event_id=entry.event_id, timestamp=entry.timestamp, subject=entry.subject,
                      actor=entry.actor, roles=entry.roles, action=entry.action, outcome=entry.outcome,
                      evidence=[evidence[i] for i in entry.evidence_ids])


class AuditFinding(BaseModel):
    kind: Kind
    severity: Severity
    subject: Optional[str] = None  # None only for a finding about one actor across subjects
    actor: Optional[str] = None
    subjects: list[str]            # the finding's subject and the subjects of its cited events, sorted
    event_ids: list[str]           # in replay order
    detail: str
    events: list[CitedEvent]       # the same events, in the same order, with their evidence
    role_changes: list[RoleChange] = []  # policy-log entries the finding relies on; omitted when none

    _serialize = omit_empty(("role_changes",))


class AuditReport(BaseModel):
    tool: str = "orgmemory"
    mode: Literal["read_only"] = "read_only"
    as_of: Optional[datetime]
    reference_time: datetime       # staleness is measured here: as_of, else the time of the run
    stale_days: int
    events_total: int
    events_replayed: int
    subjects_total: int
    summary: dict[str, int]        # findings per severity: high, medium, low
    kinds: dict[str, int]          # findings per kind, every kind the report lists (report_kinds), by name
    findings: list[AuditFinding]
    dated_policy: Optional[DatedPolicy] = None  # as in ReplayResult; omitted when roles are not dated

    _serialize = omit_empty(("dated_policy",))


def _duration(delta: timedelta) -> str:
    return f"{delta.days}d {delta.seconds // 3600}h"


def _note(text: str) -> str:
    """Quote free-text risk notes in the detail; the Markdown renderer also escapes their markup."""
    return json.dumps(text, ensure_ascii=False)


@dataclass
class Period:
    """One stretch of time during which a subject's pending approval, or its reconsideration, was open."""
    opened: list[TrailEntry]               # the applied events that opened it and kept it open, in replay order
    closed: Optional[TrailEntry] = None    # the applied approve or reject that closed it; None while open


def periods(entries: list[TrailEntry]) -> tuple[list[Period], list[Period]]:
    """Every pending-approval period and every reconsideration period of one subject's trail, in replay order.

    This mirrors replay() without changing it: an applied approve or reject closes both; an applied
    request, reconsider or revoke opens the pending approval; a reconsider, or a request while an approve
    or reject is in force, opens the reconsideration. Unauthorized events and invalid revokes change nothing.
    """
    pending: list[Period] = []
    reconsider: list[Period] = []

    def keep_open(runs: list[Period], entry: TrailEntry) -> None:
        if runs and runs[-1].closed is None:
            runs[-1].opened.append(entry)
        else:
            runs.append(Period([entry]))

    decided = False
    for entry in entries:
        if entry.outcome != "applied":
            continue
        if entry.action in ("approve", "reject"):
            for runs in (pending, reconsider):
                if runs and runs[-1].closed is None:
                    runs[-1].closed = entry
            decided = True
        elif entry.action == "revoke":
            keep_open(pending, entry)
            decided = False
        elif entry.action in ("request", "reconsider"):
            keep_open(pending, entry)
            if decided or entry.action == "reconsider":
                keep_open(reconsider, entry)
    return pending, reconsider


def open_periods(entries: list[TrailEntry]) -> tuple[list[TrailEntry], list[TrailEntry]]:
    """The applied events that opened, and kept open, a subject's final pending approval and reconsideration."""
    def still_open(runs: list[Period]) -> list[TrailEntry]:
        return list(runs[-1].opened) if runs and runs[-1].closed is None else []

    pending, reconsider = periods(entries)
    return still_open(pending), still_open(reconsider)


class _Auditor:
    def __init__(self, dataset: Dataset, result: ReplayResult, reference: datetime, stale_days: int):
        self.dataset, self.result, self.reference, self.stale_days = dataset, result, reference, stale_days
        self.events = {event.id: event for event in dataset.events}
        self.entries = {entry.event_id: entry for entry in result.audit_log}
        self.position = {entry.event_id: index for index, entry in enumerate(result.audit_log)}
        self.trails: dict[str, list[TrailEntry]] = defaultdict(list)
        self.standing: dict[str, Optional[str]] = {}  # invalid revoke -> the decision in force at that point
        current: dict[str, str] = {}
        for entry in result.audit_log:
            self.trails[entry.subject].append(entry)
            if entry.outcome == "invalid_supersession":
                self.standing[entry.event_id] = current.get(entry.subject)
            if entry.outcome == "applied" and entry.action in DECISIONS:
                current[entry.subject] = entry.event_id
        self.findings: list[AuditFinding] = []

    def cite(self, event_id: str) -> CitedEvent:
        return cited(self.entries[event_id], self.dataset.evidence)

    def add(self, kind: str, severity: str, detail: str, event_ids, subject: Optional[str] = None,
            actor: Optional[str] = None, role_changes: tuple[RoleChange, ...] = ()) -> None:
        # Only replayed events can be cited; an event after --as-of is not part of this audit.
        ids = sorted({i for i in event_ids if i in self.position}, key=self.position.__getitem__)
        cited = [self.cite(i) for i in ids]
        subjects = sorted({c.subject for c in cited} | ({subject} if subject is not None else set()))
        self.findings.append(AuditFinding(kind=kind, severity=severity, subject=subject, actor=actor,
                                          subjects=subjects, event_ids=ids, detail=detail, events=cited,
                                          role_changes=list(role_changes)))

    # --- conflicts replay already detected ----------------------------------------------------------

    def conflict(self, state: SubjectState, conflict: Conflict) -> None:
        ids = list(conflict.event_ids)
        current = state.last_authorized_decision.event_id if state.last_authorized_decision else None
        if conflict.kind == "simultaneous_decisions":
            if current == ids[-1]:
                severity, note = "high", "; the order-decided decision is still current"
            else:
                severity, note = "medium", f"; a later decision ({current}) has replaced it"
        elif conflict.kind == "decision_reversed":
            same = self.entries[ids[0]].actor == self.entries[ids[-1]].actor
            severity = "low" if same else "medium"
            note = "; the same actor changed the decision" if same else "; a different actor overrode the decision"
        else:  # invalid_supersession: also cite the event it named and the decision in force at the time
            severity, note = "medium", "; the revoke changed nothing"
            ids += [self.events[ids[0]].supersedes, self.standing.get(ids[0])]
        self.add(conflict.kind, severity, conflict.detail + note, [i for i in ids if i], subject=state.subject)

    # --- approved subjects --------------------------------------------------------------------------

    def approved(self, state: SubjectState) -> None:
        approval = state.last_authorized_decision
        trail = self.trails[state.subject]
        for risk in state.risks:
            if self.position[risk.event_id] > self.position[approval.event_id]:
                severity = "high"
                detail = f"risk raised by {risk.actor} after the approval by {approval.actor}: {_note(risk.note)}"
            else:
                severity = "medium"
                detail = (f"approved by {approval.actor} while a risk raised by {risk.actor} was on record: "
                          f"{_note(risk.note)}; the log has no event that resolves a risk")
            self.add("risk_after_approval", severity, detail, [approval.event_id, risk.event_id],
                     subject=state.subject)
        for dependency in state.dependencies:
            other = self.result.states.get(dependency)
            if other is not None and other.current_status == "approved":
                continue
            declared = [e.event_id for e in trail if e.outcome == "applied" and e.action == "dependency"
                        and self.events[e.event_id].dependency == dependency]
            ids = [approval.event_id, *declared]
            if other is None:
                severity, why = "low", "which has no events in this log"
            else:
                severity = "high" if other.current_status == "rejected" else "medium"
                why = f"which is {other.current_status}"
                if other.last_authorized_decision:
                    ids.append(other.last_authorized_decision.event_id)
            self.add("unresolved_dependency", severity, f"approved, but depends on {dependency}, {why}", ids,
                     subject=state.subject)
        own = [e.event_id for e in trail if e.outcome == "applied" and e.action in ("request", "reconsider")
               and e.actor == approval.actor and self.position[e.event_id] < self.position[approval.event_id]]
        if own:
            self.add("self_approval", "low", f"approved by {approval.actor}, who had also requested the subject",
                     [*own, approval.event_id],
                     subject=state.subject, actor=approval.actor)

    # --- requests still open ------------------------------------------------------------------------

    def staleness(self, state: SubjectState) -> None:
        pending, reconsider = open_periods(self.trails[state.subject])
        if state.reconsideration_requested and reconsider:
            kind, opened, what = "stale_reconsideration", reconsider, "reconsideration awaiting"
        elif state.pending_approval and pending:
            kind, opened, what = "stale_pending_approval", pending, "awaiting"
        else:
            return
        since = opened[0].timestamp
        age = self.reference - since
        if age.total_seconds() <= self.stale_days * 86400:
            return
        detail = (f"{what} a decision from {', '.join(state.pending_approval) or 'no role'} since "
                  f"{since.isoformat()} (status {state.current_status}): {_duration(age)} at "
                  f"{self.reference.isoformat()}, more than {self.stale_days} days")
        decision = [state.last_authorized_decision.event_id] if state.last_authorized_decision else []
        self.add(kind, "medium", detail, [*decision, *(e.event_id for e in opened)], subject=state.subject)

    # --- actors -------------------------------------------------------------------------------------

    def attempts(self) -> None:
        by_actor: dict[str, list[TrailEntry]] = defaultdict(list)
        for entry in self.result.audit_log:
            # A comment never changes state, so the policy ignoring one refuses nothing.
            if entry.outcome == "unauthorized" and entry.action != "comment":
                by_actor[entry.actor].append(entry)
        for actor, entries in by_actor.items():
            decisions = sum(e.action in DECISIONS for e in entries)
            severity = "high" if decisions >= 2 else "medium" if decisions == 1 or len(entries) >= 2 else "low"
            counts = Counter(e.action for e in entries)
            subjects = sorted({e.subject for e in entries})
            # Roles are those held at each attempt; with dated roles they can differ between attempts.
            held = [roles for roles, _ in groupby(", ".join(e.roles) or "none" for e in entries)]
            detail = (f"{len(entries)} attempt(s) the policy ignored ("
                      + ", ".join(f"{action} ×{counts[action]}" for action in sorted(counts))
                      + f") on {len(subjects)} subject(s): {', '.join(subjects)}; "
                      + (f"roles: {held[0]}" if len(held) == 1 else f"roles at the time: {', then '.join(held)}"))
            self.add("unauthorized_attempts", severity, detail, [e.event_id for e in entries], actor=actor)

    # --- dated roles --------------------------------------------------------------------------------

    def role_changed(self, state: SubjectState) -> None:
        """The current decision's maker later lost a role that authorized it. The decision stands."""
        decision = state.last_authorized_decision
        if decision is None or self.result.dated_policy is None:
            return
        authorizing = [r for r in decision.roles if r in self.dataset.policy.permitted(decision.action)]
        lost = [c for c in self.result.dated_policy.role_changes
                if c.action == "revoke_role" and c.subject_actor == decision.actor and c.role in authorizing
                and c.timestamp > decision.timestamp]
        if not lost:
            return
        end = f"as of {self.result.as_of.isoformat()}" if self.result.as_of else "at the end of the log"
        now = self.dataset.authority.roles(decision.actor, self.result.as_of)
        detail = (f"{decision.action} {decision.event_id} by {decision.actor} (roles: {', '.join(decision.roles)}) "
                  f"is the current decision; {decision.actor} later lost "
                  + ", ".join(f"{c.role} ({c.change_id} at {c.timestamp.isoformat()})" for c in lost)
                  + f"; roles {end}: {', '.join(now) or 'none'}. The decision stands: it is judged by the roles "
                  "held when it was made")
        self.add("role_changed_after_decision", "low", detail, [decision.event_id], subject=state.subject,
                 actor=decision.actor, role_changes=tuple(lost))

    def run(self) -> list[AuditFinding]:
        for state in self.result.states.values():
            for conflict in state.conflicts:
                self.conflict(state, conflict)
            if state.current_status == "approved":
                self.approved(state)
            self.staleness(state)
            self.role_changed(state)
        self.attempts()
        rank = {severity: index for index, severity in enumerate(SEVERITIES)}
        return sorted(self.findings, key=lambda f: (rank[f.severity], f.kind, f.subject or "", f.actor or "",
                                                    [self.position[i] for i in f.event_ids]))


def audit(dataset: Dataset, as_of: Optional[datetime] = None, stale_days: int = DEFAULT_STALE_DAYS,
          now: Optional[datetime] = None) -> AuditReport:
    """Replay ``dataset`` up to ``as_of`` and report where the decision process needs attention.

    Staleness is measured at ``as_of``; without it, at ``now`` (default: the current time, whole seconds).
    """
    if stale_days < 0:
        raise ValueError("stale_days must not be negative")
    result = replay(dataset, as_of)
    reference = as_of or now or datetime.now(timezone.utc).replace(microsecond=0)
    findings = _Auditor(dataset, result, reference, stale_days).run()
    return AuditReport(as_of=as_of, reference_time=reference, stale_days=stale_days,
                       events_total=result.events_total, events_replayed=result.events_replayed,
                       subjects_total=len(result.states),
                       summary={s: sum(f.severity == s for f in findings) for s in SEVERITIES},
                       kinds={k: sum(f.kind == k for f in findings)
                              for k in report_kinds(result.dated_policy is not None)},
                       findings=findings, dated_policy=result.dated_policy)
