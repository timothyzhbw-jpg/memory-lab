"""The claims of an audit that need a look, short enough to read in a terminal: both quotes and why they were paired.

Everything from a memory file, evidence or a file name is untrusted and goes through ``diagnostic_line``.
"""
from __future__ import annotations

from memcore.terminal import diagnostic_line

from .audit import ATTENTION, MISSING_REFERENCE, AuditReport, AuditSet, EvidenceFinding, Finding, has_open_missing_reference
from .report import LABELS

_WIDTH = 100
_SHOWN_CONFIRMED = 2
FOOTER = ('A flag means "read the two quotes": a person decides, in a reviews file. No memory was modified.\n'
          "All claims and every source: --format markdown or --format html.")


def _clip(text: str) -> str:
    line = diagnostic_line(text)
    return line if len(line) <= _WIDTH else line[:_WIDTH - 1] + "…"


def _flagged(finding: Finding) -> bool:
    return finding.status in ATTENTION or has_open_missing_reference(finding)


def _shown(finding: Finding) -> list[EvidenceFinding]:
    """What a person confirmed, or else the strongest candidate against the claim. Support alone is not a flag."""
    if finding.status not in ATTENTION:
        return []
    confirmed = [e for e in finding.evidence if e.confirmed]
    return confirmed[:_SHOWN_CONFIRMED] or [e for e in finding.evidence if e.relation != "supports"][:1]


def _source(evidence: EvidenceFinding) -> list[str]:
    who = f"{diagnostic_line(evidence.relation)}, confirmed by {_clip(evidence.reviewer or '')}" if evidence.confirmed \
        else diagnostic_line(evidence.rationale)
    return [f"  source  {diagnostic_line(evidence.span.citation)}", f"          {_clip(evidence.span.quote)}",
            f"  why     {who}"]


def _finding(finding: Finding) -> list[str]:
    label = LABELS[finding.status] if finding.status in ATTENTION else "MISSING PATH"
    out = [f"{label} · {diagnostic_line(finding.memory_id)}",
           f"  memory  {diagnostic_line(finding.location)}", f"          {_clip(finding.text)}"]
    shown = _shown(finding)
    for evidence in shown:
        out += _source(evidence)
    left = sum(e.relation != "supports" or e.confirmed for e in finding.evidence) - len(shown)
    if left > 0:
        out.append(f"          and {left} more in the full report")
    if has_open_missing_reference(finding):
        out += [f"  path    {_clip(issue.detail)}" for issue in finding.issues if issue.kind == MISSING_REFERENCE]
    return [*out, ""]


def _summary(summary: dict[str, int], missing_paths: int) -> str:
    counts = [f"{n} {LABELS[status].lower()}" for status, n in summary.items()]
    if missing_paths:
        counts.append(f"{missing_paths} naming a missing path")
    return ", ".join(counts) or "no claims"


def _closing(claims: int, flagged: int, summary: dict[str, int], dismissed: dict[str, int]) -> list[str]:
    settled = f" {sum(dismissed.values())} candidate(s) dismissed by a person." if dismissed else ""
    if flagged:
        return [f"{flagged} of {claims} claim(s) need a look.{settled}", FOOTER]
    unchecked = summary.get("insufficient_evidence", 0)
    note = f" {unchecked} have no evidence, which means unchecked, not wrong." if unchecked else ""
    return [f"Nothing flagged in {claims} claim(s).{note}{settled}", FOOTER]


def _missing_paths(findings: list[Finding]) -> int:
    return sum(has_open_missing_reference(finding) for finding in findings)


def to_brief(report: AuditReport) -> str:
    flagged = [finding for finding in report.findings if _flagged(finding)]
    out = [f"{diagnostic_line(report.memory_file)}: {_summary(report.summary, _missing_paths(report.findings))}", ""]
    for finding in flagged:
        out += _finding(finding)
    out += _closing(len(report.findings), len(flagged), report.summary, report.dismissed)
    return "\n".join(out) + "\n"


def set_to_brief(result: AuditSet) -> str:
    """One file reads like its own report. Otherwise files follow in order; one with nothing flagged takes one line."""
    if len(result.reports) == 1:
        return to_brief(result.reports[0])
    findings = [finding for report in result.reports for finding in report.findings]
    out = [f"{len(result.files)} file(s): {_summary(result.summary, _missing_paths(findings))}", ""]
    for report in result.reports:
        flagged = [finding for finding in report.findings if _flagged(finding)]
        out.append(f"== {diagnostic_line(report.memory_file)}: {_summary(report.summary, _missing_paths(report.findings))}")
        out.append("")
        for finding in flagged:
            out += _finding(finding)
    out += _closing(len(findings), sum(map(_flagged, findings)), result.summary, result.dismissed)
    return "\n".join(out) + "\n"
