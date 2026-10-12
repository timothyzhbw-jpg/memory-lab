# Changelog

All notable changes to the four packages in this repository, newest first. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Each package has its own version number. While
they are 0.x, a minor version may change behavior. Pins between the packages admit one minor version of
each dependency, for example `memlab-core>=0.2,<0.3`.

Release 0.4 contains:

| Directory | Distribution | Version | Previous release |
|---|---|---|---|
| `packages/memcore` | `memlab-core` | 0.3.0 | 0.2.0 |
| `packages/memfalsify` | `memfalsify` | 0.4.0 | 0.3.0 |
| `packages/orgmemory` | `orgmemory` | 0.4.0 | 0.3.0 |
| `packages/memlab-mcp` | `memlab-mcp` | 0.4.0 | 0.3.0 |

## Unreleased

Changes after release 0.4 (`v0.4.0`), not yet released. The package versions are unchanged.

### memfalsify

#### Added

- `--verifier openai` on `audit` and `diff` (`memfalsify.OpenAICompatVerifier`, new extra `[openai]`, also in
  `[all]`): one verifier for any OpenAI-compatible Chat Completions API. `--provider` presets the base URL
  and the key's environment variable for `openai` (default), `grok` (xAI), `deepseek`, `gemini` (Google's
  OpenAI-compatible endpoint) and `ollama` (`http://localhost:11434/v1`, no key); `--base-url` and
  `--api-key-env` override them. `--openai-model` is required: model names differ between providers, so
  there is no default. Memory claims and evidence passages are sent to the configured endpoint.
  - The base URL must be `https://`, or `http://` only to `localhost`, `127.0.0.1` or `::1`; a user name,
    password, query or fragment in it is refused, and no message repeats it.
  - The key is read from the named variable, never taken as an argument, and must hold only the characters
    keys use. It is never printed: errors are built from the provider's status, type, code, param and
    message, and any of them that contains the key in any encoding (percent-escaped in either case,
    `\u`-escaped, partly escaped) is withheld whole; no error keeps the SDK exception as its cause or
    context; and the SDK's and the HTTP stack's log records (`OPENAI_LOG=debug`) are sanitized when they are
    made, messages, exceptions and stacks alike, including loggers created later. The OpenAI SDK's
    `OPENAI_BASE_URL`, `OPENAI_ORG_ID`, `OPENAI_PROJECT_ID` and `OPENAI_CUSTOM_HEADERS` are ignored, so
    settings meant for one provider never reach another.
  - Requests go only to the base URL: redirects are not followed (a 3xx stops the audit) and proxy variables
    are not used. Certificates are checked against the system trust store, never `SSL_CERT_FILE` or
    `SSL_CERT_DIR`: through the OS on macOS and Windows, and on Linux against OpenSSL's built-in CA
    locations (or the usual bundle files).
  - Requests ask for the verdict as a strict JSON schema (`response_format` `json_schema`). After HTTP 400
    or 422 with an error about the format (its param, else a format code, else its message, never a model
    that happens to be named that way), the verifier asks for `json_object`, then plain text, keeps
    the format that worked and says so on stderr; any other error stops the audit at once. The first request
    settles the format while concurrent ones wait. Only an answer with `finish_reason` `stop` is read; an
    unfinished one, one that is not a valid verdict (invalid JSON, a repeated or unknown key, a relation
    outside the schema), an empty answer, a malformed response and a refusal are no verdict (`unrelated`), never
    support.
  - API errors exit 2 like the other backends. `--max-concurrency` applies; the report's API usage line has
    requests and tokens, with no cache counters. `--batch` is not supported with it (exit 2), and `dual` and
    `cascade` still pair Claude with Jev.
  - The pre-commit hooks (`memfalsify-pre-commit diff` and `audit`) take the same four options.

### memlab-mcp

#### Added

- `memfalsify_audit` and `memfalsify_diff` accept `verifier: "openai"`, refused unless the server runs with
  `--allow-model-verifiers`. Its endpoint comes only from server flags (`--openai-model`,
  `--openai-provider`, `--openai-base-url`, `--openai-api-key-env`), checked at startup; no tool argument
  names a provider, URL, model or key variable, so a client cannot send text elsewhere. Without
  `--openai-model` the verifier is refused as not configured.

### Repository

#### Added

- GitHub Action: `verifier: openai`, with the inputs `openai-model`, `provider`, `base-url` and
  `api-key-env` (passed through `env:` and checked before the audit), and a hash-pinned lock set
  `action/requirements/openai.txt`. `all.txt` (dual and cascade) still holds only the Claude and Jev SDKs.
- MemFalsify benchmark: `run.py --verifier openai` with the same options, and a documented local Ollama run.
  No model results are recorded.

## Release 0.4

Release 0.4 contains memlab-core 0.3.0, memfalsify 0.4.0, orgmemory 0.4.0 and memlab-mcp
0.4.0. It was tagged `v0.4.0` on 2026-10-11; all changes since release 0.3 are collected here.

Multimodal evidence through transcript and OCR sidecars (roadmap v0.4; see
[docs/multimodal.md](docs/multimodal.md)) and HTML reports use the new memlab-core API,
including `memcore.htmlpage`. The dependent packages' pins move to memlab-core 0.3.

### Security

- MemFalsify audit, diff and history Markdown, orgmemory replay/explain (including YAML state),
  audit, changes and ADR exports, CLI diagnostics and Action summaries use shared memcore display
  helpers: untrusted fields occupy one line with control, format and separator characters visibly
  escaped, then literal Markdown or safely quoted YAML. Paths and review metadata receive the same
  treatment. Action logs and annotations retain workflow-command escaping and also escape terminal
  controls, preventing forged report headings, YAML fence breaks and runner commands.

- `memfalsify rollback` without `--output` shows the patch with control, bidi and other invisible
  characters escaped (`\uXXXX`) when stdout is a terminal, and says so on stderr. A pipe, a redirect and
  `--output` still get the exact bytes, so `git apply` works. No untrusted value starts an output line
  any more, so none can read as a workflow command: the self-approval finding's detail now begins
  `approved by <actor>, who had also requested the subject`, `orgmemory export --format csv` quotes a
  subject that starts with `::` (the cell decodes to the same text), and HTML quotes write each line
  break as `&#10;` (the same layout). `orgmemory validate` prints the actors without a role with
  `one_line`, so a Windows path keeps its single backslashes.
- GitHub fetch rejects percent-encoded credential reflection in JSON strings and response headers,
  including header names. Pagination stays on the original listing endpoint and preserves its query
  filters, allowing only pagination changes: at most one positive `page` and one `after` cursor
  (decoded `[A-Za-z0-9+/=_-]{1,512}`), with at least one present. This accepts GitHub's cursor-bearing
  next links on both named and repository-ID paths; changed filters and invalid or duplicated cursors
  are refused. Both HTTP transports cap response bodies at 8 MiB, stop reading on excess and fail the
  sync without appending records or saving a capture.
- MemFalsify applies one shared path policy to memory, linked memory and evidence inputs (including
  review evidence), and MCP path resolution: both the walked and resolved paths must stay inside their
  root and contain no hidden component or skipped directory. Rejected inputs carry a skip reason;
  descriptor-confined reads prevent symlink swaps on supported platforms.
- The GitHub Action warns and skips memory paths resolving outside the workspace or into hidden/skipped
  components, preventing checkout credentials from being quoted in job summaries. Workflow examples
  recommend `persist-credentials: false`, and the CI dogfood checkout sets it.
- JSONL append refuses symlinks and non-regular files and uses one descriptor for separator checks and
  writes. The GitHub sync lock retains its descriptor through the write; POSIX directory locking keeps
  writers excluded even if the marker is deleted, and cleanup tolerates a missing marker.
- A JSON object that repeats a key is rejected, including inside a nested object. `json.loads` kept the
  last value, so a forged `events.jsonl` line could show one `actor` to a person reading the file and
  another to replay, and a `policy.json` that listed an actor twice granted the second set of roles. The
  same check covers evidence manifests, `reviews.jsonl`, `github.json`, `github-sync.jsonl`, GitHub
  capture files, GitHub API responses, and benchmark `labels.jsonl` and `case.json`. The error is
  `<file>:<line>: invalid JSON: duplicate key` and the commands exit 2.
- The GitHub Action no longer resolves anything floating. `actions/setup-python` in `action.yml`, and
  `actions/checkout` and `actions/setup-python` in the CI workflow, are pinned to full commit SHAs with a
  `# vX.Y.Z` comment, and the documentation examples are pinned too. The install step runs
  `pip install --require-hashes --only-binary :all:` against a lock file vendored in `action/requirements/`
  (`lexical`, `llm`, `jev`, `all`, chosen by `verifier`; universal for Python 3.10 and newer on Linux, macOS and
  Windows), then installs `memlab-core` and `memfalsify` from the action path with `--no-deps
  --no-build-isolation` and runs `pip check`, so a retargeted tag or a compromised PyPI release can no longer
  run in the job. `scripts/update-action-lock.sh` regenerates the lock files with `uv pip compile
  --generate-hashes`. `docs/github-action.md` explains why the dogfood job (`uses: ./`) is safe and which
  changes would make it unsafe; tests fail the build if CI gains a secret, a write permission or
  `pull_request_target`.
- Documented that evidence reads follow hard links: a hard link is indistinguishable from its original, so a
  file hard-linked into the evidence directory on the same filesystem is read and can be quoted. Git does not
  store hard links, so only someone with write access to the audited directory can plant one. See
  `docs/github-action.md` and the trust boundaries in both READMEs.
- MCP resource and prompt errors carry the tools' messages or a fixed one. They do not echo the requested URI
  or arguments, and they are not logged, so neither a client nor stderr sees a path outside the root or a
  traceback. The SDK's default resource security (no `..` escape, absolute path or NUL) runs on every
  resource path before the server's own path checks.

### Added

- Explicit package-root Python APIs for `memcore`, `memfalsify`, `orgmemory`, and `memlab_mcp`,
  including evidence reading, memory audits and Git history, organizational replay and GitHub ingestion,
  and MCP server construction. Existing module import paths remain available.
- Public signature annotations and wheel-included `py.typed` markers in all four packages.
- English and Chinese API references, a 1.0 stability and deprecation policy, and pinned export tests.
- `memcore.load_json` parses one JSON value and raises `ValueError` for malformed JSON, bytes that are not UTF-8, or JSON nested too deeply.
- Claude Code plugin marketplace. `.claude-plugin/marketplace.json` lists the `memory-lab` plugin, whose root is
  `skills/`. `claude plugin marketplace add timothyzhbw-jpg/memory-lab` and
  `claude plugin install memory-lab@memory-lab` install `skills/memfalsify`, `skills/orgmemory` and the read-only
  `memlab` MCP server, started as `memlab-mcp --root ${CLAUDE_PROJECT_DIR}` without `--allow-model-verifiers`.
  The plugin pins no version, so Claude Code versions it by commit. It does not install the Python packages.
- `docs/install-agents.md` and `docs/install-agents.zh-CN.md`: installing into Claude Code with the plugin and into
  Codex with `~/.agents/skills` and `codex mcp add`, including the `config.toml` table that command writes.
- `tests/test_packaging_agents.py`: the manifests' fields, names and paths follow the Claude Code marketplace and
  manifest references, the plugin ships exactly the repository's skills, and the server's arguments parse with
  `memlab-mcp`'s own command line with model verifiers off. An unexpanded `${CLAUDE_PROJECT_DIR}` fails to start.
- `memfalsify diff --staged` compares `--base` with the git index (the text `git commit` will record),
  including one level of linked `.md` files read from the index. Evidence stays the working tree.
  `--staged` does not take `--head`. In a repository with no commits, `--base HEAD` treats every staged
  claim as added.
- `.pre-commit-hooks.yaml` provides `memfalsify-diff` (added and changed claims) and `memfalsify-audit`
  (every staged claim) for [pre-commit](https://pre-commit.com). Both read staged `CLAUDE.md`, `AGENTS.md`
  and `MEMORY.md` from the index, audit them against the working tree, and fail the commit only when a
  checked claim is challenged, conflicted, disputed or has possible counterevidence. The hook install is
  the repository-root project `memory-lab-pre-commit`, which bundles the memfalsify and memcore sources
  from that commit.
- MemFalsify benchmark cases `en-units-scope`, `zh-units-negation` and `es-operacion-alcance`: 63 labeled
  claims for paraphrased support and contradiction, unit conflicts, date and version arithmetic, scope swaps
  between services, and negation and modality. The lexical baseline was measured again on all 202 claims.
- English and Chinese 15-minute tutorials (`docs/tutorial.md`, `docs/tutorial.zh-CN.md`), linked from both READMEs. The walkthrough installs the CLIs, audits `examples/memfalsify`, records a review, diffs a `CLAUDE.md` and `AGENTS.md` change in a throwaway git repository, checks `history` and `rollback`, replays and audits `examples/orgmemory/feature-a`, converts the GitHub example captures, and registers the read-only MCP server. `tests/test_tutorial.py` runs the English page's bash blocks in order and checks the exit code each block states.

### Changed

- MCP, Action, benchmark, and README library imports use the public package roots where possible,
  with no behavior changes. CLI configuration and report-header helpers remain internal.
- memfalsify `audit` and `diff` find internal conflicts from an inverted index of normalized terms and reuse each claim's lexical features. Reports stay byte-identical; large memories are much faster. Timings are in `benchmarks/perf/`.
- README: the Codex skills location is `~/.agents/skills/`, the user location in OpenAI's documentation, instead
  of `~/.codex/skills/`. The memfalsify and orgmemory skills say that the plugin roots the server at the project
  directory.
- `orgmemory validate`, `replay`, `explain` and `audit` do less repeated work on large logs and return the
  same reports. `load_manifest` reads and hashes each distinct evidence path once per load and checks later
  entries against those bytes. Replay resolves each actor's permissions once, creates each subject state
  once, and sorts that subject's dependencies once at the end. Audit indexes applied dependencies and
  requests during its trail walk. The Markdown renderer keeps the previous escaping for any line that needs
  it. Measurements are in `benchmarks/perf/README.md`.

### Fixed

- Memory files, reviews, evidence manifests, organizational-memory events and policy, GitHub captures, and git history output reject malformed or hostile input with `ValueError`, `EvidenceError`, or `HistoryError`. The CLIs exit 2 and print one line, instead of a traceback or a hang.
- A leading BOM in a memory file is ignored, and CR, CRLF, and LF are line breaks.
- `memfalsify` stdout and organizational-memory replay and explain do not emit terminal control or bidi characters. `--output`, and MemFalsify's audit Markdown, keep the original quotes.

### memfalsify

#### Added

- The evidence scan indexes each cue or OCR line of a sidecar as a passage. Markdown reports cite the media
  file and position (`clip.mp4#t=12.5,17 (00:00:12.500-00:00:17.000)`), then the sidecar line and its
  hash, then the media file's hash or that it was not present. A multi-line cue is quoted line by line
  (in `diff` reports too, before control characters are neutralized). JSON spans carry `media`. When a
  report uses sidecar evidence, its limitations say that a transcript or OCR is someone's reading of the
  media.
- Reviews may cite a sidecar. The quote must lie inside one cue or line; an optional `locator` picks which.
- Example: `examples/memfalsify-media`.

- `--format html` on `audit` and `diff`: one self-contained page with the same content as the Markdown
  report (summary table, findings grouped by status, every evidence quote with location, character range
  and full SHA-256, issues and proposed actions; for `diff`, the changed, added and removed claims).
  Works with `--output`. `history` stays `markdown` or `json`. The examples have `report.html` next to
  `report.md`.

#### Changed

- Lexical judging of Chinese text, with no new dependency. Text without CJK characters is read exactly as
  before: the English golden reports and every English benchmark prediction are unchanged.
  - Full-width digits, letters and punctuation are read as ASCII (`１２０`, `ＡＰＩ`, `，`).
  - Chinese numerals before a measure word (`三个月`, `两名`), `万`/`亿`/`千` amounts (`1200万`,
    `一千二百万`), `百分之` and `成` percentages, `半` halves (`半小时`, `一年半`) and dates
    (`2026年10月11日`) are compared as numbers, so `三秒` against `2秒` is a different value. `第四季度`
    reads as `Q4`, as in English.
  - CJK runs yield character trigrams as well as bigrams. A Chinese claim's term coverage counts
    characters: a character is found when a bigram or trigram containing it occurs in the passage, so word
    boundaries need not line up. A Latin-script term in a Chinese claim weighs two characters.
  - Negation cues (`不`, `没有`, `未`, `无`, `非`, `别`, `勿`, `不再`, `禁止`, `不得`) count only in a
    clause that shares a term with the other side. Words such as `未来`, `非常`, `无论`, `不仅` and `级别`,
    and double negatives (`非…不可`, `不得不`), are not negations.
  - New rationale `opposite modality`: all (`所有`, `任何`, `一律`) against only (`仅`, `只有`), or must
    (`必须`, `非…不可`) against need not (`无需`, `不必`).
  - A bare quantity such as `三个月。` is no longer a claim, as with `3 months.`; `memfalsify diff` pairs
    edited Chinese claims by bigram and trigram similarity.
  - On the original eight-case benchmark run, Chinese contradiction recall rises from 2/19 to 5/19,
    precision from 0.25 to 0.56, and Chinese false flags fall from 6/32 to 4/32. See
    `benchmarks/memfalsify/RESULTS.md`.

- A media file is listed as skipped only when no sidecar was indexed for it, with the reason "no usable
  transcript (.vtt, .srt) or OCR sidecar (.ocr.json) next to it". Malformed, stale, unbound and ambiguous
  sidecars are listed as skipped with the reason. Markdown keeps each skipped path and reason on one line,
  with terminal and bidi controls replaced, so a file name cannot break the list. JSON still stores the
  path exactly.
- `.vtt`, `.srt` and `.ocr.json` files are parsed as sidecars instead of being indexed as plain text, so a
  malformed one is now skipped rather than read line by line.
- JSON reports have a `media` field (null for text files) on every evidence span.
- Instruction files of other agents. `claims`, `audit`, `diff`, `history` and `rollback` take them like
  `MEMORY.md`. Each format was checked against the agent's documentation (sources in
  `packages/memfalsify/src/memfalsify/formats.py`):
  - Claude Code: `CLAUDE.md`, `CLAUDE.local.md`, `.claude/CLAUDE.md`, `.claude/rules/**/*.md` (`paths`).
  - Codex and other `AGENTS.md` readers: `AGENTS.md`, `AGENTS.override.md`, and `.claude/AGENTS.md`
    (Claude Code loads that file too).
  - Cursor: `.cursor/rules/**/*.mdc` (`description`, `globs`, `alwaysApply`) and the legacy `.cursorrules`.
  - GitHub Copilot: `.github/copilot-instructions.md` and `.github/instructions/**/*.instructions.md`
    (`applyTo`, `excludeAgent`).
  - Gemini CLI: `GEMINI.md`.
  - Windsurf (now Devin Desktop): `.windsurf/rules/*.md` and `.devin/rules/*.md` (`trigger`, `globs`,
    `description`), and the legacy `.windsurfrules`.
- The frontmatter fields named above become claim metadata: `metadata` in `claims` output (only when
  there is some), in audit findings, in `diff` claim versions and in `history` claims, and a
  "Frontmatter:" line in Markdown reports. A `diff` report lists unchanged claims whose frontmatter
  changed under "Frontmatter changed". Frontmatter is read as a small YAML subset, as written; values are
  capped at 300 characters and 50 list items.
- `memfalsify discover [DIR]` lists the instruction files of all these agents in a repository, as a
  Markdown table or JSON, with `--exclude` globs. It reads names only, fails on unreadable directories,
  lists a file reachable under two names once (`same_as`; both names when the filesystem reports no file
  index), and skips symlinks that leave the directory,
  names with control characters, symlinked directories and nested git repositories.
- `@file.md` imports (Claude Code, Gemini CLI) are followed like local Markdown links: one level deep,
  inside the memory file's directory, and not inside code spans. `--no-follow-links` turns both off.

- A line holding an `@file.md` import is an index entry, like a line with a local Markdown link: it was
  read as a claim before.
- `history` counts a change to a file's frontmatter metadata as the latest change of each of its claims.
- Frontmatter after a UTF-8 byte order mark is recognized and skipped; its lines were read as claims.
- `--max-concurrency N` on `audit` and `diff` (default 4) runs up to N model-verifier requests at once.
  Judgments stay in request order, so findings match a serial run of the same deterministic verifier.
  Cache-token totals are whatever that run's API responses reported. Not used with `--batch`. Lexical
  judging stays serial. `audit()`, `audit_claims()` and `memory_diff()` take `max_concurrency` (default 1).
- `--batch` on `audit` and `diff`, for `--verifier llm`, `dual` or `cascade`, sends the Claude requests
  through the [Message Batches API](https://platform.claude.com/docs/en/build-with-claude/batch-processing).
  Batch usage is billed at 50% of standard prices, but a batch can take up to 24 hours (most end within
  an hour). The command polls with backoff (5 s, doubling to once a minute), maps results back by
  `custom_id`, and splits batches below the API's limits. An errored, expired or canceled request, a
  missing result or an unreadable verdict exits 2 with no report. An interrupt cancels the running batch.
  The Batches API rejects the server-side refusal fallback, so a refused request is sent again directly
  with the fallback, on models that have one. `LLMVerifier(batch=True)` does the same from Python.
- Prompt caching for the Claude verifier. The instructions are a cached prefix, and so is the claim when
  it is judged against more than one passage; the passage follows the last breakpoint. Requests that share
  a claim's prefix start after the first one returns, so they can read its cache entry. The 5-minute TTL
  is used, or 1 hour in a batch. A prefix shorter than the model's minimum cacheable length is sent
  uncached, which is the usual case for short claims.
- `verifier_stats` in audit reports, and an "API usage" line per model backend in Markdown: requests,
  requests sent in a batch, input and output tokens, and for Claude the tokens written to and read from
  the prompt cache. Counts cover that audit only.
- Library: `verify.judge_all`, `verify.concurrently`, `VerifierStats` and `UsageMeter`. A verifier may
  define `judge_many(pairs, max_concurrency)` to schedule a whole audit's pairs, and `usage()` to report
  its API usage.

- The audit shortlists passages for every claim first, then sends all (claim, passage) pairs to the
  verifier in one `judge_all` call. `dual` asks each backend in one pass. `cascade` asks Jev about every
  pair and then Claude about the pairs Jev was unsure of. Verdicts are unchanged.
- Claude requests send the instructions as a system text block with `cache_control`. The claim and the
  passage are two text blocks of the user turn instead of one string. The wording, schema and effort are
  unchanged.
- `audit.candidates()` now ranks judged passages; selecting them moved to `audit.shortlist()`.

- An HTML report (a `<!DOCTYPE html>` page whose head names `memfalsify` as its generator) is skipped as
  evidence, like the Markdown and JSON reports.

#### Fixed

- Model rationales, batch status lines and batch errors are one line with terminal and bidi controls
  removed. The Markdown API-usage line keeps the verifier name inside a single code span.

### memlab-core

#### Added

- `memcore.sidecar`: parsers for WebVTT (`.vtt`) and SubRip (`.srt`) transcripts and for OCR JSON
  (`.ocr.json`, format `memlab-ocr/1`: pages, lines, text and pixel bounding boxes). Each cue or OCR line
  is an exact `[start, end)` range of the sidecar's own text with a locator in the media file:
  `t=START,END` in seconds, or `page=N&xywh=X,Y,W,H`. Malformed input raises `SidecarError`, never
  anything else. An OCR line written with JSON escape sequences is left out, since its quote could not be
  exact. hOCR is not supported.
- `load_sidecar` binds a sidecar to the media file next to it (`clip.mp4.vtt`, `clip.vtt`, `clip.en.vtt`,
  `scan.pdf.ocr.json`). The media file name must match a directory entry exactly, including case, so
  macOS, Windows and Linux bind the same files. When the media file is present, its SHA-256 must equal
  the `source_sha256` the sidecar records (a `NOTE source_sha256: <hex>` block in a transcript); otherwise
  the sidecar is stale or unbound.
- `MediaRef` and `EvidenceSpan.media` (kind, media path, locator, media SHA-256), and
  `EvidenceSpan.citation` (`clip.mp4#t=12.5,17` for a sidecar quote, else `path:line`).
- `ManifestEntry.locator` (optional). For a transcript or OCR sidecar, `verify_entry` requires a locator
  and rechecks it and the sidecar's binding to its media file on every load; a sidecar record that omits
  `locator` fails validation. Entries that are not sidecars and have no locator serialize exactly as before.
- `memcore.reading.file_sha256` (hashes in chunks, confined like every other read), `list_files` and
  `entry_names` (exact directory-entry names, including directories, so a case-insensitive filesystem
  cannot open `clip.mp4` for `Clip.mp4`).

#### Changed

- `locate_quote(root, path, quote, locator=None)`: in a sidecar, the quote must lie inside one cue or OCR
  line (the one at `locator`, if given), and the span carries its media position.

- `memcore.htmlpage`: building blocks for self-contained HTML report pages. A page has inline CSS, no
  JavaScript and loads nothing, behind a `Content-Security-Policy` meta tag (`default-src 'none';
  style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'`) and `referrer` `no-referrer`. `text()`
  HTML-escapes every value and shows control, format (bidi, zero-width, tag), separator, surrogate and
  other default-ignorable characters (variation selectors, Hangul fillers) as visible `U+XXXX` markers.
  Only `http` and `https` URIs become links (`link()`, `safe_url()`), with `rel="noopener noreferrer"`.
  The page is 7-bit ASCII and is written to stdout as bytes, so a Windows text-mode console cannot turn
  LF into CRLF. It follows `prefers-color-scheme`, and its tables scroll sideways at phone width.

### orgmemory

#### Added

- `orgmemory evidence add` on a transcript or OCR sidecar records the cue's or line's `locator`;
  `--locator` picks it when the quote occurs more than once. The sidecar must match its media file.
- `replay`, `explain` and `audit` cite sidecar evidence by media file and position, and print each line
  of a multi-line cue as quoted text. `explain` and `replay` replace terminal and bidi controls in a quote
  after splitting lines, so a cue stays multi-line and cannot retarget the terminal. The audit report still
  escapes those characters onto one line. `evidence add` prints its confirmation on one line.

- `--format html` on `replay` and `audit`. `replay`: a summary row per subject, then each subject's
  state, authorizing decision with its quoted evidence, ignored attempts, conflicts and decision trail.
  `audit`: the kind-by-severity table, then findings grouped by severity with every cited event and
  quote. The examples have `replay-report.html` and `audit-report.html` next to the Markdown reports.

- Effective-dated roles. An optional, append-only `policy-log.jsonl` grants roles (`grant`) and removes
  them (`revoke_role`) from an effective time on, with evidence. Replay judges each event by the roles its
  actor held at its effective time, so a later role change no longer rewrites past outcomes. `policy.json`
  is the baseline, from the beginning of time or from its new optional `as_of`; before `as_of` no actor
  holds a role. Changes apply in effective-time order (ties in file order), and a change at time T applies
  to events at T. A change must be made by an actor whose roles allow the new `manage_roles` permission at
  that time; the whole log is validated on every load, and a change that does not hold rejects the dataset.
  See [docs/design/dated-policy.md](docs/design/dated-policy.md).
- `orgmemory policy grant` and `orgmemory policy revoke` validate a role change against the whole log, then
  append it.
- `explain` lists the role changes of the actors in a subject's history and notes when the current
  decision's maker's roles changed later. Replay and audit reports name the role source, and the audit lists
  every role change.
- Audit finding `role_changed_after_decision` (low, informational): the current decision's maker later lost
  a role that authorized it. The decision stands.
- Example `examples/orgmemory/feature-a-dated`.

#### Changed

- `validate` prints a `roles:` line for a dated dataset, notes events before the baseline, and no longer
  lists actors the log grants a role as actors without any role. `append` refuses an event ID that a
  policy-log entry uses.
- `unauthorized_attempts` names the roles held at each attempt when they differ.
- Policy baseline and role-change timestamps require ISO 8601 datetimes with timezones; numeric epochs
  and numeric strings are rejected rather than guessing seconds or milliseconds.
- GitHub capture destinations reserve `policy-log.jsonl` even when absent, and importer inputs refuse
  aliases of either policy file before opening them.
- Datasets without `policy-log.jsonl` and `as_of` are unaffected: their reports and JSON are byte-identical,
  and they do not list the new finding kind.

- `orgmemory jira convert DATASET CAPTURE [--dry-run]` imports a saved Jira Cloud search offline. A capture
  is every page of `GET /rest/api/3/search?expand=changelog` with a fixed field list, wrapped with the site
  and capture time; the README has the curl and jq recipe. Each issue is a subject (its key). Creation is a
  `request` by the creator, and comments are `comment`. A status transition is `approve`, `reject`,
  `revoke` or `reconsider` only when `jira.json` maps its target status id; any other transition is a
  `comment`. Actors are `jira:<accountId>`. App accounts are comment-only, and acts without an author are
  skipped and reported. Pages merge in any order, and identical duplicates are ignored. A capture that
  misses pages, or an issue whose changelog or comments are missing or truncated, is incomplete (exit 3),
  and that issue is not imported. Re-imports are idempotent and report drift, gone comments and moved
  issues. A dataset imports one site, and the importer never reads `policy.json`. See
  [docs/design/jira-import.md](docs/design/jira-import.md) and `examples/orgmemory/jira-example`.

- The GitHub and Jira importers share their capture machinery (`orgmemory.ingest`). Neither starts while
  the other's lock (`.github-sync.lock`, `.jira-sync.lock`) exists. GitHub output is unchanged.

- `orgmemory changes <dataset> --from T1 --to T2 [--format markdown|json]` reports what changed between
  two times. It replays the log at T1 and at T2 and lists, per subject, the authorized decisions in the
  period (status before and after, actor, roles, quoted evidence), pending approvals and reconsiderations
  opened or resolved, new risks, dependencies and conflicts, and attempts the policy ignored, under a
  summary table. The period holds the events after T1, up to and including T2. Like the audit, it is
  built only on `replay()` and its audit trail, is deterministic and escapes untrusted text. Golden reports:
  `examples/orgmemory/changes-report.md` and `examples/orgmemory/github-acme-web/changes-report.md`.
- `orgmemory.audit.periods()`: every pending-approval and reconsideration period of a subject's trail.
  `open_periods()` is now built on it and returns the same events as before.

- `orgmemory export DATASET --out DIR [--as-of T] [--force]` writes one Architecture Decision Record per
  subject whose replay has a current authorized decision. The layout is Michael Nygard's, as adr-tools
  writes it (title, `Date:`, Status, Context, Decision, Consequences), with MADR's status words
  (`approved` is Accepted, `rejected` Rejected, revoked back to `proposed` Proposed) and an Evidence section
  with every cited quote, its file, line, character range, full SHA-256 and source URI. Context lists the
  requests and risks on record; Consequences lists who may change the status, dependencies, ignored change
  attempts and conflicts. File names are `<slug>-<hash12>.md`, from the subject alone, and records contain
  nothing from the run, so an unchanged log exports to the same bytes.
- `orgmemory export --format csv [--out FILE]`: the same decisions as one table, to stdout or a new file.
  List columns are JSON arrays that decode to the exact values; other cells are one line, and a cell that
  starts like a spreadsheet formula gets a leading `'`.
- Export is create-only: if any target exists, nothing is written, unless `--force`, which replaces regular
  files by rename and never writes through a symlink or hard link. A target inside the dataset directory is
  refused, also through a symlink, `..` or different letter case. Untrusted text is escaped as in
  `orgmemory audit`. `examples/orgmemory/adr/` holds the output for both example datasets.

#### Fixed

- `orgmemory audit` Markdown: untrusted text starting with four spaces, such as an indented evidence quote,
  became an indented code block that showed the escaping instead of the text. Leading spaces are now written
  as character references.
- `orgmemory export --force` no longer deletes a pre-existing temporary name when that name cannot be
  created.

### memlab-mcp

Through the libraries, `memfalsify_audit`, `memfalsify_diff` and the orgmemory tools read
sidecars and hash media files with the same confined reads as other evidence, so a media file swapped for
a symlink out of the root is never read.

#### Added

- `orgmemory_changes` (`dataset`, `from_time`, `to_time`, `format`): the read-only tool over
  `orgmemory changes`, with the same root confinement as the other dataset tools. Requires the orgmemory
  release that adds `changes`.
- Read-only resource templates: `memlab://memory/{+memory_file}/claims` and
  `.../audit{?evidence_dir,reviews_file,as_of}`, and `memlab://dataset/{+dataset}/state{?as_of}`,
  `.../audit{?as_of,stale_days}` and `.../explain/{subject}{?as_of}`. Each one runs the code of the matching
  tool, so it returns the same text, under the same confinement. A resource error is the tool's message,
  or a fixed one when the SDK refuses the URI. The memory audit resource always uses the lexical verifier,
  even with `--allow-model-verifiers`.
- Prompts `audit_memory`, `review_memory_changes` and `explain_status` for the skills' report-first
  workflows. They quote their arguments as one line of JSON with invisible characters escaped the same way
  on Python 3.10 through 3.13, check paths as the tools do, and accept a revision only as a non-empty
  string that does not start with `-` or contain a control character. The tool resolves that revision to a
  commit. Prompts embed no file contents and never tell the agent to write a file.

#### Changed

- `memfalsify_claims` returns the same objects as `memfalsify claims`, including `metadata`, and the
  other memfalsify tools accept the new instruction formats.
- No change. The tools' `format` stays `markdown` or `json`: an MCP client reads text, and HTML is for
  people, through the CLIs.
- The orgmemory tools read an optional `policy-log.jsonl`, refusing one that is a symlink leaving the server
  root. `orgmemory_validate` prints the same lines as `orgmemory validate`.

### Repository

#### Added

- GitHub Action: `memory: auto` audits every instruction file `memfalsify discover` finds in the
  workspace (or, with `diff-base`, diffs each one). The `exclude` globs also leave instruction files out
  of discovery.
- The `memfalsify` skill covers the new formats, `memfalsify discover` and `memory: auto`.
- Test fixtures for every format in `tests/fixtures/formats/`.
- An Organizational Memory benchmark (`benchmarks/orgmemory`): ten labeled datasets (233 labels, 598
  checks) that attack the authority rules, from unknown and look-alike actors, same-instant decisions,
  revoke chains and as-of cutoffs to forged evidence, malformed logs and a GitHub import. `run.py`
  replays them offline through `Dataset.load`, `replay` and `audit` and reports every check (`--check`,
  `--case`, `--json`); it exits 1 on any mismatch. `tests/test_orgbench.py` runs it in CI against the
  known mismatches.
- [Measured results](benchmarks/orgmemory/RESULTS.md): the first run passed 588 of 598 checks, with
  two findings: duplicate JSON keys in `events.jsonl`, `evidence.jsonl` or `policy.json` were accepted
  with the last value winning, and current roles in `policy.json` applied to every past event. Duplicate
  keys now fail closed, so 590 of 598 checks pass; all 8 remaining mismatches are in `role-change`.
  Effective-dated roles fix historical authority when recorded in `policy-log.jsonl`; this case has
  no dated policy log and keeps its independent labels.

## memlab-core

### 0.3.0 - 2026-10-11

Changes are collected in the [release 0.4 section](#release-04).

### 0.2.0 - 2026-10-11

#### Added

- `memcore.reading`: `read_file_bytes`, `read_file_text`, `open_text` and `walk_files`. All packages read
  memory, evidence and datasets through them. Outside `confined_reads` they behave like plain `open` and
  `os.walk`.
- `confined_reads(root)`. Inside it, every open walks from a pinned root descriptor with `O_NOFOLLOW` on
  each path component, so a file or directory swapped for a symlink after a check cannot be read. Only
  regular files are read, opened non-blocking, so a FIFO cannot hang a read. Directory walks never descend
  into directory symlinks and report errors instead of dropping entries. This needs POSIX `O_NOFOLLOW` and
  `dir_fd`; `confinement_supported()` reports whether the platform has them. The MCP server uses it.
- `confinement_root()`: the root of the active `confined_reads` context, for readers that check their own
  locations, such as memfalsify's git access.

#### Changed

- `append_jsonl` writes UTF-8 bytes with LF line endings in binary mode. On Windows, text mode had added
  CR, so appended records were not byte-identical across platforms.
- `read_text` raises `EvidenceError` for an evidence path that exists but is not a regular file.

### 0.1.0 - 2026-10-10

- First release. Evidence spans with SHA-256 and exact quote offsets. `resolve_inside` keeps paths and
  symlinks inside a root. Evidence manifests and append-only JSONL storage.

## memfalsify

### 0.4.0 - 2026-10-11

Changes are collected in the [release 0.4 section](#release-04).

### 0.3.0 - 2026-10-11

This release jumps from 0.1.0 to 0.3.0 so the version matches the MemFalsify roadmap. Both v0.2 (model
verification) and v0.3 (memory kept in git) ship in it. There is no memfalsify 0.2.0.

#### Added

- `memfalsify diff MEMORY --base REV [--head REV]` compares the memory file and the `.md` files it links
  to at two revisions. `--head` defaults to the working tree. Claims are classified as added, changed (old
  → new, paired by file, section and term similarity), removed or unchanged. Only added and changed
  claims are audited, against the evidence as it is now. It takes the audit options. With `--strict` it
  exits 1 when an added or changed claim needs attention.
- `memfalsify history MEMORY [--max-commits N]` shows, for each current claim, the commits that
  introduced it and last changed it on the first-parent line of HEAD, plus uncommitted changes. It also
  lists removed claims and when they were removed, and it reports shallow clones and scans cut short by
  `--max-commits`.
- `memfalsify rollback MEMORY --claim ID --to REV [--output FILE]` prints a patch for `git apply`. The
  patch restores a claim's wording as of a revision, or re-adds a removed claim under its old section. It
  never edits memory.
- Git reads for diff, history and rollback are hardened:
  - A revision that starts with `-` or contains control characters is refused. Other revisions are
    resolved to a commit with `--end-of-options`.
  - Contents come from one `git cat-file --batch` process, and trees are walked entry by entry, so no
    symlink or submodule in history is followed.
  - Commit subjects and author names are printed on one line, without terminal or bidi control
    characters.
  - Inside `memcore.reading.confined_reads`, git may read only a repository whose work tree, git
    directory, common directory and object store all resolve inside the root, with no alternates and no
    symlinks in the object store. Later git calls are pinned to those locations, and `GIT_DIR`-style
    variables are ignored.
- Model verifiers:
  - `--verifier llm` uses Claude through `anthropic` (extra `[llm]`).
  - `--verifier jev` uses TypeSafe AI's Jev through `typesafe-sdk` (extra `[jev]`).
  - `--verifier dual` runs both. When they disagree the result is `disputed`, and the report shows each
    opinion.
  - `--verifier cascade` asks Jev first and passes unsure answers to Claude (`--accept-at`, default
    0.8).
  - Related options are `--llm-model`, `--jev-model`, `--max-judged` (default 8 passages per memory) and
    `--min-coverage` (default 0.2 for models, 0.5 for lexical). The extra `[all]` installs both SDKs.
- `--exclude GLOB` on `audit` and `diff` (repeatable) leaves evidence files out. Patterns are fnmatch
  globs on paths relative to `--evidence`, and `dir/**` excludes a whole directory. The report lists the
  patterns.
- A GitHub Action (`action.yml` at the repository root) audits `CLAUDE.md` and `AGENTS.md` in CI. Its
  inputs are `memory`, `evidence`, `exclude`, `strict`, `verifier`, `as-of`, `python-version` and
  `diff-base`; with `diff-base`, only the claims a pull request added or changed are audited. Its
  outputs are `report` and `attention`. See [docs/github-action.md](docs/github-action.md).
- Library: `memfalsify.history` (`memory_diff`, `memory_history`, `compare`, `resolve_commit`),
  `memfalsify.rollback.rollback_patch`, `audit.audit_claims` with `only=`, and `memory.parse_text`.
  `gitstore.GitError` keeps git's own diagnostics out of its `summary`.

#### Changed

- Requires memlab-core 0.2. Memory and evidence reads go through `memcore.reading`.
- Evidence scans list directory symlinks (still never followed) and broken or cyclic symlinks as
  skipped, with a reason. An unreadable subdirectory stops the audit instead of being skipped silently.
- Output from MemFalsify's diff, history and rollback is never counted as evidence, the same as audit
  reports.
- `--output` writes the report as exact UTF-8/LF bytes on every platform. It still refuses to overwrite
  a memory file.

#### Fixed

- The LLM verifier crashed with a traceback when Anthropic credentials could not be resolved. It now
  exits 2 with a message. For every model verifier, a missing SDK, missing key or API error exits 2 and
  writes no partial report.
- Adversarial review of history, diff and rollback:
  - A shallow clone that contains the root commit is complete history. `history` looks at the commit
    object (`--no-replace-objects`): no `parent` line means the root was fetched, even when
    `--is-shallow-repository` is true. A boundary commit that still records an unfetched parent stays
    incomplete.
  - Rollback of a changed claim restores markup that wraps that claim alone, including its old link
    target. Markup shared with a neighboring sentence on the same line is left in place.
  - Diff and history Markdown neutralize terminal and bidi controls in memory text, headings and
    evidence quotes. JSON keeps the original text.
  - CRLF answers from `git rev-parse` (toplevel, prefix, git directory and common directory) are
    accepted. A trailing CR is not left on the prefix, where it would fail the control-character check
    and reject every repository.

### 0.1.0 - 2026-10-10

- First release. Audits `MEMORY.md`, and the local `.md` files it links to, against an evidence
  directory. Candidate evidence is lexical. Only human reviews in `reviews.jsonl` mark a claim supported
  or challenged. Flags overgeneralization, relative time, staleness, inference markers, internal
  conflicts and duplicates, and proposes actions without applying them. Commands: `audit` and `claims`.

## orgmemory

### 0.4.0 - 2026-10-11

Changes are collected in the [release 0.4 section](#release-04).

### 0.3.0 - 2026-10-11

#### Added

- `orgmemory audit DATASET [--as-of TIME] [--stale-days N] [--format] [--strict]` reports, from replay
  alone, where the decision process needs attention. Finding kinds: reversed decisions; conflicting
  decisions at the same time; invalid supersessions; attempts the policy ignored, grouped per actor; pending approvals and
  reconsiderations older than `--stale-days` (default 14); risks raised after approval; approved subjects
  whose dependencies are not approved; and self-approval. Each finding has a fixed severity (high, medium,
  low), the event IDs involved and their quoted evidence. Subjects, notes and quotes are escaped, so they
  appear as literal text in the Markdown report. `--strict` exits 1 on any high-severity finding.

#### Changed

- Requires memlab-core 0.2. The dataset readers use `memcore.reading`.
- `orgmemory github sync` keeps one verified HTTPS connection to api.github.com for the whole run,
  which made a live sync about 20% faster. It falls back to urllib when a proxy is configured. It
  refuses other origins, redirects, caller-supplied Host headers and network-path targets.
- Long GitHub syncs print a throttled progress line on stderr, and a line before each rate-limit sleep.
  Stdout is unchanged.

### 0.2.0 - 2026-10-11

#### Added

- `orgmemory github sync DATASET OWNER/REPO` maps GitHub issues and pull requests (timelines, comments,
  reviews) to events and evidence, deterministically. Its options are `--full`, `--since`, `--issue`,
  `--max-wait`, `--save-capture` and `--dry-run`.
- `orgmemory github convert DATASET CAPTURE` applies a saved capture offline.
- Imports are create-only. They keep cursors, report drift and recover after a crash. A run that did not
  complete exits 3. See [docs/design/github-ingestion-spec.md](docs/design/github-ingestion-spec.md).

### 0.1.0 - 2026-10-10

- First release. Replays `events.jsonl` under the authority rules in `policy.json`, with provenance,
  reconsideration, ignored unauthorized changes and conflict records. Commands: `replay`, `explain`,
  `validate`, `append` and `evidence add`.

## memlab-mcp

### 0.4.0 - 2026-10-11

Changes are collected in the [release 0.4 section](#release-04).

### 0.3.0 - 2026-10-11

#### Added

- `memfalsify_diff`, `memfalsify_history` and `memfalsify_rollback`, read-only tools over the library
  behind `memfalsify diff`, `history` and `rollback`. `memfalsify_rollback` returns the patch text and
  never applies it. Revisions are validated exactly like the CLI's.
- An `exclude` argument for `memfalsify_audit` and `memfalsify_diff`, like `--exclude`.

#### Changed

- Requires memlab-core 0.2 and memfalsify 0.3.
- `--allow-model-verifiers` also enables model verifiers for `memfalsify_diff`, and marks that tool as
  open-world.

#### Security

- The git tools refuse a memory file whose repository has its work tree, git directory, common
  directory (a worktree's main repository) or object store outside `--root`. They also refuse a
  repository with alternates or with symlinks in its object store. So a `.git` file, a worktree or a
  symlink cannot make git read outside the root. `GIT_DIR`-style variables in the server's environment
  are ignored. Git's own error output is not passed on, because it can name outside paths.

### 0.2.0 - 2026-10-11

#### Added

- `orgmemory_audit`, the read-only decision-process audit. Requires orgmemory 0.3.

#### Fixed

- Confined evidence walks fail closed when a directory entry cannot be classified. Before, such an
  entry could vanish from a scan.

### 0.1.0 - 2026-10-11

- First version: a read-only MCP server over stdio with `memfalsify_claims`, `memfalsify_audit`,
  `orgmemory_validate`, `orgmemory_replay` and `orgmemory_explain`. Every path is confined to `--root`
  through descriptor-based opens that refuse symlinks. Model verifiers need `--allow-model-verifiers`.
  Errors carry no tracebacks, backend diagnostics or outside-root paths.

## Repository

Changes outside the packages since 0.1:

- Skills for Claude Code and Codex (`skills/`) cover every command, the MCP tools and the safety
  boundaries. `tests/test_skills.py` fails when a skill names a command or flag that the CLI does not
  have.
- A labeled MemFalsify benchmark (`benchmarks/memfalsify`) with measured lexical results.
- A Chinese README, a Chinese MemFalsify example, `scripts/setup-local.sh`, and shared agent
  instructions in `AGENTS.md`.
- CI runs the full suite on Linux, macOS and Windows with Python 3.10 to 3.13. `.gitattributes` keeps
  evidence and golden files byte-identical on every platform.
