"""Render an AuditReport as a Markdown document a reviewer can read top to bottom."""
from __future__ import annotations

import re

from .audit import AuditReport, Finding
from .corpus import REPORT_HEADER
from .formats import Value
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


def _code(text: str) -> str:
    """Untrusted text as one Markdown code span, whatever backticks it contains."""
    text = " ".join(show_text(text).split())
    fence = "`" * (max((len(run) for run in re.findall("`+", text)), default=0) + 1)
    pad = " " if text.startswith("`") or text.endswith("`") else ""
    return f"{fence}{pad}{text}{pad}{fence}"


def metadata_line(metadata: dict[str, Value]) -> str:
    """Frontmatter fields as one line, e.g. ``Frontmatter: `globs: src/**` · `alwaysApply: false` ``."""
    shown = [_code(f"{key}: {', '.join(value) if isinstance(value, list) else value}")
             for key, value in metadata.items()]
    return "Frontmatter: " + " · ".join(shown)


def _finding(f: Finding, *, scrub: bool = False) -> list[str]:
    def show(text: str) -> str:
        return show_text(text) if scrub else text

    where = f"`{show(f.location)}`" + (f" · {show(f.section)}" if f.section else "")
    out = [f"### {LABELS[f.status]} · `{f.memory_id}`", "", _quote(f.text, scrub=scrub), "", where, ""]
    if f.metadata:
        out += [metadata_line(f.metadata), ""]
    if f.evidence:
        out.append("**Evidence**")
        for e in f.evidence:
            who = f"confirmed by {show(e.reviewer)}" if e.confirmed else "candidate, unconfirmed"
            out.append(f"- {show(e.relation)} ({who}) · `{show(e.span.location)}` · "
                       f"sha256 `{e.span.sha256[:12]}` · {show(e.rationale)}")
            out += [f"  - {show(o.verifier)}: {show(o.relation)} · {show(o.rationale)}" for o in e.opinions]
            out.append(f"  > {show(e.span.quote)}")
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
        out += [f"- `{s.path}`: {s.reason}" for s in report.skipped_evidence]
        out.append("")
    out += ["## Limitations", ""] + [f"- {line}" for line in report.limitations]
    return "\n".join(out) + "\n"
