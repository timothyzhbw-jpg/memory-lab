"""memfalsify command line. Reads memory and evidence; writes only what you ask for: a report, the demo ``init``
makes, the files ``evidence from-git`` makes. Existing memory is never changed."""
from __future__ import annotations

import json
import shlex
import sys
from collections.abc import Callable, Iterable
from datetime import date
from enum import Enum
from pathlib import Path
from typing import Optional

import typer
from pydantic import ValidationError

from memcore.cli import SafeTyperGroup
from memcore.display import one_line as terminal_text
from memcore.terminal import diagnostic_line
from memcore.display import json_shield as _json_shield

from . import jev, llm, openai_compat
from .audit import AuditReport, AuditSet, audit, has_open_missing_reference, needs_attention
from .auditdir import audit_directory
from .demo import DEFAULT_DIRECTORY, FILES, create_demo, review_line
from .discover import discover, discovery_to_markdown, same_file
from .gitevidence import evidence_from_git
from .history import diff_to_markdown, history_to_markdown, memory_diff, memory_history
from .htmlreport import diff_to_html, set_to_html, to_html
from .memory import claim_record, parse_memory
from .report import LABELS, set_to_markdown, to_markdown
from .rollback import rollback_patch
from .verify import CascadeVerifier, DualVerifier, LexicalVerifier, Verifier, VerifierError

app = typer.Typer(cls=SafeTyperGroup, no_args_is_help=True, add_completion=False,
                  help="Audit agent memory and instruction files against evidence. Never modifies memory.\n\n"
                       "New here? Run: memfalsify init demo && cd demo && memfalsify audit")
evidence_app = typer.Typer(cls=SafeTyperGroup, no_args_is_help=True, add_completion=False,
                           help="Turn project history into evidence files to audit against (--evidence).")

# Memory files are Markdown: MEMORY.md and the instruction files `discover` lists.
MEMORY_KINDS = ("a memory or agent instruction file: MEMORY.md, CLAUDE.md, AGENTS.md, GEMINI.md, "
                ".cursor/rules/*.mdc, .github/copilot-instructions.md, ... (see `memfalsify discover`)")


class Format(str, Enum):
    markdown = "markdown"
    json = "json"


class ReportFormat(str, Enum):
    """`audit` and `diff` also render one self-contained HTML file."""
    markdown = "markdown"
    json = "json"
    html = "html"


class DemoLanguage(str, Enum):
    en = "en"
    zh = "zh"


class VerifierChoice(str, Enum):
    lexical = "lexical"
    llm = "llm"
    jev = "jev"
    dual = "dual"
    cascade = "cascade"
    openai = "openai"


CLAUDE_CHOICES = (VerifierChoice.llm, VerifierChoice.dual, VerifierChoice.cascade)


def build_verifier(choice: VerifierChoice, llm_model: str, jev_model: str, accept_at: float = 0.8,
                   batch: bool = False, progress: Callable[[str], None] | None = None,
                   openai: openai_compat.OpenAISettings | None = None) -> Verifier:
    """Model backends import their heavy dependencies only when constructed. ``batch`` sends the Claude
    requests through the Message Batches API; ``progress`` receives its status lines. ``openai`` configures
    ``--verifier openai``."""
    if batch and choice is VerifierChoice.openai:
        raise ValueError("--batch is not supported with --verifier openai in this version: its requests are "
                         "sent directly, --max-concurrency at a time")
    if batch and choice not in CLAUDE_CHOICES:
        raise ValueError("--batch applies to Claude requests: use it with --verifier llm, dual or cascade")

    def claude() -> Verifier:
        return llm.LLMVerifier(llm_model, batch=True, progress=progress) if batch else llm.LLMVerifier(llm_model)

    if choice is VerifierChoice.lexical:
        return LexicalVerifier()
    if choice is VerifierChoice.llm:
        return claude()
    if choice is VerifierChoice.jev:
        return jev.JevVerifier(jev_model)
    if choice is VerifierChoice.openai:
        settings = openai or openai_compat.OpenAISettings()
        return openai_compat.OpenAICompatVerifier(settings.model or "", settings.provider, settings.base_url,
                                                  settings.api_key_env, response_format=settings.response_format)
    if choice is VerifierChoice.cascade:
        return CascadeVerifier(jev.JevVerifier(jev_model), claude(), accept_at)
    return DualVerifier(claude(), jev.JevVerifier(jev_model))


MAX_JUDGED = 8


def preselection(choice: VerifierChoice, min_coverage: float | None = None, max_judged: int = MAX_JUDGED) -> dict:
    """audit() passage limits: lexical judges every passage covering half the terms, models a bounded few."""
    lexical = choice is VerifierChoice.lexical
    return {"min_coverage": min_coverage if min_coverage is not None else 0.5 if lexical else 0.2,
            "max_judged": None if lexical else max_judged}


def concurrency(choice: VerifierChoice, max_concurrency: int) -> int:
    """Model verifiers wait on the network, so they run requests in parallel; lexical judging stays serial."""
    return 1 if choice is VerifierChoice.lexical else max_concurrency


def openai_settings(model: Optional[str], provider: openai_compat.Provider, base_url: Optional[str],
                    api_key_env: Optional[str],
                    response_format: Optional[openai_compat.ResponseFormat]) -> openai_compat.OpenAISettings:
    """The ``--verifier openai`` options as settings (shared with the pre-commit hooks)."""
    return openai_compat.OpenAISettings(model, provider.value, base_url, api_key_env,
                                        response_format.value if response_format else None)


def _progress(message: str) -> None:
    typer.echo(diagnostic_line(message), err=True)


def _fail(message: str) -> None:
    typer.echo(f"error: {diagnostic_line(message)}", err=True)
    raise typer.Exit(2)


def _emit(text: str, markdown: bool) -> None:
    typer.echo(text if markdown else _json_shield(text), nl=False)


def _same_file(one: Path, other: Path) -> bool:
    """Whether two paths lead to one file: the same place once links are followed (even if nothing is there yet),
    or the same file under two names."""
    try:
        return one.resolve() == other.resolve() or same_file(one, other)
    except (OSError, RuntimeError, ValueError):  # nothing there; Python 3.10 raises RuntimeError on a cyclic link
        return False


def _write(output: Path, data: bytes, protected: Iterable[Path], what: str = "report") -> None:
    """Write exact bytes (no newline translation); never over a memory file, by any name or link."""
    if any(_same_file(output, Path(p)) for p in protected):
        _fail(f"refusing to overwrite a memory file with the {what}")
    try:
        output.write_bytes(data)
    except OSError as exc:
        _fail(f"cannot write the {what}: {exc}")
    typer.echo(f"wrote {terminal_text(str(output))}", err=True)


def _stdout_is_terminal() -> bool:
    try:
        return sys.stdout.isatty()
    except (AttributeError, ValueError):  # no stdout, or a closed one
        return False


def _screen_patch(patch: bytes) -> str:
    """A patch for a person's terminal: control, bidi, zero-width and separator characters shown as ``\\uXXXX``
    escapes (a hostile revision could otherwise clear the screen or reorder text). Line breaks, a CRLF and tabs
    stay, so the layout is the patch's own."""
    def line(raw: str) -> str:
        body, cr = (raw[:-1], "\r") if raw.endswith("\r") else (raw, "")
        return "\t".join(terminal_text(part) for part in body.split("\t")) + cr

    return "\n".join(line(raw) for raw in patch.decode("utf-8", errors="replace").split("\n"))


def _echo_exact(text: str) -> None:
    """ASCII report bytes. Windows text-mode stdout would otherwise turn LF into CRLF."""
    typer.echo(text.encode("ascii"), nl=False)


def _echo_utf8(text: str, err: bool = False) -> None:
    """UTF-8 bytes, whatever the console's encoding: LF stays LF, and a cp1252 stream cannot refuse Chinese text."""
    typer.echo(text.encode("utf-8"), nl=False, err=err)


def _note(message: str) -> None:
    """One line on stderr that may name files, whatever their names are written in."""
    _echo_utf8(diagnostic_line(message) + "\n", err=True)


def _render(fmt: ReportFormat, report, markdown: Callable[..., str], html: Callable[..., str]) -> str:
    if fmt is ReportFormat.json:
        return report.model_dump_json(indent=2) + "\n"
    return html(report) if fmt is ReportFormat.html else markdown(report)


def _memory_paths(result: AuditSet) -> list[Path]:
    """Everything an ``audit`` with no MEMORY argument took for memory: chosen and read, but also chosen or linked
    and then skipped, or passed over, since writing to such a link would land on the file behind it."""
    names = [*result.files, *(place.path for place in result.not_searched)]
    for report in result.reports:
        names += report.files_read
        names += [s.path for s in report.skipped_evidence if s.reason.startswith("memory skipped: ")]
    return [Path(name) for name in names]


def _note_missing_paths(reports: Iterable[AuditReport]) -> None:
    """The status table does not count these, so a note says why ``--strict`` may fail with nothing red in it."""
    count = sum(has_open_missing_reference(finding) for report in reports for finding in report.findings)
    if count:
        # Text, not bytes: only digits come from the report, and the Action reads stderr through a text stream.
        typer.echo(f"note: {count} claim(s) name a path that exists nowhere under the evidence root; "
                   "see missing_reference in the report", err=True)


def _finish_directory(result: AuditSet, fmt: ReportFormat, output: Optional[Path], strict: bool) -> None:
    """Output and exit code of ``audit`` with no MEMORY argument."""
    if not result.files:
        applied = " (--exclude applied)" if result.excluded else ""
        _fail(f"no memory or instruction file found in this directory{applied}; "
              "run `memfalsify init` to make a demo, or name a file: memfalsify audit FILE")
    text = _render(fmt, result, set_to_markdown, set_to_html)
    if output:
        _write(output, text.encode("utf-8"), _memory_paths(result))
    else:
        _echo_utf8(_json_shield(text) if fmt is ReportFormat.json else text)
    claims = sum(len(report.findings) for report in result.reports)
    counts = ", ".join(f"{n} {LABELS[status].lower()}" for status, n in result.summary.items())
    _note(f"note: {claims} claim(s) in {len(result.files)} file(s){': ' + counts if counts else ''}. "
          "Nothing was changed. A flag means \"read the quotes\"; a person decides, in a reviews file.")
    _note_missing_paths(result.reports)
    if strict and any(needs_attention(report) for report in result.reports):
        raise typer.Exit(1)


AUDIT_HELP = """Audit a memory or instruction file and print an evidence-cited report with proposed (unapplied) actions.

With no MEMORY: audit every instruction file `memfalsify discover` finds in the current directory, plus its \
MEMORY.md, in path order, and compare them with that directory unless --evidence says otherwise. Hidden, skipped \
and generated files stay out of the evidence. --exclude also leaves files out of the choice.

Several files share one report. --format json then always gives one object with a list of reports. A note on \
stderr says which files were chosen."""
FROM_GIT_HELP = """Write one small Markdown file per commit (subject, body, author date, full hash), named by its \
short hash.

The result works with --evidence. The same commits give the same bytes, and a re-run adds only new files. Only \
--output is written, and a file holding anything else stops the run; the repository is only read.

Commit messages are untrusted. Control, bidi and zero-width characters are stored as visible \\uXXXX escapes, not raw \
and not removed: quotes stay exact, printing a file is safe, and the full hash leads to the original bytes.

A GitHub version (pull request titles and bodies) is on the roadmap."""


# Options shared by `audit` and `diff`.
MEMORY_ARG = typer.Argument(
    None, exists=True, dir_okay=False,
    help="File to audit, " + MEMORY_KINDS + ". Leave it out to audit every such file in the current directory, "
                                            "plus its MEMORY.md")
GIT_MEMORY_ARG = typer.Argument(..., exists=True, dir_okay=False, help="In a git work tree, " + MEMORY_KINDS)
EVIDENCE = typer.Option(None, "--evidence", "-e", help="Directory of evidence files")
EXCLUDE = typer.Option(
    None, "--exclude", "-x", metavar="GLOB",
    help="Leave matching evidence files out (fnmatch on paths relative to --evidence, e.g. 'examples/**'); "
         "repeatable")
REVIEWS = typer.Option(None, "--reviews", "-r", exists=True, dir_okay=False, help="JSONL of human-confirmed assertions")
FORMAT = typer.Option(Format.markdown, "--format", "-f")
REPORT_FORMAT = typer.Option(
    ReportFormat.markdown, "--format", "-f",
    help="markdown, json, or html (one self-contained page: inline CSS, no scripts, nothing loaded)")
OUTPUT = typer.Option(None, "--output", "-o", help="Write the report here instead of stdout")
AS_OF = typer.Option(None, help="Reference date YYYY-MM-DD for staleness (default: today)")
STALE_DAYS = typer.Option(180, help="Dated claims older than this are flagged possibly_stale")
FOLLOW_LINKS = typer.Option(True, help="Also audit local .md files the memory file links to or @-imports")
VERIFIER = typer.Option(VerifierChoice.lexical, "--verifier",
                        help="lexical (offline), llm (Claude), jev (TypeSafe Jev judgment model), "
                             "dual (llm + jev; disagreements become disputed), "
                             "cascade (jev, escalating unsure answers to llm), "
                             "or openai (any OpenAI-compatible Chat Completions API: see --provider)")
LLM_MODEL = typer.Option(llm.DEFAULT_MODEL, help="Claude model for --verifier llm/dual/cascade")
JEV_MODEL = typer.Option(jev.DEFAULT_MODEL, help="TypeSafe model for --verifier jev/dual/cascade")
OPENAI_MODEL = typer.Option(
    None, "--openai-model", help="--verifier openai: the model, as the provider names it (required; there is no "
                                 "default, because names differ between providers)")
PROVIDER = typer.Option(
    openai_compat.Provider.openai, "--provider",
    help="--verifier openai: preset base URL and key variable: openai (OPENAI_API_KEY), grok (XAI_API_KEY), "
         "deepseek (DEEPSEEK_API_KEY), gemini (GEMINI_API_KEY) or ollama (http://localhost:11434/v1, no key). "
         "Memory and evidence text is sent to that endpoint")
BASE_URL = typer.Option(
    None, "--base-url", metavar="URL",
    help="--verifier openai: base URL of an OpenAI-compatible API, overriding the provider's; https, or http only "
         "for localhost, 127.0.0.1 or ::1")
API_KEY_ENV = typer.Option(
    None, "--api-key-env", metavar="NAME",
    help="--verifier openai: name of the environment variable that holds the API key, overriding the provider's "
         "(never the key itself)")
OPENAI_RESPONSE_FORMAT = typer.Option(
    None, "--openai-response-format",
    help="--verifier openai: what to ask the model to answer in: json_schema (strict structured output), "
         "json_object or text (the instructions still ask for JSON). Default: json_object for --provider deepseek, "
         "json_schema for the other providers and for any --base-url")
ACCEPT_AT = typer.Option(0.8, min=0.0, max=1.0, help="--verifier cascade: accept Jev at this confidence, else ask the LLM")
MAX_JUDGED_OPTION = typer.Option(MAX_JUDGED, min=1, help="Model verifiers: passages judged per memory (bounds API calls)")
MAX_CONCURRENCY = typer.Option(
    4, min=1, help="Model verifiers: API requests in flight at once (results keep their order; not used for "
                   "--batch requests)")
BATCH = typer.Option(
    False, "--batch", help="Send the Claude requests (llm, dual, cascade) through the Message Batches API: half "
                           "the price, but a batch can take up to 24 hours (most end within an hour). Not "
                           "supported with --verifier openai")
MIN_COVERAGE = typer.Option(
    None, min=0.0, max=1.0,
    help="Share of a memory's terms a passage must contain to be judged (default 0.5 lexical, 0.2 models)")


@app.command("init")
def init_command(
    directory: Path = typer.Argument(
        Path(DEFAULT_DIRECTORY), help="Where to write the demo; created if it does not exist"),
    lang: DemoLanguage = typer.Option(DemoLanguage.en, "--lang", help="Language of the demo's memory and evidence"),
) -> None:
    """Write a tiny demo to audit: MEMORY.md with 3 claims, evidence/ with 2 files, an empty reviews.jsonl.

    Works offline. Refuses (exit 2, nothing written) if a target file already exists."""
    try:
        alone = not directory.is_dir() or not any(directory.iterdir())
        create_demo(directory, lang.value)
    except (ValueError, OSError) as exc:
        _fail(str(exc))
    place = f"./{directory}" if str(directory).startswith("-") else str(directory)  # cd would read an option
    steps = ([f"  cd {terminal_text(shlex.quote(place))}"] if directory != Path(".") else [])
    expect = (["You should see one claim the evidence seems to contradict (both quotes shown), one with supporting",
               "evidence and one with no evidence."] if alone else
              ["This directory held other files, and the audit reads those too, so the report can show more than the",
               "demo's three claims. For the demo alone, run:",
               "  memfalsify audit MEMORY.md --evidence evidence"])
    _echo_utf8("\n".join([
        f"Wrote {terminal_text(str(directory))}: " + ", ".join(FILES) + ".",
        "",
        "Next, run:",
        *steps,
        "  memfalsify audit",
        "",
        *expect,
        "",
        "To confirm the contradiction yourself, add this line to reviews.jsonl:",
        f"  {review_line(lang.value)}",
        "Then run:",
        "  memfalsify audit --reviews reviews.jsonl",
        "",
    ]))


@app.command("audit", help=AUDIT_HELP)
def audit_command(
    memory: Optional[Path] = MEMORY_ARG,
    evidence: Optional[Path] = EVIDENCE,
    exclude: Optional[list[str]] = EXCLUDE,
    reviews: Optional[Path] = REVIEWS,
    fmt: ReportFormat = REPORT_FORMAT,
    output: Optional[Path] = OUTPUT,
    as_of: Optional[str] = AS_OF,
    stale_days: int = STALE_DAYS,
    follow_links: bool = FOLLOW_LINKS,
    strict: bool = typer.Option(False, help="Exit 1 if any memory is challenged, conflicted, has counterevidence or "
                                            "names a path that exists nowhere under the evidence root, unless a review "
                                            "confirms that memory"),
    references: bool = typer.Option(
        True, "--references/--no-references",
        help="Report paths the memory names that exist nowhere under the evidence root (missing_reference). Needs "
             "--evidence with the memory file under it, or no MEMORY argument; otherwise nothing is checked"),
    verifier: VerifierChoice = VERIFIER,
    llm_model: str = LLM_MODEL,
    jev_model: str = JEV_MODEL,
    openai_model: Optional[str] = OPENAI_MODEL,
    provider: openai_compat.Provider = PROVIDER,
    base_url: Optional[str] = BASE_URL,
    api_key_env: Optional[str] = API_KEY_ENV,
    openai_response_format: Optional[openai_compat.ResponseFormat] = OPENAI_RESPONSE_FORMAT,
    accept_at: float = ACCEPT_AT,
    max_judged: int = MAX_JUDGED_OPTION,
    min_coverage: Optional[float] = MIN_COVERAGE,
    max_concurrency: int = MAX_CONCURRENCY,
    batch: bool = BATCH,
) -> None:
    try:
        reference = date.fromisoformat(as_of) if as_of else None
        judge = build_verifier(verifier, llm_model, jev_model, accept_at, batch=batch, progress=_progress,
                               openai=openai_settings(openai_model, provider, base_url, api_key_env,
                                                      openai_response_format))
        if memory is None:
            result = audit_directory(Path("."), evidence, reviews, verifier=judge, as_of=reference,
                                     stale_days=stale_days, follow_links=follow_links, exclude=exclude or (),
                                     max_concurrency=concurrency(verifier, max_concurrency), progress=_note,
                                     check_references=references, **preselection(verifier, min_coverage, max_judged))
        else:
            report = audit(memory, evidence, reviews, verifier=judge, as_of=reference, stale_days=stale_days,
                           follow_links=follow_links, exclude=exclude or (),
                           max_concurrency=concurrency(verifier, max_concurrency), check_references=references,
                           **preselection(verifier, min_coverage, max_judged))
    except (ValidationError, ValueError, OSError, VerifierError) as exc:
        _fail(str(exc))
    if memory is None:
        _finish_directory(result, fmt, output, strict)
        return
    text = _render(fmt, report, to_markdown, to_html)
    if output:
        base = Path(memory).absolute().parent.resolve()
        _write(output, text.encode("utf-8"), (Path(memory), *(base / p for p in report.files_read)))
    elif fmt is ReportFormat.html:
        _echo_exact(text)
    else:
        _emit(text, fmt is ReportFormat.markdown)
    _note_missing_paths([report])
    if strict and needs_attention(report):
        raise typer.Exit(1)


@app.command("diff")
def diff_command(
    memory: Path = GIT_MEMORY_ARG,
    base: str = typer.Option(..., "--base", help="Revision to compare against (branch, tag or commit), "
                                                 "e.g. main or the pull request's base commit"),
    head: Optional[str] = typer.Option(None, "--head", help="Revision to compare (default: the working tree). "
                                                            "Not used with --staged."),
    staged: bool = typer.Option(False, "--staged",
                                help="Compare --base with the index (what git commit will record) instead of "
                                     "the working tree. Unstaged edits to memory are ignored. Evidence is still "
                                     "the working tree. Cannot be combined with --head."),
    evidence: Optional[Path] = EVIDENCE,
    exclude: Optional[list[str]] = EXCLUDE,
    reviews: Optional[Path] = REVIEWS,
    fmt: ReportFormat = REPORT_FORMAT,
    output: Optional[Path] = OUTPUT,
    as_of: Optional[str] = AS_OF,
    stale_days: int = STALE_DAYS,
    follow_links: bool = typer.Option(True, help="Also compare local .md files the memory file links to or "
                                                 "@-imports"),
    strict: bool = typer.Option(False, help="Exit 1 if an added or changed memory is challenged, conflicted, "
                                            "disputed or has counterevidence"),
    verifier: VerifierChoice = VERIFIER,
    llm_model: str = LLM_MODEL,
    jev_model: str = JEV_MODEL,
    openai_model: Optional[str] = OPENAI_MODEL,
    provider: openai_compat.Provider = PROVIDER,
    base_url: Optional[str] = BASE_URL,
    api_key_env: Optional[str] = API_KEY_ENV,
    openai_response_format: Optional[openai_compat.ResponseFormat] = OPENAI_RESPONSE_FORMAT,
    accept_at: float = ACCEPT_AT,
    max_judged: int = MAX_JUDGED_OPTION,
    min_coverage: Optional[float] = MIN_COVERAGE,
    max_concurrency: int = MAX_CONCURRENCY,
    batch: bool = BATCH,
) -> None:
    """Show which claims changed between two revisions and audit only the added and changed ones.

    With --staged, the version under test is the index. Evidence is the working tree either way.
    """
    try:
        reference = date.fromisoformat(as_of) if as_of else None
        judge = build_verifier(verifier, llm_model, jev_model, accept_at, batch=batch, progress=_progress,
                               openai=openai_settings(openai_model, provider, base_url, api_key_env,
                                                      openai_response_format))
        report = memory_diff(memory, base, head, follow_links=follow_links, evidence_root=evidence, reviews=reviews,
                             verifier=judge, as_of=reference, stale_days=stale_days, exclude=exclude or (),
                             max_concurrency=concurrency(verifier, max_concurrency), staged=staged,
                             **preselection(verifier, min_coverage, max_judged))
    except (ValidationError, ValueError, OSError, VerifierError) as exc:
        _fail(str(exc))
    text = _render(fmt, report, diff_to_markdown, diff_to_html)
    if output:
        root = Path(memory).resolve().parent
        _write(output, text.encode("utf-8"), (root / p for p in report.base.files + report.head.files))
    elif fmt is ReportFormat.html:
        _echo_exact(text)
    else:
        _emit(text, fmt is ReportFormat.markdown)
    if strict and report.attention:
        raise typer.Exit(1)


@app.command("history")
def history_command(
    memory: Path = GIT_MEMORY_ARG,
    max_commits: int = typer.Option(100, min=1, help="Read at most this many commits that touched the memory"),
    fmt: Format = FORMAT,
    follow_links: bool = typer.Option(True, help="Also trace local .md files the memory file links to or "
                                                 "@-imports"),
) -> None:
    """For each claim, the commits that introduced and last changed it; plus the claims removed, and when."""
    try:
        report = memory_history(memory, max_commits=max_commits, follow_links=follow_links)
    except (ValidationError, ValueError, OSError) as exc:
        _fail(str(exc))
    text = history_to_markdown(report) if fmt is Format.markdown else report.model_dump_json(indent=2) + "\n"
    _emit(text, fmt is Format.markdown)


@app.command("rollback")
def rollback_command(
    memory: Path = GIT_MEMORY_ARG,
    claim: str = typer.Option(..., "--claim", help="Claim ID (from `claims`, `diff` or `history`)"),
    to: str = typer.Option(..., "--to", help="Revision whose wording of the claim to restore"),
    output: Optional[Path] = typer.Option(None, "--output", "-o", help="Write the patch here instead of stdout"),
    follow_links: bool = typer.Option(True, help="Also look in local .md files the memory file links to or "
                                                 "@-imports"),
) -> None:
    """Print (never apply) a patch restoring a claim's text as of a revision, or re-adding a removed claim.

    The patch goes out byte for byte to a pipe, a redirect or --output. On a terminal, control and bidi
    characters in it are shown escaped; use --output for a patch to apply."""
    try:
        result = rollback_patch(memory, claim, to, follow_links=follow_links)
    except (ValidationError, ValueError, OSError) as exc:
        _fail(str(exc))
    if result.patch is not None:
        if output:
            _write(output, result.patch, result.files, "patch")
        elif _stdout_is_terminal():
            shown = _screen_patch(result.patch)
            typer.echo(shown.encode("utf-8"), nl=False)  # bytes, so Windows text mode adds no CR to each LF
            if shown.encode("utf-8") != result.patch:
                typer.echo("note: the patch holds control, bidi or other invisible characters; they are shown "
                           "escaped here. Use --output (or a pipe) for the exact patch.", err=True)
        else:
            typer.echo(result.patch, nl=False)  # bytes go out as-is, so line endings survive on Windows
    typer.echo(result.message, err=True)


@app.command("claims")
def claims_command(
    memory: Path = typer.Argument(..., exists=True, dir_okay=False, help="File to split, " + MEMORY_KINDS),
    follow_links: bool = typer.Option(True),
) -> None:
    """List extracted claims and their IDs (use the IDs in reviews.jsonl), plus frontmatter metadata."""
    try:
        claims, _ = parse_memory(memory, follow_links=follow_links)
    except (ValidationError, ValueError, OSError) as exc:
        _fail(str(exc))
    for claim in claims:
        typer.echo(_json_shield(json.dumps(claim_record(claim), ensure_ascii=False)))


@app.command("discover")
def discover_command(
    directory: Path = typer.Argument(Path("."), exists=True, file_okay=False,
                                     help="Repository or directory to search (default: the current directory)"),
    exclude: Optional[list[str]] = typer.Option(
        None, "--exclude", "-x", metavar="GLOB",
        help="Leave matching files out (fnmatch on paths relative to DIRECTORY, e.g. 'tests/fixtures/**'); "
             "repeatable"),
    fmt: Format = FORMAT,
) -> None:
    """List every supported agent's instruction files (Claude Code, Codex, Cursor, Copilot, Gemini, Windsurf).

    Reads directory listings only, never file contents."""
    try:
        report = discover(directory, exclude=exclude or ())
    except (ValueError, OSError) as exc:
        _fail(str(exc))
    _emit(discovery_to_markdown(report) if fmt is Format.markdown else report.model_dump_json(indent=2) + "\n",
          fmt is Format.markdown)


@evidence_app.command("from-git", help=FROM_GIT_HELP)
def evidence_from_git_command(
    since: str = typer.Option(
        ..., "--since", metavar="WHEN",
        help="Commits after this point: a date (2026-01-31 means midnight UTC; 2026-01-31T12:00:00+02:00; "
             "'30 days ago'), filtered by committer date, or a commit, tag or branch, meaning the commits after it "
             "up to HEAD. A value that looks like a date is read as a date"),
    output: Path = typer.Option(..., "--output", "-o", file_okay=False,
                                help="Directory for the evidence files; created if it does not exist"),
    repo: Path = typer.Option(Path("."), "--repo", exists=True, file_okay=False,
                              help="Directory inside the repository to read (default: the current directory)"),
    max_commits: int = typer.Option(500, "--max-commits", min=1, help="Write at most this many commits, newest first"),
) -> None:
    try:
        result = evidence_from_git(since, output, repo=repo, max_commits=max_commits)
    except (ValueError, OSError) as exc:
        _fail(str(exc))
    _note(f"note: --since read as {result.since}")
    if result.cut_short:
        _note(f"note: stopped at --max-commits {max_commits}; older commits in the window were left out")
    where = terminal_text(shlex.quote(str(output.absolute())))
    _echo_utf8(f"Wrote {len(result.written)} new file(s) to {terminal_text(str(output))}; "
               f"{len(result.unchanged)} already there.\n"
               "From the directory with your memory files, audit against them with: "
               f"memfalsify audit --evidence {where}\n")


app.add_typer(evidence_app, name="evidence")


def main() -> None:
    app()
