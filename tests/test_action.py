import importlib.util
import os
import re
import shutil
from pathlib import Path, PureWindowsPath

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


@pytest.mark.parametrize("diff", [False, True])
def test_action_report_paths_are_posix_on_windows(memfalsify_demo, monkeypatch, diff):
    from memfalsify import DiffReport
    from memfalsify.audit import audit

    report = audit(memfalsify_demo / "MEMORY.md", memfalsify_demo / "evidence")
    report.memory_file = r".cursor\rules\api.mdc"
    report.evidence_root = r"C:\checkout\docs"
    if diff:
        report = DiffReport(memory_file=report.memory_file, audit=report,
                            base={"rev": "base", "files": []}, head={"rev": "working tree", "files": []},
                            summary={}, changes=[], limitations=[])
    # PureWindowsPath exposes native Windows spelling without needing Windows I/O.
    monkeypatch.setattr(action, "Path", PureWindowsPath)
    action._posix_report_paths(report)
    assert report.memory_file == ".cursor/rules/api.mdc"
    nested = report.audit if diff else report
    assert nested.memory_file == ".cursor/rules/api.mdc"
    assert nested.evidence_root == "C:/checkout/docs"
    assert "\\\\" not in report.model_dump_json()


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
    assert Path(out["report"]).read_bytes() == summary.encode("utf-8")
    assert Path(env["GITHUB_STEP_SUMMARY"]).read_bytes() == summary.encode("utf-8")
    assert Path(env["GITHUB_OUTPUT"]).read_bytes() == f"report={out['report']}\nattention=1\n".encode("utf-8")
    assert "Memory: `MEMORY.md`" in summary and "infra.md:" not in summary
    assert f"{level} title=MemFalsify::1 memory needs attention" in capsys.readouterr().out


def test_action_failure_keeps_a_hostile_memory_name_inert(tmp_path, monkeypatch, runner_env):
    monkeypatch.chdir(tmp_path)
    name = "a`[x](evil).md"
    (tmp_path / name).write_text("- The cache uses Redis.\n", encoding="utf-8")
    env = {**runner_env, "MEMFALSIFY_MEMORY": name, "MEMFALSIFY_EVIDENCE": "missing-dir"}
    assert action.main(env) == 2
    summary = Path(env["GITHUB_STEP_SUMMARY"]).read_text(encoding="utf-8")
    assert "- Memory: ``a`[x](evil).md``" in summary
    assert "\n[x](evil)" not in summary


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


@pytest.mark.parametrize("exists", [False, pytest.param(
    True,
    marks=pytest.mark.skipif(
        os.name == "nt", reason="Windows forbids control characters and colons in filenames",
    ),
)])
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


requires_git = pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")


@requires_git
def test_action_diff_base_gates_only_added_or_changed_claims(git_repo, monkeypatch, runner_env, capsys):
    monkeypatch.chdir(git_repo.root)
    base = git_repo.commit("base", {"AGENTS.md": "- The database runs PostgreSQL 15.\n",
                                    "docs/db.md": "The database runs PostgreSQL 16.\n",
                                    "docs/api.md": "The public API rate limit is 60 requests per minute.\n"})
    env = {**runner_env, "MEMFALSIFY_MEMORY": "AGENTS.md", "MEMFALSIFY_DIFF_BASE": base}
    assert action.main(env) == 0  # the contradicted claim predates the base, so it is not gated
    assert outputs(env)["attention"] == "0"
    assert "nothing was audited" in Path(outputs(env)["report"]).read_text(encoding="utf-8")
    git_repo.write({"AGENTS.md": "- The database runs PostgreSQL 15.\n"
                                 "- The public API rate limit is 100 requests per minute.\n"})
    assert action.main(env) == 1
    assert outputs(env)["attention"] == "1"
    report = Path(outputs(env)["report"]).read_text(encoding="utf-8")
    assert report.startswith("# MemFalsify diff report") and "docs/api.md" in report and "docs/db.md" not in report
    assert "1 of 1 added or changed memories need attention" in capsys.readouterr().out
    assert action.main({**env, "MEMFALSIFY_STRICT": "false"}) == 0
    assert action.main({**env, "MEMFALSIFY_DIFF_BASE": ""}) == 1 and outputs(env)["attention"] == "2"


@requires_git
@pytest.mark.parametrize("rev", ["-x", "--output=pwned.txt", "no-such-rev", "HEAD\n--all", "HEAD~5"])
def test_action_diff_base_is_validated(git_repo, monkeypatch, runner_env, capsys, rev):
    monkeypatch.chdir(git_repo.root)
    git_repo.commit("base", {"AGENTS.md": "- The database runs PostgreSQL 15.\n"})
    assert action.main({**runner_env, "MEMFALSIFY_MEMORY": "AGENTS.md", "MEMFALSIFY_DIFF_BASE": rev}) == 2
    out = capsys.readouterr().out
    assert "::error title=MemFalsify::diff-base:" in out and "fetch-depth: 0" in out
    assert not (git_repo.root / "pwned.txt").exists()


@requires_git
def test_action_diff_base_outside_a_repository_fails(tmp_path, monkeypatch, runner_env):
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path))
    work = tmp_path / "work"
    work.mkdir()
    (work / "AGENTS.md").write_bytes(b"- The database runs PostgreSQL 15.\n")
    monkeypatch.chdir(work)
    assert action.main({**runner_env, "MEMFALSIFY_MEMORY": "AGENTS.md", "MEMFALSIFY_DIFF_BASE": "HEAD"}) == 2


def test_action_passes_diff_base_through_env():
    text = (ROOT / "action.yml").read_text(encoding="utf-8")
    assert "  diff-base:\n" in text and "MEMFALSIFY_DIFF_BASE: ${{ inputs.diff-base }}" in text


# --- memory: auto ------------------------------------------------------------------------------

FORMATS = ROOT / "tests" / "fixtures" / "formats"
needs_symlinks = pytest.mark.skipif(os.name == "nt", reason="symlinks need extra privileges on Windows")


def audited(report: str) -> list[str]:
    return re.findall(r"^- Memory: `([^`]+)`", report, re.M)


@pytest.mark.parametrize("strict, code", [("true", 1), ("false", 0)])
def test_action_memory_auto_audits_every_discovered_file(tmp_path, monkeypatch, runner_env, capsys, strict, code):
    monkeypatch.chdir(shutil.copytree(FORMATS, tmp_path / "repo"))
    env = {**runner_env, "MEMFALSIFY_MEMORY": " auto\n", "MEMFALSIFY_STRICT": strict,
           "MEMFALSIFY_EXCLUDE": "packages/**", "MEMFALSIFY_AS_OF": "2026-10-10"}
    assert action.main(env) == code
    out = capsys.readouterr().out
    report = Path(outputs(env)["report"]).read_text(encoding="utf-8")
    assert audited(report) == [".claude/rules/api.md", ".cursor/rules/api.mdc", ".cursor/rules/frontend/components.mdc",
                               ".cursorrules", ".devin/rules/deploy.md", ".github/copilot-instructions.md",
                               ".github/instructions/python.instructions.md", ".windsurf/rules/testing.md",
                               ".windsurfrules", "AGENTS.md", "CLAUDE.md", "GEMINI.md"]
    assert "MemFalsify: found .cursor/rules/api.mdc (Cursor)" in out and "packages/web" not in out + report
    # docs/api.md says 60 requests per minute; AGENTS.md and the Cursor rule say 100. The Copilot instructions
    # say PostgreSQL 16 and CLAUDE.md, evidence for the others, says 15. CLAUDE.md is not flagged in turn:
    # files under hidden directories (.github/) are never evidence.
    assert outputs(env)["attention"] == "3"
    assert "`CLAUDE.md:5` · sha256" in report and "`copilot-instructions.md:3` · sha256" not in report
    assert "Frontmatter: `description: Rate limits and validation for the fixture API`" in report


@needs_symlinks
def test_action_memory_auto_audits_a_linked_file_once(tmp_path, monkeypatch, runner_env, capsys):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "AGENTS.md").write_bytes(b"- The database runs PostgreSQL 15.\n")
    (tmp_path / "CLAUDE.md").symlink_to("AGENTS.md")
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "db.md").write_bytes(b"The database runs PostgreSQL 16.\n")
    assert action.main({**runner_env, "MEMFALSIFY_MEMORY": "auto"}) == 1
    assert outputs(runner_env)["attention"] == "1"
    assert audited(Path(outputs(runner_env)["report"]).read_text(encoding="utf-8")) == ["AGENTS.md"]
    assert "MemFalsify: found CLAUDE.md (Claude Code, same file as AGENTS.md)" in capsys.readouterr().out


def test_action_memory_auto_without_instruction_files_notices_and_succeeds(tmp_path, monkeypatch, runner_env,
                                                                           capsys):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "README.md").write_bytes(b"- The cache uses Redis.\n")
    assert action.main({**runner_env, "MEMFALSIFY_MEMORY": "auto"}) == 0
    assert outputs(runner_env)["attention"] == "0"
    assert ("::notice title=MemFalsify::No agent instruction file found in the workspace (memory: auto)"
            in capsys.readouterr().out)


def test_action_memory_auto_fails_closed_when_the_workspace_cannot_be_searched(tmp_path, monkeypatch, runner_env,
                                                                               capsys):
    monkeypatch.chdir(tmp_path)

    def refuse(*args, **kwargs):
        raise PermissionError(13, "Permission denied", "private-dir")
    monkeypatch.setattr(action, "discover", refuse)
    assert action.main({**runner_env, "MEMFALSIFY_MEMORY": "auto"}) == 2
    assert "::error title=MemFalsify::memory: auto could not search the workspace" in capsys.readouterr().out


@requires_git
def test_action_memory_auto_with_diff_base_gates_a_new_rule_file(git_repo, monkeypatch, runner_env):
    monkeypatch.chdir(git_repo.root)
    base = git_repo.commit("base", {"AGENTS.md": "- The database runs PostgreSQL 15.\n",
                                    "docs/api.md": "The public API rate limit is 60 requests per minute.\n"})
    git_repo.write({".cursor/rules/api.mdc": "---\nglobs: src/api/**\n---\n\n"
                                             "- The public API rate limit is 100 requests per minute.\n"})
    env = {**runner_env, "MEMFALSIFY_MEMORY": "auto", "MEMFALSIFY_DIFF_BASE": base}
    assert action.main(env) == 1
    assert outputs(env)["attention"] == "1"
    report = Path(outputs(env)["report"]).read_text(encoding="utf-8")
    assert audited(report) == [".cursor/rules/api.mdc", "AGENTS.md"]
    assert "| Added | 1 |" in report and "`api.mdc:5`" in report


# --- Supply chain: pinned actions, hash-locked dependencies ---------------------------------------------------

LOCK_DIR = ROOT / "action" / "requirements"
LOCK_SETS = {"lexical": {"pydantic", "typer", "setuptools"},
             "llm": {"pydantic", "typer", "setuptools", "anthropic"},
             "jev": {"pydantic", "typer", "setuptools", "typesafe-sdk"},
             "all": {"pydantic", "typer", "setuptools", "anthropic", "typesafe-sdk"}}
PINNED_USES = re.compile(r"^[\w.-]+/[\w./-]+@[0-9a-f]{40}$")
PINNED_LINE = re.compile(r"^\s*(?:- )?uses:\s+\S+@[0-9a-f]{40}  # v\d+\.\d+\.\d+$")
ENTRY = re.compile(r"^(?P<name>[A-Za-z0-9][\w.-]*)==(?P<version>[^\s;]+)(?: ; (?P<marker>.*?))? "
                   r"(?P<hashes>--hash=sha256:[0-9a-f]{64}(?: --hash=sha256:[0-9a-f]{64})*)$")


def uses_lines(path):
    return [line for line in (ROOT / path).read_text(encoding="utf-8").splitlines()
            if re.match(r"^\s*(?:- )?uses:", line) and not re.match(r"^\s*(?:- )?uses:\s+\./\s*$", line)]


def lock_entries(name):
    """Logical requirement lines of a lock file: continuations joined, comments and blank lines dropped."""
    text = re.sub(r"\s*\\\n\s*", " ", (LOCK_DIR / f"{name}.txt").read_text(encoding="utf-8"))
    return [line.strip() for line in text.splitlines() if line.strip() and not line.lstrip().startswith("#")]


def pins(name):
    """Package name (normalized) to the exact versions the lock file pins (one per environment marker)."""
    found = {}
    for entry in lock_entries(name):
        match = ENTRY.match(entry)
        assert match, f"{name}.txt: not an exact pin with sha256 hashes: {entry[:100]}"
        found.setdefault(normalized(match.group("name")), []).append(match.group("version"))
    return found


def normalized(name):
    return re.sub(r"[-_.]+", "-", name).lower()


def requirements_of(name):
    """Direct requirements of a .in file, following -r includes."""
    found = []
    for line in (LOCK_DIR / f"{name}.in").read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if line.startswith("-r "):
            found += requirements_of(line[3:].strip().removesuffix(".in"))
        elif line:
            found.append(line)
    return found


@pytest.mark.parametrize("path", ["action.yml", ".github/workflows/ci.yml"])
def test_third_party_actions_are_pinned_to_commit_shas(path):
    lines = uses_lines(path)
    assert lines
    for line in lines:
        assert PINNED_LINE.match(line), f"{path}: pin to a 40-character commit SHA with a '# vX.Y.Z' comment: {line}"


@pytest.mark.parametrize("path", ["docs/github-action.md", "README.md", "README.zh-CN.md",
                                  "skills/memfalsify/SKILL.md", "skills/orgmemory/SKILL.md"])
def test_documented_actions_are_pinned_too(path):
    refs = re.findall(r"uses:\s+(actions/\S+)", (ROOT / path).read_text(encoding="utf-8"))
    assert refs, path
    assert all(PINNED_USES.match(ref) for ref in refs), refs


def install_commands():
    text = re.sub(r"\s*\\\n\s*", " ", (ROOT / "action.yml").read_text(encoding="utf-8"))
    return [line.strip() for line in text.splitlines()
            if "-m pip install" in line and not line.lstrip().startswith("#")]


def test_action_installs_the_lock_file_with_require_hashes():
    locked = [c for c in install_commands() if "--require-hashes" in c]
    assert len(locked) == 1
    assert "--only-binary :all:" in locked[0]
    assert '-r "$GITHUB_ACTION_PATH/action/requirements/$lock.txt"' in locked[0]
    text = (ROOT / "action.yml").read_text(encoding="utf-8")
    assert 'lexical|llm|jev) lock="$MEMFALSIFY_VERIFIER" ;;' in text and 'dual|cascade) lock="all" ;;' in text
    for name in LOCK_SETS:
        assert (LOCK_DIR / f"{name}.txt").is_file()


def test_action_installs_its_own_packages_without_dependencies():
    own = [c for c in install_commands() if "$src/memcore" in c]
    assert len(own) == 1
    assert "--no-deps" in own[0] and "--no-build-isolation" in own[0] and '"$src/memfalsify"' in own[0]
    assert "[" not in own[0]  # no extras: pip would resolve them from PyPI
    # Every pip install is either the hash-checked lock file or the --no-deps install of the action's packages.
    assert len(install_commands()) == 2
    assert "$GITHUB_ACTION_PATH/packages/memcore" in (ROOT / "action.yml").read_text(encoding="utf-8")


@pytest.mark.parametrize("name", LOCK_SETS)
def test_lock_file_pins_every_requirement_with_hashes(name):
    found = pins(name)  # fails on any line that is not an exact pin followed by sha256 hashes
    assert set(found) >= LOCK_SETS[name]
    only_models = {"anthropic", "typesafe-sdk"} - LOCK_SETS[name]
    assert not only_models & set(found)  # a verifier installs only the SDKs it uses


@pytest.mark.parametrize("name", LOCK_SETS)
def test_lock_file_satisfies_its_input_requirements(name):
    from packaging.requirements import Requirement

    found = pins(name)
    for line in requirements_of(name):
        requirement = Requirement(line)
        versions = found[normalized(requirement.name)]
        assert all(requirement.specifier.contains(v) for v in versions), f"{name}: {line} vs the lock {versions}"


def toml_list(text, key):
    match = re.search(rf"^{key}\s*=\s*\[(.*?)\]\s*$", text, re.MULTILINE)
    return re.findall(r'"([^"]+)"', match.group(1)) if match else []


def test_lock_inputs_match_the_package_dependencies():
    core = (ROOT / "packages" / "memcore" / "pyproject.toml").read_text(encoding="utf-8")
    falsify = (ROOT / "packages" / "memfalsify" / "pyproject.toml").read_text(encoding="utf-8")
    runtime = {spec for spec in toml_list(core, "dependencies") + toml_list(falsify, "dependencies")
               if not spec.startswith("memlab-core")}
    build = set(toml_list(core, "requires")) | set(toml_list(falsify, "requires"))
    assert runtime and build
    assert set(requirements_of("lexical")) == runtime | build
    assert set(requirements_of("llm")) == set(requirements_of("lexical")) | set(toml_list(falsify, "llm"))
    assert set(requirements_of("jev")) == set(requirements_of("lexical")) | set(toml_list(falsify, "jev"))
    assert set(requirements_of("all")) == set(requirements_of("llm")) | set(requirements_of("jev"))


def test_dogfood_job_holds_nothing_for_a_pull_request_to_take():
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    code = "\n".join(line.split(" #", 1)[0] for line in workflow.splitlines() if not line.lstrip().startswith("#"))
    assert "uses: ./" in code  # the job the documentation talks about still exists
    assert "pull_request_target" not in code and "secrets" not in code
    assert not re.search(r"write|id-token", code)  # nothing beyond the read-only token
    assert re.search(r"^permissions:\n  contents: read\n", code, re.MULTILINE)
    dogfood = code.split("  dogfood:", 1)[1]
    assert "persist-credentials: false" in dogfood and "verifier" not in dogfood  # the offline lexical default
    assert re.search(r"^    permissions:\n      contents: read\n", dogfood, re.MULTILINE)


@pytest.mark.parametrize("path", ["docs/github-action.md", "README.md", "README.zh-CN.md"])
def test_hard_link_limitation_is_documented(path):
    assert re.search(r"[Hh]ard links|硬链接", (ROOT / path).read_text(encoding="utf-8")), path
