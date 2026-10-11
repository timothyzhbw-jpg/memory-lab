"""Render an AuditReport as a Markdown document a reviewer can read top to bottom."""
from __future__ import annotations

from .audit import AuditReport, Finding
from .corpus import REPORT_HEADER

LABELS = {
    "challenged": "CHALLENGED",
    "conflicted": "CONFLICTED",
    "candidate_counterevidence": "POSSIBLE COUNTEREVIDENCE",
    "disputed": "DISPUTED",
    "insufficient_evidence": "NO EVIDENCE",
    "candidate_support": "POSSIBLE SUPPORT",
    "supported": "SUPPORTED",
}


def _quote(text: str) -> str:
    return "> " + text.replace("\n", "\n> ")


def _finding(f: Finding) -> list[str]:
    where = f"`{f.location}`" + (f" · {f.section}" if f.section else "")
    out = [f"### {LABELS[f.status]} · `{f.memory_id}`", "", _quote(f.text), "", where, ""]
    if f.evidence:
        out.append("**Evidence**")
        for e in f.evidence:
            who = f"confirmed by {e.reviewer}" if e.confirmed else "candidate, unconfirmed"
            out.append(f"- {e.relation} ({who}) · `{e.span.location}` · sha256 `{e.span.sha256[:12]}` · {e.rationale}")
            out.append(f"  > {e.span.quote}")
        out.append("")
    if f.issues:
        out.append("**Issues**")
        out += [f"- {i.kind}: {i.detail}" for i in f.issues]
        out.append("")
    if f.proposals:
        out.append("**Proposed actions** (not applied)")
        out += [f"- `{p.action}`: {p.reason}" for p in f.proposals]
        out.append("")
    return out


def to_markdown(report: AuditReport) -> str:
    out = [REPORT_HEADER, "",
           f"- Memory: `{report.memory_file}` ({len(report.files_read)} file(s): "
           + ", ".join(f"`{p}`" for p in report.files_read) + ")",
           f"- Evidence root: `{report.evidence_root}`" if report.evidence_root else "- Evidence root: none supplied",
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
