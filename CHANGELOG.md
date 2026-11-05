# Changelog

All notable changes to the packages in this repository, newest first. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Each package has its own version number. While
they are 0.x, a minor version may change behavior. Pins between the packages admit one minor version of
each dependency, for example `memlab-core>=0.2,<0.3`.

Release 0.5 contains:

| Directory | Distribution | Version | Previous release |
|---|---|---|---|
| `packages/memcore` | `memlab-core` | 0.4.0 | 0.3.0 |
| `packages/memfalsify` | `memfalsify` | 0.5.0 | 0.4.0 |
| `packages/orgmemory` | `orgmemory` | 0.4.1 | 0.4.0 |
| `packages/memlab-mcp` | `memlab-mcp` | 0.5.0 | 0.4.0 |
| `packages/memlab-ui` | `memlab-ui` | 0.1.0 | none (new) |

## Unreleased

### Added

- memfalsify: review relation `unrelated` dismisses a false flag: a person read this passage and it does not bear on the
  claim. Same fields and same exact-quote check as the other relations. The passage stops being a candidate for that
  claim (a model is not asked about it, and the next passage takes its place), and the status comes from what remains.
  A dismissal never marks a claim supported or challenged. Markdown and HTML reports print the line under each unsettled
  flag, and `memfalsify init` says how. Reports state how many candidates were dismissed and by whom. In JSON,
  `Finding.dismissed`, `AuditReport.dismissed` and `AuditSet.dismissed` appear only when there are some, so reports with
  no dismissal keep their shape. If the claim or the quote is gone, the dismissal stops applying and the report lists it
  under Skipped evidence as `review no longer applies`. A short hand-written quote dismisses the passage it sits in and
  word-for-word copies of that passage in the file, nothing else.
- memlab-ui: a flagged card's Details hold the dismissal line; a card says how many passages a reviewer dismissed; a
  dismissal that no longer applies is listed. memlab-mcp: the audit tool and prompt describe dismissals.
- memfalsify: the lexical verifier has a new, strongest rule: both texts state the same key with different values. The
  key must look like an identifier (`snake_case`, `kebab-case`, `camelCase`, `dotted.name`, a `--flag` or a name in
  backticks), in `key: 100`, `key (default: 100)`, `key: int = 180`, `key: float = Field(default=0.1, ...)`, `"key": 5`,
  `| key | 100 |` and similar forms. The reason reads `same setting max_steps: claim 100 vs source 500`. `None` and
  `null` mean no value. A value merely set (`key=100`, `key = 100`, `--flag 100`) is not compared with a stated default,
  and against anything else it counts for less (`set differently ...`).
- memfalsify: the passage that states a key a claim binds is compared however little else it shares, so a long
  description of `max_steps` is checked against `max_steps: int = 500`. Lexical verifier only, and not with
  `--max-judged`.
- memfalsify: two more lexical reasons: `different units` (2 minutes against 2 seconds, 512 GB against 512 GiB, and in a
  named value `512MB` against `512GB` or `30m` against `30s`) and `source says what the claim names was removed`.
- `benchmarks/memfalsify/wild/`: a real-repository benchmark with 413 labeled pairs from public projects' agent files, as
  hashes and metadata, and a script that needs your own clones. All labeling is by AI models, not people; its README says
  how the numbers can mislead.
- `docs/lexical-rules.md` and `docs/lexical-rules.zh-CN.md`: the lexical rules in full.
- memfalsify: `memfalsify audit --format brief`, a short view for the terminal: only the claims that need a look, each
  with the claim, the strongest quote against it, its file and line, and why the two were paired. A long Markdown report
  on a terminal mentions it on stderr. Other formats and exit codes stay as they were.
- memfalsify: `memfalsify audit` reports a path a memory names that exists nowhere under the evidence root, as the new
  issue kind `missing_reference` with the proposal `fix_reference`. The path is looked up, not guessed, and the claim
  keeps its status. It runs with no file named, and with `--evidence` when the memory file sits under that directory;
  `memfalsify diff` and the pre-commit hooks do not run it. `--strict` and the Action's `strict` fail on it, which
  existing strict users will notice. A claim a person confirms as supported through `--reviews` no longer fails
  `--strict` on the path and keeps the issue in the report; the Action does not read reviews. `--no-references` (Action
  input `references: "false"`) turns the check off. Paths are only compared with names from a listing. Git is asked only
  with `check-ignore --no-index` and `config --get`, with hooks and the file system monitor off. In a sparse checkout
  the check reports nothing. See [docs/quickstart.md](docs/quickstart.md).
- Repository: `SECURITY.md`, `CONTRIBUTING.md` and `CONTRIBUTING.zh-CN.md`, two issue forms (a bug report, and a false
  flag or missed contradiction), a pull request template and a badge row at the top of both READMEs.
  `tests/test_repo_face.py` keeps the Python badge equal to the CI matrix and the package classifiers, and the dogfood
  command in `CONTRIBUTING.md` equal to the CI job.
- memfalsify: `memfalsify init [DIR]` (default `memfalsify-demo`, `--lang en|zh`) writes a small offline demo:
  `MEMORY.md` with three claims, `evidence/` with two files and an empty `reviews.jsonl`. `memfalsify audit` inside it
  shows one possible contradiction with both quotes, one piece of supporting evidence and one unsupported claim. It
  refuses with exit 2, writing nothing, if a target exists. The templates ship in the wheel.
- memfalsify: the MEMORY argument of `memfalsify audit` is now optional. Left out, the command takes every instruction
  file `memfalsify discover` finds in the current directory plus a top-level `MEMORY.md`, in path order, and compares
  them with that directory or `--evidence`. The evidence follows the rules of the web page, which keep
  Memory Lab's own files (review records, decision logs, generated reports) out of it. One line on stderr says which
  files were chosen. Several files share one Markdown or HTML report, and a claim stated in several files is reported
  once. `--format json` prints one `AuditSet` object at any file count. `--strict` keeps its meaning across the files.
  An empty choice exits 2 and suggests `memfalsify init`. Runs that name a file print the same bytes as 0.5.0
  (`tests/fixtures/audit-explicit`).
- memfalsify: `memfalsify evidence from-git --since WHEN --output DIR [--repo DIR] [--max-commits N]` writes one small
  Markdown file per commit, named by its short hash: subject, body, author date and full hash. The same commits give
  the same bytes and a re-run adds only new files. `--since` takes a date or a revision, validated like the other
  revision inputs. Commit messages are treated as untrusted: names come from the hash, and control, bidi and
  zero-width characters are stored as visible `\uXXXX` escapes, so printing a file is safe. A GitHub version is on the
  roadmap.
- memfalsify: public API `memfalsify.AuditSet` and `memfalsify.audit_directory`.
- memlab (`packages/memlab`, new): a meta-distribution holding no modules. It installs `memfalsify` and `orgmemory`,
  with the extras `ui` (memlab-ui), `mcp` (memlab-mcp), `llm`, `jev`, `openai` and `all` (the last four pass through to
  memfalsify's extras of those names). Its version names the release. It is called `memlab` because PyPI treats
  `memory-lab` as the existing project `memorylab`: see [docs/publishing.md](docs/publishing.md#the-name-memlab).
- Repository: `docs/architecture.md` and `docs/architecture.zh-CN.md`: how MemFalsify and orgmemory relate, the data
  flow across Remember, Verify, Challenge, Resolve and Evolve, the field-by-field mapping from a finding to an event,
  and why orgmemory keeps its eight event actions.
- Repository: `examples/closed-loop/`: a runnable demo on synthetic data. A small git repository, `memfalsify audit` and
  `history`, `to_events.py` (standard library only) and the real `orgmemory evidence add`, `append`, `audit` and
  `explain`. An approval from someone without the permission is recorded and ignored; a person who holds it rejects the
  line, and the correction goes through the loop again. The expected reports are committed, and
  `tests/test_closed_loop.py` runs the demo from scratch with the real CLIs and compares them.
- Repository: `docs/quickstart.md` and `docs/quickstart.zh-CN.md`: the path to a first report, how to read it, running
  it in your own repository, and git history as evidence.
- Repository: `.github/workflows/publish.yml`, a manual workflow that publishes to PyPI through Trusted Publishing, in
  dependency order. It starts only from "Run workflow", takes a typed `confirm` that must equal the built memlab
  version, and by default (`target: build-only`) uploads nothing. Nothing has been published yet. What the owner does
  once on pypi.org, and the dry runs: [docs/publishing.md](docs/publishing.md).
- Repository: `scripts/dists.py` builds every sdist and wheel with hash-locked tools
  (`scripts/publish-requirements.txt`) and checks them against the checkout. `scripts/rehearse_install.py` installs them
  in a fresh virtualenv and runs the commands. CI's new `packaging` job runs the rehearsal on every push.

### Changed

- **Behavior change.** memfalsify: lexical contradictions are much stricter. Numbers count only on nearly identical
  sentences or clauses, next to the same word, and not when each clause goes on to name a case of its own (`2 spaces in
  YAML files`, `4 spaces in Python files`; Chinese text is left as it was). References such as `#7326`, digits in names,
  paths and versions, times, ports and operands no longer count, a date is compared only with a date, `0723` equals
  `723`, `1,000` reads as 1000, `18.x` and `3.x` take in `18.20` and `3.12`, and `100/min` is its number. In a named
  value, `-1` and `1` differ. Opposing terms and negation need the same subject on both sides, and a negation cue must
  reach a word the other side states plainly. Two short statements in the same terms, apart from an opposing word or a
  `not`, are about one subject however few terms they share: `Telemetry is disabled.` against `Telemetry is enabled.`. A
  line opening with `Do not`, `Never` or `Skip` is an instruction: only a line that says nearly the same without the
  `not`, in words of its own, speaks against it. `Do not use npm in this repository.` against `Use npm in this
  repository.` is flagged; `Do not run migrations in production.` against `Run migrations in production after every
  deploy.` is not. On 15 public repositories' agent files, flagged pairs went from 1,719 to 47 and flagged claims from
  794 to 29. `audit --strict` and the GitHub Action therefore pass where they used to fail, and a clean report says less
  than a flag does. Some real contradictions are no longer flagged; `docs/lexical-rules.md` lists what the verifier
  misses.
- **Behavior change.** memfalsify: add/remove, start/stop and open/close are no longer opposing terms for English text,
  which also lowers term coverage slightly for such pairs.
- **Behavior change.** memfalsify: under the lexical verifier, tests contradict nothing, changelogs only through a stated
  value or by saying something was removed, examples only through a stated value, and source code only through a declared
  value (`key: int = 5`, `KEY = 5`, `key (default: 5)`; `retry({ maxRetries: 5 })` is one call's argument). All of them
  can still support a claim. Everything under `test/`, `tests/` and `__tests__/` is a test except a document, while
  `testing/`, `spec/`, `specs/` and `e2e/` make only code a test. `history`, `news`, `changes` and `releases` name a
  changelog only as the whole file name, and a code file is never a changelog. Model verifiers are not affected. A
  passage set aside this way does not appear in the report.
- **Behavior change.** memfalsify: a directory scan, for every verifier, no longer reads `vendor/`, `third_party/`,
  `thirdparty/`, `testdata/`, `fixtures/`, `__snapshots__/`, `*.min.js`, `*.min.css`, `*.snap`, `*.svg`, `*.patch`,
  `*.lock`, `package-lock.json`, `pnpm-lock.yaml`, `npm-shrinkwrap.json` or `go.sum`, or lines over 1,000 characters,
  which includes a long paragraph kept on one line. What was left out is listed under Skipped evidence. Paths are
  relative to the evidence root, so pointing `--evidence` at such a directory reads it, and a review can still quote any
  file in the root.
- memfalsify, **breaking for existing reviews**: a memory file is read as Markdown blocks, not line by line. The lines of
  a wrapped paragraph or list item are one claim, located at the line it starts on. A table row is one statement. `e.g.`,
  `i.e.`, `vs.`, `cf.` and similar no longer end a sentence, and neither does `etc.` before more of the sentence. These
  are no longer claims: table headers and rules, comments over several lines, indented code blocks, Setext headings (not
  sentences above a `---` that end in a period or semicolon), lines of nothing but HTML tags, link definitions and labels
  ending in a colon (`Run ty:`, `When writing comments:`; a line with `must`, `should`, `never`, `always` or `do not`
  stays). Chinese text and anything that reads as a sentence stay. Claim IDs come from the text, so a claim whose split
  changed gets a new ID, and old reviews for those lines stop applying. A confirming review naming an old ID stops the
  audit with `unknown memory_id` (exit 2). The error now names every such line and points at `memfalsify claims`.
  Re-wrapping a paragraph mostly no longer shows as a change in `diff` or `history`; the rules page says when it still
  does.
- memfalsify: only the sentence that holds a link to a local `.md` file is a pointer. A block that opens with such a link
  is still an index entry, but the other sentences of any other block are now claims, where a line with a link used to be
  skipped whole.
- memfalsify: evidence in Markdown, MDX, reStructuredText and plain text is read with its wrapped lines joined, so a
  wrapped claim can match a wrapped sentence. Code and data files stay line by line. A quote can now run over several
  lines: a Markdown report shows it a line per line, and the JSON report and the line under a flag hold its exact bytes.
- memfalsify: `rollback` works on blocks. It restores a wrapped sentence with its line breaks, re-adds a claim after the
  whole block of its neighbour, falls back to one line when a first line would stand alone, prefers a form that leaves
  other claims' IDs as they are, and refuses with a message when the patch would not bring the claim back.
- memfalsify: the file passed with `--reviews` is never read as evidence, including when it sits inside `--evidence`.
- memfalsify: within a status, findings are ordered by their strongest lexical reason, and within a finding contradictions
  come first in the same order. The JSON shape is unchanged.
- memfalsify: the lexical limitation line in every report has new wording, and the `verify_counterevidence` proposal now
  says how to confirm or dismiss (`relation contradicts`, `relation unrelated`). Output for a named memory file is
  therefore no longer byte-identical to 0.5.0, also where verdicts changed.
- memlab-ui: words and highlights for the new reasons, and for what the scan leaves out.
- examples/closed-loop: `to_events.py` finds a claim that runs over several lines; its evidence record quotes the first
  line.
- `benchmarks/memfalsify/RESULTS.md`: rerun, with contradiction precision 0.92, recall 0.44, F1 0.60 and 3-way accuracy
  0.48, and a table of the verdicts that flipped, 3 of them correct flags lost.
- memfalsify: `memfalsify audit` is much faster on large repositories, and that change alone leaves reports byte-identical. One run each on
  public clones (Apple M4 Pro, with other jobs running): next.js `AGENTS.md` 1,197 s to 42 s, grafana `CLAUDE.md` 947 s
  to 36 s, supabase `CLAUDE.md` 197 s to 18 s, mem0 `CLAUDE.md` 113 s to 4 s. n8n and bun `AGENTS.md`, which did not
  finish in 900 s, now take 54 s and 70 s. Peak RSS falls too: supabase 4,015 MB to 1,138 MB, next.js 5,752 MB to
  1,352 MB, grafana 9,664 MB to 2,026 MB. A claim now reads only the passages that can reach the threshold, through an
  index from term to passages that keeps today's ordering. Passages build their evidence span on demand, and the
  lexical verifier is no longer asked about pairs it must call unrelated. Claims with Chinese text gain less. See
  `benchmarks/perf/README.md`, which also adds `run.py --evidence N`.
- `scripts/setup-local.sh` installs `cmarkgfm` and `setuptools>=77`, as CI does, so a contributor runs the same tests.
- memlab-core, memfalsify, orgmemory, memlab-mcp, memlab-ui: PyPI metadata (keywords, classifiers for Python 3.10 to
  3.13, and `Documentation`, `Repository` and `Issues` links), and READMEs that stand alone on PyPI, with an install
  line and a link in place of "the root README". memlab-mcp and memlab-ui declare macOS and Linux as their operating
  systems.

### Fixed

- memfalsify: several inputs took time, and one took memory, in proportion to the square of their length, in a memory
  file or in evidence: one key bound many times, `key:` followed by a long run of spaces, many negation words in one
  passage, a line of spaces holding a pipe, many short sentences in emphasis in one block, and long runs of `__`, `**`,
  `[`, `[a](` or `[[`. All now grow in proportion to their length. One key keeps at most eight different values from one
  text.
- orgmemory: `append`, `evidence add` and `policy grant`/`revoke` accept a dataset path that goes through a link above
  the dataset, such as `/tmp` or `/var` on macOS; they used to end in a traceback. A link at the dataset directory or
  below it is still refused, now as a clean exit 2.

## Release 0.5

Release 0.5 contains memlab-core 0.4.0, memfalsify 0.5.0, orgmemory 0.4.1, memlab-mcp 0.5.0 and the new
memlab-ui 0.1.0. It was tagged `v0.5.0` on 2026-10-12; all changes since release 0.4 are collected here.
Highlights: the OpenAI-compatible verifier (`--verifier openai`, for OpenAI, xAI Grok, DeepSeek, Google Gemini,
a local Ollama server or any compatible endpoint) and `memlab-ui`, a local read-only web page.

memlab-ui's resource limits are best-effort robustness against a project's own content, not a security
boundary: they keep a very large or unusual project from tying up the page, and the page says when they cut
it short.

### Added

- `memlab-ui` (`packages/memlab-ui`): a local, read-only web page for trying Memory Lab without the command
  line, started as `memlab-ui --root DIR [--port N]`. Home lists the agent files under the root; "Check an
  agent file" runs the lexical audit (or, when the root is a repository, the diff since a revision) and
  shows each flagged statement next to the quote that seems to disagree; "Team decisions" opens the first
  decision log and shows each topic's status, the deciding quote, a timeline of what counted, audit
  findings and changes between two dates. Memory Lab's own files (review records, decision logs, generated
  reports and exports) are recognised by content and listed as left out, never quoted as evidence. It uses
  only the standard library's `http.server` and has no JavaScript. Every page reads in English or Simplified
  Chinese (`?lang=zh`, with a switch in the sidebar). Public API: `memlab_ui.create_server` (binds 127.0.0.1
  without serving) and `__version__`. Requires memlab-core 0.4, memfalsify 0.5.0 and orgmemory 0.4.1. See
  [docs/ui.md](docs/ui.md).

### Security

- memlab-ui binds 127.0.0.1 only and checks the `Host` header against DNS rebinding; refuses requests sent by
  other sites (`Sec-Fetch-Site` other than `same-origin` or `none`, or a foreign `Origin`) before any work; with
  `--allow-model-verifiers`, runs a model check only for a request carrying the random key it prints in the
  terminal at start-up (like Jupyter; pages pass it on only to a visitor who already has it, and it is compared
  as bytes); refuses agent files over 256 KB before parsing them; answers GET and HEAD only (405
  otherwise, HTTP/0.9 gets a 400 with full headers); serves at most 16 connections and computes at most 2 pages
  at once; needs the request line and headers within 10 s in total; confines every path to `--root` with
  confined reads; writes nothing; keeps model verifiers off unless `--allow-model-verifiers`; escapes every
  untrusted value; sends a strict Content-Security-Policy that loads only its own logo and favicon; and
  answers errors without tracebacks or outside paths. Path and subject values are used exactly as sent.
- MemFalsify's evidence scan enforces its 2 MB limit while reading: `read_input_bytes()` takes an optional
  `max_bytes` (default unchanged), checks the opened file's size with fstat and reads at most `max_bytes + 1`
  bytes, so a large or growing file is never held in memory whole.
- memlab-core's internal `read_limits()` bounds one execution context: bytes per file and in total, charged in
  bytes before decoding and, for `open_text` streams, as each byte is read; directories, directory entries
  (counted while listing) and depth for every walk; and named budgets that MemFalsify draws from while it
  builds (`claims` parsed, evidence `passages`, claim-passage and old-new `comparisons`). Git objects are charged
  before their bytes are read, media files are hashed within the same byte limits (a sidecar whose media file
  is over them is skipped with that reason), and bytes read before a refusal are charged too. Internal-conflict
  comparisons draw from the comparison budget. memlab-ui sets these per page and says when a page was cut
  short; outside `read_limits` nothing is bounded and every read returns what it did before. Walks are counted
  the same way with or without confinement; the confined walk is iterative, so a deep tree meets the depth
  limit instead of Python's recursion limit.

### Changed

- memlab-core 0.4.0 adds the internal read-limit API above (`read_limits`, `take`, `available`, `charge`,
  `read_head`, `ReadLimitError`, `read_input_bytes(max_bytes=...)`); its public `__all__` is unchanged.
  memfalsify 0.5.0 and memlab-ui use it. memfalsify 0.5.0, orgmemory 0.4.1 and memlab-mcp 0.5.0 require
  `memlab-core>=0.4,<0.5`: their dependency metadata changed, so their released 0.4.0 versions could not be
  reused. memlab-ui requires `memfalsify>=0.5,<0.6` and `orgmemory>=0.4.1,<0.5`; memlab-mcp requires
  `memfalsify>=0.5,<0.6`, the first memfalsify with the `openai` verifier it imports.
  `tests/test_packaging.py` checks that every package imports only names that the lowest version its pins
  admit of each package here defines (released versions' names are in `tests/released_exports.json`) and
  that a package whose dependencies differ from a released version's (`tests/released_metadata.json`, from
  the v0.4.0 tag) has a new version. The packaging tests run on Python 3.10 too, with `tomli`, which CI and
  `scripts/setup-local.sh` install there.

### memfalsify

#### Added

- `--verifier openai` on `audit` and `diff` (`memfalsify.OpenAICompatVerifier`, new extra `[openai]`, also in
  `[all]`): one verifier for any OpenAI-compatible Chat Completions API. `--provider` presets the base URL,
  the key's environment variable and the response format for `openai` (default), `grok` (xAI), `deepseek`,
  `gemini` (Google's OpenAI-compatible endpoint) and `ollama` (`http://localhost:11434/v1`, no key);
  `--base-url`, `--api-key-env` and `--openai-response-format` override them. `--openai-model` is required:
  model names differ between providers, so there is no default. Memory claims and evidence passages are sent
  to the configured endpoint.
  - The base URL must be `https://`, or `http://` only to `localhost`, `127.0.0.1` or `::1`; a user name,
    password, query or fragment in it is refused, and no message repeats it.
  - Requests go only to the base URL: redirects are not followed (a 3xx stops the audit) and proxy variables
    are not used. Certificates are verified as the OpenAI SDK and httpx2 ship it (the platform's trust store;
    on Linux, OpenSSL honours `SSL_CERT_FILE` and `SSL_CERT_DIR`).
  - The key is read from the named variable, never taken as an argument, and must be at least 16 characters,
    all of them characters keys use. The threat model is everything the provider or the model controls, and
    the promise is that the literal key and its once-encoded forms (percent, `\u`/`\x`/`\/` escapes, HTML
    references) never appear in output. A provider error is shown as its HTTP status, its type, code and param
    when each is a plain token of at most 40 characters sharing no 8 characters with the key
    (case-insensitively), and a fixed hint for the status, never the provider's message. The model's
    rationale, the only free text kept, has the key redacted, each run of key characters that shows 8 or more
    characters of the key once decoded elided (`[…]`), and is withheld if it still shows the key once decoded.
    Token counts above 10,000,000 per request count as missing. finish_reason and other envelope fields are
    never echoed; no error keeps the SDK exception as its cause or context; and the SDK's and the HTTP stack's
    log records (`OPENAI_LOG=debug`) are redacted as they are made, a message, exception or stack that still
    shows the key once decoded being withheld. A provider that encodes the key it received more deeply is out
    of scope (it holds the key already). Other code in the same Python process is trusted. The OpenAI SDK's
    `OPENAI_BASE_URL`, `OPENAI_ORG_ID`, `OPENAI_PROJECT_ID` and `OPENAI_CUSTOM_HEADERS` are ignored, so
    settings meant for one provider never reach another.
  - `--openai-response-format` is `json_schema` (strict structured output), `json_object` or `text`: by default
    `json_object` for `deepseek` (DeepSeek documents no `json_schema`) and `json_schema` for the other providers
    and for any explicit `--base-url`, DeepSeek's included. Nothing is retried in another format; an HTTP 400
    or 422 says which simpler format to try. Only an answer with `finish_reason` `stop` is read; an unfinished
    one, one that is not a valid verdict (invalid JSON, a repeated or unknown key, a relation outside the
    schema), an empty answer, a malformed response and a refusal are no verdict (`unrelated`), never support.
  - API errors exit 2 like the other backends. `--max-concurrency` applies; the report's API usage line has
    requests and tokens, with no cache counters. `--batch` is not supported with it (exit 2), and `dual` and
    `cascade` still pair Claude with Jev.
  - The pre-commit hooks (`memfalsify-pre-commit diff` and `audit`) take the same options.

### memlab-mcp

#### Added

- `memfalsify_audit` and `memfalsify_diff` accept `verifier: "openai"`, refused unless the server runs with
  `--allow-model-verifiers`. Its endpoint comes only from server flags (`--openai-model`,
  `--openai-provider`, `--openai-base-url`, `--openai-api-key-env`, `--openai-response-format`), checked at
  startup; no tool argument names a provider, URL, model, key variable or response format, so a client cannot
  send text elsewhere. Without `--openai-model` the verifier is refused as not configured.

### Repository

#### Added

- GitHub Action: `verifier: openai`, with the inputs `openai-model`, `provider`, `base-url`, `api-key-env`
  and `openai-response-format` (passed through `env:` and checked before the audit), and a hash-pinned lock
  set `action/requirements/openai.txt`. `all.txt` (dual and cascade) still holds only the Claude and Jev SDKs.
- MemFalsify benchmark: `run.py --verifier openai` with the same options (the response format is recorded in
  the JSON settings), and a documented local Ollama run. No model results are recorded.
- `scripts/setup-local.sh`, both CI install lines and `docs/local-setup*.md` include `-e packages/memlab-ui`.
- Windows checks that run on any OS: `tests/test_windows_guard.py` requires `newline=""` on text-mode
  writes, the confinement marker on tests that build the MCP or UI server, and f-string syntax that Python 3.10
  parses; `pytest --simulate-windows-newlines` writes CRLF like Windows text mode, and a Linux CI job runs the
  suite with it. Test fixtures are written as exact bytes (`write_text_exact`, `scripts/exact_writes.py`).
  AGENTS.md has the Windows rules.

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

### 0.4.0 - 2026-10-12

Changes are collected in the [release 0.5 section](#release-05): the internal read-limit API.

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

### 0.5.0 - 2026-10-12

Changes are collected in the [release 0.5 section](#release-05): the OpenAI-compatible verifier (`--verifier openai`, `--openai-response-format`), the 2 MB evidence limit holds while reading, and the scan, the parser and the diff draw from memlab-core's read limits. Requires memlab-core 0.4.

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

### 0.4.1 - 2026-10-12

Requires memlab-core 0.4 (`>=0.4,<0.5`); no other change. See the [release 0.5 section](#release-05).

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

### 0.5.0 - 2026-10-12

The `openai` verifier with server-side `--openai-model`, `--openai-provider`, `--openai-base-url`, `--openai-api-key-env` and `--openai-response-format` options (tool arguments cannot change them). Requires memlab-core 0.4 (`>=0.4,<0.5`) and memfalsify 0.5 (`>=0.5,<0.6`). See the [release 0.5 section](#release-05).

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

## memlab-ui

### 0.1.0 - 2026-10-12

First release. Changes are collected in the [release 0.5 section](#release-05).

## memlab

### 0.5.0 - Unreleased

First version. See [Unreleased](#unreleased).

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
