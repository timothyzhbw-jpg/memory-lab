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
    assert out["attention"] == "1"  # Action audits the named file, not linked infra.md.
    summary = Path(env["GITHUB_STEP_SUMMARY"]).read_text(encoding="utf-8")
    assert summary == Path(out["report"]).read_text(encoding="utf-8") and "POSSIBLE COUNTEREVIDENCE | 1" in summary
    assert "Memory: `MEMORY.md`" in summary and "infra.md:" not in summary
    assert f"{level} title=MemFalsify::1 memory needs attention" in capsys.readouterr().out


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


@pytest.mark.parametrize("strict", ["true", "false"])
@pytest.mark.parametrize("exclude", ["*", "**"])
def test_action_refuses_all_excluded_evidence(tmp_path, monkeypatch, runner_env, strict, exclude):
    monkeypatch.chdir(shutil.copytree(EXAMPLES / "memfalsify", tmp_path / "demo"))
    env = {**runner_env, "MEMFALSIFY_MEMORY": "MEMORY.md", "MEMFALSIFY_EVIDENCE": "evidence",
           "MEMFALSIFY_STRICT": strict, "MEMFALSIFY_EXCLUDE": exclude}
    assert action.main(env) == 2
    assert "Audit failed" in Path(outputs(env)["report"]).read_text()


def test_action_link_cannot_remove_counterevidence(tmp_path, monkeypatch, runner_env):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "api.md").write_text("The public API rate limit is 60 requests per minute.\n")
    memory = tmp_path / "AGENTS.md"
    memory.write_text("- The public API rate limit is 100 requests per minute.\n")
    env = {**runner_env, "MEMFALSIFY_MEMORY": "AGENTS.md"}
    for suffix in ("", "- [API](docs/api.md)\n"):
        memory.write_text("- The public API rate limit is 100 requests per minute.\n" + suffix)
        assert action.main(env) == 1
        assert outputs(runner_env)["attention"] == "1"
        report = Path(outputs(runner_env)["report"]).read_text()
        assert "docs/api.md" in report and "POSSIBLE COUNTEREVIDENCE" in report


def test_action_named_instruction_files_remain_evidence_for_each_other(tmp_path, monkeypatch, runner_env):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "CLAUDE.md").write_text("- The database runs PostgreSQL 15.\n- [Agents](AGENTS.md)\n")
    (tmp_path / "AGENTS.md").write_text("- The database runs PostgreSQL 16.\n- [Claude](CLAUDE.md)\n")
    assert action.main(runner_env) == 1
    assert outputs(runner_env)["attention"] == "2"
    report = Path(outputs(runner_env)["report"]).read_text()
    assert "CLAUDE.md:1" in report and "AGENTS.md:1" in report


@pytest.mark.parametrize("exists", [False, True])
def test_memory_progress_cannot_inject_workflow_commands(tmp_path, monkeypatch, runner_env, capsys, exists):
    monkeypatch.chdir(tmp_path)
    name = "notes\n::error::forged%\rcommand.md"
    if exists:
        (tmp_path / name).write_text("- The cache uses Redis.\n")
        (tmp_path / "evidence.txt").write_text("The cache uses Redis.\n")
    assert action.main({**runner_env, "MEMFALSIFY_MEMORY": '"' + name + '"'}) == 0
    output = capsys.readouterr().out
    assert "notes%0A%3A%3Aerror%3A%3Aforged%25%0Dcommand.md" in output
    assert all("::" not in line for line in output.splitlines() if line.startswith("MemFalsify: "))
    assert not any(line.startswith("::error::forged") for line in output.splitlines())


@pytest.mark.parametrize("mode", ["success", "exit", "exception"])
def test_cli_output_cannot_inject_workflow_commands(monkeypatch, capsys, mode):
    def cli(*args, **kwargs):
        print("::warning::forged\n::stop-commands::token\r::add-mask::value")
        if mode == "exit":
            raise SystemExit(2)
        if mode == "exception":
            raise RuntimeError("failure\n::error::forged")

    monkeypatch.setattr(action, "app", cli)
    assert action.run_cli([]) == (0 if mode == "success" else 2)
    lines = capsys.readouterr().out.splitlines()
    assert lines and all(line.startswith("MemFalsify CLI: ") for line in lines)
    assert all("::" not in line for line in lines)
    assert any("%3A%3Astop-commands%3A%3Atoken" in line for line in lines)


def test_action_external_cwd_and_option_shaped_filename(tmp_path, monkeypatch, runner_env):
    # The runner imports the Action from ROOT; the audited checkout is independent.
    consumer = tmp_path / "consumer"
    consumer.mkdir()
    monkeypatch.chdir(consumer)
    (consumer / "--strict").write_text("- The database runs PostgreSQL 15.\n")
    (consumer / "db.txt").write_text("The database runs PostgreSQL 16.\n")
    assert action.main({**runner_env, "MEMFALSIFY_MEMORY": "--strict"}) == 1
    assert outputs(runner_env)["attention"] == "1"
    report = Path(outputs(runner_env)["report"]).read_text()
    assert "db.txt" in report and "--strict" in report
    assert sorted(p.name for p in consumer.iterdir()) == ["--strict", "db.txt"]
