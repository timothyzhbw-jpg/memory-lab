"""What changed in an organizational decision log between two times.

Everything here is derived from two runs of ``replay()`` on the same dataset, one at the start of the period
and one at its end, and from the audit trail between them. Like the audit, it adds no event semantics: a
change only restates what the two replays and the trail contain. The period holds the events after its start,
up to and including its end, the same cutoff ``replay --as-of`` applies. Subjects are sorted by name, and
each subject's changes by kind (in ``KINDS`` order), then by the replay position of their last cited event.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Literal, Optional, get_args

from pydantic import BaseModel

from .audit import CitedEvent, Period, _note, cited, periods
from .models import DECISIONS, ReplayResult, SubjectState, TrailEntry
from .replay import Dataset, replay

Kind = Literal["decision", "pending_approval_opened", "pending_approval_resolved", "reconsideration_opened",
               "reconsideration_resolved", "risk_raised", "dependency_added", "conflict", "unauthorized_attempt"]
KINDS: tuple[str, ...] = get_args(Kind)
Status = Literal["proposed", "approved", "rejected"]


class Change(BaseModel):
    kind: Kind
    detail: str
    status_before: Optional[Status] = None  # a decision only: the status it changed from ...
    status_after: Optional[Status] = None   # ... and to; equal when the decision confirmed the status
    event_ids: list[str]                    # in replay order
    events: list[CitedEvent]                # the same events, in the same order, with their evidence


class SubjectChanges(BaseModel):
    subject: str
    status_before: Optional[Status]         # None: the subject had no events up to from_time
    status_after: Status
    changes: list[Change]


class ChangesReport(BaseModel):
    tool: str = "orgmemory"
    mode: Literal["read_only"] = "read_only"
    from_time: datetime                     # the period starts after this time ...
    to_time: datetime                       # ... and ends at this time, inclusive
    events_total: int
    events_before: int                      # events replayed up to from_time
    events_in_period: int
    subjects_total: int                     # subjects with events up to to_time
    subjects_in_period: int                 # subjects with events in the period
    subjects_changed: int                   # subjects listed in `subjects`
    summary: dict[str, int]                 # changes per kind, every kind listed, in KINDS order
    subjects: list[SubjectChanges]


class _Differ:
    def __init__(self, dataset: Dataset, before: ReplayResult, after: ReplayResult):
        self.dataset, self.before, self.after = dataset, before, after
        self.events = {event.id: event for event in dataset.events}
        self.entries = {entry.event_id: entry for entry in after.audit_log}
        self.position = {entry.event_id: index for index, entry in enumerate(after.audit_log)}
        self.start = len(before.audit_log)  # the period's first entry in the later trail
        self.trails: dict[str, list[TrailEntry]] = defaultdict(list)
        for entry in after.audit_log:
            self.trails[entry.subject].append(entry)

    def in_period(self, entry: TrailEntry) -> bool:
        return self.position[entry.event_id] >= self.start

    def change(self, kind: str, detail: str, event_ids, **status) -> tuple[int, int, Change]:
        ids = sorted(set(event_ids), key=self.position.__getitem__)
        change = Change(kind=kind, detail=detail, event_ids=ids,
                        events=[cited(self.entries[i], self.dataset.evidence) for i in ids], **status)
        return KINDS.index(kind), self.position[ids[-1]], change

    def periods(self, kind: str, runs: list[Period], label: str) -> list[tuple[int, int, Change]]:
        """Open stretches that began in the period, and those an approve or reject in the period closed.

        An opened change cites every event that opened or kept the stretch open; a resolved one cites the
        event that opened it and the decision that closed it.
        """
        out = []
        for run in runs:
            first, closed = run.opened[0], run.closed
            since = f"{label} since {first.timestamp.isoformat()}"
            end = (f"closed by {closed.action} by {closed.actor} at {closed.timestamp.isoformat()}" if closed
                   else "still open at the end of the period")
            if self.in_period(first):
                out.append(self.change(f"{kind}_opened", f"{since}; {end}", [e.event_id for e in run.opened]))
            if closed is not None and self.in_period(closed):
                out.append(self.change(f"{kind}_resolved", f"{since}; {end}", [first.event_id, closed.event_id]))
        return out

    def subject(self, subject: str) -> Optional[SubjectChanges]:
        old: Optional[SubjectState] = self.before.states.get(subject)
        new = self.after.states[subject]
        trail = self.trails[subject]
        found = []
        for entry in trail:
            if not self.in_period(entry):
                continue
            if entry.outcome == "applied" and entry.action in DECISIONS:
                detail = f"{entry.status_before} → {entry.status_after}"
                if entry.status_before == entry.status_after:
                    detail += "; the status is unchanged, and this is now the authorizing decision"
                found.append(self.change("decision", detail, [entry.event_id],
                                         status_before=entry.status_before, status_after=entry.status_after))
            elif entry.outcome == "unauthorized" and entry.action != "comment":
                # A comment never changes state, so the policy ignoring one refuses nothing.
                who = f"roles {', '.join(entry.roles)}" if entry.roles else "an actor without a role"
                found.append(self.change("unauthorized_attempt", f"ignored: {who} may not {entry.action}",
                                         [entry.event_id]))
        pending, reconsider = periods(trail)
        roles = self.dataset.policy.decision_roles
        if roles:  # replay never opens a pending approval when no role may both approve and reject
            found += self.periods("pending_approval", pending, f"pending approval from {', '.join(roles)}")
        found += self.periods("reconsideration", reconsider, "reconsideration requested")
        for risk in new.risks:
            if self.in_period(self.entries[risk.event_id]):
                found.append(self.change("risk_raised", f"raised by {risk.actor}: {_note(risk.note)}",
                                         [risk.event_id]))
        for dependency in sorted(set(new.dependencies) - set(old.dependencies if old else [])):
            declared = [e.event_id for e in trail if self.in_period(e) and e.outcome == "applied"
                        and e.action == "dependency" and self.events[e.event_id].dependency == dependency]
            other = self.after.states.get(dependency)
            where = (f"which is {other.current_status} at the end of the period" if other is not None
                     else "which has no events in this log up to the end of the period")
            found.append(self.change("dependency_added", f"now depends on {dependency}, {where}", declared))
        for conflict in new.conflicts[len(old.conflicts) if old else 0:]:
            found.append(self.change("conflict", f"{conflict.kind}: {conflict.detail}", conflict.event_ids))
        if not found and old is not None:
            return None
        found.sort(key=lambda item: item[:2])
        return SubjectChanges(subject=subject, status_before=old.current_status if old else None,
                              status_after=new.current_status, changes=[change for _, _, change in found])


def changes(dataset: Dataset, start: datetime, end: datetime) -> ChangesReport:
    """Replay ``dataset`` at ``start`` and at ``end`` and report, per subject, what changed in between."""
    if start > end:
        raise ValueError("the period must not start after it ends")
    before, after = replay(dataset, start), replay(dataset, end)
    # replay() orders events the same way at every cutoff, so the earlier trail is a prefix of the later one.
    window = after.audit_log[len(before.audit_log):]
    differ = _Differ(dataset, before, after)
    subjects = [found for found in (differ.subject(s) for s in sorted(after.states)) if found is not None]
    every = [change.kind for found in subjects for change in found.changes]
    return ChangesReport(from_time=start, to_time=end, events_total=after.events_total,
                         events_before=before.events_replayed, events_in_period=len(window),
                         subjects_total=len(after.states), subjects_in_period=len({e.subject for e in window}),
                         subjects_changed=len(subjects), summary={kind: every.count(kind) for kind in KINDS},
                         subjects=subjects)
