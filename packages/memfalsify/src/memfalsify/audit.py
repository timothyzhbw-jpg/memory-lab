"""MemFalsify audit: challenge each memory claim against evidence. Read-only by construction."""
from __future__ import annotations

from collections.abc import Iterable
from datetime import date
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from memcore import EvidenceError, EvidenceSpan, locate_quote, read_jsonl

from . import __version__
from .corpus import Passage, Skipped, scan
from .memory import Claim, parse_memory
from .text import ABSOLUTE, HEDGE, RELATIVE_TIME, explicit_dates, features
from .verify import (Judgment, LexicalVerifier, Verifier, VerifierStats, coverage, judge_all, usage_since,
                     verifier_usage)

Status = Literal["challenged", "conflicted", "candidate_counterevidence", "disputed",
                 "insufficient_evidence", "candidate_support", "supported"]
SEVERITY: list[str] = list(Status.__args__)
_LEXICAL = LexicalVerifier()
ATTENTION = {"challenged", "conflicted", "candidate_counterevidence", "disputed"}


class Review(BaseModel):
    """A human-confirmed assertion. Only reviews can move a memory to supported/challenged."""
    model_config = ConfigDict(extra="forbid")

    memory_id: str
    relation: Literal["supports", "contradicts"]
    path: str
    quote: str = Field(min_length=1)
    reviewer: str = Field(min_length=1)
    note: str | None = None


class EvidenceFinding(BaseModel):
    relation: Literal["supports", "contradicts", "disputed"]
    confirmed: bool
    rationale: str
    reviewer: str | None = None
    opinions: list[Judgment] = []
    span: EvidenceSpan


class Issue(BaseModel):
    kind: Literal["overgeneralization", "relative_time", "possibly_stale", "inference_marker",
                  "internal_conflict", "duplicate"]
    detail: str


class Proposal(BaseModel):
    action: str
    reason: str
    replacement: None = None  # MemFalsify never writes replacement facts.


class Finding(BaseModel):
    memory_id: str
    text: str
    location: str
    section: str | None
    status: Status
    evidence: list[EvidenceFinding]
    issues: list[Issue]
    proposals: list[Proposal]


class AuditReport(BaseModel):
    tool: str = "memfalsify"
    version: str = __version__
    mode: Literal["read_only"] = "read_only"
    memory_file: str
    files_read: list[str]
    evidence_root: str | None
    excluded: list[str] = []
    verifier: str
    verifier_stats: list[VerifierStats] = []
    as_of: date
    summary: dict[str, int]
    findings: list[Finding]
    skipped_evidence: list[Skipped]
    limitations: list[str]


LEXICAL_NOTE = "Unconfirmed matches are candidates found by term overlap; they can miss paraphrases and misread context."
MODEL_NOTE = ("Unconfirmed matches are model judgments on passages pre-selected by term overlap; "
              "passages that share too few terms with a memory are never judged, and models can be wrong.")
LIMITATIONS = [
    "Only human reviews (reviews.jsonl) can mark a memory supported or challenged.",
    "Hashes prove a quote exists in the scanned bytes, not that the source is authentic or current.",
    "Missing evidence means unverified, not false. No memory was modified.",
]


def load_reviews(path: Path, evidence_root: Path | None, claims: dict[str, Claim]) -> dict[str, list[EvidenceFinding]]:
    if evidence_root is None:
        raise EvidenceError("--reviews requires --evidence: review quotes are located inside the evidence root")
    confirmed: dict[str, list[EvidenceFinding]] = {}
    for number, record in read_jsonl(path):
        review = Review.model_validate(record)
        if review.memory_id not in claims:
            raise EvidenceError(f"{path}:{number}: unknown memory_id {review.memory_id} "
                                "(the memory text may have changed; re-review it)")
        span = locate_quote(evidence_root, review.path, review.quote, source_kind="file")
        confirmed.setdefault(review.memory_id, []).append(EvidenceFinding(
            relation=review.relation, confirmed=True, reviewer=review.reviewer,
            rationale=review.note or "confirmed by reviewer", span=span))
    return confirmed


def shortlist(claim: Claim, passages: list[Passage], min_coverage: float = 0.5,
              max_judged: int | None = None) -> list[Passage]:
    """The passages to ask the verifier about: those sharing terms with the claim, best ``max_judged`` first."""
    words, _ = features(claim.text)
    if not words:
        return []
    ranked = []
    for passage in passages:
        shared = len(words & passage.words)
        score = coverage(claim.text, passage.span.quote) if shared else 0.0
        if shared and score >= min_coverage:
            ranked.append((-score, -shared, passage.span.location, passage))
    ranked.sort(key=lambda item: item[:3])
    return [passage for *_, passage in ranked[:max_judged]]


def candidates(claim: Claim, judged: list[tuple[Passage, Judgment]], limit: int) -> list[EvidenceFinding]:
    """The ``limit`` most telling judged passages: contradictions first, then the most shared terms."""
    words, _ = features(claim.text)
    scored = [(judgment.relation != "contradicts", -len(words & passage.words), passage.span.location, judgment,
               passage)
              for passage, judgment in judged if judgment.relation != "unrelated"]
    scored.sort(key=lambda item: item[:3])
    return [EvidenceFinding(relation=j.relation, confirmed=False, rationale=j.rationale, opinions=j.opinions,
                            span=p.span)
            for *_, j, p in scored[:limit]]


def issues_for(claim: Claim, others: list[Claim], as_of: date, stale_days: int) -> list[Issue]:
    found = []
    text = claim.text
    if match := ABSOLUTE.search(text):
        found.append(Issue(kind="overgeneralization", detail=f"absolute wording '{match.group()}'"))
    dates = explicit_dates(text)
    if (match := RELATIVE_TIME.search(text)) and not dates:
        found.append(Issue(kind="relative_time", detail=f"'{match.group()}' has no absolute date"))
    if dates and (as_of - max(dates)).days > stale_days:
        found.append(Issue(kind="possibly_stale",
                           detail=f"latest date {max(dates)} is over {stale_days} days before {as_of}"))
    if match := HEDGE.search(text):
        found.append(Issue(kind="inference_marker", detail=f"'{match.group()}' marks an inference, not an observation"))
    for other in others:
        if other.id == claim.id:
            continue
        # Always lexical: comparing every pair with a model verifier would be quadratic in model calls.
        if any(_LEXICAL.judge(a, b).relation == "contradicts" for a, b in ((text, other.text), (other.text, text))):
            found.append(Issue(kind="internal_conflict",
                               detail=f"may contradict {other.id} at {other.location}: \"{other.text}\""))
    if claim.also_at:
        found.append(Issue(kind="duplicate", detail="also stated at " + ", ".join(claim.also_at)))
    return found


def _overlaps(a: EvidenceSpan, b: EvidenceSpan) -> bool:
    return a.path == b.path and a.start < b.end and b.start < a.end


def status_of(evidence: list[EvidenceFinding]) -> Status:
    confirmed = {e.relation for e in evidence if e.confirmed}
    if confirmed:
        if confirmed == {"supports", "contradicts"}:
            return "conflicted"
        return "challenged" if "contradicts" in confirmed else "supported"
    relations = {e.relation for e in evidence}
    if "contradicts" in relations:
        return "candidate_counterevidence"
    if "disputed" in relations:
        return "disputed"
    return "candidate_support" if "supports" in relations else "insufficient_evidence"


STATUS_PROPOSALS = {
    "challenged": ("review_or_retire", "A reviewer confirmed contrary evidence; correct or remove this memory by hand."),
    "conflicted": ("resolve_conflict", "Confirmed evidence points both ways; decide which source is authoritative."),
    "candidate_counterevidence": ("verify_counterevidence", "Possible contrary evidence; confirm or dismiss it in reviews.jsonl."),
    "disputed": ("resolve_dispute", "Verifiers disagree about the evidence; a human should read it."),
    "candidate_support": ("confirm_support", "Possible supporting evidence; confirm it to mark this memory supported."),
    "insufficient_evidence": ("attach_evidence", "No evidence found; add a source or label this memory unverified."),
}
ISSUE_PROPOSALS = {
    "overgeneralization": ("narrow_scope", "State the cases the evidence actually covers."),
    "relative_time": ("add_absolute_date", "Replace relative time with the date it refers to."),
    "possibly_stale": ("reverify", "Re-check this against current sources."),
    "inference_marker": ("label_as_inference", "Keep it explicitly marked as an inference, or verify it."),
    "internal_conflict": ("resolve_internal_conflict", "Two memories disagree; at most one can stay as written."),
    "duplicate": ("deduplicate", "Keep one copy so future edits cannot diverge."),
}


def audit(memory_file: Path, evidence_root: Path | None = None, reviews: Path | None = None,
          verifier: Verifier | None = None, as_of: date | None = None, stale_days: int = 180,
          follow_links: bool = True, max_candidates: int = 3, min_coverage: float = 0.5,
          max_judged: int | None = None, exclude: Iterable[str] = (), max_concurrency: int = 1) -> AuditReport:
    """Audit every claim. ``min_coverage`` and ``max_judged`` bound which passages the verifier sees;
    ``exclude`` globs (relative to the evidence root) keep files out of the evidence; up to ``max_concurrency``
    verifier calls run at once."""
    claims, files = parse_memory(memory_file, follow_links=follow_links)
    base = Path(memory_file).resolve().parent
    return audit_claims(claims, memory_file=str(memory_file), files_read=[p.relative_to(base).as_posix() for p in files],
                        memory_paths=files, evidence_root=evidence_root, reviews=reviews, verifier=verifier,
                        as_of=as_of, stale_days=stale_days, max_candidates=max_candidates,
                        min_coverage=min_coverage, max_judged=max_judged, exclude=exclude,
                        max_concurrency=max_concurrency)


def audit_claims(claims: list[Claim], *, memory_file: str, files_read: list[str], memory_paths: Iterable[Path],
                 evidence_root: Path | None = None, reviews: Path | None = None, verifier: Verifier | None = None,
                 as_of: date | None = None, stale_days: int = 180, max_candidates: int = 3,
                 min_coverage: float = 0.5, max_judged: int | None = None, exclude: Iterable[str] = (),
                 only: Iterable[str] | None = None, max_concurrency: int = 1) -> AuditReport:
    """Audit already parsed claims. With ``only`` (claim IDs), findings cover just those claims, while
    reviews and internal conflicts are still checked against every claim. ``memory_paths`` are kept out
    of the evidence. The verifier gets every shortlisted (claim, passage) pair in one ``judge_all`` call."""
    verifier = verifier or LexicalVerifier()
    as_of = as_of or date.today()
    by_id = {c.id: c for c in claims}
    selected = None if only is None else set(only)
    targets = [c for c in claims if selected is None or c.id in selected]
    root = Path(evidence_root).resolve() if evidence_root else None
    if root is not None and not root.is_dir():
        raise EvidenceError(f"evidence root is not a directory: {evidence_root}")
    exclude = [p.strip() for p in exclude if p.strip()]
    if exclude and root is None:
        raise EvidenceError("--exclude requires --evidence: patterns are relative to the evidence root")
    # When a selection is empty there is nothing to look up, so the evidence is not scanned.
    scanned = root is not None and (selected is None or bool(targets))
    passages, skipped = scan(root, exclude=exclude, memory_files=memory_paths) if scanned else ([], [])
    if targets and root is not None and exclude and not passages:
        raise EvidenceError("no evidence passages indexed with --exclude; check the exclusion patterns")
    confirmed = load_reviews(reviews, root, by_id) if reviews else {}
    picked = {claim.id: shortlist(claim, passages, min_coverage, max_judged) for claim in targets}
    pairs = [(claim.text, passage.span.quote) for claim in targets for passage in picked[claim.id]]
    before = verifier_usage(verifier)
    judgments = iter(judge_all(verifier, pairs, max_concurrency))
    stats = usage_since(before, verifier_usage(verifier))
    findings = []
    for claim in targets:
        reviewed = confirmed.get(claim.id, [])
        judged = [(passage, next(judgments)) for passage in picked[claim.id]]
        evidence = reviewed + [c for c in candidates(claim, judged, max_candidates)
                               if not any(_overlaps(c.span, r.span) for r in reviewed)]
        status = status_of(evidence)
        issues = issues_for(claim, claims, as_of, stale_days)
        actions = [STATUS_PROPOSALS[status]] if status in STATUS_PROPOSALS else []
        actions += [ISSUE_PROPOSALS[i.kind] for i in issues]
        proposals = [Proposal(action=a, reason=r) for a, r in dict.fromkeys(actions)]
        findings.append(Finding(memory_id=claim.id, text=claim.text, location=claim.location, section=claim.section,
                                status=status, evidence=evidence, issues=issues, proposals=proposals))
    findings.sort(key=lambda f: SEVERITY.index(f.status))
    summary = {s: n for s in SEVERITY if (n := sum(f.status == s for f in findings))}
    return AuditReport(memory_file=memory_file, files_read=files_read,
                       evidence_root=str(evidence_root) if evidence_root else None, excluded=exclude,
                       verifier=verifier.name, verifier_stats=stats, as_of=as_of, summary=summary,
                       findings=findings, skipped_evidence=skipped,
                       limitations=[LEXICAL_NOTE if isinstance(verifier, LexicalVerifier) else MODEL_NOTE,
                                    *LIMITATIONS])
