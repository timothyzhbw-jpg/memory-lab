#!/usr/bin/env python3
"""The closed loop on synthetic data, with the real CLIs: Remember, Verify, Challenge, Resolve, Evolve.

    python examples/closed-loop/run_demo.py [--work DIR] [--update-expected]

Builds a small git repository in a temporary directory, runs `memfalsify` and `orgmemory` on it, and compares the
reports with expected/. The script plays the people: it commits as them and appends the decision one of them
makes. Neither tool edits the memory or decides anything. Standard library only; README.md explains each step.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
EXPECTED = HERE / "expected"
INSTALL = "pip install -e packages/memcore -e packages/memfalsify -e packages/orgmemory"

FIRST_AUDIT = "2026-10-10"
FIRST_CUTOFF = "2026-10-11T00:00:00+00:00"
RESOLVED_AT = "2026-10-11T10:00:00+00:00"
SECOND_AUDIT = "2026-10-13"
SECOND_CUTOFF = "2026-10-14T00:00:00+00:00"

REFUNDS = "Refund requests over 500 dollars need a second reviewer."
DATABASE = "The main database runs PostgreSQL 15."
RATE_LIMIT = "The public API rate limit is 100 requests per minute."
EXPORT = "The nightly export job finishes before 04:00 UTC."
CORRECTED_RATE_LIMIT = "The public API rate limit is 60 requests per minute."


def memory(*lines: str) -> str:
    return "# Memory\n\n" + "".join(f"- {line}\n" for line in lines)


# (author, author date, subject, files). The subject's "(approved-by: NAME)" names who approved the write; without
# it the author approved their own line by landing it. Every author is a synthetic person or agent.
HISTORY = [
    ("zhou", "2026-10-01T09:00:00+00:00", "Add the service docs", {
        "docs/api.md": "# API limits\n\nThe public API rate limit is 60 requests per minute.\n",
        "docs/database.md": "# Database\n\nThe main database runs PostgreSQL 15.\n",
        "docs/refunds.md": "# Refunds\n\nRefund requests over 500 dollars need a second reviewer.\n"}),
    ("lin", "2026-10-05T09:00:00+00:00", "Remember the refund rule (approved-by: zhou)",
     {"MEMORY.md": memory(REFUNDS)}),
    ("lin", "2026-10-06T09:00:00+00:00", "Remember the database version (approved-by: zhou)",
     {"MEMORY.md": memory(REFUNDS, DATABASE)}),
    ("agent-7", "2026-10-07T09:00:00+00:00", "Remember the API rate limit (approved-by: wu)",
     {"MEMORY.md": memory(REFUNDS, DATABASE, RATE_LIMIT)}),
    ("agent-7", "2026-10-08T09:00:00+00:00", "Remember when the nightly export finishes",
     {"MEMORY.md": memory(REFUNDS, DATABASE, RATE_LIMIT, EXPORT)}),
    ("lin", "2026-10-09T09:00:00+00:00", "Record the database upgrade",
     {"docs/database.md": "# Database\n\nThe main database runs PostgreSQL 16.\n"}),
]
CORRECTION = ("lin", "2026-10-12T09:00:00+00:00", "Correct the API rate limit (approved-by: zhou)",
              {"MEMORY.md": memory(REFUNDS, DATABASE, CORRECTED_RATE_LIMIT, EXPORT)})


def shown(text: str) -> str:
    """A path on one line, whatever it holds."""
    return "".join(c if c.isprintable() else f"\\u{ord(c):04x}" for c in text)


def lf(data: bytes) -> bytes:
    """Some reports go to stdout as text, which has CRLF on Windows. Hashes and goldens need the same bytes."""
    return data.replace(b"\r\n", b"\n")


def work_directory(requested: Path | None) -> Path:
    """Where the run goes, with symlinks resolved: `orgmemory append` refuses a path that goes through one, and
    the temporary directory on macOS does (/var is a link to /private/var)."""
    return (requested or Path(tempfile.mkdtemp(prefix="memlab-closed-loop-")) / "run").resolve()


def clean_environment(work: Path) -> dict[str, str]:
    """No user or system git configuration, and SHA-1 ids, so the commit ids are the same everywhere."""
    config = work / "gitconfig"
    config.write_bytes(b"")
    env = {**os.environ, "GIT_CONFIG_GLOBAL": str(config), "GIT_CONFIG_NOSYSTEM": "1", "GIT_DEFAULT_HASH": "sha1",
           "GIT_TERMINAL_PROMPT": "0", "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
    for name in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY", "GIT_CEILING_DIRECTORIES"):
        env.pop(name, None)
    return env


class Demo:
    def __init__(self, work: Path) -> None:
        self.work, self.repo = work, work / "repo"
        self.dataset, self.reports = work / "dataset", work / "reports"
        for directory in (self.repo, self.dataset, self.reports):
            directory.mkdir(parents=True)
        self.env = clean_environment(work)
        self.git = shutil.which("git")
        self.run_git("init", "-q")

    def run_git(self, *args: str, **env: str) -> None:
        options = ("-c", "core.autocrlf=false", "-c", "commit.gpgsign=false")
        subprocess.run([self.git, "-C", str(self.repo), *options, *args], env={**self.env, **env}, check=True,
                       capture_output=True)

    def commit(self, author: str, date: str, subject: str, files: dict[str, str]) -> None:
        for name, text in files.items():
            path = self.repo / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(text.encode("utf-8"))
        self.run_git("add", "-A")
        email = f"{author}@example.com"
        self.run_git("commit", "-q", "-m", subject, GIT_AUTHOR_NAME=author, GIT_AUTHOR_EMAIL=email,
                     GIT_COMMITTER_NAME=author, GIT_COMMITTER_EMAIL=email, GIT_AUTHOR_DATE=date,
                     GIT_COMMITTER_DATE=date)

    def _python(self, what: str, *args: str) -> bytes:
        """Run from inside the repository, so reports hold relative paths. Stop if it fails."""
        proc = subprocess.run([sys.executable, *args], cwd=self.repo, env=self.env, capture_output=True)
        if proc.returncode != 0:
            sys.stderr.buffer.write(proc.stderr)
            sys.stderr.buffer.flush()
            raise SystemExit(f"{what} exited with {proc.returncode}")
        return lf(proc.stdout)

    def tool(self, name: str, *args: str) -> bytes:
        """A real CLI, with the interpreter that runs this script."""
        return self._python(f"{name} {args[0]}", "-m", name, *args)

    def verify(self, as_of: str, round_name: str, *audit_options: str, evidence: str = "docs") -> dict[str, Path]:
        """Audit and history, as JSON for the script and Markdown for a reader. Returns the paths."""
        paths = {"audit": self.reports / f"audit-{round_name}.json",
                 "history": self.reports / f"history-{round_name}.json",
                 "history_md": self.reports / f"history-{round_name}.md"}
        self.tool("memfalsify", "audit", "MEMORY.md", "--evidence", evidence, "--as-of", as_of, "--format", "json",
                  "--output", str(paths["audit"]), *audit_options)
        paths["history"].write_bytes(self.tool("memfalsify", "history", "MEMORY.md", "--format", "json"))
        paths["history_md"].write_bytes(self.tool("memfalsify", "history", "MEMORY.md"))
        return paths

    def record(self, paths: dict[str, Path], *options: str) -> list[dict]:
        """to_events.py, then one real orgmemory command per record."""
        output = self._python("to_events.py", str(HERE / "to_events.py"), str(paths["audit"]), str(paths["history"]),
                              str(paths["history_md"]), "--repo", str(self.repo), "--dataset", str(self.dataset),
                              *options)
        records = [json.loads(line) for line in output.decode("ascii").splitlines()]
        for record in records:
            if record["kind"] == "evidence":
                self.tool("orgmemory", "evidence", "add", str(self.dataset), f"--id={record['id']}",
                          f"--path={record['path']}", f"--quote={record['quote']}",
                          f"--source-uri={record['source_uri']}", f"--source-kind={record['source_kind']}",
                          f"--captured-at={record['captured_at']}")
            else:
                self.append(record["event"])
        return records

    def append(self, event: dict) -> None:
        self.tool("orgmemory", "append", str(self.dataset), "--event=" + json.dumps(event, ensure_ascii=True))

    def save(self, name: str, data: bytes) -> bytes:
        (self.reports / name).write_bytes(data)
        return data


def say(text: str = "") -> None:
    sys.stdout.buffer.write(text.encode("ascii", "backslashreplace") + b"\n")


def show(data: bytes) -> None:
    sys.stdout.buffer.write(data.rstrip(b"\n") + b"\n\n")
    sys.stdout.buffer.flush()


def counted(records: list[dict]) -> str:
    return (f"{sum(r['kind'] == 'evidence' for r in records)} evidence records and "
            f"{sum(r['kind'] == 'event' for r in records)} events")


def run(demo: Demo) -> dict[str, Path]:
    """The five steps. Returns every file that expected/ holds, by its name there."""
    say("1. Remember: lines land in MEMORY.md, from commits by different authors.")
    for commit in HISTORY:
        demo.commit(*commit)
    say(f"   {len(HISTORY)} commits. The subject of a commit can name who approved its line.")
    shutil.copyfile(HERE / "policy.json", demo.dataset / "policy.json")
    (demo.dataset / "events.jsonl").write_bytes(b"")

    say("\n2. Verify: memfalsify audit and memfalsify history. Read-only; the memory stays as it is.")
    first = demo.verify(FIRST_AUDIT, "first")
    show(demo.save("1-memfalsify-audit.md", demo.tool("memfalsify", "audit", "MEMORY.md", "--evidence", "docs",
                                                      "--as-of", FIRST_AUDIT)))
    demo.save("2-memfalsify-history.md", first["history_md"].read_bytes())

    say("3. Challenge: to_events.py turns the reports into evidence records and events; orgmemory replays them.")
    records = demo.record(first)
    say(f"   {counted(records)} went in through `orgmemory evidence add` and `orgmemory append`.\n")
    show(demo.save("3-orgmemory-audit.md", demo.tool("orgmemory", "audit", str(demo.dataset), "--as-of", FIRST_CUTOFF)))
    findings = json.loads(first["audit"].read_bytes())["findings"]
    rate_limit = next(f for f in findings if f["text"] == RATE_LIMIT)
    subject = f"memory:{rate_limit['memory_id']}"
    say("   Two lines have the same finding. zhou was allowed to approve the database line, so it is a risk after")
    say("   an approval. wu was not allowed to approve the rate limit line, so that approval was recorded and")
    say("   ignored, and the line is still waiting for someone who may approve it:\n")
    show(demo.save("4-orgmemory-explain.md", demo.tool("orgmemory", "explain", str(demo.dataset), subject)))

    say("4. Resolve: zhou holds the permission, reads docs/api.md and rejects the line. This script plays zhou.")
    risk = next(r["event"] for r in records if r["kind"] == "event" and r["event"]["subject"] == subject
                and r["event"]["action"] == "raise_risk")
    demo.append({"id": f"resolve-reject-{rate_limit['memory_id']}", "timestamp": RESOLVED_AT, "actor": "zhou",
                 "action": "reject", "subject": subject, "evidence_ids": risk["evidence_ids"],
                 "note": "docs/api.md says 60. Correct the line by hand."})
    show(demo.save("5-orgmemory-explain-resolved.md", demo.tool("orgmemory", "explain", str(demo.dataset), subject)))

    say("5. Evolve: lin corrects the line by hand, and the loop runs again. Only what is new is recorded.")
    demo.commit(*CORRECTION)
    second = demo.verify(SECOND_AUDIT, "second")
    demo.save("6-memfalsify-audit-corrected.md", demo.tool("memfalsify", "audit", "MEMORY.md", "--evidence", "docs",
                                                           "--as-of", SECOND_AUDIT))
    say(f"   {counted(demo.record(second))} are new.\n")
    findings = json.loads(second["audit"].read_bytes())["findings"]
    corrected = next(f for f in findings if f["text"] == CORRECTED_RATE_LIMIT)
    show(demo.save("7-orgmemory-explain-corrected.md",
                   demo.tool("orgmemory", "explain", str(demo.dataset), f"memory:{corrected['memory_id']}")))
    demo.save("8-orgmemory-audit-final.md",
              demo.tool("orgmemory", "audit", str(demo.dataset), "--as-of", SECOND_CUTOFF))
    say("The reports never changed the log. `orgmemory validate` checks every hash and quote once more:")
    show(demo.tool("orgmemory", "validate", str(demo.dataset)))
    return {**{path.name: path for path in sorted(demo.reports.glob("[0-9]-*"))},
            "events.jsonl": demo.dataset / "events.jsonl", "evidence.jsonl": demo.dataset / "evidence.jsonl"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the closed-loop demo with the real CLIs, on synthetic data.")
    parser.add_argument("--work", type=Path, help="a new directory for the run (default: a new temporary directory)")
    parser.add_argument("--update-expected", action="store_true", help="write the reports to expected/")
    args = parser.parse_args(argv)
    missing = [name for name in ("memfalsify", "orgmemory") if importlib.util.find_spec(name) is None]
    if shutil.which("git") is None or missing:
        sys.stderr.write(f"This demo needs git and these packages: {INSTALL}\n")
        return 2
    if args.work is not None and args.work.exists():
        sys.stderr.write("--work must name a directory that does not exist yet\n")
        return 2
    work = work_directory(args.work)
    produced = run(Demo(work))
    if args.update_expected:
        EXPECTED.mkdir(exist_ok=True)
        for name, path in produced.items():
            (EXPECTED / name).write_bytes(path.read_bytes())
    different = [name for name, path in produced.items()
                 if not (EXPECTED / name).is_file() or (EXPECTED / name).read_bytes() != path.read_bytes()]
    say(f"Everything is in {shown(str(work))}")
    if different:
        say("These differ from expected/: " + ", ".join(different))
        return 1
    say(f"All {len(produced)} files match expected/.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
