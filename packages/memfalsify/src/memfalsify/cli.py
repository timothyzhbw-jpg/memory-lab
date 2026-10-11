"""memfalsify command line. Reads memory and evidence; writes only the report you ask for."""
from __future__ import annotations

import json
from collections.abc import Iterable
from datetime import date
from enum import Enum
from pathlib import Path
from typing import Optional

import typer
from pydantic import ValidationError

from . import jev, llm
from .audit import ATTENTION, audit
from .history import diff_to_markdown, history_to_markdown, memory_diff, memory_history
from .memory import parse_memory
from .report import to_markdown
from .rollback import rollback_patch
from .verify import CascadeVerifier, DualVerifier, LexicalVerifier, Verifier, VerifierError

app = typer.Typer(no_args_is_help=True, add_completion=False,
                  help="Audit agent memory against evidence. Never modifies memory.")


class Format(str, Enum):
    markdown = "markdown"
    json = "json"


class VerifierChoice(str, Enum):
    lexical = "lexical"
    llm = "llm"
    jev = "jev"
    dual = "dual"
    cascade = "cascade"


def build_verifier(choice: VerifierChoice, llm_model: str, jev_model: str, accept_at: float = 0.8) -> Verifier:
    """Model backends import their heavy dependencies only when constructed."""
    if choice is VerifierChoice.lexical:
        return LexicalVerifier()
    if choice is VerifierChoice.llm:
        return llm.LLMVerifier(llm_model)
    if choice is VerifierChoice.jev:
        return jev.JevVerifier(jev_model)
    if choice is VerifierChoice.cascade:
        return CascadeVerifier(jev.JevVerifier(jev_model), llm.LLMVerifier(llm_model), accept_at)
    return DualVerifier(llm.LLMVerifier(llm_model), jev.JevVerifier(jev_model))


MAX_JUDGED = 8


def preselection(choice: VerifierChoice, min_coverage: float | None = None, max_judged: int = MAX_JUDGED) -> dict:
    """audit() passage limits: lexical judges every passage covering half the terms, models a bounded few."""
    lexical = choice is VerifierChoice.lexical
    return {"min_coverage": min_coverage if min_coverage is not None else 0.5 if lexical else 0.2,
            "max_judged": None if lexical else max_judged}


def concurrency(choice: VerifierChoice, max_concurrency: int) -> int:
    """Model verifiers wait on the network, so they run requests in parallel; lexical judging stays serial."""
    return 1 if choice is VerifierChoice.lexical else max_concurrency


def _fail(message: str) -> None:
    typer.echo(f"error: {message}", err=True)
    raise typer.Exit(2)


def _write(output: Path, data: bytes, protected: Iterable[Path], what: str = "report") -> None:
    """Write exact bytes (no newline translation); never over a memory file."""
    if output.resolve() in {Path(p).resolve() for p in protected}:
        _fail(f"refusing to overwrite a memory file with the {what}")
    output.write_bytes(data)
    typer.echo(f"wrote {output}", err=True)


# Options shared by `audit` and `diff`.
MEMORY_ARG = typer.Argument(..., exists=True, dir_okay=False, help="MEMORY.md to audit")
EVIDENCE = typer.Option(None, "--evidence", "-e", help="Directory of evidence files")
EXCLUDE = typer.Option(
    None, "--exclude", "-x", metavar="GLOB",
    help="Leave matching evidence files out (fnmatch on paths relative to --evidence, e.g. 'examples/**'); "
         "repeatable")
REVIEWS = typer.Option(None, "--reviews", "-r", exists=True, dir_okay=False, help="JSONL of human-confirmed assertions")
FORMAT = typer.Option(Format.markdown, "--format", "-f")
OUTPUT = typer.Option(None, "--output", "-o", help="Write the report here instead of stdout")
AS_OF = typer.Option(None, help="Reference date YYYY-MM-DD for staleness (default: today)")
STALE_DAYS = typer.Option(180, help="Dated claims older than this are flagged possibly_stale")
FOLLOW_LINKS = typer.Option(True, help="Also audit local .md files linked from MEMORY.md")
VERIFIER = typer.Option(VerifierChoice.lexical, "--verifier",
                        help="lexical (offline), llm (Claude), jev (TypeSafe Jev judgment model), "
                             "dual (llm + jev; disagreements become disputed), "
                             "or cascade (jev, escalating unsure answers to llm)")
LLM_MODEL = typer.Option(llm.DEFAULT_MODEL, help="Claude model for --verifier llm/dual/cascade")
JEV_MODEL = typer.Option(jev.DEFAULT_MODEL, help="TypeSafe model for --verifier jev/dual/cascade")
ACCEPT_AT = typer.Option(0.8, min=0.0, max=1.0, help="--verifier cascade: accept Jev at this confidence, else ask the LLM")
MAX_JUDGED_OPTION = typer.Option(MAX_JUDGED, min=1, help="Model verifiers: passages judged per memory (bounds API calls)")
MAX_CONCURRENCY = typer.Option(4, min=1, help="Model verifiers: API requests in flight at once (results keep "
                                                "their order)")
MIN_COVERAGE = typer.Option(
    None, min=0.0, max=1.0,
    help="Share of a memory's terms a passage must contain to be judged (default 0.5 lexical, 0.2 models)")


@app.command("audit")
def audit_command(
    memory: Path = MEMORY_ARG,
    evidence: Optional[Path] = EVIDENCE,
    exclude: Optional[list[str]] = EXCLUDE,
    reviews: Optional[Path] = REVIEWS,
    fmt: Format = FORMAT,
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
) -> None:
    """Audit MEMORY.md and print an evidence-cited report with proposed (unapplied) actions."""
    try:
        reference = date.fromisoformat(as_of) if as_of else None
        judge = build_verifier(verifier, llm_model, jev_model, accept_at)
        report = audit(memory, evidence, reviews, verifier=judge, as_of=reference, stale_days=stale_days,
                       follow_links=follow_links, exclude=exclude or (),
                       max_concurrency=concurrency(verifier, max_concurrency),
                       **preselection(verifier, min_coverage, max_judged))
    except (ValidationError, ValueError, OSError, VerifierError) as exc:
        _fail(str(exc))
    text = to_markdown(report) if fmt is Format.markdown else report.model_dump_json(indent=2) + "\n"
    if output:
        _write(output, text.encode("utf-8"), (Path(memory).resolve().parent / p for p in report.files_read))
    else:
        typer.echo(text, nl=False)
    if strict and ATTENTION & report.summary.keys():
        raise typer.Exit(1)


@app.command("diff")
def diff_command(
    memory: Path = typer.Argument(..., exists=True, dir_okay=False, help="MEMORY.md in a git work tree"),
    base: str = typer.Option(..., "--base", help="Revision to compare against (branch, tag or commit), "
                                                 "e.g. main or the pull request's base commit"),
    head: Optional[str] = typer.Option(None, "--head", help="Revision to compare (default: the working tree)"),
    evidence: Optional[Path] = EVIDENCE,
    exclude: Optional[list[str]] = EXCLUDE,
    reviews: Optional[Path] = REVIEWS,
    fmt: Format = FORMAT,
    output: Optional[Path] = OUTPUT,
    as_of: Optional[str] = AS_OF,
    stale_days: int = STALE_DAYS,
    follow_links: bool = typer.Option(True, help="Also compare local .md files linked from MEMORY.md"),
    strict: bool = typer.Option(False, help="Exit 1 if an added or changed memory is challenged, conflicted, "
                                            "disputed or has counterevidence"),
    verifier: VerifierChoice = VERIFIER,
    llm_model: str = LLM_MODEL,
    jev_model: str = JEV_MODEL,
    accept_at: float = ACCEPT_AT,
    max_judged: int = MAX_JUDGED_OPTION,
    min_coverage: Optional[float] = MIN_COVERAGE,
    max_concurrency: int = MAX_CONCURRENCY,
) -> None:
    """Show which claims changed between two revisions and audit only the added and changed ones."""
    try:
        reference = date.fromisoformat(as_of) if as_of else None
        judge = build_verifier(verifier, llm_model, jev_model, accept_at)
        report = memory_diff(memory, base, head, follow_links=follow_links, evidence_root=evidence, reviews=reviews,
                             verifier=judge, as_of=reference, stale_days=stale_days, exclude=exclude or (),
                             max_concurrency=concurrency(verifier, max_concurrency),
                             **preselection(verifier, min_coverage, max_judged))
    except (ValidationError, ValueError, OSError, VerifierError) as exc:
        _fail(str(exc))
    text = diff_to_markdown(report) if fmt is Format.markdown else report.model_dump_json(indent=2) + "\n"
    if output:
        root = Path(memory).resolve().parent
        _write(output, text.encode("utf-8"), (root / p for p in report.base.files + report.head.files))
    else:
        typer.echo(text, nl=False)
    if strict and report.attention:
        raise typer.Exit(1)


@app.command("history")
def history_command(
    memory: Path = typer.Argument(..., exists=True, dir_okay=False, help="MEMORY.md in a git work tree"),
    max_commits: int = typer.Option(100, min=1, help="Read at most this many commits that touched the memory"),
    fmt: Format = FORMAT,
    follow_links: bool = typer.Option(True, help="Also trace local .md files linked from MEMORY.md"),
) -> None:
    """For each claim, the commits that introduced and last changed it; plus the claims removed, and when."""
    try:
        report = memory_history(memory, max_commits=max_commits, follow_links=follow_links)
    except (ValidationError, ValueError, OSError) as exc:
        _fail(str(exc))
    typer.echo(history_to_markdown(report) if fmt is Format.markdown else report.model_dump_json(indent=2) + "\n",
               nl=False)


@app.command("rollback")
def rollback_command(
    memory: Path = typer.Argument(..., exists=True, dir_okay=False, help="MEMORY.md in a git work tree"),
    claim: str = typer.Option(..., "--claim", help="Claim ID (from `claims`, `diff` or `history`)"),
    to: str = typer.Option(..., "--to", help="Revision whose wording of the claim to restore"),
    output: Optional[Path] = typer.Option(None, "--output", "-o", help="Write the patch here instead of stdout"),
    follow_links: bool = typer.Option(True, help="Also look in local .md files linked from MEMORY.md"),
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
    memory: Path = typer.Argument(..., exists=True, dir_okay=False),
    follow_links: bool = typer.Option(True),
) -> None:
    """List extracted claims and their IDs (use the IDs in reviews.jsonl)."""
    claims, _ = parse_memory(memory, follow_links=follow_links)
    for claim in claims:
        typer.echo(json.dumps({"memory_id": claim.id, "location": claim.location, "text": claim.text},
                              ensure_ascii=False))


def main() -> None:
    app()
