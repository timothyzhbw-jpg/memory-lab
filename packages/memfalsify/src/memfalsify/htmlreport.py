"""Render audit and diff reports as one self-contained HTML file each (``--format html``).

The pages carry the same information as the Markdown reports, grouped by status, with every evidence
quote's location, character range and full SHA-256. Every value from memory, evidence, reviews, git or
the command line goes through ``memcore.htmlpage``, which escapes it and marks hidden characters.
"""
from __future__ import annotations

from memcore import htmlpage as h

from .audit import SEVERITY, AuditReport, EvidenceFinding, Finding
from .corpus import HTML_GENERATOR
from .history import ClaimVersion, DiffReport
from .report import LABELS
from .verify import VerifierStats

TONE = {"challenged": "red", "conflicted": "red", "candidate_counterevidence": "amber", "disputed": "amber",
        "insufficient_evidence": "gray", "candidate_support": "blue", "supported": "green"}
RELATION_TONE = {"supports": "green", "contradicts": "red", "disputed": "amber", "unrelated": "gray"}


def status_badge(status: str) -> str:
    return h.badge(LABELS[status], TONE[status])


def _where(location: str, section: str | None) -> str:
    return h.code(location) + (f" &middot; {h.text(section)}" if section else "")


def _evidence(e: EvidenceFinding) -> str:
    who = f"confirmed by {h.text(e.reviewer)}" if e.confirmed else "candidate, unconfirmed"
    out = [f"<p>{h.badge(e.relation, RELATION_TONE[e.relation])} {who} &middot; {h.span_ref(e.span)}</p>",
           f"<p>{h.text(e.rationale)}</p>"]
    if e.opinions:
        out.append(h.items(f"{h.text(o.verifier)}: {h.badge(o.relation, RELATION_TONE[o.relation])} "
                           f"{h.text(o.rationale)}" for o in e.opinions))
    out.append(h.quote(e.span.quote))
    return "\n".join(out)


def _metadata(f: Finding) -> list[str]:
    """Frontmatter fields of the claim's file, e.g. a Cursor rule's globs, as in the Markdown report."""
    if not f.metadata:
        return []
    shown = [h.code(f"{key}: {', '.join(value) if isinstance(value, list) else value}")
             for key, value in f.metadata.items()]
    return ["<p>Frontmatter: " + " &middot; ".join(shown) + "</p>"]


def _finding(f: Finding, level: int) -> str:
    out = ['<article class="item">',
           f"<h{level}>{status_badge(f.status)} {h.code(f.memory_id)}</h{level}>",
           h.quote(f.text),
           f"<p>{_where(f.location, f.section)}</p>",
           *_metadata(f)]
    if f.evidence:
        out += ['<p class="label">Evidence</p>', h.items(_evidence(e) for e in f.evidence)]
    if f.issues:
        out += ['<p class="label">Issues</p>', h.items(f"{h.code(i.kind)}: {h.text(i.detail)}" for i in f.issues)]
    if f.proposals:
        out += ['<p class="label">Proposed actions (not applied)</p>',
                h.items(f"{h.code(p.action)}: {h.text(p.reason)}" for p in f.proposals)]
    out.append("</article>")
    return "\n".join(out)


def _status_table(summary: dict[str, int], caption: str) -> str:
    rows = [[f'<a href="#status-{s}">{h.text(LABELS[s])}</a>', str(n)] for s, n in summary.items()]
    return h.table(caption, ["Status", "Memories"], rows, numeric=[1])


def _findings(findings: list[Finding], level: int) -> str:
    """Findings grouped under one heading per status, most severe first."""
    out = []
    for status in SEVERITY:
        group = [f for f in findings if f.status == status]
        if group:
            out.append(f'<section id="status-{status}">\n'
                       f"<h{level}>{status_badge(status)} {len(group)} memor{'y' if len(group) == 1 else 'ies'}"
                       f"</h{level}>")
            out += [_finding(f, level + 1) for f in group]
            out.append("</section>")
    return "\n".join(out)


def _skipped(report: AuditReport) -> list[str]:
    if not report.skipped_evidence:
        return []
    rows = [[h.code(s.path), h.text(s.reason)] for s in report.skipped_evidence]
    return ["<section>\n<h2>Skipped evidence</h2>",
            h.table("Files not read", ["Path", "Reason"], rows), "</section>"]


def _limitations(lines: list[str]) -> list[str]:
    return ["<section>\n<h2>Limitations</h2>", h.items(h.text(line) for line in lines), "</section>"]


def _common_meta(report: AuditReport, mode: str, evidence_note: str = "") -> list[tuple[str, str]]:
    rows = [("Evidence root", h.code(report.evidence_root) + evidence_note if report.evidence_root
             else "none supplied")]
    if report.excluded:
        rows.append(("Excluded from evidence", ", ".join(h.code(p) for p in report.excluded)))
    rows += [("Verifier", h.code(report.verifier)), ("As of", h.when(report.as_of)),
             ("Mode", f"<strong>{h.text(mode)}</strong> (no memory was modified)")]
    rows += [("API usage", _usage(stats)) for stats in report.verifier_stats]
    return rows


def _usage(stats: VerifierStats) -> str:
    """One model backend's requests and reported tokens, as on the Markdown report's API usage line."""
    line = f"{h.code(stats.verifier)}: {stats.requests} request(s)"
    if stats.batched_requests:
        line += f" ({stats.batched_requests} through the Message Batches API)"
    line += f" &middot; input tokens: {stats.input_tokens}"
    if stats.cache_creation_input_tokens is not None:
        line += (f" uncached, {stats.cache_creation_input_tokens} written to the prompt cache, "
                 f"{stats.cache_read_input_tokens} read from it")
    return line + f" &middot; output tokens: {stats.output_tokens}"


def _files(files: list[str]) -> str:
    return f"{len(files)} file(s)" + (": " + ", ".join(h.code(f) for f in files) if files else "")


def to_html(report: AuditReport) -> str:
    """The audit as one HTML document: summary table, findings by status, skipped evidence, limitations."""
    meta = [("Memory", f"{h.code(report.memory_file)} ({_files(report.files_read)})"),
            *_common_meta(report, report.mode)]
    body = ["<h1>MemFalsify audit report</h1>", h.meta(meta), "<section>\n<h2>Summary</h2>"]
    if report.findings:
        body.append(_status_table(report.summary, "Memories by status"))
    else:
        body.append("<p>No claims were found in the memory.</p>")
    body.append("</section>")
    if report.findings:
        body.append(_findings(report.findings, 2))
    body += _skipped(report) + _limitations(report.limitations)
    return h.page("MemFalsify audit report", "\n".join(body), HTML_GENERATOR)


def _version(label: str, version: ClaimVersion, similarity: float | None = None) -> str:
    extra = f" (similarity {similarity:.2f})" if similarity is not None else ""
    return (f"<p><strong>{label}</strong> at {_where(version.location, version.section)}{extra}</p>\n"
            + h.quote(version.text))


def diff_to_html(report: DiffReport) -> str:
    """The diff as one HTML document: change summary, changed/added/removed claims, then their audit."""
    audit = report.audit

    def revision(rev: str, commit: str | None, files: list[str]) -> str:
        where = f"{h.code(rev)} (commit {h.code(commit[:12])})" if commit else "working tree"
        return f"{where}, {_files(files)}"

    meta = [("Memory", h.code(report.memory_file)),
            ("Base", revision(report.base.rev, report.base.commit, report.base.files)),
            ("Head", revision(report.head.rev, report.head.commit, report.head.files)),
            *_common_meta(audit, report.mode, " (as it is now)")]
    body = ["<h1>MemFalsify diff report</h1>", h.meta(meta), "<section>\n<h2>Summary</h2>"]
    rows = [[f'<a href="#change-{kind}">{kind.capitalize()}</a>' if n and kind != "unchanged"
             else kind.capitalize(), str(n)] for kind, n in report.summary.items()]
    body.append(h.table("Claims by change", ["Change", "Claims"], rows, numeric=[1]))
    touched = report.summary["added"] + report.summary["changed"]
    if touched:
        body += [f"<p>Audited the {touched} added or changed claim(s) only.</p>",
                 _status_table(audit.summary, "Added and changed memories by status")]
    else:
        body.append("<p>No claim was added or changed, so nothing was audited.</p>")
    body.append("</section>")
    for kind, title in (("changed", "Changed"), ("added", "Added"), ("removed", "Removed")):
        entries = [c for c in report.changes if c.change == kind]
        if not entries:
            continue
        body.append(f'<section id="change-{kind}">\n<h2>{title} ({len(entries)})</h2>')
        for c in entries:
            body.append('<article class="item">')
            if kind == "changed":
                body += [f"<h3>{h.code(c.old.memory_id)} &rarr; {h.code(c.new.memory_id)} {status_badge(c.status)}"
                         "</h3>", _version("Old", c.old), _version("New", c.new, c.similarity)]
            elif kind == "added":
                body += [f"<h3>{h.code(c.new.memory_id)} {status_badge(c.status)}</h3>", h.quote(c.new.text),
                         f"<p>{_where(c.new.location, c.new.section)}</p>"]
            else:
                body += [f"<h3>{h.code(c.old.memory_id)}</h3>", h.quote(c.old.text),
                         f"<p>Was at {_where(c.old.location, c.old.section)}</p>"]
            body.append("</article>")
        body.append("</section>")
    if audit.findings:
        body += ["<section>\n<h2>Audit of added and changed claims</h2>", _findings(audit.findings, 3), "</section>"]
    body += _skipped(audit) + _limitations(report.limitations + audit.limitations)
    return h.page("MemFalsify diff report", "\n".join(body), HTML_GENERATOR)
