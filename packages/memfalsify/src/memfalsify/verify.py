"""Pluggable verifiers. A verifier judges one claim against one passage; it never edits memory.

The built-in LexicalVerifier needs no model or API key. ``llm.LLMVerifier`` (Claude) and
``jev.JevVerifier`` (TypeSafe AI's Jev judgment model) implement the same ``Verifier`` protocol;
``DualVerifier`` combines two of them and keeps their disagreement visible instead of picking a winner;
``CascadeVerifier`` trusts a cheap verifier when it is confident and escalates to a stronger one otherwise.

``judge_all`` judges many pairs at once, on a bounded thread pool or through a verifier's own ``judge_many``
(the Claude verifier uses it for prompt caching and the Message Batches API). Model backends count their API
usage in a ``UsageMeter`` and expose it through ``usage()``.
"""
from __future__ import annotations

import threading
from collections import deque
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from typing import Any, Literal, Protocol, TypeVar

from pydantic import BaseModel

from .text import (CJK, Quantity, char_coverage, clauses, features, has_cue, is_absolute, is_date, is_prohibition,
                   modality_conflict, named_conflict, negation,
                   opposing_terms, plain_words, quantities, reached_terms, removal, same_release, says_removal, stated_terms,
                   statements, units_differ)

Relation = Literal["supports", "contradicts", "unrelated", "disputed"]
NAMED_VALUE = "same setting"  # how a named-value contradiction's rationale starts; it outranks the other rules
SET_VALUES = "set differently"  # the same when a side merely sets the value, as in a config or an example
REMOVED = "source says what the claim names was removed"
# How much each lexical reason says, strongest first, as measured on real repositories (benchmarks/memfalsify/wild).
REASONS = (NAMED_VALUE, "different units", "different values", REMOVED, SET_VALUES, "opposing terms", "opposite modality",
           "same subject")


def strength(rationale: str) -> int:
    """The rank of a lexical contradiction's reason: lower is stronger, and any other text ranks last."""
    return next((rank for rank, reason in enumerate(REASONS) if rationale.startswith(reason)), len(REASONS))

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


class VerifierStats(BaseModel):
    """API usage of one model backend: requests sent and the token counts the API reported for them.

    The cache counters are ``None`` for a backend without prompt caching. ``input_tokens`` excludes
    tokens written to or read from the prompt cache, as in the Anthropic API's ``usage``."""
    verifier: str
    requests: int = 0
    batched_requests: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_input_tokens: int | None = None
    cache_read_input_tokens: int | None = None

    def minus(self, earlier: VerifierStats) -> VerifierStats:
        return VerifierStats(verifier=self.verifier, **{
            field: None if (now := getattr(self, field)) is None else now - (getattr(earlier, field) or 0)
            for field in COUNTERS})


COUNTERS = ("requests", "batched_requests", "input_tokens", "output_tokens",
            "cache_creation_input_tokens", "cache_read_input_tokens")
TOKENS = COUNTERS[2:]


class UsageMeter:
    """Running totals for one backend, safe to update from several threads."""

    def __init__(self, verifier: str, caching: bool = False):
        self._lock = threading.Lock()
        zero = 0 if caching else None
        self._totals = VerifierStats(verifier=verifier, cache_creation_input_tokens=zero, cache_read_input_tokens=zero)

    def record(self, usage: Any = None, *, batched: bool = False) -> None:
        """Count one request; ``usage`` is the response's usage object (missing token fields count as 0)."""
        with self._lock:
            totals = self._totals
            totals.requests += 1
            totals.batched_requests += batched
            for field in TOKENS:
                if getattr(totals, field) is not None:
                    setattr(totals, field, getattr(totals, field) + (getattr(usage, field, None) or 0))

    def snapshot(self) -> VerifierStats:
        with self._lock:
            return self._totals.model_copy()


def verifier_usage(verifier: Verifier) -> list[VerifierStats]:
    """Usage so far of every model backend inside ``verifier``; empty for verifiers that call no API."""
    usage = getattr(verifier, "usage", None)
    return list(usage()) if callable(usage) else []


def usage_since(before: list[VerifierStats], after: list[VerifierStats]) -> list[VerifierStats]:
    """What each backend used between two ``verifier_usage`` snapshots."""
    return [now.minus(then) for now, then in zip(after, before)] + after[len(before):]


T = TypeVar("T")


def concurrently(call: Callable[[int], T], count: int, max_concurrency: int = 1,
                 after: Mapping[int, Sequence[int]] | None = None) -> list[T]:
    """Return ``[call(0), ..., call(count - 1)]`` with at most ``max_concurrency`` calls running at once.

    ``after`` maps an index to the (larger) indices that may start only once it has finished; those run before
    work that has not started yet. Results keep index order whatever order the calls finish in. The first
    exception is raised once the calls already running have returned; nothing else is started."""
    if max_concurrency <= 1 or count <= 1:
        return [call(i) for i in range(count)]
    after = after or {}
    held = {i for followers in after.values() for i in followers}
    fresh = deque(i for i in range(count) if i not in held)
    ready: deque[int] = deque()
    results: list[Any] = [None] * count
    running: dict[Future, int] = {}
    with ThreadPoolExecutor(max_workers=min(max_concurrency, count)) as pool:
        while fresh or ready or running:
            while len(running) < max_concurrency and (ready or fresh):
                i = (ready or fresh).popleft()
                running[pool.submit(call, i)] = i
            done, _ = wait(running, return_when=FIRST_COMPLETED)
            for future in done:
                i = running.pop(future)
                results[i] = future.result()
                ready.extend(after.get(i, ()))
    return results


def judge_all(verifier: Verifier, pairs: Sequence[tuple[str, str]], max_concurrency: int = 1) -> list[Judgment]:
    """Judge ``(claim, passage)`` pairs and return the judgments in the order of ``pairs``.

    A verifier with ``judge_many(pairs, max_concurrency)`` schedules the work itself; any other verifier
    is called once per pair, ``max_concurrency`` calls at a time."""
    many = getattr(verifier, "judge_many", None)
    if callable(many):
        judgments = list(many(pairs, max_concurrency))
        if len(judgments) != len(pairs):
            raise VerifierError(f"{verifier.name}: {len(judgments)} judgments for {len(pairs)} pairs")
        return judgments
    return concurrently(lambda i: verifier.judge(*pairs[i]), len(pairs), max_concurrency)


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


NEAR = 0.6  # two texts are about the same subject when each covers at least this share of the other
MIN_SHARED = 2  # and they share at least this many terms
NEGATION_SHARED = 3  # a negation is a small word: it takes more shared terms to tell that it is about the same thing
OPPOSING_NEAR = 0.4  # an opposing word says more than a "not": the rest of the sentence may differ more
NUMBERS_NEAR = 0.5  # a number next to the same word is evidence by itself, so the sentences may differ a little more
PROHIBITION_NEAR = 0.8  # a line opening with ``Do not``: an instruction, so only its near opposite speaks against it
REMOVED_TERMS = 4
REMOVED_SUBJECT = 3  # terms before ``was removed`` that a claim has to name, when it says more about the thing


def _near(one: str, other: str, least: int = MIN_SHARED, near: float = NEAR) -> bool:
    """Each text covers at least ``near`` of the other, on at least ``least`` shared terms. CJK text is measured per
    character, which has no term count to go by."""
    if min(coverage(one, other), coverage(other, one)) < near:
        return False
    words, _ = features(one)
    others, _ = features(other)
    return len(words & others) >= least or bool(CJK.search(one + other))


def _alike(one: str, other: str, opposed: tuple[str, str] = ("", "")) -> bool:
    """The two say the same in the same terms, apart from the opposing pair. Then there is nothing else to tell them
    apart, so a short statement is about one subject on however few terms: ``Telemetry is disabled.``"""
    words, others = features(one)[0] - {opposed[0]}, features(other)[0] - {opposed[1]}
    return bool(words) and words == others


def opposed_on_same_subject(claim: str, passage: str) -> tuple[str, str] | None:
    """Opposing terms that stand as words, not parts of a name, in two sentences or clauses about the same subject."""
    for a, b in ((claim, passage), *((a, b) for a in clauses(claim) for b in clauses(passage))):
        if (found := opposing_terms(plain_words(a), plain_words(b))) and (_near(a, b, near=OPPOSING_NEAR)
                                                                          or _alike(a, b, found)):
            return found
    return None


def _scoped(one: Quantity, other: Quantity, claim: str, passage: str) -> bool:
    """Each number's clause goes on with words the other text does not have, so each is about its own case: 2 spaces in
    YAML files and 4 in Python files, 200 on success and 404 when the resource is missing. Not read in Chinese text, where
    ``after`` holds lone characters, and no real repository has been measured."""
    if CJK.search(claim + passage):
        return False
    return bool(one.after - features(passage)[0]) and bool(other.after - features(claim)[0])


def number_conflict(claim: str, passage: str) -> tuple[list[str], list[str]] | None:
    """(claim numbers, source numbers) when two nearly identical sentences, or clauses, attach different numbers to
    the same word, and neither goes on to name a case of its own. Numbers inside names, paths, references, dates and
    times, or bound to a key, do not count."""
    ours, theirs = quantities(claim), quantities(passage)
    if not ours or not theirs:
        return None
    values, others = {q.value for q in ours}, {q.value for q in theirs}

    def has(value: str, among: set[str]) -> bool:
        return value in among or any(same_release(value, other) for other in among)

    whole = _near(claim, passage, near=NUMBERS_NEAR)
    for one in ours:
        for other in theirs:
            if (not has(one.value, others) and not has(other.value, values) and one.next_to & other.next_to
                    and is_date(one.value) == is_date(other.value)
                    and (whole or _near(one.clause, other.clause, near=NUMBERS_NEAR))
                    and not _scoped(one, other, claim, passage)):
                return [one.value], [other.value]
    return None


def unit_conflict(claim: str, passage: str) -> tuple[str, str] | None:
    """(claim amount, source amount) when two nearly identical sentences, or clauses, give the same number different
    units: 2 minutes and 2 seconds, 512 GB and 512 GiB."""
    ours, theirs = quantities(claim), quantities(passage)
    for one in ours:
        for other in theirs:
            if units_differ(one, other) and (_near(claim, passage, near=NUMBERS_NEAR)
                                             or _near(one.clause, other.clause, near=NUMBERS_NEAR)):
                return one.written, other.written
    return None


def _negates(negated: str, plain: str) -> bool:
    """A statement of ``negated`` reaches a word ``plain`` states without negation, and the two have one subject. A line
    opening with ``Do not`` is an instruction, which says little about a line that merely shares its words: only a line
    that says the same without the ``not``, in words of its own and not just names or a label, speaks against it."""
    prohibition = is_prohibition(negated)
    if prohibition and plain.rstrip().endswith(":"):
        return False
    aside = frozenset().union(*map(reached_terms, statements(plain)))
    words = stated_terms(plain_words(plain) if prohibition else plain) - aside
    shared, _ = features(plain)
    for statement in statements(negated):
        reached = reached_terms(statement)
        if not reached & words or reached & aside:
            continue
        if prohibition:
            if _near(statement, plain, near=PROHIBITION_NEAR):
                return True
            continue
        # One counterexample contradicts an absolute statement, however much else it says.
        if is_absolute(plain) and coverage(plain, statement) >= NEAR and len(shared & features(statement)[0]) >= NEGATION_SHARED:
            return True
        if any(_near(statement, part, NEGATION_SHARED) or _alike(statement, part) for part in (plain, *clauses(plain))):
            return True
    return False


def negation_conflict(claim: str, passage: str) -> bool:
    """One side negates a word the other states plainly, in two statements about the same subject."""
    return _negates(claim, passage) or _negates(passage, claim)


def removal_conflict(claim: str, passage: str) -> bool:
    """The source says what the claim names is removed: all the claim's terms stand before the words that say so. Or it
    was removed: then the terms before those words only have to be in the claim, which may say more about the thing."""
    words, _ = features(claim)
    if len(words) < REMOVED_TERMS or says_removal(words) or removal(claim)[0]:
        return False
    for part in statements(passage):
        before, gone = removal(part)
        if words <= before or (gone and len(before) >= REMOVED_SUBJECT and before <= words):
            return True
    return False


class LexicalVerifier:
    """Named values first, then opposing terms, numbers, modality and negation on texts about the same subject.

    Fast, explainable, shallow."""
    name = "lexical"

    def __init__(self, related: float = 0.6, support: float = 0.75) -> None:
        self.related = related
        self.support = support

    def judge(self, claim: str, passage: str) -> Judgment:
        named = named_conflict(claim, passage)
        score = coverage(claim, passage)
        if named and (not named.merely_set or score >= self.related):
            reason = SET_VALUES if named.merely_set else NAMED_VALUE
            return Judgment(relation="contradicts", verifier=self.name,
                            rationale=f"{reason} {named.key}: claim {named.claim} vs source {named.source}")
        if score < self.related:
            return Judgment(relation="unrelated", rationale=f"term coverage {score:.2f}", verifier=self.name)
        cjk = bool(CJK.search(claim) or CJK.search(passage))
        opposed = opposing_terms(claim, passage)
        opposing = opposed if cjk else opposed and opposed_on_same_subject(claim, passage)
        if opposing:
            return Judgment(relation="contradicts", verifier=self.name,
                            rationale=f"opposing terms: claim '{opposing[0]}…' vs source '{opposing[1]}…'")
        units = unit_conflict(claim, passage)
        if units:
            return Judgment(relation="contradicts", verifier=self.name,
                            rationale=f"different units: claim {units[0]} vs source {units[1]}")
        differ = number_conflict(claim, passage)
        if differ:
            return Judgment(relation="contradicts", verifier=self.name,
                            rationale=f"different values: claim {differ[0]} vs source {differ[1]}")
        modal = modality_conflict(claim, passage)
        if modal:
            return Judgment(relation="contradicts", verifier=self.name,
                            rationale=f"opposite modality: claim '{modal[0]}' vs source '{modal[1]}'")
        negated_once = ((negation(claim, passage) is None) != (negation(passage, claim) is None) if cjk
                        else has_cue(claim) != has_cue(passage))
        if negated_once if cjk else negation_conflict(claim, passage):
            return Judgment(relation="contradicts", verifier=self.name,
                            rationale="same subject, but only one side is negated")
        if removal_conflict(claim, passage):
            return Judgment(relation="contradicts", verifier=self.name,
                            rationale=REMOVED)
        _, claim_numbers = features(claim)
        _, passage_numbers = features(passage)
        # What the rules above read as doubtful is no support either, even when it is no contradiction.
        doubtful = opposed or negated_once or (claim_numbers and passage_numbers and not claim_numbers & passage_numbers)
        if score >= self.support and not doubtful:
            return Judgment(relation="supports", verifier=self.name,
                            rationale=f"source restates the claim (term coverage {score:.2f})")
        return Judgment(relation="unrelated", rationale=f"partial overlap only ({score:.2f})", verifier=self.name)


class DualVerifier:
    """Ask two verifiers; agreement passes through, disagreement becomes ``disputed``."""

    def __init__(self, first: Verifier, second: Verifier) -> None:
        self.first, self.second = first, second
        self.name = f"{first.name}+{second.name}"

    def judge(self, claim: str, passage: str) -> Judgment:
        return self._combine(self.first.judge(claim, passage), self.second.judge(claim, passage))

    def judge_many(self, pairs: Sequence[tuple[str, str]], max_concurrency: int = 1) -> list[Judgment]:
        firsts = judge_all(self.first, pairs, max_concurrency)
        return [self._combine(a, b) for a, b in zip(firsts, judge_all(self.second, pairs, max_concurrency))]

    def usage(self) -> list[VerifierStats]:
        return verifier_usage(self.first) + verifier_usage(self.second)

    def _combine(self, a: Judgment, b: Judgment) -> Judgment:
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
        return self._accept(a) if self._sure(a) else self._escalate(a, self.second.judge(claim, passage))

    def judge_many(self, pairs: Sequence[tuple[str, str]], max_concurrency: int = 1) -> list[Judgment]:
        """Ask ``first`` about every pair, then ``second`` about the pairs ``first`` was unsure of."""
        firsts = judge_all(self.first, pairs, max_concurrency)
        unsure = [i for i, a in enumerate(firsts) if not self._sure(a)]
        seconds = dict(zip(unsure, judge_all(self.second, [pairs[i] for i in unsure], max_concurrency)))
        return [self._escalate(a, seconds[i]) if i in seconds else self._accept(a) for i, a in enumerate(firsts)]

    def usage(self) -> list[VerifierStats]:
        return verifier_usage(self.first) + verifier_usage(self.second)

    def _sure(self, a: Judgment) -> bool:
        return a.confidence is not None and a.confidence >= self.accept_at

    def _accept(self, a: Judgment) -> Judgment:
        return Judgment(relation=a.relation, rationale=f"{a.verifier}: {a.rationale}", verifier=self.name,
                        confidence=a.confidence, opinions=[a])

    def _escalate(self, a: Judgment, b: Judgment) -> Judgment:
        unsure = "no confidence" if a.confidence is None else f"confidence {a.confidence:.2f} < {self.accept_at}"
        return Judgment(relation=b.relation, verifier=self.name, opinions=[a, b],
                        rationale=f"{a.verifier} unsure ({unsure}), escalated; {b.verifier}: {b.rationale}")
