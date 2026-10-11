# Public Python API

English | [中文](api.zh-CN.md)

Import from the package roots: `memcore`, `memfalsify`, `orgmemory`, and `memlab_mcp`.
The distributions for `memcore` and `memlab_mcp` are `memlab-core` and `memlab-mcp`.
Each package has an explicit `__all__` and wheel-included `py.typed`. Python 3.10+ is supported.

## Stability and semantic versioning

The public Python API is exactly the names in each package’s `__all__`, their documented
signatures, public model fields and methods, and documented exceptions. Inherited Pydantic
methods such as `model_dump`, `model_validate`, and `model_dump_json` follow Pydantic’s own
contract. Use package-root imports for new callers. Existing defining-module imports remain
available and refer to the same objects. Each distribution has an independent `__version__`.

This is the proposed 1.0 surface. During 0.x, minor releases may change behavior and patches
fix bugs compatibly. Starting at 1.0, each distribution follows semantic versioning: patches
are compatible fixes, minor releases add compatible APIs, and incompatible changes require
a major release. 1.0 will freeze public names, signatures/defaults, model field names/types,
serialized enum values, documented return types and exceptions, and read-only/create-only
guarantees. Provider answers, model availability, and exact generated report wording are not frozen.

A public name, parameter, or field will be deprecated for at least one full minor version
before removal or incompatible replacement. Deprecated callables emit `DeprecationWarning`
on use; deprecated names and fields warn on access/use where applicable. Release notes and
this reference will identify replacements. For example, an API deprecated in 1.2 remains
available through 1.3; removal requires 2.0. This change introduces no deprecations.

Explicitly internal: names absent from `__all__`, underscore helpers, CLI modules/apps and
option enums, `build_verifier`, `preselection`, default-model constants, report headers and
formatting helpers, tokenization/ranking, Git object-store helpers, GitHub mapping contexts,
locks/cursors/apply internals, and MCP workspace/tool-registration classes. These paths remain
available but have no stability promise. CLI and MCP wire interfaces have separate documentation.
Incidental submodule attributes are not public exports. A legacy import is
`from memfalsify.audit import audit`; the preferred callable import is `from memfalsify import audit`.

## Usage and examples

Run examples from the repository root after installing relevant packages. Use `Path` values
for filesystem parameters and timezone-aware timestamps for Organizational Memory.

### Evidence and confinement

```python
from pathlib import Path
from memcore import confinement_supported, confined_reads, locate_quote, read_file_text

root = Path("examples/memfalsify/evidence").resolve()
span = locate_quote(root, "docs/database.md", "PostgreSQL 16")
print(span.location, span.sha256)
if confinement_supported():
    with confined_reads(root):
        print(read_file_text(root / "docs/database.md"))
```

Offsets count Unicode characters; hashes cover original bytes. Integrity does not authenticate
a source. Confinement needs POSIX support, otherwise raises `ValueError`. Outside the context,
readers retain ordinary filesystem behavior. `make_span` trusts its supplied text/digest;
`locate_quote`, `verify_entry`, and `load_manifest` read and check file bytes.

### Memory auditing

```python
from datetime import date
from pathlib import Path
from memfalsify import LexicalVerifier, audit, parse_memory, to_markdown

memory = Path("examples/memfalsify/MEMORY.md")
claims, files = parse_memory(memory)
report = audit(memory, Path("examples/memfalsify/evidence"),
               reviews=Path("examples/memfalsify/reviews.jsonl"),
               verifier=LexicalVerifier(), as_of=date(2026, 10, 10))
print(to_markdown(report))
print(report.model_dump_json(indent=2))
```

`audit` defaults to lexical verification, `follow_links=True`, `stale_days=180`,
`max_candidates=3`, `min_coverage=0.5`, `max_judged=None`, and no exclusions.
`exclude=("private/**",)` excludes evidence. `audit_claims` takes parsed claims, required
`memory_file`, `files_read`, `memory_paths` keywords, and optional `only` claim IDs.
Only human `Review` records settle support/contradiction; model results remain candidates.
The audit entry points never edit memory. Evidence failures raise `memcore.EvidenceError`;
malformed inputs raise `ValueError` (including Pydantic validation errors); file I/O can raise `OSError`.

Custom verifiers implement `Verifier`: `name: str` and
`judge(claim: str, passage: str) -> Judgment`. Package import does not import `anthropic` or
`typesafe_sdk`. Constructing `LLMVerifier`/`JevVerifier` requires `[llm]`/`[jev]` extras and
credentials or an injected client. `DualVerifier(LLMVerifier(), JevVerifier())` keeps disagreements;
`CascadeVerifier(JevVerifier(), LLMVerifier(), accept_at=0.8)` escalates low confidence.
For model audits, explicitly set `min_coverage=0.2, max_judged=8` to match CLI preselection.
Construction/judgment failures raise `VerifierError`.

### Git history and rollback

```python
from pathlib import Path
from memfalsify import diff_to_markdown, memory_diff, memory_history, rollback_patch

# Requires a Git repository containing this memory file and revision.
memory = Path("MEMORY.md")
changes = memory_diff(memory, base="HEAD~1", evidence_root=Path("evidence"))
print(diff_to_markdown(changes))
history = memory_history(memory, max_commits=50)
# Substitute a claim ID from the memory/history report.
restoration = rollback_patch(memory, claim="m-1a2b3c4d", to="HEAD~1")
# restoration.patch is UTF-8 bytes or None; review before applying externally.
```

`memory_diff` defaults `head=None` to the worktree and audits added/changed claims against
current evidence. It accepts audit’s staleness/coverage/judgment options, without
`max_candidates`/`only`. `memory_history` follows first parents, defaults to 100 commits,
reports shallow/truncated scans, and does not follow renamed memory files.
`rollback_patch` defaults `follow_links=True` and never applies its patch. Catch
`HistoryError` (including `GitError`) for invalid or unavailable Git operations.

### Organizational Memory

```python
from pathlib import Path
from orgmemory import Dataset, audit, audit_markdown, explain, replay, validate

dataset = Dataset.load(Path("examples/orgmemory/feature-a"))
validate(dataset.events, dataset.evidence)
result = replay(dataset)
print(explain(result, "Feature A"))
print(audit_markdown(audit(dataset)))
```

`Dataset(events, policy, evidence)` validates references. `Dataset.load` accepts `directory`,
`events`, `policy`, `evidence` Paths, defaulting to directory `.`. It verifies the full manifest,
including entries beyond an `as_of` cutoff. `replay(dataset, as_of=None)` respects authority;
unauthorized attempts remain in the trail. `audit(dataset, as_of=None, stale_days=14, now=None)`
uses `as_of`, then `now`, then current UTC as its reference time. These operations never write
files. Validation errors are `ValueError` or `memcore.EvidenceError`; I/O can raise `OSError`.

### GitHub ingestion

```python
from pathlib import Path
from orgmemory import run_convert

# Use a capture you recorded yourself and an existing dataset directory.
preview = run_convert(Path("dataset"), Path("capture.json"), dry_run=True)
print(preview.lines())
# Omit dry_run=True for a create-only import.
```

`convert(capture, existing, evidence_ids, config)` is pure: raw capture dict, event-ID mapping,
evidence-ID set, and `GithubConfig` in; `Plan` out. `Capture` is a rendered source file inside
a plan, not the raw capture dict. `run_convert` loads the dataset/config, converts and appends;
its default clock is capture start time. `run_sync` also fetches using a supplied `Client`:

```python
from pathlib import Path
from orgmemory import Client, PersistentTransport, run_sync, token_from_env

# This example performs network requests when executed.
with PersistentTransport() as transport:
    client = Client(transport, token_from_env(), max_wait=60)
    summary = run_sync(Path("dataset"), "owner/repo", client, dry_run=True)
    print(summary.complete, summary.events_appended)
```

`run_sync` defaults `full=False`, `since=None`, `issues=None`, `save_capture=None`,
`dry_run=False`, and current UTC clock `now`. `full`, `since`, and targeted `issues` are mutually
exclusive. `run_convert` defaults `dry_run=False`, `now=None`. Both return `Summary`; check
`complete` because transient stops can produce partial imports. Imports are create-only and
never edit policy. Dry runs skip dataset writes, but an explicit `save_capture` still saves
the fetched capture; omit it for a preview without writes. Permanent fetch failures raise
`GitHubError`; invalid input raises `ValueError`, evidence validation can raise
`memcore.EvidenceError`, and I/O can raise `OSError`. Default transports confine credentials
to the GitHub API origin and refuse redirects; custom `Transport` implementations must
preserve this trust boundary. `Client.get`, `get_json`, and `pages` are the lower-level request
methods; `Stopped` signals a transient stop there.

### MCP server construction

```python
from pathlib import Path
from memlab_mcp import create_server

server = create_server(root=Path("examples"), allow_model_verifiers=False)
# Start serving separately through the installed MCP SDK or CLI.
```

Install `memlab-mcp` for this package. Construction requires an existing root and POSIX
confinement, otherwise raises `ValueError`. It registers read-only tools without starting a
transport. The MCP SDK owns the returned server object's serving API.

## Export reference

Every public name appears below. Model fields follow their typed definitions and are
inspectable with Pydantic `model_fields`; `Change`, `Rollback`, `Capture`, `Plan`, and `Summary`
use dataclass fields. Complete function signatures follow each table. `_now` in the sync
signature means current UTC; it is an internal default implementation, not a public name.


### `memcore`

| API | Purpose |
|---|---|
| `memcore.EvidenceError` | ValueError for missing, altered, or out-of-root evidence. |
| `memcore.EvidenceSpan` | Frozen exact quote: path, SHA-256, character offsets, line, optional source provenance. |
| `memcore.ManifestEntry` | Validated evidence.jsonl record with ID and required source provenance. |
| `memcore.__version__` | Installed distribution version string. |
| `memcore.append_jsonl` | Append one dict as UTF-8/LF JSONL; the parent directory must exist. |
| `memcore.confined_reads` | Context manager pinning root and refusing symlink races during reads; unsupported platforms raise ValueError. |
| `memcore.confinement_root` | Return the active confinement root, or None outside a confined context. |
| `memcore.confinement_supported` | Whether POSIX O_NOFOLLOW and dir_fd support are available. |
| `memcore.line_of` | Return the one-based line at a Unicode character offset. |
| `memcore.load_json` | Parse one JSON value from str or bytes; malformed JSON, a repeated object key, non-UTF-8 bytes and too-deep nesting raise ValueError. |
| `memcore.load_manifest` | Verify all entries and return ID → EvidenceSpan; paths are relative to the manifest directory. |
| `memcore.locate_quote` | Locate the first verbatim quote in a UTF-8 file under root; optional provenance is passed to EvidenceSpan. |
| `memcore.make_span` | Build a span from supplied text, digest, and [start, end) Unicode offsets; does not read or hash a file. |
| `memcore.open_text` | Read-only text context manager; defaults to UTF-8 and honors confined_reads. |
| `memcore.read_file_bytes` | Read bytes from a Path, honoring the active confined_reads context. |
| `memcore.read_file_text` | Read text from a Path; encoding defaults to UTF-8; honors confined_reads. |
| `memcore.read_jsonl` | Iterate (one-based line number, dict); blank lines are ignored and invalid records fail. |
| `memcore.read_text` | Return (SHA-256 of raw bytes, UTF-8 text) for a root-relative evidence path. |
| `memcore.resolve_inside` | Resolve a relative path under root; reject absolute paths and symlink escapes. |
| `memcore.sha256_bytes` | Return the hexadecimal SHA-256 digest of bytes. |
| `memcore.verify_entry` | Check a ManifestEntry against root and return its verified span. |
| `memcore.walk_files` | Yield (directory, mutable directories, files); supports pruning and an OSError callback. Confined directories are Paths, ordinary os.walk directories are strings. |

```python
append_jsonl(path: Path, record: dict) -> None
confined_reads(root: Path) -> Iterator[None]
confinement_root() -> Path | None
confinement_supported() -> bool
line_of(text: str, offset: int) -> int
load_json(data: str | bytes) -> Any
load_manifest(manifest: Path) -> dict[str, EvidenceSpan]
locate_quote(root: Path, relative: str, quote: str, **provenance: Any) -> EvidenceSpan
make_span(relative: str, digest: str, text: str, start: int, end: int, **provenance: Any) -> EvidenceSpan
open_text(path: Path, encoding: str='utf-8') -> AbstractContextManager[TextIO]
read_file_bytes(path: Path) -> bytes
read_file_text(path: Path, encoding: str='utf-8') -> str
read_jsonl(path: Path) -> Iterator[tuple[int, dict]]
read_text(root: Path, relative: str) -> tuple[str, str]
resolve_inside(root: Path, relative: str) -> Path
sha256_bytes(data: bytes) -> str
verify_entry(entry: ManifestEntry, root: Path) -> EvidenceSpan
walk_files(path: Path, onerror: Callable[[OSError], None] | None=None) -> Iterator[tuple[str | Path, list[str], list[str]]]
```

### `memfalsify`

| API | Purpose |
|---|---|
| `memfalsify.ATTENTION` | Frozen set of statuses counted by strict audits: challenged, conflicted, candidate_counterevidence, disputed. |
| `memfalsify.AuditReport` | Read-only audit result with findings, status counts, skipped evidence, and limitations. |
| `memfalsify.CascadeVerifier` | Construct with first, second, accept_at=0.8; escalate when first has insufficient confidence. |
| `memfalsify.Change` | Frozen comparison result: kind, old/new Claim, similarity, and moved flag. |
| `memfalsify.Claim` | Parsed claim with stable ID, text, path, line, section, and duplicate locations. |
| `memfalsify.ClaimChange` | Serialized diff entry with old/new ClaimVersion and optional audit status. |
| `memfalsify.ClaimHistory` | One current claim’s introduction, last change, and optional replaced version. |
| `memfalsify.ClaimVersion` | Claim wording/location at a revision; of(claim) constructs one. |
| `memfalsify.CommitInfo` | Commit identity, timestamp, author, and subject for attribution. |
| `memfalsify.DiffReport` | Revision comparison plus audit of added/changed claims; attention counts strict statuses. |
| `memfalsify.DualVerifier` | Construct with first and second; disagreement yields disputed and keeps both opinions. |
| `memfalsify.EvidenceFinding` | Candidate or human-confirmed relation, rationale, opinions, and quoted span. |
| `memfalsify.Finding` | One claim’s status, evidence, issues, and proposed actions. |
| `memfalsify.GitError` | HistoryError with sanitized summary plus underlying diagnostic message. |
| `memfalsify.HistoryError` | ValueError for invalid or unavailable Git history operations. |
| `memfalsify.HistoryReport` | Current and removed claim provenance, scanned commits, and scan limitations. |
| `memfalsify.Issue` | Wording, time, conflict, or duplicate issue with kind and detail. |
| `memfalsify.JevVerifier` | Construct with model, optional SDK client, threshold=0.5; imports typesafe_sdk only at construction. |
| `memfalsify.Judgment` | Relation, rationale, verifier name, optional confidence, and nested opinions. |
| `memfalsify.LLMVerifier` | Construct with model, optional client, effort="low", fallbacks=None; imports anthropic only at construction. |
| `memfalsify.LexicalVerifier` | Offline verifier; related=0.6 and support=0.75 coverage thresholds. |
| `memfalsify.Proposal` | Suggested action and reason; replacement is always None. |
| `memfalsify.Relation` | Literal type: supports, contradicts, unrelated, disputed. |
| `memfalsify.RemovedClaim` | Removed claim version and the commit that removed it. |
| `memfalsify.Review` | Human confirmation input: memory ID, relation, path, quote, reviewer, optional note. |
| `memfalsify.Revision` | Revision label, resolved commit (None for worktree), and files read. |
| `memfalsify.Rollback` | Patch result: bytes or None plus a message; no patch is applied. |
| `memfalsify.Skipped` | Evidence file path and reason it was skipped. |
| `memfalsify.Status` | Literal type for the seven audit status values. |
| `memfalsify.Verifier` | Protocol: name: str and judge(claim: str, passage: str) → Judgment. |
| `memfalsify.VerifierError` | RuntimeError for unavailable SDKs, credentials, or failed model judgments. |
| `memfalsify.__version__` | Installed distribution version string. |
| `memfalsify.audit` | Parse memory and audit it against optional evidence/reviews; never edits memory. |
| `memfalsify.audit_claims` | Audit parsed claims; only= selects findings while review/conflict checks retain all claims. memory_paths are excluded from evidence. |
| `memfalsify.claim_id` | Return the normalized-text memory ID. |
| `memfalsify.compare` | Compare two Claim lists deterministically by ID, section, and term similarity. |
| `memfalsify.diff_to_markdown` | Render a DiffReport as Markdown. |
| `memfalsify.history_to_markdown` | Render a HistoryReport as Markdown. |
| `memfalsify.load_reviews` | Validate review records and their verbatim quotes for a claim-ID mapping; with skipped=, review evidence excluded by the input path policy is recorded there instead of raising. |
| `memfalsify.memory_diff` | Compare base with head or the worktree and audit only added/changed claims against current evidence. |
| `memfalsify.memory_history` | Attribute claims on the first-parent HEAD chain; max_commits defaults to 100. |
| `memfalsify.parse_memory` | Return (deduplicated claims, files read); follow_links=True follows local Markdown links inside the memory directory. With skipped=, memory or linked files excluded by the input path policy are recorded there instead of read. |
| `memfalsify.parse_text` | Parse supplied text/display path into (claims, link targets), without reading files. |
| `memfalsify.resolve_commit` | Resolve a validated revision to a commit ID in a repository directory. |
| `memfalsify.rollback_patch` | Generate a patch restoring claim wording at to; accepts an old or current claim ID. |
| `memfalsify.to_markdown` | Render an AuditReport as Markdown. |

```python
audit(memory_file: Path, evidence_root: Path | None=None, reviews: Path | None=None, verifier: Verifier | None=None, as_of: date | None=None, stale_days: int=180, follow_links: bool=True, max_candidates: int=3, min_coverage: float=0.5, max_judged: int | None=None, exclude: Iterable[str]=()) -> AuditReport
audit_claims(claims: list[Claim], *, memory_file: str, files_read: list[str], memory_paths: Iterable[Path], evidence_root: Path | None=None, reviews: Path | None=None, verifier: Verifier | None=None, as_of: date | None=None, stale_days: int=180, max_candidates: int=3, min_coverage: float=0.5, max_judged: int | None=None, exclude: Iterable[str]=(), only: Iterable[str] | None=None) -> AuditReport
claim_id(text: str) -> str
compare(base: list[Claim], head: list[Claim]) -> list[Change]
diff_to_markdown(report: DiffReport) -> str
history_to_markdown(report: HistoryReport) -> str
load_reviews(path: Path, evidence_root: Path | None, claims: dict[str, Claim], *, skipped: list[Skipped] | None=None) -> dict[str, list[EvidenceFinding]]
memory_diff(memory_file: Path, base: str, head: str | None=None, *, follow_links: bool=True, evidence_root: Path | None=None, reviews: Path | None=None, verifier: Verifier | None=None, as_of: date | None=None, stale_days: int=180, min_coverage: float=0.5, max_judged: int | None=None, exclude: Iterable[str]=()) -> DiffReport
memory_history(memory_file: Path, max_commits: int=100, follow_links: bool=True) -> HistoryReport
parse_memory(memory_file: Path, follow_links: bool=True, *, skipped: list[Skipped] | None=None) -> tuple[list[Claim], list[Path]]
parse_text(text: str, display: str) -> tuple[list[Claim], list[str]]
resolve_commit(directory: Path, rev: str) -> str
rollback_patch(memory_file: Path, claim: str, to: str, follow_links: bool=True) -> Rollback
to_markdown(report: AuditReport) -> str
```

### `orgmemory`

| API | Purpose |
|---|---|
| `orgmemory.Action` | Literal type for the eight event actions. |
| `orgmemory.AuditFinding` | Decision-process finding with kind, severity, subjects, events, and citations. |
| `orgmemory.AuditReport` | Read-only decision-process report with replay counts, findings, and severity counts. |
| `orgmemory.Capture` | Rendered source-file bytes, relative path, and manifest entries; not the raw capture dict. |
| `orgmemory.CitedEvent` | Event metadata with its exact evidence quotes. |
| `orgmemory.Client` | Construct with transport and token; optional max_wait=3600, sleep, clock, progress. get/get_json/pages perform origin-confined requests. |
| `orgmemory.Conflict` | Recorded simultaneous decision, reversal, or invalid supersession. |
| `orgmemory.Dataset` | Construct from events, Policy, and verified evidence; load(directory=...) reads and validates all three dataset files. |
| `orgmemory.Decision` | Authorized decision, actor roles, timestamp, and quoted evidence. |
| `orgmemory.Event` | Validated event with aware timestamp, actor, action, subject, and evidence IDs. |
| `orgmemory.FetchProgress` | Optional throttled progress reporter with injectable clock and stream; update/rate_limit report progress. |
| `orgmemory.GitHubError` | Permanent GitHub failure with optional HTTP status. |
| `orgmemory.GithubConfig` | github.json label-to-action configuration; key(name) finds a case-insensitive label. |
| `orgmemory.PendingChange` | Ignored unauthorized decision attempt and reason. |
| `orgmemory.PersistentTransport` | Reusable HTTPS transport/context manager; timeout=30, injectable connection_factory/env, close() releases resources. |
| `orgmemory.Plan` | Pure conversion result with events, captures, spans, drift, skips, and counts. |
| `orgmemory.Policy` | Actor roles and action permissions; roles/allows/decision_roles expose policy checks. |
| `orgmemory.ReplayResult` | Subject states and event audit trail, optionally cut off at as_of. |
| `orgmemory.Risk` | Risk event, actor, and note. |
| `orgmemory.Stopped` | Transient fetch stop; high-level sync can return an incomplete Summary. |
| `orgmemory.SubjectState` | Current authorized status, risks, dependencies, pending approvals, decisions, and conflicts. |
| `orgmemory.Summary` | GitHub import capture/plan, append counts, cursor, completeness; lines() returns display text. |
| `orgmemory.TrailEntry` | One replayed event’s outcome and before/after status. |
| `orgmemory.Transport` | Callable taking URL and headers, returning (HTTP status, response headers, bytes). |
| `orgmemory.__version__` | Installed distribution version string. |
| `orgmemory.audit` | Audit a Dataset using replay; as_of/now select the reference time and stale_days defaults to 14. |
| `orgmemory.audit_markdown` | Render a decision-process AuditReport as Markdown. |
| `orgmemory.convert` | Purely map raw capture dict plus existing events/evidence IDs and GithubConfig into a Plan; no I/O. |
| `orgmemory.explain` | Render one subject’s state and decision provenance from a ReplayResult. |
| `orgmemory.load_events` | Parse events JSONL with line-numbered validation errors. |
| `orgmemory.replay` | Deterministically replay a validated Dataset under its policy, with optional aware as_of cutoff. |
| `orgmemory.run_convert` | Apply a saved capture offline, create-only; dry_run previews without writes. |
| `orgmemory.run_sync` | Fetch and import GitHub via a supplied Client; supports full/since/issues, save_capture, and dry_run. |
| `orgmemory.to_markdown` | Render a ReplayResult as Markdown. |
| `orgmemory.token_from_env` | Read the first nonempty GITHUB_TOKEN or GH_TOKEN from env, or from os.environ when env is omitted. |
| `orgmemory.urllib_transport` | Build an origin-confined transport; default opener refuses redirects, timeout=30. |
| `orgmemory.validate` | Check event ID uniqueness, evidence references, and supersession targets across the full log. |

```python
audit(dataset: Dataset, as_of: Optional[datetime]=None, stale_days: int=14, now: Optional[datetime]=None) -> AuditReport
audit_markdown(report: AuditReport) -> str
convert(capture: dict, existing: dict[str, Event], evidence_ids: set[str], config: GithubConfig) -> Plan
explain(result: ReplayResult, subject: str) -> str
load_events(path: Path) -> list[Event]
replay(dataset: Dataset, as_of: datetime | None=None) -> ReplayResult
run_convert(dataset: Path, capture_path: Path, *, dry_run: bool=False, now: Callable[[], datetime] | None=None) -> Summary
run_sync(dataset: Path, repo: str, client: Client, *, full: bool=False, since: datetime | None=None, issues: list[int] | None=None, save_capture: Path | None=None, dry_run: bool=False, now: Callable[[], datetime]=_now) -> Summary
to_markdown(result: ReplayResult) -> str
token_from_env(env: Mapping[str, str] | None=None) -> str | None
urllib_transport(opener: urllib.request.OpenerDirector | None=None, timeout: float=30) -> Transport
validate(events: list[Event], evidence: dict[str, EvidenceSpan]) -> None
```

### `memlab_mcp`

| API | Purpose |
|---|---|
| `memlab_mcp.__version__` | Installed distribution version string. |
| `memlab_mcp.create_server` | Construct the read-only MCPServer with root="." and allow_model_verifiers=False; does not start serving. Requires supported POSIX confinement. |

```python
create_server(root: Path | str='.', allow_model_verifiers: bool=False) -> MCPServer
```
