"""Time lexical ``audit`` and ``diff`` on deterministic memories.

    .venv/bin/python benchmarks/perf/run.py
    .venv/bin/python benchmarks/perf/run.py --sizes 100 500 --profile 500

Builds synthetic MEMORY.md files of exactly 100, 500, 1,000 and 3,000 claims (plus a few
duplicate copies that merge away), and also times the repository README.md. Synthetic claims
are a pure function of their index: clusters share terms so some pairs conflict, and unrelated
clusters do not. ``diff`` compares a committed file with a working tree where every 10th claim
is revised. Timings cover the library calls, not process startup.
"""
from __future__ import annotations

import argparse
import cProfile
import os
import pstats
import subprocess
import sys
import tempfile
import time
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for _package in ("memcore", "memfalsify"):  # this checkout, not another install
    if (_src := str(ROOT / "packages" / _package / "src")) not in sys.path:
        sys.path.insert(0, _src)

from memfalsify.audit import audit  # noqa: E402
from memfalsify.history import memory_diff  # noqa: E402
from memfalsify.memory import parse_memory  # noqa: E402

AS_OF = date(2026, 10, 11)
SIZES = (100, 500, 1000, 3000)
README = ROOT / "README.md"


def sentence(index: int) -> str:
    """One claim. Eight sentences share a cluster id; other clusters use different tokens."""
    cluster, slot = divmod(index, 8)
    a, b = f"c{cluster}a", f"c{cluster}b"
    left, right = f"t{index}", f"u{index}"
    if slot == 0:
        return f"{a} {left} approves {b} {right} today."
    if slot == 1:
        return f"{a} {left} rejects {b} {right} today."
    if slot == 2:
        return f"{a} {left} approves {b} {right} at 10."
    if slot == 3:
        return f"{a} {left} approves {b} {right} at 12."
    if slot == 4:
        return f"{a} {left} does not approve {b} {right}."
    if slot == 5:
        return f"{a} {left} enables {b} {right} always."
    # Cluster-specific characters, so the shared antonym is not enough to conflict across groups.
    mark = chr(0x5000 + cluster) + chr(0x5100 + cluster)
    if slot == 6:
        return f"{mark}批准{index}。"
    return f"{mark}拒绝{index}。"


def _revise(text: str) -> str:
    if text.endswith("。"):
        return text[:-1] + "补充。"
    if text.endswith("."):
        return text[:-1] + " revised."
    return text + " revised"


def render_memory(count: int, edited: bool = False) -> str:
    """Markdown that parses to ``count`` unique claims, with one duplicate copy per cluster."""
    lines = ["# Synthetic memory", ""]
    for index in range(count):
        if index % 8 == 0:
            lines.append(f"## Group {index // 8}")
        text = sentence(index)
        if edited and index % 10 == 0:
            text = _revise(text)
        lines.append(f"- {text}")
        if index % 8 == 0:
            lines.append(f"- {text}")
    return "\n".join(lines) + "\n"


def _git(repo: Path, *args: str, env: dict[str, str]) -> None:
    proc = subprocess.run(["git", "-C", str(repo), "-c", "commit.gpgsign=false", "-c", "core.autocrlf=false",
                           *args], capture_output=True, env=env, check=False)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.decode("utf-8", "replace") or f"git exited {proc.returncode}")


def _git_env(root: Path) -> dict[str, str]:
    config = root / "gitconfig"
    config.write_bytes(b"")
    hooks = root / "hooks"
    hooks.mkdir(exist_ok=True)
    env = os.environ.copy()
    for name in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR", "GIT_OBJECT_DIRECTORY",
                 "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_NAMESPACE", "GIT_CEILING_DIRECTORIES"):
        env.pop(name, None)
    env.update(GIT_CONFIG_GLOBAL=str(config), GIT_CONFIG_NOSYSTEM="1",
               GIT_AUTHOR_DATE="2026-10-01T00:00:00+00:00", GIT_COMMITTER_DATE="2026-10-01T00:01:00+00:00")
    return env


def _init_repo(repo: Path, env: dict[str, str]) -> None:
    repo.mkdir()
    hooks = str(repo.parent / "hooks")
    _git(repo, "-c", f"core.hooksPath={hooks}", "init", "-q", "-b", "main", env=env)
    _git(repo, "config", "user.name", "Perf Bench", env=env)
    _git(repo, "config", "user.email", "perf@example.com", env=env)


def time_diff(base_text: str, head_text: str, follow_links: bool) -> tuple[float, int]:
    """Seconds for ``memory_diff`` of base (HEAD) against head (working tree), and head claim count."""
    with tempfile.TemporaryDirectory(prefix="memfalsify-perf-") as tmp:
        root = Path(tmp)
        env = _git_env(root)
        repo = root / "repo"
        _init_repo(repo, env)
        memory = repo / "MEMORY.md"
        memory.write_bytes(base_text.encode("utf-8"))
        _git(repo, "add", "MEMORY.md", env=env)
        _git(repo, "commit", "-q", "-m", "base", env=env)
        memory.write_bytes(head_text.encode("utf-8"))
        started = time.perf_counter()
        report = memory_diff(memory, "HEAD", follow_links=follow_links, as_of=AS_OF)
        elapsed = time.perf_counter() - started
    return elapsed, len(report.audit.findings)


def time_audit(memory: Path, follow_links: bool) -> tuple[float, int]:
    started = time.perf_counter()
    report = audit(memory, as_of=AS_OF, follow_links=follow_links)
    return time.perf_counter() - started, len(report.findings)


def _row(label: str, claims: int, audit_s: float, diff_s: float | None) -> None:
    diff = f"{diff_s:10.3f}" if diff_s is not None else f"{'—':>10}"
    print(f"{label:18} {claims:7d} {audit_s:10.3f} {diff}", flush=True)


def run_sizes(sizes: list[int], readme: bool) -> None:
    print(f"{'input':18} {'claims':>7} {'audit_s':>10} {'diff_s':>10}", flush=True)
    with tempfile.TemporaryDirectory(prefix="memfalsify-perf-") as tmp:
        root = Path(tmp)
        for count in sizes:
            path = root / f"m{count}.md"
            text = render_memory(count)
            path.write_bytes(text.encode("utf-8"))
            claims = len(parse_memory(path, follow_links=False)[0])
            if claims != count:
                raise RuntimeError(f"synthetic file parsed to {claims} claims, expected {count}")
            audit_s, _ = time_audit(path, follow_links=False)
            diff_s, _ = time_diff(text, render_memory(count, edited=True), follow_links=False)
            _row(f"synthetic-{count}", claims, audit_s, diff_s)
    if readme:
        for follow in (False, True):
            claims = len(parse_memory(README, follow_links=follow)[0])
            audit_s, _ = time_audit(README, follow_links=follow)
            label = "README.md" if not follow else "README.md+links"
            diff_s = None
            if not follow:
                diff_s, _ = time_diff(README.read_text(encoding="utf-8"),
                                      README.read_text(encoding="utf-8") + "\n- Benchmark anchor claim stays here.\n",
                                      follow_links=False)
            _row(label, claims, audit_s, diff_s)


def profile_audit(count: int) -> None:
    with tempfile.TemporaryDirectory(prefix="memfalsify-perf-") as tmp:
        path = Path(tmp) / "profile.md"
        path.write_bytes(render_memory(count).encode("utf-8"))
        profiler = cProfile.Profile()
        profiler.enable()
        audit(path, as_of=AS_OF, follow_links=False)
        profiler.disable()
    stats = pstats.Stats(profiler).sort_stats("cumulative")
    stats.print_stats(40)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Time memfalsify lexical audit and diff.")
    parser.add_argument("--sizes", type=int, nargs="*", default=list(SIZES),
                        help="synthetic claim counts (default: 100 500 1000 3000)")
    parser.add_argument("--no-readme", action="store_true", help="skip README.md")
    parser.add_argument("--profile", type=int, metavar="N",
                        help="cProfile one synthetic audit of N claims instead of the timing table")
    args = parser.parse_args(argv)
    if args.profile is not None:
        profile_audit(args.profile)
        return
    run_sizes(args.sizes, readme=not args.no_readme)


if __name__ == "__main__":
    main()
