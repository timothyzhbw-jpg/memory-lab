"""The fast internal-conflict pass must match a direct pairwise lexical check.

Reports stay byte-identical: seeded random claims, including one-word antonyms the
parser would drop, and a full audit of a seeded memory file.
"""
import random
from datetime import date

from memfalsify.audit import Issue, audit, audit_claims, issues_for
from memfalsify.memory import Claim, parse_memory
from memfalsify.report import to_markdown
from memfalsify.text import ABSOLUTE, HEDGE, RELATIVE_TIME, explicit_dates
from memfalsify.verify import LexicalVerifier

AS_OF = date(2026, 10, 11)
_LEXICAL = LexicalVerifier()
WORDS = ("redis", "postgres", "kafka", "billing", "export", "staging", "invoice", "tenant", "quota",
         "gateway", "schema", "worker", "cache", "queue")
PAIRS = (("approves", "rejects"), ("approved", "denied"), ("enabled", "disabled"), ("accepted", "declined"),
         ("allows", "blocks"), ("increased", "decreased"), ("public", "private"), ("required", "optional"),
         ("supported", "deprecated"), ("opens", "closes"))
ZH = (("批准", "拒绝"), ("启用", "禁用"), ("允许", "禁止"), ("增加", "减少"), ("通过", "否决"),
      ("开启", "关闭"), ("成功", "失败"), ("支持", "弃用"), ("同意", "拒绝"))


def reference_issues_for(claim, others, as_of, stale_days, conflicts=None):
    """Every other claim, judged both ways. The definition the index has to reproduce."""
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
        if any(_LEXICAL.judge(a, b).relation == "contradicts" for a, b in ((text, other.text), (other.text, text))):
            found.append(Issue(kind="internal_conflict",
                               detail=f"may contradict {other.id} at {other.location}: \"{other.text}\""))
    if claim.also_at:
        found.append(Issue(kind="duplicate", detail="also stated at " + ", ".join(claim.also_at)))
    return found


def _sentence(rng: random.Random) -> str:
    words = [rng.choice(WORDS) for _ in range(rng.randint(3, 12))]
    kind = rng.randrange(12)
    if kind == 0:
        left, right = rng.choice(PAIRS)
        return f"{words[0]} {left} {words[1]} {words[2]}"
    if kind == 1:
        _left, right = rng.choice(PAIRS)
        return f"{' '.join(words)} {right} at {rng.randint(1, 30)}"
    if kind == 2:
        return f"{' '.join(words)} does not {rng.choice(('approve', 'enable', 'allow', 'skip'))} it"
    if kind == 3:
        left, right = rng.choice(ZH)
        return f"团队{rng.choice((left, right))}了{words[0]}方案{rng.randint(1, 9)}"
    if kind == 4:
        return f"{' '.join(words)} always probably next week"
    if kind == 5:
        return (f"{rng.randint(2020, 2026):04d}-{rng.randint(1, 12):02d}-{rng.randint(1, 28):02d}: "
                f"{' '.join(words[:4])}")
    if kind == 6:
        return f"{' '.join(words)} at {rng.choice((10, 12, 60, 100))} per minute"
    if kind == 7:
        return " ".join(rng.choice(("the", "a", "of", "and", "not", "no")) for _ in range(rng.randint(1, 8)))
    if kind == 8:
        return rng.choice(("approved", "rejected", "disabled", "enabled", "true", "false", "拒绝", "批准", "禁用"))
    if kind == 9:
        left, right = rng.choice(ZH)
        return f"完全{left}但是并不{right}事项{rng.randrange(5)}"
    if kind == 10:
        return (f"Feature {words[0]} is {rng.choice(('approved', 'rejected'))} for the {words[1]} release "
                f"at {rng.randint(1, 9)}")
    return " ".join(words) + (" today" if rng.random() < 0.4 else "")


def _claims(seed: int, count: int) -> list[Claim]:
    rng = random.Random(seed)
    claims = []
    for index in range(count):
        claim_id = f"id-{index}"
        if claims and rng.random() < 0.07:
            claim_id = rng.choice(claims).id
        claims.append(Claim(id=claim_id, text=_sentence(rng),
                            path=rng.choice(("MEMORY.md", "infra.md", "doc/a.md")),
                            line=index + 1, section=rng.choice((None, "Plan", "计划")),
                            also_at=["x.md:2"] if rng.random() < 0.1 else []))
    claims.append(claims[0])  # same object twice: both copies are reported, in list order
    return claims


def test_indexed_conflicts_match_pairwise_reference():
    claims = _claims(20261011, 80)
    extra = [Claim(id="outsider", text="billing rejects export quota", path="MEMORY.md", line=3),
             Claim(id="one", text="rejected", path="MEMORY.md", line=4),
             Claim(id="zh", text="拒绝", path="MEMORY.md", line=5)]
    for claim in (*claims, *extra):
        assert issues_for(claim, claims, AS_OF, 180) == reference_issues_for(claim, claims, AS_OF, 180)


def test_random_audit_report_matches_reference(tmp_path, monkeypatch):
    rng = random.Random(20261011)
    lines = ["# Memory", ""]
    for index in range(45):
        if index % 9 == 0:
            lines.append(f"## Group {index // 9}")
        sentence = _sentence(rng).replace(".", "") + "."
        lines.append(f"- {sentence}")
        if index % 12 == 0:
            lines.append(f"- {sentence}")
    memory = tmp_path / "MEMORY.md"
    memory.write_bytes(("\n".join(lines) + "\n").encode("utf-8"))
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    (evidence / "notes.md").write_bytes(
        "The billing service approves export at 10 per minute.\n"
        "The billing service rejects export at 12 per minute.\n".encode("utf-8"))
    optimized = audit(memory, evidence, as_of=AS_OF)
    monkeypatch.setattr("memfalsify.audit.issues_for", reference_issues_for)
    reference = audit(memory, evidence, as_of=AS_OF)
    assert optimized.model_dump_json() == reference.model_dump_json()
    assert to_markdown(optimized) == to_markdown(reference)
    # The file really did parse, and the reference was not a no-op on an empty report.
    assert len(parse_memory(memory)[0]) >= 30
    assert any(issue.kind == "internal_conflict" for finding in optimized.findings for issue in finding.issues)


def test_pairwise_reference_on_more_seeds():
    for seed in range(4):
        claims = _claims(seed, 30)
        for claim in claims:
            assert issues_for(claim, claims, AS_OF, 90) == reference_issues_for(claim, claims, AS_OF, 90)


def test_one_term_claim_conflicts_when_its_only_term_is_common():
    """A one-term claim has no rare-term prefix. Negation and a different number still contradict.

    Pads make ``billing`` and ``批准`` more common than the unique tokens, so a longer claim's
    prefix omits them. Both sides of the pair have to report the conflict, and ``audit_claims``
    (one shared index) has to agree with a fresh index per claim.
    """
    unique = " ".join(f"uniq{i}xx" for i in range(12))
    zh_unique = " ".join(f"zhuniq{i}xx" for i in range(12))
    claims = [
        Claim(id="long", text=f"billing {unique}", path="MEMORY.md", line=1),
        Claim(id="neg", text="not billing", path="MEMORY.md", line=2),
        Claim(id="numbered", text=f"billing {unique} 10", path="MEMORY.md", line=3),
        Claim(id="other-number", text="billing 99", path="MEMORY.md", line=4),
        Claim(id="zh-long", text=f"批准 {zh_unique}", path="MEMORY.md", line=5),
        Claim(id="zh-neg", text="不批准", path="MEMORY.md", line=6),
    ]
    claims += [Claim(id=f"pad-{i}", text=f"billing pad{i}xx", path="MEMORY.md", line=10 + i) for i in range(6)]
    claims += [Claim(id=f"zhpad-{i}", text=f"批准 zpad{i}xx", path="MEMORY.md", line=20 + i) for i in range(6)]
    report = audit_claims(claims, memory_file="MEMORY.md", files_read=["MEMORY.md"], memory_paths=[], as_of=AS_OF)
    by_id = {finding.memory_id: finding for finding in report.findings}
    assert set(by_id) == {claim.id for claim in claims}
    for claim in claims:
        reference = reference_issues_for(claim, claims, AS_OF, 180)
        assert by_id[claim.id].issues == reference
        assert issues_for(claim, claims, AS_OF, 180) == reference
    outsider = Claim(id="outsider", text="not billing", path="MEMORY.md", line=40)
    assert issues_for(outsider, claims, AS_OF, 180) == reference_issues_for(outsider, claims, AS_OF, 180)


def test_rare_prefix_keeps_the_marginal_shared_term():
    """Sharing exactly ``k`` terms must still be found when only the least-rare prefix term overlaps.

    Twelve copies make the six ``common`` tokens more frequent than ``marginalqq``, which is
    itself more frequent than the tokens that appear once. The prefix of the approved claim is
    seven terms and ``marginalqq`` is the last of them; a prefix one term shorter would miss
    the rejected claim.
    """
    commons = " ".join(f"common{i}qq" for i in range(6))
    approved = "approved " + commons + " marginalqq " + " ".join(f"rare{i}qq" for i in range(5))
    rejected = "rejected " + commons + " marginalqq " + " ".join(f"bonly{i}qq" for i in range(6))
    claims = [Claim(id="approved", text=approved, path="MEMORY.md", line=1),
              Claim(id="rejected", text=rejected, path="MEMORY.md", line=2)]
    claims += [Claim(id=f"pad-{i}", text=f"{commons} pad{i}qq", path="MEMORY.md", line=10 + i) for i in range(12)]
    for claim in claims:
        assert issues_for(claim, claims, AS_OF, 180) == reference_issues_for(claim, claims, AS_OF, 180)
