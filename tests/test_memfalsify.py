import json
import shutil
from datetime import date
from pathlib import Path

import pytest
from typer.testing import CliRunner

from conftest import EXAMPLES, snapshot
from memcore import EvidenceError
from memfalsify.audit import audit
from memfalsify.cli import app
from memfalsify.memory import claim_id, parse_memory
from memfalsify.report import to_markdown
from memfalsify.verify import DualVerifier, Judgment, LexicalVerifier

runner = CliRunner()
AS_OF = date(2026, 10, 10)


def run_demo(root, **kwargs):
    return audit(root / "MEMORY.md", root / "evidence", root / "reviews.jsonl", as_of=AS_OF, **kwargs)


def by_text(report):
    return {f.text: f for f in report.findings}


# --- parsing -----------------------------------------------------------------

def test_parse_follows_links_and_skips_structure(tmp_path):
    (tmp_path / "MEMORY.md").write_text(
        "---\nname: x\n---\n# Heading is not a claim\n"
        "- The cache uses Redis.\n"
        "```\nprint('code is not a claim')\n```\n"
        "- [Infra](infra.md) — index lines point at claims\n"
        "- [Escape](../outside.md) — never followed\n", encoding="utf-8")
    (tmp_path / "infra.md").write_text("---\ndescription: d\n---\nThe queue runs on Kafka.\n"
                                       "- The cache uses Redis.\n", encoding="utf-8")
    (tmp_path.parent / "outside.md").write_text("Outside secrets are here.\n", encoding="utf-8")
    claims, files = parse_memory(tmp_path / "MEMORY.md")
    texts = {c.text: c for c in claims}
    assert set(texts) == {"The cache uses Redis.", "The queue runs on Kafka."}
    assert texts["The cache uses Redis."].also_at == ["infra.md:5"]
    assert [f.name for f in files] == ["MEMORY.md", "infra.md"]


def test_claim_ids_are_stable_and_whitespace_insensitive():
    assert claim_id("Feature A  is approved.") == claim_id("feature a is approved.")
    assert claim_id("Feature A is approved.") != claim_id("Feature A is rejected.")


# --- demo audit ----------------------------------------------------------------

def test_demo_statuses(memfalsify_demo):
    found = by_text(run_demo(memfalsify_demo))
    expected = {
        "Feature A (bulk export) is approved for the November release.": "challenged",
        "Bulk export depends on the billing schema migration.": "supported",
        "Feature A was rejected by the product manager.": "candidate_support",
        "We probably use squash merges for pull requests.": "candidate_support",
        "The public API rate limit is 100 requests per minute.": "candidate_counterevidence",
        "The main database runs PostgreSQL 15.": "candidate_counterevidence",
        "Deployments always go through the staging cluster.": "candidate_counterevidence",
        "The bulk export will ship next week.": "insufficient_evidence",
        "2026-03-02: Feature B was postponed to Q3.": "insufficient_evidence",
        "Priya owns the payments integration.": "insufficient_evidence",
    }
    assert {t: f.status for t, f in found.items()} == expected


def test_demo_issues(memfalsify_demo):
    found = by_text(run_demo(memfalsify_demo))
    kinds = lambda text: {i.kind for i in found[text].issues}
    assert "overgeneralization" in kinds("Deployments always go through the staging cluster.")
    assert "relative_time" in kinds("The bulk export will ship next week.")
    assert "possibly_stale" in kinds("2026-03-02: Feature B was postponed to Q3.")
    assert "inference_marker" in kinds("We probably use squash merges for pull requests.")
    assert "internal_conflict" in kinds("Feature A was rejected by the product manager.")
    assert "duplicate" in kinds("Bulk export depends on the billing schema migration.")


def test_every_cited_quote_is_exact(memfalsify_demo):
    for finding in run_demo(memfalsify_demo).findings:
        for e in finding.evidence:
            text = (memfalsify_demo / "evidence" / e.span.path).read_text(encoding="utf-8")
            assert text[e.span.start:e.span.end] == e.span.quote


def test_proposals_never_contain_replacement_text(memfalsify_demo):
    for finding in run_demo(memfalsify_demo).findings:
        assert all(p.replacement is None for p in finding.proposals)


def test_candidates_alone_never_decide(memfalsify_demo):
    report = audit(memfalsify_demo / "MEMORY.md", memfalsify_demo / "evidence", as_of=AS_OF)
    assert not {"supported", "challenged", "conflicted"} & {f.status for f in report.findings}


def test_memory_files_are_excluded_from_evidence(memfalsify_demo):
    report = audit(memfalsify_demo / "MEMORY.md", memfalsify_demo, as_of=AS_OF)
    cited = {e.span.path for f in report.findings for e in f.evidence}
    assert not cited & {"MEMORY.md", "infra.md", "report.md"}
    assert any(s.path == "report.md" and "report" in s.reason for s in report.skipped_evidence)


@pytest.mark.parametrize("pattern", ["docs/**", "docs/*", "docs", "./docs/"])
def test_exclude_drops_whole_directories_silently(memfalsify_demo, pattern):
    deep = memfalsify_demo / "evidence" / "docs" / "v1" / "old"
    deep.mkdir(parents=True)
    (deep / "api.md").write_text("The public API rate limit is 5 requests per minute.\n", encoding="utf-8")
    cited = lambda report: {e.span.path for f in report.findings for e in f.evidence}
    assert "docs/v1/old/api.md" in cited(audit(memfalsify_demo / "MEMORY.md", memfalsify_demo / "evidence",
                                               as_of=AS_OF))
    report = audit(memfalsify_demo / "MEMORY.md", memfalsify_demo / "evidence", as_of=AS_OF,
                   exclude=[pattern, "*.png"])
    assert not any(path.startswith("docs/") for path in cited(report))
    assert by_text(report)["The public API rate limit is 100 requests per minute."].status == "insufficient_evidence"
    assert report.skipped_evidence == [] and report.excluded == [pattern, "*.png"]


def test_exclude_matches_paths_relative_to_evidence_root(memfalsify_demo):
    report = audit(memfalsify_demo / "MEMORY.md", memfalsify_demo / "evidence", as_of=AS_OF,
                   exclude=["notes/2026-10-07-*.md", "evidence/docs/**"])
    cited = {e.span.path for f in report.findings for e in f.evidence}
    assert "notes/2026-10-07-decision-log.md" not in cited and "docs/api.md" in cited


def test_cli_exclude_is_repeatable_and_needs_evidence(memfalsify_demo):
    result = runner.invoke(app, cli_args(memfalsify_demo, "--exclude", "docs/**", "-x", "media/*"))
    assert result.exit_code == 0 and "Excluded from evidence: `docs/**`, `media/*`" in result.output
    assert "docs/api.md" not in result.output and "whiteboard.png" not in result.output
    result = runner.invoke(app, ["audit", str(memfalsify_demo / "MEMORY.md"), "--exclude", "docs/**"])
    assert result.exit_code == 2 and "--exclude requires --evidence" in result.output


def test_media_is_reported_not_read(memfalsify_demo):
    skipped = run_demo(memfalsify_demo).skipped_evidence
    assert [s.path for s in skipped] == ["media/whiteboard.png"]


def test_evidence_symlink_escape_is_skipped(memfalsify_demo, tmp_path):
    (tmp_path / "secret.md").write_text("The public API rate limit is 5 requests per minute.", encoding="utf-8")
    (memfalsify_demo / "evidence" / "leak.md").symlink_to(tmp_path / "secret.md")
    report = run_demo(memfalsify_demo)
    assert any(s.path == "leak.md" and "escapes" in s.reason for s in report.skipped_evidence)
    assert all(e.span.path != "leak.md" for f in report.findings for e in f.evidence)


@pytest.mark.parametrize("patterns", [["*"], ["**"], ["docs/**", "notes/**", "media/**", "CONTRIBUTING.md"]])
def test_excluding_all_evidence_fails_closed(memfalsify_demo, patterns):
    with pytest.raises(EvidenceError, match="no evidence passages indexed with --exclude"):
        audit(memfalsify_demo / "MEMORY.md", memfalsify_demo / "evidence", exclude=patterns)
    result = runner.invoke(app, cli_args(memfalsify_demo, "--strict",
                                        *[arg for p in patterns for arg in ("--exclude", p)]))
    assert result.exit_code == 2 and "no evidence passages indexed" in result.output


def test_empty_evidence_without_exclusions_remains_unverified(memfalsify_demo, tmp_path):
    evidence = tmp_path / "empty"
    evidence.mkdir()
    report = audit(memfalsify_demo / "MEMORY.md", evidence)
    assert report.findings and set(report.summary) == {"insufficient_evidence"}


def test_broken_and_cyclic_links_are_reported(memfalsify_demo):
    evidence = memfalsify_demo / "evidence"
    (evidence / "broken.md").symlink_to("absent.md")
    (evidence / "loop.md").symlink_to("loop.md")
    report = run_demo(memfalsify_demo)
    skipped = {s.path: s.reason for s in report.skipped_evidence}
    assert skipped["broken.md"] == skipped["loop.md"] == "broken or cyclic symlink"
    assert any(f.status == "candidate_counterevidence" for f in report.findings)
    excluded = audit(memfalsify_demo / "MEMORY.md", evidence, exclude=["loop.md"])
    assert "loop.md" not in {s.path for s in excluded.skipped_evidence}


def test_symlinked_directories_are_reported_unless_excluded(memfalsify_demo, tmp_path):
    evidence = memfalsify_demo / "evidence"
    (evidence / "alias").symlink_to(evidence / "docs", target_is_directory=True)
    (evidence / "outside").symlink_to(tmp_path, target_is_directory=True)
    report = run_demo(memfalsify_demo)
    skipped = {s.path: s.reason for s in report.skipped_evidence}
    assert skipped["alias"] == skipped["outside"] == "symlinked directory not followed"
    assert not any(e.span.path.startswith(("alias/", "outside/")) for f in report.findings for e in f.evidence)
    report = audit(memfalsify_demo / "MEMORY.md", evidence, exclude=["alias/**", "outside"])
    assert not {"alias", "outside"} & {s.path for s in report.skipped_evidence}


def test_unreadable_evidence_fails_closed(memfalsify_demo, monkeypatch):
    import memfalsify.corpus as corpus
    original = corpus.read_input_bytes

    def read(root, path):
        if Path(path).name == "api.md":
            raise PermissionError("cannot read api.md")
        return original(root, path)

    monkeypatch.setattr(corpus, "read_input_bytes", read)
    result = runner.invoke(app, cli_args(memfalsify_demo))
    assert result.exit_code == 2 and "cannot read api.md" in result.output


def test_unreadable_evidence_directory_fails_closed(memfalsify_demo, monkeypatch):
    import memfalsify.corpus as corpus

    def walk(root, onerror=None):
        onerror(PermissionError("cannot traverse docs"))
        yield root, [], []

    monkeypatch.setattr(corpus, "walk_files", walk)
    result = runner.invoke(app, cli_args(memfalsify_demo))
    assert result.exit_code == 2 and "cannot traverse docs" in result.output


# --- reviews ---------------------------------------------------------------------

def test_review_with_unknown_memory_fails(memfalsify_demo):
    reviews = memfalsify_demo / "reviews.jsonl"
    reviews.write_text(json.dumps({"memory_id": "m-00000000", "relation": "supports", "path": "docs/api.md",
                                   "quote": "The public API", "reviewer": "r"}) + "\n", encoding="utf-8")
    with pytest.raises(EvidenceError, match="unknown memory_id"):
        run_demo(memfalsify_demo)


def test_review_quote_must_be_verbatim(memfalsify_demo):
    reviews = memfalsify_demo / "reviews.jsonl"
    reviews.write_text(json.dumps({"memory_id": claim_id("Priya owns the payments integration."),
                                   "relation": "supports", "path": "docs/api.md",
                                   "quote": "Priya owns payments.", "reviewer": "r"}) + "\n", encoding="utf-8")
    with pytest.raises(EvidenceError, match="not found verbatim"):
        run_demo(memfalsify_demo)


def test_confirmed_both_ways_is_conflicted(memfalsify_demo):
    with (memfalsify_demo / "reviews.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"memory_id": claim_id("Feature A (bulk export) is approved for the November release."),
                                 "relation": "supports", "path": "docs/api.md", "quote": "The public API",
                                 "reviewer": "careless"}) + "\n")
    found = by_text(run_demo(memfalsify_demo))
    assert found["Feature A (bulk export) is approved for the November release."].status == "conflicted"


# --- verifiers ---------------------------------------------------------------------

class Fixed:
    def __init__(self, name, relation):
        self.name, self.relation = name, relation

    def judge(self, claim, passage):
        return Judgment(relation=self.relation, rationale="fixed", verifier=self.name)


def test_dual_verifier_keeps_disagreement(memfalsify_demo):
    report = run_demo(memfalsify_demo, verifier=DualVerifier(LexicalVerifier(), Fixed("jev-stub", "supports")))
    found = by_text(report)
    assert found["The main database runs PostgreSQL 15."].status == "disputed"
    evidence = found["The main database runs PostgreSQL 15."].evidence[0]
    assert evidence.relation == "disputed" and "jev-stub says supports" in evidence.rationale


def test_dual_verifier_agreement_passes_through():
    judgment = DualVerifier(Fixed("a", "contradicts"), Fixed("b", "contradicts")).judge("x", "y")
    assert judgment.relation == "contradicts" and len(judgment.opinions) == 2


@pytest.mark.parametrize("claim, passage, relation", [
    ("Feature A is approved.", "Product manager rejects Feature A.", "contradicts"),
    ("Feature A is approved.", "Customer requests Feature A again; no approval is given.", "contradicts"),
    ("The service uses Redis for caching.", "The service uses Redis for caching sessions.", "supports"),
    ("The service uses Redis for caching.", "Redis was mentioned in a meetup talk.", "unrelated"),
    ("功能A已批准", "产品经理拒绝了功能A", "contradicts"),
])
def test_lexical_verifier(claim, passage, relation):
    assert LexicalVerifier().judge(claim, passage).relation == relation


# --- CLI ---------------------------------------------------------------------------

def cli_args(root, *extra):
    return ["audit", str(root / "MEMORY.md"), "-e", str(root / "evidence"), "-r", str(root / "reviews.jsonl"),
            "--as-of", "2026-10-10", *extra]


def test_cli_is_read_only(memfalsify_demo):
    before = snapshot(memfalsify_demo)
    result = runner.invoke(app, cli_args(memfalsify_demo))
    assert result.exit_code == 0 and "CHALLENGED" in result.output
    assert snapshot(memfalsify_demo) == before


def test_cli_json(memfalsify_demo):
    result = runner.invoke(app, cli_args(memfalsify_demo, "--format", "json"))
    data = json.loads(result.output)
    assert data["mode"] == "read_only" and data["summary"]["challenged"] == 1


@pytest.mark.parametrize("fmt", ["markdown", "json"])
def test_cli_output_preserves_report_bytes(memfalsify_demo, tmp_path, fmt):
    output = tmp_path / "report"
    report = run_demo(memfalsify_demo)
    expected = to_markdown(report) if fmt == "markdown" else report.model_dump_json(indent=2) + "\n"
    result = runner.invoke(app, cli_args(memfalsify_demo, "--format", fmt, "-o", str(output)))
    assert result.exit_code == 0, result.output
    assert output.read_bytes() == expected.encode("utf-8")


def test_cli_refuses_to_overwrite_memory(memfalsify_demo):
    before = (memfalsify_demo / "infra.md").read_bytes()
    result = runner.invoke(app, cli_args(memfalsify_demo, "-o", str(memfalsify_demo / "infra.md")))
    assert result.exit_code == 2 and (memfalsify_demo / "infra.md").read_bytes() == before


def test_cli_strict_exit_code(memfalsify_demo):
    assert runner.invoke(app, cli_args(memfalsify_demo, "--strict")).exit_code == 1


def test_cli_bad_reviews_exit_2(memfalsify_demo):
    (memfalsify_demo / "reviews.jsonl").write_text("{not json}\n", encoding="utf-8")
    result = runner.invoke(app, cli_args(memfalsify_demo))
    assert result.exit_code == 2 and "invalid JSON" in result.output


def test_cli_claims_lists_ids(memfalsify_demo):
    result = runner.invoke(app, ["claims", str(memfalsify_demo / "MEMORY.md")])
    rows = [json.loads(line) for line in result.output.splitlines()]
    assert {"memory_id": claim_id("Priya owns the payments integration."), "location": "MEMORY.md:15",
            "text": "Priya owns the payments integration."} in rows


def test_chinese_example(tmp_path, monkeypatch):
    monkeypatch.chdir(shutil.copytree(EXAMPLES / "memfalsify-zh", tmp_path / "zh"))
    report = audit(Path("MEMORY.md"), Path("evidence"), as_of=AS_OF)
    found = {f.text: (f.status, sorted(i.kind for i in f.issues)) for f in report.findings}
    assert found == {
        "功能A（批量导出）已批准，随11月版本发布。": ("candidate_counterevidence", []),
        "公开 API 的限流是每分钟 100 次请求。": ("candidate_counterevidence", []),
        "部署总是先经过预发环境。": ("candidate_counterevidence", ["overgeneralization"]),
        "批量导出下周上线。": ("insufficient_evidence", ["relative_time"]),
        "2026年3月2日：功能B推迟到第三季度。": ("insufficient_evidence", ["possibly_stale"]),
        "我们可能使用 squash 合并 PR。": ("insufficient_evidence", ["inference_marker"]),
        "主数据库使用 PostgreSQL 16。": ("candidate_support", []),
    }
    assert Path("report.md").read_text(encoding="utf-8") == to_markdown(report)  # committed report is current
