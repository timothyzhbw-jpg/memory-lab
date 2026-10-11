"""memfalsify command line. Reads memory and evidence; writes only the report you ask for."""
from __future__ import annotations

import json
from datetime import date
from enum import Enum
from pathlib import Path
from typing import Optional

import typer
from pydantic import ValidationError

from . import jev, llm
from .audit import ATTENTION, audit
from .memory import parse_memory
from .report import to_markdown
from .verify import DualVerifier, LexicalVerifier, Verifier, VerifierError

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


def build_verifier(choice: VerifierChoice, llm_model: str, jev_model: str) -> Verifier:
    """Model backends import their heavy dependencies only when constructed."""
    if choice is VerifierChoice.lexical:
        return LexicalVerifier()
    if choice is VerifierChoice.llm:
        return llm.LLMVerifier(llm_model)
    if choice is VerifierChoice.jev:
        return jev.JevVerifier(jev_model)
    return DualVerifier(llm.LLMVerifier(llm_model), jev.JevVerifier(jev_model))


def _fail(message: str) -> None:
    typer.echo(f"error: {message}", err=True)
    raise typer.Exit(2)


@app.command("audit")
def audit_command(
    memory: Path = typer.Argument(..., exists=True, dir_okay=False, help="MEMORY.md to audit"),
    evidence: Optional[Path] = typer.Option(None, "--evidence", "-e", help="Directory of evidence files"),
    reviews: Optional[Path] = typer.Option(None, "--reviews", "-r", exists=True, dir_okay=False,
                                           help="JSONL of human-confirmed assertions"),
    fmt: Format = typer.Option(Format.markdown, "--format", "-f"),
    output: Optional[Path] = typer.Option(None, "--output", "-o", help="Write the report here instead of stdout"),
    as_of: Optional[str] = typer.Option(None, help="Reference date YYYY-MM-DD for staleness (default: today)"),
    stale_days: int = typer.Option(180, help="Dated claims older than this are flagged possibly_stale"),
    follow_links: bool = typer.Option(True, help="Also audit local .md files linked from MEMORY.md"),
    strict: bool = typer.Option(False, help="Exit 1 if any memory is challenged, conflicted or has counterevidence"),
    verifier: VerifierChoice = typer.Option(VerifierChoice.lexical, "--verifier",
                                            help="lexical (offline), llm (Claude), jev (TypeSafe Jev judgment model), "
                                                 "or dual (llm + jev; disagreements become disputed)"),
    llm_model: str = typer.Option(llm.DEFAULT_MODEL, help="Claude model for --verifier llm/dual"),
    jev_model: str = typer.Option(jev.DEFAULT_MODEL, help="TypeSafe model for --verifier jev/dual"),
    max_judged: int = typer.Option(8, min=1, help="Model verifiers: passages judged per memory (bounds API calls)"),
    min_coverage: Optional[float] = typer.Option(
        None, min=0.0, max=1.0,
        help="Share of a memory's terms a passage must contain to be judged (default 0.5 lexical, 0.2 models)"),
) -> None:
    """Audit MEMORY.md and print an evidence-cited report with proposed (unapplied) actions."""
    lexical = verifier is VerifierChoice.lexical
    try:
        reference = date.fromisoformat(as_of) if as_of else None
        report = audit(memory, evidence, reviews, verifier=build_verifier(verifier, llm_model, jev_model),
                       as_of=reference, stale_days=stale_days, follow_links=follow_links,
                       min_coverage=min_coverage if min_coverage is not None else 0.5 if lexical else 0.2,
                       max_judged=None if lexical else max_judged)
    except (ValidationError, ValueError, OSError, VerifierError) as exc:
        _fail(str(exc))
    text = to_markdown(report) if fmt is Format.markdown else report.model_dump_json(indent=2) + "\n"
    if output:
        read = {(Path(memory).resolve().parent / p).resolve() for p in report.files_read}
        if output.resolve() in read:
            _fail("refusing to overwrite a memory file with the report")
        output.write_text(text, encoding="utf-8")
        typer.echo(f"wrote {output}", err=True)
    else:
        typer.echo(text, nl=False)
    if strict and ATTENTION & report.summary.keys():
        raise typer.Exit(1)


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
