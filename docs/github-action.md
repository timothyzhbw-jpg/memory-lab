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
      - uses: actions/checkout@11d5960a326750d5838078e36cf38b85af677262  # v4.4.0
        with:
          persist-credentials: false
      - uses: timothyzhbw-jpg/memory-lab@<40-character SHA>  # main, <date>
        with:
          exclude: |
            vendor/**
            CHANGELOG.md
```

Pin the action to a full commit SHA rather than a branch such as `main`, with a comment saying which version it is, for
example `uses: timothyzhbw-jpg/memory-lab@<40-character SHA>  # main, 2026-10-11`. The action runs inside your
job, and a branch can move under you; update the SHA when you choose to. Pin the other actions in your workflow
the same way, as the examples here do: a tag such as `@v4` is a pointer its owner can move to other code. The
action pins the actions it uses and installs its Python dependencies from a hash-checked lock file, so pinning
the action pins everything it runs (see "Dependencies and pinning").

This repository audits its own `AGENTS.md` in [`.github/workflows/ci.yml`](../.github/workflows/ci.yml)
(`uses: ./`; see "Why the dogfood job is safe").

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

**Hard links are not detected.** A hard link is a second name for the same file, so MemFalsify cannot tell it
from the file it was made from. If a file on the same filesystem is hard-linked into the evidence directory,
its bytes are read, hashed and can be quoted in the report, wherever its other name lives (a key outside the
checkout, for example). The audit does not skip every file with more than one link, because that would also
skip legitimate ones. Git does not store hard links, so a checkout never creates one and a pull request cannot
plant it. Someone, or an earlier step of your own job, with write access to the audited directory on that
machine can. Audit only directories you control, and where others can write, look first with
`find . -type f -links +1`.

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
      - uses: actions/checkout@11d5960a326750d5838078e36cf38b85af677262  # v4.4.0
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
| `verifier` | `lexical` | `lexical`, `llm`, `jev`, `dual`, `cascade` or `openai` (see Model verifiers). |
| `openai-model` | none | `verifier: openai`: the model, as the provider names it. Required with that verifier; there is no default. |
| `provider` | `openai` | `verifier: openai`: preset base URL and key variable: `openai`, `grok`, `deepseek`, `gemini` or `ollama`. |
| `base-url` | the provider's | `verifier: openai`: another OpenAI-compatible base URL; `https://`, or `http://` only for `localhost`, `127.0.0.1` or `::1`. |
| `api-key-env` | the provider's | `verifier: openai`: name of the environment variable that holds the key. Never the key itself. |
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
  an unknown verifier, a model verifier without credentials, an `openai` verifier without `openai-model` or
  with an unknown `provider`, a refused `base-url` or an `api-key-env` that is not a variable name, unreadable
  evidence, exclusion patterns that leave zero indexed passages while there are claims to audit, or a
  `diff-base` that is not a commit in the checkout.

A zero `attention` count means no contradiction was flagged. It does not guarantee that memory files exist
or that their claims have support. Missing memory files still produce a notice and success; workflows that
require a particular file should check its presence separately.

`$RUNNER_TEMP` is removed when the job ends. Upload the report to keep it:

```yaml
      - id: memfalsify
        uses: timothyzhbw-jpg/memory-lab@<sha>
      - if: always() && steps.memfalsify.outputs.report != ''
        uses: actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02  # v4.6.2
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
| `openai` | any OpenAI-compatible Chat Completions API (`openai-model`, `provider`, `base-url`) | the `provider`'s variable (`OPENAI_API_KEY`, `XAI_API_KEY`, `DEEPSEEK_API_KEY`, `GEMINI_API_KEY`) or the one `api-key-env` names |

The action installs the matching extra (`[llm]`, `[jev]`, `[openai]`, or Claude and Jev for `dual` and
`cascade`). Store keys as repository or organization secrets and pass them on the step. Never commit a key to
the repository.

```yaml
      - uses: timothyzhbw-jpg/memory-lab@<sha>
        env:
          TYPESAFE_API_KEY: ${{ secrets.TYPESAFE_API_KEY }}
        with:
          verifier: jev
```

With `verifier: openai`, name the model and, for a provider other than OpenAI, the preset. The key reaches the
step as an environment variable; `api-key-env` names a different variable when yours is not the provider's
default:

```yaml
      - uses: timothyzhbw-jpg/memory-lab@<sha>
        env:
          DEEPSEEK_API_KEY: ${{ secrets.DEEPSEEK_API_KEY }}
        with:
          verifier: openai
          provider: deepseek
          openai-model: <model>
```

The inputs reach the run step through `env:` and are checked before anything is audited: an unknown
`provider`, a `base-url` that is not `https://` (or `http://` to a loopback host) or carries a user name,
password, query or fragment, or an `api-key-env` that is not a variable name fails the step with exit 2, and
the message never repeats the URL or name. The key is never printed: provider error messages are logged with
it redacted, and the OpenAI SDK's own `OPENAI_*` variables are ignored. `provider: ollama` needs an Ollama
server reachable at `http://localhost:11434` inside the job. The verifier asks for the verdict as a strict
JSON schema, falls back to a JSON object and then plain text when the endpoint refuses a format, and counts
an answer that is not a valid verdict as no verdict; the action does not support `--batch` for it.

- **Data leaves the runner.** Instruction text, and the evidence passages pre-selected for each instruction
  (up to 8, by term overlap), are sent to Anthropic, TypeSafe AI or, with `openai`, the configured
  endpoint. Use `exclude` to keep sensitive
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

## Dependencies and pinning

The action runs inside your job, so what it installs matters as much as its own code. It trusts exactly this:

- **Actions are pinned to commit SHAs.** `action.yml` uses `actions/setup-python` at a full 40-character
  commit with a `# vX.Y.Z` comment, and so does this repository's CI. Pin the action itself to a commit too.
- **Python dependencies come from a lock file.** The install step picks one file from
  [`action/requirements/`](../action/requirements) by `verifier`: `lexical.txt` (pydantic, typer),
  `llm.txt` (adds `anthropic`), `jev.txt` (adds `typesafe-sdk`), `openai.txt` (adds `openai`), and `all.txt`
  (`anthropic` and `typesafe-sdk`) for `dual` and `cascade`. Each
  pins every package, including transitive ones, to an exact version with the SHA-256 of every release file,
  and each includes `setuptools`, the build backend of the action's own packages. The step runs
  `pip install --require-hashes --only-binary :all: -r <file>`: pip refuses a package that is unpinned,
  unlisted or whose bytes differ from the hash, and never builds a source distribution (which would fetch its
  build requirements unpinned).
- **The action's own packages come from the action path.** `memlab-core` and `memfalsify` are copied from the
  checked-out action, so they are the code at the pinned commit, and installed with
  `--no-deps --no-build-isolation`: nothing is resolved, and the build uses the locked `setuptools`. A final
  `pip check` fails the step if the lock no longer satisfies the packages' declared requirements.

Still trusted: the hashes only say that PyPI serves the bytes someone locked, so review the lock file's diff
whenever it changes; the Python that `actions/setup-python` installs (and the `pip` bundled with it); and
GitHub itself.

Each lock file is universal: one file covers Linux, macOS and Windows on every Python from 3.10 (the packages'
`requires-python`) upward, using environment markers such as `exceptiongroup ; python_full_version < '3.11'`.
The `python-version` input accepts any such version.

### Regenerating the lock files

The `.in` files next to the `.txt` files list the direct requirements; keep them in step with the dependencies
in `packages/memcore/pyproject.toml` and `packages/memfalsify/pyproject.toml` (a test compares them). Then,
with [uv](https://docs.astral.sh/uv/) installed and network access to PyPI:

```bash
scripts/update-action-lock.sh             # re-lock, keeping the versions already pinned
scripts/update-action-lock.sh --upgrade   # re-resolve everything to the newest releases
```

The script runs `uv pip compile --universal --python-version 3.10 --generate-hashes` once per set. Review the
diff, run `python -m pytest tests/test_action.py`, and commit the `.in` and `.txt` files together. Do this when a
package's dependencies change, and regularly to pick up security fixes.

## Why the dogfood job is safe

`uses: ./` runs the action from the checked-out ref, so on a pull request the `dogfood` job runs the pull
request's own `action.yml`, lock files and package sources. Unlike a pinned `uses: owner/repo@<sha>`, the code
is not fixed in advance. That is acceptable here only because the job has nothing for that code to take:

- the token is read-only (`permissions: contents: read` on the workflow and on the job) and is not stored in
  `.git/config` (`persist-credentials: false`);
- `ci.yml` contains no `secrets.` reference, and the job uses the offline `lexical` verifier, so there is no
  provider key to read;
- the trigger is `pull_request`, never `pull_request_target`: for a pull request from a fork GitHub withholds
  secrets and gives the job a read-only token whatever the workflow says;
- the `test` job in the same workflow already runs the pull request's code (pytest) under the same token, so
  the dogfood job opens no path that did not exist.

It would stop being safe if someone gave this job a secret (for example a model verifier key), a write
permission, or a `pull_request_target` trigger: a pull request could then edit `action.yml` or a lock file to
use it. `tests/test_action.py` fails the build if `ci.yml` does any of those. To dogfood a model verifier, run it
on `push` to the default branch only, or use `uses: timothyzhbw-jpg/memory-lab@<sha>` of a reviewed commit.
Another repository should not copy `uses: ./`: it works here because the repository is the action.

## Security notes

Inputs reach the scripts only through environment variables, never through `${{ }}` interpolation inside a
`run:` script, so an input cannot inject shell commands. memfalsify runs with `python -I`, which keeps the
audited repository's files off the import path. The packages are built from a copy under `$RUNNER_TEMP`, so
running the action from a checkout (`uses: ./`) leaves no build artifacts in the audited workspace.
Memory-derived progress text is escaped, and CLI diagnostics are prefixed line by line so repository paths
and error messages cannot become runner workflow commands. Report contents are written directly to files,
not interpreted as log commands.
