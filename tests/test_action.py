import importlib.util
import shutil
from pathlib import Path

import pytest

from conftest import EXAMPLES

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("memfalsify_action", ROOT / "action" / "memfalsify_action.py")
action = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(action)


@pytest.fixture
def runner_env(tmp_path):
    (tmp_path / "runner").mkdir()
    env = {"RUNNER_TEMP": str(tmp_path / "runner"), "GITHUB_STEP_SUMMARY": str(tmp_path / "runner" / "summary.md"),
           "GITHUB_OUTPUT": str(tmp_path / "runner" / "output")}
    for name in ("GITHUB_STEP_SUMMARY", "GITHUB_OUTPUT"):
        Path(env[name]).write_text("", encoding="utf-8")
    return env


def outputs(env):
    return dict(line.split("=", 1) for line in Path(env["GITHUB_OUTPUT"]).read_text().splitlines())


@pytest.mark.parametrize("strict, code, level", [("true", 1, "::error"), ("false", 0, "::warning")])
def test_action_publishes_report_and_applies_strict(tmp_path, monkeypatch, capsys, runner_env, strict, code, level):
    monkeypatch.chdir(shutil.copytree(EXAMPLES / "memfalsify", tmp_path / "demo"))
    env = {**runner_env, "MEMFALSIFY_MEMORY": "MEMORY.md", "MEMFALSIFY_EVIDENCE": "evidence",
           "MEMFALSIFY_AS_OF": "2026-10-10", "MEMFALSIFY_STRICT": strict}
    assert action.main(env) == code
    out = outputs(env)
    assert out["attention"] == "4"
    summary = Path(env["GITHUB_STEP_SUMMARY"]).read_text(encoding="utf-8")
    assert summary == Path(out["report"]).read_text(encoding="utf-8") and "POSSIBLE COUNTEREVIDENCE | 4" in summary
    assert f"{level} title=MemFalsify::4 memories need attention" in capsys.readouterr().out


def test_action_without_memory_files_notices_and_succeeds(tmp_path, monkeypatch, capsys, runner_env):
    monkeypatch.chdir(tmp_path)
    assert action.main(runner_env) == 0
    assert outputs(runner_env)["attention"] == "0"
    assert "::notice title=MemFalsify::No memory file found (looked for CLAUDE.md, AGENTS.md)" in capsys.readouterr().out


def test_action_handles_spaces_and_excludes(tmp_path, monkeypatch, runner_env):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "my evidence" / "old stuff").mkdir(parents=True)
    (tmp_path / "CLAUDE.md").write_text("- The API rate limit is 100 requests per minute.\n", encoding="utf-8")
    (tmp_path / "agent notes.md").write_text("- The database runs PostgreSQL 15.\n", encoding="utf-8")
    (tmp_path / "my evidence" / "old stuff" / "api.md").write_text("The API rate limit is 60 requests per minute.\n",
                                                                     encoding="utf-8")
    (tmp_path / "my evidence" / "db.md").write_text("The database runs PostgreSQL 16.\n", encoding="utf-8")
    env = {**runner_env, "MEMFALSIFY_MEMORY": 'CLAUDE.md "agent notes.md"', "MEMFALSIFY_EVIDENCE": "my evidence",
           "MEMFALSIFY_EXCLUDE": "old stuff/**\n\n"}
    assert action.main(env) == 1
    assert outputs(env)["attention"] == "1"
    summary = Path(env["GITHUB_STEP_SUMMARY"]).read_text(encoding="utf-8")
    assert "Memory: `agent notes.md`" in summary and "old stuff/api.md" not in summary


@pytest.mark.parametrize("override", [{"MEMFALSIFY_EVIDENCE": "missing-dir"}, {"MEMFALSIFY_AS_OF": "yesterday"},
                                      {"MEMFALSIFY_STRICT": "ture"}, {"MEMFALSIFY_MEMORY": '"MEMORY.md'}])
def test_action_errors_exit_2(tmp_path, monkeypatch, runner_env, override):
    monkeypatch.chdir(shutil.copytree(EXAMPLES / "memfalsify", tmp_path / "demo"))
    env = {**runner_env, "MEMFALSIFY_MEMORY": "MEMORY.md", "MEMFALSIFY_EVIDENCE": "evidence", **override}
    assert action.main(env) == 2


@pytest.mark.parametrize("path", ["action.yml", ".github/workflows/ci.yml"])
def test_no_expressions_inside_run_scripts(path):
    """Inputs reach scripts through env: only; ${{ }} inside run: would allow script injection."""
    in_run, indent = False, 0
    for line in (ROOT / path).read_text(encoding="utf-8").splitlines():
        stripped = line.lstrip()
        if in_run and stripped and len(line) - len(stripped) <= indent:
            in_run = False
        if stripped.startswith(("run:", "- run:")):
            in_run, indent = True, len(line) - len(stripped)
        assert not (in_run and "${{" in line), line
