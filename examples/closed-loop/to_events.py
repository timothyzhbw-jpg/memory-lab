#!/usr/bin/env python3
"""Turn a MemFalsify audit and history into orgmemory evidence records and events. Standard library only.

    to_events.py AUDIT_JSON HISTORY_JSON HISTORY_MD --repo REPO --dataset DATASET

AUDIT_JSON is `memfalsify audit --format json`, HISTORY_JSON is `memfalsify history --format json` and HISTORY_MD
is `memfalsify history`, all run from REPO. Each line printed is a JSON object for one real command:
{"kind": "evidence", ...} for `orgmemory evidence add` and {"kind": "event", "event": {...}} for `orgmemory append`.
docs/architecture.md has the field-by-field mapping.

The script writes only under DATASET/sources/: copies of the files the records cite. It leaves the memory and the
event log alone, and a finding never becomes a decision. Records already in DATASET are left out, so running it
again after a later audit adds only what is new. Everything it reads is untrusted: the output is ASCII JSON, and
its messages name no text from the input.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path, PurePosixPath, PureWindowsPath

AUDITOR = "memfalsify"  # who raises a risk that no person has confirmed; policy.json gives it a role
MEMORY_ID = re.compile(r"m-[0-9a-f]{8}\Z")
# The approver a commit subject names. A claim, like the author name: see "Limits" in docs/architecture.md.
APPROVED_BY = re.compile(r"\(approved-by: ([A-Za-z0-9._@-]+)\)\Z")
RISK_STATUSES = {"challenged", "conflicted", "candidate_counterevidence", "disputed"}


class ConversionError(Exception):
    """The input cannot be converted. The message holds no text taken from the input."""


def checked_path(relative: str) -> str:
    """A relative path from a report, usable under both the repository and the dataset, or an error."""
    posix, windows = PurePosixPath(relative), PureWindowsPath(relative)
    if not posix.parts or posix.is_absolute() or windows.is_absolute() or windows.drive or ".." in posix.parts:
        raise ConversionError("a report names a path outside the repository")
    return relative


def checked_id(memory_id: object) -> str:
    if not isinstance(memory_id, str) or not MEMORY_ID.match(memory_id):
        raise ConversionError("a report holds a memory_id that is not m- and eight hex digits")
    return memory_id


def introduced_line(report: str, memory_id: str) -> str:
    """The claim's `Introduced` line in the Markdown history: the text an evidence record quotes."""
    section = report.partition(f"### `{memory_id}`\n")[2].partition("\n### ")[0]
    for line in section.split("\n"):
        if line.startswith("- Introduced: "):
            return line[2:]
    raise ConversionError(f"the Markdown history has no Introduced line for {memory_id}")


class Converter:
    def __init__(self, audit: dict, history: dict, history_report: str, repo: Path, dataset: Path) -> None:
        if audit.get("tool") != "memfalsify" or history.get("tool") != "memfalsify":
            raise ConversionError("the inputs are not MemFalsify reports")
        if not isinstance(history.get("head"), str):
            raise ConversionError("the history has no HEAD commit to cite; commit the memory first")
        self.audit, self.history, self.history_report = audit, history, history_report
        self.repo, self.dataset = repo, dataset
        self.head = history["head"][:12]
        # The report holds a date: the end of that day comes after any commit of the day.
        self.audited_at = f"{audit['as_of']}T23:59:59+00:00"
        self.memory_root = PurePosixPath(checked_path(audit["memory_file"])).parent
        self.records: list[dict] = []
        self.known = _ids(dataset / "evidence.jsonl") | _ids(dataset / "events.jsonl")
        self.unattributed: list[str] = []

    def _read(self, relative: str) -> bytes:
        """A file of the repository. A symlink out of it is refused, as MemFalsify refuses it."""
        root = self.repo.resolve()
        path = (root / relative).resolve()
        if root not in path.parents:
            raise ConversionError("a report names a path outside the repository")
        return path.read_bytes()

    def _store(self, data: bytes, relative: str) -> str:
        """Keep the bytes a record cites, once per HEAD (their hashes must stay true); return the dataset path."""
        name = f"sources/{self.head}/{relative}"
        target = self.dataset / name
        if target.exists() and target.read_bytes() != data:
            raise ConversionError("a cited file changed without a new commit; commit it and run the audit again")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        return name

    def _evidence(self, record_id: str, path: str, quote: str, uri: str, kind: str) -> str:
        if "\0" in quote:
            raise ConversionError("a quote holds a NUL character, which no command line can carry")
        if record_id not in self.known:
            self.known.add(record_id)
            self.records.append({"kind": "evidence", "id": record_id, "path": path, "quote": quote, "source_uri": uri,
                                 "source_kind": kind, "captured_at": self.audited_at})
        return record_id

    def _event(self, event_id: str, timestamp: str, actor: str, action: str, memory_id: str,
               evidence_ids: list[str], note: str | None = None) -> None:
        if event_id not in self.known:
            self.known.add(event_id)
            event = {"id": event_id, "timestamp": timestamp, "actor": actor, "action": action,
                     "subject": f"memory:{memory_id}", "evidence_ids": evidence_ids}
            self.records.append({"kind": "event", "event": {**event, **({"note": note} if note else {})}})

    def _memory_line(self, memory_id: str, text: str, location: str) -> str:
        """Evidence that the line is in the memory: the claim's text, or its whole line when markup splits it."""
        path, _, number = location.rpartition(":")
        relative = (self.memory_root / checked_path(path)).as_posix()
        data = self._read(relative)
        # MemFalsify counts CR, CRLF and LF as line breaks, so `location` does too.
        lines = data.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n").split("\n")
        if not number.isdigit() or not 1 <= int(number) <= len(lines) or not lines[int(number) - 1].strip():
            raise ConversionError("a report names a memory line that is not in the file")
        line = lines[int(number) - 1]
        return self._evidence(f"{memory_id}-line", self._store(data, relative), text if text in line else line.strip(),
                              f"git:{self.head}:{relative}", "memory_file")

    def _writes(self, claim: dict) -> None:
        """The commit that introduced a line: its author asked for the write, and whoever approved it decided."""
        memory_id = checked_id(claim["memory_id"])
        introduced = claim.get("introduced") or {}
        author = introduced.get("author")
        if not introduced.get("commit") or not author:
            self.unattributed.append(memory_id)  # older than the scan, or uncommitted: no one to name
            return
        approved_by = APPROVED_BY.search(introduced.get("subject") or "")
        replaced = claim.get("replaced")
        note = f"Memory write at {claim['location']}: {claim['text']}"
        if replaced:
            note += f" (edits {checked_id(replaced['memory_id'])})"
        cited = [self._memory_line(memory_id, claim["text"], claim["location"]),
                 self._evidence(f"{memory_id}-introduced",
                                self._store(self.history_report.encode("utf-8"), "memfalsify-history.md"),
                                introduced_line(self.history_report, memory_id),
                                f"memfalsify-history:{self.head}", "memory_history")]
        self._event(f"write-request-{memory_id}", introduced["date"], author, "request", memory_id, cited, note)
        self._event(f"write-approve-{memory_id}", introduced["date"], approved_by.group(1) if approved_by else author,
                    "approve", memory_id, cited)

    def _span(self, memory_id: str, entry: dict) -> str:
        """Evidence from a finding: the same bytes the audit hashed, or an error if the file has changed."""
        span, root = entry["span"], self.audit["evidence_root"]
        repo_path = (PurePosixPath(checked_path(root)) / checked_path(span["path"])).as_posix()
        data = self._read(repo_path)
        if hashlib.sha256(data).hexdigest() != span["sha256"]:
            raise ConversionError("an evidence file changed after the audit; run the audit again")
        return self._evidence(f"{memory_id}-{span['sha256'][:8]}-{span['start']}", self._store(data, repo_path),
                              span["quote"], f"git:{self.head}:{repo_path}", "evidence_file")

    def _finding(self, finding: dict) -> None:
        memory_id, status = checked_id(finding["memory_id"]), finding["status"]
        note = f"{status} at {finding['location']}: {finding['text']}"
        event_id = f"finding-{status}-{memory_id}"
        if status in RISK_STATUSES:
            cited = [e for e in finding["evidence"] if e["relation"] != "supports" or status == "conflicted"]
            # A person who confirmed the evidence raises the risk; otherwise the audit does, as a lead.
            reviewer = next((e["reviewer"] for e in cited if e.get("confirmed") and e.get("reviewer")), None)
            self._event(event_id, self.audited_at, reviewer or AUDITOR, "raise_risk", memory_id,
                        list(dict.fromkeys(self._span(memory_id, entry) for entry in cited)), note)
        elif status == "insufficient_evidence":
            # Missing evidence means unverified, not false: on the record, with no change of state.
            self._event(event_id, self.audited_at, AUDITOR, "comment", memory_id,
                        [self._memory_line(memory_id, finding["text"], finding["location"])], note)

    def convert(self) -> list[dict]:
        for claim in self.history["claims"]:
            self._writes(claim)
        for finding in self.audit["findings"]:
            self._finding(finding)
        return self.records


def _ids(manifest: Path) -> set[str]:
    if not manifest.exists():
        return set()
    return {json.loads(line)["id"] for line in manifest.read_bytes().decode("utf-8").splitlines() if line.strip()}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Convert MemFalsify reports into orgmemory records (JSON lines).")
    parser.add_argument("audit", type=Path, help="memfalsify audit --format json")
    parser.add_argument("history", type=Path, help="memfalsify history --format json")
    parser.add_argument("history_report", type=Path, help="memfalsify history (Markdown)")
    parser.add_argument("--repo", type=Path, required=True, help="the directory the reports were made in")
    parser.add_argument("--dataset", type=Path, required=True, help="the orgmemory dataset directory")
    args = parser.parse_args(argv)
    try:
        converter = Converter(json.loads(args.audit.read_bytes().decode("utf-8")),
                              json.loads(args.history.read_bytes().decode("utf-8")),
                              args.history_report.read_bytes().decode("utf-8").replace("\r\n", "\n"),
                              args.repo, args.dataset)
        records = converter.convert()
    except ConversionError as exc:
        sys.stderr.write(f"error: {exc}\n")
        return 2
    except (KeyError, TypeError, AttributeError, ValueError, OSError) as exc:  # the type only: it holds no input text
        sys.stderr.write(f"error: cannot read the reports or the files they name ({type(exc).__name__})\n")
        return 2
    for record in records:
        sys.stdout.buffer.write(json.dumps(record, ensure_ascii=True).encode("ascii") + b"\n")
    sys.stdout.buffer.flush()
    if converter.unattributed:
        sys.stderr.write(f"note: no commit to attribute for {', '.join(converter.unattributed)}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
