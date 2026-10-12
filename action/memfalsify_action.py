"""Run step of the MemFalsify GitHub Action (action.yml).

Audits each memory file that exists (with ``memory: auto``, each instruction file ``memfalsify discover`` finds
in the workspace), writes the Markdown report to the job summary and to a file, and sets the
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
from collections.abc import Iterator
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from pathlib import Path

from memcore.display import one_line, report_text
from memcore.paths import InputPathError, input_path
from memfalsify import ATTENTION, AuditReport
from memfalsify.cli import app
from memfalsify.corpus import DIFF_HEADER, REPORT_HEADER
from memfalsify import DiffReport, HistoryError, diff_to_markdown, resolve_commit
from memfalsify import to_markdown
from memfalsify.discover import discover
from memfalsify.gitstore import code_span
from memfalsify.openai_compat import FORMATS, Provider, check_base_url, check_key_env

ERROR = 2
SUMMARY_LIMIT = 1_000_000  # GitHub rejects step summaries over 1 MiB.
COLOUR_VARIABLES = ("FORCE_COLOR", "PY_COLORS")  # either one makes Typer force Rich styling
AUTO = "auto"  # memory input: audit every instruction file `memfalsify discover` finds in the workspace
# Inputs of the openai verifier: environment variable -> CLI option.
OPENAI_OPTIONS = {"MEMFALSIFY_OPENAI_MODEL": "--openai-model", "MEMFALSIFY_PROVIDER": "--provider",
                  "MEMFALSIFY_BASE_URL": "--base-url", "MEMFALSIFY_API_KEY_ENV": "--api-key-env",
                  "MEMFALSIFY_OPENAI_RESPONSE_FORMAT": "--openai-response-format"}


@contextmanager
def plain_cli_output() -> Iterator[None]:
    """Keep Rich styling out of the CLI's output while it runs.

    On a runner, GITHUB_ACTIONS (and often FORCE_COLOR) makes Typer force Rich styling even though the output is
    captured here, and ``_log_text`` would then print Rich's own SGR codes as literal ``\\u001b[1;33m`` text.
    ``_TYPER_FORCE_DISABLE_TERMINAL`` stops that; Typer reads it, and the colour variables, when it first renders
    an error or help, which happens inside this block. NO_COLOR covers Rich itself. The variables are put back
    afterwards.
    """
    saved = {name: os.environ.get(name) for name in ("_TYPER_FORCE_DISABLE_TERMINAL", "NO_COLOR", *COLOUR_VARIABLES)}
    os.environ.update({"_TYPER_FORCE_DISABLE_TERMINAL": "1", "NO_COLOR": "1"})
    for name in COLOUR_VARIABLES:
        os.environ.pop(name, None)
    try:
        yield
    finally:
        for name, value in saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def _escape(text: str) -> str:
    return text.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def _log_text(text: str) -> str:
    """Keep arbitrary diagnostics inert even if a runner searches mid-line."""
    return one_line(_escape(text)).replace("::", "%3A%3A")


def annotate(kind: str, message: str) -> None:
    print(f"::{kind} title=MemFalsify::{one_line(_escape(message))}", flush=True)


def _append(env: dict[str, str], variable: str, text: str) -> None:
    if target := env.get(variable):
        with open(target, "ab") as handle:
            handle.write(text.encode("utf-8"))


def run_cli(args: list[str]) -> int:
    """Run ``memfalsify`` in this process and return its exit code."""
    output = io.StringIO()
    try:
        with redirect_stdout(output), redirect_stderr(output), plain_cli_output():
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
        captured = output.getvalue()
        lines = captured.removesuffix("\n").split("\n") if captured else []
        for line in lines:
            print(f"MemFalsify CLI: {_log_text(line)}", flush=True)


def options(env: dict[str, str]) -> list[str]:
    """CLI options from the action inputs; ``--name=value`` so a value can never be read as another option."""
    out = [f"--evidence={env.get('MEMFALSIFY_EVIDENCE') or '.'}",
           f"--verifier={env.get('MEMFALSIFY_VERIFIER') or 'lexical'}"]
    out += [f"--exclude={pattern}" for pattern in excludes(env)]
    if as_of := env.get("MEMFALSIFY_AS_OF", "").strip():
        out.append(f"--as-of={as_of}")
    if (env.get("MEMFALSIFY_VERIFIER") or "").strip() == "openai":
        out += [f"{flag}={value}" for variable, flag in OPENAI_OPTIONS.items()
                if (value := env.get(variable, "").strip())]
    return out


def openai_problem(env: dict[str, str]) -> str | None:
    """Why the openai verifier's inputs are unusable, or None. The messages never repeat a base URL or a key
    variable name: a URL with a token in it, or a key pasted where its variable's name belongs, must not reach
    the log."""
    if (env.get("MEMFALSIFY_VERIFIER") or "").strip() != "openai":
        if any(env.get(variable, "").strip() for variable in OPENAI_OPTIONS):
            annotate("warning", "the openai-model, provider, base-url, api-key-env and openai-response-format "
                                "inputs apply only to verifier openai; ignoring them")
        return None
    if not env.get("MEMFALSIFY_OPENAI_MODEL", "").strip():
        return ("verifier openai needs the openai-model input: model names differ between providers, so there "
                "is no default")
    provider = env.get("MEMFALSIFY_PROVIDER", "").strip()
    if provider and provider not in {p.value for p in Provider}:
        return "the provider input must be one of " + ", ".join(p.value for p in Provider)
    response_format = env.get("MEMFALSIFY_OPENAI_RESPONSE_FORMAT", "").strip()
    if response_format and response_format not in FORMATS:
        return "the openai-response-format input must be one of " + ", ".join(FORMATS)
    try:
        if base_url := env.get("MEMFALSIFY_BASE_URL", "").strip():
            check_base_url(base_url)
        if key_env := env.get("MEMFALSIFY_API_KEY_ENV", "").strip():
            check_key_env(key_env)
    except ValueError as exc:
        return f"verifier openai: {exc}"
    return None


def excludes(env: dict[str, str]) -> list[str]:
    return [line.strip() for line in env.get("MEMFALSIFY_EXCLUDE", "").splitlines() if line.strip()]


def discovered(env: dict[str, str]) -> list[str]:
    """``memory: auto``: every instruction file under the workspace, each file once (raises OSError)."""
    found = discover(Path.cwd(), exclude=excludes(env))
    for f in found.files:
        alias = f", same file as {_log_text(f.same_as)}" if f.same_as else ""
        print(f"MemFalsify: found {_log_text(f.path)} ({f.agent}{alias})", flush=True)
    for s in found.skipped:
        print(f"MemFalsify: skipped {_log_text(s.path)}: {s.reason}", flush=True)
    return found.distinct


def _posix_report_paths(report: AuditReport | DiffReport) -> None:
    """The CLI receives native Paths; Action artifacts use POSIX path spelling on every runner."""
    report.memory_file = Path(report.memory_file).as_posix()
    audit = report.audit if isinstance(report, DiffReport) else report
    audit.memory_file = Path(audit.memory_file).as_posix()
    if audit.evidence_root is not None:
        audit.evidence_root = Path(audit.evidence_root).as_posix()


def publish(env: dict[str, str], path: Path, text: str, attention: int) -> None:
    path.write_bytes(text.encode("utf-8"))
    if len(text.encode("utf-8")) > SUMMARY_LIMIT:
        text = text.encode("utf-8")[:SUMMARY_LIMIT].decode("utf-8", "ignore")
        text += "\n\n**Report truncated for the job summary; the full report is in the `report` output file.**\n"
    _append(env, "GITHUB_STEP_SUMMARY", text)
    _append(env, "GITHUB_OUTPUT", f"report={path.as_posix()}\nattention={attention}\n")


def main(env: dict[str, str] | None = None) -> int:
    env = dict(os.environ if env is None else env)
    requested = env.get("MEMFALSIFY_MEMORY", "CLAUDE.md AGENTS.md")
    auto = requested.strip() == AUTO
    try:
        memories = [] if auto else shlex.split(requested)
    except ValueError as exc:
        annotate("error", f"cannot parse the memory input: {exc}")
        return ERROR
    strict = env.get("MEMFALSIFY_STRICT", "true").strip().lower()
    if strict not in ("true", "false"):
        annotate("error", "the strict input must be true or false")
        return ERROR
    if problem := openai_problem(env):
        annotate("error", problem)
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
    if auto:
        try:
            memories = discovered(env)
        except (ValueError, OSError) as exc:
            annotate("error", f"memory: auto could not search the workspace: {exc}")
            return ERROR
    out_dir = Path(env.get("RUNNER_TEMP") or tempfile.mkdtemp()) / "memfalsify"
    out_dir.mkdir(parents=True, exist_ok=True)
    report_path = out_dir / "report.md"
    found = []
    workspace = Path(env.get("GITHUB_WORKSPACE") or Path.cwd()).resolve()
    for memory in memories:
        try:
            path = input_path(workspace, Path(memory), instructions=True)
        except InputPathError as exc:
            annotate("warning", f"{memory}: {exc}; skipping memory file")
            continue
        if path.is_file():
            found.append(memory)
        else:
            print(f"MemFalsify: {_log_text(memory)} not found, skipping", flush=True)
    if not found:
        message = ("No agent instruction file found in the workspace (memory: auto); nothing to audit." if auto
                   else "No memory file found (looked for " + (", ".join(memories) or "nothing")
                   + "); nothing to audit.")
        annotate("notice", message)
        publish(env, report_path, f"{REPORT_HEADER}\n\n{report_text(message)}\n", 0)
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
            sections.append(f"{DIFF_HEADER if base else REPORT_HEADER}\n\n- Memory: {code_span(memory)}\n\n"
                            f"**Audit failed** with exit code {code}; see the step log.\n")
            codes.append(ERROR)
            continue
        text = json_path.read_text(encoding="utf-8")
        if base:
            diff = DiffReport.model_validate_json(text)
            _posix_report_paths(diff)
            json_path.write_bytes((diff.model_dump_json(indent=2) + "\n").encode("utf-8"))
            count, total, what = diff.attention, len(diff.audit.findings), "added or changed memories"
            sections.append(diff_to_markdown(diff))
        else:
            report = AuditReport.model_validate_json(text)
            _posix_report_paths(report)
            json_path.write_bytes((report.model_dump_json(indent=2) + "\n").encode("utf-8"))
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
