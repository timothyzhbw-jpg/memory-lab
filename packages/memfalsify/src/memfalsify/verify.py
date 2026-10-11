"""Pluggable verifiers. A verifier judges one claim against one passage; it never edits memory.

The built-in LexicalVerifier needs no model or API key. ``llm.LLMVerifier`` (Claude) and
``jev.JevVerifier`` (a dedicated NLI judgment model) implement the same ``Verifier`` protocol;
``DualVerifier`` combines two of them and keeps their disagreement visible instead of picking a winner.
"""
from __future__ import annotations

from typing import Literal, Protocol

from pydantic import BaseModel

from .text import features, has_negation, opposing_terms

Relation = Literal["supports", "contradicts", "unrelated", "disputed"]


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
    """Share of the claim's content terms found in the passage; an opposing term counts as a match."""
    claim_words, _ = features(claim)
    if not claim_words:
        return 0.0
    passage_words, _ = features(passage)
    matched = len(claim_words & passage_words) + (1 if opposing_terms(claim, passage) else 0)
    return min(matched, len(claim_words)) / len(claim_words)


class LexicalVerifier:
    """Term overlap plus opposing terms, negation and numeric mismatches. Fast, explainable, shallow."""
    name = "lexical"

    def __init__(self, related: float = 0.6, support: float = 0.75):
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
        if has_negation(claim) != has_negation(passage):
            return Judgment(relation="contradicts", verifier=self.name,
                            rationale="same subject, but only one side is negated")
        if score >= self.support:
            return Judgment(relation="supports", verifier=self.name,
                            rationale=f"source restates the claim (term coverage {score:.2f})")
        return Judgment(relation="unrelated", rationale=f"partial overlap only ({score:.2f})", verifier=self.name)


class DualVerifier:
    """Ask two verifiers; agreement passes through, disagreement becomes ``disputed``."""

    def __init__(self, first: Verifier, second: Verifier):
        self.first, self.second = first, second
        self.name = f"{first.name}+{second.name}"

    def judge(self, claim: str, passage: str) -> Judgment:
        a, b = self.first.judge(claim, passage), self.second.judge(claim, passage)
        if a.relation == b.relation:
            return Judgment(relation=a.relation, rationale=f"{a.verifier}: {a.rationale}; {b.verifier}: {b.rationale}",
                            verifier=self.name, opinions=[a, b])
        return Judgment(relation="disputed", verifier=self.name, opinions=[a, b],
                        rationale=f"{a.verifier} says {a.relation}, {b.verifier} says {b.relation}")
