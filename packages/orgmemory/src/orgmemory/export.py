"""Export current authorized decisions as Architecture Decision Records (ADRs) or as one CSV table.

Everything is derived from ``replay()``, as the audit is: one record per subject whose replay has a current
authorized decision (an ``approve``, a ``reject``, or a ``revoke`` that sent the subject back to proposed).
An ADR follows Michael Nygard's template as adr-tools writes it (title, date, Status, Context, Decision,
Consequences) with MADR's status words, plus an Evidence section. Untrusted text is escaped with the
report renderers' helpers, and output is created only outside the dataset directory.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import re
import secrets
import unicodedata
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path
from typing import Literal, Optional

from pydantic import BaseModel

from .audit import CitedEvent, open_periods
from .models import Conflict, PendingChange, TrailEntry
from .replay import Dataset, replay
from .report import UNSAFE_CATEGORIES, _audit_code, _one_line, _safe

AdrStatus = Literal["accepted", "rejected", "proposed"]
ADR_STATUS: dict[str, AdrStatus] = {"approved": "accepted", "rejected": "rejected", "proposed": "proposed"}
SLUG_LENGTH = 48
HASH_LENGTH = 12


class RecordEvent(CitedEvent):
    """An event a record names, with its evidence and the free-text fields the record shows."""
    note: Optional[str] = None
    dependency: Optional[str] = None
    supersedes: Optional[str] = None
    after_decision: bool = False


class Dependency(BaseModel):
    subject: str
    current_status: Optional[str]  # None: the dependency has no events in this log
    event_ids: list[str]           # the applied dependency events that declared it


class DecisionRecord(BaseModel):
    subject: str
    file_name: str
    status: AdrStatus
    current_status: Literal["approved", "rejected", "proposed"]
    as_of: Optional[datetime]
    events_replayed: int
    events_total: int
    decision: str                       # the current authorized decision
    earlier_decisions: list[str]        # earlier authorized approve, reject and revoke events, replay order
    context: list[str]                  # applied request, reconsider and raise_risk events, replay order
    reconsideration_requested: bool
    pending_approval: list[str]         # roles
    pending_since: list[str]            # the events that opened the open reconsideration or pending approval
    dependencies: list[Dependency]
    unauthorized_change_attempts: list[PendingChange]
    conflicts: list[Conflict]
    change_roles: dict[str, list[str]]  # approve, reject and revoke: the roles the policy allows
    events: dict[str, RecordEvent]      # every event named above, in replay order


def slug(subject: str) -> str:
    """Lowercase ASCII letters, digits and hyphens: safe as a file name on every platform."""
    ascii_text = unicodedata.normalize("NFKD", subject).encode("ascii", "ignore").decode("ascii").lower()
    text = re.sub(r"[^a-z0-9]+", "-", ascii_text).strip("-")[:SLUG_LENGTH].strip("-")
    return text or "subject"


def file_name(subject: str) -> str:
    """``<slug>-<12 hex>.md``: the hash of the exact subject keeps subjects with the same slug apart."""
    return f"{slug(subject)}-{hashlib.sha256(subject.encode('utf-8')).hexdigest()[:HASH_LENGTH]}.md"


def decision_records(dataset: Dataset, as_of: Optional[datetime] = None) -> tuple[int, list[DecisionRecord]]:
    """Replay up to ``as_of``; return the number of subjects and one record per subject with a decision."""
    result = replay(dataset, as_of)
    events = {event.id: event for event in dataset.events}
    entries = {entry.event_id: entry for entry in result.audit_log}
    position = {entry.event_id: index for index, entry in enumerate(result.audit_log)}
    trails: dict[str, list[TrailEntry]] = {}
    for entry in result.audit_log:
        trails.setdefault(entry.subject, []).append(entry)
    policy = dataset.policy
    change_roles = {action: sorted(policy.permissions.get(action, [])) for action in ("approve", "reject", "revoke")}
    records: list[DecisionRecord] = []
    for subject, state in result.states.items():
        decision = state.last_authorized_decision
        if decision is None:
            continue
        trail = trails[subject]
        applied = [e for e in trail if e.outcome == "applied"]
        context = [e.event_id for e in applied if e.action in ("request", "reconsider", "raise_risk")]
        pending, reconsider = open_periods(trail)
        opened = reconsider if state.reconsideration_requested else pending if state.pending_approval else []
        dependencies = []
        for name in state.dependencies:
            other = result.states.get(name)
            declared = [e.event_id for e in applied
                        if e.action == "dependency" and events[e.event_id].dependency == name]
            dependencies.append(Dependency(subject=name, current_status=other.current_status if other else None,
                                           event_ids=declared))
        named = {decision.event_id, *state.decision_history, *context, *(e.event_id for e in opened),
                 *(i for d in dependencies for i in d.event_ids),
                 *(c.event_id for c in state.pending_changes), *(i for c in state.conflicts for i in c.event_ids)}
        cited = {}
        for event_id in sorted(named, key=position.__getitem__):
            entry, event = entries[event_id], events[event_id]
            cited[event_id] = RecordEvent(
                event_id=event_id, timestamp=entry.timestamp, subject=entry.subject, actor=entry.actor,
                roles=entry.roles, action=entry.action, outcome=entry.outcome,
                evidence=[dataset.evidence[i] for i in entry.evidence_ids], note=event.note,
                dependency=event.dependency, supersedes=event.supersedes,
                after_decision=position[event_id] > position[decision.event_id])
        records.append(DecisionRecord(
            subject=subject, file_name=file_name(subject), status=ADR_STATUS[state.current_status],
            current_status=state.current_status, as_of=as_of, events_replayed=result.events_replayed,
            events_total=result.events_total, decision=decision.event_id,
            earlier_decisions=state.decision_history[:-1], context=context,
            reconsideration_requested=state.reconsideration_requested, pending_approval=state.pending_approval,
            pending_since=[e.event_id for e in opened], dependencies=dependencies,
            unauthorized_change_attempts=state.pending_changes, conflicts=state.conflicts,
            change_roles=change_roles, events=cited))
    names = [record.file_name for record in records]
    if len(set(names)) != len(names):
        raise ValueError("two subjects map to the same ADR file name; nothing written")
    return len(result.states), records


# --- Markdown ----------------------------------------------------------------------------------------

def _roles(roles: Iterable[str]) -> str:
    return ", ".join(_safe(role) for role in roles) or "no role"


def _ids(ids: Iterable[str]) -> str:
    return ", ".join(_audit_code(i) for i in ids)


def _event_line(event: RecordEvent, what: str) -> str:
    evidence = _ids(span.id for span in event.evidence)
    return (f"- {_audit_code(event.event_id)} · {event.timestamp.isoformat()} · {_safe(event.actor)} "
            f"(roles: {_roles(event.roles)}) · {what} · evidence {evidence}")


def _what(event: RecordEvent) -> str:
    what = f"`{event.action}`"  # one of the Action literals
    if event.note:
        what += f": {_safe(event.note)}"
    return what + (" (after the decision)" if event.after_decision else "")


def _status_lines(record: DecisionRecord) -> list[str]:
    cutoff = record.as_of.isoformat() if record.as_of else "the end of the log"
    out = [record.status.capitalize(), "",
           f"{_audit_code(record.decision)} is the current authorized decision when the event log is replayed as of "
           f"{cutoff} ({record.events_replayed} of {record.events_total} events). Replayed status: "
           f"`{record.current_status}`."]
    if record.reconsideration_requested:
        out += ["", f"A reconsideration has been requested ({_ids(record.pending_since)}); it awaits a decision "
                    f"from {_roles(record.pending_approval)}."]
    elif record.pending_approval:
        out += ["", f"It awaits a decision from {_roles(record.pending_approval)}, open since "
                    f"{_ids(record.pending_since)}."]
    return out


def _decision_lines(record: DecisionRecord) -> list[str]:
    decision = record.events[record.decision]
    what = {"approve": "**approved**", "reject": "**rejected**", "revoke": "**revoked**"}[decision.action]
    if decision.action == "revoke":
        revoked = record.events.get(decision.supersedes)
        what += f" {_audit_code(decision.supersedes)}"
        if revoked is not None:
            what += f" (`{revoked.action}` by {_safe(revoked.actor)} at {revoked.timestamp.isoformat()})"
    if decision.note:
        what += f"; note: {_safe(decision.note)}"
    out = [_event_line(decision, what)]
    if record.earlier_decisions:
        out += ["", "Earlier authorized decisions on this subject, oldest first:", ""]
        out += [_event_line(record.events[i], _what(record.events[i])) for i in record.earlier_decisions]
    return out


def _consequence_lines(record: DecisionRecord) -> list[str]:
    roles = {action: _roles(r) for action, r in record.change_roles.items()}
    decision = _audit_code(record.decision)
    if record.current_status == "approved":
        out = [f"The subject stays accepted until an authorized `reject` (roles: {roles['reject']}) or a `revoke` "
               f"of {decision} (roles: {roles['revoke']})."]
    elif record.current_status == "rejected":
        out = [f"The subject stays rejected until an authorized `approve` (roles: {roles['approve']}) or a "
               f"`revoke` of {decision} (roles: {roles['revoke']})."]
    else:
        out = [f"No decision is in force: the next authorized `approve` (roles: {roles['approve']}) or `reject` "
               f"(roles: {roles['reject']}) decides the subject."]
    if record.dependencies:
        out += ["", "Dependencies:", ""]
        for dependency in record.dependencies:
            state = (f"which is {dependency.current_status}" if dependency.current_status
                     else "which has no events in this log")
            out.append(f"- {_safe(dependency.subject)}, {state}; declared by {_ids(dependency.event_ids)}")
    if record.unauthorized_change_attempts:
        out += ["", "Ignored change attempts (the actor's roles do not allow the action, so the status did not "
                    "change):", ""]
        out += [_event_line(record.events[c.event_id], _what(record.events[c.event_id]))
                for c in record.unauthorized_change_attempts]
    if record.conflicts:
        out += ["", "Conflicts recorded by replay:", ""]
        out += [f"- `{c.kind}` ({_ids(c.event_ids)}): {_safe(c.detail)}" for c in record.conflicts]
    return out


def _evidence_lines(record: DecisionRecord) -> list[str]:
    spans, cited_by = {}, {}
    for event in record.events.values():
        for span in event.evidence:
            spans.setdefault(span.id, span)
            cited_by.setdefault(span.id, []).append(event.event_id)
    out = ["Each quote is the exact text at the given characters of a file whose raw bytes have the SHA-256 "
           "shown; every hash and quote was checked when the dataset was loaded. A hash proves that a quote is "
           "in those bytes, not that the source is authentic.", ""]
    for evidence_id, span in spans.items():
        captured = span.captured_at.isoformat() if span.captured_at else "unknown"
        out += [f"- {_audit_code(evidence_id)} · {_audit_code(span.location)} · characters {span.start}–{span.end} "
                f"· sha256 `{span.sha256}` · source {_audit_code(span.source_uri or '')} · "
                f"{_safe(span.source_kind or 'unknown')} · captured {captured} · cited by "
                f"{_ids(dict.fromkeys(cited_by[evidence_id]))}",
                f"  > {_safe(span.quote)}"]
    return out


def adr_markdown(record: DecisionRecord) -> str:
    """One decision record. Every event the record names has its evidence in the Evidence section."""
    decision = record.events[record.decision]
    context = [_event_line(record.events[i], _what(record.events[i])) for i in record.context]
    out = [f"# {_safe(record.subject)}", "", f"Date: {decision.timestamp.date().isoformat()}", "",
           "## Status", "", *_status_lines(record), "",
           "## Context", "",
           *(["Requests and risks on record for this subject, in replay order:", "", *context] if context
             else ["No request or risk is on record for this subject."]), "",
           "## Decision", "", *_decision_lines(record), "",
           "## Consequences", "", *_consequence_lines(record), "",
           "## Evidence", "", *_evidence_lines(record), "",
           "*Generated by `orgmemory export` from the replayed event log, which it did not modify. It restates "
           "the log; it is not a rationale written by the decision maker.*"]
    return "\n".join(out) + "\n"


# --- CSV ---------------------------------------------------------------------------------------------

CSV_COLUMNS = ("subject", "adr_file", "status", "current_status", "decided_at", "decision_event",
               "decision_action", "decided_by", "decided_by_roles", "supersedes", "earlier_decisions",
               "reconsideration_requested", "pending_approval", "dependencies", "open_risks",
               "unauthorized_change_attempts", "conflicts", "evidence_ids", "evidence_sha256",
               "evidence_source_uris", "evidence_quotes", "as_of")
# A spreadsheet runs a cell that starts with one of these as a formula.
FORMULA_START = ("=", "+", "-", "@")


def _cell(value: object) -> str:
    """One line of text, with control and bidi characters escaped as in the Markdown reports."""
    text = _one_line("" if value is None else str(value))
    return "'" + text if text.startswith(FORMULA_START) else text


def _json_char(char: str) -> str:
    if unicodedata.category(char) not in UNSAFE_CATEGORIES:
        return char
    code = ord(char)
    if code <= 0xFFFF:
        return f"\\u{code:04x}"
    code -= 0x10000  # JSON spells a supplementary character as a surrogate pair
    return f"\\u{0xD800 + (code >> 10):04x}\\u{0xDC00 + (code & 0x3FF):04x}"


def _json_cell(values: list) -> str:
    """A JSON array that decodes to the exact values. Control, bidi and separator characters, which can only
    occur inside its strings, are JSON escapes."""
    return "".join(map(_json_char, json.dumps(values, ensure_ascii=False)))


def decisions_csv(records: list[DecisionRecord]) -> str:
    """One row per record; list columns are JSON arrays, so they decode to the exact values."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(CSV_COLUMNS)
    for record in records:
        decision = record.events[record.decision]
        evidence = decision.evidence
        writer.writerow([
            _cell(record.subject), _cell(record.file_name), record.status, record.current_status,
            decision.timestamp.isoformat(), _cell(decision.event_id), decision.action, _cell(decision.actor),
            _json_cell(decision.roles), _cell(decision.supersedes), _json_cell(record.earlier_decisions),
            "true" if record.reconsideration_requested else "false", _json_cell(record.pending_approval),
            _json_cell([d.subject for d in record.dependencies]),
            _json_cell([record.events[i].note for i in record.context if record.events[i].action == "raise_risk"]),
            _json_cell([c.event_id for c in record.unauthorized_change_attempts]),
            _json_cell([c.kind for c in record.conflicts]), _json_cell([s.id for s in evidence]),
            _json_cell([s.sha256 for s in evidence]), _json_cell([s.source_uri for s in evidence]),
            _json_cell([s.quote for s in evidence]), record.as_of.isoformat() if record.as_of else ""])
    return buffer.getvalue()


# --- writing -----------------------------------------------------------------------------------------

def _inside(root: Path, target: Path) -> bool:
    """Whether ``target`` or any existing directory above it is ``root``, compared by file identity so that
    symlinks, ``..`` and case-insensitive file systems cannot hide the dataset."""
    root_stat = root.stat()
    resolved = target.resolve()
    for candidate in (resolved, *resolved.parents):
        try:
            if os.path.samestat(candidate.stat(), root_stat):
                return True
        except OSError:
            continue
    return False


def create_files(dataset: Path, directory: Path, files: dict[str, bytes], force: bool = False) -> list[Path]:
    """Create ``files`` (name -> exact bytes) in ``directory``, which must be outside ``dataset``.

    Every name is checked before the first write: an existing file is refused unless ``force``, and even
    then only a regular file is replaced (atomically, by rename, so a symlink or hard link is never written
    through). The temporary name used for that rename is removed only when this call created it. The
    directory and its parents are created as needed. Returns the written paths.
    """
    targets = [directory / name for name in files]
    for path in (directory, *targets):
        if _inside(dataset, path):
            raise ValueError(f"refusing to write inside the dataset directory: {path}")
    if directory.exists() and not directory.is_dir():
        raise ValueError(f"not a directory: {directory}")
    existing = [path for path in targets if os.path.lexists(path)]
    if existing and not force:
        raise ValueError(f"refusing to overwrite {len(existing)} existing file(s), use --force to replace them: "
                         + ", ".join(str(path) for path in existing))
    for path in existing:
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"refusing to replace something that is not a regular file: {path}")
    directory.mkdir(parents=True, exist_ok=True)
    for path, data in zip(targets, files.values()):
        if not force:
            with path.open("xb") as handle:
                handle.write(data)
            continue
        temporary = path.with_name(f".{path.name}.{secrets.token_hex(6)}.tmp")
        created = False
        try:
            with temporary.open("xb") as handle:
                created = True
                handle.write(data)
            os.replace(temporary, path)
        except OSError:
            if created:
                try:
                    temporary.unlink()
                except OSError:
                    pass
            raise
    return targets
