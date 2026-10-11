# MemFalsify GitHub Action

The action audits agent instruction files (`CLAUDE.md`, `AGENTS.md`) against the repository they describe,
on every push or pull request. Each instruction is checked against the repository's own docs, configs and code
comments; the evidence-cited report goes to the job summary. The action is read-only: it never edits an
instruction file or anything else in the workspace.

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
the audit of the added and changed ones; the `attention` output counts only those.

## Inputs

| Input | Default | Meaning |
|---|---|---|
| `memory` | `CLAUDE.md AGENTS.md` | Files to audit, separated by spaces or newlines. Quote a path that contains spaces, shell-style: `CLAUDE.md "docs/agent notes.md"`. Missing files are skipped; if none exists, the action emits a notice and succeeds. |
| `evidence` | `.` | Evidence directory, relative to the workspace. |
| `exclude` | none | Globs to leave out of the evidence, one per line (see Exclusions). |
| `strict` | `"true"` | Fail when any instruction is challenged, conflicted, disputed or has possible counterevidence. |
| `verifier` | `lexical` | `lexical`, `llm`, `jev`, `dual` or `cascade` (see Model verifiers). |
| `diff-base` | none | Revision to diff against, for example `${{ github.event.pull_request.base.sha }}`. When set, only claims added or changed since then are audited and can fail the step (see above). Needs that commit in the checkout. |
| `as-of` | today | Reference date `YYYY-MM-DD` for the staleness check. |
| `python-version` | `3.12` | Python for an isolated install under `$RUNNER_TEMP`. The job's own Python and `PATH` are not changed. |

Only the files listed in `memory` are audited: Markdown links are not followed. To audit another instruction
file, list it explicitly. Linked repository documents stay available as evidence; adding a link does not
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

Always skipped with a reason: hidden files and directories (`.git/`, `.github/`, ...), `node_modules/`,
`__pycache__/`, `venv/`, and earlier MemFalsify reports. The instruction files actually read are silently
excluded from evidence. Hidden/skipped components in resolved symlink targets are rejected too. Skipped
memory inputs also appear in the report's skip list, with reasons prefixed by `memory skipped`.
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
  job as the gate.

## Security notes

Inputs reach the scripts only through environment variables, never through `${{ }}` interpolation inside a
`run:` script, so an input cannot inject shell commands. memfalsify runs with `python -I`, which keeps the
audited repository's files off the import path. The packages are built from a copy under `$RUNNER_TEMP`, so
running the action from a checkout (`uses: ./`) leaves no build artifacts in the audited workspace.
Memory-derived progress text is escaped, and CLI diagnostics are prefixed line by line so repository paths
and error messages cannot become runner workflow commands. Report contents are written directly to files,
not interpreted as log commands.
