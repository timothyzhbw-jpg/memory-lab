"""MemFalsify audit: challenge each memory claim against evidence. Read-only by construction."""
from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from memcore import EvidenceError, EvidenceSpan, make_span, read_jsonl, sha256_bytes
from memcore.paths import InputPathError, input_path
from memcore.reading import read_input_bytes

from . import __version__
from .corpus import Passage, Skipped, scan
from .memory import Claim, parse_memory
from .text import (ABSOLUTE, ANTONYM_STEMS, ANTONYMS_ZH, HEDGE, RELATIVE_TIME, explicit_dates, features,
                   has_negation)
from .verify import Judgment, LexicalVerifier, Verifier, coverage

Status = Literal["challenged", "conflicted", "candidate_counterevidence", "disputed",
                 "insufficient_evidence", "candidate_support", "supported"]
SEVERITY: list[str] = list(Status.__args__)
_LEXICAL = LexicalVerifier()
ATTENTION = frozenset({"challenged", "conflicted", "candidate_counterevidence", "disputed"})


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


def load_reviews(path: Path, evidence_root: Path | None, claims: dict[str, Claim], *,
                 skipped: list[Skipped] | None = None) -> dict[str, list[EvidenceFinding]]:
    if evidence_root is None:
        raise EvidenceError("--reviews requires --evidence: review quotes are located inside the evidence root")
    confirmed: dict[str, list[EvidenceFinding]] = {}
    for number, record in read_jsonl(path):
        review = Review.model_validate(record)
        if review.memory_id not in claims:
            raise EvidenceError(f"{path}:{number}: unknown memory_id {review.memory_id} "
                                "(the memory text may have changed; re-review it)")
        # Human-confirmed evidence must obey the same policy as scanned evidence.
        try:
            target = input_path(evidence_root, Path(evidence_root).resolve() / review.path)
            raw = read_input_bytes(evidence_root, target)
        except InputPathError as exc:
            if skipped is not None and str(exc) != "path escapes root":
                skipped.append(Skipped(path=review.path, reason=f"review evidence skipped: {exc}"))
                continue
            raise EvidenceError(f"review evidence skipped: {exc}") from None
        text = raw.decode("utf-8")
        start = text.find(review.quote)
        if start < 0:
            raise EvidenceError(f"quote not found verbatim in {review.path}: {review.quote!r}")
        span = make_span(review.path, sha256_bytes(raw), text, start, start + len(review.quote), source_kind="file")
        confirmed.setdefault(review.memory_id, []).append(EvidenceFinding(
            relation=review.relation, confirmed=True, reviewer=review.reviewer,
            rationale=review.note or "confirmed by reviewer", span=span))
    return confirmed


def candidates(claim: Claim, passages: list[Passage], verifier: Verifier, limit: int,
               min_coverage: float = 0.5, max_judged: int | None = None) -> list[EvidenceFinding]:
    """Pre-select passages by term overlap, ask the verifier about the best ``max_judged`` of them."""
    words, _ = features(claim.text)
    if not words:
        return []
    shortlist = []
    for passage in passages:
        shared = len(words & passage.words)
        score = coverage(claim.text, passage.span.quote) if shared else 0.0
        if shared and score >= min_coverage:
            shortlist.append((-score, -shared, passage.span.location, passage))
    shortlist.sort(key=lambda item: item[:3])
    scored = []
    for _, negative_shared, location, passage in shortlist[:max_judged]:
        judgment: Judgment = verifier.judge(claim.text, passage.span.quote)
        if judgment.relation != "unrelated":
            scored.append((judgment.relation != "contradicts", negative_shared, location, judgment, passage))
    scored.sort(key=lambda item: item[:3])
    return [EvidenceFinding(relation=j.relation, confirmed=False, rationale=j.rationale, opinions=j.opinions,
                            span=p.span)
            for *_, j, p in scored[:limit]]


@dataclass(frozen=True)
class _Lex:
    """Lexical features of one claim, computed once per audit."""
    words: frozenset[str]
    numbers: frozenset[str]
    negated: bool
    zh: frozenset[str]


_ZH_TERMS = tuple(dict.fromkeys(term for pair in ANTONYMS_ZH for term in pair))


def _opposite_map(pairs: list[tuple[str, str]]) -> dict[str, frozenset[str]]:
    found: dict[str, set[str]] = {}
    for left, right in pairs:
        found.setdefault(left, set()).add(right)
        found.setdefault(right, set()).add(left)
    return {term: frozenset(opposites) for term, opposites in found.items()}


_EN_OPPOSITES = _opposite_map(ANTONYM_STEMS)
_ZH_OPPOSITES = _opposite_map(list(ANTONYMS_ZH))


def _zh_present(text: str) -> frozenset[str]:
    return frozenset(term for term in _ZH_TERMS if term in text)


def _lex_of(text: str) -> _Lex:
    words, numbers = features(text)
    return _Lex(words, numbers, has_negation(text), _zh_present(text))


def _min_shared(size: int, related: float) -> int:
    """Fewest shared terms that can still reach ``related`` coverage after the antonym bonus of +1.

    ``coverage`` adds that one before the threshold, so a smaller intersection can never be
    ``contradicts`` in this direction. Zero means an empty intersection can pass (a single term
    plus an antonym).
    """
    for shared in range(size + 1):
        if min(shared + 1, size) / size >= related:
            return shared
    return size + 1


def _opposed(claim: _Lex, passage: _Lex) -> bool:
    """Same question as ``opposing_terms``, answered from features already in hand."""
    words = passage.words
    for left, right in ANTONYM_STEMS:
        if left in claim.words and right in words and left not in words:
            return True
        if right in claim.words and left in words and right not in words:
            return True
    present = passage.zh
    for left, right in ANTONYMS_ZH:
        if left in claim.zh and right in present and left not in present:
            return True
        if right in claim.zh and left in present and right not in present:
            return True
    return False


def _directed_contradicts(claim: _Lex, passage: _Lex, shared: int, related: float) -> bool:
    """``LexicalVerifier.judge(claim, passage).relation == 'contradicts'``, without building a judgment."""
    size = len(claim.words)
    if size == 0:
        return False
    # ``score < related`` is the verifier's cutoff. Shared terms cannot exceed ``size``.
    if shared / size >= related:
        if _opposed(claim, passage):
            return True
        if claim.numbers and passage.numbers and claim.numbers.isdisjoint(passage.numbers):
            return True
        return claim.negated != passage.negated
    if (shared + 1) / size < related:
        return False
    return _opposed(claim, passage)


class _ConflictIndex:
    """Pairs that can lexically contradict, found from a prefix of each claim's rarest terms.

    Terms are ordered by increasing document frequency. If claim A must share at least ``k`` terms
    with another claim to clear the coverage threshold (counting the antonym bonus), any such claim
    contains one of A's rarest ``len(A) - k + 1`` terms. Claims whose own threshold is lower are
    found from their prefixes. A claim of one term can also contradict a claim it shares nothing
    with, when that term and an antonym are opposed; those are probed directly.
    """

    def __init__(self, claims: list[Claim]):
        self.claims = claims
        self.related = _LEXICAL.related
        self.lex = [_lex_of(claim.text) for claim in claims]
        self._pos = {}
        for index, claim in enumerate(claims):
            self._pos.setdefault(id(claim), index)
        counts: Counter[str] = Counter()
        self.contains: dict[str, list[int]] = defaultdict(list)
        self.zh_at: dict[str, list[int]] = defaultdict(list)
        for index, lex in enumerate(self.lex):
            counts.update(lex.words)
            for term in lex.words:
                self.contains[term].append(index)
            for term in lex.zh:
                self.zh_at[term].append(index)
        self._counts = counts
        # One-term claims can contradict a claim they share nothing with, via an antonym.
        # Everyone else has to be able to find those claims from the opposite term.
        self.singletons: dict[str, list[int]] = defaultdict(list)
        for index, lex in enumerate(self.lex):
            if len(lex.words) == 1:
                self.singletons[next(iter(lex.words))].append(index)
        self.prefixes: list[tuple[str, ...]] = []
        self.in_prefix: dict[str, list[int]] = defaultdict(list)
        for index, lex in enumerate(self.lex):
            prefix = self._prefix(lex.words)
            self.prefixes.append(prefix)
            for term in prefix:
                self.in_prefix[term].append(index)

    def _prefix(self, words: frozenset[str]) -> tuple[str, ...]:
        size = len(words)
        # No terms, or a threshold outside (0, 1]: a zero score can pass, or no score can.
        # Either way the rare-term prefix does not apply.
        if size == 0 or not 0.0 < self.related <= 1.0:
            return ()
        needed = _min_shared(size, self.related)
        if needed <= 0:
            return ()
        rarest = sorted(words, key=lambda term: (self._counts.get(term, 0), term))
        return tuple(rarest[:size - needed + 1])

    def _loose(self, lex: _Lex, chosen: set[int]) -> None:
        """Candidates when an empty intersection can still reach the threshold."""
        for term in lex.words:
            chosen.update(self.contains.get(term, ()))
            for opposite in _EN_OPPOSITES.get(term, ()):
                chosen.update(self.contains.get(opposite, ()))
        for term in lex.zh:
            for opposite in _ZH_OPPOSITES.get(term, ()):
                chosen.update(self.zh_at.get(opposite, ()))

    def _antonym_singletons(self, lex: _Lex, chosen: set[int]) -> None:
        for term in lex.words:
            for opposite in _EN_OPPOSITES.get(term, ()):
                chosen.update(self.singletons.get(opposite, ()))
        for term in lex.zh:
            for opposite in _ZH_OPPOSITES.get(term, ()):
                chosen.update(self.singletons.get(opposite, ()))

    def _candidates(self, lex: _Lex, prefix: tuple[str, ...], skip: int | None) -> set[int]:
        chosen: set[int] = set()
        if prefix:
            for term in prefix:
                chosen.update(self.contains.get(term, ()))
        elif 0.0 < self.related <= 1.0:
            self._loose(lex, chosen)
        else:
            chosen.update(range(len(self.claims)))
        for term in lex.words:
            chosen.update(self.in_prefix.get(term, ()))
        if 0.0 < self.related <= 1.0:
            self._antonym_singletons(lex, chosen)
        if skip is not None:
            chosen.discard(skip)
        return chosen

    def issues(self, claim: Claim) -> list[Issue]:
        position = self._pos.get(id(claim))
        if position is not None and self.claims[position] is claim:
            lex = self.lex[position]
            chosen = self._candidates(lex, self.prefixes[position], position)
        else:
            lex = _lex_of(claim.text)
            chosen = self._candidates(lex, self._prefix(lex.words), None)
        found = []
        for index in sorted(chosen):
            other = self.claims[index]
            if other.id == claim.id:
                continue
            other_lex = self.lex[index]
            shared = len(lex.words & other_lex.words)
            if (_directed_contradicts(lex, other_lex, shared, self.related)
                    or _directed_contradicts(other_lex, lex, shared, self.related)):
                found.append(Issue(kind="internal_conflict",
                                   detail=f"may contradict {other.id} at {other.location}: \"{other.text}\""))
        return found


def issues_for(claim: Claim, others: list[Claim], as_of: date, stale_days: int,
               conflicts: _ConflictIndex | None = None) -> list[Issue]:
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
    # Always lexical: comparing every pair with a model verifier would be quadratic in model calls.
    # ``conflicts`` indexes ``others``; internal conflicts stay in that list's order.
    found.extend((conflicts if conflicts is not None else _ConflictIndex(others)).issues(claim))
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
          max_judged: int | None = None, exclude: Iterable[str] = ()) -> AuditReport:
    """Audit every claim. ``min_coverage`` and ``max_judged`` bound which passages the verifier sees;
    ``exclude`` globs (relative to the evidence root) keep files out of the evidence."""
    skipped = []
    claims, files = parse_memory(memory_file, follow_links=follow_links, skipped=skipped)
    base = Path(memory_file).absolute().parent.resolve()
    report = audit_claims(claims, memory_file=str(memory_file), files_read=[p.relative_to(base).as_posix() for p in files],
                        memory_paths=files, evidence_root=evidence_root, reviews=reviews, verifier=verifier,
                        as_of=as_of, stale_days=stale_days, max_candidates=max_candidates,
                        min_coverage=min_coverage, max_judged=max_judged, exclude=exclude)
    # Keep the report schema stable; identify memory exclusions explicitly in the reason.
    report.skipped_evidence[:0] = [s.model_copy(update={"reason": "memory skipped: " + s.reason}) for s in skipped]
    return report


def audit_claims(claims: list[Claim], *, memory_file: str, files_read: list[str], memory_paths: Iterable[Path],
                 evidence_root: Path | None = None, reviews: Path | None = None, verifier: Verifier | None = None,
                 as_of: date | None = None, stale_days: int = 180, max_candidates: int = 3,
                 min_coverage: float = 0.5, max_judged: int | None = None, exclude: Iterable[str] = (),
                 only: Iterable[str] | None = None) -> AuditReport:
    """Audit already parsed claims. With ``only`` (claim IDs), findings cover just those claims, while
    reviews and internal conflicts are still checked against every claim. ``memory_paths`` are kept out
    of the evidence."""
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
    confirmed = load_reviews(reviews, root, by_id, skipped=skipped) if reviews else {}
    findings = []
    conflict_index = _ConflictIndex(claims) if targets else None
    for claim in targets:
        reviewed = confirmed.get(claim.id, [])
        evidence = reviewed + [c for c in candidates(claim, passages, verifier, max_candidates,
                                                     min_coverage, max_judged)
                               if not any(_overlaps(c.span, r.span) for r in reviewed)]
        status = status_of(evidence)
        issues = issues_for(claim, claims, as_of, stale_days, conflicts=conflict_index)
        actions = [STATUS_PROPOSALS[status]] if status in STATUS_PROPOSALS else []
        actions += [ISSUE_PROPOSALS[i.kind] for i in issues]
        proposals = [Proposal(action=a, reason=r) for a, r in dict.fromkeys(actions)]
        findings.append(Finding(memory_id=claim.id, text=claim.text, location=claim.location, section=claim.section,
                                status=status, evidence=evidence, issues=issues, proposals=proposals))
    findings.sort(key=lambda f: SEVERITY.index(f.status))
    summary = {s: n for s in SEVERITY if (n := sum(f.status == s for f in findings))}
    return AuditReport(memory_file=memory_file, files_read=files_read,
                       evidence_root=str(evidence_root) if evidence_root else None, excluded=exclude,
                       verifier=verifier.name, as_of=as_of, summary=summary, findings=findings,
                       skipped_evidence=skipped,
                       limitations=[LEXICAL_NOTE if isinstance(verifier, LexicalVerifier) else MODEL_NOTE,
                                    *LIMITATIONS])
