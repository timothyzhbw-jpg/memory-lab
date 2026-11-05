# Quick start

English | [中文](quickstart.zh-CN.md)

From nothing to a first MemFalsify report takes one install step and one command that makes a demo and audits it. The clone and `pip install` need the network; everything after them runs offline, with no API key. The [tutorial](tutorial.md) goes further: `diff`, `history`, `rollback` and the other tool in this repository.

## 1. Install

You need Python 3.10 or newer and git. From a checkout of this repository:

```bash
git clone https://github.com/timothyzhbw-jpg/memory-lab.git && cd memory-lab
python3 -m venv .venv && . .venv/bin/activate
pip install -e packages/memcore -e packages/memfalsify
```

## 2. Audit the demo

```bash
# exit 0
memfalsify init demo && cd demo && memfalsify audit
```

`init` writes a small project into a new directory: a `MEMORY.md` with three claims, `evidence/` with two files, and an empty `reviews.jsonl`. It refuses, with exit code 2 and nothing written, if any of those files already exists. `--lang zh` writes the same demo in Chinese.

`audit` with no arguments takes the memory files in the current directory and compares them with everything else in it. The report shows three outcomes:

| The report says | The claim | Why |
|---|---|---|
| `POSSIBLE COUNTEREVIDENCE` | The public API allows 100 requests per minute. | `evidence/api.md:3` says 60. Both quotes are shown, with the file's SHA-256. |
| `POSSIBLE SUPPORT` | Database backups run nightly at 02:00 UTC. | `evidence/runbook.md:3` says the same thing. |
| `NO EVIDENCE` | The design team prefers short release notes. | Nothing in the folder mentions it. That means unchecked, not wrong. |

## Read the report

- The flags come from comparing words, so a flag means "read these two quotes", not "this is wrong". The comparison can miss a paraphrase and misread context. That is what `Verifier: lexical` in the header means; [model verifiers](local-setup.md) are optional.
- `m-c1b4957f` is the claim's ID, taken from its text. Editing a claim gives it a new ID, so an old review stops applying.
- `Proposed actions` are suggestions. MemFalsify writes reports and proposals only; your memory files stay as they are.
- The exit code is 0 whatever the report says, until you pass `--strict`: then it is 1 when a claim needs attention. It is 2 when the input cannot be used.

Only a person can mark a claim supported or challenged, by recording a review. Add this line to `reviews.jsonl`:

```bash
# exit 0
printf '%s\n' '{"memory_id": "m-c1b4957f", "relation": "contradicts", "path": "evidence/api.md", "quote": "The public API allows 60 requests per minute.", "reviewer": "you"}' >> reviews.jsonl
memfalsify audit --reviews reviews.jsonl
```

The claim is now `CHALLENGED`, confirmed by you. `memfalsify init` prints the same line, and `memfalsify claims MEMORY.md` lists the IDs. With no file named, a file of review records stays out of the evidence. It applies only when you pass `--reviews`, and the command tells you when it sees one at the top of the evidence that you did not pass.

## Run it in your own repository

```bash
cd your-repository
memfalsify audit
```

The files come from `memfalsify discover` (`CLAUDE.md`, `AGENTS.md` and `GEMINI.md` at any depth; Cursor, Copilot, Windsurf and Devin rules where those agents look for them), plus a `MEMORY.md` at the top of the directory. A line on stderr says which ones were chosen. If there are none, the command exits with code 2 and suggests `memfalsify init`.

The evidence is the current directory, or `--evidence DIR`. The rules are the ones the web page uses:

- Hidden files and folders, `node_modules/`, `__pycache__/` and `venv/` are not read. Files over 2 MB are not read either, and neither are files that are not UTF-8 text.
- Media files count only through a transcript or OCR sidecar.
- MemFalsify's and orgmemory's reports, review records and decision logs are left out.
- Each file's evidence is everything except that file and the files it links to. Your other agent files do count, which is how two files that disagree show up.

The report lists what it skipped. `--exclude GLOB` (repeatable) leaves files out of both the choice of memory files and the evidence: `memfalsify audit --exclude 'tests/fixtures/**' --exclude 'vendor/**'`.

Several files share one report, in path order: totals, a table of the files, then a section per file. A claim stated in two files appears once, with the other place listed as a duplicate. With one file, the Markdown and HTML reports have the same layout as `memfalsify audit FILE --evidence .`, unless the search passed over a place, which then gets a section of its own. With `--format json` the shape does not depend on the count: one object with `files`, `summary` and `reports`, a list of the same objects `memfalsify audit FILE --format json` prints. `--strict` means what it always means: exit 1 if any claim in any of the files needs attention.

Naming a file, as in `memfalsify audit CLAUDE.md`, works as it always has: only that file, no evidence unless you pass `--evidence`, and only the skips of the evidence scan, so a review file or decision log inside `--evidence` reads as evidence there.

## Paths that exist nowhere

Besides comparing words, `audit` looks up the paths a memory names. An agent file that points to `src/old/module.py` when that file is gone sends the agent the wrong way, and that can be checked exactly and offline. The claim then gets one more issue:

```text
- missing_reference: 'src/old/module.py' is named here, but nothing under the evidence root matches it
```

The check runs with `memfalsify audit` and no file named, and with `memfalsify audit FILE --evidence DIR` when the file sits under `DIR`. With no evidence root it does not run, and the report says nothing about paths. `memfalsify diff` and the pre-commit hooks do not run it.

How to read it:

- The claim keeps its status. Missing evidence and a missing path are different things, so the status table does not count this issue. A line on stderr gives the count.
- `--strict` exits 1 on it, and the GitHub Action counts it in `attention`: a claim that sends an agent to a file that is not there needs attention. `--no-references` turns the check off.
- Read the line before you change it. The path may have moved (search for its last part), or the mention may have gone stale. `Proposed actions` only suggests; your files stay as they are.

A path counts as named when it is in inline code, is the target of a link, or is a word in an inline command that has an extension or a trailing slash. It is reported only if all of this holds:

- It has at least two segments, and its first segment is a directory at the root or next to the file that names it. A path relative to something the check cannot see stays silent, and so does a stale path whose top directory is gone too.
- It matches nothing under the root, read as a path from any directory there and with any extension. Short paths in a monorepo and import paths pass. So do files the evidence scan skips (binary, over 2 MB, hidden like `.github/`) and files `--exclude` leaves out.
- Git does not ignore it, so build output stays silent. Git is asked with `git check-ignore --no-index`, with hooks and the file system monitor off. If git is missing or fails, a short list of names (`build`, `dist`, `target` and others) stands in.
- Nothing on its way is a link to a directory or an empty directory (a submodule that was not fetched), and it stays inside the root. A path that leaves the root is not judged, and no file outside is opened: the check only compares names from a listing.
- It does not read as something else: a placeholder (`nodes/YourService/`, `src/foo.py`, `docs/NNN-title.md`), a remote name (`actions/checkout`), a MIME type, a branch name, or an API route. Its line does not say it is gone, yet to be made or someone else's (`Prettier's src/main/core.js`).

So a missing path is a strong sign, not proof: a file another tool writes under an unignored name still gets flagged. Fenced code blocks, plain words outside inline code and lines that hold only `.md` links are not read for paths.

## Use git history as evidence

Commit messages often record what was decided and why. Starting from the demo directory, this turns them into evidence files:

```bash
# exit 0
cd .. && git init -q history && cd history
git -c user.name=you -c user.email=you@example.com commit -q --allow-empty -m "Lower the public API limit to 60 requests per minute"
memfalsify evidence from-git --since 2026-01-01 --output commits --max-commits 100
cd ../demo
memfalsify audit --evidence ../history/commits
```

The last two lines go back to the demo, because an audit runs from the directory that holds the memory files. The report now cites the commit message as possible counterevidence to the 100 requests claim. If `commits/` sits inside the project, a plain `memfalsify audit` reads it along with everything else.

`--since` takes a date (`2026-01-31` means midnight UTC, `2026-01-31T12:00:00+02:00`, `'30 days ago'`), matched against the committer date, or a commit, tag or branch, meaning the commits after it up to `HEAD`. A value that looks like a date is read as a date, and a value that is neither a date nor a commit is refused. A date is checked against every commit in the history, so a commit with an old date hides nothing behind it.

Each commit becomes one small Markdown file, named by its 12-digit hash (the full hash, if two selected commits share 12 digits). It starts with a fixed header, the short hash, the full hash and the author date, followed by the subject and body:

```text
# Commit 1a2b3c4d5e6f

- Full hash: 1a2b3c4d5e6f...
- Author date: 2026-11-04T10:00:00-06:00

Lower the public API limit to 60 requests per minute
```

The same commits give the same bytes, so a re-run adds only what is new. Dates given in full do not depend on the machine; `'30 days ago'` depends on when you run it. The command writes only inside `--output` and only reads the repository. It refuses `--output` inside the git directory, and it stops with nothing written if a file there holds different content.

A commit message is text from whoever wrote the commit, so it is a candidate source like any other. Control, bidi and zero-width characters in a message are stored as visible `\uXXXX` escapes, not raw and not removed (a joiner inside real text, as in Persian or an emoji sequence, stays):

- A quote and its hash describe the evidence file, so the quote in a report is exact either way, and it matches what you see when you open the file. A `reviews.jsonl` line is JSON, so a stored `\uXXXX` in a quote takes a doubled backslash there; `memfalsify audit --format json` shows the quote in that form, ready to copy.
- Printing the file cannot drive your terminal.
- Removing such characters would hide that they were there, and could turn the text into a word its author's bytes did not hold. The full hash leads to the original.

Bytes that are not UTF-8 become U+FFFD, and a message over 256 KB is cut, with a note saying so. A character newer than your Python's Unicode tables is also stored as an escape, so that one rare case can differ between Python versions.

A GitHub version, with pull request titles and bodies, is on the roadmap.

## Windows

Activate the virtual environment with `.venv\Scripts\Activate.ps1`. The other commands are the same. PowerShell 7 accepts `&&`; in Windows PowerShell 5, use `;`: `memfalsify init demo; cd demo; memfalsify audit`. To add the review line, paste it into `reviews.jsonl` with an editor. The `printf` command above is bash.
