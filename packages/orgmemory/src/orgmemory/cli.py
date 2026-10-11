"""orgmemory command line. Replay is read-only; `append` and `evidence add` only ever append lines."""
from __future__ import annotations

import json
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Optional

import typer
from pydantic import AwareDatetime, TypeAdapter, ValidationError

from memcore import EvidenceError, ManifestEntry, append_jsonl, load_manifest, locate_quote

from .models import Event
from .replay import Dataset, replay, validate
from .report import explain, to_markdown

app = typer.Typer(no_args_is_help=True, add_completion=False,
                  help="Replay organizational decisions under explicit authority rules.")
evidence_app = typer.Typer(no_args_is_help=True, help="Manage the evidence manifest.")
app.add_typer(evidence_app, name="evidence")

DATASET = typer.Argument(..., exists=True, file_okay=False, help="Directory with events.jsonl, policy.json, evidence.jsonl")


class Format(str, Enum):
    markdown = "markdown"
    json = "json"


def _fail(message: str) -> None:
    typer.echo(f"error: {message}", err=True)
    raise typer.Exit(2)


def _as_of(value: Optional[str]) -> Optional[datetime]:
    return TypeAdapter(AwareDatetime).validate_python(value) if value else None


def _load(dataset: Path) -> Dataset:
    try:
        return Dataset.load(dataset)
    except (ValidationError, ValueError, OSError) as exc:
        _fail(str(exc))


@app.command("replay")
def replay_command(
    dataset: Path = DATASET,
    as_of: Optional[str] = typer.Option(None, help="Ignore events after this time (ISO 8601 with timezone)"),
    fmt: Format = typer.Option(Format.markdown, "--format", "-f"),
) -> None:
    """Rebuild current state from the event log."""
    data = _load(dataset)
    try:
        result = replay(data, _as_of(as_of))
    except ValidationError as exc:
        _fail(str(exc))
    typer.echo(to_markdown(result) if fmt is Format.markdown else result.model_dump_json(indent=2))


@app.command("explain")
def explain_command(dataset: Path = DATASET, subject: str = typer.Argument(...),
                    as_of: Optional[str] = typer.Option(None)) -> None:
    """Show why a subject is in its current state, with the decision's evidence."""
    result = replay(_load(dataset), _as_of(as_of))
    if subject not in result.states:
        _fail(f"unknown subject {subject!r}; known: {', '.join(result.states)}")
    typer.echo(explain(result, subject))


@app.command("validate")
def validate_command(dataset: Path = DATASET) -> None:
    """Validate every event, the policy and every evidence hash and span."""
    data = _load(dataset)
    unknown = sorted({e.actor for e in data.events} - data.policy.actors.keys())
    typer.echo(f"ok: {len(data.events)} events, {len(data.evidence)} evidence records")
    if unknown:
        typer.echo(f"note: actors without any role (their events cannot change state): {', '.join(unknown)}")


@app.command("append")
def append_command(dataset: Path = DATASET,
                   event: str = typer.Option(..., help="Event as a JSON object")) -> None:
    """Validate one new event against the existing log and append it. Existing lines are never rewritten."""
    data = _load(dataset)
    try:
        new = Event.model_validate(json.loads(event))
        validate(data.events + [new], data.evidence)
    except (ValidationError, ValueError) as exc:
        _fail(str(exc))
    append_jsonl(dataset / "events.jsonl", new.model_dump(mode="json", exclude_none=True))
    typer.echo(f"appended {new.id}")


@evidence_app.command("add")
def evidence_add(
    dataset: Path = DATASET,
    id: str = typer.Option(...),
    path: str = typer.Option(..., help="Source file, relative to the dataset directory"),
    quote: str = typer.Option(..., help="Exact text to cite; must appear verbatim in the file"),
    source_uri: str = typer.Option(...),
    source_kind: str = typer.Option(...),
    captured_at: str = typer.Option(..., help="ISO 8601 with timezone"),
) -> None:
    """Hash a source file, locate the exact quote, and append the record to evidence.jsonl."""
    manifest = dataset / "evidence.jsonl"
    try:
        existing = load_manifest(manifest) if manifest.exists() else {}
        if id in existing:
            raise EvidenceError(f"evidence id already exists: {id}")
        span = locate_quote(dataset, path, quote)
        entry = ManifestEntry(id=id, path=span.path, sha256=span.sha256, quote=span.quote, start=span.start,
                              end=span.end, source_uri=source_uri, source_kind=source_kind,
                              captured_at=captured_at)
    except (ValidationError, ValueError, OSError) as exc:
        _fail(str(exc))
    append_jsonl(manifest, entry.model_dump(mode="json"))
    typer.echo(f"registered {id} at {span.location}")


def main() -> None:
    app()
