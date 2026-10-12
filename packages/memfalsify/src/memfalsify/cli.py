"""memfalsify command line. Reads memory and evidence; writes only the report you ask for."""
from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterable
from datetime import date
from enum import Enum
from pathlib import Path
from typing import Optional

import typer
from pydantic import ValidationError

from memcore.cli import SafeTyperGroup
from memcore.display import report_text

from . import jev, llm
from .audit import ATTENTION, audit
from .discover import discover, discovery_to_markdown
from .history import diff_to_markdown, history_to_markdown, memory_diff, memory_history
from .htmlreport import diff_to_html, to_html
from .memory import claim_record, parse_memory
from .report import to_markdown
from .rollback import rollback_patch
from .verify import CascadeVerifier, DualVerifier, LexicalVerifier, Verifier, VerifierError

app = typer.Typer(cls=SafeTyperGroup, no_args_is_help=True, add_completion=False,
                  help="Audit agent memory and instruction files against evidence. Never modifies memory.")

# Memory files are Markdown: MEMORY.md and the instruction files `discover` lists.
MEMORY_KINDS = ("a memory or agent instruction file: MEMORY.md, CLAUDE.md, AGENTS.md, GEMINI.md, "
                ".cursor/rules/*.mdc, .github/copilot-instructions.md, ... (see `memfalsify discover`)")


class Format(str, Enum):
    markdown = "markdown"
    json = "json"


class ReportFormat(str, Enum):
    """`audit` and `diff` also render one self-contained HTML file."""
    markdown = "markdown"
    json = "json"
    html = "html"


class VerifierChoice(str, Enum):
    lexical = "lexical"
    llm = "llm"
    jev = "jev"
    dual = "dual"
    cascade = "cascade"


CLAUDE_CHOICES = (VerifierChoice.llm, VerifierChoice.dual, VerifierChoice.cascade)


def build_verifier(choice: VerifierChoice, llm_model: str, jev_model: str, accept_at: float = 0.8,
                   batch: bool = False, progress: Callable[[str], None] | None = None) -> Verifier:
    """Model backends import their heavy dependencies only when constructed. ``batch`` sends the Claude
    requests through the Message Batches API; ``progress`` receives its status lines."""
    if batch and choice not in CLAUDE_CHOICES:
        raise ValueError("--batch applies to Claude requests: use it with --verifier llm, dual or cascade")

    def claude() -> Verifier:
        return llm.LLMVerifier(llm_model, batch=True, progress=progress) if batch else llm.LLMVerifier(llm_model)

    if choice is VerifierChoice.lexical:
        return LexicalVerifier()
    if choice is VerifierChoice.llm:
        return claude()
    if choice is VerifierChoice.jev:
        return jev.JevVerifier(jev_model)
    if choice is VerifierChoice.cascade:
        return CascadeVerifier(jev.JevVerifier(jev_model), claude(), accept_at)
    return DualVerifier(claude(), jev.JevVerifier(jev_model))


MAX_JUDGED = 8


def preselection(choice: VerifierChoice, min_coverage: float | None = None, max_judged: int = MAX_JUDGED) -> dict:
    """audit() passage limits: lexical judges every passage covering half the terms, models a bounded few."""
    lexical = choice is VerifierChoice.lexical
    return {"min_coverage": min_coverage if min_coverage is not None else 0.5 if lexical else 0.2,
            "max_judged": None if lexical else max_judged}


# C1, bidi, zero-width and line separators. json.dumps already escapes C0. Replacing the raw
# character with a \\uXXXX escape keeps json.loads able to restore it and a terminal unable to act on it.
_JSON_UNSAFE = re.compile("[\x7f-\x9f\u061c\u200b-\u200f\u2028-\u202e\u2066-\u2069\ufeff]")


def _json_shield(text: str) -> str:
    return _JSON_UNSAFE.sub(lambda match: "\\u%04x" % ord(match.group()), text)
def concurrency(choice: VerifierChoice, max_concurrency: int) -> int:
    """Model verifiers wait on the network, so they run requests in parallel; lexical judging stays serial."""
    return 1 if choice is VerifierChoice.lexical else max_concurrency


def _progress(message: str) -> None:
    typer.echo(report_text(message), err=True)


def _fail(message: str) -> None:
    typer.echo(f"error: {report_text(message)}", err=True)
    raise typer.Exit(2)


def _emit(text: str, markdown: bool) -> None:
    typer.echo(text if markdown else _json_shield(text), nl=False)


def _write(output: Path, data: bytes, protected: Iterable[Path], what: str = "report") -> None:
    """Write exact bytes (no newline translation); never over a memory file."""
    if output.resolve() in {Path(p).resolve() for p in protected}:
        _fail(f"refusing to overwrite a memory file with the {what}")
    output.write_bytes(data)
    typer.echo(f"wrote {report_text(str(output))}", err=True)


def _echo_exact(text: str) -> None:
    """ASCII report bytes. Windows text-mode stdout would otherwise turn LF into CRLF."""
    typer.echo(text.encode("ascii"), nl=False)


def _render(fmt: ReportFormat, report, markdown: Callable[..., str], html: Callable[..., str]) -> str:
    if fmt is ReportFormat.json:
        return report.model_dump_json(indent=2) + "\n"
    return html(report) if fmt is ReportFormat.html else markdown(report)


# Options shared by `audit` and `diff`.
MEMORY_ARG = typer.Argument(..., exists=True, dir_okay=False, help="File to audit, " + MEMORY_KINDS)
GIT_MEMORY_ARG = typer.Argument(..., exists=True, dir_okay=False, help="In a git work tree, " + MEMORY_KINDS)
EVIDENCE = typer.Option(None, "--evidence", "-e", help="Directory of evidence files")
EXCLUDE = typer.Option(
    None, "--exclude", "-x", metavar="GLOB",
    help="Leave matching evidence files out (fnmatch on paths relative to --evidence, e.g. 'examples/**'); "
         "repeatable")
REVIEWS = typer.Option(None, "--reviews", "-r", exists=True, dir_okay=False, help="JSONL of human-confirmed assertions")
FORMAT = typer.Option(Format.markdown, "--format", "-f")
REPORT_FORMAT = typer.Option(
    ReportFormat.markdown, "--format", "-f",
    help="markdown, json, or html (one self-contained page: inline CSS, no scripts, nothing loaded)")
OUTPUT = typer.Option(None, "--output", "-o", help="Write the report here instead of stdout")
AS_OF = typer.Option(None, help="Reference date YYYY-MM-DD for staleness (default: today)")
STALE_DAYS = typer.Option(180, help="Dated claims older than this are flagged possibly_stale")
FOLLOW_LINKS = typer.Option(True, help="Also audit local .md files the memory file links to or @-imports")
VERIFIER = typer.Option(VerifierChoice.lexical, "--verifier",
                        help="lexical (offline), llm (Claude), jev (TypeSafe Jev judgment model), "
                             "dual (llm + jev; disagreements become disputed), "
                             "or cascade (jev, escalating unsure answers to llm)")
LLM_MODEL = typer.Option(llm.DEFAULT_MODEL, help="Claude model for --verifier llm/dual/cascade")
JEV_MODEL = typer.Option(jev.DEFAULT_MODEL, help="TypeSafe model for --verifier jev/dual/cascade")
ACCEPT_AT = typer.Option(0.8, min=0.0, max=1.0, help="--verifier cascade: accept Jev at this confidence, else ask the LLM")
MAX_JUDGED_OPTION = typer.Option(MAX_JUDGED, min=1, help="Model verifiers: passages judged per memory (bounds API calls)")
MAX_CONCURRENCY = typer.Option(
    4, min=1, help="Model verifiers: API requests in flight at once (results keep their order; not used for "
                   "--batch requests)")
BATCH = typer.Option(
    False, "--batch", help="Send the Claude requests (llm, dual, cascade) through the Message Batches API: half "
                           "the price, but a batch can take up to 24 hours (most end within an hour)")
MIN_COVERAGE = typer.Option(
    None, min=0.0, max=1.0,
    help="Share of a memory's terms a passage must contain to be judged (default 0.5 lexical, 0.2 models)")


@app.command("audit")
def audit_command(
    memory: Path = MEMORY_ARG,
    evidence: Optional[Path] = EVIDENCE,
    exclude: Optional[list[str]] = EXCLUDE,
    reviews: Optional[Path] = REVIEWS,
    fmt: ReportFormat = REPORT_FORMAT,
    output: Optional[Path] = OUTPUT,
    as_of: Optional[str] = AS_OF,
    stale_days: int = STALE_DAYS,
    follow_links: bool = FOLLOW_LINKS,
    strict: bool = typer.Option(False, help="Exit 1 if any memory is challenged, conflicted or has counterevidence"),
    verifier: VerifierChoice = VERIFIER,
    llm_model: str = LLM_MODEL,
    jev_model: str = JEV_MODEL,
    accept_at: float = ACCEPT_AT,
    max_judged: int = MAX_JUDGED_OPTION,
    min_coverage: Optional[float] = MIN_COVERAGE,
    max_concurrency: int = MAX_CONCURRENCY,
    batch: bool = BATCH,
) -> None:
    """Audit a memory or instruction file and print an evidence-cited report with proposed (unapplied) actions."""
    try:
        reference = date.fromisoformat(as_of) if as_of else None
        judge = build_verifier(verifier, llm_model, jev_model, accept_at, batch=batch, progress=_progress)
        report = audit(memory, evidence, reviews, verifier=judge, as_of=reference, stale_days=stale_days,
                       follow_links=follow_links, exclude=exclude or (),
                       max_concurrency=concurrency(verifier, max_concurrency),
                       **preselection(verifier, min_coverage, max_judged))
    except (ValidationError, ValueError, OSError, VerifierError) as exc:
        _fail(str(exc))
    text = _render(fmt, report, to_markdown, to_html)
    if output:
        _write(output, text.encode("utf-8"), (Path(memory).absolute().parent.resolve() / p for p in report.files_read))
    elif fmt is ReportFormat.html:
        _echo_exact(text)
    else:
        _emit(text, fmt is ReportFormat.markdown)
    if strict and ATTENTION & report.summary.keys():
        raise typer.Exit(1)


@app.command("diff")
def diff_command(
    memory: Path = GIT_MEMORY_ARG,
    base: str = typer.Option(..., "--base", help="Revision to compare against (branch, tag or commit), "
                                                 "e.g. main or the pull request's base commit"),
    head: Optional[str] = typer.Option(None, "--head", help="Revision to compare (default: the working tree). "
                                                            "Not used with --staged."),
    staged: bool = typer.Option(False, "--staged",
                                help="Compare --base with the index (what git commit will record) instead of "
                                     "the working tree. Unstaged edits to memory are ignored. Evidence is still "
                                     "the working tree. Cannot be combined with --head."),
    evidence: Optional[Path] = EVIDENCE,
    exclude: Optional[list[str]] = EXCLUDE,
    reviews: Optional[Path] = REVIEWS,
    fmt: ReportFormat = REPORT_FORMAT,
    output: Optional[Path] = OUTPUT,
    as_of: Optional[str] = AS_OF,
    stale_days: int = STALE_DAYS,
    follow_links: bool = typer.Option(True, help="Also compare local .md files the memory file links to or "
                                                 "@-imports"),
    strict: bool = typer.Option(False, help="Exit 1 if an added or changed memory is challenged, conflicted, "
                                            "disputed or has counterevidence"),
    verifier: VerifierChoice = VERIFIER,
    llm_model: str = LLM_MODEL,
    jev_model: str = JEV_MODEL,
    accept_at: float = ACCEPT_AT,
    max_judged: int = MAX_JUDGED_OPTION,
    min_coverage: Optional[float] = MIN_COVERAGE,
    max_concurrency: int = MAX_CONCURRENCY,
    batch: bool = BATCH,
) -> None:
    """Show which claims changed between two revisions and audit only the added and changed ones.

    With --staged, the version under test is the index. Evidence is the working tree either way.
    """
    try:
        reference = date.fromisoformat(as_of) if as_of else None
        judge = build_verifier(verifier, llm_model, jev_model, accept_at, batch=batch, progress=_progress)
        report = memory_diff(memory, base, head, follow_links=follow_links, evidence_root=evidence, reviews=reviews,
                             verifier=judge, as_of=reference, stale_days=stale_days, exclude=exclude or (),
                             max_concurrency=concurrency(verifier, max_concurrency), staged=staged,
                             **preselection(verifier, min_coverage, max_judged))
    except (ValidationError, ValueError, OSError, VerifierError) as exc:
        _fail(str(exc))
    text = _render(fmt, report, diff_to_markdown, diff_to_html)
    if output:
        root = Path(memory).resolve().parent
        _write(output, text.encode("utf-8"), (root / p for p in report.base.files + report.head.files))
    elif fmt is ReportFormat.html:
        _echo_exact(text)
    else:
        _emit(text, fmt is ReportFormat.markdown)
    if strict and report.attention:
        raise typer.Exit(1)


@app.command("history")
def history_command(
    memory: Path = GIT_MEMORY_ARG,
    max_commits: int = typer.Option(100, min=1, help="Read at most this many commits that touched the memory"),
    fmt: Format = FORMAT,
    follow_links: bool = typer.Option(True, help="Also trace local .md files the memory file links to or "
                                                 "@-imports"),
) -> None:
    """For each claim, the commits that introduced and last changed it; plus the claims removed, and when."""
    try:
        report = memory_history(memory, max_commits=max_commits, follow_links=follow_links)
    except (ValidationError, ValueError, OSError) as exc:
        _fail(str(exc))
    text = history_to_markdown(report) if fmt is Format.markdown else report.model_dump_json(indent=2) + "\n"
    _emit(text, fmt is Format.markdown)


@app.command("rollback")
def rollback_command(
    memory: Path = GIT_MEMORY_ARG,
    claim: str = typer.Option(..., "--claim", help="Claim ID (from `claims`, `diff` or `history`)"),
    to: str = typer.Option(..., "--to", help="Revision whose wording of the claim to restore"),
    output: Optional[Path] = typer.Option(None, "--output", "-o", help="Write the patch here instead of stdout"),
    follow_links: bool = typer.Option(True, help="Also look in local .md files the memory file links to or "
                                                 "@-imports"),
) -> None:
    """Print (never apply) a patch restoring a claim's text as of a revision, or re-adding a removed claim."""
    try:
        result = rollback_patch(memory, claim, to, follow_links=follow_links)
    except (ValidationError, ValueError, OSError) as exc:
        _fail(str(exc))
    if result.patch is not None:
        if output:
            _write(output, result.patch, result.files, "patch")
        else:
            typer.echo(result.patch, nl=False)  # bytes go out as-is, so line endings survive on Windows
    typer.echo(result.message, err=True)


@app.command("claims")
def claims_command(
    memory: Path = typer.Argument(..., exists=True, dir_okay=False, help="File to split, " + MEMORY_KINDS),
    follow_links: bool = typer.Option(True),
) -> None:
    """List extracted claims and their IDs (use the IDs in reviews.jsonl), plus frontmatter metadata."""
    try:
        claims, _ = parse_memory(memory, follow_links=follow_links)
    except (ValidationError, ValueError, OSError) as exc:
        _fail(str(exc))
    for claim in claims:
        typer.echo(_json_shield(json.dumps(claim_record(claim), ensure_ascii=False)))


@app.command("discover")
def discover_command(
    directory: Path = typer.Argument(Path("."), exists=True, file_okay=False,
                                     help="Repository or directory to search (default: the current directory)"),
    exclude: Optional[list[str]] = typer.Option(
        None, "--exclude", "-x", metavar="GLOB",
        help="Leave matching files out (fnmatch on paths relative to DIRECTORY, e.g. 'tests/fixtures/**'); "
             "repeatable"),
    fmt: Format = FORMAT,
) -> None:
    """List every supported agent's instruction files (Claude Code, Codex, Cursor, Copilot, Gemini, Windsurf).

    Reads directory listings only, never file contents."""
    try:
        report = discover(directory, exclude=exclude or ())
    except (ValueError, OSError) as exc:
        _fail(str(exc))
    _emit(discovery_to_markdown(report) if fmt is Format.markdown else report.model_dump_json(indent=2) + "\n",
          fmt is Format.markdown)


def main() -> None:
    app()
