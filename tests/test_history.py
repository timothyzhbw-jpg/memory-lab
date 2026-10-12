"""memfalsify diff / history / rollback against temporary git repositories (offline, any OS with git)."""
import json
import os
import shutil
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from conftest import GitRepo as Repo, snapshot
from memfalsify import gitstore
from memfalsify.audit import audit
from memfalsify.cli import app
from memfalsify.history import compare, diff_to_markdown, history_to_markdown, memory_diff, memory_history
from memfalsify.memory import parse_text, sentences
from memfalsify.report import to_markdown
from memfalsify.rollback import rollback_patch

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")
runner = CliRunner()
AS_OF = "2026-10-10"


@pytest.fixture
def repo(git_repo):
    return git_repo


BASE_MEMORY = """# Memory

## Infra

- The cache uses **Redis**.
- The main database runs PostgreSQL 15.
- [Infra notes](infra.md)

## Process

- We use squash merges for pull requests.
- Feature B ships in Q3.
"""
HEAD_MEMORY = """# Memory

## Infrastructure

- The cache uses **Redis** for sessions.
- The main database runs PostgreSQL 15.
- [Infra notes](infra.md)

## Process

- We use rebase merges for pull requests.
- The public API rate limit is 100 requests per minute.
"""
EVIDENCE = {"evidence/api.md": "The public API rate limit is 60 requests per minute.\n",
            "evidence/db.md": "The main database runs PostgreSQL 16.\n"}


def demo(repo: Repo) -> str:
    """Commit the base memory; leave the head version in the working tree. Returns the base commit."""
    base = repo.commit("first memory", {"MEMORY.md": BASE_MEMORY, "infra.md": "- The queue runs on Kafka.\n",
                                        **EVIDENCE})
    repo.write({"MEMORY.md": HEAD_MEMORY, "infra.md": "- The queue runs on RabbitMQ.\n"})
    return base


def claims_of(text: str, path: str = "MEMORY.md"):
    return parse_text(text, path)[0]


def by_change(report):
    out = {}
    for c in report.changes:
        out.setdefault(c.change, []).append(c)
    return out


# --- classification (no git) ------------------------------------------------------------------------

def test_compare_classifies_every_kind():
    old = claims_of("- The cache uses Redis.\n- We use squash merges for pull requests.\n- Feature B ships in Q3.\n")
    new = claims_of("- The cache uses Redis.\n- We use rebase merges for pull requests.\n"
                    "- The public API rate limit is 100 requests per minute.\n")
    kinds = {(c.kind, c.old.text if c.old else None, c.new.text if c.new else None) for c in compare(old, new)}
    assert kinds == {
        ("unchanged", "The cache uses Redis.", "The cache uses Redis."),
        ("changed", "We use squash merges for pull requests.", "We use rebase merges for pull requests."),
        ("added", None, "The public API rate limit is 100 requests per minute."),
        ("removed", "Feature B ships in Q3.", None),
    }


def test_compare_case_and_whitespace_edits_are_unchanged_but_moved():
    old = claims_of("- The cache uses Redis.\n")
    new = claims_of("## Infra\n\n- the  cache uses REDIS.\n")
    [change] = compare(old, new)
    assert change.kind == "unchanged" and change.moved


def test_compare_pairs_edits_across_a_renamed_section():
    old = claims_of("## Infra\n- The database runs PostgreSQL 15.\n- The cache uses Redis.\n")
    new = claims_of("## Infrastructure\n- The database runs PostgreSQL 15.\n- The cache uses Redis for sessions.\n")
    changes = {c.kind: c for c in compare(old, new)}
    assert set(changes) == {"unchanged", "changed"}
    assert not changes["unchanged"].moved  # a renamed heading is not a move
    assert changes["changed"].old.section == "Infra" and changes["changed"].new.section == "Infrastructure"


def test_compare_does_not_pair_across_files_or_unrelated_sections():
    old = claims_of("## Infra\n- The cache uses Redis.\n## Process\n- Reviews need two approvals.\n")
    new = (claims_of("## Infra\n- Deploys need a ticket.\n## Process\n- The cache uses Redis for sessions.\n")
           + claims_of("- Reviews need two approvals from owners.\n", "infra.md"))
    assert {c.kind for c in compare(old, new)} == {"added", "removed"}


def test_compare_low_similarity_is_removed_plus_added():
    old = claims_of("- The cache uses Redis.\n")
    new = claims_of("- The queue uses Kafka.\n")
    assert sorted(c.kind for c in compare(old, new)) == ["added", "removed"]


def test_compare_ties_are_deterministic():
    old = claims_of("- Deploys run at 5 pm on Fridays.\n- Deploys run at 6 pm on Fridays.\n")
    new = claims_of("- Deploys run at 7 pm on Fridays.\n")
    for _ in range(3):
        changes = compare(old, new)
        changed = [c for c in changes if c.kind == "changed"]
        assert [c.old.text for c in changed] == ["Deploys run at 5 pm on Fridays."]
        assert [c.old.text for c in changes if c.kind == "removed"] == ["Deploys run at 6 pm on Fridays."]


def test_link_paths_stay_inside_the_memory_directory_on_every_platform():
    assert gitstore.link_path("./infra.md") == "infra.md"
    assert gitstore.link_path("notes/../infra.md") == "infra.md"
    assert gitstore.link_path("sub//deep.md") == "sub/deep.md"
    for target in ("../outside.md", "notes/../../outside.md", "/etc/x.md", "C:/x.md", "c:x.md", "..\\x.md",
                   "\\\\server\\share\\x.md", "", ".", "a\x01.md"):
        assert gitstore.link_path(target) is None, target


def test_sentences_map_back_to_raw_markup():
    raw = "- The cache uses **Redis** via [the client](https://x.test). Deploys use `make ship`."
    found = sentences(raw)
    assert [text for text, _, _ in found] == ["The cache uses Redis via the client.", "Deploys use make ship."]
    assert [raw[s:e] for _, s, e in found] == ["The cache uses **Redis** via [the client](https://x.test).",
                                               "Deploys use `make ship`."]


def test_sentences_keep_wrapping_markup_unless_it_is_shared():
    """A link or emphasis that wraps one sentence is part of its raw span. One that also touches another
    sentence stays outside both, so rollback cannot pull the neighbor's URL or marker in."""
    wrapped = "- **The cache uses Redis.** [The queue runs on Kafka.](https://q.example/old)"
    found = sentences(wrapped)
    assert [text for text, _, _ in found] == ["The cache uses Redis.", "The queue runs on Kafka."]
    assert [wrapped[s:e] for _, s, e in found] == ["**The cache uses Redis.**",
                                                   "[The queue runs on Kafka.](https://q.example/old)"]
    wiki = "- [[The cache uses Redis.]]"
    _, start, end = sentences(wiki)[0]
    assert wiki[start:end] == "[[The cache uses Redis.]]"
    adjacent = "[Alpha ships in Q3.](https://a.example) [Beta ships in Q4.](https://b.example)"
    assert [adjacent[s:e] for _, s, e in sentences(adjacent)] == [
        "[Alpha ships in Q3.](https://a.example)", "[Beta ships in Q4.](https://b.example)"]
    shared = "[Alpha ships in Q3. Beta ships in Q4.](https://shared.example)"
    assert [shared[s:e] for _, s, e in sentences(shared)] == ["Alpha ships in Q3.", "Beta ships in Q4."]
    # The period is a boundary only when whitespace survives the marker strip, so the stars are two pairs.
    stars = "First claim stays put.** **Second claim stays put."
    found = sentences(stars)
    assert [text for text, _, _ in found] == ["First claim stays put.", "Second claim stays put."]
    assert [stars[s:e] for _, s, e in found] == ["First claim stays put.**", "**Second claim stays put."]
    assert found[0][2] <= found[1][1]


# --- diff ------------------------------------------------------------------------------------------

def test_diff_classifies_and_audits_only_touched_claims(repo):
    base = demo(repo)
    report = memory_diff(repo.root / "MEMORY.md", base, evidence_root=repo.root / "evidence")
    assert report.summary == {"added": 1, "changed": 3, "removed": 1, "unchanged": 1}
    kinds = by_change(report)
    assert {(c.old.text, c.new.text) for c in kinds["changed"]} == {
        ("The cache uses Redis.", "The cache uses Redis for sessions."),
        ("We use squash merges for pull requests.", "We use rebase merges for pull requests."),
        ("The queue runs on Kafka.", "The queue runs on RabbitMQ."),
    }
    assert [c.new.location for c in kinds["changed"] if c.new.location.startswith("infra.md")] == ["infra.md:1"]
    assert [c.old.text for c in kinds["removed"]] == ["Feature B ships in Q3."]
    # The unchanged PostgreSQL claim contradicts evidence/db.md, but only touched claims are audited.
    audited = {f.text: f.status for f in report.audit.findings}
    assert audited == {"The public API rate limit is 100 requests per minute.": "candidate_counterevidence",
                       "The cache uses Redis for sessions.": "insufficient_evidence",
                       "We use rebase merges for pull requests.": "insufficient_evidence",
                       "The queue runs on RabbitMQ.": "insufficient_evidence"}
    assert [c.status for c in kinds["added"]] == ["candidate_counterevidence"]
    assert report.base.files == report.head.files == ["MEMORY.md", "infra.md"]
    assert report.head.rev == "working tree" and report.head.commit is None and report.attention == 1


def test_diff_cli_strict_exit_codes_and_formats(repo):
    base = demo(repo)
    memory = str(repo.root / "MEMORY.md")
    evidence = ["--evidence", str(repo.root / "evidence"), "--as-of", AS_OF]
    result = runner.invoke(app, ["diff", memory, "--base", base, *evidence])
    assert result.exit_code == 0, result.output
    text = result.stdout
    assert text.startswith("# MemFalsify diff report") and "| Changed | 3 |" in text
    assert "**Old** at `MEMORY.md:5` · `Infra`" in text and "**New** at `MEMORY.md:5` · `Infrastructure`" in text
    assert "The main database runs PostgreSQL 15." not in text  # unchanged: neither listed nor audited
    assert runner.invoke(app, ["diff", memory, "--base", base, "--strict", *evidence]).exit_code == 1
    result = runner.invoke(app, ["diff", memory, "--base", base, "--format", "json", *evidence])
    data = json.loads(result.stdout)
    assert data["tool"] == "memfalsify" and data["summary"]["added"] == 1
    assert data["audit"]["summary"] == {"candidate_counterevidence": 1, "insufficient_evidence": 3}
    # Committing the change leaves nothing touched: strict passes although an unchanged claim is contradicted.
    repo.commit("second")
    result = runner.invoke(app, ["diff", memory, "--base", "HEAD", "--strict", *evidence])
    assert result.exit_code == 0 and "nothing was audited" in result.stdout
    full = runner.invoke(app, ["audit", memory, "--strict", *evidence])
    assert full.exit_code == 1  # the whole-memory audit still flags it


def test_diff_cli_model_options_and_usage(repo, monkeypatch):
    from memfalsify import cli
    from memfalsify.verify import Judgment, UsageMeter

    class Metered:
        name = "stand-in"

        def __init__(self):
            self.meter = UsageMeter(self.name, caching=True)

        def judge(self, claim, passage):
            self.meter.record(SimpleNamespace(input_tokens=10, output_tokens=2, cache_read_input_tokens=5))
            return Judgment(relation="unrelated", rationale="-", verifier=self.name)

        def usage(self):
            return [self.meter.snapshot()]

    seen, real_diff = [], cli.memory_diff
    monkeypatch.setattr(cli, "build_verifier", lambda *a, **k: Metered())
    monkeypatch.setattr(cli, "memory_diff", lambda *a, **k: seen.append(k["max_concurrency"]) or real_diff(*a, **k))
    base = demo(repo)
    args = ["diff", str(repo.root / "MEMORY.md"), "--base", base, "--evidence", str(repo.root / "evidence"),
            "--verifier", "llm", "--max-concurrency", "3"]
    result = runner.invoke(app, args)
    assert result.exit_code == 0 and seen == [3]
    assert "- API usage of `stand-in`: 3 request(s) · input tokens: 30 uncached, 0 written to the prompt cache, " \
           "15 read from it · output tokens: 6" in result.stdout
    data = json.loads(runner.invoke(app, args + ["--format", "json"]).stdout)
    assert data["audit"]["verifier_stats"][0]["requests"] == 3
    monkeypatch.undo()
    result = runner.invoke(app, ["diff", str(repo.root / "MEMORY.md"), "--base", base, "--verifier", "jev", "--batch"])
    assert result.exit_code == 2 and "use it with --verifier llm, dual or cascade" in result.output


def test_diff_between_two_commits_ignores_the_working_tree(repo):
    first = demo(repo)
    second = repo.commit("second")
    repo.write({"MEMORY.md": "- Something else entirely is written here now.\n"})
    report = memory_diff(repo.root / "MEMORY.md", first, second)
    assert report.summary == {"added": 1, "changed": 3, "removed": 1, "unchanged": 1}
    assert report.head.rev == second and report.head.commit == second
    assert memory_diff(repo.root / "MEMORY.md", "HEAD~1", "HEAD").summary == report.summary


def test_diff_follows_links_one_level_and_only_inside_the_memory_directory(repo):
    repo.commit("base", {
        "notes/MEMORY.md": "- [Infra](infra.md)\n- [Outside](../outside.md)\n- [Self](./MEMORY.md)\n",
        "notes/infra.md": "- The queue runs on Kafka.\n- [Deeper](deep.md)\n",
        "notes/deep.md": "- Deeper claims are never read.\n",
        "outside.md": "- Outside claims are never read.\n",
    })
    repo.write({"notes/infra.md": "- The queue runs on RabbitMQ.\n- [Deeper](deep.md)\n",
                "notes/deep.md": "- Deeper claims changed but are never read.\n",
                "outside.md": "- Outside claims changed but are never read.\n"})
    report = memory_diff(repo.root / "notes" / "MEMORY.md", "HEAD")
    assert report.base.files == report.head.files == ["MEMORY.md", "infra.md"]
    assert report.summary == {"added": 0, "changed": 1, "removed": 0, "unchanged": 0}
    unlinked = memory_diff(repo.root / "notes" / "MEMORY.md", "HEAD", follow_links=False)
    assert unlinked.summary == {"added": 0, "changed": 0, "removed": 0, "unchanged": 0}
    assert unlinked.base.files == ["MEMORY.md"]


def test_diff_new_memory_file_and_new_link_count_as_added(repo):
    base = repo.commit("before memory", {"README.md": "Nothing to see.\n", "infra.md": "- The queue runs on Kafka.\n"})
    repo.write({"MEMORY.md": "- The cache uses Redis.\n- [Infra](infra.md)\n"})
    report = memory_diff(repo.root / "MEMORY.md", base)
    assert report.base.files == [] and report.summary["added"] == 2


@pytest.mark.skipif(os.name == "nt", reason="symlinks need extra privileges on Windows")
def test_diff_never_reads_a_symlink_blob_as_memory(repo):
    repo.write({"real.md": "- The queue runs on Kafka.\n", "MEMORY.md": "- [Infra](infra.md)\n"})
    (repo.root / "infra.md").symlink_to("real.md")
    base = repo.commit("symlinked link")
    report = memory_diff(repo.root / "MEMORY.md", base)
    # In git the link is a blob holding "real.md"; it is skipped, not parsed as a claim. The working tree
    # follows it like `audit` does, so its claims show as added and get audited.
    assert report.base.files == ["MEMORY.md"] and report.head.files == ["MEMORY.md", "infra.md"]
    assert [c.new.text for c in report.changes if c.change == "added"] == ["The queue runs on Kafka."]


def test_diff_excludes_memory_files_and_reports_from_evidence(repo):
    base = demo(repo)
    out = repo.root / "evidence" / "diff.md"
    result = runner.invoke(app, ["diff", str(repo.root / "MEMORY.md"), "--base", base, "--evidence", str(repo.root),
                                 "-o", str(out)])
    assert result.exit_code == 0, result.output
    report = memory_diff(repo.root / "MEMORY.md", base, evidence_root=repo.root)
    cited = {e.span.path for f in report.audit.findings for e in f.evidence}
    assert not cited & {"MEMORY.md", "infra.md", "evidence/diff.md"}
    assert any(s.path == "evidence/diff.md" and "report" in s.reason for s in report.audit.skipped_evidence)


def test_diff_output_never_overwrites_memory(repo):
    base = demo(repo)
    before = repo.read("infra.md")
    result = runner.invoke(app, ["diff", str(repo.root / "MEMORY.md"), "--base", base,
                                 "-o", str(repo.root / "infra.md")])
    assert result.exit_code == 2 and "refusing to overwrite" in result.output
    assert repo.read("infra.md") == before


def test_diff_reviews_and_exclude_use_audit_rules(repo):
    base = demo(repo)
    memory = str(repo.root / "MEMORY.md")
    reviews = repo.root / "reviews.jsonl"
    reviews.write_bytes(json.dumps({"memory_id": "m-ffffffff", "relation": "supports", "path": "api.md",
                                    "quote": "The public API rate limit is 60 requests per minute.",
                                    "reviewer": "r"}).encode() + b"\n")
    result = runner.invoke(app, ["diff", memory, "--base", base, "--evidence", str(repo.root / "evidence"),
                                 "--reviews", str(reviews)])
    assert result.exit_code == 2 and "unknown memory_id" in result.output
    result = runner.invoke(app, ["diff", memory, "--base", base, "--exclude", "*"])
    assert result.exit_code == 2 and "--exclude requires --evidence" in result.output
    result = runner.invoke(app, ["diff", memory, "--base", base, "--evidence", str(repo.root / "evidence"),
                                 "--exclude", "*", "--strict"])
    assert result.exit_code == 2 and "no evidence passages indexed" in result.output


# --- errors and hostile input -----------------------------------------------------------------------

def test_errors_outside_a_repository_and_without_git(tmp_path, monkeypatch, repo):
    loose = tmp_path / "loose"
    loose.mkdir()
    (loose / "MEMORY.md").write_bytes(b"- The cache uses Redis.\n")
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path))
    for args in (["diff", str(loose / "MEMORY.md"), "--base", "HEAD"], ["history", str(loose / "MEMORY.md")],
                 ["rollback", str(loose / "MEMORY.md"), "--claim", "m-12345678", "--to", "HEAD"]):
        result = runner.invoke(app, args)
        assert result.exit_code == 2 and "not inside a git work tree" in result.output
    demo(repo)
    monkeypatch.setattr(gitstore.shutil, "which", lambda name: None)
    result = runner.invoke(app, ["diff", str(repo.root / "MEMORY.md"), "--base", "HEAD"])
    assert result.exit_code == 2 and "git was not found" in result.output


@pytest.mark.skipif(os.name == "nt", reason="the CRLF shim is a POSIX PATH override; Git for Windows pipes LF")
def test_crlf_rev_parse_still_opens_the_repository(repo, tmp_path, monkeypatch):
    """A git that writes CRLF from ``rev-parse --show-toplevel`` must not leave CR on the prefix.
    ``cat-file --batch`` is exec'd through so its stdin pipe stays connected."""
    repo.commit("one", {"docs/MEMORY.md": "- The cache uses Redis.\n"})
    repo.write({"docs/MEMORY.md": "- The cache uses Memcached.\n"})
    real = shutil.which("git")
    bindir = tmp_path / "bin"
    bindir.mkdir()
    shim = bindir / "git"
    shim.write_text(
        f"#!{sys.executable}\n"
        "import os, subprocess, sys\n"
        f"real = {real!r}\n"
        "args = sys.argv[1:]\n"
        "if 'rev-parse' in args and '--show-toplevel' in args:\n"
        "    proc = subprocess.run([real, *args], stdout=subprocess.PIPE, stderr=subprocess.PIPE)\n"
        "    sys.stderr.buffer.write(proc.stderr)\n"
        "    sys.stdout.buffer.write(proc.stdout.replace(b'\\n', b'\\r\\n'))\n"
        "    raise SystemExit(proc.returncode)\n"
        "os.execv(real, [real, *args])\n",
        encoding="utf-8")
    shim.chmod(0o755)
    monkeypatch.setenv("PATH", str(bindir) + os.pathsep + os.environ["PATH"])
    memory = repo.root / "docs" / "MEMORY.md"
    opened = gitstore.open_repository(memory)
    assert opened.prefix == "docs/"
    assert opened.root == repo.root.resolve()
    report = memory_diff(memory, "HEAD")
    assert report.summary == {"added": 0, "changed": 1, "removed": 0, "unchanged": 0}


def test_diff_and_history_markdown_strip_terminal_controls(repo, tmp_path):
    """Claim text, headings and evidence quotes are untrusted. Diff and history Markdown must not carry
    terminal or bidi controls into a terminal or a job summary. Audit Markdown uses the same field escaping."""
    esc, rlo = "\x1b\x07", "\u202e"
    repo.commit("one", {"MEMORY.md": "- The cache uses Redis.\n"})
    repo.write({"MEMORY.md": f"# {esc}{rlo}Infra\n\n- The cache uses {esc}{rlo}Redis for sessions.\n",
                "evidence/note.md": f"The cache uses {esc}{rlo}Redis for sessions.\n"})
    memory = repo.root / "MEMORY.md"
    diff = memory_diff(memory, "HEAD", evidence_root=repo.root / "evidence")
    markdown = diff_to_markdown(diff)
    history = history_to_markdown(memory_history(memory))
    for report in (markdown, history):
        assert esc not in report and rlo not in report and "u001b" in report and "u202e" in report
        assert "Redis for sessions" in report
    assert any(esc in (e.span.quote or "") for f in diff.audit.findings for e in f.evidence)
    assert esc not in markdown.split("## Audit of added and changed claims", 1)[1]
    out = tmp_path / "diff.md"
    result = runner.invoke(app, ["diff", str(memory), "--base", "HEAD", "-e", str(repo.root / "evidence"),
                                 "-o", str(out)])
    assert result.exit_code == 0, result.output
    written = out.read_bytes()
    assert esc.encode() not in written and rlo.encode() not in written and b"u001b" in written and b"u202e" in written
    payload = diff.model_dump_json()
    assert rlo in payload and "\\u001b" in payload  # JSON keeps the text; only C0 is escaped
    audited = to_markdown(audit(memory, repo.root / "evidence"))
    assert esc not in audited and rlo not in audited
    assert "u001b" in audited and "u202e" in audited


@pytest.mark.skipif(os.name == "nt", reason="Windows forbids control characters in file names")
def test_memory_path_with_control_characters_is_refused(repo):
    name = "notes\nforged.md"
    repo.commit("base", {name: "- The cache uses Redis.\n"})
    result = runner.invoke(app, ["diff", str(repo.root / name), "--base", "HEAD"])
    assert result.exit_code == 2 and "control characters" in result.output


HOSTILE = ["--output=pwned.txt", "-n1", "", "HEAD\n--all", "HEAD\x00", "HEAD\x1b[31m", "$(touch pwned.txt)",
           "HEAD;touch pwned.txt", "`touch pwned.txt`", "HEAD..HEAD", "HEAD:MEMORY.md", "no-such-branch",
           "HEAD^{tree}", "@{-9}"]


@pytest.mark.parametrize("rev", HOSTILE)
def test_hostile_revisions_exit_2_and_change_nothing(repo, rev):
    demo(repo)
    before = snapshot(repo.root)
    memory = str(repo.root / "MEMORY.md")
    for args in (["diff", memory, f"--base={rev}"], ["diff", memory, "--base=HEAD", f"--head={rev}"],
                 ["rollback", memory, "--claim=m-12345678", f"--to={rev}"]):
        result = runner.invoke(app, args)
        assert result.exit_code == 2, (args, result.output)
        assert "\x1b" not in result.output
    assert snapshot(repo.root) == before and not (repo.root / "pwned.txt").exists()
    assert not Path("pwned.txt").exists()


def test_check_revision_rejects_option_like_values():
    for rev in ("-p", "--all", "", "a\nb", "a\rb", "a\x00b", "\u202eHEAD"):
        with pytest.raises(gitstore.HistoryError):
            gitstore.check_revision(rev)
    assert gitstore.check_revision("origin/main~2") == "origin/main~2"


# --- history ---------------------------------------------------------------------------------------

def test_history_attributes_introduction_changes_and_removals(repo):
    first = repo.commit("first: initial memory", {
        "MEMORY.md": "## Infra\n- The cache uses Redis.\n- Feature B ships in Q3.\n- [Infra](infra.md)\n",
        "infra.md": "- The queue runs on Kafka.\n"})
    second = repo.commit("second: edit the cache", {
        "MEMORY.md": "## Infrastructure\n- The cache uses Redis for sessions.\n- Feature B ships in Q3.\n"
                     "- [Infra](infra.md)\n"})
    third = repo.commit("third: drop feature B, recase", {
        "MEMORY.md": "## Infrastructure\n- the cache uses redis for sessions.\n- [Infra](infra.md)\n"})
    fourth = repo.commit("fourth: queue moves", {"infra.md": "- The queue runs on RabbitMQ.\n"})
    repo.write({"MEMORY.md": "## Infrastructure\n- the cache uses redis for sessions.\n- [Infra](infra.md)\n"
                             "- Deploys go through staging.\n"})
    report = memory_history(repo.root / "MEMORY.md")
    assert report.complete and report.commits_scanned == 4 and report.head == fourth
    found = {c.text: c for c in report.claims}
    cache = found["the cache uses redis for sessions."]
    assert cache.introduced.commit == second and cache.last_changed.commit == third  # recased in third
    assert cache.replaced.text == "The cache uses Redis." and cache.introduced.subject == "second: edit the cache"
    assert cache.introduced.author == "Test Author" and cache.introduced.date.startswith("2026-10-01T")
    queue = found["The queue runs on RabbitMQ."]
    assert queue.introduced.commit == queue.last_changed.commit == fourth and queue.location == "infra.md:1"
    deploys = found["Deploys go through staging."]
    assert deploys.introduced.commit is None and "working tree" in deploys.introduced.subject
    [gone] = report.removed
    assert gone.text == "Feature B ships in Q3." and gone.removed.commit == third
    assert gone.introduced.commit == first
    text = runner.invoke(app, ["history", str(repo.root / "MEMORY.md")]).stdout
    assert text.startswith("# MemFalsify claim history") and f"`{third[:12]}`" in text
    data = json.loads(runner.invoke(app, ["history", str(repo.root / "MEMORY.md"), "--format", "json"]).stdout)
    assert data["tool"] == "memfalsify" and data["removed"][0]["memory_id"] == gone.memory_id


def test_history_max_commits_marks_older_claims(repo):
    repo.commit("one", {"MEMORY.md": "- The cache uses Redis.\n"})
    repo.commit("two", {"MEMORY.md": "- The cache uses Redis.\n- The queue runs on Kafka.\n"})
    last = repo.commit("three", {"MEMORY.md": "- The cache uses Redis.\n- The queue runs on Kafka.\n"
                                              "- Deploys go through staging.\n"})
    report = memory_history(repo.root / "MEMORY.md", max_commits=1)
    assert not report.complete and report.commits_scanned == 1
    found = {c.text: c.introduced for c in report.claims}
    assert found["The cache uses Redis."] is None and found["The queue runs on Kafka."] is None
    assert found["Deploys go through staging."].commit == last
    text = runner.invoke(app, ["history", str(repo.root / "MEMORY.md"), "--max-commits", "1"]).stdout
    assert "before the scanned commits" in text and "--max-commits 1" in text
    assert runner.invoke(app, ["history", str(repo.root / "MEMORY.md"), "--max-commits", "0"]).exit_code == 2


def test_history_follows_the_first_parent_line(repo):
    repo.commit("main one", {"MEMORY.md": "- The cache uses Redis.\n"})
    repo.git("checkout", "-q", "-b", "feature")
    side = repo.commit("feature: add queue", {"MEMORY.md": "- The cache uses Redis.\n- The queue runs on Kafka.\n"})
    repo.git("checkout", "-q", "main")
    repo.commit("main two", {"notes.txt": "unrelated\n"})
    repo.git("merge", "-q", "--no-ff", "-m", "merge feature", "feature")
    merge = repo.git("rev-parse", "HEAD").strip()
    report = memory_history(repo.root / "MEMORY.md")
    queue = next(c for c in report.claims if c.text == "The queue runs on Kafka.")
    assert queue.introduced.commit == merge != side
    assert report.commits_scanned == 2


def test_history_includes_commits_to_files_linked_in_the_past(repo):
    repo.commit("one", {"MEMORY.md": "- [Old](old.md)\n", "old.md": "- Legacy builds use Jenkins.\n"})
    edit = repo.commit("two", {"old.md": "- Legacy builds use Jenkins nightly.\n"})
    unlink = repo.commit("three", {"MEMORY.md": "- The cache uses Redis.\n"})
    report = memory_history(repo.root / "MEMORY.md")
    assert report.commits_scanned == 3
    [gone] = report.removed
    assert gone.text == "Legacy builds use Jenkins nightly." and gone.removed.commit == unlink
    assert gone.introduced.commit == edit and gone.location == "old.md:1"


def test_history_stops_honestly_at_a_shallow_clone(repo, tmp_path):
    repo.commit("one", {"MEMORY.md": "- The cache uses Redis.\n"})
    repo.commit("two", {"MEMORY.md": "- The cache uses Redis.\n- The queue runs on Kafka.\n"})
    last = repo.commit("three", {"MEMORY.md": "- The cache uses Redis.\n- The queue runs on Kafka.\n"
                                              "- Deploys go through staging.\n"})
    clone = tmp_path / "clone"
    repo.git("clone", "-q", "--depth", "2", repo.root.as_uri(), str(clone))
    report = memory_history(clone / "MEMORY.md")
    assert report.shallow and not report.complete and report.commits_scanned == 1
    found = {c.text: c.introduced for c in report.claims}
    assert found["The cache uses Redis."] is None and found["The queue runs on Kafka."] is None
    assert found["Deploys go through staging."].commit == last
    assert "the clone is shallow" in runner.invoke(app, ["history", str(clone / "MEMORY.md")]).stdout
    assert not memory_history(repo.root / "MEMORY.md").shallow  # the root commit of a full clone is real


def test_history_attributes_the_root_commit_inside_a_shallow_clone(repo, tmp_path):
    """``clone --depth`` that reaches the root still has no parent *object*, but the commit itself has no
    ``parent`` line. That history is complete. A boundary commit still records the parent it did not fetch."""
    repo.commit("one", {"MEMORY.md": "- The cache uses Redis.\n"})
    repo.commit("two", {"MEMORY.md": "- The cache uses Redis.\n- The queue runs on Kafka.\n"})
    reached = tmp_path / "reached"
    repo.git("clone", "-q", "--depth", "2", repo.root.as_uri(), str(reached))
    report = memory_history(reached / "MEMORY.md")
    assert report.complete and not report.shallow and report.commits_scanned == 2
    assert {c.text: c.introduced.subject for c in report.claims} == {
        "The cache uses Redis.": "one", "The queue runs on Kafka.": "two"}
    assert "clone is shallow" not in runner.invoke(app, ["history", str(reached / "MEMORY.md")]).stdout

    boundary = tmp_path / "boundary"
    repo.git("clone", "-q", "--depth", "1", repo.root.as_uri(), str(boundary))
    report = memory_history(boundary / "MEMORY.md")
    assert report.shallow and not report.complete and report.commits_scanned == 0
    assert all(c.introduced is None for c in report.claims)

    solo_root = tmp_path / "solo"
    solo_root.mkdir()
    solo = Repo(solo_root, repo.hooks)
    solo.git("init", "-q")
    solo.git("config", "--local", "user.name", "Test Author")
    solo.git("config", "--local", "user.email", "test@example.com")
    # The subject itself begins with "parent ", which must not be read as a commit header.
    solo.commit("parent is not a header", {"MEMORY.md": "- The cache uses Redis.\n"})
    solo_clone = tmp_path / "solo-clone"
    solo.git("clone", "-q", "--depth", "1", solo_root.as_uri(), str(solo_clone))
    report = memory_history(solo_clone / "MEMORY.md")
    assert report.complete and not report.shallow and report.commits_scanned == 1
    assert report.claims[0].introduced.subject == "parent is not a header"


def test_history_in_a_repository_without_commits(repo):
    repo.write({"MEMORY.md": "- The cache uses Redis.\n"})
    report = memory_history(repo.root / "MEMORY.md")
    assert report.head is None and report.commits_scanned == 0
    assert report.claims[0].introduced.commit is None
    assert "no commits yet" in runner.invoke(app, ["history", str(repo.root / "MEMORY.md")]).stdout


# --- rollback --------------------------------------------------------------------------------------

def apply(repo: Repo, patch: bytes, check_only: bool = False) -> None:
    path = repo.root.parent / "rollback.patch"
    path.write_bytes(patch)
    repo.git("apply", "--check", str(path))
    if not check_only:
        repo.git("apply", str(path))


def test_rollback_restores_old_wording_with_markup(repo):
    base = demo(repo)
    new_id = next(c.id for c in claims_of(HEAD_MEMORY) if c.text == "The cache uses Redis for sessions.")
    before = snapshot(repo.root)
    result = runner.invoke(app, ["rollback", str(repo.root / "MEMORY.md"), "--claim", new_id, "--to", base])
    assert result.exit_code == 0, result.output
    patch = result.stdout_bytes
    assert patch.startswith(b"# MemFalsify rollback patch") and b"git apply" in patch
    assert snapshot(repo.root) == before  # printed, never applied
    apply(repo, patch)
    expected = HEAD_MEMORY.replace("- The cache uses **Redis** for sessions.", "- The cache uses **Redis**.")
    assert repo.read("MEMORY.md") == expected.encode()  # only that claim; other edits stay
    old_id = next(c.id for c in claims_of(BASE_MEMORY) if c.text == "The cache uses Redis.")
    assert rollback_patch(repo.root / "MEMORY.md", old_id, base).patch is None  # now unchanged


def test_rollback_restores_wrapping_link_and_emphasis(repo):
    old = ("- [The queue runs on Kafka.](https://q.example/old)\n"
           "- **The cache uses Redis.**\n")
    new = ("- [The queue runs on RabbitMQ.](https://q.example/new)\n"
           "- The cache uses Memcached.\n")
    base = repo.commit("base", {"MEMORY.md": old})
    repo.write({"MEMORY.md": new})
    queue = claims_of("- The queue runs on RabbitMQ.\n")[0].id
    cache = claims_of("- The cache uses Memcached.\n")[0].id
    apply(repo, rollback_patch(repo.root / "MEMORY.md", queue, base).patch)
    assert repo.read("MEMORY.md") == ("- [The queue runs on Kafka.](https://q.example/old)\n"
                                      "- The cache uses Memcached.\n").encode()
    apply(repo, rollback_patch(repo.root / "MEMORY.md", cache, base).patch)
    assert repo.read("MEMORY.md") == old.encode()

    longer = "- The cache uses Redis. [The queue runs on Kafka.](https://q.example/old)\n"
    base = repo.commit("longer", {"MEMORY.md": longer})
    repo.write({"MEMORY.md": "- The cache uses Redis.\n"})
    gone = claims_of("- The queue runs on Kafka.\n")[0].id
    apply(repo, rollback_patch(repo.root / "MEMORY.md", gone, base).patch)
    assert repo.read("MEMORY.md") == ("- The cache uses Redis.\n"
                                      "- [The queue runs on Kafka.](https://q.example/old)\n").encode()

    paired = "- [Alpha ships in Q3.](https://a.example) [Beta ships in Q4.](https://b.example)\n"
    base = repo.commit("paired", {"MEMORY.md": paired})
    repo.write({"MEMORY.md": "- [Alpha ships in Q3.](https://a.example) [Beta ships in Q5.](https://b.example/new)\n"})
    beta = claims_of("- Beta ships in Q5.\n")[0].id
    apply(repo, rollback_patch(repo.root / "MEMORY.md", beta, base).patch)
    assert repo.read("MEMORY.md") == paired.encode()


def test_rollback_accepts_the_old_id_and_linked_files(repo):
    base = demo(repo)
    old_id = claims_of("- The queue runs on Kafka.\n")[0].id
    result = rollback_patch(repo.root / "MEMORY.md", old_id, base)
    assert b"diff --git a/infra.md b/infra.md" in result.patch
    apply(repo, result.patch)
    assert repo.read("infra.md") == b"- The queue runs on Kafka.\n"


def test_rollback_readds_a_removed_claim_near_its_old_position(repo):
    base = demo(repo)
    gone = next(c.id for c in claims_of(BASE_MEMORY) if c.text == "Feature B ships in Q3.")
    apply(repo, rollback_patch(repo.root / "MEMORY.md", gone, base).patch)
    # It followed the (since edited) merge-policy claim in the Process section.
    assert repo.read("MEMORY.md") == HEAD_MEMORY.replace(
        "- We use rebase merges for pull requests.\n",
        "- We use rebase merges for pull requests.\n- Feature B ships in Q3.\n").encode()


def test_rollback_readds_one_sentence_of_a_longer_line(repo):
    base = repo.commit("base", {"MEMORY.md": "# Notes\n\n* Feature B was postponed. It ships in Q3.\n"})
    repo.write({"MEMORY.md": "# Notes\n\n* Feature B was postponed.\n"})
    gone = claims_of("It ships in Q3.\n")[0].id
    apply(repo, rollback_patch(repo.root / "MEMORY.md", gone, base).patch)
    assert repo.read("MEMORY.md") == b"# Notes\n\n* Feature B was postponed.\n* It ships in Q3.\n"


def test_rollback_without_anchors_goes_under_the_heading_or_at_the_end(repo):
    base = repo.commit("base", {"MEMORY.md": "# A\n\n- The cache uses Redis.\n\n# B\n\n- The queue runs on Kafka."})
    repo.write({"MEMORY.md": "# A\n\n# C\n\n- Deploys go through staging."})
    cache, queue = (c.id for c in claims_of("- The cache uses Redis.\n- The queue runs on Kafka.\n"))
    apply(repo, rollback_patch(repo.root / "MEMORY.md", cache, base).patch)
    assert repo.read("MEMORY.md") == b"# A\n\n- The cache uses Redis.\n# C\n\n- Deploys go through staging."
    apply(repo, rollback_patch(repo.root / "MEMORY.md", queue, base).patch)  # heading B is gone
    assert repo.read("MEMORY.md").endswith(b"- Deploys go through staging.\n- The queue runs on Kafka.\n")


@pytest.mark.parametrize("name", ["MEMORY.md", "my notes/记忆.md", pytest.param(
    'odd "name".md', marks=pytest.mark.skipif(os.name == "nt", reason="Windows forbids quotes in file names"))])
def test_rollback_patch_applies_with_crlf_and_unusual_paths(repo, name):
    base = repo.commit("base", {name: "- The cache uses Redis.\r\n- Deploys run at 5 pm.\r\n- Feature B ships in Q3."})
    repo.write({name: "- The cache uses Memcached.\r\n- Deploys run at 5 pm."})
    memory = repo.root / name
    changed = rollback_patch(memory, claims_of("- The cache uses Memcached.\n")[0].id, base).patch
    readd = rollback_patch(memory, claims_of("- Feature B ships in Q3.\n")[0].id, base).patch
    apply(repo, changed, check_only=True)
    apply(repo, readd)
    assert repo.read(name) == b"- The cache uses Memcached.\r\n- Deploys run at 5 pm.\r\n- Feature B ships in Q3.\r\n"
    apply(repo, rollback_patch(memory, claims_of("- The cache uses Memcached.\n")[0].id, base).patch)
    assert repo.read(name).startswith(b"- The cache uses Redis.\r\n")


def test_rollback_from_a_subdirectory_memory(repo):
    base = repo.commit("base", {"docs/agents/AGENTS.md": "- Tests run with pytest.\n"})
    repo.write({"docs/agents/AGENTS.md": "- Tests run with pytest and coverage.\n"})
    result = rollback_patch(repo.root / "docs" / "agents" / "AGENTS.md",
                            claims_of("- Tests run with pytest and coverage.\n")[0].id, base)
    assert b"--- a/docs/agents/AGENTS.md" in result.patch
    apply(repo, result.patch)
    assert repo.read("docs/agents/AGENTS.md") == b"- Tests run with pytest.\n"


def test_rollback_refusals(repo):
    base = demo(repo)
    memory = str(repo.root / "MEMORY.md")
    unchanged = claims_of("- The main database runs PostgreSQL 15.\n")[0].id
    added = claims_of("- The public API rate limit is 100 requests per minute.\n")[0].id
    result = runner.invoke(app, ["rollback", memory, "--claim", unchanged, "--to", base])
    assert result.exit_code == 0 and result.stdout == "" and "nothing to roll back" in result.output
    result = runner.invoke(app, ["rollback", memory, "--claim", added, "--to", base])
    assert result.exit_code == 2 and "did not exist" in result.output
    result = runner.invoke(app, ["rollback", memory, "--claim", "m-00000000", "--to", base])
    assert result.exit_code == 2 and "is not a claim" in result.output
    result = runner.invoke(app, ["rollback", memory, "--claim", "../../etc/passwd", "--to", base])
    assert result.exit_code == 2 and "looks like m-" in result.output


def test_rollback_output_file_and_memory_protection(repo):
    base = demo(repo)
    memory = str(repo.root / "MEMORY.md")
    claim = claims_of("- The queue runs on RabbitMQ.\n")[0].id
    before = repo.read("infra.md")
    result = runner.invoke(app, ["rollback", memory, "--claim", claim, "--to", base, "-o", str(repo.root / "infra.md")])
    assert result.exit_code == 2 and "refusing to overwrite" in result.output and repo.read("infra.md") == before
    out = repo.root.parent / "fix.patch"
    result = runner.invoke(app, ["rollback", memory, "--claim", claim, "--to", base, "-o", str(out)])
    assert result.exit_code == 0 and result.stdout == ""
    apply(repo, out.read_bytes(), check_only=True)


def test_rollback_refuses_a_file_that_no_longer_exists(repo):
    base = repo.commit("base", {"MEMORY.md": "- [Old](old.md)\n", "old.md": "- Legacy builds use Jenkins.\n"})
    repo.write({"MEMORY.md": "- The cache uses Redis.\n", "old.md": None})
    result = runner.invoke(app, ["rollback", str(repo.root / "MEMORY.md"), "--to", base,
                                 "--claim", claims_of("- Legacy builds use Jenkins.\n")[0].id])
    assert result.exit_code == 2 and "no longer exists" in result.output
