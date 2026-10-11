"""orgmemory command line. Replay and audit are read-only; `append` and `evidence add` only ever append lines."""
from __future__ import annotations

import json
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Optional

import typer
from pydantic import AwareDatetime, TypeAdapter, ValidationError

from memcore import EvidenceError, ManifestEntry, append_jsonl, load_json, load_manifest, locate_quote

from .audit import DEFAULT_STALE_DAYS, audit
from .models import Event
from .replay import Dataset, replay, validate
from .report import audit_markdown, explain, json_shield, terminal_line, to_markdown

app = typer.Typer(no_args_is_help=True, add_completion=False,
                  help="Replay organizational decisions under explicit authority rules.")
evidence_app = typer.Typer(no_args_is_help=True, help="Manage the evidence manifest.")
app.add_typer(evidence_app, name="evidence")

DATASET = typer.Argument(..., exists=True, file_okay=False, help="Directory with events.jsonl, policy.json, evidence.jsonl")


class Format(str, Enum):
    markdown = "markdown"
    json = "json"


def _fail(message: str) -> None:
    flat = "".join(ch if ch.isprintable() else "\ufffd" for ch in " ".join(message.split()))
    typer.echo(f"error: {flat}", err=True)
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
    typer.echo(to_markdown(result) if fmt is Format.markdown else json_shield(result.model_dump_json(indent=2)))


@app.command("explain")
def explain_command(dataset: Path = DATASET, subject: str = typer.Argument(...),
                    as_of: Optional[str] = typer.Option(None)) -> None:
    """Show why a subject is in its current state, with the decision's evidence."""
    result = replay(_load(dataset), _as_of(as_of))
    if subject not in result.states:
        _fail(f"unknown subject {subject!r}; known: {', '.join(result.states)}")
    typer.echo(explain(result, subject))


@app.command("audit")
def audit_command(
    dataset: Path = DATASET,
    as_of: Optional[str] = typer.Option(
        None, help="Ignore events after this time and measure staleness at it (ISO 8601 with timezone; "
                   "default: whole log, staleness measured now)"),
    stale_days: int = typer.Option(DEFAULT_STALE_DAYS, min=0,
                                   help="Report pending approvals and reconsiderations open longer than this"),
    fmt: Format = typer.Option(Format.markdown, "--format", "-f"),
    strict: bool = typer.Option(False, help="Exit 1 if any finding has high severity"),
) -> None:
    """Report where the decision process needs attention, with the evidence of every event involved."""
    data = _load(dataset)
    try:
        report = audit(data, _as_of(as_of), stale_days)
    except ValidationError as exc:
        _fail(str(exc))
    text = audit_markdown(report) if fmt is Format.markdown else json_shield(report.model_dump_json(indent=2) + "\n")
    typer.echo(text, nl=False)
    if strict and report.summary["high"]:
        raise typer.Exit(1)


@app.command("validate")
def validate_command(dataset: Path = DATASET) -> None:
    """Validate every event, the policy and every evidence hash and span."""
    data = _load(dataset)
    unknown = sorted({e.actor for e in data.events} - data.policy.actors.keys())
    typer.echo(f"ok: {len(data.events)} events, {len(data.evidence)} evidence records")
    if unknown:
        shown = ", ".join(terminal_line(actor) for actor in unknown)
        typer.echo(f"note: actors without any role (their events cannot change state): {shown}")


@app.command("append")
def append_command(dataset: Path = DATASET,
                   event: str = typer.Option(..., help="Event as a JSON object")) -> None:
    """Validate one new event against the existing log and append it. Existing lines are never rewritten."""
    data = _load(dataset)
    try:
        try:
            new = Event.model_validate(load_json(event))
        except RecursionError as exc:
            raise ValueError("invalid JSON: too deeply nested") from exc
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


github_app = typer.Typer(no_args_is_help=True, help="Ingest GitHub issues and pull requests.")
app.add_typer(github_app, name="github")


def _github_result(summary, dry_run: bool) -> None:
    for line in summary.lines():
        typer.echo(line)
    if dry_run:
        for event in summary.plan.events:
            typer.echo(json_shield(json.dumps(event.model_dump(mode="json", exclude_none=True), ensure_ascii=True)))
    if not summary.complete:
        raise typer.Exit(3)


def _github_fail(exc: Exception) -> None:
    # Validation errors may embed raw API text or malformed existing records.
    # Only known importer constants are safe to display.
    from .github_fetch import GitHubError
    safe = {"another sync is running; delete the lock if not", "repository identity changed",
            "a cursor already exists; use --full or --issue", "save-capture path already exists",
            "invalid sync options: --full, --since and --issue cannot be combined",
            "content-addressed capture tamper conflict"}
    _fail(str(exc) if str(exc) in safe or isinstance(exc, GitHubError)
          else "GitHub ingestion failed: invalid input, dataset or filesystem; nothing appended")


@github_app.command("sync")
def github_sync(
    dataset: Path = DATASET,
    repo: str = typer.Argument(..., help="GitHub OWNER/REPO"),
    full: bool = typer.Option(False, help="Rescan every issue and pull request"),
    since: Optional[str] = typer.Option(None, help="Bound a first run (aware ISO 8601; only without a cursor)"),
    issue: Optional[list[int]] = typer.Option(None, "--issue", min=1, help="Refresh this number (repeatable)"),
    max_wait: float = typer.Option(3600, min=0, help="Maximum single rate-limit sleep, in seconds"),
    save_capture: Optional[Path] = typer.Option(None, help="Create a raw capture file; may contain private text"),
    dry_run: bool = typer.Option(False, help="Fetch and print planned JSONL without writing the dataset"),
) -> None:
    """Fetch GitHub activity and append deterministic events and evidence."""
    from . import github_fetch
    from .github_apply import run_sync
    try:
        token = github_fetch.token_from_env()
        if not token:
            typer.echo("warning: unauthenticated GitHub access (public repos only, 60 requests/hour)", err=True)
        if save_capture is not None:
            typer.echo("warning: saved capture contains raw and possibly private GitHub content", err=True)
        transport = github_fetch.PersistentTransport()
        try:
            client = github_fetch.Client(transport, token, max_wait=max_wait, progress=github_fetch.FetchProgress())
            summary = run_sync(dataset, repo, client, full=full, since=_as_of(since), issues=issue or [],
                               save_capture=save_capture, dry_run=dry_run)
        finally:
            # Plain Transport fakes need not implement resource management.
            close = getattr(transport, "close", None)
            if close is not None:
                close()
    except (github_fetch.GitHubError, ValueError, OSError) as exc:
        _github_fail(exc)
    _github_result(summary, dry_run)


@github_app.command("convert")
def github_convert(dataset: Path = DATASET,
                   capture_json: Path = typer.Argument(..., exists=True, dir_okay=False),
                   dry_run: bool = typer.Option(False, help="Print planned JSONL without writing anything")) -> None:
    """Apply a previously saved GitHub capture offline."""
    from .github_apply import run_convert
    try:
        summary = run_convert(dataset, capture_json, dry_run=dry_run)
    except (ValueError, OSError) as exc:
        _github_fail(exc)
    _github_result(summary, dry_run)


def main() -> None:
    app()
