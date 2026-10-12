"""pre-commit hooks for MemFalsify. Read-only.

Claims come from the git index (the blob ``git commit`` will record). Evidence comes from the working
tree, and the hook defaults that root to the repository. The diff hook matches
``memfalsify diff CLAUDE.md --base HEAD --staged --evidence . --strict``.
The CLI leaves ``--strict`` off and, without ``--evidence``, has nothing to contradict a claim.
During ``git commit``, pre-commit hides
unstaged changes to tracked files before the hook runs, so tracked evidence then matches the index;
untracked evidence files stay visible. A hand-run ``--staged`` does not hide those edits.

The manifest launches this module with ``python -X utf8`` so the report is UTF-8 on Windows too.
"""
from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Optional

import typer
from pydantic import ValidationError

from memcore.cli import SafeTyperGroup
from memcore.display import report_text

from . import __version__, jev, llm
from .audit import ATTENTION
from .cli import Format, VerifierChoice, build_verifier, preselection
from .cli import MAX_JUDGED as DEFAULT_MAX_JUDGED
from .gitstore import CONTROL, HistoryError, list_staged_paths, printable, repository_toplevel
from .history import audit_staged, diff_to_markdown, memory_diff
from .report import to_markdown
from .verify import VerifierError

# Basenames the hooks accept. The manifest ``files`` regex must name the same three.
MEMORY_NAMES = frozenset({"CLAUDE.md", "AGENTS.md", "MEMORY.md"})

app = typer.Typer(cls=SafeTyperGroup, no_args_is_help=True, add_completion=False,
                  help="Check staged CLAUDE.md, AGENTS.md and MEMORY.md. Never modifies them.")


def _configure_stdio() -> None:
    """Ask the console for UTF-8. ``-X utf8`` already does this; reconfigure covers other launches."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8")
        except (OSError, ValueError):
            pass


def _fail(message: str) -> None:
    typer.echo(f"error: {report_text(message)}", err=True)
    raise typer.Exit(2)


def _checked_path(name: str, base: Path, toplevel: Path) -> Path | None:
    """A memory path inside ``toplevel``, or None when the basename is not one of the three files.

    Hostile paths (absolutes, ``..``, control characters, symlinks, anything that resolves outside
    the work tree) fail the hook. The index is then read by that literal path, not by following a link.
    """
    if not name or CONTROL.search(name) or "\\" in name:
        _fail(f"refusing path {printable(name)!r}")
    slash = name
    posix = PurePosixPath(slash)
    if posix.is_absolute() or PureWindowsPath(name).is_absolute() or ":" in slash:
        _fail(f"refusing path {printable(name)!r}")
    parts = tuple(part for part in posix.parts if part != ".")
    if not parts or any(part in ("", "..") for part in parts):
        _fail(f"refusing path {printable(name)!r}")
    if parts[-1] not in MEMORY_NAMES:
        typer.echo(f"skipping {printable(name)}: only CLAUDE.md, AGENTS.md and MEMORY.md are checked", err=True)
        return None
    path = base.joinpath(*parts)
    root = toplevel.resolve()
    try:
        resolved = path.resolve()
    except OSError as exc:
        _fail(f"refusing path {printable(name)!r}: {exc}")
    if not resolved.is_relative_to(root):
        _fail(f"refusing path {printable(name)!r}: it resolves outside the repository")
    cursor = path
    while True:
        if cursor.is_symlink():
            _fail(f"refusing symlink {printable(name)!r}")
        if cursor.resolve() == root or cursor == cursor.parent:
            break
        if not cursor.exists() and cursor != path:
            break
        cursor = cursor.parent
    if path.is_dir():
        _fail(f"{printable(name)} is a directory")
    return path


def _dedupe(paths: list[Path]) -> list[Path]:
    seen: set[str] = set()
    chosen = []
    for path in paths:
        key = str(path.resolve())
        if key in seen:
            continue
        seen.add(key)
        chosen.append(path)
    return chosen


def memory_paths(names: list[str] | None, cwd: Path) -> list[Path]:
    """Files to check. With no names, the staged CLAUDE.md, AGENTS.md and MEMORY.md in ``cwd``'s work tree.

    Names pre-commit passes are relative to ``cwd``. Paths discovered from the index are relative to
    the work-tree root, which is where ``git diff --cached --name-only`` counts from.
    """
    toplevel = repository_toplevel(cwd).resolve()
    if not names:
        found = []
        for name in list_staged_paths(toplevel):
            if PurePosixPath(name).name not in MEMORY_NAMES:
                continue
            path = _checked_path(name, toplevel, toplevel)
            if path is not None:
                found.append(path)
        return _dedupe(found)
    chosen = []
    for name in names:
        path = _checked_path(name, cwd, toplevel)
        if path is not None:
            chosen.append(path)
    return _dedupe(chosen)


def _attention(summary: dict[str, int]) -> int:
    return sum(n for status, n in summary.items() if status in ATTENTION)


def _execute(mode: str, filenames: list[str] | None, *, evidence: Path | None, exclude: list[str] | None,
             reviews: Path | None, fmt: Format, as_of: str | None, stale_days: int, follow_links: bool,
             strict: bool, verifier: VerifierChoice, llm_model: str, jev_model: str, accept_at: float,
             max_judged: int, min_coverage: float | None) -> None:
    _configure_stdio()
    try:
        reference = date.fromisoformat(as_of) if as_of else None
    except ValueError as exc:
        _fail(f"invalid --as-of: {exc}")
    try:
        paths = memory_paths(filenames, Path.cwd())
        evidence_root = evidence if evidence is not None else repository_toplevel(Path.cwd())
    except (HistoryError, OSError, ValueError) as exc:
        _fail(str(exc))
    if not paths:
        typer.echo("no staged CLAUDE.md, AGENTS.md or MEMORY.md to check", err=True)
        return
    try:
        built = build_verifier(verifier, llm_model, jev_model, accept_at)
        common = dict(follow_links=follow_links, evidence_root=evidence_root, reviews=reviews, verifier=built,
                      as_of=reference, stale_days=stale_days, exclude=exclude or (),
                      **preselection(verifier, min_coverage, max_judged))
    except (ValidationError, ValueError, OSError, VerifierError) as exc:
        _fail(str(exc))
    attention = 0
    dumped: list[dict] = []
    for path in paths:
        try:
            if mode == "diff":
                report = memory_diff(path, "HEAD", staged=True, **common)
                attention += report.attention
            else:
                report = audit_staged(path, **common)
                attention += _attention(report.summary)
        except (ValidationError, ValueError, OSError, VerifierError) as exc:
            _fail(str(exc))
        if fmt is Format.markdown:
            # Diff Markdown already scrubs. Audit Markdown is literal on the CLI; the hook prints it
            # on a terminal, so the same controls have to come out inert here too.
            text = diff_to_markdown(report) if mode == "diff" else to_markdown(report, scrub=True)
            typer.echo(text, nl=False)
        else:
            dumped.append(report.model_dump(mode="json"))
    if fmt is Format.json:
        payload = {"tool": "memfalsify", "version": __version__, "hook": mode, "attention": attention,
                   "reports": dumped}
        typer.echo(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", nl=False)
    if strict and attention:
        raise typer.Exit(1)


def _argument_files() -> typer.Argument:
    return typer.Argument(None, help="Memory files (pre-commit passes the staged ones). Empty: discover staged files.")


def _option_evidence() -> typer.Option:
    return typer.Option(None, "--evidence", "-e",
                        help="Evidence directory in the working tree (default: the git work-tree root)")


def _option_exclude() -> typer.Option:
    return typer.Option(None, "--exclude", "-x", metavar="GLOB",
                        help="Leave matching evidence files out (fnmatch, relative to --evidence); repeatable")


def _option_reviews() -> typer.Option:
    return typer.Option(None, "--reviews", "-r", exists=True, dir_okay=False,
                        help="JSONL of human-confirmed assertions")


def _option_format() -> typer.Option:
    return typer.Option(Format.markdown, "--format", "-f")


def _option_as_of() -> typer.Option:
    return typer.Option(None, "--as-of", help="Reference date YYYY-MM-DD for staleness (default: today)")


def _option_stale_days() -> typer.Option:
    return typer.Option(180, "--stale-days", help="Dated claims older than this are flagged possibly_stale")


def _option_follow() -> typer.Option:
    return typer.Option(True, "--follow-links/--no-follow-links",
                        help="Also read local .md files linked from the memory file (from the index)")


def _option_strict() -> typer.Option:
    # On by default: a commit hook fails when an audited claim needs attention.
    # NO EVIDENCE and wording issues still exit 0.
    return typer.Option(True, "--strict/--no-strict",
                        help="Exit 1 when a checked claim is challenged, conflicted, disputed or has counterevidence")


def _option_verifier() -> typer.Option:
    return typer.Option(VerifierChoice.lexical, "--verifier",
                        help="lexical (offline, default), llm, jev, dual or cascade")


def _option_llm() -> typer.Option:
    return typer.Option(llm.DEFAULT_MODEL, "--llm-model", help="Claude model for --verifier llm/dual/cascade")


def _option_jev() -> typer.Option:
    return typer.Option(jev.DEFAULT_MODEL, "--jev-model", help="TypeSafe model for --verifier jev/dual/cascade")


def _option_accept() -> typer.Option:
    return typer.Option(0.8, "--accept-at", min=0.0, max=1.0,
                        help="--verifier cascade: accept Jev at this confidence, else ask the LLM")


def _option_max() -> typer.Option:
    return typer.Option(DEFAULT_MAX_JUDGED, "--max-judged", min=1,
                        help="Model verifiers: passages judged per memory (bounds API calls)")


def _option_coverage() -> typer.Option:
    return typer.Option(None, "--min-coverage", min=0.0, max=1.0,
                        help="Share of a memory's terms a passage must contain to be judged")


@app.command("diff")
def diff_command(
    filenames: Optional[list[str]] = _argument_files(),
    evidence: Optional[Path] = _option_evidence(),
    exclude: Optional[list[str]] = _option_exclude(),
    reviews: Optional[Path] = _option_reviews(),
    fmt: Format = _option_format(),
    as_of: Optional[str] = _option_as_of(),
    stale_days: int = _option_stale_days(),
    follow_links: bool = _option_follow(),
    strict: bool = _option_strict(),
    verifier: VerifierChoice = _option_verifier(),
    llm_model: str = _option_llm(),
    jev_model: str = _option_jev(),
    accept_at: float = _option_accept(),
    max_judged: int = _option_max(),
    min_coverage: Optional[float] = _option_coverage(),
) -> None:
    """Audit claims the index adds or changes since HEAD. Fail only when one of those needs attention."""
    _execute("diff", filenames, evidence=evidence, exclude=exclude, reviews=reviews, fmt=fmt, as_of=as_of,
             stale_days=stale_days, follow_links=follow_links, strict=strict, verifier=verifier,
             llm_model=llm_model, jev_model=jev_model, accept_at=accept_at, max_judged=max_judged,
             min_coverage=min_coverage)


@app.command("audit")
def audit_command(
    filenames: Optional[list[str]] = _argument_files(),
    evidence: Optional[Path] = _option_evidence(),
    exclude: Optional[list[str]] = _option_exclude(),
    reviews: Optional[Path] = _option_reviews(),
    fmt: Format = _option_format(),
    as_of: Optional[str] = _option_as_of(),
    stale_days: int = _option_stale_days(),
    follow_links: bool = _option_follow(),
    strict: bool = _option_strict(),
    verifier: VerifierChoice = _option_verifier(),
    llm_model: str = _option_llm(),
    jev_model: str = _option_jev(),
    accept_at: float = _option_accept(),
    max_judged: int = _option_max(),
    min_coverage: Optional[float] = _option_coverage(),
) -> None:
    """Audit every claim in the staged file. Fail when any of them needs attention."""
    _execute("audit", filenames, evidence=evidence, exclude=exclude, reviews=reviews, fmt=fmt, as_of=as_of,
             stale_days=stale_days, follow_links=follow_links, strict=strict, verifier=verifier,
             llm_model=llm_model, jev_model=jev_model, accept_at=accept_at, max_judged=max_judged,
             min_coverage=min_coverage)


def main() -> None:
    _configure_stdio()
    app()


if __name__ == "__main__":
    main()
