"""Staged diffs and the pre-commit hooks. Git tests skip where git is absent; the manifest checks do not need it."""
import json
import os
import re
import shutil
import subprocess
from datetime import date
from pathlib import Path

import pytest
from typer.testing import CliRunner

from memfalsify.cli import app as memfalsify_app
from memfalsify.gitstore import HistoryError, _index_entry
from memfalsify.history import audit_staged, diff_to_markdown, memory_diff
from memfalsify.precommit import MEMORY_NAMES, app

ROOT = Path(__file__).resolve().parent.parent
runner = CliRunner()
AS_OF = date(2026, 10, 10)
needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")


@pytest.fixture
def repo(git_repo):
    return git_repo


def worktree_bytes(root: Path) -> dict[str, bytes]:
    found = {}
    for path in root.rglob("*"):
        if path.is_file() and ".git" not in path.parts:
            found[path.relative_to(root).as_posix()] = path.read_bytes()
    return found


def git_raw(repo, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([shutil.which("git"), "-C", str(repo.root), "-c", "commit.gpgsign=false",
                           "-c", "core.autocrlf=false", *args],
                          capture_output=True, env=os.environ, check=False)


# --- index record parser (no git) -----------------------------------------------------------------

def test_index_parser_reads_one_regular_file_and_rejects_conflicts():
    blob = "100644 " + "a" * 40 + " 0\tCLAUDE.md\0"
    assert _index_entry(blob.encode("utf-8"), "CLAUDE.md") == ("100644", "a" * 40)
    assert _index_entry(blob.encode("utf-8"), "other.md") is None
    conflict = "100644 " + "b" * 40 + " 1\tCLAUDE.md\0"
    with pytest.raises(HistoryError, match="unmerged"):
        _index_entry(conflict.encode("utf-8"), "CLAUDE.md")
    with pytest.raises(HistoryError, match="unexpected"):
        _index_entry(b"not-a-record\0", "CLAUDE.md")


# --- manifest --------------------------------------------------------------------------------------

def test_hook_manifest_names_the_two_hooks_and_the_three_files():
    text = (ROOT / ".pre-commit-hooks.yaml").read_text(encoding="utf-8")
    patterns = re.findall(r"files: '([^']+)'", text)
    assert patterns == ["(^|/)(CLAUDE|AGENTS|MEMORY)\\.md$"] * 2
    pattern = patterns[0]
    for name in MEMORY_NAMES:
        assert re.search(pattern, name)
        assert re.search(pattern, f"docs/{name}")
    assert not re.search(pattern, "notes.md")
    assert not re.search(pattern, "CLAUDE.md.bak")
    assert not re.search(pattern, "notMEMORY.md")
    assert text.count("id: memfalsify-diff") == 1
    assert text.count("id: memfalsify-audit") == 1
    assert "entry: python -X utf8 -m memfalsify.precommit diff" in text
    assert "entry: python -X utf8 -m memfalsify.precommit audit" in text
    assert text.count("language: python") == 2
    assert text.count("stages: [pre-commit]") == 2
    assert text.count('minimum_pre_commit_version: "3.2.0"') == 2
    assert text.count("require_serial: true") == 2
    assert text.count("pass_filenames: true") == 2
    project = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    package = (ROOT / "packages/memfalsify/pyproject.toml").read_text(encoding="utf-8")
    assert 'memfalsify-pre-commit = "memfalsify.precommit:main"' in project
    assert 'memfalsify-pre-commit = "memfalsify.precommit:main"' in package
    assert "packages/memfalsify/src" in project and "packages/memcore/src" in project


def test_hook_manifest_loads_with_pre_commit_when_installed():
    clientlib = pytest.importorskip("pre_commit.clientlib")
    hooks = clientlib.load_manifest(str(ROOT / ".pre-commit-hooks.yaml"))
    assert [hook["id"] for hook in hooks] == ["memfalsify-diff", "memfalsify-audit"]
    for hook in hooks:
        assert hook["language"] == "python"
        assert hook["stages"] == ["pre-commit"]
        assert hook["types"] == ["file"]
        assert hook["pass_filenames"] is True
        assert hook["require_serial"] is True


# --- staged diff -----------------------------------------------------------------------------------

@needs_git
def test_staged_diff_ignores_unstaged_memory_and_reads_working_tree_evidence(repo):
    repo.commit("base", {"CLAUDE.md": "- The cache uses Redis.\n",
                         "evidence/api.md": "The public API rate limit is 100 requests per minute.\n"})
    repo.write({"CLAUDE.md": "- The public API rate limit is 100 requests per minute.\n"})
    repo.git("add", "--", "CLAUDE.md")
    # Unstaged memory must be ignored. Unstaged evidence must be what the audit sees (60, not the
    # committed 100, which would agree with the staged claim).
    repo.write({"CLAUDE.md": "- The cache uses Memcached for sessions.\n",
                "evidence/api.md": "The public API rate limit is 60 requests per minute.\n"})
    staged = memory_diff(repo.root / "CLAUDE.md", "HEAD", staged=True, evidence_root=repo.root / "evidence",
                         as_of=AS_OF)
    assert staged.head.rev == "index" and staged.head.commit is None
    assert staged.summary == {"added": 1, "changed": 0, "removed": 1, "unchanged": 0}
    assert {f.text: f.status for f in staged.audit.findings} == {
        "The public API rate limit is 100 requests per minute.": "candidate_counterevidence"}
    text = diff_to_markdown(staged)
    assert "the index (staged content)" in text
    assert "The head version is the git index" in text
    assert "Memcached" not in text
    work = memory_diff(repo.root / "CLAUDE.md", "HEAD", evidence_root=repo.root / "evidence", as_of=AS_OF)
    assert [finding.text for finding in work.audit.findings] == ["The cache uses Memcached for sessions."]
    assert work.head.rev == "working tree"


@needs_git
def test_staged_diff_reads_linked_files_from_the_index(repo):
    repo.commit("base", {"CLAUDE.md": "- The cache uses Redis.\n- [Notes](infra.md)\n",
                         "infra.md": "- The queue runs on Kafka.\n"})
    repo.write({"CLAUDE.md": "- The cache uses Redis.\n- [Notes](infra.md)\n",
                "infra.md": "- The queue runs on RabbitMQ.\n"})
    repo.git("add", "--", "CLAUDE.md", "infra.md")
    repo.write({"infra.md": "- The queue runs on PostgreSQL.\n"})
    staged = memory_diff(repo.root / "CLAUDE.md", "HEAD", staged=True, as_of=AS_OF)
    new = [change.new.text for change in staged.changes if change.new]
    assert "The queue runs on RabbitMQ." in new
    assert "The queue runs on PostgreSQL." not in new
    work = memory_diff(repo.root / "CLAUDE.md", "HEAD", as_of=AS_OF)
    assert "The queue runs on PostgreSQL." in [change.new.text for change in work.changes if change.new]


@needs_git
def test_staged_diff_does_not_fail_on_an_unchanged_contradiction(repo):
    repo.commit("base", {"MEMORY.md": "- The main database runs PostgreSQL 15.\n",
                         "evidence/db.md": "The main database runs PostgreSQL 16.\n"})
    repo.write({"MEMORY.md": "- The main database runs PostgreSQL 15.\n- Feature B ships in Q3.\n"})
    repo.git("add", "--", "MEMORY.md")
    staged = memory_diff(repo.root / "MEMORY.md", "HEAD", staged=True, evidence_root=repo.root / "evidence",
                         as_of=AS_OF)
    assert staged.summary["added"] == 1 and staged.summary["unchanged"] == 1 and staged.attention == 0
    full = audit_staged(repo.root / "MEMORY.md", evidence_root=repo.root / "evidence", as_of=AS_OF)
    assert full.summary.get("candidate_counterevidence") == 1
    assert any("git index" in line for line in full.limitations)


@needs_git
def test_staged_crlf_blob_matches_the_lf_claim_at_head(repo):
    repo.commit("base", {"MEMORY.md": "- The cache uses Redis.\n"})
    (repo.root / "MEMORY.md").write_bytes("- The cache uses Redis.\r\n".encode("utf-8"))
    repo.git("add", "--", "MEMORY.md")
    (repo.root / "MEMORY.md").write_bytes("- The cache uses Memcached.\n".encode("utf-8"))
    staged = memory_diff(repo.root / "MEMORY.md", "HEAD", staged=True, as_of=AS_OF)
    assert staged.summary == {"added": 0, "changed": 0, "removed": 0, "unchanged": 1}
    assert "Memcached" not in diff_to_markdown(staged)


@needs_git
def test_staged_diff_of_the_first_commit_audits_every_claim(repo):
    repo.write({"MEMORY.md": "- The public API rate limit is 100 requests per minute.\n",
                "evidence/api.md": "The public API rate limit is 60 requests per minute.\n"})
    repo.git("add", "-A")
    report = memory_diff(repo.root / "MEMORY.md", "HEAD", staged=True, evidence_root=repo.root / "evidence",
                         as_of=AS_OF)
    assert report.base.commit is None and report.base.rev == "HEAD"
    assert report.summary["added"] == 1 and report.attention == 1
    assert "no commit yet" in diff_to_markdown(report)
    with pytest.raises(HistoryError, match="only accepts --base HEAD"):
        memory_diff(repo.root / "MEMORY.md", "main", staged=True)


@needs_git
@pytest.mark.skipif(os.name == "nt", reason="symlinks need extra privileges on Windows")
def test_staged_diff_does_not_read_a_symlink_blob(repo):
    repo.commit("base", {"CLAUDE.md": "- The cache uses Redis.\n", "other.md": "- The queue runs on Kafka.\n"})
    (repo.root / "CLAUDE.md").unlink()
    (repo.root / "CLAUDE.md").symlink_to("other.md")
    repo.git("add", "--", "CLAUDE.md")
    report = memory_diff(repo.root / "CLAUDE.md", "HEAD", staged=True, as_of=AS_OF)
    assert report.head.files == []
    assert report.attention == 0
    assert "Kafka" not in diff_to_markdown(report)


@needs_git
def test_staged_diff_refuses_an_unmerged_path(repo):
    repo.commit("base", {"MEMORY.md": "- The cache uses Redis.\n"})
    ours = repo.git("rev-parse", "--abbrev-ref", "HEAD").strip()
    repo.git("branch", "other")
    repo.write({"MEMORY.md": "- The cache uses Redis for sessions.\n"})
    repo.commit("ours")
    repo.git("checkout", "other")
    repo.write({"MEMORY.md": "- The cache uses Memcached.\n"})
    repo.commit("theirs")
    merged = git_raw(repo, "merge", ours)
    assert merged.returncode != 0, merged.stderr.decode()
    with pytest.raises(HistoryError, match="unmerged"):
        memory_diff(repo.root / "MEMORY.md", "HEAD", staged=True)


def test_staged_flag_rejects_a_head_revision(tmp_path):
    path = tmp_path / "MEMORY.md"
    path.write_bytes("- The cache uses Redis.\n".encode("utf-8"))
    result = runner.invoke(memfalsify_app, ["diff", str(path), "--base", "HEAD", "--head", "main", "--staged"])
    assert result.exit_code == 2 and "head revision" in result.output
    assert path.read_bytes() == "- The cache uses Redis.\n".encode("utf-8")


# --- hook entry point ------------------------------------------------------------------------------

def _stage_contradiction(repo) -> None:
    repo.commit("base", {"CLAUDE.md": "- The cache uses Redis.\n",
                         "evidence/api.md": "The public API rate limit is 60 requests per minute.\n"})
    repo.write({"CLAUDE.md": "- The public API rate limit is 100 requests per minute.\n"})
    repo.git("add", "--", "CLAUDE.md")


@needs_git
def test_hook_diff_fails_only_for_attention_and_audit_checks_old_claims(repo, monkeypatch):
    repo.commit("base", {"CLAUDE.md": "- The main database runs PostgreSQL 15.\n",
                         "MEMORY.md": "- The cache uses Redis.\n",
                         "evidence/db.md": "The main database runs PostgreSQL 16.\n"})
    repo.write({"CLAUDE.md": "- The main database runs PostgreSQL 15.\n- Feature B ships in Q3.\n"})
    repo.git("add", "--", "CLAUDE.md")
    before = worktree_bytes(repo.root)
    tree = repo.git("write-tree")
    head = repo.git("rev-parse", "HEAD")
    monkeypatch.chdir(repo.root)
    diff = runner.invoke(app, ["diff", "CLAUDE.md", "--evidence", "evidence", "--as-of", "2026-10-10"])
    assert diff.exit_code == 0, diff.output
    assert "Feature B ships in Q3." in diff.stdout
    assert "PostgreSQL 15" not in diff.stdout  # unchanged, so the diff hook does not audit it
    audit = runner.invoke(app, ["audit", "CLAUDE.md", "--evidence", "evidence", "--as-of", "2026-10-10"])
    assert audit.exit_code == 1, audit.output
    assert "POSSIBLE COUNTEREVIDENCE" in audit.stdout or "candidate_counterevidence" in audit.stdout
    quiet = runner.invoke(app, ["audit", "CLAUDE.md", "--no-strict", "--evidence", "evidence",
                                "--as-of", "2026-10-10"])
    assert quiet.exit_code == 0, quiet.output
    assert repo.git("write-tree") == tree
    assert repo.git("rev-parse", "HEAD") == head
    assert worktree_bytes(repo.root) == before


@needs_git
def test_hook_discovers_staged_files_and_ignores_unstaged_ones(repo, monkeypatch):
    _stage_contradiction(repo)
    repo.write({"MEMORY.md": "- The public API rate limit is 100 requests per minute.\n"})  # not staged
    monkeypatch.chdir(repo.root)
    result = runner.invoke(app, ["diff", "--evidence", "evidence", "--as-of", "2026-10-10", "--format", "json"])
    assert result.exit_code == 1, result.output
    data = json.loads(result.stdout)
    assert data["tool"] == "memfalsify" and data["hook"] == "diff" and data["attention"] == 1
    assert data["reports"][0]["head"]["rev"] == "index"
    assert "MEMORY.md" not in result.stdout


@needs_git
def test_hook_refuses_paths_outside_the_repository(repo, monkeypatch):
    _stage_contradiction(repo)
    monkeypatch.chdir(repo.root)
    outside = runner.invoke(app, ["diff", "../CLAUDE.md"])
    assert outside.exit_code == 2 and "refusing path" in outside.output
    absolute = runner.invoke(app, ["diff", str(repo.root / "CLAUDE.md")])
    assert absolute.exit_code == 2 and "refusing path" in absolute.output
    other = runner.invoke(app, ["diff", "README.md"])
    assert other.exit_code == 0 and "skipping README.md" in other.stderr
    assert "no staged" in other.stderr
    nested = repo.root / "docs"
    nested.mkdir()
    (nested / "AGENTS.md").write_bytes("- The cache uses Redis.\n".encode("utf-8"))
    repo.git("add", "--", "docs/AGENTS.md")
    found = runner.invoke(app, ["diff", "docs/AGENTS.md", "--as-of", "2026-10-10"])
    assert found.exit_code == 0, found.output
    assert "The cache uses Redis." in found.stdout


@needs_git
@pytest.mark.skipif(os.name == "nt", reason="symlinks need extra privileges on Windows")
def test_hook_refuses_a_symlink_memory_path(repo, monkeypatch):
    repo.commit("base", {"notes.md": "- The cache uses Redis.\n"})
    (repo.root / "CLAUDE.md").symlink_to("notes.md")
    repo.git("add", "--", "CLAUDE.md")
    monkeypatch.chdir(repo.root)
    result = runner.invoke(app, ["diff", "CLAUDE.md"])
    assert result.exit_code == 2 and "symlink" in result.output


@needs_git
def test_hook_exclude_that_indexes_nothing_exits_2(repo, monkeypatch):
    _stage_contradiction(repo)
    monkeypatch.chdir(repo.root)
    result = runner.invoke(app, ["diff", "CLAUDE.md", "--evidence", "evidence", "--exclude", "*",
                                 "--as-of", "2026-10-10"])
    assert result.exit_code == 2 and "no evidence passages" in result.output


@needs_git
def test_hook_chinese_report_is_utf8(repo, monkeypatch):
    repo.commit("base", {"MEMORY.md": "- 缓存使用 Redis。\n"})
    repo.write({"MEMORY.md": "- 缓存使用 Redis 保存会话。\n"})
    repo.git("add", "--", "MEMORY.md")
    monkeypatch.chdir(repo.root)
    result = runner.invoke(app, ["diff", "--as-of", "2026-10-10"])
    assert result.exit_code == 0, result.output
    assert "缓存使用 Redis 保存会话" in result.stdout


@needs_git
def test_hook_markdown_keeps_untrusted_text_inert_and_json_escapes_controls(repo, monkeypatch):
    """Claim text and evidence quotes are untrusted. Both hooks print them on a terminal.

    Markdown must not contain ESC, CR or bidi controls. JSON keeps the text, with C0 escaped, matching
    ``memfalsify diff --format json``.
    """
    esc, rlo, cr = "\x1b[2J", "\u202e", "\r"
    repo.commit("base", {"CLAUDE.md": "- The cache uses Redis.\n",
                         "evidence/note.md": "The cache uses Redis.\n"})
    repo.write({"CLAUDE.md": f"- The cache uses Redis for {esc}{rlo}sessions.\n",
                "evidence/note.md": f"The cache uses Redis{cr} for {esc}{rlo}sessions.\n"})
    repo.git("add", "--", "CLAUDE.md")
    monkeypatch.chdir(repo.root)
    common = ["CLAUDE.md", "--evidence", "evidence", "--as-of", "2026-10-10"]
    for mode in ("diff", "audit"):
        result = runner.invoke(app, [mode, *common])
        assert result.exit_code in (0, 1), result.output
        assert esc not in result.stdout and rlo not in result.stdout and cr not in result.stdout
        assert "u001b" in result.stdout and "u202e" in result.stdout and "sessions" in result.stdout
    payload = runner.invoke(app, ["audit", *common, "--format", "json"])
    assert payload.exit_code in (0, 1), payload.output
    # JSON on a terminal escapes format characters too; the parsed data keeps them (checked below).
    assert rlo not in payload.stdout and esc not in payload.stdout and cr not in payload.stdout
    assert "\\u001b" in payload.stdout and "\\u202e" in payload.stdout
    data = json.loads(payload.stdout)
    quotes = [e["span"]["quote"] for report in data["reports"] for finding in report["findings"]
              for e in finding["evidence"]]
    assert any(cr in quote and esc in quote and rlo in quote for quote in quotes)


def test_docs_name_the_strict_staged_command():
    """The hook fails closed and reads evidence. The CLI does neither unless the command says so."""
    command = "memfalsify diff CLAUDE.md --base HEAD --staged --evidence . --strict"
    for name in ("README.md", "README.zh-CN.md", "skills/memfalsify/SKILL.md", "docs/github-action.md",
                 "packages/memfalsify/src/memfalsify/precommit.py"):
        text = (ROOT / name).read_text(encoding="utf-8")
        assert command in text, name
