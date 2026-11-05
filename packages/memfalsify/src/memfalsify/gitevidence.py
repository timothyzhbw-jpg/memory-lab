"""Turn git history into evidence files: ``memfalsify evidence from-git``.

One small Markdown file per commit, named by the commit's hash, holding its full hash, author date, subject and
body. The same commits give the same bytes, and a re-run leaves existing files alone. Git runs as in ``gitstore``:
argument lists, the pinned options and environment, ``--end-of-options``, revisions validated and resolved first.
Nothing here writes to the repository, and only ``git log`` and ``git rev-parse`` run, so no hook does.

Commit messages are untrusted. They never reach a file name or the terminal: names come from the validated hash,
and only names and counts are printed. In the file, a fixed header comes first, so a message cannot make the file
look like a report (which ``scan`` would skip) or change the header a reader sees first.

Control characters are stored as visible escapes, not raw and not removed. Evidence hashes and quote offsets cover
the file's bytes, so a quote stays exact either way. Raw, a quote could not be typed into ``reviews.jsonl`` from
what a report shows, and printing the file could drive a terminal. Removed, a zero-width or bidi character
would vanish without a trace, and the text could read as a word the author's bytes did not hold. The commit named
by the full hash keeps the original bytes. Bytes that are not UTF-8 become U+FFFD, so the file stays readable.
"""
from __future__ import annotations

import os
import re
import stat
import subprocess
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from memcore.display import one_line

from .gitstore import (GIT_OPTIONS, HEX, GitError, HistoryError, _env, _failure, _git_executable, _resolve, _run,
                       check_revision, printable)

SHORT_HASH = 12
MAX_MESSAGE_BYTES = 256 * 1024  # of one commit's stored text; the evidence scan reads files up to 2 MB
MAX_LOG_BYTES = 256 * 1024 * 1024  # of the whole git log output, so a hostile repository cannot fill memory
_LOG_FORMAT = "--format=%H%x00%aI%x00%s%x00%b"
_READ_OPTIONS = ("--no-replace-objects", "-c", "i18n.logOutputEncoding=UTF-8", "-c", "log.mailmap=false")
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
_DATETIME = re.compile(r"(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2}(?::\d{2})?)(Z|[+-]\d{2}:\d{2})?")
_RELATIVE = re.compile(r"(\d{1,6})[ .](second|minute|hour|day|week|month|year)s?(?:[ .]ago)?", re.IGNORECASE)
_EPOCH = datetime(1970, 1, 2, tzinfo=timezone.utc)  # git misreads anything earlier as a recent date
_SINCE_HELP = ("a date such as 2026-01-31 (midnight UTC), 2026-01-31T12:00:00+02:00 or '30 days ago', "
               "or a commit, tag or branch in this repository")


@dataclass(frozen=True)
class Commit:
    full_hash: str
    author_date: str
    subject: str
    body: str


@dataclass
class FromGit:
    """What ``evidence_from_git`` did: file names written and already there, how ``--since`` was read, and whether
    ``max_commits`` cut the window short."""
    output: Path
    since: str
    written: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    cut_short: bool = False


def _bad_date(value: str) -> HistoryError:
    return HistoryError(f"--since: {printable(value)!r} is not a valid date; use {_SINCE_HELP}")


def _utc(moment: datetime) -> str:
    return max(moment, _EPOCH).astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S +0000")


def _ago(count: int, unit: str, now: datetime) -> datetime:
    """``count`` units before ``now``; months and years by the calendar, as git counts them."""
    if unit in ("month", "year"):
        months = now.year * 12 + now.month - 1 - count * (12 if unit == "year" else 1)
        year, month = divmod(months, 12)
        if year < _EPOCH.year:
            return _EPOCH
        last = (datetime(year + (month == 11), (month + 1) % 12 + 1, 1) - datetime(year, month + 1, 1)).days
        return now.replace(year=year, month=month + 1, day=min(now.day, last))
    try:
        return now - timedelta(**{unit + "s": count})
    except OverflowError:
        return _EPOCH


def date_argument(value: str, now: datetime | None = None) -> str | None:
    """``value`` as an absolute date git reads the same way on every machine, or None if it does not look like one.

    git fills a missing time of day with the current time, reads an unknown word as now, and misreads large
    relative dates and dates before 1970 without a word. So only these forms reach it, spelled out in UTC, and
    earlier dates are moved up to 1970-01-02. A relative date still depends on when it runs."""
    try:
        if _DATE.fullmatch(value):
            day = date.fromisoformat(value)
            return _utc(datetime(day.year, day.month, day.day, tzinfo=timezone.utc))
        if match := _DATETIME.fullmatch(value):
            day, time, zone = match.groups()
            return _utc(datetime.fromisoformat(f"{day}T{time}{'+00:00' if zone in (None, 'Z') else zone}"))
    except (ValueError, OverflowError):
        raise _bad_date(value) from None
    if match := _RELATIVE.fullmatch(value):
        return _utc(_ago(int(match.group(1)), match.group(2).lower(), now or datetime.now(timezone.utc)))
    return None


def _log_range(git: str, repo: Path, since: str, head: str) -> tuple[list[str], str]:
    """The ``git log`` arguments that select the commits, and how ``since`` was read, for the user."""
    if (when := date_argument(since)) is not None:
        return [f"--since={when}", "--end-of-options", head], f"the date {when} (committer date)"
    try:
        commit = _resolve(git, repo, since)
    except GitError:
        raise
    except HistoryError:
        raise HistoryError(f"--since: {printable(since)!r} is neither a date nor a commit in this repository; "
                           f"use {_SINCE_HELP}") from None
    return ["--end-of-options", f"{commit}..{head}"], f"commit {commit[:SHORT_HASH]} (later commits up to HEAD)"


def parse_log(out: bytes) -> list[Commit]:
    """Commits from ``git log -z`` with ``_LOG_FORMAT``: NUL-separated fields, four per commit. A message cannot
    add a field, since git stops at a NUL in a message. Anything else raises ``HistoryError``."""
    fields = out.split(b"\0")
    if fields and fields[-1] == b"":
        fields.pop()
    if len(fields) % 4 or any(not HEX.fullmatch(fields[i].decode("ascii", "replace"))
                              for i in range(0, len(fields), 4)):
        raise HistoryError("cannot parse git log output")
    return [Commit(*(field_bytes.decode("utf-8", "replace") for field_bytes in fields[index:index + 4]))
            for index in range(0, len(fields), 4)]


def _read_log(git: str, repo: Path, selection: list[str], limit: int) -> bytes:
    args = [git, "-C", str(repo), *GIT_OPTIONS, *_READ_OPTIONS, "log", "--no-patch", "--no-color", "-z",
            f"--max-count={limit}", _LOG_FORMAT, *selection, "--"]
    try:
        process = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                   stderr=subprocess.DEVNULL, env=_env())
    except OSError as exc:
        raise GitError(f"cannot run git: {exc}", "cannot run git") from exc
    with process:
        data = process.stdout.read(MAX_LOG_BYTES + 1)
        if len(data) > MAX_LOG_BYTES:
            process.kill()
            raise HistoryError("git log printed more than 256 MB; narrow --since or lower --max-commits")
    if process.returncode:
        raise GitError(f"git log failed with exit code {process.returncode}", "git log failed")
    return data


def _inert(text: str) -> str:
    """Line breaks as LF, tabs kept, every other control, format or separator character as a visible escape."""
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    return "\n".join("\t".join(one_line(part) for part in line.split("\t")) for line in lines)


def _bounded(text: str) -> str:
    data = text.encode("utf-8")
    if len(data) <= MAX_MESSAGE_BYTES:
        return text
    kept = data[:MAX_MESSAGE_BYTES].decode("utf-8", "ignore")
    return f"{kept}\n\n[Cut here by memfalsify: the message is longer than {MAX_MESSAGE_BYTES} bytes. Read the commit.]"


def render(commit: Commit) -> bytes:
    """The evidence file of one commit, as exact bytes: the header (fixed lines first), then the message."""
    subject = _inert(commit.subject).strip("\n") or "(no subject)"
    body = _inert(commit.body).strip("\n")
    message = _bounded(subject + ("\n\n" + body if body else ""))
    return (f"# Commit {commit.full_hash[:SHORT_HASH]}\n\n"
            f"- Full hash: {commit.full_hash}\n"
            f"- Author date: {one_line(commit.author_date) or 'unknown'}\n\n"
            f"{message}\n").encode("utf-8")


def file_names(hashes: list[str]) -> dict[str, str]:
    """Commit hash -> file name: the short hash, or the full hash for commits that share a short one (a pair of
    commits made to share 12 hex digits is cheap to craft, and neither may hide the other)."""
    shorts = Counter(full[:SHORT_HASH] for full in hashes)
    return {full: f"{full[:SHORT_HASH] if shorts[full[:SHORT_HASH]] == 1 else full}.md" for full in hashes}


def _same_file(path: Path, data: bytes) -> bool | None:
    """True if ``path`` holds exactly ``data``, None if nothing is there, False for anything else (a link counts
    as anything else, even to identical bytes)."""
    try:
        status = os.lstat(path)
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(status.st_mode) or status.st_size != len(data):
        return False
    return path.read_bytes() == data


def _git_locations(git: str, repo: Path) -> list[Path]:
    """The repository's git directory and common directory; raises if ``repo`` is not in a repository."""
    proc = _run(git, repo, ["rev-parse", "--absolute-git-dir", "--git-common-dir"])
    if proc.returncode:
        where = f"{printable(str(repo))} is not inside a git repository"
        raise GitError(f"{where} ({_failure(proc)})", where)
    try:
        lines = proc.stdout.decode("utf-8").replace("\r\n", "\n").split("\n")
    except UnicodeDecodeError as exc:
        raise HistoryError("cannot read the repository location from git") from exc
    if len(lines) != 3 or not lines[0] or not lines[1] or lines[2]:
        raise HistoryError("cannot read the repository location from git")
    return [Path(lines[0]).resolve(), (repo / lines[1]).resolve()]


def evidence_from_git(since: str, output: Path, repo: Path = Path("."), max_commits: int = 500) -> FromGit:
    """Write one evidence file per commit selected by ``since``, newest ``max_commits`` first, into ``output``.

    Raises ``HistoryError``, with nothing written, if ``since`` or the repository is unusable, ``output`` is inside
    the git directory, or a file there already holds different content."""
    check_revision(since)
    if max_commits < 1:
        raise HistoryError("--max-commits must be at least 1")
    git = _git_executable()
    repo, output = Path(repo), Path(output)
    if not repo.is_dir():
        raise HistoryError(f"not a directory: {printable(str(repo))}")
    target = output.resolve()
    for place in _git_locations(git, repo):
        if target == place or target.is_relative_to(place):
            raise HistoryError("--output is inside the git directory; evidence never goes into the repository itself")
    try:
        head = _resolve(git, repo, "HEAD")
    except GitError:
        raise
    except HistoryError:
        raise HistoryError(f"{printable(str(repo))} has no commits yet") from None
    selection, read_as = _log_range(git, repo, since, head)
    commits = parse_log(_read_log(git, repo, selection, max_commits + 1))
    result = FromGit(output=output, since=read_as, cut_short=len(commits) > max_commits)
    commits = commits[:max_commits]
    names = file_names([commit.full_hash for commit in commits])
    planned: list[tuple[str, bytes]] = []
    for commit in commits:
        name, data = names[commit.full_hash], render(commit)
        same = _same_file(output / name, data)
        if same is False:
            raise HistoryError(f"{printable(str(output / name))} already exists with different content; nothing was "
                               "written. Use a new --output directory")
        if same:
            result.unchanged.append(name)
        else:
            planned.append((name, data))
    output.mkdir(parents=True, exist_ok=True)
    for name, data in planned:
        _write_new(output / name, data)
        result.written.append(name)
    return result


def _write_new(path: Path, data: bytes) -> None:
    """A complete new file or none: exclusive, so never over a file or a link that appeared after the check, and
    a failed write leaves nothing a re-run would mistake for a different commit."""
    try:
        handle = open(path, "xb")
    except FileExistsError:
        raise HistoryError(f"{printable(str(path))} appeared while writing; run again") from None
    try:
        with handle:
            handle.write(data)
    except OSError:
        path.unlink(missing_ok=True)
        raise
