"""Run step of the MemFalsify GitHub Action (action.yml).

Audits each memory file that exists, writes the Markdown report to the job summary and to a file, and sets the
``report`` and ``attention`` outputs. With a ``diff-base`` input, runs ``memfalsify diff`` instead, so only the
claims added or changed since that revision are audited. Inputs arrive as MEMFALSIFY_* environment variables,
never as interpolated script text. Exit codes follow ``memfalsify audit --strict``: 1 when a memory needs
attention, 2 on errors.
"""
from __future__ import annotations

import io
import os
import shlex
import sys
import tempfile
import traceback
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from memfalsify.audit import ATTENTION, AuditReport
from memfalsify.cli import app
from memfalsify.corpus import DIFF_HEADER, REPORT_HEADER
from memfalsify.history import DiffReport, HistoryError, diff_to_markdown, resolve_commit
from memfalsify.report import to_markdown

ERROR = 2
SUMMARY_LIMIT = 1_000_000  # GitHub rejects step summaries over 1 MiB.


def _escape(text: str) -> str:
    return text.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def _log_text(text: str) -> str:
    """Keep arbitrary diagnostics inert even if a runner searches mid-line."""
    return _escape(text).replace("::", "%3A%3A")


def annotate(kind: str, message: str) -> None:
    print(f"::{kind} title=MemFalsify::{_escape(message)}", flush=True)


def _append(env: dict[str, str], variable: str, text: str) -> None:
    if target := env.get(variable):
        with open(target, "ab") as handle:
            handle.write(text.encode("utf-8"))


def run_cli(args: list[str]) -> int:
    """Run ``memfalsify`` in this process and return its exit code."""
    output = io.StringIO()
    try:
        with redirect_stdout(output), redirect_stderr(output):
            try:
                app(args, prog_name="memfalsify", windows_expand_args=False)
            except SystemExit as exc:
                return exc.code if isinstance(exc.code, int) else 0 if exc.code is None else ERROR
            except Exception:
                traceback.print_exc()
                return ERROR
        return 0
    finally:
        # CLI errors can contain repository paths or provider text. Never let a
        # physical output line become a runner workflow command.
        for line in output.getvalue().splitlines():
            print(f"MemFalsify CLI: {_log_text(line)}", flush=True)


def options(env: dict[str, str]) -> list[str]:
    """CLI options from the action inputs; ``--name=value`` so a value can never be read as another option."""
    out = [f"--evidence={env.get('MEMFALSIFY_EVIDENCE') or '.'}",
           f"--verifier={env.get('MEMFALSIFY_VERIFIER') or 'lexical'}"]
    out += [f"--exclude={line.strip()}" for line in env.get("MEMFALSIFY_EXCLUDE", "").splitlines() if line.strip()]
    if as_of := env.get("MEMFALSIFY_AS_OF", "").strip():
        out.append(f"--as-of={as_of}")
    return out


def publish(env: dict[str, str], path: Path, text: str, attention: int) -> None:
    path.write_bytes(text.encode("utf-8"))
    if len(text.encode("utf-8")) > SUMMARY_LIMIT:
        text = text.encode("utf-8")[:SUMMARY_LIMIT].decode("utf-8", "ignore")
        text += "\n\n**Report truncated for the job summary; the full report is in the `report` output file.**\n"
    _append(env, "GITHUB_STEP_SUMMARY", text)
    _append(env, "GITHUB_OUTPUT", f"report={path}\nattention={attention}\n")


def main(env: dict[str, str] | None = None) -> int:
    env = dict(os.environ if env is None else env)
    try:
        memories = shlex.split(env.get("MEMFALSIFY_MEMORY", "CLAUDE.md AGENTS.md"))
    except ValueError as exc:
        annotate("error", f"cannot parse the memory input: {exc}")
        return ERROR
    strict = env.get("MEMFALSIFY_STRICT", "true").strip().lower()
    if strict not in ("true", "false"):
        annotate("error", "the strict input must be true or false")
        return ERROR
    base = None
    if diff_base := env.get("MEMFALSIFY_DIFF_BASE", "").strip():
        try:
            # Refuses option-like values, then: git rev-parse --verify --end-of-options REV^{commit}
            base = resolve_commit(Path.cwd(), diff_base)
        except HistoryError as exc:
            annotate("error", f"diff-base: {exc}. actions/checkout needs fetch-depth: 0 (or enough history "
                              "to include the base commit)")
            return ERROR
    out_dir = Path(env.get("RUNNER_TEMP") or tempfile.mkdtemp()) / "memfalsify"
    out_dir.mkdir(parents=True, exist_ok=True)
    report_path = out_dir / "report.md"
    found = [m for m in memories if Path(m).is_file()]
    for missing in (m for m in memories if m not in found):
        print(f"MemFalsify: {_log_text(missing)} not found, skipping", flush=True)
    if not found:
        message = "No memory file found (looked for " + (", ".join(memories) or "nothing") + "); nothing to audit."
        annotate("notice", message)
        publish(env, report_path, f"{REPORT_HEADER}\n\n{message}\n", 0)
        return 0
    sections, codes, attention = [], [], 0
    command = ["diff", f"--base={base}"] if base else ["audit"]
    for index, memory in enumerate(found):
        json_path = out_dir / f"report-{index}.json"
        json_path.unlink(missing_ok=True)
        args = [*command, "--no-follow-links", "--format=json", f"--output={json_path}", *options(env)]
        code = run_cli(args + (["--strict"] if strict == "true" else []) + ["--", memory])
        if code not in ((0, 1) if strict == "true" else (0,)) or not json_path.is_file():
            annotate("error", f"memfalsify could not audit {memory} (exit code {code}); see the step log")
            sections.append(f"{DIFF_HEADER if base else REPORT_HEADER}\n\n- Memory: `{memory}`\n\n"
                            f"**Audit failed** with exit code {code}; see the step log.\n")
            codes.append(ERROR)
            continue
        text = json_path.read_text(encoding="utf-8")
        if base:
            diff = DiffReport.model_validate_json(text)
            count, total, what = diff.attention, len(diff.audit.findings), "added or changed memories"
            sections.append(diff_to_markdown(diff))
        else:
            report = AuditReport.model_validate_json(text)
            count = sum(n for status, n in report.summary.items() if status in ATTENTION)
            total, what = len(report.findings), "memories"
            sections.append(to_markdown(report))
        attention += count
        codes.append(code)
        print(f"MemFalsify: {_log_text(memory)}: {count} of {total} {what} need attention", flush=True)
    publish(env, report_path, "\n".join(sections), attention)
    if ERROR in codes:
        return ERROR
    if attention:
        annotate("error" if 1 in codes else "warning",
                 f"{attention} {'memory needs' if attention == 1 else 'memories need'} attention (challenged, "
                 "conflicted, disputed or possible counterevidence); see the job summary")
    return 1 if 1 in codes else 0


if __name__ == "__main__":
    sys.exit(main())
