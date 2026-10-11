"""The fast internal-conflict pass must match a direct pairwise lexical check.

Reports stay byte-identical: seeded random claims, including one-word antonyms the
parser would drop, and a full audit of a seeded memory file.
"""
import random
import sys
from datetime import date

from memfalsify.audit import Issue, audit, issues_for
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


def test_one_term_claim_is_found_from_a_shared_common_term():
    # "never billing" covers all of "billing x y z w" with its single term and only it is negated.
    # The longer claim's rare-term prefix leaves out the common "billing", so it must find the
    # one-term claim from that shared term, or only one side of the pair reports the conflict.
    texts = ["billing approves export quota tenant", "billing approves gateway", "billing x y z w",
             "billing a1 b1 c1 d1 e1", "never billing"]
    claims = [Claim(id=f"c{index}", text=text, path="MEMORY.md", line=index + 1)
              for index, text in enumerate(texts)]
    for claim in claims:
        assert issues_for(claim, claims, AS_OF, 180) == reference_issues_for(claim, claims, AS_OF, 180)
    assert any(issue.kind == "internal_conflict" for issue in issues_for(claims[2], claims, AS_OF, 180))


ZH_SUBJECTS = ("账单服务", "导出接口", "缓存", "网关", "队列", "租户配额", "API", "Q4 发布", "方案", "案")
ZH_FRAGMENTS = ("必须", "无需", "不必", "所有", "任何", "仅", "只有", "不", "没有", "未", "未来", "非常", "非得", "不可",
                "三个月", "3个月", "2个月", "百分之十", "一成", "approve", "reject", "的", "了", "是", "案", "表",
                "，", "。", "开放", "关闭")


def _zh_sentence(rng: random.Random) -> str:
    parts = [rng.choice(ZH_SUBJECTS)]
    pool = ZH_FRAGMENTS + tuple(term for pair in ZH for term in pair)
    parts.extend(rng.choice(pool) for _ in range(rng.randint(1, 5)))
    return rng.choice(("", " ")).join(parts)


def test_indexed_conflicts_match_pairwise_reference_for_chinese():
    # Character coverage, lone characters, per-clause negation and modality are not term counts:
    # pairs with Chinese on either side must still agree with the verifier exactly.
    rng = random.Random(4)
    claims = _claims(7, 25)
    claims += [Claim(id=f"zh-{index}", text=_zh_sentence(rng), path="MEMORY.md", line=100 + index)
               for index in range(90)]
    flagged = 0
    for claim in claims:
        found = issues_for(claim, claims, AS_OF, 180)
        assert found == reference_issues_for(claim, claims, AS_OF, 180), claim.text
        flagged += sum(issue.kind == "internal_conflict" for issue in found)
    assert flagged > 10
    outsider = Claim(id="outsider-zh", text="案 approve", path="MEMORY.md", line=1)
    assert issues_for(outsider, claims, AS_OF, 180) == reference_issues_for(outsider, claims, AS_OF, 180)


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
    # The package root exports the audit function under the module's name, so patch the module object.
    monkeypatch.setattr(sys.modules["memfalsify.audit"], "issues_for", reference_issues_for)
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
