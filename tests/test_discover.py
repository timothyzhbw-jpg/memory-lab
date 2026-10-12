"""memfalsify discover: the instruction files of every supported agent in a directory (names only)."""
import json
import os
from pathlib import Path

import pytest
from typer.testing import CliRunner

from memfalsify.cli import app
from memfalsify.corpus import Skipped
from memfalsify.discover import Discovery, InstructionFile, discover, discovery_to_markdown

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "formats"
runner = CliRunner()
needs_symlinks = pytest.mark.skipif(os.name == "nt", reason="symlinks need extra privileges on Windows")


FIXTURE_FILES = [
    (".claude/rules/api.md", "claude-rule"),
    (".cursor/rules/api.mdc", "cursor-rule"),
    (".cursor/rules/frontend/components.mdc", "cursor-rule"),
    (".cursorrules", "cursorrules"),
    (".devin/rules/deploy.md", "windsurf-rule"),
    (".github/copilot-instructions.md", "copilot-instructions"),
    (".github/instructions/python.instructions.md", "copilot-path-instructions"),
    (".windsurf/rules/testing.md", "windsurf-rule"),
    (".windsurfrules", "windsurfrules"),
    ("AGENTS.md", "agents-md"),
    ("CLAUDE.md", "claude-md"),
    ("GEMINI.md", "gemini-md"),
    ("packages/web/.cursor/rules/vue.mdc", "cursor-rule"),
    ("packages/web/AGENTS.md", "agents-md"),
]


def test_discover_finds_every_fixture_format():
    found = discover(FIXTURE)
    assert [(f.path, f.format) for f in found.files] == FIXTURE_FILES
    assert found.distinct == [path for path, _ in FIXTURE_FILES] and found.skipped == []
    assert {f.agent for f in found.files} == {"Claude Code", "Cursor", "Cursor (legacy)", "Windsurf",
                                              "Windsurf (legacy)", "GitHub Copilot", "Gemini CLI",
                                              "Codex and other AGENTS.md readers"}


def test_discover_excludes_like_evidence_exclusions():
    found = discover(FIXTURE, exclude=["packages/**", ".cursor", "*.md"])
    assert [f.path for f in found.files] == [".cursorrules", ".windsurfrules"]
    assert found.excluded == ["packages/**", ".cursor", "*.md"]


def test_discover_searches_only_documented_places(tmp_path):
    for name in ["CLAUDE.md", "sub/CLAUDE.local.md", "sub/.claude/CLAUDE.md", "sub/.claude/AGENTS.md",
                 "sub/AGENTS.override.md", "sub/.windsurf/rules/a.md", "sub/.devin/rules/b.md",
                 "sub/.claude/rules/deep/c.md",
                 # Not instruction files, or not searched there:
                 "MEMORY.md", "notes.md", "sub/.cursorrules", "sub/.github/copilot-instructions.md",
                 "sub/.windsurf/rules/deep/d.md", "sub/.cursor/rules/e.md", ".github/instructions/f.md",
                 ".hidden/CLAUDE.md", "node_modules/pkg/AGENTS.md", "venv/AGENTS.md", "__pycache__/AGENTS.md",
                 ".claude/worktrees/w/CLAUDE.md", "vendor/lib/.git/HEAD", "vendor/lib/AGENTS.md"]:
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_bytes(b"- The cache uses Redis.\n")
    found = discover(tmp_path)
    assert found.distinct == ["CLAUDE.md", "sub/.claude/AGENTS.md", "sub/.claude/CLAUDE.md",
                              "sub/.claude/rules/deep/c.md", "sub/.devin/rules/b.md", "sub/.windsurf/rules/a.md",
                              "sub/AGENTS.override.md", "sub/CLAUDE.local.md"]
    assert [(s.path, s.reason) for s in found.skipped] == [
        ("vendor/lib", "nested git repository (submodule or worktree) not searched")]


@needs_symlinks
def test_discover_lists_each_file_once_and_never_leaves_the_directory(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    (root / "AGENTS.md").write_bytes(b"- The cache uses Redis.\n")
    (root / "CLAUDE.md").symlink_to("AGENTS.md")
    (tmp_path / "secret.md").write_bytes(b"- Outside the repository.\n")
    (root / "GEMINI.md").symlink_to(tmp_path / "secret.md")
    (root / "pkg").mkdir()
    (root / "pkg" / "AGENTS.md").symlink_to("missing.md")
    (root / "linked").symlink_to(tmp_path, target_is_directory=True)
    found = discover(root)
    assert [(f.path, f.same_as) for f in found.files] == [("AGENTS.md", None), ("CLAUDE.md", "AGENTS.md")]
    assert found.distinct == ["AGENTS.md"]
    assert {(s.path, s.reason) for s in found.skipped} == {
        ("GEMINI.md", "symlink leads outside the searched directory"),
        ("pkg/AGENTS.md", "broken or cyclic symlink"),
        ("linked", "symlinked directory not followed")}
    # The real file is the one to audit, whichever name sorts first.
    (root / "AGENTS.md").unlink()
    (root / "CLAUDE.md").unlink()
    (root / "CLAUDE.md").write_bytes(b"- The cache uses Redis.\n")
    (root / "AGENTS.md").symlink_to("CLAUDE.md")
    assert [(f.path, f.same_as) for f in discover(root).files] == [("AGENTS.md", "CLAUDE.md"), ("CLAUDE.md", None)]


@pytest.mark.skipif(os.name == "nt", reason="Windows file names cannot hold control characters")
def test_discover_skips_names_with_control_characters(tmp_path):
    (tmp_path / "a\x1bb").mkdir()
    (tmp_path / "a\x1bb" / "AGENTS.md").write_bytes(b"- The cache uses Redis.\n")
    found = discover(tmp_path)
    assert found.files == [] and [s.reason for s in found.skipped] == ["file name contains control characters"]
    assert "\x1b" not in discovery_to_markdown(found)


def test_discovery_markdown_keeps_names_and_patterns_inert(tmp_path):
    rules = tmp_path / ".cursor" / "rules"
    rules.mkdir(parents=True)
    (rules / "a`b.mdc").write_bytes(b"- The cache uses Redis.\n")
    nested = tmp_path / "sub`tick"
    nested.mkdir()
    (nested / "AGENTS.md").write_bytes(b"- The cache uses Redis.\n")
    pattern = "*\n- [phish](http://evil)"
    markdown = discovery_to_markdown(discover(tmp_path, exclude=[pattern, "ok"]))
    # A backtick in the name needs a longer fence, or the span closes and the rest of the name is Markdown.
    assert "| ``.cursor/rules/a`b.mdc`` | Cursor | `cursor-rule` |" in markdown
    assert "| ``sub`tick/AGENTS.md`` | Codex and other AGENTS.md readers | `agents-md` |" in markdown
    assert "- Excluded: `*\\u000a- [phish](http://evil)`, `ok`" in markdown
    assert "\n- [phish](http://evil)" not in markdown and "\x1b" not in markdown
    # A name the OS may reject still has to render as one cell: pipes stay escaped inside the fence.
    synthetic = Discovery(
        directory="we\nird",
        excluded=[pattern],
        files=[InstructionFile(path="a`|b.mdc", format="cursor-rule", agent="Cursor", same_as="sub`|x.md")],
        skipped=[Skipped(path="bad`name", reason="symlinked directory not followed")],
        limitations=["limit"],
    )
    text = discovery_to_markdown(synthetic)
    assert "| ``a`\\|b.mdc`` | Cursor | `cursor-rule` (same file as ``sub`\\|x.md``) |" in text
    assert "- Directory: `we\\u000aird`" in text and "\nird" not in text
    assert "- Excluded: `*\\u000a- [phish](http://evil)`" in text
    assert "- ``bad`name``: symlinked directory not followed" in text
    assert "\n- [phish](http://evil)" not in text


def test_zero_file_index_does_not_collapse_files(tmp_path, monkeypatch):
    import memfalsify.discover as discover_module

    (tmp_path / "AGENTS.md").write_bytes(b"- The cache uses Redis.\n")
    (tmp_path / "CLAUDE.md").write_bytes(b"- Handlers validate request bodies.\n")
    real = os.stat

    def zero_index(path, *args, **kwargs):
        status = real(path, *args, **kwargs)
        fields = list(status)
        fields[1] = 0  # st_ino
        fields[2] = 0  # st_dev
        return os.stat_result(tuple(fields))

    monkeypatch.setattr(discover_module.os, "stat", zero_index)
    found = discover(tmp_path)
    assert found.distinct == ["AGENTS.md", "CLAUDE.md"]
    assert [f.same_as for f in found.files] == [None, None]


def test_discover_cli(tmp_path):
    result = runner.invoke(app, ["discover", str(FIXTURE)])
    assert result.exit_code == 0
    assert result.output.startswith("# MemFalsify instruction files\n")
    assert "| `.cursor/rules/api.mdc` | Cursor | `cursor-rule` |" in result.output
    assert "14 instruction file(s)." in result.output
    result = runner.invoke(app, ["discover", str(FIXTURE), "--format", "json", "--exclude", "packages/**"])
    data = json.loads(result.output)
    assert data["mode"] == "read_only" and data["excluded"] == ["packages/**"]
    assert [f["path"] for f in data["files"]] == [path for path, _ in FIXTURE_FILES[:-2]]
    assert runner.invoke(app, ["discover", str(tmp_path)]).output.count("No instruction file found.") == 1
    assert runner.invoke(app, ["discover", str(FIXTURE / "AGENTS.md")]).exit_code == 2
    assert runner.invoke(app, ["discover", str(tmp_path / "missing")]).exit_code == 2


def test_discover_fails_closed_on_unreadable_directories(tmp_path, monkeypatch):
    import memfalsify.discover as module

    def walk(path, onerror=None):
        onerror(PermissionError(13, "Permission denied", str(path)))
        return iter(())
    monkeypatch.setattr(module, "walk_files", walk)
    with pytest.raises(PermissionError):
        discover(tmp_path)
    assert runner.invoke(app, ["discover", str(tmp_path)]).exit_code == 2
