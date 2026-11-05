"""Dismissing a false flag: a review with relation unrelated, from the line a report prints to the status that follows."""
import json
import re
import shutil
from pathlib import Path

import pytest
from typer.testing import CliRunner

from conftest import EXAMPLES, write_text_exact
from memcore import EvidenceError, make_span
from memfalsify import Review, audit, load_reviews
from memfalsify.audit import EvidenceFinding, dismissal_line, status_of
from memfalsify.cli import app
from memfalsify.htmlreport import to_html
from memfalsify.memory import claim_id, parse_memory
from memfalsify.report import set_to_markdown, to_markdown

runner = CliRunner()
CLAIM = "The public API allows 100 requests per minute."
QUOTE = "The public API allows 60 requests per minute."
LIMIT = claim_id(CLAIM)


def project(tmp_path: Path, memory: str = f"- {CLAIM}\n", **files: str) -> Path:
    write_text_exact(tmp_path / "MEMORY.md", memory)
    (tmp_path / "evidence").mkdir()
    for name, text in (files or {"api.md": f"{QUOTE}\n"}).items():
        write_text_exact(tmp_path / "evidence" / name.replace("__", "/"), text)
    return tmp_path


def reviews(root: Path, *lines: dict) -> Path:
    path = root / "reviews.jsonl"
    write_text_exact(path, "".join(json.dumps(line, ensure_ascii=False) + "\n" for line in lines))
    return path


def dismissal(quote: str = QUOTE, path: str = "api.md", memory_id: str = LIMIT, reviewer: str = "ada", **more) -> dict:
    return {"memory_id": memory_id, "relation": "unrelated", "path": path, "quote": quote, "reviewer": reviewer, **more}


def run(root: Path, *lines: dict):
    return audit(root / "MEMORY.md", root / "evidence", reviews(root, *lines) if lines else None)


def status(report) -> str:
    return report.findings[0].status


# --- what it does to a status ------------------------------------------------------------------------------------

def test_a_dismissed_candidate_stops_counting_and_the_status_comes_from_what_is_left(tmp_path):
    root = project(tmp_path)
    assert status(run(root)) == "candidate_counterevidence"
    report = run(root, dismissal(note="that is the free tier"))
    finding = report.findings[0]
    assert finding.status == "insufficient_evidence" and finding.evidence == []
    assert [(e.relation, e.confirmed, e.reviewer, e.rationale) for e in finding.dismissed] == [
        ("unrelated", True, "ada", "that is the free tier")]
    assert report.dismissed == {"ada": 1} and report.summary == {"insufficient_evidence": 1}


def test_a_dismissal_leaves_the_other_candidates_alone(tmp_path):
    root = project(tmp_path, **{"api.md": f"{QUOTE}\n", "billing.md": f"{CLAIM}\n"})
    report = run(root, dismissal())
    assert status(report) == "candidate_support"
    assert [e.span.path for e in report.findings[0].evidence] == ["billing.md"]


def test_the_next_candidate_takes_the_place_of_a_dismissed_one(tmp_path):
    """The report shows three passages per claim; one past the cut must show once a person rules out one before it."""
    files = {f"doc{n}.md": f"The public API allows {n}0 requests per minute.\n" for n in range(1, 6)}
    root = project(tmp_path, **files)
    shown = [e.span.path for e in run(root).findings[0].evidence]
    assert len(shown) == 3
    after = run(root, dismissal(f"The public API allows {shown[0][3]}0 requests per minute.", shown[0]))
    assert [e.span.path for e in after.findings[0].evidence] == [*shown[1:], "doc4.md"]


def test_a_dismissal_marks_a_claim_neither_way(tmp_path):
    span = make_span("a.md", "0" * 64, "hello there", 0, 5)
    assert status_of([EvidenceFinding(relation="unrelated", confirmed=True, rationale="r", reviewer="ada", span=span)]) \
        == "insufficient_evidence"
    root = project(tmp_path)
    assert status(run(root, dismissal())) not in ("supported", "challenged", "conflicted")


def test_a_confirmed_review_is_not_taken_away_by_a_dismissal(tmp_path):
    root = project(tmp_path)
    confirm = {**dismissal(), "relation": "contradicts", "reviewer": "bo"}
    report = run(root, confirm, dismissal())
    assert status(report) == "challenged" and report.findings[0].evidence[0].reviewer == "bo"
    assert report.dismissed == {"ada": 1}


def test_a_dismissal_is_for_one_claim(tmp_path):
    other = "The public API rate limit is 100 requests per minute."
    root = project(tmp_path, f"- {CLAIM}\n- {other}\n", **{"api.md": "The public API rate limit is 60 requests per minute.\n"})
    report = run(root, dismissal("The public API rate limit is 60 requests per minute."))
    found = {f.text: f.status for f in report.findings}
    assert found[other] == "candidate_counterevidence" and found[CLAIM] != "candidate_counterevidence"


def test_a_partial_quote_and_the_same_words_elsewhere_in_the_file_are_covered(tmp_path):
    root = project(tmp_path, **{"api.md": f"{QUOTE}\n\nSome other text in between here.\n\n{QUOTE}\n"})
    assert status(run(root)) == "candidate_counterevidence"
    assert status(run(root, dismissal())) == "insufficient_evidence"  # both copies, though the quote finds the first
    assert status(run(root, dismissal("allows 60 requests"))) == "insufficient_evidence"


def test_a_dismissal_of_a_support_candidate_works_the_same(tmp_path):
    root = project(tmp_path, **{"api.md": f"{CLAIM}\n"})
    assert status(run(root)) == "candidate_support"
    assert status(run(root, dismissal(CLAIM))) == "insufficient_evidence"


def test_strict_passes_once_the_flag_is_dismissed(tmp_path, monkeypatch):
    root = project(tmp_path)
    args = ["audit", str(root / "MEMORY.md"), "--evidence", str(root / "evidence"), "--strict"]
    assert runner.invoke(app, args).exit_code == 1
    assert runner.invoke(app, [*args, "--reviews", str(reviews(root, dismissal()))]).exit_code == 0


def test_a_model_is_not_asked_about_a_dismissed_passage(tmp_path):
    from memfalsify.verify import Judgment

    class Counting:
        name = "counting"

        def __init__(self):
            self.asked = []

        def judge(self, claim, passage):
            self.asked.append(passage)
            return Judgment(relation="contradicts", rationale="r", verifier=self.name)

    root = project(tmp_path, **{"api.md": f"{QUOTE}\n", "other.md": f"{CLAIM}\n"})
    model = Counting()
    audit(root / "MEMORY.md", root / "evidence", reviews(root, dismissal()), verifier=model, min_coverage=0.2, max_judged=8)
    assert model.asked == [CLAIM]


def test_a_named_value_found_outside_the_shortlist_can_be_dismissed_too(tmp_path):
    root = project(tmp_path, "- retry_limit (default: 3): How many times a call may be retried by the client.\n",
                   **{"views.py": "retry_limit: int = 5\n"})
    assert status(run(root)) == "candidate_counterevidence"
    claim = claim_id("retry_limit (default: 3): How many times a call may be retried by the client.")
    assert status(run(root, dismissal("retry_limit: int = 5", "views.py", claim))) == "insufficient_evidence"


# --- when it stops applying --------------------------------------------------------------------------------------

def test_a_dismissal_whose_quote_is_gone_stops_applying_and_says_so(tmp_path):
    root = project(tmp_path)
    line = reviews(root, dismissal())
    write_text_exact(root / "evidence" / "api.md", "The public API allows 70 requests per minute.\n")
    report = audit(root / "MEMORY.md", root / "evidence", line)
    assert status(report) == "candidate_counterevidence" and report.dismissed == {}
    [note] = report.skipped_evidence
    assert note.path == "api.md" and note.reason.startswith("review no longer applies: ada dismissed")
    assert "review no longer applies" in to_markdown(report) and "review no longer applies" in to_html(report)


def test_a_dismissal_whose_file_is_gone_stops_applying_and_says_so(tmp_path):
    root = project(tmp_path, **{"api.md": f"{QUOTE}\n", "note.md": "Nothing here.\n"})
    line = reviews(root, dismissal())
    (root / "evidence" / "api.md").unlink()
    report = audit(root / "MEMORY.md", root / "evidence", line)
    assert [s.reason.split(":")[0] for s in report.skipped_evidence] == ["review no longer applies"]


def test_a_dismissal_of_a_claim_that_is_gone_stops_applying_and_says_so(tmp_path):
    root = project(tmp_path)
    report = run(root, dismissal(memory_id="m-00000000"))
    assert status(report) == "candidate_counterevidence"
    assert "m-00000000" in report.skipped_evidence[0].reason
    assert "which the memory no longer has" in report.skipped_evidence[0].reason


def test_confirming_reviews_still_stop_the_audit(tmp_path):
    root = project(tmp_path)
    for line, message in (({**dismissal(), "relation": "contradicts", "memory_id": "m-00000000"}, "unknown memory_id"),
                          ({**dismissal(), "relation": "supports", "quote": "not in the file"}, "quote not found")):
        with pytest.raises(EvidenceError, match=message):
            run(root, line)


def test_without_a_place_for_the_notice_a_dead_dismissal_stops_too(tmp_path):
    root = project(tmp_path)
    claims = {c.id: c for c in parse_memory(root / "MEMORY.md")[0]}
    with pytest.raises(EvidenceError, match="quote not found"):
        load_reviews(reviews(root, dismissal("gone")), root / "evidence", claims)
    with pytest.raises(EvidenceError, match="unknown memory_id"):
        load_reviews(reviews(root, dismissal(memory_id="m-00000000")), root / "evidence", claims)


def test_a_review_line_follows_the_same_rules_as_the_others(tmp_path):
    root = project(tmp_path)
    with pytest.raises(ValueError):
        Review.model_validate({**dismissal(), "relation": "irrelevant"})
    with pytest.raises(ValueError):
        Review.model_validate({**dismissal(), "reviewer": ""})
    with pytest.raises(ValueError):
        Review.model_validate({**dismissal(), "extra": "field"})
    with pytest.raises(EvidenceError):  # a path out of the evidence is refused, as for the other relations
        run(root, dismissal(path="../MEMORY.md"))


def test_the_reviews_file_is_not_evidence_when_it_sits_inside_the_evidence(tmp_path):
    """It quotes the very passages it dismisses, so reading it would flag the claim all over again."""
    root = project(tmp_path)
    line = reviews(root / "evidence", dismissal())
    report = audit(root / "MEMORY.md", root / "evidence", line)
    assert status(report) == "insufficient_evidence" and report.findings[0].evidence == []


# --- the printed line --------------------------------------------------------------------------------------------

PRINTED = re.compile(r"Add to reviews\.jsonl: (`+) ?(.*?) ?\1$", re.MULTILINE)


def printed_lines(markdown: str) -> list[str]:
    return [match.group(2) for match in PRINTED.finditer(markdown)]


def test_the_markdown_report_prints_a_line_that_works_when_pasted(tmp_path):
    root = project(tmp_path)
    markdown = to_markdown(run(root))
    [line] = printed_lines(markdown)
    assert json.loads(line) == dismissal(reviewer="you")
    path = root / "pasted.jsonl"
    write_text_exact(path, line + "\n")
    assert status(audit(root / "MEMORY.md", root / "evidence", path)) == "insufficient_evidence"


def test_only_an_unsettled_flag_gets_the_line(tmp_path):
    root = project(tmp_path, **{"api.md": f"{QUOTE}\n", "billing.md": f"{CLAIM}\n"})
    markdown = to_markdown(run(root))
    assert len(printed_lines(markdown)) == 1 and '"path": "api.md"' in markdown
    confirmed = to_markdown(run(root, {**dismissal(), "relation": "contradicts"}))
    assert printed_lines(confirmed) == []


@pytest.mark.parametrize("quote", [
    "The public API allows 60 `requests` per minute.",
    "The public API allows 60 ``requests`` per `minute`.",
    "The public API allows 60 requests per minute \\ and more.",
    'The public API allows 60 "requests" per minute.',
    "The public API‮ allows 60 requests per minute.",
    "The public API​ allows 60 requests per minute.",
    "公开 API allows 60 requests per minute.",
])
def test_the_printed_line_reads_back_as_the_exact_quote(tmp_path, quote):
    span = make_span("api.md", "0" * 64, quote, 0, len(quote))
    line = dismissal_line(LIMIT, span)
    assert json.loads(line)["quote"] == quote
    assert "‮" not in line and "​" not in line


def test_a_quote_with_odd_characters_can_be_dismissed_from_the_report(tmp_path):
    quote = "The public `API`‮ allows 60 requests per minute."
    root = project(tmp_path, **{"api.md": f"{quote}\n"})
    report = run(root)
    assert status(report) == "candidate_counterevidence"
    [line] = printed_lines(to_markdown(report))
    assert "‮" not in line
    path = root / "pasted.jsonl"
    write_text_exact(path, line + "\n")
    assert status(audit(root / "MEMORY.md", root / "evidence", path)) == "insufficient_evidence"


def test_a_sidecar_passage_names_its_cue(tmp_path):
    span = make_span("clip.vtt", "0" * 64, "hello there", 0, 11)
    assert "locator" not in json.loads(dismissal_line(LIMIT, span))


def test_a_transcript_cue_can_be_dismissed_from_the_printed_line(tmp_path):
    vtt = "WEBVTT\n\n00:00:01.000 --> 00:00:04.000\nThe public API allows 60 requests per minute.\n"
    root = project(tmp_path, **{"call.vtt": vtt})
    report = run(root)
    assert status(report) == "candidate_counterevidence"
    [line] = printed_lines(to_markdown(report))
    assert json.loads(line)["locator"] == "t=1,4"
    path = root / "pasted.jsonl"
    write_text_exact(path, line + "\n")
    assert status(audit(root / "MEMORY.md", root / "evidence", path)) == "insufficient_evidence"


def test_the_html_page_holds_the_line_and_the_count(tmp_path):
    root = project(tmp_path)
    page = to_html(run(root))
    assert "Does not bear on this claim?" in page and "&quot;relation&quot;: &quot;unrelated&quot;" in page
    dismissed = to_html(run(root, dismissal(reviewer="<ada>")))
    assert "Dismissed by a person" in dismissed and "&lt;ada&gt;" in dismissed and "<ada>" not in dismissed


# --- how the report says it --------------------------------------------------------------------------------------

def test_the_report_says_how_many_and_by_whom(tmp_path):
    other = "The bulk export has a limit of 30 records per request."
    root = project(tmp_path, f"- {CLAIM}\n- {other}\n", **{
        "api.md": f"{QUOTE}\n", "export.md": "The bulk export has a limit of 10 records per request.\n"})
    markdown = to_markdown(run(root, dismissal(), dismissal(
        "The bulk export has a limit of 10 records per request.", "export.md", claim_id(other), "bo")))
    assert "- Dismissed by a person: 2 candidate(s) (ada: 1, bo: 1)\n" in markdown
    assert "**Dismissed** (read by a person; not counted as evidence)" in markdown
    assert "- dismissed by bo · `export.md:1`" in markdown
    assert "POSSIBLE COUNTEREVIDENCE" not in markdown


def test_no_dismissal_means_no_new_field_in_the_json(tmp_path):
    root = project(tmp_path)
    data = json.loads(run(root).model_dump_json())
    assert "dismissed" not in data and "dismissed" not in data["findings"][0]
    data = json.loads(run(root, dismissal()).model_dump_json())
    assert data["dismissed"] == {"ada": 1}
    assert data["findings"][0]["dismissed"][0]["relation"] == "unrelated"
    assert data["findings"][0]["dismissed"][0]["span"]["quote"] == QUOTE


def test_a_hostile_reviewer_name_stays_inert_in_every_report(tmp_path):
    root = project(tmp_path)
    report = run(root, dismissal(reviewer="evil\x1b[31m | **x**\n"))
    assert "\x1b" not in to_markdown(report) and "\x1b" not in to_html(report)
    (tmp_path / "second").mkdir()
    stale = project(tmp_path / "second")
    line = reviews(stale, dismissal(reviewer="evil\x1b[31m\n"))
    write_text_exact(stale / "evidence" / "api.md", "Something else entirely here.\n")
    assert "\x1b" not in to_markdown(audit(stale / "MEMORY.md", stale / "evidence", line))


# --- the demo, the set of files, diff ----------------------------------------------------------------------------

def test_end_to_end_on_the_init_demo(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    created = runner.invoke(app, ["init", "demo"])
    assert created.exit_code == 0 and '"relation": "unrelated"' in created.stdout
    monkeypatch.chdir(tmp_path / "demo")
    before = runner.invoke(app, ["audit"])
    assert "POSSIBLE COUNTEREVIDENCE" in before.stdout
    [line] = printed_lines(before.stdout)
    write_text_exact(Path("reviews.jsonl"), line + "\n")
    after = runner.invoke(app, ["audit", "--reviews", "reviews.jsonl", "--strict"])
    assert after.exit_code == 0, after.output
    assert "POSSIBLE COUNTEREVIDENCE" not in after.stdout
    assert "- Dismissed by a person: 1 candidate(s) (you: 1)\n" in after.stdout


def test_a_set_of_files_adds_the_counts_up(tmp_path, monkeypatch):
    other = "The bulk export has a limit of 30 records per request."
    write_text_exact(tmp_path / "CLAUDE.md", f"- {CLAIM}\n")
    write_text_exact(tmp_path / "AGENTS.md", f"- {other}\n")
    (tmp_path / "docs").mkdir()
    write_text_exact(tmp_path / "docs" / "api.md", f"{QUOTE}\n")
    write_text_exact(tmp_path / "docs" / "export.md", "The bulk export has a limit of 10 records per request.\n")
    lines = reviews(tmp_path, dismissal(path="docs/api.md"),
                    dismissal("The bulk export has a limit of 10 records per request.", "docs/export.md", claim_id(other)))
    monkeypatch.chdir(tmp_path)
    shown = runner.invoke(app, ["audit", "--reviews", lines.name, "--format", "json"])
    assert shown.exit_code == 0, shown.output
    data = json.loads(shown.stdout)
    assert data["dismissed"] == {"ada": 2} and "candidate_counterevidence" not in data["summary"]
    markdown = runner.invoke(app, ["audit", "--reviews", lines.name]).stdout
    assert "- Dismissed by a person: 2 candidate(s) (ada: 2)\n" in markdown


def test_the_example_reviews_still_read_as_review_records(tmp_path):
    from memfalsify.ownfiles import REVIEWS, classify
    from memlab_ui.internal import REVIEWS as UI_REVIEWS, classify as ui_classify
    line = (json.dumps(dismissal()) + "\n").encode("utf-8")
    assert classify("x.jsonl", line, True, False) == REVIEWS
    assert ui_classify("x.jsonl", line, True, False) == UI_REVIEWS


def test_docs_and_skills_name_the_relation():
    root = EXAMPLES.parent
    for name in ("docs/quickstart.md", "docs/quickstart.zh-CN.md", "docs/tutorial.md", "docs/tutorial.zh-CN.md",
                 "docs/api.md", "docs/api.zh-CN.md", "skills/memfalsify/SKILL.md"):
        assert "unrelated" in (root / name).read_text(encoding="utf-8"), name


def test_the_init_demo_template_still_has_one_flag(tmp_path):
    demo = shutil.copytree(EXAMPLES / "memfalsify", tmp_path / "copy")
    report = audit(demo / "MEMORY.md", demo / "evidence", demo / "reviews.jsonl")
    assert sum(1 for f in report.findings if f.status == "candidate_counterevidence") == 3
    assert report.dismissed == {}


def test_a_set_report_with_one_file_shows_the_count(tmp_path, monkeypatch):
    write_text_exact(tmp_path / "CLAUDE.md", f"- {CLAIM}\n")
    write_text_exact(tmp_path / "api.md", f"{QUOTE}\n")
    lines = reviews(tmp_path, dismissal())
    monkeypatch.chdir(tmp_path)
    from memfalsify.auditdir import audit_directory
    result = audit_directory(Path("."), reviews=Path(lines.name))
    assert result.dismissed == {"ada": 1}
    assert "- Dismissed by a person: 1 candidate(s) (ada: 1)\n" in set_to_markdown(result)
