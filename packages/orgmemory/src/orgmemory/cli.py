"""orgmemory command line. Replay, explain, validate, audit and changes are read-only; `export` creates
files only outside the dataset; every other command only appends."""
from __future__ import annotations

import json
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Optional

import typer
from pydantic import AwareDatetime, TypeAdapter, ValidationError

from memcore import EvidenceError, ManifestEntry, append_jsonl, load_manifest, locate_quote
from memcore.jsonutil import loads_at

from memcore.cli import SafeTyperGroup
from memcore.display import one_line as terminal_text
from memcore.display import json_shield as _json_shield

from .audit import DEFAULT_STALE_DAYS, audit
from .authority import POLICY_LOG
from .changes import changes
from .export import adr_markdown, create_files, decision_records, decisions_csv
from .htmlreport import audit_html, replay_html
from .models import Event, PolicyChange
from .replay import Dataset, replay, validation_summary
from .report import audit_markdown, changes_markdown, explain, to_markdown

app = typer.Typer(cls=SafeTyperGroup, no_args_is_help=True, add_completion=False,
                  help="Replay organizational decisions under explicit authority rules.")
evidence_app = typer.Typer(no_args_is_help=True, help="Manage the evidence manifest.")
app.add_typer(evidence_app, name="evidence")
policy_app = typer.Typer(no_args_is_help=True,
                         help="Record effective-dated role changes in policy-log.jsonl (append-only).")
app.add_typer(policy_app, name="policy")

DATASET = typer.Argument(..., exists=True, file_okay=False,
                         help="Directory with events.jsonl, policy.json, evidence.jsonl and optionally policy-log.jsonl")


class Format(str, Enum):
    markdown = "markdown"
    json = "json"
    html = "html"


FORMAT = typer.Option(Format.markdown, "--format", "-f",
                      help="markdown, json, or html (one self-contained page: inline CSS, no scripts, nothing loaded)")


class TextFormat(str, Enum):
    """`changes` has no HTML page."""
    markdown = "markdown"
    json = "json"


def _fail(message: str) -> None:
    typer.echo(f"error: {terminal_text(message)}", err=True)
    raise typer.Exit(2)


def _echo_exact(text: str) -> None:
    """UTF-8 report bytes. Windows text-mode stdout would otherwise turn LF into CRLF."""
    typer.echo(text.encode("utf-8"), nl=False)


def _as_of(value: Optional[str], flag: str = "--as-of") -> Optional[datetime]:
    return _moment(value, flag) if value is not None else None


def _load(dataset: Path) -> Dataset:
    try:
        return Dataset.load(dataset)
    except (ValidationError, ValueError, OSError) as exc:
        _fail(str(exc))


@app.command("replay")
def replay_command(
    dataset: Path = DATASET,
    as_of: Optional[str] = typer.Option(None, help="Ignore events after this time (ISO 8601 with timezone)"),
    fmt: Format = FORMAT,
) -> None:
    """Rebuild current state from the event log."""
    data = _load(dataset)
    try:
        result = replay(data, _as_of(as_of))
    except ValidationError as exc:
        _fail(str(exc))
    if fmt is Format.html:
        _echo_exact(replay_html(result))
    else:
        _echo_exact((to_markdown(result) if fmt is Format.markdown
                     else _json_shield(result.model_dump_json(indent=2))) + "\n")


@app.command("explain")
def explain_command(dataset: Path = DATASET, subject: str = typer.Argument(...),
                    as_of: Optional[str] = typer.Option(None)) -> None:
    """Show why a subject is in its current state, with the decision's evidence."""
    result = replay(_load(dataset), _as_of(as_of))
    if subject not in result.states:
        _fail(f"unknown subject {subject!r}; known: {', '.join(result.states)}")
    _echo_exact(explain(result, subject) + "\n")


@app.command("audit")
def audit_command(
    dataset: Path = DATASET,
    as_of: Optional[str] = typer.Option(
        None, help="Ignore events after this time and measure staleness at it (ISO 8601 with timezone; "
                   "default: whole log, staleness measured now)"),
    stale_days: int = typer.Option(DEFAULT_STALE_DAYS, min=0,
                                   help="Report pending approvals and reconsiderations open longer than this"),
    fmt: Format = FORMAT,
    strict: bool = typer.Option(False, help="Exit 1 if any finding has high severity"),
) -> None:
    """Report where the decision process needs attention, with the evidence of every event involved."""
    data = _load(dataset)
    try:
        report = audit(data, _as_of(as_of), stale_days)
    except ValidationError as exc:
        _fail(str(exc))
    if fmt is Format.json:
        _echo_exact(_json_shield(report.model_dump_json(indent=2) + "\n"))
    elif fmt is Format.html:
        _echo_exact(audit_html(report))
    else:
        _echo_exact(audit_markdown(report))
    if strict and report.summary["high"]:
        raise typer.Exit(1)


def _moment(value: str, flag: str) -> datetime:
    try:
        return TypeAdapter(AwareDatetime).validate_python(value)
    except ValidationError:
        _fail(f"{flag} must be an ISO 8601 time with a timezone; expected a datetime like 2026-10-07T23:59:59Z")


@app.command("changes")
def changes_command(
    dataset: Path = DATASET,
    start: str = typer.Option(..., "--from", help="Start of the period: events after this time are in it "
                                                  "(ISO 8601 with timezone)"),
    end: str = typer.Option(..., "--to", help="End of the period, inclusive (ISO 8601 with timezone)"),
    fmt: TextFormat = typer.Option(TextFormat.markdown, "--format", "-f"),
) -> None:
    """Report what changed between two times, per subject, with the evidence of every event involved."""
    start_time, end_time = _moment(start, "--from"), _moment(end, "--to")
    if start_time > end_time:
        _fail("--from must not be after --to")
    data = _load(dataset)
    try:
        report = changes(data, start_time, end_time)
    except (ValidationError, ValueError) as exc:
        _fail(str(exc))
    _echo_exact(changes_markdown(report) if fmt is TextFormat.markdown
                else _json_shield(report.model_dump_json(indent=2) + "\n"))


class ExportFormat(str, Enum):
    adr = "adr"
    csv = "csv"


@app.command("export")
def export_command(
    dataset: Path = DATASET,
    fmt: ExportFormat = typer.Option(ExportFormat.adr, "--format", "-f",
                                     help="adr: one Markdown decision record per file; csv: one table"),
    out: Optional[Path] = typer.Option(
        None, "--out", "-o",
        help="adr: directory to create the records in (required); csv: file to create (default: stdout). "
             "Must be outside the dataset"),
    as_of: Optional[str] = typer.Option(None, help="Ignore events after this time (ISO 8601 with timezone)"),
    force: bool = typer.Option(False, "--force", help="Replace existing files of the same name instead of refusing"),
) -> None:
    """Export each subject's current authorized decision as an ADR, or all of them as one CSV table."""
    if fmt is ExportFormat.adr and out is None:
        _fail("--format adr needs --out DIR")
    data = _load(dataset)
    try:
        cutoff = _as_of(as_of)
        subjects, records = decision_records(data, cutoff)
    except (ValidationError, ValueError) as exc:
        _fail(str(exc))
    when = cutoff.isoformat() if cutoff else "end of log"
    if fmt is ExportFormat.csv:
        text = decisions_csv(records)
        if out is None:
            _echo_exact(text)
            return
        files, directory = {out.name: text.encode("utf-8")}, out.parent
    elif not records:
        typer.echo(f"no subject has an authorized decision as of {when}; nothing written")
        return
    else:
        files, directory = {r.file_name: adr_markdown(r).encode("utf-8") for r in records}, out
    try:
        written = create_files(dataset, directory, files, force=force)
    except (ValueError, OSError) as exc:
        _fail(str(exc))
    for path in written:
        typer.echo(f"wrote {terminal_text(str(path))}")
    typer.echo(f"exported {len(records)} decision(s) of {subjects} subject(s), as of {when}; subjects without an "
               "authorized decision are left out")


@app.command("validate")
def validate_command(dataset: Path = DATASET) -> None:
    """Validate every event, the policy, the policy log and every evidence hash and span."""
    _echo_exact("\n".join(validation_summary(_load(dataset))) + "\n")


@app.command("append")
def append_command(dataset: Path = DATASET,
                   event: str = typer.Option(..., help="Event as a JSON object")) -> None:
    """Validate one new event against the existing log and append it. Existing lines are never rewritten."""
    data = _load(dataset)
    try:
        try:
            new = Event.model_validate(loads_at("event", event))
        except RecursionError as exc:
            raise ValueError("event: invalid JSON: too deeply nested") from exc
        Dataset(data.events + [new], data.policy, data.evidence, data.policy_log)
    except (ValidationError, ValueError) as exc:
        _fail(str(exc))
    append_jsonl(dataset / "events.jsonl", new.model_dump(mode="json", exclude_none=True))
    typer.echo(f"appended {terminal_text(new.id)}")


def _record_role_change(dataset: Path, action: str, id: str, timestamp: str, actor: str, subject_actor: str,
                        role: str, evidence_id: list[str], note: Optional[str]) -> None:
    data = _load(dataset)
    try:
        change = PolicyChange.model_validate({
            "id": id, "timestamp": timestamp, "actor": actor, "action": action, "subject_actor": subject_actor,
            "role": role, "evidence_ids": evidence_id, "note": note})
        # The whole log is checked again, so a backdated entry cannot invalidate a later one.
        Dataset(data.events, data.policy, data.evidence, [*(data.policy_log or []), change])
    except (ValidationError, ValueError) as exc:
        _fail(str(exc))
    append_jsonl(dataset / POLICY_LOG, change.model_dump(mode="json", exclude_none=True))
    typer.echo(f"appended {terminal_text(change.id)} to {POLICY_LOG}")


ROLE_CHANGE_OPTIONS = dict(
    id=typer.Option(..., help="Unique ID, distinct from every event ID"),
    timestamp=typer.Option(..., help="When the change takes effect (ISO 8601 with timezone)"),
    actor=typer.Option(..., help="Who makes the change; must hold a role permitted to manage_roles at that time"),
    subject_actor=typer.Option(..., help="Whose roles change"),
    role=typer.Option(..., help="A role policy.json names"),
    evidence_id=typer.Option(..., "--evidence-id", help="Evidence ID backing the change (repeatable)"),
    note=typer.Option(None, help="Optional free-text note"),
)


@policy_app.command("grant")
def policy_grant(dataset: Path = DATASET, id: str = ROLE_CHANGE_OPTIONS["id"],
                 timestamp: str = ROLE_CHANGE_OPTIONS["timestamp"], actor: str = ROLE_CHANGE_OPTIONS["actor"],
                 subject_actor: str = ROLE_CHANGE_OPTIONS["subject_actor"], role: str = ROLE_CHANGE_OPTIONS["role"],
                 evidence_id: list[str] = ROLE_CHANGE_OPTIONS["evidence_id"],
                 note: Optional[str] = ROLE_CHANGE_OPTIONS["note"]) -> None:
    """Grant a role from an effective time on: validate against the whole policy log, then append."""
    _record_role_change(dataset, "grant", id, timestamp, actor, subject_actor, role, evidence_id, note)


@policy_app.command("revoke")
def policy_revoke(dataset: Path = DATASET, id: str = ROLE_CHANGE_OPTIONS["id"],
                  timestamp: str = ROLE_CHANGE_OPTIONS["timestamp"], actor: str = ROLE_CHANGE_OPTIONS["actor"],
                  subject_actor: str = ROLE_CHANGE_OPTIONS["subject_actor"], role: str = ROLE_CHANGE_OPTIONS["role"],
                  evidence_id: list[str] = ROLE_CHANGE_OPTIONS["evidence_id"],
                  note: Optional[str] = ROLE_CHANGE_OPTIONS["note"]) -> None:
    """Remove a role from an effective time on (a revoke_role entry). Earlier events keep their outcome."""
    _record_role_change(dataset, "revoke_role", id, timestamp, actor, subject_actor, role, evidence_id, note)


@evidence_app.command("add")
def evidence_add(
    dataset: Path = DATASET,
    id: str = typer.Option(...),
    path: str = typer.Option(..., help="Source file, relative to the dataset directory"),
    quote: str = typer.Option(..., help="Exact text to cite; must appear verbatim in the file (in a transcript "
                                        "or OCR sidecar, inside one cue or line)"),
    source_uri: str = typer.Option(...),
    source_kind: str = typer.Option(...),
    captured_at: str = typer.Option(..., help="ISO 8601 with timezone"),
    locator: Optional[str] = typer.Option(
        None, help="Transcript or OCR sidecar only: the cue or line to quote from, e.g. 't=12.5,17' or "
                   "'page=1&xywh=40,120,570,38' (default: the first one containing the quote)"),
) -> None:
    """Hash a source file, locate the exact quote, and append the record to evidence.jsonl.

    For a transcript (.vtt, .srt) or OCR sidecar (.ocr.json), the record also gets the cue's or line's locator,
    and the sidecar must match the media file next to it."""
    manifest = dataset / "evidence.jsonl"
    try:
        existing = load_manifest(manifest) if manifest.exists() else {}
        if id in existing:
            raise EvidenceError(f"evidence id already exists: {id}")
        span = locate_quote(dataset, path, quote, locator)
        entry = ManifestEntry(id=id, path=span.path, sha256=span.sha256, quote=span.quote, start=span.start,
                              end=span.end, source_uri=source_uri, source_kind=source_kind,
                              captured_at=captured_at, locator=span.media.locator if span.media else None)
    except (ValidationError, ValueError, OSError) as exc:
        _fail(str(exc))
    append_jsonl(manifest, entry.model_dump(mode="json"))
    where = span.location if span.media is None else f"{span.citation} ({span.media.label} {span.location})"
    typer.echo(terminal_text(f"registered {id} at {where}"))


github_app = typer.Typer(no_args_is_help=True, help="Ingest GitHub issues and pull requests.")
app.add_typer(github_app, name="github")


def _github_result(summary, dry_run: bool) -> None:
    for line in summary.lines():
        typer.echo(line)
    if dry_run:
        for event in summary.plan.events:
            typer.echo(_json_shield(json.dumps(event.model_dump(mode="json", exclude_none=True), ensure_ascii=True)))
    if not summary.complete:
        raise typer.Exit(3)


def _github_fail(exc: Exception) -> None:
    # Validation errors may embed raw API text or malformed existing records.
    # Only known importer constants, and duplicate-key errors that name a path and
    # line but not the rejected document, are safe to display.
    from .github_fetch import GitHubError
    message = str(exc)
    safe = {"another sync is running; delete the lock if not", "repository identity changed",
            "a cursor already exists; use --full or --issue", "save-capture path already exists",
            "invalid sync options: --full, --since and --issue cannot be combined",
            "content-addressed capture tamper conflict"}
    duplicate = message.endswith(": invalid JSON: duplicate key") and not any(
        ord(char) < 32 or ord(char) == 127 for char in message)
    _fail(message if message in safe or duplicate or isinstance(exc, GitHubError)
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
            summary = run_sync(dataset, repo, client, full=full, since=_as_of(since, "--since"), issues=issue or [],
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


jira_app = typer.Typer(no_args_is_help=True, help="Import Jira Cloud issues from a saved search capture.")
app.add_typer(jira_app, name="jira")


@jira_app.command("convert")
def jira_convert(dataset: Path = DATASET,
                 capture_json: Path = typer.Argument(..., exists=True, dir_okay=False,
                                                     help="Saved GET /rest/api/3/search?expand=changelog pages"),
                 dry_run: bool = typer.Option(False, help="Print planned JSONL without writing anything")) -> None:
    """Apply a saved Jira Cloud search capture offline. Exit 3 when the capture is incomplete."""
    from .ingest import LOCK_HELD
    from .jira_apply import run_convert
    from .jira_map import JiraCaptureError
    try:
        summary = run_convert(dataset, capture_json, dry_run=dry_run)
    except JiraCaptureError as exc:
        _fail(str(exc))
    except (ValueError, OSError, RecursionError) as exc:
        # Other messages may embed capture or dataset text; only importer constants are shown.
        safe = {LOCK_HELD, "content-addressed capture tamper conflict"}
        _fail(str(exc) if str(exc) in safe else "Jira import failed: invalid input, dataset or filesystem")
    for line in summary.lines():
        typer.echo(line)
    if dry_run:
        for event in summary.plan.events:
            typer.echo(json.dumps(event.model_dump(mode="json", exclude_none=True), ensure_ascii=True))
    if not summary.plan.complete:
        raise typer.Exit(3)


def main() -> None:
    app()
