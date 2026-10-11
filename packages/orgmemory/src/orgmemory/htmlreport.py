"""Render a replay and a decision audit as one self-contained HTML file each (``--format html``).

The pages carry the same information as the Markdown reports: the replay's summary of every subject, then
each subject's state, the decision that put it there with its quoted evidence, ignored attempts, conflicts
and its decision trail; the audit's summary table, then its findings grouped by severity with every cited
event and evidence quote. Event-log, policy and evidence values are untrusted and go through
``memcore.htmlpage``, which escapes them and marks hidden characters; only http(s) sources become links.
"""
from __future__ import annotations

from memcore import htmlpage as h

from .audit import SEVERITIES, AuditFinding, AuditReport
from .models import DatedPolicy, ReplayResult, RoleChange, SubjectState
from .report import role_change_text

GENERATOR = "orgmemory"
STATUS_TONE = {"approved": "green", "rejected": "red", "proposed": "gray"}
OUTCOME_TONE = {"applied": "green", "unauthorized": "red", "invalid_supersession": "amber"}
SEVERITY_TONE = {"high": "red", "medium": "amber", "low": "gray"}


def _list(values: list[str], empty: str = "none") -> str:
    return ", ".join(h.code(v) for v in values) if values else f'<span class="muted">{empty}</span>'


def _roles(roles: list[str], empty: str = "no role") -> str:
    return ", ".join(h.text(r) for r in roles) or empty


def _cites(spans) -> str:
    return h.items(h.cite(span) for span in spans)


def _state(state: SubjectState) -> str:
    decision = state.last_authorized_decision
    last = (f"{h.code(decision.event_id)} ({h.text(decision.action)} by {h.text(decision.actor)})"
            if decision else '<span class="muted">none</span>')
    risks = h.items(f"{h.text(r.note)} {_by(r.actor, r.event_id)}" for r in state.risks) if state.risks \
        else '<span class="muted">none</span>'
    return h.meta([
        ("Current status", h.badge(state.current_status, STATUS_TONE[state.current_status])),
        ("Reconsideration requested", "yes" if state.reconsideration_requested else "no"),
        ("Pending approval", _list(state.pending_approval)),
        ("Dependencies", _list(state.dependencies)),
        ("Open risks", risks),
        ("Last authorized decision", last),
        ("Unauthorized change attempts", _list([c.event_id for c in state.pending_changes])),
    ])


def _by(actor: str, event_id: str) -> str:
    return f'<span class="muted">(raised by {h.text(actor)} in {h.code(event_id)})</span>'


def _roles_row(dated: DatedPolicy | None) -> list[tuple[str, str]]:
    """The header row of a page on a dataset with dated roles, as in the Markdown reports; nothing otherwise."""
    if dated is None:
        return []
    start = f"as of {h.when(dated.baseline)}" if dated.baseline else "from the beginning of the log"
    return [("Roles", f"policy.json {start}, then {len(dated.role_changes)} role change(s) from policy-log.jsonl; "
                      "each event is judged by the roles its actor held at its time")]


def _role_changes(changes: list[RoleChange]) -> str:
    return h.items(role_change_text(change, clean=h.text, code=h.code) + "\n" + _cites(change.evidence)
                   for change in changes)


def _subject(index: int, state: SubjectState, result: ReplayResult) -> str:
    out = [f'<section id="subject-{index}">', f"<h2>{h.text(state.subject)}</h2>", _state(state),
           "<h3>Why this is the current status</h3>"]
    decision = state.last_authorized_decision
    changes = result.dated_policy.role_changes if result.dated_policy else []
    if decision:
        out += [f"<p>{h.code(decision.event_id)}: {h.text(decision.actor)} (roles: {_roles(decision.roles)}) chose "
                f"<strong>{h.text(decision.action)}</strong> at {h.when(decision.timestamp)}</p>",
                _cites(decision.evidence)]
        later = [c for c in changes if c.subject_actor == decision.actor and c.timestamp > decision.timestamp]
        if later:
            out.append(f"<p>{h.text(decision.actor)}'s roles changed later: " + "; ".join(
                f"{'gained' if c.action == 'grant' else 'lost'} {h.text(c.role)} at {h.when(c.timestamp)} "
                f"({h.code(c.change_id)})" for c in later)
                + ". The decision stands: it is judged by the roles held when it was made.</p>")
    else:
        out.append("<p>No authorized decision yet.</p>")
    if state.pending_changes:
        out += ["<h3>Ignored: actor lacked authority</h3>",
                h.items(f"{h.code(c.event_id)}: {h.text(c.actor)} tried to {h.text(c.action)}; {h.text(c.reason)}"
                        for c in state.pending_changes)]
    if state.conflicts:
        out += ["<h3>Conflicts</h3>",
                h.items(f"{h.code(c.kind)} {_list(c.event_ids)}: {h.text(c.detail)}" for c in state.conflicts)]
    actors = {e.actor for e in result.audit_log if e.subject == state.subject}
    relevant = [c for c in changes if c.subject_actor in actors]
    if relevant:
        out += ["<h3>Role changes of these actors</h3>",
                "<p>Each event below was judged by the roles its actor held at its effective time.</p>",
                _role_changes(relevant)]
    rows = [[h.code(e.event_id), h.when(e.timestamp),
             f"{h.text(e.actor)} ({_roles(e.roles)})", h.text(e.action),
             h.badge(e.outcome, OUTCOME_TONE[e.outcome]),
             f"{h.text(e.status_before)} &rarr; {h.text(e.status_after)}"]
            for e in result.audit_log if e.subject == state.subject]
    out += ["<h3>Decision trail</h3>",
            h.table("Events in replay order", ["Event", "Time", "Actor", "Action", "Outcome", "Status"], rows),
            "</section>"]
    return "\n".join(out)


def replay_html(result: ReplayResult) -> str:
    """The replay as one HTML document: a summary row per subject, then each subject's state and trail."""
    cutoff = h.when(result.as_of) if result.as_of else "end of log"
    body = ["<h1>Organizational memory replay</h1>",
            h.meta([("Replayed", f"{result.events_replayed} of {result.events_total} events, as of {cutoff}"),
                    *_roles_row(result.dated_policy),
                    ("Mode", f"<strong>{h.text(result.mode)}</strong> (the event log was not modified)")]),
            "<section>\n<h2>Summary</h2>"]
    subjects = list(result.states.values())
    if subjects:
        rows = []
        for index, state in enumerate(subjects, start=1):
            decision = state.last_authorized_decision
            rows.append([f'<a href="#subject-{index}">{h.text(state.subject)}</a>',
                         h.badge(state.current_status, STATUS_TONE[state.current_status]),
                         "yes" if state.reconsideration_requested else "no",
                         _list(state.pending_approval),
                         h.code(decision.event_id) if decision else '<span class="muted">none</span>',
                         str(len(state.pending_changes)), str(len(state.conflicts))])
        body.append(h.table("Subjects and their current state",
                            ["Subject", "Status", "Reconsideration", "Pending approval",
                             "Last authorized decision", "Ignored attempts", "Conflicts"], rows, numeric=[5, 6]))
    else:
        body.append("<p>No events were replayed.</p>")
    body.append("</section>")
    body += [_subject(index, state, result) for index, state in enumerate(subjects, start=1)]
    return h.page("Organizational memory replay", "\n".join(body), GENERATOR)


def _finding(finding: AuditFinding) -> str:
    about = h.text(finding.subject) if finding.subject is not None else f"actor {h.text(finding.actor or '')}"
    out = ['<article class="item">',
           f"<h3>{h.badge(finding.severity, SEVERITY_TONE[finding.severity])} {h.code(finding.kind)} &middot; "
           f"{about}</h3>",
           f"<p>{h.text(finding.detail)}</p>"]
    events = []
    for event in finding.events:
        where = f" on {h.text(event.subject)}" if event.subject != finding.subject else ""
        events.append(f"<p>{h.code(event.event_id)}{where} at {h.when(event.timestamp)}: "
                      f"{h.text(event.actor)} (roles: {_roles(event.roles, 'none')}) &middot; "
                      f"{h.text(event.action)} &middot; {h.badge(event.outcome, OUTCOME_TONE[event.outcome])}</p>\n"
                      + _cites(event.evidence))
    out += ['<p class="label">Cited events</p>', h.items(events)]
    if finding.role_changes:
        out += ['<p class="label">Role changes</p>', _role_changes(finding.role_changes)]
    out.append("</article>")
    return "\n".join(out)


def audit_html(report: AuditReport) -> str:
    """The audit as one HTML document: findings per kind and severity, then the findings by severity."""
    def count(n: int, severity: str) -> str:
        return f'<a href="#severity-{severity}">{n}</a>' if n else "0"

    rows = [[h.code(kind)] + [str(sum(f.kind == kind and f.severity == s for f in report.findings))
                              for s in SEVERITIES] for kind in report.kinds]
    foot = ["Total"] + [count(report.summary[s], s) for s in SEVERITIES]
    cutoff = h.when(report.as_of) if report.as_of else "end of log"
    body = ["<h1>Organizational decision audit</h1>",
            h.meta([("Audited", f"{report.events_replayed} of {report.events_total} events on "
                                f"{report.subjects_total} subject(s), as of {cutoff}"),
                    ("Stale after", f"{report.stale_days} days, measured at "
                                    f"{h.when(report.reference_time)}"),
                    *_roles_row(report.dated_policy),
                    ("Mode", f"<strong>{h.text(report.mode)}</strong> (the event log was not modified)")]),
            "<section>\n<h2>Summary</h2>",
            h.table("Findings by kind and severity", ["Kind", "High", "Medium", "Low"], rows, foot=foot,
                    numeric=[1, 2, 3]),
            "</section>"]
    if not report.findings:
        body.append("<p>No findings.</p>")
    for severity in SEVERITIES:
        group = [f for f in report.findings if f.severity == severity]
        if group:
            body.append(f'<section id="severity-{severity}">\n'
                        f"<h2>{h.badge(severity, SEVERITY_TONE[severity])} {len(group)} finding(s)</h2>")
            body += [_finding(f) for f in group]
            body.append("</section>")
    if report.dated_policy is not None:
        changes = report.dated_policy.role_changes
        body += ["<section>\n<h2>Role changes</h2>",
                 _role_changes(changes) if changes else "<p>No role changes up to the cutoff.</p>", "</section>"]
    notes = ["Findings restate the replayed log and policy. They are leads for a person to review, not verdicts.",
             "The log has no event that resolves a risk, so a risk stays on record after it is addressed."]
    if report.dated_policy is not None:
        notes.append("Roles are effective-dated: a role granted or removed later does not change an earlier "
                     f"event's outcome, and {h.code('role_changed_after_decision')} is informational.")
    body += ["<section>\n<h2>Notes</h2>", h.items(notes), "</section>"]
    return h.page("Organizational decision audit", "\n".join(body), GENERATOR)
