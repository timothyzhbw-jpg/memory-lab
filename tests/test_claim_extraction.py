"""What counts as one claim: sentences across wrapped lines, abbreviations, and fragments that say nothing.

The texts are made up, in the shapes that came out of real agent files. The rollback and diff tests at the end check
that claims read across lines survive files whose line structure changed between commits.
"""
import json
import re
import shutil
import subprocess

import pytest

from conftest import write_text_exact
from memfalsify import audit
from memfalsify.blocks import unwrapped
from memfalsify.corpus import as_read
from memfalsify.history import compare, memory_diff, memory_history
from memfalsify.memory import blocks, claim_id, is_fragment, parse_text, sentences
from memfalsify.report import to_markdown
from memfalsify.rollback import rollback_patch
from memfalsify.text import sentence_spans


def texts(markdown: str) -> list[str]:
    return [claim.text for claim in parse_text(markdown, "MEMORY.md")[0]]


def lines_of(markdown: str) -> list[tuple[str, int]]:
    return [(claim.text, claim.line) for claim in parse_text(markdown, "MEMORY.md")[0]]


# --- abbreviations ---------------------------------------------------------------------------------------------

@pytest.mark.parametrize("sentence", [
    "Backend tests: Some packages (e.g. the api package) compile slowly.",
    "Use a fixture, i.e. a small file under the test directory.",
    "E.g. run the fast suite first when you only touched docs.",
    "Patch vs. minor bump follows what the last release did.",
    "See the notes (cf. the release checklist) before tagging a version.",
    "Lint, format, etc. run in the same job as the tests.",
    "Python 3. 13 is what the image ships with today.",
    "The change landed in no. 5 of the release notes.",
    "Prefer integration tests, e.g., at tests/it/... over unit tests here.",
])
def test_an_abbreviation_does_not_end_a_sentence(sentence):
    assert texts(f"- {sentence}\n") == [sentence]


@pytest.mark.parametrize("line, expected", [
    ("Use ruff, black, etc. Then run the tests.", ["Use ruff, black, etc.", "Then run the tests."]),
    ("The retry limit is 5. Set it per project.", ["The retry limit is 5.", "Set it per project."]),
    ("The short answer is no. Ask a maintainer first.", ["The short answer is no.", "Ask a maintainer first."]),
    ("Requires Node >= 22.13. Use pnpm for installs.", ["Requires Node >= 22.13.", "Use pnpm for installs."]),
])
def test_a_real_sentence_end_still_cuts(line, expected):
    assert texts(line + "\n") == expected


def test_evidence_passages_read_abbreviations_the_same_way():
    text = "Some packages (e.g. the api package) compile slowly. Use targeted runs."
    assert [text[start:end] for start, end in sentence_spans(text)] == [
        "Some packages (e.g. the api package) compile slowly.", "Use targeted runs."]


# --- wrapped lines ---------------------------------------------------------------------------------------------

def test_a_wrapped_paragraph_is_read_as_one_with_its_first_line_as_location():
    markdown = ("# Rules\n\n"
                "Before opening a change, read the contributing guide, because bots close\n"
                "pull requests that skip it. A second sentence starts here and goes on to\n"
                "the next line.\n")
    assert lines_of(markdown) == [
        ("Before opening a change, read the contributing guide, because bots close pull requests that skip it.", 3),
        ("A second sentence starts here and goes on to the next line.", 4)]


def test_a_wrapped_list_item_is_read_as_one():
    markdown = ("- Prefer explicit fixture setup and command arguments over local helpers that hide\n"
                "  the inputs just to reduce repetition\n"
                "- Keep fixtures small\n"
                "    and focused\n"
                "1. Number one goes on\n"
                "   on its next line.\n")
    assert lines_of(markdown) == [
        ("Prefer explicit fixture setup and command arguments over local helpers that hide the inputs just to reduce "
         "repetition", 1),
        ("Keep fixtures small and focused", 3),
        ("Number one goes on on its next line.", 5)]


def test_a_lazy_continuation_of_a_list_item_is_read_as_one():
    markdown = ("- If you add a build script to this package, you also have to list it in the\n"
                "workspace file at the top of the repository.\n")
    assert texts(markdown) == ["If you add a build script to this package, you also have to list it in the workspace "
                               "file at the top of the repository."]


def test_quoted_lines_join_and_their_markers_go():
    markdown = ("> Deploys go through the staging cluster before they reach any of the\n"
                "> production regions.\n")
    assert lines_of(markdown) == [("Deploys go through the staging cluster before they reach any of the production "
                                   "regions.", 1)]


def test_short_lines_with_no_marker_stay_on_their_own():
    markdown = "Project: acme\nLanguage: Python 3.12\nTests: pytest\n"
    assert lines_of(markdown) == [("Project: acme", 1), ("Language: Python 3.12", 2), ("Tests: pytest", 3)]


def test_long_lines_that_each_start_with_a_key_stay_on_their_own():
    markdown = ("Project: a description of the project that runs on for more than sixty columns here\n"
                "**Language**: Python\n")
    assert texts(markdown) == ["Project: a description of the project that runs on for more than sixty columns here",
                               "Language: Python"]


def test_a_hard_line_break_is_kept():
    long = "This line is long enough that it would otherwise read as one that was wrapped"
    assert texts(f"{long}  \nThe next one stands alone\n") == [long, "The next one stands alone"]
    assert texts(f"{long}<br>\nThe next one stands alone\n") == [f"{long}<br>", "The next one stands alone"]


def test_a_line_ending_in_a_url_or_colon_does_not_run_on():
    url = "Source: (go to or request this page to learn more) https://docs.example.test/tools/basics"
    assert texts(f"{url}\nTools are what the agent has to work with.\n") == [
        url, "Tools are what the agent has to work with."]
    assert texts("The library runs in two modes:\nThe first one is local only.\n") == [
        "The library runs in two modes:", "The first one is local only."]


def test_what_starts_a_new_block_ends_a_paragraph():
    markdown = ("A paragraph that is plenty long enough to read as a wrapped one, going on\n"
                "- a bullet interrupts it\n"
                "2. an ordered item goes on the bullet list\n\n"
                "Another paragraph that is plenty long enough to read as a wrapped one, and\n"
                "2. this is not a list, since only a 1 can interrupt a paragraph\n\n"
                "Yet another paragraph that is plenty long enough to read as a wrapped one\n"
                "| a table | row here |\n")
    assert texts(markdown) == [
        "A paragraph that is plenty long enough to read as a wrapped one, going on",
        "a bullet interrupts it", "an ordered item goes on the bullet list",
        "Another paragraph that is plenty long enough to read as a wrapped one, and 2.",
        "this is not a list, since only a 1 can interrupt a paragraph",
        "Yet another paragraph that is plenty long enough to read as a wrapped one", "| a table | row here |"]


def test_chinese_lines_join_with_no_space():
    markdown = "部署之前必须先经过预发环境的全部回归测试，这样才能发现数据迁移的问题，\n然后才可以进入生产环境。\n"
    assert lines_of(markdown) == [("部署之前必须先经过预发环境的全部回归测试，这样才能发现数据迁移的问题，然后才可以进入生产环境。", 1)]


def test_link_text_can_wrap():
    markdown = ("Telemetry has no measurable impact on the performance of this. Code is available in [the telemetry\n"
                "service](https://example.test/telemetry).\n")
    assert texts(markdown)[-1] == "Code is available in the telemetry service."


def test_crlf_files_read_the_same():
    markdown = "A wrapped sentence that is long enough to count as wrapped when it stops at\nthe end of its line.\n"
    assert lines_of(markdown.replace("\n", "\r\n")) == lines_of(markdown)


def test_raw_spans_cover_the_lines_with_their_markup():
    raw = "- The cache uses **Redis** through a client that was written long ago for\n  [the old queue](https://x.test).\n"
    [(text, start, end)] = sentences(raw.rstrip("\n"))
    assert text == "The cache uses Redis through a client that was written long ago for the old queue."
    assert raw[start:end] == ("The cache uses **Redis** through a client that was written long ago for\n"
                              "  [the old queue](https://x.test).")


# --- wrapped lines in evidence -----------------------------------------------------------------------------------

STATED = ("Every pull request must link to an issue with the closing keyword, and that issue must carry the accepted\n"
          "label before anyone reviews the change.")


def evidence_project(tmp_path, evidence: str, name: str = "guide.md"):
    (tmp_path / "evidence").mkdir()
    write_text_exact(tmp_path / "evidence" / name, evidence)
    return tmp_path / "evidence"


def test_a_wrapped_claim_finds_the_same_sentence_wrapped_elsewhere_in_evidence(tmp_path):
    evidence = evidence_project(tmp_path, "# Guide\n\n" + STATED.replace("accepted\n", "accepted\n  ") + "\n")
    write_text_exact(tmp_path / "MEMORY.md", f"- {STATED.replace('the closing', 'the\n  closing')}\n")
    [finding] = audit(tmp_path / "MEMORY.md", evidence).findings
    assert finding.status == "candidate_support"
    [item] = finding.evidence
    assert item.span.line == 3 and "accepted\n  label" in item.span.quote
    assert (evidence / "guide.md").read_text(encoding="utf-8")[item.span.start:item.span.end] == item.span.quote


def test_a_wrapped_passage_can_contradict_a_claim(tmp_path):
    evidence = evidence_project(tmp_path, "The public API allows 60 requests per minute for each key that we hand\n"
                                          "out to customers on the paid plans.\n")
    write_text_exact(tmp_path / "MEMORY.md", "The public API allows 100 requests per minute for each key that we hand out "
                                             "to customers on the paid plans.\n")
    [finding] = audit(tmp_path / "MEMORY.md", evidence).findings
    assert finding.status == "candidate_counterevidence" and finding.evidence[0].span.line == 1


@pytest.mark.parametrize("name", ["guide.md", "guide.MDX", "notes.txt", "guide.rst"])
def test_prose_files_are_read_with_their_wrapped_lines(name):
    assert as_read(name, STATED) == STATED.replace("\n", " ")


@pytest.mark.parametrize("name", ["views.py", "app.ts", "config.json", "schema.yaml", "Makefile"])
def test_code_and_data_stay_line_by_line(name):
    assert as_read(name, STATED) == STATED


@pytest.mark.parametrize("text", [
    STATED, STATED.replace("\n", "\r\n") + "\r\n", "# Title\n\n- " + STATED + "\n- Next item here\n",
    "```\ncode one that is long enough to count as a wrapped line if it were prose, and\ncode two\n```\n",
    "| a | b |\n|---|---|\n| c | d |\n", "", "\n\n", "no newline",
])
def test_reading_keeps_every_offset(text):
    read = unwrapped(text)
    assert len(read) == len(text)
    assert [line for line in read.split() if line] == [line for line in text.split() if line]


def test_code_fences_and_table_rows_in_prose_are_not_joined():
    fence = "```\ncode one that is long enough to count as a wrapped line if it were prose, and\ncode two\n```\n"
    assert unwrapped(fence) == fence
    table = "| a row that is long enough to count as a wrapped line if it were prose, and it ends |\n| next |\n"
    assert unwrapped(table) == table


def test_a_crlf_file_keeps_its_offsets_and_the_quote_keeps_its_bytes(tmp_path):
    raw = STATED.replace("\n", "\r\n") + "\r\n"
    evidence = evidence_project(tmp_path, raw)
    write_text_exact(tmp_path / "MEMORY.md", " ".join(STATED.split()) + "\n")
    [finding] = audit(tmp_path / "MEMORY.md", evidence).findings
    assert finding.status == "candidate_support" and finding.evidence[0].span.quote == STATED.replace("\n", "\r\n")


def test_a_wrapped_quote_shows_a_line_per_line_and_the_printed_line_is_exact(tmp_path):
    raw = "The public API allows 60 requests per minute for each key that we hand\n    out to customers.\n"
    evidence = evidence_project(tmp_path, raw)
    write_text_exact(tmp_path / "MEMORY.md", "The public API allows 100 requests per minute for each key that we hand out "
                                             "to customers.\n")
    report = audit(tmp_path / "MEMORY.md", evidence)
    markdown = to_markdown(report)
    assert ("  > The public API allows 60 requests per minute for each key that we hand\n"
            "  > out to customers.\n") in markdown
    line = next(json.loads(item) for item in re.findall(r"(\{\"memory_id.*\})`$", markdown, re.MULTILINE))
    assert line["quote"] == raw.rstrip("\n")
    write_text_exact(tmp_path / "reviews.jsonl", json.dumps(line) + "\n")
    assert audit(tmp_path / "MEMORY.md", evidence, tmp_path / "reviews.jsonl").findings[0].status == "insufficient_evidence"


# --- fragments -------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("label", [
    "Store and schema:", "Run ty:", "When writing comments:", "Key flags:", "**Code quality:**", "For exported classes:",
    "Before making any changes to public APIs:", "For a full build from the repository root:", "Or in your Python code:",
    "Example partners with profiles in this repo:",
])
def test_a_label_is_no_claim(label):
    assert texts(f"{label}\n\n- Tests use pytest.\n") == ["Tests use pytest."]


@pytest.mark.parametrize("line", [
    "Tests use pytest.", "Disclose AI use.", "Skip pre-commit hooks.", "Use async python", "Requires Node >= 22.13.",
    "pnpm 11 + Turborepo monorepo.", "Flaky tests", "pnpm workspaces", "Uses Python 3.12", "variants require explicit handling",
    "The library can run as an MCP server for integration with a desktop client:",
    "Browser is an alias for BrowserSession - they are the same class:",
    "For multi-step tasks, state a brief plan:", "Before running your tests, set up all packages by running:",
    "Pin Python 3.12:", "Never comment on these topics:", "Configuration lives per package:",
])
def test_a_short_real_claim_stays(line):
    assert texts(f"- {line}\n") == [line]


@pytest.mark.parametrize("text", [
    "部署流程：", "常用命令：", "公开 API 的限流是每分钟 100 次请求。", "主数据库：PostgreSQL 16", "项目结构", "测试使用 pytest",
])
def test_chinese_text_is_never_a_fragment(text):
    assert not is_fragment(text) and not is_fragment(text, 3)
    assert texts(f"- {text}\n") == [text]


@pytest.mark.parametrize("line", [
    "</browser_docs>", "<browser_docs>", '<Note icon="key" color="#FFC107">', "<details>", "<br/>",
    '<a href="https://example.test/tools">Example</a>', "<https://example.test/tools>",
    "[the guide]: https://example.test/guide",
])
def test_markup_with_nothing_else_is_no_claim(line):
    assert texts(f"{line}\n") == []


def test_a_tag_around_a_sentence_keeps_the_sentence():
    assert texts("<Note> Custom browsers can be configured in one line.\n") == [
        "<Note> Custom browsers can be configured in one line."]
    assert texts('<Note icon="key">\nSet the key in your environment before you start.\n</Note>\n') == [
        "Set the key in your environment before you start."]


def test_table_headers_and_rules_are_no_claims_and_a_row_stays_whole():
    markdown = ("Tests use pytest.\n\n"
                "| Package | Purpose | Status |\n"
                "|---|:---:|---|\n"
                "| pkg/api | Serves the API. It is public. Do not break it. | ok |\n"
                "| pkg/ui | Renders the page, e.g. the shell | ok |\n\n"
                "No | Pipes | Around\n"
                "--|--|--\n"
                "row | without | pipes\n")
    assert lines_of(markdown) == [("Tests use pytest.", 1),
                                  ("| pkg/api | Serves the API. It is public. Do not break it. | ok |", 5),
                                  ("| pkg/ui | Renders the page, e.g. the shell | ok |", 6),
                                  ("row | without | pipes", 10)]


def test_a_chinese_table_header_goes_too():
    assert texts("| 名称 | 说明 |\n|---|---|\n| api | 公开接口 |\n") == ["| api | 公开接口 |"]


def test_a_short_piece_cut_from_a_sentence_goes_but_not_a_short_sentence():
    assert texts("Avoid sleeps in tests; time-based assertions; retries with no limit; flaky ones.\n") == [
        "Avoid sleeps in tests;", "flaky ones."]
    assert texts("Avoid sleeps in tests; retries are capped at the client.\n") == [
        "Avoid sleeps in tests;", "retries are capped at the client."]
    assert texts("Run the checks first. They use pytest. tests live here. flaky ones.\n") == [
        "Run the checks first.", "They use pytest.", "tests live here.", "flaky ones."]
    assert texts("Bump the version; 3 files change;\n") == ["Bump the version;", "3 files change;"]
    assert texts("The manifests are checked; tests/test_packaging.py checks them;\n") == [
        "The manifests are checked;", "tests/test_packaging.py checks them;"]
    assert texts("Bump the version; pnpm checks them; it stops the build.\n") == [
        "Bump the version;", "pnpm checks them;", "it stops the build."]


def test_a_list_marker_read_as_a_sentence_does_not_make_the_next_part_a_piece():
    assert texts("3. `runtime/worker.rs` - Worker setup\n") == ["runtime/worker.rs - Worker setup"]


def test_setext_headings_are_sections():
    markdown = "Style Guide\n===========\n\nTests use pytest.\n\nOther Things\n---\n\nDeploys go out on Friday.\n"
    assert [(c.text, c.section) for c in parse_text(markdown, "MEMORY.md")[0]] == [
        ("Tests use pytest.", "Style Guide"), ("Deploys go out on Friday.", "Other Things")]


def test_a_rule_after_a_list_is_a_rule():
    assert [c.section for c in parse_text("# A\n\n- Tests use pytest\n---\nDeploys go out on Friday.\n", "M.md")[0]] == ["A", "A"]


def test_comments_and_indented_code_hold_no_claims():
    markdown = ("Tests use pytest.\n\n<!-- this comment\nruns over several lines\nand ends here -->\n\n"
                "Deploys go out.\n\n    npm install --save-dev something\n\nBackups run nightly.\n\n"
                "- An item about deploys\n\n    its second paragraph is indented, and in the item.\n")
    assert texts(markdown) == ["Tests use pytest.", "Deploys go out.", "Backups run nightly.", "An item about deploys",
                               "its second paragraph is indented, and in the item."]


def test_an_unclosed_comment_hides_only_its_own_line():
    assert texts("<!-- never closed\nStill a claim here.\n") == ["Still a claim here."]


# --- links -----------------------------------------------------------------------------------------------------

def test_an_index_entry_points_at_claims_however_it_is_wrapped():
    claims, links = parse_text("- [Title](file.md) — a hook that is long enough to get wrapped by whoever wrote this\n"
                               "  index, with a second sentence. And a third one.\n", "MEMORY.md")
    assert claims == [] and links == ["file.md"]


def test_a_sentence_with_a_link_is_a_pointer_and_its_neighbors_are_not():
    markdown = ("Run the tests before you push. See [the guide](docs/guide.md) for how to write new\n"
                "ones when you add a feature. Deploys go out on Friday.\n")
    claims, links = parse_text(markdown, "MEMORY.md")
    assert [(c.text, c.line) for c in claims] == [("Run the tests before you push.", 1),
                                                  ("Deploys go out on Friday.", 2)]
    assert links == ["docs/guide.md"]
    assert parse_text("Tests use pytest. See @notes.md.\n", "M.md")[1] == ["notes.md"]


def test_a_link_outside_markdown_files_is_part_of_the_claim():
    assert texts("Read [the docs](https://example.test) before you start.\n") == ["Read the docs before you start."]


# --- blocks ------------------------------------------------------------------------------------------------------

def test_blocks_of_a_file_cover_the_lines_of_each_claim():
    lines = ["# Title", "", "- One that is long enough to count as wrapped, as it goes on over to", "  the next line.",
             "", "| a | b |", "|---|---|", "| c | d |"]
    found = blocks(lines)
    assert [(b.first, b.last, b.kind) for b in found] == [(0, 0, "heading"), (2, 3, "text"), (7, 7, "row")]


@pytest.mark.parametrize("hostile", [
    "|" * 5000, "- " * 5000, "> " * 5000, "<" * 5000, "<a " * 5000, "-" * 5000, "=\n" * 2000,
    "[" * 2000 + "\n" + "]" * 2000, "word " * 500 + "\n" + "word " * 500,
    # Each of these takes minutes if a pattern backtracks over the run.
    "a" + " " * 200_000 + "b\nc", "_" * 200_000 + ": x\n" + "_" * 200_000 + ": y", "x" * 200_000 + " ,\nnext",
    "<!-- open\n" * 50_000, "line that goes on\n" * 50_000,
])
def test_odd_input_only_costs_a_few_claims(hostile):
    parse_text(hostile, "MEMORY.md")


# --- diff and history ------------------------------------------------------------------------------------------

def git(root, *args):
    subprocess.run(["git", "-C", str(root), "-c", "user.name=t", "-c", "user.email=t@example.test",
                    "-c", "commit.gpgsign=false", "-c", "core.autocrlf=false", *args],
                   check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path):
    if shutil.which("git") is None:
        pytest.skip("git is not installed")
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    return root


def commit(root, text):
    write_text_exact(root / "MEMORY.md", text)
    git(root, "add", "MEMORY.md")
    git(root, "commit", "-q", "-m", "change")
    return subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], check=True, capture_output=True,
                          text=True).stdout.strip()


WRAPPED = ("# Rules\n\n"
           "- Deploys go through the staging cluster before they reach any of the\n"
           "  production regions, and nothing skips that step.\n"
           "- Tests use pytest.\n")
ONE_LINE = ("# Rules\n\n"
            "- Deploys go through the staging cluster before they reach any of the production regions, and nothing "
            "skips that step.\n"
            "- Tests use pytest.\n")
DEPLOYS = "Deploys go through the staging cluster before they reach any of the production regions, and nothing skips that step."


def test_rewrapping_a_paragraph_is_no_change(repo):
    base = commit(repo, WRAPPED)
    commit(repo, ONE_LINE)
    report = memory_diff(repo / "MEMORY.md", base, "HEAD")
    assert report.summary == {"added": 0, "changed": 0, "removed": 0, "unchanged": 2}
    history = memory_history(repo / "MEMORY.md")
    assert {claim.text for claim in history.claims} == {DEPLOYS, "Tests use pytest."}
    assert history.removed == []


def test_an_edit_across_lines_is_one_change(repo):
    base = commit(repo, WRAPPED)
    commit(repo, WRAPPED.replace("nothing skips that step", "nothing ever skips that step"))
    report = memory_diff(repo / "MEMORY.md", base, "HEAD")
    assert report.summary["changed"] == 1 and report.summary["added"] == 0 and report.summary["removed"] == 0
    change = next(c for c in report.changes if c.change == "changed")
    assert change.old.text == DEPLOYS and change.old.location == "MEMORY.md:3"


def apply(root, patch: bytes) -> str:
    write_text_exact(root.parent / "rollback.patch", patch.decode("utf-8"))
    git(root, "apply", str(root.parent / "rollback.patch"))
    return (root / "MEMORY.md").read_bytes().decode("utf-8")


def test_rollback_restores_a_wrapped_sentence_with_its_line_breaks(repo):
    base = commit(repo, WRAPPED)
    edited = WRAPPED.replace("nothing skips that step", "nothing ever skips that step")
    commit(repo, edited)
    now = claim_id(DEPLOYS.replace("nothing skips", "nothing ever skips"))
    assert apply(repo, rollback_patch(repo / "MEMORY.md", now, base).patch) == WRAPPED


def test_rollback_restores_a_claim_that_was_wrapped_then_and_is_on_one_line_now(repo):
    base = commit(repo, WRAPPED)
    commit(repo, ONE_LINE.replace("nothing skips that step", "nothing ever skips that step"))
    now = claim_id(DEPLOYS.replace("nothing skips", "nothing ever skips"))
    result = apply(repo, rollback_patch(repo / "MEMORY.md", now, base).patch)
    assert claim_id(DEPLOYS) in {c.id for c in parse_text(result, "MEMORY.md")[0]}
    assert result == WRAPPED  # the old line breaks came back with the old wording


def test_rollback_restores_a_claim_on_one_line_then_and_wrapped_now(repo):
    base = commit(repo, ONE_LINE)
    commit(repo, WRAPPED.replace("nothing skips that step", "nothing ever skips that step"))
    now = claim_id(DEPLOYS.replace("nothing skips", "nothing ever skips"))
    assert apply(repo, rollback_patch(repo / "MEMORY.md", now, base).patch) == ONE_LINE


def test_a_rewrapped_claim_has_nothing_to_roll_back(repo):
    base = commit(repo, WRAPPED)
    commit(repo, ONE_LINE)
    assert rollback_patch(repo / "MEMORY.md", claim_id(DEPLOYS), base).patch is None


def test_rollback_readds_a_removed_wrapped_claim_whole(repo):
    base = commit(repo, WRAPPED)
    commit(repo, "# Rules\n\n- Tests use pytest.\n")
    result = apply(repo, rollback_patch(repo / "MEMORY.md", claim_id(DEPLOYS), base).patch)
    assert claim_id(DEPLOYS) in {c.id for c in parse_text(result, "MEMORY.md")[0]}
    assert result == WRAPPED


def test_rollback_readds_after_the_whole_wrapped_block_of_its_neighbor(repo):
    first = ("Tests use pytest in every package of this repository, with the shared fixtures that are\n"
             "kept in the root of it.")
    base = commit(repo, f"# Rules\n\n{first}\n\nDeploys go out on Friday.\n")
    commit(repo, f"# Rules\n\n{first}\n")
    result = apply(repo, rollback_patch(repo / "MEMORY.md", claim_id("Deploys go out on Friday."), base).patch)
    assert result.startswith(f"# Rules\n\n{first}\n")
    assert [c.text for c in parse_text(result, "MEMORY.md")[0]] == [" ".join(first.split()), "Deploys go out on Friday."]


def test_rollback_readds_with_a_blank_line_where_the_next_line_would_run_on(repo):
    claim = "A statement with no period that is long enough to read as wrapped when it stops here"
    base = commit(repo, f"# Rules\n\n{claim}\n\nDeploys go out on Friday\n")
    commit(repo, "# Rules\n\nDeploys go out on Friday\n")
    result = apply(repo, rollback_patch(repo / "MEMORY.md", claim_id(claim), base).patch)
    assert [c.text for c in parse_text(result, "MEMORY.md")[0]] == [claim, "Deploys go out on Friday"]


def test_rollback_of_one_sentence_of_a_wrapped_paragraph(repo):
    paragraph = ("Tests use pytest in every package of this repository. Deploys go out on Friday\n"
                 "after the freeze is lifted by the release manager. Backups run at night.\n")
    base = commit(repo, f"# Rules\n\n{paragraph}")
    commit(repo, f"# Rules\n\n{paragraph.replace('on Friday', 'on Monday')}")
    now = claim_id("Deploys go out on Monday after the freeze is lifted by the release manager.")
    assert apply(repo, rollback_patch(repo / "MEMORY.md", now, base).patch) == f"# Rules\n\n{paragraph}"


def test_rollback_keeps_crlf_across_the_lines_it_restores(repo):
    git(repo, "config", "core.autocrlf", "false")
    base = commit(repo, WRAPPED.replace("\n", "\r\n"))
    commit(repo, WRAPPED.replace("nothing skips that step", "nothing ever skips that step").replace("\n", "\r\n"))
    now = claim_id(DEPLOYS.replace("nothing skips", "nothing ever skips"))
    patch = rollback_patch(repo / "MEMORY.md", now, base).patch
    assert patch.count(b"\r\n") >= 2
    (repo.parent / "rollback.patch").write_bytes(patch)
    git(repo, "apply", str(repo.parent / "rollback.patch"))
    assert (repo / "MEMORY.md").read_bytes() == WRAPPED.replace("\n", "\r\n").encode()


def test_rollback_restores_every_copy_when_lines_change_in_number(repo):
    old = "A sentence wrapped at the edge of the editor, which goes on over to the next\n  line, stays the same."
    base = commit(repo, f"# A\n\n- {old}\n\n# B\n\n- {old}\n")
    new = "A sentence wrapped at the edge of the editor, which goes on over to the next line, stays quite the same."
    commit(repo, f"# A\n\n- {new}\n\n# B\n\n- {new}\n")
    result = apply(repo, rollback_patch(repo / "MEMORY.md", claim_id(new), base).patch)
    assert result == f"# A\n\n- {old}\n\n# B\n\n- {old}\n"


def test_rollback_in_a_quote_moved_into_a_list_still_restores_the_claim(repo):
    old = "> A sentence in a quote that is long enough to be wrapped by the editor, and it\n> goes on here."
    base = commit(repo, f"# Rules\n\n{old}\n")
    new = "A sentence in a quote that is long enough to be wrapped by the editor, and it goes on over here."
    commit(repo, f"# Rules\n\n- {new}\n")
    result = apply(repo, rollback_patch(repo / "MEMORY.md", claim_id(new), base).patch)
    assert claim_id(" ".join(old.replace("> ", "").split())) in {c.id for c in parse_text(result, "MEMORY.md")[0]}


def test_compare_pairs_a_claim_with_its_old_fragment_only_by_id():
    """A file parsed by this version has one claim where a line-by-line read had two; an ID is its text, nothing else."""
    head = parse_text("A wrapped sentence long enough to be read as one, which goes on to the\nnext line.\n", "M.md")[0]
    assert [c.kind for c in compare(head, head)] == ["unchanged"]


# --- audits and old reviews ------------------------------------------------------------------------------------

def test_a_review_of_a_fragment_says_it_no_longer_applies(tmp_path):
    from typer.testing import CliRunner

    from memfalsify.cli import app
    runner = CliRunner()
    write_text_exact(tmp_path / "MEMORY.md", "Some packages (e.g. the api package) compile slowly.\n")
    (tmp_path / "evidence").mkdir()
    write_text_exact(tmp_path / "evidence" / "notes.md", "Some packages (e.g. the api package) compile quickly.\n")
    old_id = claim_id("Some packages (e.g.")  # how an earlier version read that line
    write_text_exact(tmp_path / "reviews.jsonl", json.dumps({
        "memory_id": old_id, "relation": "contradicts", "path": "notes.md", "quote": "compile quickly",
        "reviewer": "ada"}) + "\n")
    result = runner.invoke(app, ["audit", str(tmp_path / "MEMORY.md"), "--evidence", str(tmp_path / "evidence"),
                                 "--reviews", str(tmp_path / "reviews.jsonl")])
    assert result.exit_code == 2
    assert f"unknown memory_id {old_id}" in result.output and "memfalsify claims" in result.output


def test_every_stale_line_is_named_at_once(tmp_path):
    from memcore import EvidenceError
    (tmp_path / "evidence").mkdir()
    write_text_exact(tmp_path / "MEMORY.md", "Deploys go out on Friday.\n")
    write_text_exact(tmp_path / "evidence" / "notes.md", "Deploys go out on Monday.\n")
    lines = [{"memory_id": f"m-0000000{n}", "relation": "supports", "path": "notes.md", "quote": "Deploys",
              "reviewer": "ada"} for n in (1, 2)]
    write_text_exact(tmp_path / "reviews.jsonl", "".join(json.dumps(line) + "\n" for line in lines))
    with pytest.raises(EvidenceError) as caught:
        audit(tmp_path / "MEMORY.md", tmp_path / "evidence", tmp_path / "reviews.jsonl")
    assert "reviews.jsonl:1: unknown memory_id m-00000001" in str(caught.value)
    assert "reviews.jsonl:2: unknown memory_id m-00000002" in str(caught.value)


def test_a_wrapped_claim_is_compared_as_a_whole(tmp_path):
    (tmp_path / "evidence").mkdir()
    write_text_exact(tmp_path / "MEMORY.md", "The public API allows 100 requests per minute for each key that we hand\n"
                                             "out to customers.\n")
    write_text_exact(tmp_path / "evidence" / "api.md",
                     "The public API allows 60 requests per minute for each key that we hand out to customers.\n")
    report = audit(tmp_path / "MEMORY.md", tmp_path / "evidence")
    [finding] = report.findings
    assert finding.status == "candidate_counterevidence" and finding.location == "MEMORY.md:1"
