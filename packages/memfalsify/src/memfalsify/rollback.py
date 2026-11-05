"""Print (never apply) a patch that restores one claim's wording as of a revision, or re-adds it."""
from __future__ import annotations

import difflib
import re
from dataclasses import dataclass
from pathlib import Path

from memcore.reading import read_file_bytes

from .blocks import Block, block_at, blocks
from .corpus import ROLLBACK_HEADER
from .gitstore import HistoryError, Objects, Snapshot, open_repository, printable
from .history import Change, compare
from .formats import frontmatter
from .memory import Claim, block_sentences, claim_id, claim_ids, on_one_line

CLAIM_ID = re.compile(r"m-[0-9a-f]{8}")
LIST_MARKER = re.compile(r"^\s*(?:(?:>|[-*+]|\d+[.)]|\[[ xX]\])\s+)*")


def _lines(text: str, where: str) -> list[str]:
    """Split keeping line endings; only LF and CRLF, so line numbers match the parser's and git's."""
    if re.search("\r(?!\n)", text):
        raise HistoryError(f"{printable(where)} uses bare CR line endings; rollback supports LF and CRLF only")
    return re.findall(r"[^\n]*\n|[^\n]+$", text)


def _body(line: str) -> str:
    return line[:-2] if line.endswith("\r\n") else line[:-1] if line.endswith("\n") else line


def _ending(lines: list[str]) -> str:
    return "\r\n" if lines and lines[0].endswith("\r\n") else "\n"


def _split_location(location: str) -> tuple[str, int]:
    path, _, line = location.rpartition(":")
    return path, int(line)


def _quote_path(path: str) -> str:
    """Git's C-style quoting for names that need it, so `git apply` reads the path back exactly."""
    if not re.search(r'["\\\x00-\x1f\x7f]', path):
        return path
    escapes = {'"': '\\"', "\\": "\\\\", "\t": "\\t", "\n": "\\n", "\r": "\\r"}
    quoted = "".join(escapes.get(ch, f"\\{ord(ch):03o}" if ord(ch) < 32 or ord(ch) == 127 else ch) for ch in path)
    return f'"{quoted}"'


def _patch(repo_path: str, before: list[str], after: list[str]) -> str:
    a, b = _quote_path("a/" + repo_path), _quote_path("b/" + repo_path)
    out = [f"diff --git {a} {b}\n"]
    for line in difflib.unified_diff(before, after, a, b, n=3):
        out.append(line if line.endswith("\n") else line + "\n\\ No newline at end of file\n")
    return "".join(out)


def _spans(block: Block, memory_id: str) -> list[tuple[int, int]]:
    """Raw spans (markup included) of the sentences in one block whose claim ID is ``memory_id``."""
    return [(start, end) for text, start, end in block_sentences(block) if claim_id(text) == memory_id]


def _old_sentence(snapshot: Snapshot, claim: Claim) -> tuple[str, Block]:
    """(the raw sentence with its markup, its whole block) of ``claim`` in ``snapshot``. A wrapped sentence keeps its
    line breaks."""
    block = block_at(snapshot.texts[claim.path].split("\n"), claim.line)
    spans = _spans(block, claim.id) if block else []
    if not spans:
        raise HistoryError(f"cannot locate {claim.id} in {printable(claim.path)}:{claim.line}")
    return block.raw[spans[0][0]:spans[0][1]], block


def _with_endings(text: str, replaced: list[str], ending: str) -> list[str]:
    """``text``, the new form of the block that was ``replaced``, as lines with endings: those it had where they still
    fit, and the block's last ending on its last line, which may be none at the end of a file."""
    pieces = text.split("\n")
    endings = [line[len(_body(line)):] for line in replaced]
    return [piece + (endings[-1] if at == len(pieces) - 1 else endings[at] if at < len(endings) - 1 else ending)
            for at, piece in enumerate(pieces)]


def _restored(lines: list[str], place: Block, spans: list[tuple[int, int]], sentence: str, ending: str,
              memory_id: str) -> list[str] | None:
    """``lines`` with the old wording in those spans of ``place``, wrapped as it was or, when that no longer reads as the
    same claim here (a quote's marker in a list, say), on one line. None if neither brings the claim back."""
    for wording in dict.fromkeys((sentence, on_one_line(sentence))):
        edited = place.raw
        for start, end in reversed(spans):
            edited = edited[:start] + wording + edited[end:]
        result = (lines[:place.first] + _with_endings(edited, lines[place.first:place.last + 1], ending)
                  + lines[place.last + 1:])
        if memory_id in claim_ids("".join(result)):
            return result
    return None


def _with_claim(lines: list[str], at: int, pieces: list[str], ending: str, memory_id: str) -> list[str] | None:
    """``lines`` with the removed claim's lines put back at index ``at``, with a blank line on a side only where the
    file would otherwise read one of them as going on with its neighbor. None if no form brings it back alone."""
    added = [piece + ending for piece in pieces]
    wanted = claim_ids("".join(lines)) | {memory_id}
    for before, after in (([], []), ([ending], []), ([], [ending]), ([ending], [ending])):
        result = lines[:at] + before + added + after + lines[at:]
        if claim_ids("".join(result)) == wanted:
            return result
    return None


def _anchor(old: Snapshot, changes: list[Change], gone: Claim, target: list[str]) -> int:
    """Index in ``target`` (lines of the current file) at which to re-insert the removed claim ``gone``:
    after the block of the nearest earlier claim of its section that still exists (possibly edited), else before the
    block of the nearest later one, else under its section heading, else at the end of the file."""
    in_file = [c for c in old.claims if c.path == gone.path]
    index = next(i for i, c in enumerate(in_file) if c.id == gone.id)
    present: dict[str, int] = {}  # old claim ID -> its line in the current file
    for change in changes:
        if change.old is not None and change.new is not None:
            for location in [change.new.location, *change.new.also_at]:
                path, line = _split_location(location)
                if path == gone.path:
                    present.setdefault(change.old.id, line)
    same = [c.section == gone.section for c in in_file]
    bodies = [_body(line) for line in target]
    for i in range(index - 1, -1, -1):
        if same[i] and in_file[i].id in present:
            line = present[in_file[i].id]
            block = block_at(bodies, line)
            return block.last + 1 if block else line
    for i in range(index + 1, len(in_file)):
        if same[i] and in_file[i].id in present:
            line = present[in_file[i].id]
            block = block_at(bodies, line)
            return block.first if block else line - 1
    if gone.section is not None:
        for block in blocks(bodies, frontmatter(bodies)[0]):
            if block.kind == "heading" and block.raw == gone.section:
                i = block.last + 1
                while i < len(target) and not bodies[i].strip():
                    i += 1
                return i
    return len(target)


@dataclass(frozen=True)
class Rollback:
    patch: bytes | None  # None: the claim already reads as it did
    message: str
    files: list[Path]  # the memory files in the working tree (never to be overwritten by the patch file)


def rollback_patch(memory_file: Path, claim: str, to: str, follow_links: bool = True) -> Rollback:
    """A patch restoring ``claim`` as it was at ``to``. A changed claim gets its old wording back wherever it
    occurs; a removed claim is re-added after the nearest surviving claim that preceded it in its section.
    Nothing is written."""
    if not CLAIM_ID.fullmatch(claim):
        raise HistoryError("a claim ID looks like m-1a2b3c4d (see `memfalsify claims`)")
    repo = open_repository(memory_file)
    commit = repo.resolve(to)
    with Objects(repo) as objects:
        old = repo.at(objects, commit, follow_links)
    new = repo.worktree(follow_links)
    files = [p for name in new.files if (p := repo.worktree_path(name))]
    changes = compare(old.claims, new.claims)
    change = next((c for c in changes if claim in ((c.old.id if c.old else None), (c.new.id if c.new else None))),
                  None)
    label = f"{to} ({commit[:12]})"
    if change is None:
        raise HistoryError(f"{claim} is not a claim of the memory now or at {printable(label)}")
    if change.kind == "unchanged":
        return Rollback(None, f"{claim} reads the same as at {printable(label)}; nothing to roll back", files)
    if change.kind == "added":
        raise HistoryError(f"{claim} did not exist at {printable(label)}, so there is no earlier text to restore; "
                           "to drop it, edit the memory by hand")
    sentence, old_block = _old_sentence(old, change.old)
    originals: dict[str, list[str]] = {}
    edits: dict[str, list[str]] = {}

    def lines_of(path: str) -> list[str]:
        if path not in edits:
            file = repo.worktree_path(path)
            if file is None:
                raise HistoryError(f"{printable(path)} no longer exists in the working tree; restore that file first")
            try:
                originals[path] = _lines(read_file_bytes(file).decode("utf-8"), path)
            except UnicodeDecodeError as exc:
                raise HistoryError(f"{printable(path)} is not UTF-8 text") from exc
            edits[path] = list(originals[path])
        return edits[path]

    if change.kind == "changed":
        # Every copy of the current wording gets the old wording back, with the old markup. Bottom up, since the old
        # wording may take more or fewer lines, and each block once: it can hold several copies.
        places = sorted({_split_location(location) for location in [change.new.location, *change.new.also_at]},
                        key=lambda place: (place[0], -place[1]))
        seen: set[tuple[str, int]] = set()
        for path, number in places:
            lines = lines_of(path)
            place = block_at([_body(line) for line in lines], number)
            if place is not None and (path, place.first) in seen:
                continue
            spans = _spans(place, change.new.id) if place else []
            if not spans:
                raise HistoryError(f"cannot locate {change.new.id} in {printable(path)}:{number}")
            seen.add((path, place.first))
            restored = _restored(lines, place, spans, sentence, _ending(lines), change.old.id)
            if restored is None:
                raise HistoryError(f"cannot put {change.old.id} back in {printable(path)}:{number} as it was; "
                                   "edit it by hand")
            edits[path] = restored
        action = f"restore the wording of {change.new.id} to {change.old.id}"
    else:
        gone = change.old
        lines = lines_of(gone.path)
        # A block holding only this claim comes back verbatim; otherwise the sentence alone, under the block's
        # list or quote marker.
        alone = len(block_sentences(old_block)) == 1
        text = old_block.raw if alone else LIST_MARKER.match(old_block.raw).group() + sentence
        at = _anchor(old, changes, gone, lines)
        if at == len(lines) and lines and not lines[-1].endswith("\n"):
            lines[-1] += _ending(lines)
        restored = _with_claim(lines, at, text.split("\n"), _ending(lines), gone.id)
        if restored is None:
            raise HistoryError(f"cannot re-add {gone.id} to {printable(gone.path)} so that it reads as it did; "
                               "edit it by hand")
        edits[gone.path] = restored
        action = f"re-add removed claim {gone.id}"
    body = "".join(_patch(repo.prefix + path, originals[path], lines) for path, lines in edits.items()
                   if originals[path] != lines)
    if not body:
        return Rollback(None, f"{claim} reads the same as at {printable(label)}; nothing to roll back", files)
    header = (f"{ROLLBACK_HEADER}: {action} as of commit {commit}\n"
              "# Not applied. Review it, then run `git apply <this file>` from the repository root.\n")
    return Rollback((header + body).encode("utf-8"), f"patch to {action}; nothing was modified", files)
