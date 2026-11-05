"""The audit's shortcuts on large repositories give the same results as the walks they replaced.

``walked`` below is the shortlist from before the index, kept word for word as the reference. The guards at the end
count work instead of measuring time, so they cannot be flaky.
"""
from __future__ import annotations

import gc
import importlib
import random
import subprocess
import sys
from datetime import date
from pathlib import Path

import pytest

from conftest import write_text_exact
from memcore import make_span, sha256_bytes
from memcore.paths import below
from memcore.reading import read_limits, take
from memfalsify import Claim, LexicalVerifier, audit, claim_id
from memfalsify.audit import _PassageIndex, shortlist
from memfalsify.corpus import Passage, out_of_collection, scan
from memfalsify.text import features, normalize, sentence_spans
from memfalsify.verify import coverage

text_module = importlib.import_module("memfalsify.text")
audit_module = importlib.import_module("memfalsify.audit")  # memfalsify.audit is also the name of a function
AS_OF = date(2026, 11, 4)
THRESHOLDS = (0.0, 0.2, 0.34, 0.5, 0.6, 0.67, 0.75, 1.0, 1.01)
LIMITS = (None, 0, 1, 2, 3, 8)

EVIDENCE = {
    # Three sentences on one line tie on score, shared terms and location, so only their order separates them.
    "ties.md": "Deploys approve the release. Deploys approve the release again. Deploys approve the release now.\n"
               "Deploys reject the release. The release is not approved by deploys.\n",
    # Line 10 sorts before line 9 as a string, which is how the shortlist has always ordered them.
    "lines.md": "".join(f"Backups run nightly on host {n}.\n" for n in range(1, 13)),
    "z-last.md": "Backups run nightly on host 1.\nBackups run nightly.\n",
    "a-first.md": "Backups run nightly on host 1.\nBackups run nightly.\n",
    "numbers.md": "The rate limit is 60 requests per minute. The rate limit is 120 requests per minute.\n"
                  "The limit changed on 2026-10-11. Rate limit: 60.\n",
    "opposites.md": "Feature flags are enabled.\nFeature flags are disabled.\nFlags are enabled and disabled.\n"
                    "Enabled.\nDisabled.\nEverything else is open.\nThe gate is closed.\nThe gate is open.\n"
                    "Uploads are public. Uploads are private.\nThe build passes.\nThe build fails.\n",
    "zh.md": "部署需要批准。部署被拒绝。上线需要三个审批人。上线需要 3 个审批人。费用是1200万元。费用是一千二百万元。\n"
             "仅管理员可以批准发布。所有管理员都可以批准发布。批准。拒绝。元。元旦放假。\n"
             "支持 API 调用。弃用 API 调用。第四季度上线。Q4 上线。启用缓存。禁用缓存。\n",
    "wide.md": "ＡＰＩ rate limit is １２０ requests.\nThe API rate limit is 120 requests.\n",
    "stems.md": "Approvals were approved while approving.\nA reviewer approves.\nReviewing is required.\n"
                "Reviews are optional.\nmigration migrations migrate migrated\n",
    "long.md": "One two three four five six seven eight nine ten.\nOne two three four five.\nSix seven eight nine ten.\n"
               "Ten.\nOne ten.\nTwo three.\n",
    "nested/deep/note.md": "Deploys approve the release.\nBackups run nightly on host 1.\n",
}
MEMORY = """- Deploys approve the release.
- Deploys reject the release.
- Backups run nightly.
- Backups run nightly on host 1.
- Nightly.
- The rate limit is 60 requests per minute.
- The rate limit is 90 requests per minute.
- Rate limit.
- Feature flags are enabled.
- Flags are disabled.
- Disabled.
- Enabled.
- The gate is closed.
- Uploads are public.
- The build passes.
- 部署需要批准。
- 部署被否决。
- 上线需要三个审批人。
- 费用是1200万元。
- 所有管理员都可以批准发布。
- 仅管理员可以批准发布。
- 元。
- 支持 API 调用。
- 第四季度上线。
- ＡＰＩ rate limit is １２０ requests.
- Reviewers approve migrations.
- Reviewing is optional.
- One two three four five six seven eight nine ten.
- One two three four.
- Ten nine.
- Unmatched claim about zygomorphic quokkas.
- 12345.
"""


def walked(claim, passages, min_coverage=0.5, max_judged=None):
    """The shortlist before the index: every claim walks every passage."""
    words, _ = features(claim.text)
    if not words:
        return []
    ranked = []
    for passage in passages[:take("comparisons", len(passages))]:
        shared = len(words & passage.words)
        score = coverage(claim.text, passage.span.quote) if shared else 0.0
        if shared and score >= min_coverage:
            ranked.append((-score, -shared, passage.span.location, passage))
    ranked.sort(key=lambda item: item[:3])
    return [passage for *_, passage in ranked[:max_judged]]


def identities(passages):
    return [id(passage) for passage in passages]


def corpus(root: Path, files: dict[str, str]) -> list[Passage]:
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        write_text_exact(path, text)
    return scan(root)[0]


def claims_of(memory: str) -> list[Claim]:
    """One claim per line, as written: this includes the one-word claims that ``parse_memory`` passes over."""
    lines = [line.removeprefix("- ") for line in memory.splitlines()]
    return [Claim(id=claim_id(text), text=text, path="MEMORY.md", line=number) for number, text in enumerate(lines, 1)]


@pytest.fixture
def built(tmp_path):
    """(claims, passages, evidence directory), with the claims in tmp_path/MEMORY.md too."""
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    write_text_exact(tmp_path / "MEMORY.md", MEMORY)
    return claims_of(MEMORY), corpus(evidence, EVIDENCE), evidence


def test_the_built_corpus_reaches_what_it_is_for(built):
    claims, passages, _ = built
    assert len(passages) > 60 and len(claims) > 30
    # Something ties, something is Chinese, something is opposed, something shares nothing.
    locations = [p.location for p in passages]
    assert len(set(locations)) < len(locations)
    assert any("部署" in claim.text for claim in claims)
    assert any(walked(claim, passages) for claim in claims if "部署" in claim.text)
    assert any(claim.text.startswith("Unmatched") and not walked(claim, passages) for claim in claims)
    disabled = next(claim for claim in claims if claim.text == "Flags are disabled.")
    assert any(p.quote == "Feature flags are enabled." for p in walked(disabled, passages, 0.67))


@pytest.mark.parametrize("min_coverage", THRESHOLDS)
@pytest.mark.parametrize("max_judged", LIMITS)
def test_the_index_gives_the_walks_shortlist_in_the_walks_order(built, min_coverage, max_judged):
    claims, passages, _ = built
    shared = _PassageIndex(passages, claims)
    for claim in claims:
        expected = identities(walked(claim, passages, min_coverage, max_judged))
        assert identities(shortlist(claim, passages, min_coverage, max_judged, shared)) == expected, claim.text
        assert identities(shortlist(claim, passages, min_coverage, max_judged)) == expected, claim.text


def test_ties_keep_the_order_they_have_always_had(tmp_path):
    passages = corpus(tmp_path, {
        "b.md": "Cache warms early. Cache warms late.\n",
        "a.md": "".join(f"Cache warms {n}.\n" for n in range(1, 12)),
    })
    claim, = claims_of("Cache warms.")
    expected = [
        # Same score and shared terms: by location as a string, so a.md:10 and a.md:11 come before a.md:2.
        "a.md:1", "a.md:10", "a.md:11", "a.md:2", "a.md:3", "a.md:4", "a.md:5", "a.md:6", "a.md:7", "a.md:8", "a.md:9",
        "b.md:1", "b.md:1",
    ]
    picked = shortlist(claim, passages)
    assert [p.location for p in picked] == expected == [p.location for p in walked(claim, passages)]
    # And on one line, in the order they were read.
    assert [p.quote for p in picked[-2:]] == ["Cache warms early.", "Cache warms late."]


VOCABULARY = """approve reject approved rejected enable disable allow block pass fail add remove start stop open close
public private required optional deploy deploys release releases backup backups host nightly the is not never no
without always 60 120 3.5 2026-10-11 Q4 A API api 批准 拒绝 否决 同意 启用 禁用 开启 关闭 部署 上线 审批人 管理员 费用 元
不 所有 仅 必须 无需 三个月 1200万 ＡＰＩ １２０""".split()


def _random_case(seed: int) -> tuple[dict[str, str], str]:
    pick = random.Random(seed)

    def sentence() -> str:
        return " ".join(pick.choice(VOCABULARY) for _ in range(pick.randint(1, 9))) + pick.choice(".。!")

    files = {}
    for number in range(12):
        lines = [" ".join(sentence() for _ in range(pick.randint(1, 3))) for _ in range(pick.randint(1, 14))]
        files[f"dir{number % 3}/f{number}.md"] = "\n".join(lines) + "\n"
    return files, "\n".join(sentence() for _ in range(50))


@pytest.mark.parametrize("seed", range(6))
def test_the_index_agrees_with_the_walk_on_generated_text(tmp_path, seed):
    files, memory = _random_case(20261104 + seed)
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    passages = corpus(evidence, files)
    claims = claims_of(memory)
    assert len(passages) > 50
    shared = _PassageIndex(passages, claims)
    matched = 0
    for min_coverage in (0.0, 0.2, 0.5, 0.6, 1.0):
        for max_judged in (None, 2):
            for claim in claims:
                expected = identities(walked(claim, passages, min_coverage, max_judged))
                matched += bool(expected)
                assert identities(shortlist(claim, passages, min_coverage, max_judged, shared)) == expected, claim.text
    assert matched > 100


def test_dropping_any_candidate_from_the_index_is_noticed(built):
    """What gives the tests above their teeth: an index that loses one passage no longer matches the walk."""
    claims, passages, _ = built
    checked = 0
    for claim in claims:
        for min_coverage in (0.5, 0.6):
            for passage in walked(claim, passages, min_coverage):
                position = next(at for at, candidate in enumerate(passages) if candidate is passage)
                index = _PassageIndex(passages, [claim])
                for posting in index.postings.values():
                    if position in posting:
                        posting.remove(position)
                assert identities(shortlist(claim, passages, min_coverage, None, index)) != \
                    identities(walked(claim, passages, min_coverage)), (claim.text, passage.quote)
                checked += 1
    assert checked > 100


def test_a_passage_found_only_through_an_opposing_term_is_kept(tmp_path):
    """Half of "Gates are closed nightly" is shared, which is not enough; "open" against "closed" makes up the rest."""
    passages = corpus(tmp_path, {"a.md": "Gates are open today.\nGates are shut today.\n"})
    claim, = claims_of("Gates are closed nightly.")
    assert [p.quote for p in shortlist(claim, passages, 2 / 3)] == ["Gates are open today."]
    assert identities(shortlist(claim, passages, 2 / 3)) == identities(walked(claim, passages, 2 / 3))


@pytest.mark.parametrize("budget", (0, 1, 7, 60))
def test_a_comparison_budget_cuts_off_the_same_passages(built, budget):
    claims, passages, _ = built
    shared = _PassageIndex(passages, claims)
    for claim in claims:
        with read_limits(items={"comparisons": budget}) as limits:
            expected = identities(walked(claim, passages))
        with read_limits(items={"comparisons": budget}) as ours:
            assert identities(shortlist(claim, passages, 0.5, None, shared)) == expected, claim.text
        assert (ours.used, ours.exhausted, ours.truncated) == (limits.used, limits.exhausted, limits.truncated)


def test_a_passage_builds_the_span_the_scan_used_to_build(tmp_path):
    digest_of = {}
    files = {
        "crlf.md": "First line here.\r\nSecond line here. Third sentence here.\r\n\r\n- Fifth line here.\r\n",
        "zh.md": "第一行有内容。第二句也有。\n\n第四行有内容。\n",
        "clip.mp4.vtt": "WEBVTT\n\n00:00.000 --> 00:02.000\nThe limit is 60 requests.\n\n"
                        "00:02.000 --> 00:04.000\nThe limit is 90 requests.\n",
    }
    for name, text in files.items():
        digest_of[name] = sha256_bytes(text.encode("utf-8"))
    passages = corpus(tmp_path, files)
    assert {p.path for p in passages} == set(files)
    for passage in passages:
        text = files[passage.path]
        span = passage.span
        assert span == make_span(passage.path, digest_of[passage.path], text, passage.start,
                                 passage.start + len(passage.quote), source_kind="file", media=passage.media)
        assert span.line == text.count("\n", 0, passage.start) + 1 and span.location == passage.location
        assert (span.quote, *features(span.quote)) == (passage.quote, passage.words, passage.numbers)


def _report(evidence: Path, memory: Path, verifier, **kwargs) -> str:
    return audit(memory, evidence, verifier=verifier, as_of=AS_OF, follow_links=False, **kwargs).model_dump_json()


class AsksAboutEverything(LexicalVerifier):
    """A subclass may judge differently, so it gets every pair: the behaviour from before the shortcut."""


@pytest.mark.parametrize(("min_coverage", "max_judged"),
                         ((0.5, None), (0.2, None), (0.2, 8), (0.5, 1), (0.75, 2), (0.0, 3)))
def test_not_asking_the_lexical_verifier_about_unrelated_pairs_changes_no_report(built, tmp_path, min_coverage,
                                                                                 max_judged):
    _, _, evidence = built
    memory = tmp_path / "MEMORY.md"
    options = {"min_coverage": min_coverage, "max_judged": max_judged}
    ours = _report(evidence, memory, LexicalVerifier(), **options)
    assert ours == _report(evidence, memory, AsksAboutEverything(), **options)
    assert '"evidence":[{' in ours


def test_a_lexical_verifier_with_its_own_thresholds_gets_the_same_report_too(built, tmp_path):
    _, _, evidence = built
    memory = tmp_path / "MEMORY.md"
    for related, support in ((0.4, 0.5), (0.8, 0.9), (0.5, 0.5)):
        assert _report(evidence, memory, LexicalVerifier(related, support)) == \
            _report(evidence, memory, AsksAboutEverything(related, support))


def test_below_answers_like_relative_to(tmp_path):
    root = tmp_path.resolve()
    pairs = [(root, root), (root / "a", root), (root / "a" / "b.md", root), (root / "a" / ".." / "b", root),
             (root / "a b" / "ü.md", root), (root.parent, root), (root.parent / "other", root),
             (root.parent / (root.name + "-sibling") / "a", root), (Path(root.anchor), root), (root / "a", Path(root.anchor))]
    for path, base in pairs:
        try:
            expected = path.relative_to(base)
        except ValueError:
            with pytest.raises(ValueError):
                below(path, base)
        else:
            assert below(path, base).parts == expected.parts and str(below(path, base)) == str(expected)


# --- the collector -----------------------------------------------------------------------------------------------


def test_the_collector_comes_back_and_nothing_stays_frozen():
    assert gc.isenabled()
    during = []

    def build():
        during.append(gc.isenabled())
        return ["built"]

    with out_of_collection(build) as built_value:
        assert built_value == ["built"] and gc.isenabled() and gc.get_freeze_count() > 0
    assert during == [False] and gc.isenabled() and gc.get_freeze_count() == 0
    with pytest.raises(RuntimeError), out_of_collection(lambda: None):
        raise RuntimeError("the block failed")
    assert gc.isenabled() and gc.get_freeze_count() == 0

    def fail():
        raise RuntimeError("the build failed")

    with pytest.raises(RuntimeError), out_of_collection(fail):
        pass
    assert gc.isenabled() and gc.get_freeze_count() == 0


def test_the_collector_stays_off_if_it_was_off():
    gc.disable()
    try:
        with out_of_collection(list):
            assert not gc.isenabled()
        assert not gc.isenabled() and gc.get_freeze_count() == 0
    finally:
        gc.enable()


def test_an_audit_leaves_the_collector_as_it_found_it(built, tmp_path):
    _, _, evidence = built
    audit(tmp_path / "MEMORY.md", evidence, as_of=AS_OF, follow_links=False)
    assert gc.isenabled() and gc.get_freeze_count() == 0
    with pytest.raises(ValueError):
        audit(tmp_path / "MEMORY.md", evidence, as_of=AS_OF, exclude=["*"])
    assert gc.isenabled() and gc.get_freeze_count() == 0


# --- guards: no wall clock, these count work ---------------------------------------------------------------------


class Untouchable:
    """A passage a claim must not read."""

    def __getattr__(self, name):
        raise AssertionError(f"read .{name} of a passage this claim cannot reach")


def test_a_claim_reads_only_the_passages_it_can_reach(tmp_path):
    files = {f"filler{n}.md": "".join(f"Common words fill sentence {n} {line}.\n" for line in range(40)) for n in range(5)}
    files["rare.md"] = "A zygomorphic flower.\nCommon zygomorphic words.\n"
    passages = corpus(tmp_path, files)
    guarded = [p if p.path == "rare.md" else Untouchable() for p in passages]
    assert len(passages) == 202

    # Nothing else holds these two terms.
    claim, = claims_of("Zygomorphic flowers.")
    assert [p.quote for p in shortlist(claim, guarded, 0.5, None, _PassageIndex(passages, [claim]))] == \
        ["A zygomorphic flower.", "Common zygomorphic words."]

    # Here 200 other passages hold two of the three terms, but all three are needed, so the rarest one decides.
    claim, = claims_of("Common zygomorphic words.")
    index = _PassageIndex(passages, [claim])
    assert sorted(len(index.postings[term]) for term in features(claim.text)[0]) == [2, 201, 201]
    assert [p.quote for p in shortlist(claim, guarded, 1.0, None, index)] == ["Common zygomorphic words."]
    assert [p.quote for p in walked(claim, passages, 1.0)] == ["Common zygomorphic words."]


def test_a_stem_is_worked_out_once_per_token(monkeypatch):
    calls = []
    real = text_module.stem

    def counted(word):
        calls.append(word)
        return real(word)

    monkeypatch.setattr(text_module, "stem", counted)
    text_module._word.cache_clear()
    try:
        for number in range(500):
            features(f"Deployments require approvals before migrations {number}.")
        assert sorted(calls) == ["approvals", "deployments", "migrations", "require"]
    finally:
        text_module._word.cache_clear()


def test_line_numbers_do_not_count_from_the_start_of_the_file(tmp_path, monkeypatch):
    def counting(*_):
        raise AssertionError("line_of counts from the start of the file for every passage")

    monkeypatch.setattr("memcore.evidence.line_of", counting)
    passages = corpus(tmp_path, {"a.md": "".join(f"Sentence number {n} here.\n" for n in range(50))})
    assert [p.line for p in passages] == list(range(1, 51))


def test_the_lexical_verifier_is_not_asked_about_pairs_it_must_call_unrelated(built, tmp_path, monkeypatch):
    _, _, evidence = built
    seen = []
    real = audit_module.judge_all

    def counted(verifier, pairs, max_concurrency=1):
        seen.append(len(pairs))
        for claim, passage in pairs:
            assert coverage(claim, passage) >= verifier.related or type(verifier) is AsksAboutEverything
        return real(verifier, pairs, max_concurrency)

    monkeypatch.setattr(audit_module, "judge_all", counted)
    memory = tmp_path / "MEMORY.md"
    audit(memory, evidence, verifier=LexicalVerifier(), as_of=AS_OF, follow_links=False)
    audit(memory, evidence, verifier=AsksAboutEverything(), as_of=AS_OF, follow_links=False)
    assert 0 < seen[0] < seen[1]


def test_an_audit_scans_and_indexes_once(built, tmp_path, monkeypatch):
    _, _, evidence = built
    counts = {"scan": 0, "index": 0}
    real_scan, real_index = audit_module.scan, audit_module._PassageIndex

    def scan_once(*args, **kwargs):
        counts["scan"] += 1
        return real_scan(*args, **kwargs)

    def index_once(*args, **kwargs):
        counts["index"] += 1
        return real_index(*args, **kwargs)

    monkeypatch.setattr(audit_module, "scan", scan_once)
    monkeypatch.setattr(audit_module, "_PassageIndex", index_once)
    audit(tmp_path / "MEMORY.md", evidence, as_of=AS_OF, follow_links=False)
    assert counts == {"scan": 1, "index": 1}


def test_the_large_repository_benchmark_runs():
    script = Path(__file__).resolve().parent.parent / "benchmarks" / "perf" / "run.py"
    run = subprocess.run([sys.executable, str(script), "--evidence", "400"], capture_output=True, text=True, check=True)
    assert run.stdout.split()[:4] == ["passages", "claims", "audit_s", "peak_rss_mb"]
    assert run.stdout.split()[4:6] == ["400", "200"]


def test_text_without_non_ascii_characters_is_left_as_it_is():
    for text in ("", "plain", "1,000 + 2,000", "a\tb\nc"):
        assert normalize(text) == text
    assert list(sentence_spans("# Title\n\n- One. Two!\n")) == [(2, 7), (11, 15), (16, 20)]
