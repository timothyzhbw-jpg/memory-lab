"""Policy-blind, append-only Jira import from a saved search capture. Offline: no network access at all."""
from __future__ import annotations

import json
from collections import Counter
from contextlib import AbstractContextManager, nullcontext
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from memcore import EvidenceSpan, ManifestEntry, append_jsonl, load_manifest, read_jsonl
from memcore.evidence import verify_entry

from .ingest import create_only, dataset_path, importer_lock
from .jira_map import KEY, SITE, JiraCaptureError, JiraConfig, JiraPlan, _num, _ts, convert
from .models import Event
from .replay import load_events, validate

LOG = "jira-sync.jsonl"
IMPORTER = "orgmemory-jira/1"
UNREADABLE = ("the existing dataset does not validate or cannot be read (events.jsonl, evidence.jsonl, "
              "jira-sync.jsonl or sources/); nothing appended")


def _path(dataset: Path, relative: str) -> Path:
    return dataset_path(dataset, relative, "Jira")


def lock(dataset: Path) -> AbstractContextManager[None]:
    """The Jira importer's lock; it also waits for no other importer (see `ingest.importer_lock`)."""
    return importer_lock(dataset, ".jira-sync.lock", "Jira")


def _stamp(when: datetime) -> str:
    return when.astimezone(timezone.utc).isoformat(timespec="seconds").removesuffix("+00:00") + "Z"


@dataclass
class Existing:
    events: list[Event]
    spans: dict[str, EvidenceSpan]
    log: list[dict]
    sites: set[str]
    issue_ids: dict[str, int]
    transitions: dict[str, datetime]


def provenance(dataset: Path, spans: dict[str, EvidenceSpan]) -> tuple[set[str], dict[str, int], dict[str, datetime]]:
    """Recover immutable identities from hash-verified captures, even before events or the sync log exist.

    Every Jira capture records site, key and issue id; a missing creator must not disable identity checks.
    Only the structured record line is read, never the untrusted text below the separator.
    """
    sites: set[str] = set()
    issues: dict[str, int] = {}
    keys: dict[int, str] = {}
    transitions: dict[str, datetime] = {}
    paths = {span.path for span in spans.values()
             if span.source_kind in {"jira_issue", "jira_comment", "jira_transition"}}
    for relative in sorted(paths):
        lines = _path(dataset, relative).read_bytes().split(b"\n", 2)
        record = json.loads(lines[1]) if len(lines) > 1 else None
        if not isinstance(record, dict):
            raise ValueError("invalid Jira source record")
        site, key = record.get("site"), record.get("key")
        if not isinstance(site, str) or not SITE.fullmatch(site) or not isinstance(key, str) or not KEY.fullmatch(key):
            raise ValueError("invalid Jira source identity")
        oid = _num(record.get("issue_id"), "a stored issue id")
        if issues.setdefault(key, oid) != oid or keys.setdefault(oid, key) != key:
            raise ValueError("conflicting Jira issue identities")
        sites.add(site)
        if record.get("kind") == "transition":
            hid = _num(record.get("id"), "a stored history id")
            transitions[f"jira:{key}/transition/{hid}"] = _ts(record.get("created"), "a stored history time")
    return sites, issues, transitions


def check_log(line: dict) -> None:
    if not isinstance(line.get("site"), str) or not SITE.fullmatch(line["site"]) or line.get("source") != "convert" \
            or line.get("importer") != IMPORTER or type(line.get("complete")) is not bool:
        raise ValueError("invalid Jira sync log metadata")
    _ts(line.get("captured_at"), "captured_at")
    _ts(line.get("finished_at"), "finished_at")
    for name in ("pages", "issues", "total", "duplicates", "events_appended", "evidence_appended"):
        if type(line.get(name)) is not int or line[name] < 0:
            raise ValueError("invalid Jira sync log counts")
    for name in ("skipped", "incomplete", "gone", "drift"):
        if not isinstance(line.get(name), list) or not all(isinstance(x, str) for x in line[name]):
            raise ValueError("invalid Jira sync log lists")


def load_existing(dataset: Path) -> Existing:
    """Validate the logs and every old hash before planning anything; never open policy.json."""
    try:
        events_path, evidence_path, log_path = (_path(dataset, n) for n in ("events.jsonl", "evidence.jsonl", LOG))
        events = load_events(events_path) if events_path.exists() else []
        if evidence_path.exists():
            for _, record in read_jsonl(evidence_path):
                _path(dataset, ManifestEntry.model_validate(record).path)
        spans = load_manifest(evidence_path) if evidence_path.exists() else {}
        log = [record for _, record in read_jsonl(log_path)] if log_path.exists() else []
        validate(events, spans)
        for line in log:
            check_log(line)
        sites, issue_ids, transitions = provenance(dataset, spans)
        sites.update(line["site"] for line in log)
        if len(sites) > 1:
            raise ValueError("Jira sync log names two sites")
    except (ValueError, OSError, RecursionError) as exc:
        raise JiraCaptureError(UNREADABLE) from exc
    return Existing(events, spans, log, sites, issue_ids, transitions)


def load_config(dataset: Path) -> JiraConfig:
    path = _path(dataset, "jira.json")
    if not path.exists():
        return JiraConfig()
    try:
        return JiraConfig.model_validate_json(path.read_bytes())
    except ValueError:
        raise JiraCaptureError('invalid jira.json: expected {"statuses": {"<status id>": '
                               '"approve" | "reject" | "revoke" | "reconsider"}}') from None


@dataclass
class Summary:
    plan: JiraPlan
    config: JiraConfig
    events_appended: int
    evidence_appended: int
    files_created: int

    def lines(self) -> list[str]:
        """Terminal strings use validated values and importer-built ids only."""
        plan = self.plan
        counts = ", ".join(f"{k} {v}" for k, v in sorted(Counter(e.action for e in plan.events).items()))
        mapped = ", ".join(f"{k} {v}" for k, v in sorted(self.config.statuses.items(), key=lambda kv: int(kv[0])))
        return [
            f"jira convert {plan.site}: {plan.issues} issues of {plan.total} results in {plan.pages} pages "
            f"({plan.duplicates} duplicate copies ignored), captured {_stamp(plan.captured_at)}",
            f"  appended {self.events_appended} events ({counts}), {self.evidence_appended} evidence records, "
            f"{self.files_created} source files",
            f"  already present: {plan.present} events; new text versions (evidence only): {plan.new_versions}",
            "  decision statuses in jira.json: " + (mapped or "none, so no transition is a decision"),
            "  status ids in transitions: " + (" ".join(plan.statuses) or "none"),
            "  automated actors (recorded as comment only): " + (" ".join(sorted(plan.automated)) or "none"),
            "  skipped: " + ("; ".join(plan.skipped) or "none") + f"; unmapped changelog items: {plan.unmapped}",
            "  incomplete capture: " + ("; ".join(plan.incomplete) or "no"),
            "  gone from Jira (kept here): " + (" ".join(plan.gone) or "none"),
            "  drift (existing line kept): " + (" ".join(plan.drift) or "none"),
            "  actors in new events (assign roles in policy.json yourself; the importer never reads or writes it):",
            "    " + (" ".join(sorted({e.actor for e in plan.events})) or "none"),
        ]


def apply(dataset: Path, existing: Existing, plan: JiraPlan, config: JiraConfig, *, dry_run: bool,
          finished_at: datetime) -> Summary:
    """Check everything, then write: source files, evidence lines, event lines, and one sync-log line."""
    validate(existing.events + plan.events, {**existing.spans, **plan.spans})
    # Evidence can predate its event (an interrupted append). A new event must cite the fact rendered for
    # this act, never a stored fact naming another actor.
    for event in plan.events:
        fact_id = event.evidence_ids[0]
        span = existing.spans.get(fact_id) or plan.spans.get(fact_id)
        if span is None or span.quote != plan.facts.get(fact_id):
            raise JiraCaptureError("a planned event's fact conflicts with existing evidence; nothing appended")
    pinned = existing.sites
    if pinned and pinned != {plan.site}:
        raise JiraCaptureError(f"Jira site changed: this dataset imports {min(pinned)}; one site per dataset")
    paths = [(_path(dataset, c.path), c.data) for c in plan.captures]
    files_created = sum(not path.exists() for path, _ in paths)
    for path, data in paths:
        if path.exists() and path.read_bytes() != data:
            raise ValueError("content-addressed capture tamper conflict")
    line = {"site": plan.site, "source": "convert", "captured_at": _stamp(plan.captured_at),
            "finished_at": _stamp(finished_at), "complete": plan.complete, "pages": plan.pages,
            "issues": plan.issues, "total": plan.total, "duplicates": plan.duplicates,
            "events_appended": len(plan.events), "evidence_appended": len(plan.spans), "skipped": plan.skipped,
            "incomplete": plan.incomplete, "gone": plan.gone, "drift": plan.drift, "importer": IMPORTER}
    check_log(line)
    summary = Summary(plan, config, len(plan.events), len(plan.spans), files_created)
    if dry_run:
        return summary
    for path, data in paths:
        if not path.exists():
            create_only(path, data)
    for capture in plan.captures:
        for entry in capture.entries:
            verify_entry(entry, dataset)
    destinations = {name: _path(dataset, name) for name in ("evidence.jsonl", "events.jsonl", LOG)}
    for capture in plan.captures:
        for entry in capture.entries:
            append_jsonl(destinations["evidence.jsonl"], entry.model_dump(mode="json"))
    for event in plan.events:
        append_jsonl(destinations["events.jsonl"], event.model_dump(mode="json", exclude_none=True))
    for name in ("evidence.jsonl", "events.jsonl"):
        if not destinations[name].exists():
            destinations[name].touch()
    append_jsonl(destinations[LOG], dict(sorted(line.items())))
    return summary


def run_convert(dataset: Path, capture_path: Path, *, dry_run: bool = False,
                now: Callable[[], datetime] | None = None) -> Summary:
    """Plan a saved capture against the dataset and apply it (or only report, with dry_run)."""
    with nullcontext() if dry_run else lock(dataset):
        existing, config = load_existing(dataset), load_config(dataset)
        if capture_path.resolve() == (dataset / "policy.json").resolve():
            raise JiraCaptureError("Jira importer cannot open policy.json")
        try:
            capture = json.loads(capture_path.read_bytes())
        except (ValueError, RecursionError):
            raise JiraCaptureError("the capture is not a JSON document") from None
        plan = convert(capture, {e.id: e for e in existing.events}, set(existing.spans), config,
                       issue_ids=existing.issue_ids, known_transitions=existing.transitions)
        # Offline conversion records the capture time as the finish time, so examples are reproducible.
        return apply(dataset, existing, plan, config, dry_run=dry_run,
                     finished_at=now() if now else plan.captured_at)
