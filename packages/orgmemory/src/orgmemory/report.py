"""Markdown views of a replay (current state first, then provenance and the full audit trail) and of an audit."""
from __future__ import annotations

import json
import re
import unicodedata
from html import escape
from typing import Callable

from memcore import EvidenceSpan

from .audit import KINDS, SEVERITIES, AuditFinding, AuditReport
from .models import ReplayResult, SubjectState, TrailEntry

_LINE_CLASSES = frozenset(("Cc", "Cf", "Zl", "Zp"))
_ASCII_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_MARKUP = re.compile(r"[\\`*_{}\[\]#|~&<>]")
_LEADING = re.compile(r"^(?:\s*[-+=]|\s*\d+[.)](?=\s))")
_MARKDOWN_CHARS = re.compile(r"([\\`*_{}\[\]#|~])")
_LEADING_MARK = re.compile(r"^(\s*)([-+=])")
_LEADING_NUMBER = re.compile(r"^(\s*\d+)([.)])(?=\s)")
_BACKTICKS = re.compile(r"`+")


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


def explain(result: ReplayResult, subject: str, *, history: list[TrailEntry] | None = None) -> str:
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
    rows = history if history is not None else [e for e in result.audit_log if e.subject == subject]
    out += ["**Event history**", "", "| Time | Event | Actor | Action | Outcome | Status |", "|---|---|---|---|---|---|"]
    out += [f"| {e.timestamp.isoformat()} | `{e.event_id}` | {e.actor} ({', '.join(e.roles) or 'no role'}) | "
            f"{e.action} | {e.outcome} | {e.status_before} → {e.status_after} |" for e in rows]
    return "\n".join(out) + "\n"


def to_markdown(result: ReplayResult) -> str:
    grouped: dict[str, list[TrailEntry]] = {}
    for entry in result.audit_log:
        grouped.setdefault(entry.subject, []).append(entry)
    cutoff = result.as_of.isoformat() if result.as_of else "end of log"
    out = ["# Organizational memory replay", "",
           f"- Replayed {result.events_replayed} of {result.events_total} events, as of {cutoff}",
           f"- Mode: **{result.mode}** (the event log was not modified)", ""]
    for subject in result.states:
        out.append(explain(result, subject, history=grouped.get(subject, [])))
    return "\n".join(out)


def _plain_line(text: str) -> bool:
    """True when ``_one_line`` would copy every character unchanged."""
    if text.isascii():
        # ASCII Cc is C0 plus DEL. Format and separator characters are all above ASCII.
        return _ASCII_CONTROL.search(text) is None
    for char in text:
        if unicodedata.category(char) in _LINE_CLASSES:
            return False
    return True


def _one_line(text: str) -> str:
    """Untrusted text kept on one line: control, format (bidi, zero-width) and separator characters are escaped."""
    if _plain_line(text):
        return text
    return "".join(c if unicodedata.category(c) not in _LINE_CLASSES
                   else f"\\u{ord(c):04x}" if ord(c) <= 0xFFFF else f"\\U{ord(c):08x}" for c in text)


def _safe(text: str) -> str:
    """Literal Markdown text, including in headings and evidence blockquotes."""
    text = _one_line(text)
    if _MARKUP.search(text) is None and _LEADING.search(text) is None:
        return text
    text = escape(text, quote=False)
    text = _MARKDOWN_CHARS.sub(r"\\\1", text)
    # Block syntax can still be recognized at the start of a one-line quote/detail.
    text = _LEADING_MARK.sub(r"\1\\\2", text)
    return _LEADING_NUMBER.sub(r"\1\\\2", text)


def _audit_code(text: str) -> str:
    """Code spans need a delimiter longer than any backtick run in the data."""
    text = _one_line(text)
    if "`" not in text:
        # Padding protects significant edge spaces. A single space on both sides is not significant.
        if text.startswith(" ") and text.endswith(" ") and text.strip():
            return f"` {text} `"
        return f"`{text}`"
    fence = "`" * (1 + max(len(run) for run in _BACKTICKS.findall(text)))
    # A backtick anywhere uses the padded form so the delimiter stays unambiguous.
    return f"{fence} {text} {fence}"


def _audit_finding(finding: AuditFinding) -> list[str]:
    about = _safe(finding.subject) if finding.subject is not None else f"actor {_safe(finding.actor or '')}"
    out = [f"### {finding.severity.upper()} · {finding.kind} · {about}", "", _safe(finding.detail), ""]
    for event in finding.events:
        where = f" on {_safe(event.subject)}" if event.subject != finding.subject else ""
        roles = ", ".join(event.roles) or "none"
        out.append(f"- {_audit_code(event.event_id)}{where} at {event.timestamp.isoformat()}: {_safe(event.actor)} "
                   f"(roles: {_safe(roles)}) · {event.action} · {event.outcome}")
        for span in event.evidence:
            out += cite(span, clean=_safe, code=_audit_code)
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
