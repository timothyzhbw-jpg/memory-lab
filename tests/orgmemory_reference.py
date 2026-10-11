"""Original replay, audit and report algorithms, kept so scale optimizations can be diffed.

These functions are the pre-optimization behavior. Tests compare them to the
library on seeded datasets. Do not "fix" them to match a faster implementation.
"""
from __future__ import annotations

import json
import re
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from html import escape
from typing import Callable, Optional

from memcore import EvidenceSpan

from orgmemory.audit import KINDS, SEVERITIES, AuditFinding, AuditReport, CitedEvent
from orgmemory.models import DECISIONS, Conflict, Decision, PendingChange, ReplayResult, Risk, SubjectState, TrailEntry
from orgmemory.replay import Dataset


def _decision(event, policy, evidence):
    return Decision(event_id=event.id, actor=event.actor, roles=policy.roles(event.actor), action=event.action,
                    timestamp=event.timestamp, evidence=[evidence[i] for i in event.evidence_ids])


def reference_replay(dataset: Dataset, as_of: datetime | None = None) -> ReplayResult:
    policy, evidence = dataset.policy, dataset.evidence
    ordered = sorted(enumerate(dataset.events), key=lambda pair: (pair[1].timestamp, pair[0]))
    states: dict[str, SubjectState] = {}
    trail: list[TrailEntry] = []
    for _, event in ordered:
        if as_of and event.timestamp > as_of:
            continue
        state = states.setdefault(event.subject, SubjectState(subject=event.subject))
        before = state.current_status
        outcome = "applied" if policy.allows(event.actor, event.action) else "unauthorized"
        if outcome == "unauthorized":
            if event.action in DECISIONS:
                state.pending_changes.append(PendingChange(
                    event_id=event.id, actor=event.actor, action=event.action,
                    reason=f"actor roles {policy.roles(event.actor) or 'none'} may not {event.action}"))
        elif event.action in ("approve", "reject"):
            last = state.last_authorized_decision
            status = "approved" if event.action == "approve" else "rejected"
            if last and last.action != "revoke" and last.action != event.action:
                kind = "simultaneous_decisions" if last.timestamp == event.timestamp else "decision_reversed"
                state.conflicts.append(Conflict(kind=kind, event_ids=[last.event_id, event.id],
                                                detail=f"{last.action} by {last.actor} then {event.action} by {event.actor}"
                                                + (" at the same effective time; ingestion order decided" if kind == "simultaneous_decisions" else "")))
            state.current_status = status
            state.last_authorized_decision = _decision(event, policy, evidence)
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
                state.last_authorized_decision = _decision(event, policy, evidence)
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
                                roles=policy.roles(event.actor), action=event.action, subject=event.subject,
                                outcome=outcome, status_before=before, status_after=state.current_status,
                                evidence_ids=event.evidence_ids))
    return ReplayResult(as_of=as_of, events_total=len(dataset.events), events_replayed=len(trail),
                        states=states, audit_log=trail)


def _duration(delta: timedelta) -> str:
    return f"{delta.days}d {delta.seconds // 3600}h"


def _note(text: str) -> str:
    return json.dumps(text, ensure_ascii=False)


def reference_open_periods(entries: list[TrailEntry]) -> tuple[list[TrailEntry], list[TrailEntry]]:
    pending: list[TrailEntry] = []
    reconsider: list[TrailEntry] = []
    decided = False
    for entry in entries:
        if entry.outcome != "applied":
            continue
        if entry.action in ("approve", "reject"):
            pending, reconsider, decided = [], [], True
        elif entry.action == "revoke":
            pending.append(entry)
            decided = False
        elif entry.action in ("request", "reconsider"):
            pending.append(entry)
            if decided or entry.action == "reconsider":
                reconsider.append(entry)
    return pending, reconsider


class _ReferenceAuditor:
    def __init__(self, dataset: Dataset, result: ReplayResult, reference: datetime, stale_days: int):
        self.dataset, self.result, self.reference, self.stale_days = dataset, result, reference, stale_days
        self.events = {event.id: event for event in dataset.events}
        self.entries = {entry.event_id: entry for entry in result.audit_log}
        self.position = {entry.event_id: index for index, entry in enumerate(result.audit_log)}
        self.trails: dict[str, list[TrailEntry]] = defaultdict(list)
        self.standing: dict[str, Optional[str]] = {}
        current: dict[str, str] = {}
        for entry in result.audit_log:
            self.trails[entry.subject].append(entry)
            if entry.outcome == "invalid_supersession":
                self.standing[entry.event_id] = current.get(entry.subject)
            if entry.outcome == "applied" and entry.action in DECISIONS:
                current[entry.subject] = entry.event_id
        self.findings: list[AuditFinding] = []

    def cite(self, event_id: str) -> CitedEvent:
        entry = self.entries[event_id]
        return CitedEvent(event_id=entry.event_id, timestamp=entry.timestamp, subject=entry.subject,
                          actor=entry.actor, roles=entry.roles, action=entry.action, outcome=entry.outcome,
                          evidence=[self.dataset.evidence[i] for i in entry.evidence_ids])

    def add(self, kind: str, severity: str, detail: str, event_ids, subject: Optional[str] = None,
            actor: Optional[str] = None) -> None:
        ids = sorted({i for i in event_ids if i in self.position}, key=self.position.__getitem__)
        cited = [self.cite(i) for i in ids]
        subjects = sorted({c.subject for c in cited} | ({subject} if subject is not None else set()))
        self.findings.append(AuditFinding(kind=kind, severity=severity, subject=subject, actor=actor,
                                          subjects=subjects, event_ids=ids, detail=detail, events=cited))

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
        else:
            severity, note = "medium", "; the revoke changed nothing"
            ids += [self.events[ids[0]].supersedes, self.standing.get(ids[0])]
        self.add(conflict.kind, severity, conflict.detail + note, [i for i in ids if i], subject=state.subject)

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
            self.add("self_approval", "low", f"{approval.actor} approved a subject they had also requested",
                     [*own, approval.event_id],
                     subject=state.subject, actor=approval.actor)

    def staleness(self, state: SubjectState) -> None:
        pending, reconsider = reference_open_periods(self.trails[state.subject])
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

    def attempts(self) -> None:
        by_actor: dict[str, list[TrailEntry]] = defaultdict(list)
        for entry in self.result.audit_log:
            if entry.outcome == "unauthorized" and entry.action != "comment":
                by_actor[entry.actor].append(entry)
        for actor, entries in by_actor.items():
            decisions = sum(e.action in DECISIONS for e in entries)
            severity = "high" if decisions >= 2 else "medium" if decisions == 1 or len(entries) >= 2 else "low"
            counts = Counter(e.action for e in entries)
            subjects = sorted({e.subject for e in entries})
            detail = (f"{len(entries)} attempt(s) the policy ignored ("
                      + ", ".join(f"{action} ×{counts[action]}" for action in sorted(counts))
                      + f") on {len(subjects)} subject(s): {', '.join(subjects)}; "
                      f"roles: {', '.join(entries[0].roles) or 'none'}")
            self.add("unauthorized_attempts", severity, detail, [e.event_id for e in entries], actor=actor)

    def run(self) -> list[AuditFinding]:
        for state in self.result.states.values():
            for conflict in state.conflicts:
                self.conflict(state, conflict)
            if state.current_status == "approved":
                self.approved(state)
            self.staleness(state)
        self.attempts()
        rank = {severity: index for index, severity in enumerate(SEVERITIES)}
        return sorted(self.findings, key=lambda f: (rank[f.severity], f.kind, f.subject or "", f.actor or "",
                                                    [self.position[i] for i in f.event_ids]))


def reference_audit(dataset: Dataset, as_of: Optional[datetime] = None, stale_days: int = 14,
                    now: Optional[datetime] = None) -> AuditReport:
    if stale_days < 0:
        raise ValueError("stale_days must not be negative")
    result = reference_replay(dataset, as_of)
    reference = as_of or now or datetime.now(timezone.utc).replace(microsecond=0)
    findings = _ReferenceAuditor(dataset, result, reference, stale_days).run()
    return AuditReport(as_of=as_of, reference_time=reference, stale_days=stale_days,
                       events_total=result.events_total, events_replayed=result.events_replayed,
                       subjects_total=len(result.states),
                       summary={s: sum(f.severity == s for f in findings) for s in SEVERITIES},
                       kinds={k: sum(f.kind == k for f in findings) for k in KINDS},
                       findings=findings)


def _yaml_value(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return "null"
    if isinstance(value, list):
        return "[" + ", ".join(_yaml_value(v) for v in value) + "]"
    return json.dumps(value, ensure_ascii=False) if any(c in str(value) for c in ":#[]{},'\"") else str(value)


def _cite(span: EvidenceSpan, indent: str = "  ", clean: Callable[[str], str] = str,
          code: Callable[[str], str] | None = None) -> list[str]:
    code = code or (lambda value: f"`{clean(value)}`")
    return [f"{indent}- evidence {code(str(span.id))} · {code(span.location)} · {clean(str(span.source_uri))} "
            f"· sha256 `{span.sha256[:12]}`",
            f"{indent}  > {clean(span.quote)}"]


def _state_block(state: SubjectState) -> list[str]:
    decision = state.last_authorized_decision
    fields = {
        "subject": state.subject,
        "current_status": state.current_status,
        "reconsideration_requested": state.reconsideration_requested,
        "pending_approval": state.pending_approval,
        "dependencies": state.dependencies,
        "open_risks": [r.note for r in state.risks],
        "last_authorized_decision": f"{decision.event_id} ({decision.action} by {decision.actor})" if decision else None,
        "unauthorized_change_attempts": [c.event_id for c in state.pending_changes],
    }
    return ["```yaml"] + [f"{k}: {_yaml_value(v)}" for k, v in fields.items()] + ["```"]


def reference_explain(result: ReplayResult, subject: str) -> str:
    state = result.states[subject]
    out = [f"## {subject}", ""] + _state_block(state) + [""]
    decision = state.last_authorized_decision
    if decision:
        out += ["**Why this is the current status**", "",
                f"- `{decision.event_id}`: {decision.actor} (roles: {', '.join(decision.roles)}) "
                f"chose **{decision.action}** at {decision.timestamp.isoformat()}"]
        for span in decision.evidence:
            out += _cite(span)
        out.append("")
    else:
        out += ["No authorized decision yet.", ""]
    if state.pending_changes:
        out += ["**Ignored: actor lacked authority**", ""]
        out += [f"- `{c.event_id}`: {c.actor} tried to {c.action}; {c.reason}" for c in state.pending_changes]
        out.append("")
    if state.conflicts:
        out += ["**Conflicts**", ""] + [f"- {c.kind} {c.event_ids}: {c.detail}" for c in state.conflicts] + [""]
    rows = [e for e in result.audit_log if e.subject == subject]
    out += ["**Event history**", "", "| Time | Event | Actor | Action | Outcome | Status |", "|---|---|---|---|---|---|"]
    out += [f"| {e.timestamp.isoformat()} | `{e.event_id}` | {e.actor} ({', '.join(e.roles) or 'no role'}) | "
            f"{e.action} | {e.outcome} | {e.status_before} → {e.status_after} |" for e in rows]
    return "\n".join(out) + "\n"


def reference_to_markdown(result: ReplayResult) -> str:
    cutoff = result.as_of.isoformat() if result.as_of else "end of log"
    out = ["# Organizational memory replay", "",
           f"- Replayed {result.events_replayed} of {result.events_total} events, as of {cutoff}",
           f"- Mode: **{result.mode}** (the event log was not modified)", ""]
    for subject in result.states:
        out.append(reference_explain(result, subject))
    return "\n".join(out)


def reference_one_line(text: str) -> str:
    return "".join(c if unicodedata.category(c) not in ("Cc", "Cf", "Zl", "Zp")
                   else f"\\u{ord(c):04x}" if ord(c) <= 0xFFFF else f"\\U{ord(c):08x}" for c in text)


def reference_safe(text: str) -> str:
    text = escape(reference_one_line(text), quote=False)
    text = re.sub(r"([\\`*_{}\[\]#|~])", r"\\\1", text)
    text = re.sub(r"^(\s*)([-+=])", r"\1\\\2", text)
    return re.sub(r"^(\s*\d+)([.)])(?=\s)", r"\1\\\2", text)


def reference_audit_code(text: str) -> str:
    text = reference_one_line(text)
    fence = "`" * (1 + max((len(run) for run in re.findall(r"`+", text)), default=0))
    if "`" in text or (text.startswith(" ") and text.endswith(" ") and text.strip()):
        return f"{fence} {text} {fence}"
    return f"{fence}{text}{fence}"


def _reference_finding(finding: AuditFinding) -> list[str]:
    about = reference_safe(finding.subject) if finding.subject is not None else f"actor {reference_safe(finding.actor or '')}"
    out = [f"### {finding.severity.upper()} · {finding.kind} · {about}", "", reference_safe(finding.detail), ""]
    for event in finding.events:
        where = f" on {reference_safe(event.subject)}" if event.subject != finding.subject else ""
        roles = ", ".join(event.roles) or "none"
        out.append(f"- {reference_audit_code(event.event_id)}{where} at {event.timestamp.isoformat()}: {reference_safe(event.actor)} "
                   f"(roles: {reference_safe(roles)}) · {event.action} · {event.outcome}")
        for span in event.evidence:
            out += _cite(span, clean=reference_safe, code=reference_audit_code)
    out.append("")
    return out


def reference_audit_markdown(report: AuditReport) -> str:
    out = ["# Organizational decision audit", "", "| Kind | High | Medium | Low |", "|---|---|---|---|"]
    for kind in KINDS:
        counts = [sum(f.kind == kind and f.severity == s for f in report.findings) for s in SEVERITIES]
        out.append(f"| `{kind}` | " + " | ".join(map(str, counts)) + " |")
    out.append("| **Total** | " + " | ".join(str(report.summary[s]) for s in SEVERITIES) + " |")
    cutoff = report.as_of.isoformat() if report.as_of else "end of log"
    out += ["",
            f"- Audited {report.events_replayed} of {report.events_total} events on {report.subjects_total} "
            f"subject(s), as of {cutoff}",
            f"- Stale after {report.stale_days} days, measured at {report.reference_time.isoformat()}",
            f"- Mode: **{report.mode}** (the event log was not modified)", ""]
    if report.findings:
        out += ["## Findings", ""]
        for finding in report.findings:
            out += _reference_finding(finding)
    else:
        out += ["No findings.", ""]
    out += ["## Notes", "",
            "- Findings restate the replayed log and policy. They are leads for a person to review, not verdicts.",
            "- The log has no event that resolves a risk, so a risk stays on record after it is addressed."]
    return "\n".join(out) + "\n"
