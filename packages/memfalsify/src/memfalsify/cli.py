"""memfalsify command line. Reads memory and evidence; writes only the report you ask for."""
from __future__ import annotations

import json
from datetime import date
from enum import Enum
from pathlib import Path
from typing import Optional

import typer
from pydantic import ValidationError

from .audit import ATTENTION, audit
from .memory import parse_memory
from .report import to_markdown

app = typer.Typer(no_args_is_help=True, add_completion=False,
                  help="Audit agent memory against evidence. Never modifies memory.")


class Format(str, Enum):
    markdown = "markdown"
    json = "json"


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
) -> None:
    """Audit MEMORY.md and print an evidence-cited report with proposed (unapplied) actions."""
    try:
        reference = date.fromisoformat(as_of) if as_of else None
        report = audit(memory, evidence, reviews, as_of=reference, stale_days=stale_days, follow_links=follow_links)
    except (ValidationError, ValueError, OSError) as exc:
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
