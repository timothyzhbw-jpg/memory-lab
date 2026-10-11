"""Markdown views of a replay (current state first, then provenance and the full audit trail), of an audit,
and of the changes between two times."""
from __future__ import annotations

import json
import re
import unicodedata
from collections import Counter
from html import escape
from typing import Callable, Optional

from memcore import EvidenceSpan

from .audit import KINDS, SEVERITIES, AuditFinding, AuditReport, CitedEvent
from .changes import KINDS as CHANGE_KINDS, ChangesReport
from .models import ReplayResult, SubjectState


def _yaml_value(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return "null"
    if isinstance(value, list):
        return "[" + ", ".join(_yaml_value(v) for v in value) + "]"
    return json.dumps(value, ensure_ascii=False) if any(c in str(value) for c in ":#[]{},'\"") else str(value)


def cite(span: EvidenceSpan, indent: str = "  ", clean: Callable[[str], str] = str,
         code: Callable[[str], str] | None = None) -> list[str]:
    """One evidence citation: id, location, source, short hash, then the exact quote."""
    code = code or (lambda value: f"`{clean(value)}`")
    return [f"{indent}- evidence {code(str(span.id))} · {code(span.location)} · {clean(str(span.source_uri))} "
            f"· sha256 `{span.sha256[:12]}`",
            f"{indent}  > {clean(span.quote)}"]


def state_block(state: SubjectState) -> list[str]:
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


def explain(result: ReplayResult, subject: str) -> str:
    state = result.states[subject]
    out = [f"## {subject}", ""] + state_block(state) + [""]
    decision = state.last_authorized_decision
    if decision:
        out += ["**Why this is the current status**", "",
                f"- `{decision.event_id}`: {decision.actor} (roles: {', '.join(decision.roles)}) "
                f"chose **{decision.action}** at {decision.timestamp.isoformat()}"]
        for span in decision.evidence:
            out += cite(span)
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


def to_markdown(result: ReplayResult) -> str:
    cutoff = result.as_of.isoformat() if result.as_of else "end of log"
    out = ["# Organizational memory replay", "",
           f"- Replayed {result.events_replayed} of {result.events_total} events, as of {cutoff}",
           f"- Mode: **{result.mode}** (the event log was not modified)", ""]
    for subject in result.states:
        out.append(explain(result, subject))
    return "\n".join(out)


def _one_line(text: str) -> str:
    """Untrusted text kept on one line: control, format (bidi, zero-width) and separator characters are escaped."""
    return "".join(c if unicodedata.category(c) not in ("Cc", "Cf", "Zl", "Zp")
                   else f"\\u{ord(c):04x}" if ord(c) <= 0xFFFF else f"\\U{ord(c):08x}" for c in text)


def _safe(text: str) -> str:
    """Literal Markdown text, including in headings and evidence blockquotes."""
    text = escape(_one_line(text), quote=False)
    text = re.sub(r"([\\`*_{}\[\]#|~])", r"\\\1", text)
    # Block syntax can still be recognized at the start of a one-line quote/detail.
    text = re.sub(r"^(\s*)([-+=])", r"\1\\\2", text)
    return re.sub(r"^(\s*\d+)([.)])(?=\s)", r"\1\\\2", text)


def _audit_code(text: str) -> str:
    """Code spans need a delimiter longer than any backtick run in the data."""
    text = _one_line(text)
    fence = "`" * (1 + max((len(run) for run in re.findall(r"`+", text)), default=0))
    # Padding protects backticks at the edges and preserves significant edge spaces.
    if "`" in text or (text.startswith(" ") and text.endswith(" ") and text.strip()):
        return f"{fence} {text} {fence}"
    return f"{fence}{text}{fence}"


def _cited_events(events: list[CitedEvent], subject: Optional[str]) -> list[str]:
    """One line per event (naming its subject when it differs from ``subject``), then its evidence."""
    out = []
    for event in events:
        where = f" on {_safe(event.subject)}" if event.subject != subject else ""
        roles = ", ".join(event.roles) or "none"
        out.append(f"- {_audit_code(event.event_id)}{where} at {event.timestamp.isoformat()}: {_safe(event.actor)} "
                   f"(roles: {_safe(roles)}) · {event.action} · {event.outcome}")
        for span in event.evidence:
            out += cite(span, clean=_safe, code=_audit_code)
    return out


def _audit_finding(finding: AuditFinding) -> list[str]:
    about = _safe(finding.subject) if finding.subject is not None else f"actor {_safe(finding.actor or '')}"
    out = [f"### {finding.severity.upper()} · {finding.kind} · {about}", "", _safe(finding.detail), ""]
    out += _cited_events(finding.events, finding.subject)
    out.append("")
    return out


def audit_markdown(report: AuditReport) -> str:
    """The audit as Markdown: a summary table first, then every finding with its cited evidence."""
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
            out += _audit_finding(finding)
    else:
        out += ["No findings.", ""]
    out += ["## Notes", "",
            "- Findings restate the replayed log and policy. They are leads for a person to review, not verdicts.",
            "- The log has no event that resolves a risk, so a risk stays on record after it is addressed."]
    return "\n".join(out) + "\n"


def _change_counts(counts) -> str:
    listed = [f"`{kind}` ×{counts[kind]}" for kind in CHANGE_KINDS if counts.get(kind)]
    return ", ".join(listed) or "none"


def changes_markdown(report: ChangesReport) -> str:
    """The changes as Markdown: a summary table first, then each subject's changes with their cited evidence."""
    out = ["# Organizational decision changes", "",
           "| Subject | Status before | Status after | Changes |", "|---|---|---|---|"]
    for item in report.subjects:
        out.append(f"| {_safe(item.subject)} | {item.status_before or 'no events'} | {item.status_after} | "
                   f"{_change_counts(Counter(change.kind for change in item.changes))} |")
    out.append(f"| **Total** | | | {_change_counts(report.summary)} |")
    out += ["",
            f"- Period: after {report.from_time.isoformat()}, up to and including {report.to_time.isoformat()}",
            f"- {report.events_in_period} of {report.events_total} events fall in the period, on "
            f"{report.subjects_in_period} subject(s); {report.subjects_changed} subject(s) changed",
            f"- Mode: **{report.mode}** (the event log was not modified)", ""]
    for item in report.subjects:
        out += [f"## {_safe(item.subject)}", "",
                f"Status: {item.status_before or 'no events'} → {item.status_after}", ""]
        for change in item.changes:
            out += [f"### {change.kind}", "", _safe(change.detail), ""]
            out += _cited_events(change.events, item.subject)
            out.append("")
    if not report.subjects:
        out += ["No changes in the period.", ""]
    out += ["## Notes", "",
            "- Changes restate two replays of the log, at the start and at the end of the period, and the audit "
            "trail between them. An event at exactly the start time belongs to the earlier replay.",
            "- Only what replay applied changes state: an event the policy ignored is listed as an "
            "`unauthorized_attempt`, and a `comment` is not listed."]
    return "\n".join(out) + "\n"
