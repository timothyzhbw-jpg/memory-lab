"""Checkout credential regressions: use synthetic credentials, never the host's config."""
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from memfalsify.audit import audit
from memfalsify.memory import parse_memory
from memfalsify.report import to_markdown
from memfalsify.corpus import scan
from memfalsify.history import memory_diff
from memcore import append_jsonl
from memcore.paths import InputPathError, input_path
from conftest import requires_confinement
from orgmemory.github_apply import lock


ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("security_action", ROOT / "action/memfalsify_action.py")
action = importlib.util.module_from_spec(spec)
spec.loader.exec_module(action)
TOKEN = "ghs_SUPERSECRETTOKEN_do_not_leak"
requires_git = pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")


@pytest.fixture
def credential_checkout(git_repo, make_symlink):
    root = git_repo.root
    with (root / ".git/config").open("a", encoding="utf-8") as handle:
        handle.write(f"\n[http]\n\textraheader = AUTHORIZATION: basic {TOKEN}\n")
    make_symlink(root / "AGENTS.md", ".git/config")
    (root / "notes").mkdir()
    make_symlink(root / "notes/harmless.md", "../.git/config")
    (root / "MEMORY.md").write_text("extraheader AUTHORIZATION basic.\n", encoding="utf-8")
    return root


@requires_git
def test_library_never_reads_checkout_credentials_as_memory(credential_checkout):
    root = credential_checkout
    claims, files = parse_memory(root / "AGENTS.md")
    assert claims == [] and files == []
    report = audit(root / "AGENTS.md", root)
    assert TOKEN not in to_markdown(report)
    assert any(s.path == "AGENTS.md" and "memory skipped: hidden" in s.reason for s in report.skipped_evidence)


@requires_git
def test_library_never_quotes_checkout_credentials_as_evidence(credential_checkout):
    report = audit(credential_checkout / "MEMORY.md", credential_checkout)
    assert TOKEN not in to_markdown(report)
    assert any(s.path == "notes/harmless.md" and "hidden" in s.reason for s in report.skipped_evidence)


@requires_git
@pytest.mark.parametrize("memory", ["AGENTS.md", "MEMORY.md"])
def test_action_never_publishes_checkout_credentials(credential_checkout, tmp_path, monkeypatch, capsys, memory):
    monkeypatch.chdir(credential_checkout)
    runner = tmp_path / "runner"
    runner.mkdir()
    summary = runner / "summary.md"
    env = {"RUNNER_TEMP": str(runner), "GITHUB_STEP_SUMMARY": str(summary),
           "MEMFALSIFY_MEMORY": memory, "MEMFALSIFY_EVIDENCE": ".", "MEMFALSIFY_STRICT": "false"}
    assert action.main(env) == 0
    assert TOKEN not in summary.read_text(encoding="utf-8")
    assert TOKEN not in (runner / "memfalsify/report.md").read_text(encoding="utf-8")
    output = capsys.readouterr().out
    assert TOKEN not in output
    if memory == "AGENTS.md":
        assert "::warning title=MemFalsify::" in output and "hidden" in output


def test_jsonl_append_refuses_symlink(tmp_path, make_symlink):
    target = tmp_path / "secret.txt"
    target.write_bytes(b"ORIGINAL\n")
    path = tmp_path / "events.jsonl"
    make_symlink(path, target)
    with pytest.raises((OSError, ValueError)):
        append_jsonl(path, {"id": "injected"})
    assert target.read_bytes() == b"ORIGINAL\n"


def test_sync_lock_survives_deleted_marker(tmp_path):
    with lock(tmp_path):
        marker = tmp_path / ".github-sync.lock"
        try:
            marker.unlink()
        except PermissionError:
            # Windows holds the open marker against deletion.
            pass
        with pytest.raises(ValueError, match="another sync is running"):
            with lock(tmp_path):
                pytest.fail("second sync entered the critical section")
    assert not marker.exists()


@pytest.mark.skipif(os.name != "posix", reason="POSIX permits deletion of open lock markers")
def test_deleted_sync_marker_still_excludes_another_process(tmp_path):
    code = """import sys
from pathlib import Path
from orgmemory.github_apply import lock
try:
    with lock(Path(sys.argv[1])):
        sys.exit(1)
except ValueError as exc:
    assert 'another sync is running' in str(exc)
"""
    env = {**os.environ, "PYTHONPATH": os.pathsep.join(str(ROOT / "packages" / package / "src")
                                                     for package in ("memcore", "orgmemory"))}
    with lock(tmp_path):
        (tmp_path / ".github-sync.lock").unlink()
        result = subprocess.run([sys.executable, "-c", code, str(tmp_path)], env=env,
                                capture_output=True, timeout=10)
        assert result.returncode == 0, result.stderr.decode()
    # The descriptor lock is released even when the marker was removed.
    with lock(tmp_path):
        assert (tmp_path / ".github-sync.lock").exists()


@pytest.mark.skipif(os.name != "posix", reason="POSIX permits replacement of open lock markers")
def test_sync_cleanup_preserves_replacement_marker(tmp_path):
    marker = tmp_path / ".github-sync.lock"
    with lock(tmp_path):
        marker.unlink()
        marker.write_bytes(b"replacement")
        with pytest.raises(ValueError, match="another sync is running"):
            with lock(tmp_path):
                pytest.fail("replacement admitted a second writer")
    assert marker.read_bytes() == b"replacement"


BLOCKED = [".git/config", ".secret.md", "node_modules/secret.md", "__pycache__/secret.md", "venv/secret.md"]


@pytest.mark.parametrize("target", BLOCKED)
def test_memory_links_and_evidence_share_target_policy(tmp_path, make_symlink, target):
    secret = tmp_path / target
    secret.parent.mkdir(parents=True, exist_ok=True)
    secret.write_text(f"Secret credentials contain {TOKEN}.\n", encoding="utf-8")
    make_symlink(tmp_path / "alias.md", target)
    memory = tmp_path / "MEMORY.md"
    memory.write_text(f"- [direct]({target})\n- [alias](alias.md)\n", encoding="utf-8")
    skipped = []
    claims, files = parse_memory(memory, skipped=skipped)
    assert claims == [] and files == [memory]
    reason = "hidden" if target.startswith(".") else "skipped directory"
    # .git/config is not a Markdown link, but its innocent alias is.
    assert any(s.path == "alias.md" and reason in s.reason for s in skipped)
    if target.endswith(".md"):
        assert any(s.path == target and reason in s.reason for s in skipped)
    passages, evidence_skipped = scan(tmp_path, memory_files=files)
    assert passages == []
    assert any(s.path == "alias.md" and reason in s.reason for s in evidence_skipped)
    claims, files = parse_memory(tmp_path / "alias.md")
    assert claims == [] and files == []


@pytest.mark.parametrize("directory", [".hidden", "node_modules", "__pycache__", "venv"])
def test_walked_path_cannot_hide_behind_safe_target(tmp_path, make_symlink, directory):
    (tmp_path / directory).mkdir()
    (tmp_path / "safe.md").write_text("Safe content is readable.\n", encoding="utf-8")
    alias = tmp_path / directory / "alias.md"
    make_symlink(alias, "../safe.md")
    memory = tmp_path / "MEMORY.md"
    memory.write_text(f"- [hidden alias]({directory}/alias.md)\n", encoding="utf-8")
    report = audit(memory, tmp_path)
    assert report.findings == []
    assert any(s.path == f"{directory}/alias.md" and "walked path" in s.reason for s in report.skipped_evidence)
    assert any(s.path == directory and "walked path" in s.reason for s in report.skipped_evidence)
    with pytest.raises(InputPathError, match="walked path"):
        input_path(tmp_path, alias)


def test_hidden_memory_filename_is_skipped(tmp_path):
    path = tmp_path / ".MEMORY.md"
    path.write_text(f"Credentials contain {TOKEN}.\n", encoding="utf-8")
    report = audit(path)
    assert report.files_read == [] and report.findings == []
    assert report.skipped_evidence[0].path == ".MEMORY.md"
    assert "hidden component in walked path" in report.skipped_evidence[0].reason
    assert TOKEN not in to_markdown(report)


@pytest.mark.parametrize("kind", ["outside", "hidden", "skipped", "safe"])
def test_action_checks_workspace_memory_targets(tmp_path, monkeypatch, capsys, make_symlink, kind):
    workspace, runner = tmp_path / "workspace", tmp_path / "runner"
    workspace.mkdir()
    runner.mkdir()
    target = {"outside": tmp_path / "outside.md", "hidden": workspace / ".private/secret.md",
              "skipped": workspace / "venv/secret.md", "safe": workspace / "notes/safe.md"}[kind]
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(f"Credentials contain {TOKEN}.\n", encoding="utf-8")
    make_symlink(workspace / "AGENTS.md", target)
    monkeypatch.chdir(workspace)
    env = {"GITHUB_WORKSPACE": str(workspace), "RUNNER_TEMP": str(runner), "MEMFALSIFY_MEMORY": "AGENTS.md",
           "GITHUB_STEP_SUMMARY": str(runner / "summary.md"), "MEMFALSIFY_STRICT": "false"}
    assert action.main(env) == 0
    text = (runner / "summary.md").read_text(encoding="utf-8")
    log = capsys.readouterr().out
    if kind == "safe":
        assert TOKEN in text and "skipping memory file" not in log
    else:
        assert TOKEN not in text and TOKEN not in log
        assert "::warning title=MemFalsify::AGENTS.md:" in log and "skipping memory file" in log


def test_review_evidence_cannot_bypass_target_policy(tmp_path, make_symlink):
    (tmp_path / ".private").mkdir()
    secret = tmp_path / ".private/secret.md"
    quote = f"Credentials contain {TOKEN}."
    secret.write_text(quote, encoding="utf-8")
    make_symlink(tmp_path / "alias.md", secret)
    memory = tmp_path / "MEMORY.md"
    memory.write_text("Credentials contain a token.\n", encoding="utf-8")
    claims, _ = parse_memory(memory)
    reviews = tmp_path / "reviews.jsonl"
    reviews.write_text(json.dumps({"memory_id": claims[0].id, "relation": "supports", "path": "alias.md",
                                   "quote": quote, "reviewer": "reviewer"}) + "\n", encoding="utf-8")
    report = audit(memory, tmp_path, reviews)
    assert report.findings[0].evidence == []
    assert any(s.path == "alias.md" and "review evidence skipped: hidden" in s.reason
               for s in report.skipped_evidence)
    assert TOKEN not in to_markdown(report)


@requires_git
def test_diff_worktree_link_cannot_quote_checkout_credentials(git_repo, make_symlink):
    root = git_repo.root
    base = git_repo.commit("base", {"MEMORY.md": "- [details](alias.md)\n"})
    with (root / ".git/config").open("a", encoding="utf-8") as handle:
        handle.write(f"\n[http]\n\textraheader = AUTHORIZATION: basic {TOKEN}\n")
    make_symlink(root / "alias.md", ".git/config")
    report = memory_diff(root / "MEMORY.md", base, evidence_root=root)
    assert report.changes == [] and TOKEN not in report.model_dump_json()
    assert any(s.path == "alias.md" and "memory skipped: hidden" in s.reason
               for s in report.audit.skipped_evidence)


@requires_confinement
@pytest.mark.parametrize("component", ["file", "directory", "fifo"])
def test_jsonl_append_rejects_swaps_at_open(tmp_path, monkeypatch, make_symlink, component):
    parent = tmp_path / "logs"
    parent.mkdir()
    path = parent / "events.jsonl"
    path.write_bytes(b"ORIGINAL\n")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "events.jsonl").write_bytes(b"OUTSIDE\n")
    original_open = os.open
    changed = []

    def swap(name, flags, *args, **kwargs):
        trigger = "logs" if component == "directory" else "events.jsonl"
        if name == trigger and not changed:
            changed.append(True)
            if component == "directory":
                parent.rename(tmp_path / "previous")
                make_symlink(parent, outside, directory=True)
            else:
                path.unlink()
                if component == "fifo":
                    os.mkfifo(path)
                else:
                    make_symlink(path, outside / "events.jsonl")
        return original_open(name, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", swap)
    with pytest.raises((OSError, ValueError)):
        append_jsonl(path, {"id": "injected"})
    assert changed and (outside / "events.jsonl").read_bytes() == b"OUTSIDE\n"


def test_jsonl_append_rejects_existing_parent_symlink(tmp_path, make_symlink):
    outside = tmp_path / "outside"
    outside.mkdir()
    target = outside / "events.jsonl"
    target.write_bytes(b"ORIGINAL\n")
    make_symlink(tmp_path / "alias", outside, directory=True)
    with pytest.raises((OSError, ValueError)):
        append_jsonl(tmp_path / "alias/events.jsonl", {"id": "injected"})
    assert target.read_bytes() == b"ORIGINAL\n"


@requires_git
@pytest.mark.parametrize("target", [".private/secret.md", "node_modules/secret.md"])
def test_git_snapshots_skip_hidden_and_ignored_linked_memories(git_repo, target):
    secret = f"Credentials contain {TOKEN}.\n"
    base = git_repo.commit("base", {"MEMORY.md": "- Safe content is readable.\n"})
    # Force-add ignored directories: the git reader must still apply the input policy.
    git_repo.write({target: secret, "MEMORY.md": f"- Safe content is readable.\n- [details]({target})\n"})
    git_repo.git("add", "--force", "--", target)
    head = git_repo.commit("linked memory")
    report = memory_diff(git_repo.root / "MEMORY.md", base, head, evidence_root=git_repo.root)
    assert TOKEN not in report.model_dump_json()
    assert any(s.path == target and "memory skipped:" in s.reason for s in report.audit.skipped_evidence)


@requires_git
def test_action_diff_cannot_publish_memory_symlink_target(git_repo, tmp_path, monkeypatch, capsys, make_symlink):
    base = git_repo.commit("base", {"AGENTS.md": "- Safe content is readable.\n"})
    (git_repo.root / "AGENTS.md").unlink()
    make_symlink(git_repo.root / "AGENTS.md", ".git/config")
    with (git_repo.root / ".git/config").open("a", encoding="utf-8") as handle:
        handle.write(f"\n[http]\n\textraheader = AUTHORIZATION: basic {TOKEN}\n")
    runner = tmp_path / "runner"
    runner.mkdir()
    monkeypatch.chdir(git_repo.root)
    env = {"MEMFALSIFY_MEMORY": "AGENTS.md", "MEMFALSIFY_DIFF_BASE": base, "RUNNER_TEMP": str(runner),
           "GITHUB_STEP_SUMMARY": str(runner / "summary.md")}
    assert action.main(env) == 0
    assert TOKEN not in (runner / "summary.md").read_text(encoding="utf-8")
    assert "::warning title=MemFalsify::AGENTS.md:" in capsys.readouterr().out


@pytest.mark.parametrize("relative, allowed", [
    (".cursor/rules/api.mdc", True),
    (".cursor/rules/frontend/components.mdc", True),
    ("packages/web/.cursor/rules/vue.mdc", True),
    (".github/copilot-instructions.md", True),
    (".github/instructions/python.instructions.md", True),
    (".claude/rules/api.md", True),
    (".claude/CLAUDE.md", True),
    (".windsurf/rules/testing.md", True),
    (".cursorrules", True),
    (".git/config", False),
    (".cursor/rules/../../.git/config", False),
    (".cursor/rules/.hidden/x.mdc", False),
    (".cursor/rules/node_modules/x.mdc", False),
    (".cursor/settings.mdc", False),
    (".github/workflows/ci.yml", False),
    (".github/copilot-instructions.txt", False),
    (".windsurf/rules/nested/x.md", False),
    (".env", False),
    ("node_modules/.cursor/rules/x.mdc", False),
])
def test_only_documented_instruction_files_pass_the_hidden_policy(tmp_path, relative, allowed):
    target = tmp_path / relative
    try:
        input_path(tmp_path, target, instructions=True)
    except InputPathError:
        accepted = False
    else:
        accepted = True
    assert accepted is allowed
    with pytest.raises(InputPathError):  # evidence inputs never pass instructions=True
        input_path(tmp_path, target)


@requires_git
def test_a_rule_file_symlinked_to_checkout_credentials_is_never_read(credential_checkout, tmp_path, monkeypatch,
                                                                      capsys, make_symlink):
    root = credential_checkout
    (root / ".cursor" / "rules").mkdir(parents=True)
    make_symlink(root / ".cursor/rules/evil.mdc", "../../.git/config")
    claims, files = parse_memory(root / ".cursor/rules/evil.mdc")
    assert claims == [] and files == []
    monkeypatch.chdir(root)
    runner = tmp_path / "runner"
    runner.mkdir()
    summary = runner / "summary.md"
    env = {"RUNNER_TEMP": str(runner), "GITHUB_STEP_SUMMARY": str(summary),
           "MEMFALSIFY_MEMORY": "auto", "MEMFALSIFY_EVIDENCE": ".", "MEMFALSIFY_STRICT": "false"}
    action.main(env)
    output = capsys.readouterr().out
    assert TOKEN not in summary.read_text(encoding="utf-8") and TOKEN not in output
    assert TOKEN not in (runner / "memfalsify/report.md").read_text(encoding="utf-8")
    assert ".cursor/rules/evil.mdc" in output and "hidden component in resolved path" in output
