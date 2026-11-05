#!/usr/bin/env python3
"""Turn a MemFalsify audit and history into orgmemory evidence records and events. Standard library only.

    to_events.py AUDIT_JSON HISTORY_JSON HISTORY_MD --repo REPO --dataset DATASET [--audited-at TIME]

AUDIT_JSON is `memfalsify audit MEMORY.md --format json` (a run that names the file), HISTORY_JSON is
`memfalsify history MEMORY.md --format json` and HISTORY_MD is `memfalsify history MEMORY.md`, all run from REPO. Each line printed is a JSON object for one real command:
{"kind": "evidence", ...} for `orgmemory evidence add` and {"kind": "event", "event": {...}} for `orgmemory append`.
docs/architecture.md has the field-by-field mapping.

An audit report holds a date. --audited-at gives the time, with a zone; use it when someone may decide on the day
of the audit, because replay orders events by time. The default is the end of that day in UTC.

The script writes only under DATASET/sources/: copies of the files the records cite, each one checked with
`git cat-file` against the commit the history names, so that a `git:<head>:<path>` record is true. It leaves the
memory and the event log alone, and a finding never becomes a decision. Records already in DATASET are left out, so
running it again after a later audit adds only what is new. Everything it reads is untrusted: the output is ASCII
JSON, and its messages name no text from the input.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
from datetime import datetime
from itertools import takewhile
from pathlib import Path, PurePosixPath, PureWindowsPath

AUDITOR = "memfalsify"  # who raises a risk that no person has confirmed; policy.json gives it a role
MEMORY_ID = re.compile(r"m-[0-9a-f]{8}\Z")
COMMIT = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")
# The approver a commit subject names: words of letters, digits, ".", "_", "@" and "-" in any script, one space
# between them. A claim, like the author name: see "Limits" in docs/architecture.md.
APPROVED_BY = re.compile(r"\(approved-by: ([\w.@-]+(?: [\w.@-]+)*)\)\Z")
RISK_STATUSES = {"challenged", "conflicted", "candidate_counterevidence", "disputed"}
# The markup MemFalsify removes from a line before it reads claims out of it (memfalsify/memory.py).
LINK = re.compile(r"!?\[([^\]]*)\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
WIKILINK = re.compile(r"\[\[([^\]]+)\]\]")
EMPHASIS = re.compile(r"\*\*|__|`")
QUOTE_MARKER = re.compile(r"^[ \t]*(?:>[ \t]*)+", re.MULTILINE)


class ConversionError(Exception):
    """The input cannot be converted. The message holds no text taken from the input."""


def checked_path(relative: str) -> str:
    """A relative path from a report, usable under both the repository and the dataset, or an error."""
    posix, windows = PurePosixPath(relative), PureWindowsPath(relative)
    if not posix.parts or posix.is_absolute() or windows.anchor or ".." in posix.parts or ".." in windows.parts:
        raise ConversionError("a report names a path outside the repository")
    return relative


def report_path(field: str) -> str:
    """`memory_file` and `evidence_root` are written with the separators of the OS that ran the audit."""
    return field.replace("\\", "/")


def checked_id(memory_id: object) -> str:
    if not isinstance(memory_id, str) or not MEMORY_ID.match(memory_id):
        raise ConversionError("a report holds a memory_id that is not m- and eight hex digits")
    return memory_id


def checked_commit(commit: object) -> str:
    if not isinstance(commit, str) or not COMMIT.match(commit):
        raise ConversionError("a report holds a commit that is not 40 or 64 hex digits")
    return commit


def moment(timestamp: str) -> datetime:
    return datetime.fromisoformat(timestamp.replace("Z", "+00:00"))


def audit_time(value: str) -> str:
    """The value of --audited-at."""
    try:
        if moment(value).tzinfo is not None:
            return value
    except ValueError:
        pass
    raise argparse.ArgumentTypeError("expected an ISO 8601 time with a zone, such as 2026-11-04T14:00:00-06:00")


def without_markup(line: str) -> str:
    return EMPHASIS.sub("", WIKILINK.sub(r"\1", LINK.sub(r"\1", line)))


def holds(lines: list[str], text: str) -> bool:
    """Whether the block that starts at the first of `lines` holds a claim's text, once markup is removed. MemFalsify
    reads the lines of a wrapped paragraph as one, with what continues it (indent, quote markers) read as one space."""
    if text in without_markup(lines[0]):
        return True
    block = "\n".join(takewhile(str.strip, lines))
    return "".join(text.split()) in "".join(QUOTE_MARKER.sub("", without_markup(block)).split())


def introduced_line(report: str, memory_id: str, commit: str) -> str:
    """The claim's `Introduced` line in the Markdown history, which must name the commit the JSON history names:
    the text an evidence record quotes."""
    section = report.partition(f"### `{memory_id}`\n")[2].partition("\n### ")[0]
    for line in section.split("\n"):
        if line.startswith(f"- Introduced: `{commit[:12]}` "):
            return line[2:]
    raise ConversionError(f"the Markdown history has no Introduced line for {memory_id} at that commit")


class Converter:
    def __init__(self, audit: dict, history: dict, history_report: str, repo: Path, dataset: Path,
                 audited_at: str | None = None) -> None:
        if audit.get("tool") != "memfalsify" or history.get("tool") != "memfalsify":
            raise ConversionError("the inputs are not MemFalsify reports")
        if "reports" in audit and "findings" not in audit:
            raise ConversionError("this audit ran with no file named, which prints several reports; "
                                  "name the memory file: memfalsify audit MEMORY.md --format json")
        if not isinstance(history.get("head"), str) or not COMMIT.match(history["head"]):
            raise ConversionError("the history has no HEAD commit to cite; commit the memory first")
        self.audit, self.history, self.history_report = audit, history, history_report
        self.repo, self.dataset = repo, dataset
        self.commit, self.head = history["head"], history["head"][:12]
        # The report holds only a date, so without --audited-at what comes from it takes the end of that day, in UTC.
        self.audited_at = audited_at or f"{audit['as_of']}T23:59:59+00:00"
        moment(self.audited_at)
        self.memory_root = PurePosixPath(checked_path(report_path(audit["memory_file"]))).parent
        self.records: list[dict] = []
        self.known = _ids(dataset / "evidence.jsonl") | _ids(dataset / "events.jsonl")
        self.files: dict[str, bytes] = {}
        self.written: dict[str, str] = {}  # memory_id: the date of the commit that introduced the line
        self.unattributed: list[str] = []
        self.unclear_approvers: list[str] = []

    def _read(self, relative: str) -> bytes:
        """A file of the repository, once it is shown to be the file at the history's HEAD, so that a record's
        `git:<head>:<path>` is true. A symlink out of the repository is refused, as MemFalsify refuses it."""
        if relative not in self.files:
            root = self.repo.resolve()
            path = (root / relative).resolve()
            if root not in path.parents:
                raise ConversionError("a report names a path outside the repository")
            git = shutil.which("git")
            if git is None:
                raise ConversionError("this script needs git, to check the cited files against their commit")
            data = path.read_bytes()
            committed = subprocess.run([git, "-C", str(self.repo), "cat-file", "blob", f"{self.commit}:./{relative}"],
                                       capture_output=True)
            # Line endings aside: the audit hashed the working tree, where git may have written CRLF.
            if committed.returncode != 0 or committed.stdout.replace(b"\r\n", b"\n") != data.replace(b"\r\n", b"\n"):
                raise ConversionError("a cited file differs from the commit the history names; "
                                      "commit it and run the audit and history again")
            self.files[relative] = data
        return self.files[relative]

    def _store(self, data: bytes, relative: str) -> str:
        """Keep the bytes a record cites, once per HEAD (their hashes must stay true); return the dataset path."""
        name = f"sources/{self.head}/{relative}"
        target = (self.dataset / name).resolve()
        if self.dataset.resolve() / "sources" not in target.parents:
            raise ConversionError("a report names a path outside the dataset")
        if target.exists() and target.read_bytes() != data:
            raise ConversionError("a cited file changed without a new commit; commit it and run the audit again")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        return name

    def _evidence(self, record_id: str, data: bytes, relative: str, quote: str, uri: str, kind: str) -> str:
        if "\0" in quote:
            raise ConversionError("a quote holds a NUL character, which no command line can carry")
        if record_id not in self.known:
            self.known.add(record_id)
            self.records.append({"kind": "evidence", "id": record_id, "path": self._store(data, relative),
                                 "quote": quote, "source_uri": uri, "source_kind": kind,
                                 "captured_at": self.audited_at})
        return record_id

    def _event(self, event_id: str, timestamp: str, actor: str, action: str, memory_id: str,
               evidence_ids: list[str], note: str | None = None) -> None:
        if event_id not in self.known:
            self.known.add(event_id)
            event = {"id": event_id, "timestamp": timestamp, "actor": actor, "action": action,
                     "subject": f"memory:{memory_id}", "evidence_ids": evidence_ids}
            self.records.append({"kind": "event", "event": {**event, **({"note": note} if note else {})}})

    def _memory_line(self, memory_id: str, text: str, location: str) -> str:
        """Evidence that the line is in the memory: the claim's text, or its whole line when markup or a line break
        splits it. `location` is the line where the claim starts; a wrapped claim goes on over the next ones."""
        path, _, number = location.rpartition(":")
        relative = (self.memory_root / checked_path(path)).as_posix()
        data = self._read(relative)
        # MemFalsify counts CR, CRLF and LF as line breaks, so `location` does too.
        lines = data.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n").split("\n")
        if not number.isdigit() or not 1 <= int(number) <= len(lines):
            raise ConversionError("a report names a memory line that is not in the file")
        line = lines[int(number) - 1]
        if not text or not holds(lines[int(number) - 1:], text):
            raise ConversionError("a memory line does not hold the claim a report names; run the audit again")
        return self._evidence(f"{memory_id}-{self.head}-line", data, relative, text if text in line else line.strip(),
                              f"git:{self.head}:{relative}", "memory_file")

    def _writes(self, claim: dict) -> None:
        """The commit that introduced a line: its author asked for the write, and whoever approved it decided.
        A line removed and landed again has a new commit, so it is a new request on the same subject."""
        memory_id = checked_id(claim["memory_id"])
        introduced = claim.get("introduced") or {}
        author = introduced.get("author")
        if not introduced.get("commit") or not author:
            self.unattributed.append(memory_id)  # older than the scan, or uncommitted: no one to name
            return
        commit = checked_commit(introduced["commit"])
        self.written[memory_id] = introduced["date"]
        request, approval = f"write-request-{memory_id}-{commit[:12]}", f"write-approve-{memory_id}-{commit[:12]}"
        if request in self.known and approval in self.known:
            return
        subject = introduced.get("subject") or ""
        approved_by = APPROVED_BY.search(subject)
        if not approved_by and "approved-by:" in subject:
            self.unclear_approvers.append(memory_id)
        replaced = claim.get("replaced")
        note = f"Memory write at {claim['location']}: {claim['text']}"
        if replaced:
            note += f" (edits {checked_id(replaced['memory_id'])})"
        cited = [self._memory_line(memory_id, claim["text"], claim["location"]),
                 self._evidence(f"{memory_id}-{commit[:12]}-introduced", self.history_report.encode("utf-8"),
                                "memfalsify-history.md", introduced_line(self.history_report, memory_id, commit),
                                f"memfalsify-history:{self.head}", "memory_history")]
        self._event(request, introduced["date"], author, "request", memory_id, cited, note)
        self._event(approval, introduced["date"], approved_by.group(1) if approved_by else author,
                    "approve", memory_id, cited)

    def _span(self, memory_id: str, entry: dict) -> str:
        """Evidence from a finding: the same bytes the audit hashed, or an error if the file has changed."""
        span, root = entry["span"], report_path(self.audit["evidence_root"])
        directory = PurePosixPath(".") if root == "." else PurePosixPath(checked_path(root))
        repo_path = (directory / checked_path(span["path"])).as_posix()
        data = self._read(repo_path)
        if hashlib.sha256(data).hexdigest() != span["sha256"]:
            raise ConversionError("an evidence file changed after the audit; run the audit again")
        # `orgmemory evidence add` finds a quote's first occurrence, so that is the range the record will hold.
        start = data.decode("utf-8").find(span["quote"])
        if start < 0:
            raise ConversionError("a report quotes text that is not in the file it names")
        return self._evidence(f"{memory_id}-{span['sha256'][:8]}-{start}", data, repo_path, span["quote"],
                              f"git:{self.head}:{repo_path}", "evidence_file")

    def _finding_time(self, memory_id: str) -> str:
        """When a finding takes effect. An audit comes after the lines it read, even where the end of the as_of day
        in UTC says otherwise: a commit at 18:30 in Nashville is already the next day in UTC."""
        written = self.written.get(memory_id)
        return written if written and moment(written) > moment(self.audited_at) else self.audited_at

    def _finding(self, finding: dict) -> None:
        memory_id, status = checked_id(finding["memory_id"]), finding["status"]
        event_id = f"finding-{status}-{memory_id}"
        if event_id in self.known:
            return  # a later audit with the same status: no new event, so no new record for one to cite
        note = f"{status} at {finding['location']}: {finding['text']}"
        timestamp = self._finding_time(memory_id)
        if status in RISK_STATUSES:
            cited = [e for e in finding["evidence"] if e["relation"] != "supports" or status == "conflicted"]
            # A person who confirmed the evidence raises the risk; otherwise the audit does, as a lead.
            reviewer = next((e["reviewer"] for e in cited if e.get("confirmed") and e.get("reviewer")), None)
            self._event(event_id, timestamp, reviewer or AUDITOR, "raise_risk", memory_id,
                        list(dict.fromkeys(self._span(memory_id, entry) for entry in cited)), note)
        elif status == "insufficient_evidence":
            # Missing evidence means unverified, not false: on the record, with no change of state.
            self._event(event_id, timestamp, AUDITOR, "comment", memory_id,
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
    # Split on LF alone, as the log's reader does: a record may hold U+2028 or U+0085, which str.splitlines cuts at.
    return {json.loads(line)["id"] for line in manifest.read_bytes().split(b"\n") if line.strip()}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Convert MemFalsify reports into orgmemory records (JSON lines).")
    parser.add_argument("audit", type=Path, help="memfalsify audit MEMORY.md --format json")
    parser.add_argument("history", type=Path, help="memfalsify history MEMORY.md --format json")
    parser.add_argument("history_report", type=Path, help="memfalsify history MEMORY.md (Markdown)")
    parser.add_argument("--repo", type=Path, required=True, help="the directory the reports were made in")
    parser.add_argument("--dataset", type=Path, required=True, help="the orgmemory dataset directory")
    parser.add_argument("--audited-at", type=audit_time, help="when the audit ran, with a zone (default: the end of "
                                                              "its as_of day in UTC)")
    args = parser.parse_args(argv)
    try:
        converter = Converter(json.loads(args.audit.read_bytes().decode("utf-8")),
                              json.loads(args.history.read_bytes().decode("utf-8")),
                              args.history_report.read_bytes().decode("utf-8").replace("\r\n", "\n"),
                              args.repo, args.dataset, args.audited_at)
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
    if converter.unclear_approvers:
        sys.stderr.write(f"note: the commit subject of {', '.join(converter.unclear_approvers)} has an approved-by "
                         "with no clean name at its end, so the author stands as the approver\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
