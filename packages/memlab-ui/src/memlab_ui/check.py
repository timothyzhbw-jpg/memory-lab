"""The "Check an agent file" page: one form, then the audit (or the diff since a revision) in plain words.

The findings are the ones ``memfalsify audit`` and ``memfalsify diff`` report. Flagged statements come first,
each next to the source that seems to disagree; the rest are folded away. Nothing is written.
"""
from __future__ import annotations

import re
from datetime import date
from pathlib import PurePosixPath

from memcore import htmlpage as h
from memfalsify import (ATTENTION, AuditReport, DiffReport, EvidenceFinding, Finding, HistoryError, VerifierError,
                        audit, memory_diff)
from memfalsify import jev, llm
from memfalsify.audit import SEVERITY
# The verifier choices and passage limits are the CLI's (internal, as in the MCP server): same defaults.
from memfalsify.cli import VerifierChoice, build_verifier, preselection
from memfalsify.gitstore import check_revision

from .i18n import _
from .pages import (error_card, fill, lang_field, location_code, plural, quote, select, span_detail,
                    span_place, text_input, where)
from .workspace import InputError, Inventory, Workspace

VERIFIERS = {
    "lexical": "Quick word match (offline, free)",
    "llm": "Claude (sends text to Anthropic; costs money)",
    "jev": "Jev (sends text to TypeSafe AI; costs money)",
    "dual": "Claude and Jev (sends text to both; costs money)",
    "cascade": "Jev, then Claude when unsure (sends text to both; costs money)",
}
WHOLE = "."
FINE = ("supported", "candidate_support")
PILL = {
    "challenged": ("Confirmed conflict", "attention"),
    "conflicted": ("Confirmed both ways", "attention"),
    "candidate_counterevidence": ("Possible conflict", "attention"),
    "disputed": ("Checkers disagree", "attention"),
    "supported": ("Confirmed by a reviewer", "fine"),
    "candidate_support": ("Probably fine", "fine"),
    "insufficient_evidence": ("Can't tell", "grey"),
}
ADVICE = {
    "challenged": "A reviewer confirmed the source is right: update or remove this line in {file}.",
    "conflicted": "Reviewers found sources on both sides: decide which one is right, then update {file}.",
    "candidate_counterevidence": ("If the source is right, update this line in {file}; if it is about something "
                                  "else, you can ignore this."),
    "disputed": "The two checkers disagree about this source: read both and decide.",
}
WHY = (
    ("opposing terms", "They use opposite words."),
    ("different values", "The numbers are different."),
    ("opposite modality", "One says it must (or always) happen, the other says it need not (or only sometimes)."),
    ("same subject, but only one side is negated", "Only one of them says \"not\"."),
)
ISSUES = {
    "overgeneralization": "Uses a word like \"always\" or \"never\"; rules like that often have exceptions.",
    "relative_time": "Uses a relative time such as \"next week\" without a date, so it goes stale silently.",
    "possibly_stale": "Mentions a date long ago; it may be out of date.",
    "inference_marker": "Sounds like a guess (\"probably\", \"seems\").",
    "internal_conflict": "May disagree with another statement in the same file.",
    "duplicate": "Says the same thing as another statement.",
}
TILES = (
    ("attention", "Look at these", "Something in your project seems to disagree."),
    ("fine", "Probably fine", "Your project says the same thing."),
    ("grey", "Can't tell", "Nothing in your project talks about it."),
)
DOCS = {".md", ".markdown", ".txt", ".rst", ".adoc", ".mdx", ".vtt", ".srt", ".json"}
LEAD = ("Pick a file and press Check. Each statement in it is compared with your project, and anything that seems "
        "to disagree is shown side by side.")
HONEST = ("About this check: the quick check compares words. It can miss a source that says the same thing in other "
          "words, and it can flag a sentence that only looks like a disagreement. A flag means \"read this\", not "
          "\"this is wrong\". Only a person can confirm a statement, by recording a review.")
MODEL_NOTE = ("About this check: a model judged passages that share words with each statement. Models can be "
              "wrong, and passages that share few words were never shown to it. Only a person can confirm a "
              "statement, by recording a review.")
SWITCHED_OFF = ("Model checks are switched off. They send your files' text to an outside service and cost money, so "
                "they work only when memlab-ui is started with --allow-model-verifiers.")
MODEL_FAILED = ("The model check failed. Check that its optional SDK is installed, the API key is set and the "
                "service is reachable.")
NOT_GIT = "\"Changed since\" works when the project folder is a git repository."
NOT_GIT_ERROR = "\"Changed since\" works only when the project folder is a git repository."
BAD_REVISION = "\"Changed since\" must be a branch, tag or commit name that does not start with '-'."
REVIEWS_HINT = "Reviews a person recorded; they must point into the folder you compare against"
UNCHECKED = "Nothing in the folder you compared against mentions this. That means unchecked, not wrong."
SUBTITLE = ("{statements} from {file}{linked}, compared with {against} · as of {date} · {method} · "
            "nothing was changed")
SINCE = ("Since {rev}: {added} added, {changed} changed, {removed} removed, {unchanged} unchanged (not checked "
         "again) · as of {date} · nothing was changed")


class Refused(Exception):
    """A request for something this server does not do (a model verifier without the flag)."""


def t(message: str, **values: object) -> str:
    """A translated message as escaped text."""
    return h.text(_(message, **values))


# --- the form -----------------------------------------------------------------------------------------

def form(ws: Workspace, inventory: Inventory, params: dict[str, str], *, models: bool) -> str:
    chosen = params.get("file") or ""
    files = [(f.path, f"{f.path} ({_(f.agent) if f.agent == 'Agent memory' else f.agent})") for f in inventory.files]
    if chosen and chosen not in {value for value, _label in files} and ws.inside(chosen):
        files.insert(0, (chosen, chosen))
    if not chosen and len(files) == 1:
        chosen = files[0][0]
    evidence = params.get("evidence") or WHOLE
    folders = [(WHOLE, _("The whole project (recommended)"))]
    folders += [(d, _("Only {path}/", path=d)) for d in inventory.directories]
    if evidence not in {value for value, _label in folders} and ws.inside(evidence):
        folders.insert(1, (evidence, _("Only {path}", path=evidence)))
    reviews = [(r, r) for r in inventory.reviews]
    if params.get("reviews") and params["reviews"] not in inventory.reviews and ws.inside(params["reviews"]):
        reviews.insert(0, (params["reviews"], params["reviews"]))
    extra = any(params.get(k) for k in ("since", "as_of", "reviews")) or params.get("verifier", "lexical") != "lexical"
    more = []
    if ws.is_git:
        more.append(text_input("since", _("Only statements changed since"), params.get("since"),
                               hint=_("A git branch, tag or commit, such as main"), placeholder="main"))
    else:
        more.append(f'<p class="small field">{t(NOT_GIT)}</p>')
    more.append(text_input("as_of", _("Treat today as"), params.get("as_of"), kind="date",
                           hint=_("Old dates in the file are judged from this day")))
    more.append(select("reviews", _("Reviews file"), reviews, params.get("reviews"), placeholder=_("None"),
                       hint=_(REVIEWS_HINT)))
    if models:
        more.append(select("verifier", _("How to compare"), [(k, _(v)) for k, v in VERIFIERS.items()],
                           params.get("verifier", "lexical"),
                           hint=_("Model checks send your text to an outside service")))
    return "\n".join([
        '<form class="card" method="get" action="/check">',
        '<div class="fields">',
        select("file", _("Agent file"), files, chosen, required=True, placeholder="" if chosen else _("Choose a file")),
        select("evidence", _("Compare against"), folders, evidence),
        "</div>",
        f'<details{" open" if extra else ""}><summary>{t("More options")}</summary><div class="fields">'
        f'{"".join(more)}</div></details>',
        f'<div class="actions"><button class="button" type="submit">{t("Check")}</button></div>{lang_field()}',
        "</form>",
    ])


def header() -> str:
    return f'<h1>{t("Check an agent file")}</h1>\n<p class="lead">{t(LEAD)}</p>'


# --- running the check --------------------------------------------------------------------------------

def choice_of(params: dict[str, str], models: bool) -> VerifierChoice:
    value = params.get("verifier") or "lexical"
    if value not in VERIFIERS:
        raise InputError(_("Pick how to compare from the list."))
    if value != "lexical" and not models:
        raise Refused(_(SWITCHED_OFF))
    return VerifierChoice(value)


def as_of_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise InputError(_("\"Treat today as\" must be a date like 2026-10-10.")) from None


def run(ws: Workspace, params: dict[str, str], *, models: bool) -> AuditReport | DiffReport:
    choice = choice_of(params, models)
    memory = ws.file(params["file"], "The agent file", instructions=True)
    evidence = ws.directory(params.get("evidence") or WHOLE, "The folder to compare against")
    reviews = ws.file(params["reviews"], "The reviews file") if params.get("reviews") else None
    reference = as_of_date(params.get("as_of"))
    since = params.get("since") or None
    if since is not None:
        if not ws.is_git:
            raise InputError(_(NOT_GIT_ERROR))
        try:
            check_revision(since)
        except HistoryError:
            raise InputError(_(BAD_REVISION)) from None
    try:
        judged = {"verifier": build_verifier(choice, llm.DEFAULT_MODEL, jev.DEFAULT_MODEL), **preselection(choice)}
        if since is None:
            report = audit(memory, evidence, reviews, as_of=reference, **judged)
            return report.model_copy(update={"memory_file": ws.show(memory), "evidence_root": ws.show(evidence)})
        diff = memory_diff(memory, since, None, evidence_root=evidence, reviews=reviews, as_of=reference, **judged)
    except VerifierError:
        raise InputError(_(MODEL_FAILED)) from None
    except HistoryError as exc:
        raise InputError(ws.history_failure(exc)) from None
    except (ValueError, OSError) as exc:
        raise InputError(ws.failure(exc)) from None
    shown = ws.show(memory)
    return diff.model_copy(update={"memory_file": shown, "audit": diff.audit.model_copy(
        update={"memory_file": shown, "evidence_root": ws.show(evidence)})})


# --- the results --------------------------------------------------------------------------------------

def _prefix_ranges(text: str, stem: str) -> list[tuple[int, int]]:
    pattern = (r"(?<!\w)" + re.escape(stem) + r"\w*") if stem.isascii() else re.escape(stem)
    return [m.span() for m in re.finditer(pattern, text, re.IGNORECASE)]


def _phrase_ranges(text: str, phrase: str) -> list[tuple[int, int]]:
    pattern = (r"(?<!\w)" + re.escape(phrase) + r"(?!\w)") if phrase.isascii() else re.escape(phrase)
    return [m.span() for m in re.finditer(pattern, text, re.IGNORECASE)]


_NUMBER = re.compile(r"\d+(?:[.,]\d+)*")


def _number_ranges(text: str, other: str) -> list[tuple[int, int]]:
    theirs = {m.group().replace(",", "") for m in _NUMBER.finditer(other)}
    return [m.span() for m in _NUMBER.finditer(text) if m.group().replace(",", "") not in theirs]


def marks(e: EvidenceFinding, claim: str, verifier: str) -> tuple[list, list]:
    """What to highlight in the claim and in the quote: the opposing words, values or modal words the lexical
    verifier named. Only for its own unconfirmed judgments (a review's rationale is a person's note)."""
    if e.confirmed or verifier != "lexical":
        return [], []
    source, why = e.span.quote, e.rationale
    if found := re.match(r"opposing terms: claim '(.+?)…' vs source '(.+?)…'", why):
        return _prefix_ranges(claim, found[1]), _prefix_ranges(source, found[2])
    if found := re.match(r"opposite modality: claim '(.+?)' vs source '(.+?)'", why):
        return _phrase_ranges(claim, found[1]), _phrase_ranges(source, found[2])
    if why.startswith("different values"):
        return _number_ranges(claim, source), _number_ranges(source, claim)
    return [], []


def _why(e: EvidenceFinding, verifier: str) -> str:
    if e.confirmed:
        return h.text(_("{who} confirmed this in a review.", who=e.reviewer or _("A reviewer")))
    if verifier == "lexical":
        for prefix, words in WHY:
            if e.rationale.startswith(prefix):
                return t(words)
    return h.text(_("Why: {why}", why=e.rationale))


def _source_label(e: EvidenceFinding, base: str | None) -> str:
    docs = PurePosixPath(e.span.path).suffix.lower() in DOCS
    if e.relation == "supports":
        who = "Your docs agree" if docs else "Your project agrees"
    else:
        who = "Your docs say" if docs else "Your project says"
    return f'<p class="side-label">{t(who)} &middot; {span_place(e.span, base)}</p>'


def _claim_place(memory_file: str, location: str) -> str:
    path, line = where(location)
    folder = PurePosixPath(memory_file).parent.as_posix()
    shown = path if folder in (".", "") else f"{folder}/{path}"
    return location_code(shown, line)


def _issues(f: Finding) -> str:
    if not f.issues:
        return ""
    items = "".join(f"<li>{t(ISSUES[i.kind]) if i.kind in ISSUES else h.text(i.kind)} "
                    f'<span class="small">({h.text(i.detail)})</span></li>' for i in f.issues)
    return f'<ul class="issues">{items}</ul>'


def _pill(status: str) -> str:
    label, tone = PILL[status]
    return f'<span class="pill {tone}"><span class="dot {tone}" aria-hidden="true"></span>{t(label)}</span>'


def _heading(f: Finding, tag: str) -> str:
    return f'<h4>{_pill(f.status)}{tag}<span class="sr-only"> {t("at {where}", where=f.location)}</span></h4>'


def _agent_says(report: AuditReport, f: Finding) -> str:
    place = _claim_place(report.memory_file, f.location)
    return f'<p class="side-label">{t("Your agent file says")} &middot; {place}</p>'


def _flagged(f: Finding, report: AuditReport, tag: str = "") -> str:
    against = [e for e in f.evidence if e.relation in ("contradicts", "disputed")]
    other = [e for e in f.evidence if e.relation == "supports"]
    base = report.evidence_root
    file_name = PurePosixPath(where(f.location)[0]).name
    first = against[0] if against else None
    claim_marks, source_marks = marks(first, f.text, report.verifier) if first else ([], [])
    out = ['<article class="card finding">', _heading(f, tag), '<div class="compare">',
           f"<div>{_agent_says(report, f)}{quote(f.text, claim_marks)}</div>"]
    if first is not None:
        out.append(f"<div>{_source_label(first, base)}{quote(first.span.quote, source_marks)}</div>")
    out.append("</div>")
    if first is not None:
        out.append(f'<p class="small">{_why(first, report.verifier)}</p>{span_detail(first.span, base)}')
    out.append(f'<p class="advice">{t(ADVICE[f.status], file=file_name)}</p>')
    rest = against[1:] + other
    if rest:
        items = "".join(f'<div class="more">{_source_label(e, base)}{quote(e.span.quote)}'
                        f'<p class="small">{_why(e, report.verifier)}</p>{span_detail(e.span, base)}</div>'
                        for e in rest)
        out.append(f"<details><summary>{h.text(plural(len(rest), '{n} more source', '{n} more sources'))}"
                   f"</summary>{items}</details>")
    out.append(_issues(f))
    out.append(f'<p class="small">{t("Statement ID {id}", id=f.memory_id)}</p></article>')
    return "\n".join(out)


def _other(f: Finding, report: AuditReport, tag: str = "") -> str:
    out = ['<article class="card">', _heading(f, tag), _agent_says(report, f), quote(f.text)]
    support = [e for e in f.evidence if e.relation == "supports"]
    if support:
        e = support[0]
        out += [f'<div class="more">{_source_label(e, report.evidence_root)}{quote(e.span.quote)}',
                span_detail(e.span, report.evidence_root), "</div>"]
        if len(support) > 1:
            more = plural(len(support) - 1, "{n} more source agrees.", "{n} more sources agree.")
            out.append(f'<p class="small">{h.text(more)}</p>')
    elif f.status == "insufficient_evidence":
        out.append(f'<p class="small">{t(UNCHECKED)}</p>')
    out.append(_issues(f))
    out.append("</article>")
    return "\n".join(out)


def _tiles(findings: list[Finding]) -> str:
    counts = {"attention": sum(f.status in ATTENTION for f in findings),
              "fine": sum(f.status in FINE for f in findings),
              "grey": sum(f.status == "insufficient_evidence" for f in findings)}
    out = [f'<ul class="tiles" aria-label="{h.text(_("Summary"))}">']
    for tone, name, why in TILES:
        number = f'<span class="num">{counts[tone]}</span>'
        if counts[tone]:
            target = "look" if tone == "attention" else "others"
            number = f'<a href="#{target}">{number}<span class="sr-only"> {t(name)}</span></a>'
        out.append(f'<li class="tile"><span class="name"><span class="dot {tone}" aria-hidden="true"></span>'
                   f"{t(name)}</span>{number}<p>{t(why)}</p></li>")
    out.append("</ul>")
    return "\n".join(out)


def _findings(report: AuditReport, tags: dict[str, str] | None = None) -> list[str]:
    tags = tags or {}
    order = {status: index for index, status in enumerate(SEVERITY)}
    findings = sorted(report.findings, key=lambda f: order[f.status])
    flagged = [f for f in findings if f.status in ATTENTION]
    rest = [f for f in findings if f.status not in ATTENTION]
    out = [_tiles(report.findings)]
    if flagged:
        out.append(f'<h3 id="look">{t("Look at these")}</h3>')
        out += [_flagged(f, report, tags.get(f.memory_id, "")) for f in flagged]
    elif report.findings:
        out.append(f'<p class="card">{t("Nothing to look at: no statement seems to disagree with your project.")}</p>')
    if rest:
        label = plural(len(rest), "Show the {n} other statement", "Show the {n} other statements")
        out.append(f'<details class="others" id="others"><summary>{h.text(label)}</summary>')
        out += [_other(f, report, tags.get(f.memory_id, "")) for f in rest]
        out.append("</details>")
    return out


def _skipped(report: AuditReport) -> list[str]:
    if not report.skipped_evidence:
        return []
    rows = "".join(f'<tr><th scope="row">{h.code(s.path)}</th><td>{h.text(s.reason)}</td></tr>'
                   for s in report.skipped_evidence)
    label = plural(len(report.skipped_evidence), "{n} file was not read", "{n} files were not read")
    return [f"<details><summary>{h.text(label)}</summary><div class=\"card scroll\"><table>"
            f"<caption>{t('Files left out of the comparison')}</caption>"
            f'<thead><tr><th scope="col">{t("File")}</th><th scope="col">{t("Why")}</th></tr></thead>'
            f"<tbody>{rows}</tbody></table></div></details>"]


def _subtitle(report: AuditReport) -> str:
    against = t("the whole project") if report.evidence_root in (None, WHOLE) else h.code(report.evidence_root)
    method = _(VERIFIERS.get(report.verifier, report.verifier)).split(" (")[0].split("\uff08")[0]
    linked = len(report.files_read) - 1
    files = h.text(_(" and {linked}", linked=plural(linked, "{n} linked file", "{n} linked files"))) if linked > 0 \
        else ""
    statements = h.text(plural(len(report.findings), "{n} statement", "{n} statements"))
    line = fill(SUBTITLE, statements=statements, file=h.code(report.memory_file), linked=files, against=against,
                date=h.when(report.as_of), method=h.text(method))
    return f'<p class="small">{line}</p>'


def _title(count: int, name: str, changed: bool = False) -> str:
    if count and changed:
        return h.text(plural(count, "{n} new or changed thing in {name} may be out of date",
                             "{n} new or changed things in {name} may be out of date", name=name))
    if count:
        return h.text(plural(count, "{n} thing in {name} may be out of date",
                             "{n} things in {name} may be out of date", name=name))
    if changed:
        return t("Nothing new or changed in {name} looks out of date", name=name)
    return t("Nothing in {name} looks out of date", name=name)


def results(report: AuditReport | DiffReport) -> str:
    if isinstance(report, DiffReport):
        return diff_results(report)
    name = PurePosixPath(report.memory_file).name
    flagged = sum(f.status in ATTENTION for f in report.findings)
    out = ['<section id="results" aria-labelledby="results-title">',
           f'<h2 id="results-title">{_title(flagged, name)}</h2>', _subtitle(report)]
    if not report.findings:
        out.append(f'<p class="card">{t("No statements were found in this file.")}</p>')
    out += _findings(report)
    out += _skipped(report)
    out.append(f'<p class="card small">{t(HONEST if report.verifier == "lexical" else MODEL_NOTE)}</p>')
    out.append("</section>")
    return "\n".join(out)


def diff_results(report: DiffReport) -> str:
    audit_report = report.audit
    name = PurePosixPath(report.memory_file).name
    flagged = sum(f.status in ATTENTION for f in audit_report.findings)
    summary = report.summary
    tags = {}
    for c in report.changes:
        if c.change == "added" and c.new:
            tags[c.new.memory_id] = f' <span class="pill grey">{t("New")}</span>'
        elif c.change == "changed" and c.new and c.old:
            tags[c.new.memory_id] = f' <span class="pill grey">{t("Changed")}</span>'
    since = fill(SINCE, rev=h.code(report.base.rev), added=str(summary["added"]), changed=str(summary["changed"]),
                 removed=str(summary["removed"]), unchanged=str(summary["unchanged"]), date=h.when(audit_report.as_of))
    out = ['<section id="results" aria-labelledby="results-title">',
           f'<h2 id="results-title">{_title(flagged, name, changed=True)}</h2>', f'<p class="small">{since}</p>']
    if summary["added"] + summary["changed"]:
        out += _findings(audit_report, tags)
    else:
        out.append(f'<p class="card">{t("No statement was added or changed, so nothing was checked.")}</p>')
    changed = [c for c in report.changes if c.change == "changed" and c.old and c.new]
    if changed:
        items = "".join(f'<div class="card"><p class="side-label">{t("It used to say")} &middot; '
                        f"{location_code(*where(c.old.location))}</p>{quote(c.old.text)}"
                        f'<p class="side-label">{t("Now it says")} &middot; {location_code(*where(c.new.location))}'
                        f"</p>{quote(c.new.text)}</div>" for c in changed)
        label = plural(len(changed), "{n} changed statement: before and after",
                       "{n} changed statements: before and after")
        out.append(f"<details><summary>{h.text(label)}</summary>{items}</details>")
    removed = [c for c in report.changes if c.change == "removed" and c.old]
    if removed:
        items = "".join(f'<div class="card"><p class="side-label">{t("Removed")} &middot; '
                        f"{fill('was at {where}', where=location_code(*where(c.old.location)))}"
                        f"</p>{quote(c.old.text)}</div>" for c in removed)
        label = plural(len(removed), "{n} statement was removed", "{n} statements were removed")
        out.append(f"<details><summary>{h.text(label)}</summary>{items}</details>")
    out += _skipped(audit_report)
    out.append(f'<p class="card small">{t(HONEST if audit_report.verifier == "lexical" else MODEL_NOTE)}</p>')
    out.append("</section>")
    return "\n".join(out)


def render(ws: Workspace, inventory: Inventory, params: dict[str, str], *, models: bool) -> tuple[int, str]:
    """The page body and its status: 200, 400 for a value the page refuses, 403 for a switched-off feature."""
    status, below = 200, []
    if params.get("file"):
        try:
            below.append(results(run(ws, params, models=models)))
        except Refused as exc:
            status, below = 403, [error_card(_("This check is switched off"), str(exc))]
        except InputError as exc:
            status, below = 400, [error_card(_("We couldn't run this check"), str(exc))]
    return status, "\n".join([header(), form(ws, inventory, params, models=models)] + below)
