# Changelog

All notable changes to the four packages in this repository, newest first. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Each package has its own version number. While
they are 0.x, a minor version may change behavior. Pins between the packages admit one minor version of
each dependency, for example `memlab-core>=0.2,<0.3`.

Release 0.3 contains:

| Directory | Distribution | Version | Previous release |
|---|---|---|---|
| `packages/memcore` | `memlab-core` | 0.2.0 | 0.1.0 |
| `packages/memfalsify` | `memfalsify` | 0.3.0 | 0.1.0 |
| `packages/orgmemory` | `orgmemory` | 0.3.0 | 0.1.0 |
| `packages/memlab-mcp` | `memlab-mcp` | 0.3.0 | none (added after 0.1) |

## Unreleased

### orgmemory

#### Added

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

### memlab-mcp

#### Changed

- The orgmemory tools read an optional `policy-log.jsonl`, refusing one that is a symlink leaving the server
  root. `orgmemory_validate` prints the same lines as `orgmemory validate`.

## memlab-core

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
