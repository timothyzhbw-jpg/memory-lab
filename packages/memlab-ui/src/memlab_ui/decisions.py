"""The "Team decisions" page: a decision log replayed under its policy, in plain words.

Everything shown restates ``orgmemory replay``, ``audit`` and ``changes``: the current status of each topic,
the decision that put it there with its quote, every event with whether it counted, the audit findings, and what
changed between two dates. Event logs and evidence are untrusted text. Nothing is appended or written.
"""
from __future__ import annotations

from datetime import datetime, time, timezone

from pydantic import AwareDatetime, TypeAdapter, ValidationError

from memcore import htmlpage as h
from orgmemory import (AuditFinding, AuditReport, ChangesReport, Dataset, ReplayResult, SubjectState, TrailEntry,
                       replay)
from orgmemory import audit as decision_audit
from orgmemory import changes as decision_changes

from .i18n import _
from .pages import (attr, error_card, fill, href, lang_field, plural, quote, role_words, select, span_detail,
                    span_place, text_input, when)
from .workspace import InputError, Inventory, Workspace

STATUS = {"approved": ("Approved", "approved"), "rejected": ("Rejected", "rejected"),
          "proposed": ("Waiting", "waiting")}
ACTION = {"request": "asked for it", "reconsider": "asked to reconsider", "raise_risk": "raised a risk",
          "dependency": "said it depends on", "approve": "approved it", "reject": "rejected it",
          "revoke": "withdrew the earlier decision", "comment": "commented"}
CAN = {"approve": "approve", "reject": "reject", "revoke": "withdraw decisions", "request": "make requests",
       "reconsider": "ask to reconsider", "raise_risk": "raise risks", "dependency": "add dependencies",
       "comment": "comment"}
DECIDED = {"approve": "Approved by", "reject": "Rejected by", "revoke": "The earlier decision was withdrawn by"}
SEVERITY = {"high": ("Important", "attention"), "medium": ("Worth a look", "attention"), "low": ("Minor", "grey")}
CHANGE = {
    "decision": "Decided",
    "pending_approval_opened": "Started waiting for a decision",
    "pending_approval_resolved": "Got the decision it was waiting for",
    "reconsideration_opened": "Someone asked to reconsider",
    "reconsideration_resolved": "The request to reconsider was answered",
    "risk_raised": "A risk was raised",
    "dependency_added": "A dependency was added",
    "conflict": "Decisions conflicted",
    "unauthorized_attempt": "Not counted: someone tried to decide without the right role",
}
FINDING = {
    "stale_reconsideration": "Someone asked to reconsider {s}, and no decision has followed for more than {days} days.",
    "stale_pending_approval": "{s} has been waiting for a decision for more than {days} days.",
    "decision_reversed": "The decision on {s} was reversed. Make sure everyone knows the latest answer.",
    "simultaneous_decisions": ("Two different decisions on {s} have the same time, so the order in the file decided "
                               "which counts."),
    "invalid_supersession": ("Someone tried to withdraw a decision on {s} that was no longer the current one; "
                             "nothing changed."),
    "unresolved_dependency": "{s} is approved, but something it depends on is not approved yet.",
    "self_approval": "{a} asked for {s} and then approved it themselves.",
    "role_changed_after_decision": "{a} later lost the role that let them decide {s}. The decision still counts.",
}
ATTEMPTS = ("{who} tried {times} to do something their role does not allow ({actions}) on {topics}. It was not "
            "counted.")
RISK_AFTER = "A risk was raised after {s} was approved."
RISK_DURING = "{s} was approved while a risk was on record. Check that the risk was handled."
LEAD = ("Pick a decision log to see what was really decided, who could decide it, and which messages did not "
        "count.")
REPLAYED = ("{events} on {topics}, {cutoff}. Only events from someone whose role allows the action change a status. "
            "Nothing was changed.")
DOTS = "A filled dot counted toward the decision; a hollow dot was noted or not counted."
DECISIONS = frozenset({"approve", "reject", "revoke"})
_AWARE = TypeAdapter(AwareDatetime)


def t(message: str, **values: object) -> str:
    """A translated message as escaped text."""
    return h.text(_(message, **values))


def moment(value: str | None, label: str) -> datetime | None:
    """A date means the end of that day in UTC; a full ISO 8601 time needs its timezone."""
    if not value:
        return None
    if len(value) == 10:
        try:
            return datetime.combine(datetime.strptime(value, "%Y-%m-%d").date(), time(23, 59, 59), timezone.utc)
        except ValueError:
            pass
    else:
        try:
            return _AWARE.validate_python(value)
        except ValidationError:
            pass
    raise InputError(_("{what} must be a date like 2026-10-07.", what=_(label)))


def status_pill(status: str) -> str:
    label, tone = STATUS[status]
    return f'<span class="pill {tone}">{t(label)}</span>'


def _who(actor: str, roles: list[str]) -> str:
    shown = role_words(roles) or t("no role")
    return f"<strong>{h.text(actor)}</strong> <span class=\"muted\">({shown})</span>"


def _quotes(spans, base: str, css: str = "q") -> str:
    """Evidence quotes; ``base`` is the dataset folder, where evidence paths start."""
    if not spans:
        return ""
    out = [quote(spans[0].quote, css=css), f'<p class="small">{span_place(spans[0], base)}</p>',
           span_detail(spans[0], base)]
    if len(spans) > 1:
        rest = "".join(f'<div class="more">{quote(s.quote)}<p class="small">{span_place(s, base)}</p>'
                       f"{span_detail(s, base)}</div>" for s in spans[1:])
        label = plural(len(spans) - 1, "{n} more quote", "{n} more quotes")
        out.append(f"<details><summary>{h.text(label)}</summary>{rest}</details>")
    return "".join(out)


def _verdict(entry: TrailEntry) -> tuple[str, str]:
    if entry.outcome == "applied":
        return (_("Counted"), "counted") if entry.action in DECISIONS else (_("Noted"), "noted")
    if entry.outcome == "invalid_supersession":
        return _("Not counted: it withdraws a decision that is no longer the current one"), "not"
    roles = ", ".join(role.replace("_", " ") for role in entry.roles) or _("someone without a role")
    return _("Not counted: {roles} can't {action}", roles=roles, action=_(CAN.get(entry.action, entry.action))), "not"


def _timeline(entries: list[TrailEntry], data: Dataset, base: str) -> str:
    events = {event.id: event for event in data.events}
    items = []
    for entry in entries:
        event = events.get(entry.event_id)
        what = t(ACTION.get(entry.action, entry.action))
        if event is not None and entry.action == "raise_risk" and event.note:
            what += f": &quot;{h.text(event.note)}&quot;"
        elif event is not None and entry.action == "dependency" and event.dependency:
            what += f" {h.text(event.dependency)}"
        verdict, tone = _verdict(entry)
        spans = [data.evidence[i] for i in entry.evidence_ids if i in data.evidence]
        items.append(f'<li class="{"counted" if tone == "counted" else "uncounted"}">'
                     '<span class="mark" aria-hidden="true"></span>'
                     f'<p class="small">{when(entry.timestamp)} &middot; '
                     f'{fill("event {id}", id=h.code(entry.event_id))}</p>'
                     f"<p>{_who(entry.actor, entry.roles)} {what}</p>"
                     f'<p class="verdict {tone}">{h.text(verdict)}</p>{_quotes(spans, base)}</li>')
    return f'<ol class="timeline">{"".join(items)}</ol>'


def sentence(finding: AuditFinding, stale_days: int) -> str:
    """One audit finding in a plain sentence, with its severity and the report's own detail in small print."""
    s = f"<strong>{h.text(finding.subject)}</strong>" if finding.subject is not None else t("a topic")
    a = f"<strong>{h.text(finding.actor)}</strong>" if finding.actor is not None else t("Someone")
    kind = finding.kind
    if kind == "unauthorized_attempts":
        actions = ", ".join(sorted({_(CAN.get(e.action, e.action)) for e in finding.events}))
        topics = ", ".join(h.text(subject) for subject in finding.subjects)
        times = h.text(plural(len(finding.events), "{n} time", "{n} times"))
        text = fill(ATTEMPTS, who=a, times=times, actions=h.text(actions), topics=topics)
    elif kind == "risk_after_approval":
        text = fill(RISK_AFTER if finding.severity == "high" else RISK_DURING, s=s)
    elif kind in FINDING:
        text = fill(FINDING[kind], s=s, a=a, days=str(stale_days))
    else:
        text = h.text(finding.detail)
    label, tone = SEVERITY[finding.severity]
    return (f'<li><span class="pill {tone}">{t(label)}</span> {text} '
            f'<span class="small">({h.code(finding.kind)}: {h.text(finding.detail)})</span></li>')


def _detail(state: SubjectState, result: ReplayResult, report: AuditReport, data: Dataset, base: str) -> str:
    decision = state.last_authorized_decision
    out = ['<section id="detail" class="card" aria-labelledby="detail-title" tabindex="-1">',
           f'<h2 id="detail-title" class="flush subject-title">{h.text(state.subject)} '
           f"{status_pill(state.current_status)}</h2>"]
    if decision is not None:
        line = fill("{verb} {who} on {when}. It is based on:", verb=t(DECIDED[decision.action]),
                    who=_who(decision.actor, decision.roles), when=when(decision.timestamp))
        out.append(f'<p class="lead">{line}</p>')
        out.append(_quotes(decision.evidence, base, css="big-quote"))
    else:
        out.append(f'<p class="lead">{t("No one with the right role has decided yet.")}</p>')
    facts = []
    if state.pending_approval:
        why = t(" Someone asked to reconsider.") if state.reconsideration_requested else ""
        waiting = fill("a decision from {roles}.", roles=role_words(state.pending_approval))
        facts.append(f"<dt>{t('Waiting for')}</dt><dd>{waiting}{why}</dd>")
    if state.risks:
        risks = "".join(f"<li>{h.text(r.note)} <span class=\"small\">({t('raised by {who}', who=r.actor)})</span>"
                        "</li>" for r in state.risks)
        facts.append(f'<dt>{t("Open risks")}</dt><dd><ul class="plain">{risks}</ul></dd>')
    if state.dependencies:
        facts.append(f"<dt>{t('Depends on')}</dt><dd>" + ", ".join(h.text(d) for d in state.dependencies) + "</dd>")
    if facts:
        out.append(f'<dl class="facts">{"".join(facts)}</dl>')
    mine = [f for f in report.findings if state.subject in f.subjects]
    if mine:
        out += [f"<h3>{t('Needs attention')}</h3>",
                f'<ul class="attention-list">{"".join(sentence(f, report.stale_days) for f in mine)}</ul>']
    entries = [e for e in result.audit_log if e.subject == state.subject]
    out += [f"<h3>{t('How we got here')}</h3>", f'<p class="small">{t(DOTS)}</p>',
            _timeline(entries, data, base), "</section>"]
    return "\n".join(out)


def _changes(report: ChangesReport) -> str:
    topics = plural(report.subjects_changed, "{n} topic", "{n} topics")
    summary = plural(report.events_in_period, "{n} event in this period; {topics} changed.",
                     "{n} events in this period; {topics} changed.", topics=topics)
    out = [f'<p class="small">{h.text(summary)}</p>']
    if not report.subjects:
        out.append(f'<p class="card">{t("Nothing changed in that time.")}</p>')
    for subject in report.subjects:
        before = status_pill(subject.status_before) if subject.status_before else f'<span class="muted">{t("new")}</span>'
        items = []
        for change in subject.changes:
            line = f"<strong>{t(CHANGE.get(change.kind, change.kind))}</strong>"
            if change.kind == "decision" and change.status_before and change.status_after:
                line += f": {status_pill(change.status_before)} &rarr; {status_pill(change.status_after)}"
            people = "; ".join(fill("{who} {action} on {when}", who=_who(e.actor, e.roles),
                                    action=t(ACTION.get(e.action, e.action)), when=when(e.timestamp))
                               for e in change.events)
            items.append(f"<li>{line}<br>{people} <span class=\"small\">({h.text(change.detail)})</span></li>")
        out.append(f'<div class="card"><h3 class="flush">{h.text(subject.subject)}</h3>'
                   f"<p>{before} &rarr; {status_pill(subject.status_after)}</p>"
                   f'<ul class="attention-list">{"".join(items)}</ul></div>')
    return "\n".join(out)


def topics(result: ReplayResult, params: dict[str, str], selected: str | None) -> str:
    items = []
    for state in result.states.values():
        label, tone = STATUS[state.current_status]
        current = ' aria-current="true"' if state.subject == selected else ""
        link = href("/decisions", dataset=params.get("dataset"), subject=state.subject, as_of=params.get("as_of"))
        items.append(f'<li><a href="{link}#detail"{current}><span class="dot {tone}" aria-hidden="true"></span>'
                     f'<span>{h.text(state.subject)}<span class="sr-only">, {t(label)}</span></span></a></li>')
    return (f'<nav class="topics" aria-label="{attr(_("Topics"))}"><p class="side-title">{t("Topics")}</p>'
            f'<ul>{"".join(items)}</ul></nav>\n')


def _form(ws: Workspace, inventory: Inventory, params: dict[str, str]) -> str:
    chosen = params.get("dataset") or ""
    options = [(d, _("This folder") if d == "." else d) for d in inventory.datasets]
    if chosen and chosen not in inventory.datasets and ws.inside(chosen):
        options.insert(0, (chosen, chosen))
    if not chosen and len(options) == 1:
        chosen = options[0][0]
    return "\n".join([
        '<form class="card" method="get" action="/decisions">',
        '<div class="row">',
        select("dataset", _("Decision log"), options, chosen, required=True,
               placeholder="" if chosen else _("Choose a decision log"),
               hint=_("A folder with events.jsonl, policy.json and evidence.jsonl")),
        f'<button class="button" type="submit">{t("Open")}</button>',
        "</div>",
        f'<details{" open" if params.get("as_of") else ""}><summary>{t("More options")}</summary>'
        '<div class="fields">',
        text_input("as_of", _("Look at it as it was at the end of"), params.get("as_of"), kind="date",
                   hint=_("A day in UTC; leave empty for everything")),
        f"</div></details>{lang_field()}",
        "</form>",
    ])


def _changes_form(params: dict[str, str]) -> str:
    hidden = "".join(f'<input type="hidden" name="{key}" value="{attr(params[key])}">'
                     for key in ("dataset", "subject", "as_of") if params.get(key))
    return "\n".join([
        '<form class="card" method="get" action="/decisions#changes">', hidden + lang_field(), '<div class="fields">',
        text_input("from", _("After the end of"), params.get("from"), kind="date", hint=_("A day in UTC")),
        text_input("to", _("Up to the end of"), params.get("to"), kind="date", hint=_("A day in UTC")),
        "</div>", f'<div class="actions"><button class="button" type="submit">{t("Show changes")}</button></div>',
        "</form>"])


def render(ws: Workspace, inventory: Inventory, params: dict[str, str]) -> tuple[int, str, str]:
    """The page body, its status and the sidebar's topic list."""
    out = [f"<h1>{t('Team decisions')}</h1>", f'<p class="lead">{t(LEAD)}</p>', _form(ws, inventory, params)]
    if not inventory.datasets and not params.get("dataset"):
        out.append(f'<p class="muted">{t("No decision log was found in this folder.")}</p>')
    if not params.get("dataset"):
        return 200, "\n".join(out), ""
    cannot = _("We couldn't open this decision log")
    try:
        data = ws.dataset(params["dataset"])
        base = ws.show(ws.directory(params["dataset"], "The decision log"))
        at = moment(params.get("as_of"), "\"Look at it as it was\"")
        result = replay(data, at)
        report = decision_audit(data, at)
    except InputError as exc:
        out.append(error_card(cannot, str(exc)))
        return 400, "\n".join(out), ""
    except ValueError:  # includes pydantic ValidationError from replay or audit
        out.append(error_card(cannot, _("The events or the policy are not valid.")))
        return 400, "\n".join(out), ""
    status = 200
    subject = params.get("subject") or next(iter(result.states), None)
    if subject is not None and subject not in result.states:
        out.append(error_card(_("Unknown topic"), _("That topic is not in this decision log. Pick one from the list.")))
        status, subject = 404, next(iter(result.states), None)
    if report.findings:
        count = plural(len(report.findings), "{n} thing needs attention.", "{n} things need attention.")
        out.append(f'<div class="card banner" role="status"><span class="dot attention" aria-hidden="true"></span>'
                   f'<strong>{h.text(count)}</strong> <a href="#attention">{t("See what")}</a></div>')
    cutoff = fill("as it was at {when}", when=when(result.as_of)) if result.as_of else t("up to the last event")
    replayed = fill(REPLAYED, events=h.text(plural(result.events_replayed, "{n} event", "{n} events")),
                    topics=h.text(plural(len(result.states), "{n} topic", "{n} topics")), cutoff=cutoff)
    out.append(f'<p class="small">{replayed}</p>')
    if subject is None:
        out.append(f'<p class="card">{t("This decision log has no events yet.")}</p>')
    else:
        out.append(_detail(result.states[subject], result, report, data, base))
    if report.findings:
        out += ['<section id="attention" aria-labelledby="attention-title">',
                f'<h2 id="attention-title">{t("Needs attention")}</h2>',
                f'<ul class="attention-list card">{"".join(sentence(f, report.stale_days) for f in report.findings)}'
                "</ul>",
                f'<p class="small">{t("These are leads for a person to look at, not verdicts.")}</p></section>']
    out += ['<section id="changes" aria-labelledby="changes-title">',
            f'<h2 id="changes-title">{t("What changed between two dates")}</h2>', _changes_form(params)]
    if params.get("from") or params.get("to"):
        cannot = _("We couldn't compare these dates")
        try:
            start = moment(params.get("from"), "\"After the end of\"")
            end = moment(params.get("to"), "\"Up to the end of\"")
            if start is None or end is None:
                raise InputError(_("Fill in both dates."))
            if start > end:
                raise InputError(_("The first date must not be after the second."))
            out.append(_changes(decision_changes(data, start, end)))
        except InputError as exc:
            out.append(error_card(cannot, str(exc)))
            status = 400
        except ValueError:
            out.append(error_card(cannot, _("The events or the policy are not valid.")))
            status = 400
    out.append("</section>")
    return status, "\n".join(out), topics(result, params, subject)
