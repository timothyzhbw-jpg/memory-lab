"""Pluggable verifiers. A verifier judges one claim against one passage; it never edits memory.

The built-in LexicalVerifier needs no model or API key. ``llm.LLMVerifier`` (Claude) and
``jev.JevVerifier`` (TypeSafe AI's Jev judgment model) implement the same ``Verifier`` protocol;
``DualVerifier`` combines two of them and keeps their disagreement visible instead of picking a winner;
``CascadeVerifier`` trusts a cheap verifier when it is confident and escalates to a stronger one otherwise.
"""
from __future__ import annotations

from typing import Literal, Protocol

from pydantic import BaseModel

from .text import CJK, char_coverage, features, modality_conflict, negation, opposing_terms

Relation = Literal["supports", "contradicts", "unrelated", "disputed"]

# Shared by the model verifiers, so that when two of them disagree it is about the evidence,
# not about differently worded definitions.
RELATION_CRITERIA = {
    "supports": "The passage states the claim or directly entails it.",
    "contradicts": "The passage states something that cannot be true at the same time as the claim, such as "
                   "a different value, the opposite decision, a negation, or a counterexample to an absolute claim.",
    "unrelated": "The passage is about something else, or is compatible with the claim without establishing it.",
}


class Judgment(BaseModel):
    relation: Relation
    rationale: str
    verifier: str
    confidence: float | None = None
    opinions: list["Judgment"] = []


class VerifierError(RuntimeError):
    """A model verifier could not produce a judgment (missing dependency, API failure, bad labels)."""


class Verifier(Protocol):
    name: str

    def judge(self, claim: str, passage: str) -> Judgment: ...


def coverage(claim: str, passage: str) -> float:
    """Share of the claim's content terms found in the passage; an opposing term counts as a match.

    A claim with CJK text is measured per character instead (``text.char_coverage``)."""
    claim_words, _ = features(claim)
    if not claim_words:
        return 0.0
    if CJK.search(claim):
        return char_coverage(claim, passage)
    passage_words, _ = features(passage)
    matched = len(claim_words & passage_words) + (1 if opposing_terms(claim, passage) else 0)
    return min(matched, len(claim_words)) / len(claim_words)


class LexicalVerifier:
    """Term overlap plus opposing terms, numeric, modality and negation mismatches. Fast, explainable, shallow."""
    name = "lexical"

    def __init__(self, related: float = 0.6, support: float = 0.75) -> None:
        self.related = related
        self.support = support

    def judge(self, claim: str, passage: str) -> Judgment:
        score = coverage(claim, passage)
        if score < self.related:
            return Judgment(relation="unrelated", rationale=f"term coverage {score:.2f}", verifier=self.name)
        opposed = opposing_terms(claim, passage)
        if opposed:
            return Judgment(relation="contradicts", verifier=self.name,
                            rationale=f"opposing terms: claim '{opposed[0]}…' vs source '{opposed[1]}…'")
        _, claim_numbers = features(claim)
        _, passage_numbers = features(passage)
        if claim_numbers and passage_numbers and not claim_numbers & passage_numbers:
            return Judgment(relation="contradicts", verifier=self.name,
                            rationale=f"different values: claim {sorted(claim_numbers)} vs source {sorted(passage_numbers)}")
        modal = modality_conflict(claim, passage)
        if modal:
            return Judgment(relation="contradicts", verifier=self.name,
                            rationale=f"opposite modality: claim '{modal[0]}' vs source '{modal[1]}'")
        if (negation(claim, passage) is None) != (negation(passage, claim) is None):
            return Judgment(relation="contradicts", verifier=self.name,
                            rationale="same subject, but only one side is negated")
        if score >= self.support:
            return Judgment(relation="supports", verifier=self.name,
                            rationale=f"source restates the claim (term coverage {score:.2f})")
        return Judgment(relation="unrelated", rationale=f"partial overlap only ({score:.2f})", verifier=self.name)


class DualVerifier:
    """Ask two verifiers; agreement passes through, disagreement becomes ``disputed``."""

    def __init__(self, first: Verifier, second: Verifier) -> None:
        self.first, self.second = first, second
        self.name = f"{first.name}+{second.name}"

    def judge(self, claim: str, passage: str) -> Judgment:
        a, b = self.first.judge(claim, passage), self.second.judge(claim, passage)
        if a.relation == b.relation:
            return Judgment(relation=a.relation, rationale=f"{a.verifier}: {a.rationale}; {b.verifier}: {b.rationale}",
                            verifier=self.name, opinions=[a, b])
        return Judgment(relation="disputed", verifier=self.name, opinions=[a, b],
                        rationale=f"{a.verifier} says {a.relation}, {b.verifier} says {b.relation}")


class CascadeVerifier:
    """Accept ``first`` when its confidence reaches ``accept_at``; otherwise ``second`` decides.

    ``first`` should report a confidence (Jev does); a judgment without one is always escalated.
    The escalated result keeps both opinions, so the report shows what the first verifier thought.
    """

    def __init__(self, first: Verifier, second: Verifier, accept_at: float = 0.8) -> None:
        self.first, self.second, self.accept_at = first, second, accept_at
        self.name = f"{first.name}>{second.name}"

    def judge(self, claim: str, passage: str) -> Judgment:
        a = self.first.judge(claim, passage)
        if a.confidence is not None and a.confidence >= self.accept_at:
            return Judgment(relation=a.relation, rationale=f"{a.verifier}: {a.rationale}", verifier=self.name,
                            confidence=a.confidence, opinions=[a])
        b = self.second.judge(claim, passage)
        unsure = "no confidence" if a.confidence is None else f"confidence {a.confidence:.2f} < {self.accept_at}"
        return Judgment(relation=b.relation, verifier=self.name, opinions=[a, b],
                        rationale=f"{a.verifier} unsure ({unsure}), escalated; {b.verifier}: {b.rationale}")
