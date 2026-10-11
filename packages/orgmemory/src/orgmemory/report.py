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
from memcore.sidecar import one_line, show_line

from .audit import SEVERITIES, AuditFinding, AuditReport, CitedEvent
from .changes import KINDS as CHANGE_KINDS, ChangesReport
from .models import DatedPolicy, ReplayResult, RoleChange, SubjectState, TrailEntry

# Terminal and bidi controls. Newlines and tabs stay so the report can still break lines.
_TERMINAL = re.compile("[\x00-\x08\x0b-\x1f\x7f-\x9f\u061c\u200b-\u200f\u2028-\u202e\u2066-\u2069\ufeff]")


def terminal_safe(text: str) -> str:
    """Replay text safe to print: control and bidi characters become U+FFFD. Newlines and tabs stay."""
    return _TERMINAL.sub("\ufffd", text)


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
    """One evidence citation: id, location, source, short hash, then the exact quote.

    A quote from a transcript or OCR sidecar is located in the media file first (``clip.mp4#t=12.5,17``), then
    in the sidecar whose bytes the hash covers.

    ``explain`` and ``replay`` use the default ``clean`` (``str``). They split cue lines first, then drop
    terminal and bidi controls on each line, and they flatten ids and paths onto one line. The audit passes
    ``_safe``, which already escapes those characters (a newline becomes a ``\\u`` escape) before the split."""
    code = code or (lambda value: f"`{clean(value)}`")
    # Identity, not a copy of ``str``: only the default display path scrubs. Audits pass ``_safe``.
    literal = clean is str

    def field(value: str) -> str:
        return one_line(value) if literal else value

    where = code(field(span.location))
    if span.media is not None:
        when = f" ({span.media.time_range})" if span.media.time_range else ""
        where = f"{code(field(span.citation))}{when} · {span.media.label} {where}"
    raw = span.quote if literal else clean(span.quote)
    lines = [show_line(line) if literal else line for line in re.split(r"\r\n|\r|\n", raw)]
    return [f"{indent}- evidence {code(field(str(span.id)))} · {where} · {clean(field(str(span.source_uri)))} "
            f"· sha256 `{span.sha256[:12]}`",
            *(f"{indent}  > {line}" for line in lines)]


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


def _role_names(roles: list[str]) -> str:
    return ", ".join(roles) or "none"


def role_change_text(change: RoleChange, clean: Callable[[str], str] = str,
                     code: Callable[[str], str] | None = None) -> str:
    """One applied policy-log entry: who changed whose role, when, and the roles before and after."""
    code = code or (lambda value: f"`{clean(value)}`")
    verb = (f"granted {clean(change.role)} to" if change.action == "grant"
            else f"removed {clean(change.role)} from")
    note = f"; note: {clean(_one_line(json.dumps(change.note, ensure_ascii=False)))}" if change.note else ""
    return (f"{code(change.change_id)} at {change.timestamp.isoformat()}: {clean(change.actor)} "
            f"(roles: {clean(_role_names(change.actor_roles))}) {verb} {clean(change.subject_actor)}; "
            f"roles of {clean(change.subject_actor)}: {clean(_role_names(change.roles_before))} → "
            f"{clean(_role_names(change.roles_after))}{note}")


def _roles_line(dated: DatedPolicy | None) -> list[str]:
    """The header line of a report on a dataset with dated roles; nothing otherwise."""
    if dated is None:
        return []
    start = f"as of {dated.baseline.isoformat()}" if dated.baseline else "from the beginning of the log"
    return [f"- Roles: policy.json {start}, then {len(dated.role_changes)} role change(s) from policy-log.jsonl; "
            "each event is judged by the roles its actor held at its time"]


def explain(result: ReplayResult, subject: str) -> str:
    return _explain(result, subject, [e for e in result.audit_log if e.subject == subject])


def _explain(result: ReplayResult, subject: str, rows: list[TrailEntry]) -> str:
    """``explain`` with the subject's trail entries already selected (``to_markdown`` groups them once)."""
    state = result.states[subject]
    out = [f"## {subject}", ""] + state_block(state) + [""]
    decision = state.last_authorized_decision
    changes = result.dated_policy.role_changes if result.dated_policy else []
    if decision:
        out += ["**Why this is the current status**", "",
                f"- `{decision.event_id}`: {decision.actor} (roles: {', '.join(decision.roles)}) "
                f"chose **{decision.action}** at {decision.timestamp.isoformat()}"]
        for span in decision.evidence:
            out += cite(span)
        later = [c for c in changes if c.subject_actor == decision.actor and c.timestamp > decision.timestamp]
        if later:
            out.append(f"  - {decision.actor}'s roles changed later: " + "; ".join(
                f"{'gained' if c.action == 'grant' else 'lost'} {c.role} at {c.timestamp.isoformat()} "
                f"(`{c.change_id}`)" for c in later)
                + ". The decision stands: it is judged by the roles held when it was made.")
        out.append("")
    else:
        out += ["No authorized decision yet.", ""]
    if state.pending_changes:
        out += ["**Ignored: actor lacked authority**", ""]
        out += [f"- `{c.event_id}`: {c.actor} tried to {c.action}; {c.reason}" for c in state.pending_changes]
        out.append("")
    if state.conflicts:
        out += ["**Conflicts**", ""] + [f"- {c.kind} {c.event_ids}: {c.detail}" for c in state.conflicts] + [""]
    actors = {e.actor for e in rows}
    relevant = [c for c in changes if c.subject_actor in actors]
    if relevant:
        out += ["**Role changes of these actors**", "",
                "Each event below was judged by the roles its actor held at its effective time.", ""]
        for change in relevant:
            out.append("- " + role_change_text(change))
            for span in change.evidence:
                out += cite(span)
        out.append("")
    out += ["**Event history**", "", "| Time | Event | Actor | Action | Outcome | Status |", "|---|---|---|---|---|---|"]
    out += [f"| {e.timestamp.isoformat()} | `{e.event_id}` | {e.actor} ({', '.join(e.roles) or 'no role'}) | "
            f"{e.action} | {e.outcome} | {e.status_before} → {e.status_after} |" for e in rows]
    return terminal_safe("\n".join(out) + "\n")


def to_markdown(result: ReplayResult) -> str:
    grouped: dict[str, list[TrailEntry]] = {}
    for entry in result.audit_log:
        grouped.setdefault(entry.subject, []).append(entry)
    cutoff = result.as_of.isoformat() if result.as_of else "end of log"
    out = ["# Organizational memory replay", "",
           f"- Replayed {result.events_replayed} of {result.events_total} events, as of {cutoff}",
           *_roles_line(result.dated_policy),
           f"- Mode: **{result.mode}** (the event log was not modified)", ""]
    for subject in result.states:
        out.append(_explain(result, subject, grouped.get(subject, [])))
    return terminal_safe("\n".join(out))


# Control, format (bidi, zero-width) and line/paragraph separator characters.
UNSAFE_CATEGORIES = ("Cc", "Cf", "Zl", "Zp")
_LINE_CLASSES = frozenset(UNSAFE_CATEGORIES)
_ASCII_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_MARKUP = re.compile(r"[\\`*_{}\[\]#|~&<>]")
_LEADING = re.compile(r"^(?:\s*[-+=]|\s*\d+[.)](?=\s))")
_MARKDOWN_CHARS = re.compile(r"([\\`*_{}\[\]#|~])")
_LEADING_SPACES = re.compile(r"^ +")
_LEADING_MARK = re.compile(r"^(\s*)([-+=])")
_LEADING_NUMBER = re.compile(r"^(\s*\d+)([.)])(?=\s)")
_BACKTICKS = re.compile(r"`+")


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
    if _MARKUP.search(text) is None and _LEADING.search(text) is None and not text.startswith(" "):
        return text  # nothing to escape: skip the substitutions
    text = escape(text, quote=False)
    text = _MARKDOWN_CHARS.sub(r"\\\1", text)
    # Leading spaces would make an evidence blockquote an indented code block: keep them as references.
    text = _LEADING_SPACES.sub(lambda spaces: "&#32;" * len(spaces.group()), text)
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
    out += _audit_role_changes(finding.role_changes)
    out.append("")
    return out


def _audit_role_changes(changes: list[RoleChange]) -> list[str]:
    out = []
    for change in changes:
        out.append("- role change " + role_change_text(change, clean=_safe, code=_audit_code))
        for span in change.evidence:
            out += cite(span, clean=_safe, code=_audit_code)
    return out


def audit_markdown(report: AuditReport) -> str:
    """The audit as Markdown: a summary table first, then every finding with its cited evidence."""
    out = ["# Organizational decision audit", "", "| Kind | High | Medium | Low |", "|---|---|---|---|"]
    for kind in report.kinds:
        counts = [sum(f.kind == kind and f.severity == s for f in report.findings) for s in SEVERITIES]
        out.append(f"| `{kind}` | " + " | ".join(map(str, counts)) + " |")
    out.append("| **Total** | " + " | ".join(str(report.summary[s]) for s in SEVERITIES) + " |")
    cutoff = report.as_of.isoformat() if report.as_of else "end of log"
    out += ["",
            f"- Audited {report.events_replayed} of {report.events_total} events on {report.subjects_total} "
            f"subject(s), as of {cutoff}",
            f"- Stale after {report.stale_days} days, measured at {report.reference_time.isoformat()}",
            *_roles_line(report.dated_policy),
            f"- Mode: **{report.mode}** (the event log was not modified)", ""]
    if report.findings:
        out += ["## Findings", ""]
        for finding in report.findings:
            out += _audit_finding(finding)
    else:
        out += ["No findings.", ""]
    if report.dated_policy is not None:
        out += ["## Role changes", ""]
        out += _audit_role_changes(report.dated_policy.role_changes) or ["No role changes up to the cutoff."]
        out.append("")
    out += ["## Notes", "",
            "- Findings restate the replayed log and policy. They are leads for a person to review, not verdicts.",
            "- The log has no event that resolves a risk, so a risk stays on record after it is addressed."]
    if report.dated_policy is not None:
        out.append("- Roles are effective-dated: a role granted or removed later does not change an earlier "
                   "event's outcome, and `role_changed_after_decision` is informational.")
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
