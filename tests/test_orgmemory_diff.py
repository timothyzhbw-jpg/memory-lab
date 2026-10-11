"""Seeded differential tests: optimized replay and audit match the original algorithms."""
from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone

import pytest

from orgmemory.audit import audit
from orgmemory.models import Event, Policy, PolicyChange
from orgmemory.replay import Dataset, replay
from orgmemory.report import _audit_code, _one_line, _safe, audit_markdown, explain, to_markdown
from orgmemory_reference import (
    reference_audit,
    reference_audit_code,
    reference_audit_markdown,
    reference_explain,
    reference_one_line,
    reference_replay,
    reference_safe,
    reference_to_markdown,
)
from memcore import EvidenceSpan

NOW = datetime(2026, 10, 11, 12, 0, tzinfo=timezone.utc)
ACTORS = {
    "ada": ["product_manager"],
    "ben": ["engineer"],
    "cy": ["account_manager"],
    "dee": ["customer"],
    "gus": ["legal"],
}
PERMISSIONS = {
    "request": ["customer", "account_manager", "engineer", "product_manager"],
    "reconsider": ["customer", "account_manager", "engineer", "product_manager"],
    "raise_risk": ["engineer", "product_manager"],
    "dependency": ["engineer", "product_manager"],
    "comment": ["customer", "account_manager", "engineer", "product_manager", "legal"],
    "approve": ["product_manager"],
    "reject": ["product_manager"],
    "revoke": ["product_manager"],
}
ACTIONS = (
    ("request", 18), ("comment", 14), ("raise_risk", 10), ("dependency", 12),
    ("approve", 14), ("reject", 10), ("reconsider", 12), ("revoke", 10),
)
ACTION_TOTAL = sum(weight for _, weight in ACTIONS)
NOTES = (
    "export reads a schema that is mid-migration",
    "quota can be exceeded",
    'risk with `backticks` and a "quote"',
    "风险: schema, don't ship",
    "\u202eoverride",
    "- looks like a list",
    "1. looks numbered",
)


def _policy() -> Policy:
    return Policy.model_validate({"actors": ACTORS, "permissions": PERMISSIONS})


def _evidence(seed: int) -> dict[str, EvidenceSpan]:
    spans = {}
    captured = datetime(2026, 1, 5, tzinfo=timezone.utc)
    quotes = [f"quote {i} for seed {seed}" for i in range(10)]
    quotes.append("风险 quote")
    quotes.append("quote with `tick` & <tag>")
    for index, quote in enumerate(quotes):
        spans[f"q{index}"] = EvidenceSpan(
            path=f"sources/f{index % 3}.txt", sha256=f"{index:064x}", start=index, end=index + len(quote),
            quote=quote, line=1, id=f"q{index}", source_uri=f"synthetic://q{index}", source_kind="note",
            captured_at=captured,
        )
    return spans


def _choose_action(rng: random.Random, has_prior: bool) -> str:
    pick = rng.randrange(ACTION_TOTAL)
    upto = 0
    chosen = "request"
    for action, weight in ACTIONS:
        upto += weight
        if pick < upto:
            chosen = action
            break
    if chosen == "revoke" and not has_prior:
        return "request"
    return chosen


def random_dataset(seed: int, count: int) -> Dataset:
    """A valid in-memory dataset. Same seed and count always build the same events."""
    rng = random.Random(seed)
    evidence = _evidence(seed)
    evidence_ids = list(evidence)
    subjects = [f"s{i}" for i in range(8)] + ["dep:core", "subj`tick"]
    names = list(ACTORS) + ["mallory"]
    permitted = {
        action: [name for name, roles in ACTORS.items() if set(roles) & set(PERMISSIONS[action])]
        for action, _weight in ACTIONS
    }
    clock = datetime(2026, 1, 5, 9, 0, tzinfo=timezone.utc)
    prior: list[str] = []
    events = []
    for index in range(count):
        action = "request" if index == 0 else _choose_action(rng, bool(prior))
        if index == 0:
            actor, subject = "dee", subjects[0]
        else:
            pool = permitted[action]
            actor = rng.choice(pool) if pool and rng.randrange(100) < 70 else rng.choice(names)
            subject = subjects[int(rng.random() ** 2 * len(subjects))]
        cited = [evidence_ids[rng.randrange(len(evidence_ids))]]
        if rng.randrange(4) == 0:
            extra = evidence_ids[rng.randrange(len(evidence_ids))]
            if extra not in cited:
                cited.append(extra)
        record = {
            "id": f"e{index:04d}",
            "timestamp": clock,
            "actor": actor,
            "action": action,
            "subject": subject,
            "evidence_ids": cited,
        }
        if action == "raise_risk":
            record["note"] = NOTES[rng.randrange(len(NOTES))]
        elif action == "dependency":
            record["dependency"] = subjects[rng.randrange(len(subjects))] if rng.randrange(5) else f"missing-{rng.randrange(4)}"
        elif action == "revoke":
            record["supersedes"] = prior[rng.randrange(len(prior))]
        events.append(Event.model_validate(record))
        prior.append(record["id"])
        if rng.randrange(8) != 0:
            clock += timedelta(minutes=1 + rng.randrange(300))
    return Dataset(events, _policy(), evidence)


def _compare(data: Dataset, as_of: datetime | None, stale_days: int) -> None:
    new_replay = replay(data, as_of)
    old_replay = reference_replay(data, as_of)
    assert new_replay.model_dump_json() == old_replay.model_dump_json()
    assert to_markdown(new_replay) == reference_to_markdown(old_replay)
    for subject in new_replay.states:
        assert explain(new_replay, subject) == reference_explain(old_replay, subject)
    new_audit = audit(data, as_of, stale_days=stale_days, now=NOW)
    old_audit = reference_audit(data, as_of, stale_days=stale_days, now=NOW)
    assert new_audit.model_dump_json() == old_audit.model_dump_json()
    assert audit_markdown(new_audit) == reference_audit_markdown(old_audit)


@pytest.mark.parametrize("seed", range(8))
def test_replay_and_audit_match_the_original_algorithm(seed):
    data = random_dataset(seed, 120 + seed * 13)
    cutoffs = [
        None,
        data.events[len(data.events) // 3].timestamp,
        data.events[(2 * len(data.events)) // 3].timestamp,
        datetime(2020, 1, 1, tzinfo=timezone.utc),
        datetime(2030, 1, 1, tzinfo=timezone.utc),
    ]
    for as_of in cutoffs:
        _compare(data, as_of, stale_days=14)
    _compare(data, None, stale_days=0)
    _compare(data, None, stale_days=100000)


def test_larger_random_log_matches(org_demo):
    data = Dataset.load(org_demo)
    _compare(data, None, stale_days=14)
    _compare(data, datetime(2026, 10, 7, 23, 59, 59, tzinfo=timezone.utc), stale_days=14)
    _compare(random_dataset(99, 500), None, stale_days=14)


def test_report_escaping_matches_the_original_renderer():
    samples = [
        "", " ", "  ", "hello", "  - item", "1. item", "1) item", "1.item", "`code`", "a`b``c",
        "\x1b[31m", "\u202e", "\u2028", "\u2029", "\u200b", "风险", "&<>", "\\*_", "### heading",
        "  + plus", "=eq", "\n", "0. ", "10) next", "  2. two", "` spaced `", " `x` ",
    ]
    rng = random.Random(20261011)
    alphabet = [chr(i) for i in list(range(32)) + [127]] + list("abc`*_|#<>& \t-+=.") + ["风险", "\u202e", "\u2028"]
    for _ in range(300):
        samples.append("".join(rng.choice(alphabet) for _ in range(rng.randrange(0, 24))))
    for text in samples:
        assert _one_line(text) == reference_one_line(text)
        assert _safe(text) == reference_safe(text)
        assert _audit_code(text) == reference_audit_code(text)


def dated_dataset(seed: int, count: int, baseline: bool = False) -> Dataset:
    """``random_dataset`` with effective-dated roles: a policy log of grants and revokes (and optionally a
    policy.json as_of), so the optimized replay is diffed with roles that change over time too."""
    base = random_dataset(seed, count)
    rng = random.Random(seed + 1000)
    start, end = base.events[0].timestamp, base.events[-1].timestamp
    as_of = base.events[len(base.events) // 4].timestamp if baseline else None
    policy = Policy.model_validate({"actors": {**ACTORS, "root": ["admin"]},
                                    "permissions": {**PERMISSIONS, "manage_roles": ["admin"]}, "as_of": as_of})
    held = {name: set(roles) for name, roles in policy.actors.items()}
    first = as_of or start
    changes = []
    for index, when in enumerate(sorted(first + (end - first) * rng.random() for _ in range(14))):
        subject = rng.choice(["ada", "ben", "cy", "dee", "mallory"])
        role = rng.choice(["product_manager", "engineer", "customer"])
        roles = held.setdefault(subject, set())
        action = "revoke_role" if role in roles else "grant"
        roles.symmetric_difference_update({role})
        changes.append(PolicyChange.model_validate({
            "id": f"pl{index:02d}", "timestamp": when, "actor": "root", "action": action,
            "subject_actor": subject, "role": role, "evidence_ids": ["q0"]}))
    return Dataset(base.events, policy, base.evidence, changes)


@pytest.mark.parametrize("seed, baseline", [(3, False), (5, True), (11, False)])
def test_dated_roles_match_the_original_algorithm(seed, baseline):
    data = dated_dataset(seed, 160, baseline)
    assert data.authority.dated and data.authority.applied
    for as_of in (None, data.events[len(data.events) // 2].timestamp):
        _compare(data, as_of, stale_days=14)
