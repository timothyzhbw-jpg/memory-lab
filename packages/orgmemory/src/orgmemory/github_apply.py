"""Policy-blind, append-only GitHub ingestion with a single-writer lock."""
from __future__ import annotations

import json
from collections import Counter
from contextlib import AbstractContextManager, nullcontext
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

from memcore import EvidenceSpan, ManifestEntry, append_jsonl, load_manifest, read_jsonl
from memcore.evidence import verify_entry
from memcore.jsonutil import loads_at
from memcore.reading import read_file_bytes

from .authority import POLICY_LOG
from .github_fetch import Client, fetch
from .github_map import GithubConfig, Plan, _int, _iso, _lower, _repo_ok, _ts, convert
from .ingest import create_only as _create
from .ingest import dataset_path, importer_lock, policy_alias
from .models import Event
from .replay import load_events, validate


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _refuse_policy_alias(dataset: Path, path: Path) -> None:
    if name := policy_alias(dataset, path):
        raise ValueError(f"GitHub importer cannot open {name}")


def _path(dataset: Path, relative: str) -> Path:
    """Refuse policy aliases as well as escapes, before opening anything."""
    return dataset_path(dataset, relative, "GitHub")


def lock(dataset: Path) -> AbstractContextManager[None]:
    """The GitHub importer's lock; it also waits for no other importer (see `ingest.importer_lock`)."""
    return importer_lock(dataset, ".github-sync.lock", "GitHub")


@dataclass
class Existing:
    events: list[Event]
    spans: dict[str, EvidenceSpan]
    sync_log: list[dict]


def _check_log(line: dict) -> None:
    repo = line.get("repo")
    if not isinstance(repo, str) or repo != _lower(repo) or not _repo_ok(repo):
        raise ValueError("invalid GitHub sync log repo")
    _int(line.get("repo_id"), "repo_id")
    _ts(line.get("started_at"), "started_at")
    _ts(line.get("finished_at"), "finished_at")
    if line.get("source") not in ("sync", "convert") or line.get("mode") not in ("full", "incremental", "targeted") \
            or type(line.get("complete")) is not bool or line.get("importer") != "orgmemory-github/1":
        raise ValueError("invalid GitHub sync log metadata")
    if line.get("since") is not None:
        _ts(line["since"], "since")
    for name in ("items", "requests", "events_appended", "evidence_appended"):
        if type(line.get(name)) is not int or line[name] < 0:
            raise ValueError("invalid GitHub sync log counts")
    for name in ("drift", "gone"):
        if not isinstance(line.get(name), list) or not all(isinstance(x, str) for x in line[name]):
            raise ValueError("invalid GitHub sync log lists")


def load_existing(dataset: Path) -> Existing:
    """Validate logs and every old hash before making a request; never open either policy file."""
    events_path = _path(dataset, "events.jsonl")
    evidence_path = _path(dataset, "evidence.jsonl")
    log_path = _path(dataset, "github-sync.jsonl")
    events = load_events(events_path) if events_path.exists() else []
    if evidence_path.exists():
        for _, record in read_jsonl(evidence_path):
            entry = ManifestEntry.model_validate(record)
            _path(dataset, entry.path)
    spans = load_manifest(evidence_path) if evidence_path.exists() else {}
    log = [record for _, record in read_jsonl(log_path)] if log_path.exists() else []
    validate(events, spans)
    for line in log:
        _check_log(line)
    for repo in {line["repo"] for line in log}:
        pinned_repo_id(log, repo)
    return Existing(events, spans, log)


def load_config(dataset: Path) -> GithubConfig:
    path = _path(dataset, "github.json")
    if not path.exists():
        return GithubConfig()
    try:
        return GithubConfig.model_validate(loads_at(path, read_file_bytes(path)))
    except RecursionError as exc:
        raise ValueError(f"{path}: invalid JSON: too deeply nested") from exc


def next_since(sync_log: list[dict], repo: str) -> datetime | None:
    starts = [_ts(line["started_at"]) for line in sync_log if line.get("repo") == repo
              and line.get("source") == "sync" and line.get("mode") in ("full", "incremental")
              and line.get("complete") is True]
    return max(starts) - timedelta(minutes=5) if starts else None


def pinned_repo_id(sync_log: list[dict], repo: str) -> int | None:
    ids = {_int(line.get("repo_id"), "repo_id") for line in sync_log if line.get("repo") == repo}
    if len(ids) > 1:
        raise ValueError("repository identity changed")
    return next(iter(ids), None)


@dataclass
class Summary:
    capture: dict
    plan: Plan
    source: str
    events_appended: int
    evidence_appended: int
    files_created: int
    already_present: int
    cursor: datetime | None
    complete: bool
    remaining: str | None = None

    def lines(self) -> list[str]:
        """Terminal strings use validated metadata and importer-built ids only."""
        cap, plan = self.capture, self.plan
        counts = ", ".join(f"{k} {v}" for k, v in sorted(Counter(e.action for e in plan.events).items()))
        since = f" since {_iso(_ts(cap['since']))}" if cap.get("since") else ""
        remaining = f" ({self.remaining} remaining)" if self.remaining and self.remaining.isascii() \
            and self.remaining.isdigit() else ""
        return [
            f"github {self.source} {_lower(cap['repo'])} (repo id {cap['repo_id']}): {cap['mode']}{since}",
            f"  fetched {len(cap['items'])} issues/PRs in {cap['requests']} requests{remaining}",
            f"  appended {self.events_appended} events ({counts}), {self.evidence_appended} evidence records, "
            f"{self.files_created} source files",
            f"  already present: {self.already_present} events; new text versions (evidence only): {plan.new_versions}",
            "  automated actors (recorded as comment only): " + (" ".join(sorted(plan.automated)) or "none"),
            "  skipped: " + ("; ".join(plan.skipped) or "none") + f"; unmapped timeline items: {plan.unmapped}",
            "  gone from GitHub (kept here): " + (" ".join(plan.gone) or "none"),
            "  drift (existing line kept): " + (" ".join(plan.drift) or "none"),
            "  actors in new events (assign roles in policy.json yourself; the importer never reads or writes it):",
            "    " + (" ".join(sorted({e.actor for e in plan.events})) or "none"),
            "  cursor for next run: " + (_iso(self.cursor) if self.cursor else "none"),
        ]


def _validate_plan(existing: Existing, plan: Plan) -> None:
    """Check references and primary fact attribution before any run output is written."""
    validate(existing.events + plan.events, {**existing.spans, **plan.spans})
    # Text-version ids can predate an event (blank reviews or interrupted appends).
    # A new event must cite the fact rendered for this signal, never a stale actor/verdict.
    for event in plan.events:
        fact_id = event.evidence_ids[0]
        span = existing.spans.get(fact_id) or plan.spans.get(fact_id)
        if span is None or span.quote != plan.facts.get(fact_id):
            raise ValueError("GitHub event fact conflicts with existing evidence")


def apply(dataset: Path, existing: Existing, plan: Plan, capture: dict, *, source: str, dry_run: bool,
          now: Callable[[], datetime]) -> Summary:
    _validate_plan(existing, plan)
    pin = pinned_repo_id(existing.sync_log, _lower(capture["repo"]))
    if pin is not None and pin != capture["repo_id"]:
        raise ValueError("repository identity changed")
    # Preflight every path and conflict before writing even the first source file.
    paths = [(_path(dataset, c.path), c.data) for c in plan.captures]
    files_created = sum(not path.exists() for path, _ in paths)
    for path, data in paths:
        if path.exists() and path.read_bytes() != data:
            raise ValueError("content-addressed capture tamper conflict")
    destinations = {name: _path(dataset, name) for name in
                    ("evidence.jsonl", "events.jsonl", "github-sync.jsonl")}
    line = {"repo": _lower(capture["repo"]), "repo_id": capture["repo_id"], "source": source,
            "mode": capture["mode"], "since": capture.get("since"), "started_at": capture["started_at"],
            "finished_at": _iso(now()), "complete": capture["complete"], "items": len(capture["items"]),
            "requests": capture["requests"], "events_appended": len(plan.events),
            "evidence_appended": len(plan.spans), "gone": plan.gone, "drift": plan.drift,
            "importer": "orgmemory-github/1"}
    _check_log(line)
    cursor = next_since(existing.sync_log + ([] if dry_run else [line]), line["repo"])
    summary = Summary(capture, plan, source, len(plan.events), len(plan.spans), files_created,
                      len(existing.events), cursor, capture["complete"])
    if dry_run:
        return summary
    for path, data in paths:
        if not path.exists():
            _create(path, data)
    for c in plan.captures:
        for entry in c.entries:
            verify_entry(entry, dataset)
    for c in plan.captures:
        for entry in c.entries:
            append_jsonl(destinations["evidence.jsonl"], entry.model_dump(mode="json"))
    for event in plan.events:
        append_jsonl(destinations["events.jsonl"], event.model_dump(mode="json", exclude_none=True))
    # An empty first run still produces valid empty logs.
    for name in ("evidence.jsonl", "events.jsonl"):
        if not destinations[name].exists():
            destinations[name].touch()
    append_jsonl(destinations["github-sync.jsonl"], dict(sorted(line.items())))
    return summary


def run_sync(dataset: Path, repo: str, client: Client, *, full: bool = False, since: datetime | None = None,
             issues: list[int] | None = None, save_capture: Path | None = None, dry_run: bool = False,
             now: Callable[[], datetime] = _now) -> Summary:
    repo, issues = _lower(repo), issues or []
    if not _repo_ok(repo) or (full and since is not None) or (issues and (full or since is not None)):
        raise ValueError("invalid sync options: --full, --since and --issue cannot be combined")
    for number in issues:
        _int(number, "issue number")
    with nullcontext() if dry_run else lock(dataset):
        existing, config = load_existing(dataset), load_config(dataset)
        cursor = next_since(existing.sync_log, repo)
        if since is not None and cursor is not None:
            raise ValueError("a cursor already exists; use --full or --issue")
        if save_capture is not None:
            saved = save_capture.resolve()
            managed = {dataset.resolve() / name for name in
                       ("policy.json", POLICY_LOG, "github.json", "events.jsonl", "evidence.jsonl",
                        "github-sync.jsonl", ".github-sync.lock", "jira.json", "jira-sync.jsonl",
                        ".jira-sync.lock")}
            managed |= {path.resolve() for path in managed}
            sources = dataset.resolve() / "sources"
            if save_capture.absolute() in managed or saved in managed \
                    or saved.is_relative_to(sources / "github") or saved.is_relative_to(sources / "jira"):
                raise ValueError("save-capture path overlaps managed dataset files")
        if save_capture is not None and (save_capture.exists() or save_capture.is_symlink()):
            raise ValueError("save-capture path already exists")
        capture = fetch(client, repo, since=None if full or issues else since or cursor, issues=issues,
                        pinned_repo_id=pinned_repo_id(existing.sync_log, repo))
        plan = convert(capture, {e.id: e for e in existing.events}, set(existing.spans), config)
        # Validate before saving a raw capture or writing any dataset files.
        _validate_plan(existing, plan)
        if save_capture is not None:
            data = json.dumps(capture, ensure_ascii=True, sort_keys=True, indent=1).encode("utf-8")
            with save_capture.open("xb") as handle:
                handle.write(data)
        summary = apply(dataset, existing, plan, capture, source="sync", dry_run=dry_run, now=now)
        summary.remaining = client.remaining
        return summary


def run_convert(dataset: Path, capture_path: Path, *, dry_run: bool = False,
                now: Callable[[], datetime] | None = None) -> Summary:
    with nullcontext() if dry_run else lock(dataset):
        existing, config = load_existing(dataset), load_config(dataset)
        _refuse_policy_alias(dataset, capture_path)
        capture = loads_at(capture_path, read_file_bytes(capture_path))
        plan = convert(capture, {e.id: e for e in existing.events}, set(existing.spans), config)
        # Offline conversions use the recording time by default, making examples reproducible.
        return apply(dataset, existing, plan, capture, source="convert", dry_run=dry_run,
                     now=now or (lambda: _ts(capture["started_at"])))
