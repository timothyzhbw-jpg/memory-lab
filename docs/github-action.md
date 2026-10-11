# MemFalsify GitHub Action

The action audits agent instruction files (`CLAUDE.md`, `AGENTS.md`, or with `memory: auto` every Claude Code,
Codex, Cursor, GitHub Copilot, Gemini CLI and Windsurf instruction file in the repository) against the
repository they describe, on every push or pull request. Each instruction is checked against the repository's
own docs, configs and code comments; the evidence-cited report goes to the job summary. The action is
read-only: it never edits an instruction file or anything else in the workspace.

## What it catches, and what it does not

The default verifier is `lexical`: offline, deterministic, and nothing leaves the runner. A passage is only
compared with an instruction when it shares most of the instruction's terms (half to be considered, 60% to count
as related). Within that, it reports **possible counterevidence** when:

- **numbers differ**: the instruction says the API limit is 100 requests per minute, `docs/api.md` says 60;
  `PostgreSQL 15` against `PostgreSQL 16`.
- **opposing terms** appear: approve/reject, enable/disable, allow/block, public/private, required/optional,
  supported/deprecated, and similar pairs.
- **only one side is negated**: one says *run X*, the other *never run X* (not, no, never, without, skip...).

It also flags **wording issues** in the instructions themselves. These are reported but never fail the build:
absolute wording (*always*, *never*, *all*), relative time without a date (*next week*), dates older than
180 days, hedges (*probably*), duplicates, and two instructions that contradict each other.

It does **not** catch:

- **A rule that code silently violates.** "Never log access tokens" is not contradicted by a line that logs one:
  code is indexed as text, and no sentence in the repository disagrees with the rule. MemFalsify checks
  instructions against what the repository *says*, not what it *does*; linters and tests cover behavior.
- **Paraphrases.** "Deploys go through staging" against "production releases skip the pre-prod cluster" share
  too few words. Model verifiers (below) judge paraphrases, within a bounded set of passages.
- **Facts the repository never states.** These are reported as NO EVIDENCE, which never fails the build.

Every match is a *candidate*. Only a human review can confirm it, and the action does not read
`reviews.jsonl` yet, even though the report's proposed actions mention it.

## Usage

```yaml
name: Agent instructions
on: [push, pull_request]
permissions:
  contents: read
jobs:
  memfalsify:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          persist-credentials: false
      - uses: timothyzhbw-jpg/memory-lab@main
        with:
          exclude: |
            vendor/**
            CHANGELOG.md
```

Pin the action to a full commit SHA rather than `@main`, with a comment saying which version it is, for
example `uses: timothyzhbw-jpg/memory-lab@<40-character SHA>  # main, 2026-10-11`. The action runs inside your
job, and a branch can move under you; update the SHA when you choose to. The install step pulls memfalsify's
dependencies (pydantic, typer) from PyPI within the version ranges the package declares.

This repository audits its own `AGENTS.md` in [`.github/workflows/ci.yml`](../.github/workflows/ci.yml)
(`uses: ./`).

Set `persist-credentials: false` on checkout so its job token is not stored in `.git/config` while the
audit runs. The action skips memory symlinks that leave the workspace or resolve into hidden or skipped
directories, with a warning annotation. Memory, linked memory and evidence inputs are checked against
both their original paths and resolved targets; a harmless filename cannot expose `.git/config`.
Hidden directories are allowed only where an agent documents its instruction files (`.cursor/rules/`,
`.github/instructions/`, `.claude/rules/`, ...), for the walked path and the resolved target alike.

## Audit every agent's instruction files

With `memory: auto`, the action audits every instruction file that `memfalsify discover` finds in the
workspace, instead of a fixed list:

```yaml
      - uses: timothyzhbw-jpg/memory-lab@<sha>
        with:
          memory: auto
          exclude: |
            vendor/**
            tests/fixtures/**
```

| Agent | Files found | Frontmatter shown in the report |
|---|---|---|
| Claude Code | `CLAUDE.md`, `CLAUDE.local.md`, `.claude/CLAUDE.md`, `.claude/rules/**/*.md` | `paths` |
| Codex and other `AGENTS.md` readers | `AGENTS.md`, `AGENTS.override.md`, `.claude/AGENTS.md` | |
| Cursor | `.cursor/rules/**/*.mdc`, and `.cursorrules` at the top | `description`, `globs`, `alwaysApply` |
| GitHub Copilot | `.github/copilot-instructions.md`, `.github/instructions/**/*.instructions.md` (top `.github` only) | `applyTo`, `excludeAgent` |
| Gemini CLI | `GEMINI.md` | |
| Windsurf (Devin Desktop) | `.windsurf/rules/*.md`, `.devin/rules/*.md`, and `.windsurfrules` at the top | `trigger`, `globs`, `description` |

- Files not marked "at the top" or "top `.github` only" are found in any directory, because the agents read
  nested ones. The step log lists every file found and every path skipped.
- The `exclude` globs also leave instruction files out of discovery. There they are matched against paths
  relative to the workspace, which are the same paths as for evidence when `evidence` is `.` (the default).
  Exclude fixtures, examples and vendored code, whose instruction files are not yours.
- Discovery skips other hidden directories, `node_modules/`, `__pycache__/`, `venv/` and nested git
  repositories (submodules), and only follows a symlink to a regular file inside the workspace. A file
  reachable under two names, such as a `CLAUDE.md` symlinked to `AGENTS.md`, is audited once. If the filesystem
  reports no file index, both names are audited.
- No file found is a notice and success, as with named files that do not exist. A directory that cannot be
  read fails the step with exit 2.
- With `diff-base`, discovery looks at the checked-out head: an instruction file that the pull request adds
  is diffed (all its claims are added), but one that it deletes is not reported. Name a file in `memory` to
  see its removal.
- Files under hidden directories (`.cursor/`, `.github/`, `.claude/`, `.windsurf/`, `.devin/`) are never
  evidence. When a Cursor rule contradicts `AGENTS.md`, the Cursor rule's audit reports it (`AGENTS.md` is
  evidence), but the audit of `AGENTS.md` does not.

## Gate a pull request on the claims it changes

A strict audit of the whole file fails every pull request until all old candidates are resolved. With
`diff-base`, the action runs `memfalsify diff` instead: it compares each instruction file with its version at
that revision, audits only the claims the pull request adds or changes, and fails only on those. Claims it
leaves alone are not re-checked; keep a full audit on `push` or on a schedule for them.

```yaml
name: Agent instructions
on: pull_request
permissions:
  contents: read
jobs:
  memfalsify:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0   # the base commit must be in the checkout
          persist-credentials: false
      - uses: timothyzhbw-jpg/memory-lab@<sha>
        with:
          diff-base: ${{ github.event.pull_request.base.sha }}
```

`actions/checkout` fetches a single commit by default, and then the base commit is missing: use
`fetch-depth: 0`, or a depth that reaches the base. The value must name a commit: the action rejects anything
that starts with `-`, verifies it with `git rev-parse --verify --end-of-options <rev>^{commit}`, and fails with
exit 2 if that fails. On `push` events `github.event.before` is all zeros for a new branch, so prefer
`pull_request`. The report lists each changed claim's old and new wording, the added and removed claims, and
the audit of the added and changed ones; the `attention` output counts only those. For the commit still
in the index, the [pre-commit hooks](../README.md#pre-commit) run that check locally.

## Checking a commit locally

Before the commit exists, [`memfalsify-diff` and `memfalsify-audit`](../.pre-commit-hooks.yaml) apply the
same attention gate to staged `CLAUDE.md`, `AGENTS.md` and `MEMORY.md`. Setup is in the
[README](../README.md#pre-commit). `memfalsify-diff` reads those files from the index and audits added or
changed claims against the working tree, the same check as
`memfalsify diff CLAUDE.md --base HEAD --staged --evidence . --strict`.
The CLI leaves `--strict` off and, without `--evidence`, has nothing to contradict a claim.

## Inputs

| Input | Default | Meaning |
|---|---|---|
| `memory` | `CLAUDE.md AGENTS.md` | Files to audit, separated by spaces or newlines. Quote a path that contains spaces, shell-style: `CLAUDE.md "docs/agent notes.md"`. Missing files are skipped; if none exists, the action emits a notice and succeeds. `auto` (alone) audits every instruction file `memfalsify discover` finds (see above). |
| `evidence` | `.` | Evidence directory, relative to the workspace. |
| `exclude` | none | Globs to leave out of the evidence, one per line (see Exclusions); with `memory: auto`, also out of discovery. |
| `strict` | `"true"` | Fail when any instruction is challenged, conflicted, disputed or has possible counterevidence. |
| `verifier` | `lexical` | `lexical`, `llm`, `jev`, `dual` or `cascade` (see Model verifiers). |
| `diff-base` | none | Revision to diff against, for example `${{ github.event.pull_request.base.sha }}`. When set, only claims added or changed since then are audited and can fail the step (see above). Needs that commit in the checkout. |
| `as-of` | today | Reference date `YYYY-MM-DD` for the staleness check. |
| `python-version` | `3.12` | Python for an isolated install under `$RUNNER_TEMP`. The job's own Python and `PATH` are not changed. |

Only the files listed in `memory` (or discovered with `auto`) are audited: Markdown links and `@file.md`
imports are not followed. To audit another instruction file, list it explicitly. Linked repository documents stay available as evidence; adding a link does not
remove their counterevidence. The CLI separately supports multi-file memories with links followed by default.
Use forward slashes in paths on every runner (`docs/AGENTS.md`). The input uses POSIX shell-style tokenization:
an unquoted backslash escapes the next character, even on Windows.

## Outputs and exit status

| Output | Meaning |
|---|---|
| `report` | Path of the Markdown report under `$RUNNER_TEMP` (the same text is in the job summary). With several files, their reports are concatenated. |
| `attention` | Number of instructions (with `diff-base`: of added or changed instructions) that are challenged, conflicted, disputed or have possible counterevidence. |

- With `strict: "true"`, the step fails (exit 1) when `attention` is above zero. NO EVIDENCE, POSSIBLE SUPPORT
  and wording issues never fail it.
- With `strict: "false"`, the action always reports and emits a warning annotation when `attention` is above
  zero.
- Errors fail the step with exit 2 whatever `strict` says: a missing evidence directory, an invalid `as-of`,
  an unknown verifier, a model verifier without credentials, unreadable evidence, exclusion patterns that
  leave zero indexed passages while there are claims to audit, or a `diff-base` that is not a commit in the
  checkout.

A zero `attention` count means no contradiction was flagged. It does not guarantee that memory files exist
or that their claims have support. Missing memory files still produce a notice and success; workflows that
require a particular file should check its presence separately.

`$RUNNER_TEMP` is removed when the job ends. Upload the report to keep it:

```yaml
      - id: memfalsify
        uses: timothyzhbw-jpg/memory-lab@<sha>
      - if: always() && steps.memfalsify.outputs.report != ''
        uses: actions/upload-artifact@v4
        with:
          name: memfalsify-report
          path: ${{ steps.memfalsify.outputs.report }}
```

When a strict run fails on a candidate, one side is usually out of date: fix the instruction or the document.
If the passage is not an authoritative statement about the current repository, exclude its file. Use
`strict: "false"` for a report-only check.

## Exclusions

Each line of `exclude` is an `fnmatch` glob, matched against POSIX paths relative to the evidence directory.
`dir/**`, `dir/*`, `dir` and `dir/` all exclude everything under `dir`. In `fnmatch`, `*` also matches `/`,
so `*.lock` excludes lock files at any depth. Excluded files are not read, and the report header lists the
patterns.

Matching is case-sensitive on every platform: `Docs/**` does not exclude `docs/`. Patterns must use
root-relative forward-slash paths: `/docs/**` and `docs\**` match nothing. Check exclusions before using a
model verifier to send passages outside the runner. `**/api.md` matches nested files only; use both `api.md`
and `**/api.md` to cover the root and subdirectories. Glob metacharacters also apply in real directory names:
use `docs[[]1]/**` for a literal `docs[1]` directory and `[?]` for a literal question mark.
`*` and `**` exclude the entire tree. When patterns leave no indexed evidence and there are claims, the audit
fails with exit 2 rather than reporting a clean strict pass. An empty evidence directory without exclusion
patterns still yields NO EVIDENCE.

Exclude what is not a statement about the repository as it is now: test fixtures, example and benchmark data,
vendored code, generated files, changelogs and archived notes (historical statements contradict current ones
by design).

Always left out without configuration: hidden files and directories (`.git/`, `.github/`, ...),
`node_modules/`, `__pycache__/`, `venv/` and earlier MemFalsify reports, each listed as skipped with a reason,
and, silently, the instruction files being audited. So Cursor, Copilot, Windsurf and Claude Code rule files
under `.cursor/`, `.github/`, `.windsurf/`, `.devin/` and `.claude/` are never evidence, while `CLAUDE.md`,
`AGENTS.md` and `GEMINI.md` outside hidden directories are. Hidden/skipped components in resolved symlink
targets are rejected too. Skipped memory inputs also appear in the report's skip list, with reasons prefixed
by `memory skipped`.
Media files, files over 2 MB, non-UTF-8 files, broken or cyclic file symlinks and untraversed directory symlinks
are listed under "Skipped evidence". File symlinks escaping the evidence root are also skipped; inaccessible
files or directory traversal errors fail the audit. When both `CLAUDE.md` and `AGENTS.md` are audited as distinct
files inside the evidence root, each is evidence for the other, even if one links the other with Markdown.

## Model verifiers in CI

| `verifier` | Backend | Secret |
|---|---|---|
| `llm` | Claude | `ANTHROPIC_API_KEY` |
| `jev` | TypeSafe AI's Jev judgment model | `TYPESAFE_API_KEY` |
| `dual` | both; disagreements become DISPUTED | both |
| `cascade` | Jev first, unsure answers escalated to Claude | both |

The action installs the matching extra (`[llm]`, `[jev]` or `[all]`). Store keys as repository or organization
secrets and pass them on the step. Never commit a key to the repository.

```yaml
      - uses: timothyzhbw-jpg/memory-lab@<sha>
        env:
          TYPESAFE_API_KEY: ${{ secrets.TYPESAFE_API_KEY }}
        with:
          verifier: jev
```

- **Data leaves the runner.** Instruction text, and the evidence passages pre-selected for each instruction
  (up to 8, by term overlap), are sent to Anthropic or TypeSafe AI. Use `exclude` to keep sensitive
  directories out, or stay with `lexical` where repository content must not leave your infrastructure.
- **Forks get no secrets.** On `pull_request` from a fork, model verifiers fail with exit 2. Run them on `push`
  and same-repository pull requests, for example with
  `if: github.event_name == 'push' || github.event.pull_request.head.repo.full_name == github.repository`,
  and keep `lexical` for the rest. Do not use `pull_request_target` with a checkout of the pull request's
  code to get secrets: that runs untrusted code with your keys.
- **Cost and variance.** Up to 8 passages are judged per instruction on every run (each judged by both models
  with `dual`, and by both when Jev is unsure with `cascade`), and model verdicts can change between runs. Consider `strict: "false"` or a scheduled workflow for model verifiers, and a strict `lexical`
  job as the gate. Up to 4 model requests run at once (the CLI default), and the report in the job summary
  has an API usage line per backend with its requests and tokens. The action does not use the CLI's
  `--batch`, which can wait up to 24 hours.

## Security notes

Inputs reach the scripts only through environment variables, never through `${{ }}` interpolation inside a
`run:` script, so an input cannot inject shell commands. memfalsify runs with `python -I`, which keeps the
audited repository's files off the import path. The packages are built from a copy under `$RUNNER_TEMP`, so
running the action from a checkout (`uses: ./`) leaves no build artifacts in the audited workspace.
Memory-derived progress text is escaped, and CLI diagnostics are prefixed line by line so repository paths
and error messages cannot become runner workflow commands. Report contents are written directly to files,
not interpreted as log commands.
