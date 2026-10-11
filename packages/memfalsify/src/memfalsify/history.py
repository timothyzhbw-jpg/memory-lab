"""Memory under version control: which claims a revision added, changed or removed, and where each came from.

Read-only. ``memory_diff`` audits only the added and changed claims; ``memory_history`` attributes each
claim to the commits that introduced and last changed it. Git access lives in ``gitstore``.
"""
from __future__ import annotations

import difflib
import re
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, replace
from datetime import date
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from . import __version__
from .audit import ATTENTION, AuditReport, Status, audit_claims
from .corpus import DIFF_HEADER, HISTORY_HEADER
from .formats import Value
from .gitstore import (HEX, HistoryError, Objects, Repository, Snapshot, code_span, open_repository, printable,
                       resolve_commit, show_text)
from .memory import Claim
from .report import LABELS, _finding, _quote, metadata_line, skipped_line, usage_lines
from .text import terms
from .verify import Verifier

__all__ = ["ClaimChange", "DiffReport", "HistoryError", "HistoryReport", "audit_staged", "compare",
           "diff_to_markdown", "history_to_markdown", "memory_diff", "memory_history", "resolve_commit"]

SIMILAR = 0.5  # minimum term similarity for an edit to count as "changed" rather than removed + added


# --- classifying changes ---------------------------------------------------------------------------

Kind = Literal["added", "changed", "removed", "unchanged"]


@dataclass(frozen=True)
class Change:
    kind: Kind
    old: Claim | None
    new: Claim | None
    similarity: float | None = None
    # unchanged, but reworded (case/whitespace), moved to another file or section, or its file's frontmatter
    # metadata (where it applies) changed
    moved: bool = False


def similarity(a: str, b: str) -> float:
    """Sequence similarity of the two sentences' content terms (stemmed words, CJK bigrams and trigrams, numbers)."""
    return difflib.SequenceMatcher(None, terms(a), terms(b), autojunk=False).ratio()


def _section_renames(kept: list[tuple[Claim, Claim]]) -> dict[tuple[str, str | None], str | None]:
    """Where the claims that kept their IDs went: a heading whose claims mostly moved under another
    heading of the same file counts as renamed to it (ties broken by name, so the result is deterministic)."""
    moves: dict[tuple[str, str | None], Counter] = {}
    for old, new in kept:
        if old.path == new.path:
            moves.setdefault((old.path, old.section), Counter())[new.section] += 1
    renames = {}
    for key, counts in moves.items():
        target = min(counts, key=lambda section: (-counts[section], section is None, section or ""))
        if target != key[1]:
            renames[key] = target
    return renames


def compare(base: list[Claim], head: list[Claim]) -> list[Change]:
    """Classify claims as added, removed, changed or unchanged.

    Claims are identified by ID (normalized text), so a case- or whitespace-only edit is unchanged, as is a
    claim that only moved. A removed and an added claim are paired as one change when they are in the same
    file and section (or a renamed section) and their term similarity is at least ``SIMILAR``; pairs are
    taken greedily by similarity, then by position, so the result is deterministic.
    """
    old_by_id = {c.id: c for c in base}
    new_ids = {c.id for c in head}
    removed = [c for c in base if c.id not in new_ids]
    added = [c for c in head if c.id not in old_by_id]
    renames = _section_renames([(old_by_id[c.id], c) for c in head if c.id in old_by_id])
    candidates = []
    for i, old in enumerate(removed):
        sections = {old.section, renames.get((old.path, old.section), old.section)}
        for j, new in enumerate(added):
            if new.path == old.path and new.section in sections:
                score = similarity(old.text, new.text)
                if score >= SIMILAR:
                    candidates.append((-score, i, j))
    pairs: dict[int, tuple[int, float]] = {}
    paired_old: set[int] = set()
    for negative, i, j in sorted(candidates):
        if i not in paired_old and j not in pairs:
            paired_old.add(i)
            pairs[j] = (i, round(-negative, 2))
    changes, position = [], {c.id: j for j, c in enumerate(added)}
    for claim in head:
        if claim.id in old_by_id:
            old = old_by_id[claim.id]
            section = renames.get((old.path, old.section), old.section)  # a renamed heading is not a move
            moved = (old.text != claim.text or old.path != claim.path or claim.section not in (old.section, section)
                     or old.metadata != claim.metadata)
            changes.append(Change("unchanged", old, claim, moved=moved))
        elif (pair := pairs.get(position[claim.id])) is not None:
            changes.append(Change("changed", removed[pair[0]], claim, pair[1]))
        else:
            changes.append(Change("added", None, claim))
    changes += [Change("removed", old, None) for i, old in enumerate(removed) if i not in paired_old]
    return changes


# --- diff -----------------------------------------------------------------------------------------

class ClaimVersion(BaseModel):
    memory_id: str
    text: str
    location: str
    section: str | None = None
    also_at: list[str] = []
    metadata: dict[str, Value] = {}

    @classmethod
    def of(cls, claim: Claim) -> "ClaimVersion":
        return cls(memory_id=claim.id, text=claim.text, location=claim.location, section=claim.section,
                   also_at=claim.also_at, metadata=claim.metadata)


class ClaimChange(BaseModel):
    change: Kind
    old: ClaimVersion | None = None
    new: ClaimVersion | None = None
    similarity: float | None = None
    status: Status | None = None  # audit status of an added or changed claim


class Revision(BaseModel):
    rev: str
    commit: str | None = None  # None: the working tree
    files: list[str]


class DiffReport(BaseModel):
    tool: str = "memfalsify"
    version: str = __version__
    mode: Literal["read_only"] = "read_only"
    memory_file: str
    base: Revision
    head: Revision
    summary: dict[str, int]
    changes: list[ClaimChange]
    audit: AuditReport
    limitations: list[str]

    @property
    def attention(self) -> int:
        return sum(n for status, n in self.audit.summary.items() if status in ATTENTION)


DIFF_LIMITATIONS = [
    "Claims are compared by ID (normalized text): case and whitespace edits count as unchanged, any other "
    "edit as a change.",
    f"An edit is shown as changed only within the same file and section (or a renamed section) and with term "
    f"similarity of at least {SIMILAR}; other edits show as removed plus added.",
    "Only added and changed claims were audited, against the evidence as it is now. Unchanged and removed "
    "claims were not re-checked; run `memfalsify audit` for the whole memory.",
]


# Appended to a staged diff. Evidence stays the working tree; pre-commit's own stash is called out so the
# report still matches what a hand-run `memfalsify diff --staged` does.
STAGED_LIMITATION = (
    "The head version is the git index (what this commit will record). Unstaged edits to the memory file "
    "and to the .md files it links are outside this diff. Evidence is the working tree as it is now. "
    "During a pre-commit run, unstaged changes to tracked files are hidden first, so tracked evidence "
    "matches the index; untracked evidence files stay visible."
)
STAGED_AUDIT_NOTE = (
    "Every claim was read from the git index (the staged text a commit would record), including one level "
    "of linked .md files, also from the index. Evidence is the working tree as it is now. During a "
    "pre-commit run, unstaged changes to tracked files are hidden first, so tracked evidence matches the "
    "index; untracked evidence files stay visible."
)


def memory_diff(memory_file: Path, base: str, head: str | None = None, *, follow_links: bool = True,
                evidence_root: Path | None = None, reviews: Path | None = None, verifier: Verifier | None = None,
                as_of: date | None = None, stale_days: int = 180, min_coverage: float = 0.5,
                max_judged: int | None = None, exclude: Iterable[str] = (), max_concurrency: int = 1,
                staged: bool = False) -> DiffReport:
    """Diff the memory at ``base`` against ``head`` (default: the working tree); audit added and changed claims.

    With ``staged=True``, head is the index (what ``git commit`` will record) and ``head`` must be omitted.
    A repository with no commits yet accepts ``base="HEAD"`` and treats every staged claim as added.
    Evidence is the working tree in every mode.
    """
    if staged and head is not None:
        raise HistoryError("a staged diff reads the index and does not take a head revision")
    repo = open_repository(memory_file)
    if staged and repo.head() is None:
        if base != "HEAD":
            raise HistoryError("this repository has no commits; a staged diff only accepts --base HEAD")
        base_commit = None
    else:
        base_commit = repo.resolve(base)
    head_commit = None if staged or head is None else repo.resolve(head)
    with Objects(repo) as objects:
        old = Snapshot() if base_commit is None else repo.at(objects, base_commit, follow_links)
        if staged:
            new = repo.index(objects, follow_links)
        elif head_commit is not None:
            new = repo.at(objects, head_commit, follow_links)
        else:
            new = repo.worktree(follow_links)
    changes = compare(old.claims, new.claims)
    touched = [c.new.id for c in changes if c.kind in ("added", "changed")]
    # The memory files (either version's paths, as they are now) are never evidence for themselves.
    memory_paths = [p for name in dict.fromkeys(old.files + new.files) if (p := repo.worktree_path(name))]
    report = audit_claims(new.claims, memory_file=str(memory_file), files_read=new.files,
                          memory_paths=memory_paths, evidence_root=evidence_root, reviews=reviews,
                          verifier=verifier, as_of=as_of, stale_days=stale_days, min_coverage=min_coverage,
                          max_judged=max_judged, exclude=exclude, only=touched, max_concurrency=max_concurrency)
    rejected = {(s.path, s.reason): s for s in old.skipped + new.skipped}
    report.skipped_evidence[:0] = [s.model_copy(update={"reason": "memory skipped: " + s.reason})
                                  for s in rejected.values()]
    status = {f.memory_id: f.status for f in report.findings}
    entries = [ClaimChange(change=c.kind, old=ClaimVersion.of(c.old) if c.old else None,
                           new=ClaimVersion.of(c.new) if c.new else None, similarity=c.similarity,
                           status=status.get(c.new.id) if c.kind in ("added", "changed") else None)
               for c in changes]
    summary = {kind: sum(c.kind == kind for c in changes) for kind in ("added", "changed", "removed", "unchanged")}
    limitations = [*DIFF_LIMITATIONS, STAGED_LIMITATION] if staged else DIFF_LIMITATIONS
    head_rev = "index" if staged else (head if head is not None else "working tree")
    return DiffReport(memory_file=str(memory_file),
                      base=Revision(rev=base, commit=base_commit, files=old.files),
                      head=Revision(rev=head_rev, commit=head_commit, files=new.files),
                      summary=summary, changes=entries, audit=report, limitations=limitations)


def audit_staged(memory_file: Path, *, follow_links: bool = True, evidence_root: Path | None = None,
                 reviews: Path | None = None, verifier: Verifier | None = None, as_of: date | None = None,
                 stale_days: int = 180, min_coverage: float = 0.5, max_judged: int | None = None,
                 exclude: Iterable[str] = ()) -> AuditReport:
    """Audit every claim in the index copy of ``memory_file``. Evidence is the working tree."""
    repo = open_repository(memory_file)
    with Objects(repo) as objects:
        snap = repo.index(objects, follow_links)
    memory_paths = [p for name in snap.files if (p := repo.worktree_path(name))]
    report = audit_claims(snap.claims, memory_file=str(memory_file), files_read=snap.files,
                          memory_paths=memory_paths, evidence_root=evidence_root, reviews=reviews,
                          verifier=verifier, as_of=as_of, stale_days=stale_days, min_coverage=min_coverage,
                          max_judged=max_judged, exclude=exclude)
    report.skipped_evidence[:0] = [s.model_copy(update={"reason": "memory skipped: " + s.reason})
                                  for s in snap.skipped]
    report.limitations.append(STAGED_AUDIT_NOTE)
    return report


def _files(files: list[str]) -> str:
    return f"{len(files)} file(s)" + (": " + ", ".join(code_span(f) for f in files) if files else "")


def _where(version: ClaimVersion) -> str:
    return code_span(version.location) + (f" · {code_span(version.section)}" if version.section else "")


def _meta(metadata: dict[str, Value]) -> list[str]:
    return [metadata_line(metadata), ""] if metadata else []


def _point(revision: Revision) -> str:
    """One endpoint of a diff. A missing commit is the working tree, the index, or a repo with no commits."""
    if revision.commit:
        return f"{code_span(revision.rev)} (commit {code_span(revision.commit[:12])})"
    if revision.rev == "working tree":
        return "working tree"
    if revision.rev == "index":
        return "the index (staged content)"
    return f"{code_span(revision.rev)} (no commit yet)"


def diff_to_markdown(report: DiffReport) -> str:
    audit = report.audit
    out = [DIFF_HEADER, "",
           f"- Memory: {code_span(report.memory_file)}",
           f"- Base: {_point(report.base)}, {_files(report.base.files)}",
           f"- Head: {_point(report.head)}, {_files(report.head.files)}",
           f"- Evidence root: {code_span(audit.evidence_root)} (as it is now)" if audit.evidence_root
           else "- Evidence root: none supplied",
           *(["- Excluded from evidence: " + ", ".join(code_span(p) for p in audit.excluded)]
             if audit.excluded else []),
           f"- Verifier: `{audit.verifier}` · as of {audit.as_of} · mode: **{report.mode}** (no memory was modified)",
           *usage_lines(audit),
           "", "| Change | Claims |", "|---|---|"]
    out += [f"| {kind.capitalize()} | {n} |" for kind, n in report.summary.items()]
    out.append("")
    touched = report.summary["added"] + report.summary["changed"]
    if touched:
        out += [f"Audited the {touched} added or changed claim(s) only.", "",
                "| Status | Memories |", "|---|---|"]
        out += [f"| {LABELS[s]} | {n} |" for s, n in audit.summary.items()]
    else:
        out.append("No claim was added or changed, so nothing was audited.")
    out.append("")
    for kind, title in (("changed", "Changed"), ("added", "Added"), ("removed", "Removed")):
        entries = [c for c in report.changes if c.change == kind]
        if not entries:
            continue
        out += [f"## {title}", ""]
        for c in entries:
            if kind == "changed":
                out += [f"### `{c.old.memory_id}` → `{c.new.memory_id}` · {LABELS[c.status]}", "",
                        f"**Old** at {_where(c.old)}", "", _quote(c.old.text, scrub=True), "",
                        *(_meta(c.old.metadata) if c.old.metadata != c.new.metadata else []),
                        f"**New** at {_where(c.new)} (similarity {c.similarity:.2f})", "",
                        _quote(c.new.text, scrub=True), "", *_meta(c.new.metadata)]
            elif kind == "added":
                out += [f"### `{c.new.memory_id}` · {LABELS[c.status]}", "", _quote(c.new.text, scrub=True), "",
                        _where(c.new), "", *_meta(c.new.metadata)]
            else:
                out += [f"### `{c.old.memory_id}`", "", _quote(c.old.text, scrub=True), "",
                        f"Was at {_where(c.old)}", "", *_meta(c.old.metadata)]
    rescoped = [c for c in report.changes if c.change == "unchanged" and c.old.metadata != c.new.metadata]
    if rescoped:
        out += ["## Frontmatter changed", "",
                "These claims kept their wording, but the frontmatter that says where they apply changed. "
                "They were not audited.", ""]
        for c in rescoped:
            old = metadata_line(c.old.metadata) if c.old.metadata else "Frontmatter: none"
            new = metadata_line(c.new.metadata).removeprefix("Frontmatter: ") if c.new.metadata else "none"
            out.append(f"- `{c.new.memory_id}` at {_where(c.new)}: {old} → {new}")
        out.append("")
    if audit.findings:
        out += ["## Audit of added and changed claims", ""]
        for finding in audit.findings:
            out += _finding(finding, scrub=True)
    if audit.skipped_evidence:
        out += ["## Skipped evidence", ""]
        out += [skipped_line(s.path, s.reason, show_text) for s in audit.skipped_evidence]
        out.append("")
    out += ["## Limitations", ""] + [f"- {line}" for line in report.limitations + audit.limitations]
    return "\n".join(out) + "\n"


# --- history --------------------------------------------------------------------------------------

class CommitInfo(BaseModel):
    commit: str | None = None  # None: uncommitted changes in the working tree
    date: str | None = None  # author date, ISO 8601
    author: str | None = None
    subject: str


WORKING_TREE = CommitInfo(subject="uncommitted changes in the working tree")


class ClaimHistory(BaseModel):
    memory_id: str
    text: str
    location: str
    section: str | None = None
    metadata: dict[str, Value] = {}
    introduced: CommitInfo | None  # first appearance of this ID; None: before the scanned commits
    last_changed: CommitInfo | None  # latest edit, including case/whitespace edits, moves to another
    # file or section (a renamed heading is not a move) and changes to its file's frontmatter metadata
    replaced: ClaimVersion | None = None  # the earlier version this claim was edited from, if seen


class RemovedClaim(BaseModel):
    memory_id: str
    text: str
    location: str
    section: str | None = None
    introduced: CommitInfo | None
    removed: CommitInfo


class HistoryReport(BaseModel):
    tool: str = "memfalsify"
    version: str = __version__
    mode: Literal["read_only"] = "read_only"
    memory_file: str
    files: list[str]
    head: str | None  # the HEAD commit the scan started from
    commits_scanned: int
    max_commits: int
    complete: bool  # the scan reached the first commit that touched the memory files
    shallow: bool = False  # the scan stopped at the edge of a shallow clone
    claims: list[ClaimHistory]
    removed: list[RemovedClaim]
    limitations: list[str]


HISTORY_LIMITATIONS = [
    "History follows the first-parent line of HEAD: changes merged with a merge commit are attributed to it.",
    "Commits are compared as whole snapshots of the memory file and the files it links to; a renamed memory "
    "file is not followed, so its claims appear in the commit that created the new name.",
    "Claims are identified by ID (normalized text); an edit is followed to its earlier version only when "
    f"`diff` would pair them (same file and section, term similarity at least {SIMILAR}).",
]


@dataclass
class _Lineage:
    claim: Claim
    introduced: CommitInfo | None
    last_changed: CommitInfo | None
    replaced: Claim | None = None


# git %aI is strict ISO-8601: no fractional seconds. Z or a numeric offset, as in +00:00 and -05:00.
_GIT_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:Z|[+-]\d{2}:\d{2})$")


def parse_git_log(out: bytes) -> list[CommitInfo]:
    """Commits from ``git log -z --format=%H%x00%aI%x00%an%x00%s``.

    A record that is not four fields, a non-hex commit id, or an author date that is not strict
    ISO-8601 raises ``HistoryError``. Author and subject are untrusted and go through ``printable``.
    """
    fields = out.decode("utf-8", "replace").split("\0")
    if fields and fields[-1] == "":
        fields.pop()
    if len(fields) % 4 or any(not HEX.fullmatch(fields[i]) for i in range(0, len(fields), 4)):
        raise HistoryError("cannot parse git log output")
    commits = []
    for index in range(0, len(fields), 4):
        date = fields[index + 1]
        if not _GIT_DATE.fullmatch(date):
            raise HistoryError("cannot parse git log output")
        commits.append(CommitInfo(commit=fields[index], date=date, author=printable(fields[index + 2]),
                                  subject=printable(fields[index + 3])))
    return commits


def _log(repo: Repository, head: str, paths: list[str], limit: int) -> list[CommitInfo]:
    """Up to ``limit`` first-parent commits from ``head`` that touched ``paths``, newest first."""
    out = repo.run("log", "--first-parent", "-z", f"--max-count={limit}", "--format=%H%x00%aI%x00%an%x00%s",
                   "--end-of-options", head, "--", *paths)
    return parse_git_log(out)


def memory_history(memory_file: Path, max_commits: int = 100, follow_links: bool = True) -> HistoryReport:
    """Attribute each current claim to the commits that introduced and last changed it, and list removals."""
    if max_commits < 1:
        raise HistoryError("--max-commits must be at least 1")
    repo = open_repository(memory_file)
    head = repo.head()
    current = repo.worktree(follow_links)
    commits: list[CommitInfo] = []
    snapshots: dict[str, Snapshot] = {}
    baseline, complete = Snapshot(), True
    with Objects(repo) as objects:
        paths = {repo.prefix + name for name in [repo.memory_name, *current.links]}
        for _ in range(8):  # grows to every file linked within the window; two rounds are usual
            listed = _log(repo, head, sorted(paths), max_commits + 1) if head else []
            complete = len(listed) <= max_commits
            commits = listed[:max_commits]
            for info in listed:
                if info.commit not in snapshots:
                    snapshots[info.commit] = repo.at(objects, info.commit, follow_links)
            baseline = Snapshot() if complete else snapshots[listed[-1].commit]
            seen = {repo.prefix + name for info in listed for name in snapshots[info.commit].links}
            if seen <= paths:
                break
            paths |= seen
    shallow = complete and bool(commits) and repo.cut_off(commits[-1].commit)
    if shallow:
        # The oldest commit's parent was not fetched, so what it shows may be older: use it as the baseline.
        baseline, commits, complete = snapshots[commits[-1].commit], commits[:-1], False
    state = {c.id: _Lineage(c, None, None) for c in baseline.claims}
    removals: list[RemovedClaim] = []
    previous = baseline
    for info, snapshot in [*((c, snapshots[c.commit]) for c in reversed(commits)), (WORKING_TREE, current)]:
        following: dict[str, _Lineage] = {}
        for change in compare(previous.claims, snapshot.claims):
            if change.kind == "unchanged":
                lineage = state[change.old.id]
                following[change.new.id] = replace(lineage, claim=change.new,
                                                   last_changed=info if change.moved else lineage.last_changed)
            elif change.kind == "changed":
                following[change.new.id] = _Lineage(change.new, info, info, change.old)
            elif change.kind == "added":
                following[change.new.id] = _Lineage(change.new, info, info)
            else:
                old = change.old
                removals.append(RemovedClaim(memory_id=old.id, text=old.text, location=old.location,
                                             section=old.section, introduced=state[old.id].introduced, removed=info))
        state, previous = following, snapshot
    latest: dict[str, RemovedClaim] = {}
    for removal in removals:
        if removal.memory_id not in state:
            latest.pop(removal.memory_id, None)
            latest[removal.memory_id] = removal
    claims = [ClaimHistory(memory_id=c.id, text=c.text, location=c.location, section=c.section,
                           metadata=c.metadata, introduced=state[c.id].introduced,
                           last_changed=state[c.id].last_changed,
                           replaced=ClaimVersion.of(state[c.id].replaced) if state[c.id].replaced else None)
              for c in current.claims]
    return HistoryReport(memory_file=str(memory_file), files=current.files, head=head,
                         commits_scanned=len(commits), max_commits=max_commits, complete=complete, shallow=shallow,
                         claims=claims, removed=list(reversed(latest.values())),
                         limitations=HISTORY_LIMITATIONS + [f"Memory skipped: {s.path}: {s.reason}"
                                                            for s in current.skipped])


def _commit(info: CommitInfo | None) -> str:
    if info is None:
        return "before the scanned commits"
    if info.commit is None:
        return info.subject
    return f"`{info.commit[:12]}` {(info.date or '')[:10]} · {info.author} · {info.subject}"


def history_to_markdown(report: HistoryReport) -> str:
    if report.head is None:
        scanned = "no commits yet; everything below is uncommitted"
    else:
        scanned = (f"{report.commits_scanned} commit(s) on the first-parent line of HEAD (`{report.head[:12]}`)"
                   + (", back to the first one that touched these files" if report.complete
                      else "; the clone is shallow, so older commits were not available" if report.shallow
                      else f"; older commits were not read (`--max-commits {report.max_commits}`)"))
    out = [HISTORY_HEADER, "",
           f"- Memory: {code_span(report.memory_file)} ({_files(report.files)})",
           f"- Scanned: {scanned}, plus the working tree",
           f"- Mode: **{report.mode}** (nothing was modified)", "",
           f"## Current claims ({len(report.claims)})", ""]
    for claim in report.claims:
        out += [f"### `{claim.memory_id}`", "", _quote(claim.text, scrub=True), "",
                code_span(claim.location) + (f" · {code_span(claim.section)}" if claim.section else ""), "",
                *_meta(claim.metadata),
                f"- Introduced: {_commit(claim.introduced)}",
                "- Last changed: " + ("same as introduced" if claim.last_changed == claim.introduced
                                      else _commit(claim.last_changed))]
        if claim.replaced:
            out.append(f"- Edited from `{claim.replaced.memory_id}`: {show_text(claim.replaced.text)}")
        out.append("")
    if report.removed:
        out += [f"## Removed claims ({len(report.removed)})", ""]
        for gone in report.removed:
            out += [f"### `{gone.memory_id}`", "", _quote(gone.text, scrub=True), "",
                    f"Was at {code_span(gone.location)}"
                    + (f" · {code_span(gone.section)}" if gone.section else ""), "",
                    f"- Removed: {_commit(gone.removed)}", f"- Introduced: {_commit(gone.introduced)}", ""]
    out += ["## Limitations", ""] + [f"- {line}" for line in report.limitations]
    return "\n".join(out) + "\n"
