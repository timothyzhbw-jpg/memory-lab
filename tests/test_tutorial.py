"""The tutorial's bash blocks stay runnable.

``docs/tutorial.md`` is the source of the commands. Each fenced ``bash`` block
states its exit code on the first line (``# exit N``). This module runs those
blocks in order, in one shell, in a temporary directory that holds copies of
the example inputs, using this repository's CLIs. The run is offline and does
not select a model verifier. On Windows the shell run is skipped; the page
notes the differences there.
"""
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
TUTORIAL = ROOT / "docs" / "tutorial.md"
TUTORIAL_ZH = ROOT / "docs" / "tutorial.zh-CN.md"
MARKER = "__MEMLAB_TUTORIAL_EXIT_"
CLI_NAMES = ("memfalsify", "orgmemory", "memlab-mcp")


def bash_blocks(text: str) -> list[str]:
    """Return the interior of each ```bash fence, including a trailing newline."""
    lines = text.splitlines()
    blocks: list[str] = []
    index = 0
    while index < len(lines):
        if lines[index].strip() == "```bash":
            index += 1
            body: list[str] = []
            while index < len(lines) and lines[index].strip() != "```":
                body.append(lines[index])
                index += 1
            if index >= len(lines):
                raise AssertionError("unclosed bash fence in the tutorial")
            blocks.append("\n".join(body) + "\n")
        index += 1
    return blocks


def stated_exit(block: str) -> int:
    first = block.splitlines()[0]
    prefix = "# exit "
    if not first.startswith(prefix) or not first[len(prefix):].strip().isdigit():
        raise AssertionError(f"bash block must start with '# exit N', got {first!r}")
    return int(first[len(prefix):].strip())


def test_tutorial_blocks_match_and_state_exit_codes():
    english = bash_blocks(TUTORIAL.read_text(encoding="utf-8"))
    chinese = bash_blocks(TUTORIAL_ZH.read_text(encoding="utf-8"))
    assert english, "docs/tutorial.md has no bash blocks"
    assert chinese == english
    for block in english:
        stated_exit(block)
        assert "--verifier" not in block
        assert "ANTHROPIC_API_KEY" not in block
        assert "TYPESAFE_API_KEY" not in block


def test_readmes_link_the_tutorial_near_the_top():
    english = "\n".join((ROOT / "README.md").read_text(encoding="utf-8").splitlines()[:25])
    chinese = "\n".join((ROOT / "README.zh-CN.md").read_text(encoding="utf-8").splitlines()[:25])
    assert "docs/tutorial.md" in english
    assert "docs/tutorial.zh-CN.md" in chinese


def _fingerprint(path: Path) -> dict[str, bytes]:
    return {item.relative_to(path).as_posix(): item.read_bytes()
            for item in sorted(path.rglob("*")) if item.is_file()}


@pytest.mark.skipif(sys.platform == "win32", reason="tutorial bash blocks are for Linux and macOS")
def test_tutorial_bash_blocks_run_in_order(tmp_path):
    blocks = bash_blocks(TUTORIAL.read_text(encoding="utf-8"))
    expected = [stated_exit(block) for block in blocks]
    # The CLIs installed next to this interpreter: ./.venv/bin locally, the runner's Python on CI.
    bin_dir = Path(sys.executable).parent
    missing = [name for name in CLI_NAMES if not (bin_dir / name).is_file()]
    assert not missing, f"missing CLIs in {bin_dir}: {missing}"

    checkout = tmp_path / "checkout"
    checkout.mkdir()
    shutil.copytree(ROOT / "examples", checkout / "examples")
    fixture_dest = checkout / "tests" / "fixtures" / "github"
    fixture_dest.parent.mkdir(parents=True)
    shutil.copytree(ROOT / "tests" / "fixtures" / "github", fixture_dest)

    home = tmp_path / "home"
    home.mkdir()
    gitconfig = tmp_path / "gitconfig"
    gitconfig.write_bytes(b"")
    examples_before = _fingerprint(ROOT / "examples")
    fixtures_before = _fingerprint(ROOT / "tests" / "fixtures" / "github")

    env = os.environ.copy()
    env["PATH"] = str(bin_dir) + os.pathsep + env.get("PATH", "")
    env["HOME"] = str(home)
    env["XDG_CONFIG_HOME"] = str(home / ".config")
    env["XDG_DATA_HOME"] = str(home / ".local" / "share")
    env["TMPDIR"] = str(tmp_path)
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    env["GIT_CONFIG_GLOBAL"] = str(gitconfig)
    env["GIT_CEILING_DIRECTORIES"] = str(tmp_path)
    env["GIT_EDITOR"] = "true"
    env["GIT_SEQUENCE_EDITOR"] = "true"
    env["EDITOR"] = "true"
    env["VISUAL"] = "true"
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    for name in ("ANTHROPIC_API_KEY", "TYPESAFE_API_KEY", "GITHUB_TOKEN", "GH_TOKEN",
                 "GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_PREFIX",
                 "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES",
                 "CLAUDE_CONFIG_DIR", "CODEX_HOME"):
        env.pop(name, None)

    parts = ["set +e", f"cd {shlex.quote(str(checkout))}"]
    for index, block in enumerate(blocks):
        parts.append(block.rstrip("\n"))
        parts.append(f'printf "%s\\n" "{MARKER}{index}=$?"')
    script = tmp_path / "run-tutorial.sh"
    script.write_text("\n".join(parts) + "\n", encoding="utf-8")
    proc = subprocess.run(
        ["bash", str(script)],
        cwd=checkout,
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=180,
        check=False,
    )
    found: dict[int, int] = {}
    for line in proc.stdout.splitlines():
        if not line.startswith(MARKER):
            continue
        left, right = line[len(MARKER):].split("=", 1)
        found[int(left)] = int(right)
    mismatches = [(index, expected[index], found.get(index)) for index in range(len(blocks))
                  if found.get(index) != expected[index]]
    assert not mismatches, _failure(mismatches, blocks, proc)
    assert found.keys() == set(range(len(blocks)))
    assert _fingerprint(ROOT / "examples") == examples_before
    assert _fingerprint(ROOT / "tests" / "fixtures" / "github") == fixtures_before
    assert not (checkout / ".venv").exists()


def _failure(mismatches: list[tuple[int, int, int | None]], blocks: list[str], proc: subprocess.CompletedProcess[str]) -> str:
    lines = [f"bash exit {proc.returncode}", "mismatched blocks:"]
    for index, want, got in mismatches:
        first = blocks[index].splitlines()[0]
        lines.append(f"  block {index} ({first}): expected {want}, got {got}")
        preview = "\n".join(blocks[index].splitlines()[:8])
        lines.append(preview)
    lines.append("--- stdout (tail) ---")
    lines.append("\n".join(proc.stdout.splitlines()[-80:]))
    lines.append("--- stderr (tail) ---")
    lines.append("\n".join(proc.stderr.splitlines()[-80:]))
    return "\n".join(lines)
