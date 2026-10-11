"""Render an AuditReport as a Markdown document a reviewer can read top to bottom."""
from __future__ import annotations

import re
from collections.abc import Callable

from memcore import EvidenceSpan
from memcore.sidecar import one_line

from .audit import AuditReport, Finding
from .corpus import REPORT_HEADER
from .gitstore import show_text

LABELS = {
    "challenged": "CHALLENGED",
    "conflicted": "CONFLICTED",
    "candidate_counterevidence": "POSSIBLE COUNTEREVIDENCE",
    "disputed": "DISPUTED",
    "insufficient_evidence": "NO EVIDENCE",
    "candidate_support": "POSSIBLE SUPPORT",
    "supported": "SUPPORTED",
}


def _quote(text: str, *, scrub: bool = False) -> str:
    if scrub:
        text = show_text(text)
    return "> " + text.replace("\n", "\n> ")


def _as_is(text: str) -> str:
    return text


def source(span: EvidenceSpan, show: Callable[[str], str] = _as_is) -> str:
    """Where a quote is: ``path:line`` and the file's hash; for a transcript or OCR sidecar, first the position in
    the media file, then the sidecar line, its hash, and the media file's hash when it was checked."""
    if span.media is None:
        return f"`{show(one_line(span.location))}` · sha256 `{span.sha256[:12]}`"
    media = span.media
    when = f" ({media.time_range})" if media.time_range else ""
    checked = (f"media sha256 `{media.sha256[:12]}`" if media.sha256
               else "media file not present, binding not checked" if media.path else "no media file named or found")
    return (f"`{show(one_line(span.citation))}`{when} · {media.label} `{show(one_line(span.location))}` · "
            f"sha256 `{span.sha256[:12]}` · {checked}")


def skipped_line(path: str, reason: str, show: Callable[[str], str] = _as_is) -> str:
    """One bullet. A file name or a diagnostic is untrusted and must not break the list or the terminal."""
    return f"- `{show(one_line(path))}`: {show(one_line(reason))}"


def quote_lines(quote: str, show: Callable[[str], str] = _as_is, indent: str = "  ") -> list[str]:
    """A blockquote line per line of the quote (a transcript cue can span several lines)."""
    return [f"{indent}> {show(line)}" for line in re.split(r"\r\n|\r|\n", quote)]


def _finding(f: Finding, *, scrub: bool = False) -> list[str]:
    def show(text: str) -> str:
        return show_text(text) if scrub else text

    where = f"`{show(f.location)}`" + (f" · {show(f.section)}" if f.section else "")
    out = [f"### {LABELS[f.status]} · `{f.memory_id}`", "", _quote(f.text, scrub=scrub), "", where, ""]
    if f.evidence:
        out.append("**Evidence**")
        for e in f.evidence:
            who = f"confirmed by {show(e.reviewer)}" if e.confirmed else "candidate, unconfirmed"
            out.append(f"- {show(e.relation)} ({who}) · {source(e.span, show)} · {show(e.rationale)}")
            out += [f"  - {show(o.verifier)}: {show(o.relation)} · {show(o.rationale)}" for o in e.opinions]
            out += quote_lines(e.span.quote, show)
        out.append("")
    if f.issues:
        out.append("**Issues**")
        out += [f"- {show(i.kind)}: {show(i.detail)}" for i in f.issues]
        out.append("")
    if f.proposals:
        out.append("**Proposed actions** (not applied)")
        out += [f"- `{p.action}`: {show(p.reason)}" for p in f.proposals]
        out.append("")
    return out


def to_markdown(report: AuditReport) -> str:
    out = [REPORT_HEADER, "",
           f"- Memory: `{report.memory_file}` ({len(report.files_read)} file(s): "
           + ", ".join(f"`{p}`" for p in report.files_read) + ")",
           f"- Evidence root: `{report.evidence_root}`" if report.evidence_root else "- Evidence root: none supplied",
           *(["- Excluded from evidence: " + ", ".join(f"`{p}`" for p in report.excluded)] if report.excluded else []),
           f"- Verifier: `{report.verifier}` · as of {report.as_of} · mode: **{report.mode}** (no memory was modified)",
           "", "| Status | Memories |", "|---|---|"]
    out += [f"| {LABELS[s]} | {n} |" for s, n in report.summary.items()]
    out.append("")
    for finding in report.findings:
        out += _finding(finding)
    if report.skipped_evidence:
        out += ["## Skipped evidence", ""]
        out += [skipped_line(s.path, s.reason) for s in report.skipped_evidence]
        out.append("")
    out += ["## Limitations", ""] + [f"- {line}" for line in report.limitations]
    return "\n".join(out) + "\n"
