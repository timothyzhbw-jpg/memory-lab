"""Markdown views of a replay (current state first, then provenance and the full audit trail) and of an audit."""
from __future__ import annotations

import json
import re
import unicodedata
from html import escape
from typing import Callable

from memcore import EvidenceSpan

from .audit import KINDS, SEVERITIES, AuditFinding, AuditReport
from .models import ReplayResult, SubjectState

# Terminal and bidi controls. Newlines and tabs stay so the report can still break lines.
_TERMINAL = re.compile("[\x00-\x08\x0b-\x1f\x7f-\x9f\u061c\u200b-\u200f\u2028-\u202e\u2066-\u2069\ufeff]")


def terminal_safe(text: str) -> str:
    """Replay text safe to print: control and bidi characters become U+FFFD. Newlines and tabs stay."""
    return _TERMINAL.sub("\ufffd", text)


def terminal_line(text: str) -> str:
    """One display line: whitespace flattened, then terminal and bidi controls become U+FFFD."""
    return terminal_safe(" ".join(text.split()))


# json.dumps escapes C0. These are the characters it leaves raw under ensure_ascii=False.
_JSON_UNSAFE = re.compile("[\x7f-\x9f\u061c\u200b-\u200f\u2028-\u202e\u2066-\u2069\ufeff]")


def json_shield(text: str) -> str:
    """Replace raw DEL, C1, bidi, zero-width and line-separator characters with JSON ``\\uXXXX`` escapes.

    ``json.loads`` restores the character. A terminal never sees it raw.
    """
    return _JSON_UNSAFE.sub(lambda match: "\\u%04x" % ord(match.group()), text)


def _one_line(text: str) -> str:
    """Untrusted text kept on one line: control, format (bidi, zero-width) and separator characters are escaped."""
    return "".join(c if unicodedata.category(c) not in ("Cc", "Cf", "Zl", "Zp")
                   else f"\\u{ord(c):04x}" if ord(c) <= 0xFFFF else f"\\U{ord(c):08x}" for c in text)


def _plain(text: str) -> str:
    """One line of untrusted replay text. A pipe is escaped so a table cell cannot add a column."""
    return _one_line(text).replace("|", "\\|")


def _yaml_value(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return "null"
    if isinstance(value, list):
        return "[" + ", ".join(_yaml_value(v) for v in value) + "]"
    return json.dumps(value, ensure_ascii=False) if any(c in str(value) for c in ":#[]{},'\"") else str(value)


def cite(span: EvidenceSpan, indent: str = "  ", clean: Callable[[str], str] = _plain,
         code: Callable[[str], str] | None = None) -> list[str]:
    """One evidence citation: id, location, source, short hash, then the exact quote."""
    code = code or (lambda value: f"`{clean(value)}`")
    return [f"{indent}- evidence {code(str(span.id))} · {code(span.location)} · {clean(str(span.source_uri))} "
            f"· sha256 `{span.sha256[:12]}`",
            f"{indent}  > {clean(span.quote)}"]


def state_block(state: SubjectState) -> list[str]:
    decision = state.last_authorized_decision
    fields = {
        "subject": _plain(state.subject),
        "current_status": state.current_status,
        "reconsideration_requested": state.reconsideration_requested,
        "pending_approval": [_plain(item) for item in state.pending_approval],
        "dependencies": [_plain(item) for item in state.dependencies],
        "open_risks": [_plain(risk.note) for risk in state.risks],
        "last_authorized_decision": _plain(
            f"{decision.event_id} ({decision.action} by {decision.actor})") if decision else None,
        "unauthorized_change_attempts": [_plain(change.event_id) for change in state.pending_changes],
    }
    return ["```yaml"] + [f"{k}: {_yaml_value(v)}" for k, v in fields.items()] + ["```"]


def explain(result: ReplayResult, subject: str) -> str:
    state = result.states[subject]
    out = [f"## {_plain(subject)}", ""] + state_block(state) + [""]
    decision = state.last_authorized_decision
    if decision:
        roles = ", ".join(_plain(role) for role in decision.roles)
        out += ["**Why this is the current status**", "",
                f"- `{_plain(decision.event_id)}`: {_plain(decision.actor)} (roles: {roles}) "
                f"chose **{_plain(decision.action)}** at {decision.timestamp.isoformat()}"]
        for span in decision.evidence:
            out += cite(span)
        out.append("")
    else:
        out += ["No authorized decision yet.", ""]
    if state.pending_changes:
        out += ["**Ignored: actor lacked authority**", ""]
        out += [f"- `{_plain(change.event_id)}`: {_plain(change.actor)} tried to {_plain(change.action)}; "
                f"{_plain(change.reason)}" for change in state.pending_changes]
        out.append("")
    if state.conflicts:
        out += ["**Conflicts**", ""] + [
            f"- {_plain(conflict.kind)} {[_plain(event_id) for event_id in conflict.event_ids]}: "
            f"{_plain(conflict.detail)}" for conflict in state.conflicts] + [""]
    rows = [entry for entry in result.audit_log if entry.subject == subject]
    out += ["**Event history**", "", "| Time | Event | Actor | Action | Outcome | Status |",
            "|---|---|---|---|---|---|"]
    for entry in rows:
        roles = ", ".join(_plain(role) for role in entry.roles) or "no role"
        out.append(f"| {entry.timestamp.isoformat()} | `{_plain(entry.event_id)}` | "
                   f"{_plain(entry.actor)} ({roles}) | {_plain(entry.action)} | {_plain(entry.outcome)} | "
                   f"{_plain(entry.status_before)} → {_plain(entry.status_after)} |")
    return terminal_safe("\n".join(out) + "\n")


def to_markdown(result: ReplayResult) -> str:
    cutoff = result.as_of.isoformat() if result.as_of else "end of log"
    out = ["# Organizational memory replay", "",
           f"- Replayed {result.events_replayed} of {result.events_total} events, as of {cutoff}",
           f"- Mode: **{result.mode}** (the event log was not modified)", ""]
    for subject in result.states:
        out.append(explain(result, subject))
    return terminal_safe("\n".join(out))


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
