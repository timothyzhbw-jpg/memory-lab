# What the lexical verifier flags

`lexical` is the default verifier: offline, deterministic, no model. It reads one claim and one passage at a time and compares words. This page states its rules in full, so that you can tell what a flag means and when not to expect one. A flag means "read these two quotes". Only a person's review marks a claim challenged or supported.

## What counts as a claim

A memory file is read as Markdown, one block at a time, and each block is cut into sentences.

- The lines of a paragraph or list item are read as one, and the claim's location is the line it starts on. A wrapped line goes on with the next when the line above stops mid-sentence: it is indented under its list item, ends in a comma or a small word, or is long enough to have been wrapped. Short lines with no marker and no period (`Project: acme` over `Language: Python`) stay on their own, and so does a line with a hard break.
- A period does not end a sentence after `e.g.`, `i.e.`, `vs.`, `cf.` and the like, after `etc.` before more of the sentence, or in `no. 5`. The same goes for evidence passages.
- A table row is one statement. A table's header and rule are not claims.
- These are not claims: code blocks, comments, headings, lines of nothing but HTML tags, link definitions, and a label ending in a colon, which is three words or fewer (`Run ty:`), or a clause with no main part (`When writing comments:`, `For a full build from the repository root:`). A short piece in lower case between two semicolons, with no number and no verb from a short list (`time-based assertions;`), is not one either.
- A sentence that links to a local `.md` file points at claims and is not one itself. A block that opens with such a link is an index entry, along with all of its text.

The rules err towards keeping. Chinese text always stays, and so does anything that reads as a sentence, such as `Tests use pytest.` A bare fact in a list (`pnpm workspaces`) stays too. A claim needs two content words, as it always did, so `Python 3.12` alone is not one.

A claim's ID comes from its text, so a re-wrapped paragraph keeps its IDs, while a fix to how a line is read gives it a new one. A review naming an old ID then stops the audit with `unknown memory_id`, or, for a dismissal, is listed under `Skipped evidence`.

A heading above a list does not change how the items are read: `Use npm in this package.` under `## Do NOT` is compared as it is written, and the heading only shows next to it in the report. On the repositories measured, about 3% of the claims sit under such a heading or lead-in, and in such a list the first sentence of an item is negated while the next one is often a plain statement (`Use an optional group.`), so reading the whole list as negated would misread those.

## When it says a passage may contradict a claim

The rules run in this order, and the first that fires gives the report's reason.

| Reason in the report | Rule |
|---|---|
| `same setting max_steps: claim 100 vs source 500` | Both texts state the same key with different values. |
| `set differently timeout: claim 30 vs source 60` | The same, when a side merely sets the key (`key=30`, `key = 30`, `--key 30`). |
| `opposing terms: claim 'approv…' vs source 'reject…'` | Two texts about the same subject use opposing words, outside names and code spans. |
| `different units: claim 2 minutes vs source 2 seconds` | Two texts about the same subject give the same number a different unit of time or size. |
| `different values: claim ['100'] vs source ['60']` | Two nearly identical texts attach different numbers, or different dates, to the same word. |
| `opposite modality: claim '所有' vs source '仅'` | Chinese text only: the two sides stand at opposite ends of all/only or must/need not. |
| `same subject, but only one side is negated` | One side negates a word the other side states plainly, in two statements about the same subject. |
| `source says what the claim names was removed` | Every term of the claim, at least four, stands before "was removed" (or deleted, dropped, retired) in the source. |

A passage also has to share most of the claim's terms to be looked at (half of them to be considered, 60% to count as related). The exception is a passage that states a key the claim binds: it is compared however little else it shares.

### Named values

- A key looks like an identifier: `snake_case`, `kebab-case`, `camelCase`, `dotted.name`, a `--flag`, or a name in backticks. Keys match exactly or ignoring case. `Note: 3 things` binds nothing.
- These forms bind a key: `key: 100`, `key = 100`, `key=100`, `key (default: 100)`, `key: int = 180`, `key: float = Field(default=0.1, ...)`, `key defaults to 100`, `` `key` is 100 ``, `"key": 5`, `| key | 100 |`, `--flag 100`.
- Values are numbers, booleans and short quoted strings. `None` and `null` mean no value here. `100`, `100.0` and `1_000` compare as numbers. `500ms` and `1s` are left alone, since their units are not converted.
- A value set somewhere (`key=100`, `key = 100`, `--flag 100`) is not compared with a stated default (`key (default: 3)`, `key: int = 3`). Passing 100 in one call says nothing about the default. Against anything else a value that is merely set counts for less: that reason ranks low, needs a file that is no test, example, changelog or source code, and the passage has to count as related.

### Same subject

Two texts are about the same subject when each covers enough of the other's content terms: 60% for a negation, on at least three shared terms; 50% for numbers and units and 40% for opposing terms, on at least two. The bars differ because a `not` says little by itself, a number next to the same word says more, and an opposing word more still. Sentences are compared whole and clause by clause, so `A, and only if B` can match on `A`.

For a negation, a cue only reaches the next three terms of its clause, and a name such as `--no-cache` or `not_found` is no cue. Both sides negating the same word is agreement. `Do not ...`, `Never ...` and `Skip ...` at the start of a line are instructions, not statements about how things are, so they contradict nothing. A statement with `always`, `every` or `never` in the middle is contradicted by one counterexample, so there the source only has to cover the claim.

Opposing words are states, such as approve/reject, enable/disable, allow/block, include/exclude, public/private, required/optional, supported/deprecated, pass/fail, true/false and increase/decrease. Add/remove, start/stop and open/close are not on the list, since they happen in turn.

### Numbers that do not count

Issue and pull request references (`#7326`), digits inside names, paths and versions (`40_setup.js`, `rfc-0723`, `my-sdk-v2.0.6`), times and ports (`14:21`, `localhost:5420`), an operand such as `lines.length + 1`, and any number bound to a key, which the named-value rule compares instead. `0723` and `723` are one value. A date is only compared with a date.

### Chinese text

Text with CJK characters keeps its own per-clause negation, opposing terms and modality. There is no labeled data from real Chinese repositories to tune on, so those rules did not become stricter. The number and unit rules do apply.

## What kind of file the passage is in

| File | What it can contradict with |
|---|---|
| Tests (`tests/`, `test_*.py`, `*.test.ts`, ...) | Nothing. A test shows one case. |
| Changelogs (`CHANGELOG*`, `changelog/`, `Releases.md`, ...) | A stated value (`same setting`), and saying something was removed. A changelog records the past. |
| Examples (`examples/`, `demos/`, `examples.md`, ...) | A stated value (`same setting`). |
| Source code (`.py`, `.ts`, `.rs`, `.go`, ...) | Declared values only (`x: int = 5`, `X = 5`). `x = 5` is one assignment among others, and words and other numbers in code are mostly control flow and error messages. |
| Everything else | All the rules. |

All of these can still support a claim. This applies to the lexical verifier only: a model verifier reads the same passages and judges them itself.

## What a directory scan leaves out

The scan does not read these as evidence, and lists them under *Skipped evidence* in the report:

- Directories named `vendor`, `third_party`, `thirdparty`, `testdata`, `fixtures` or `__snapshots__`, once each, without their contents.
- `*.min.js`, `*.min.css`, `*.snap`, `*.svg`, `*.patch`, `*.lock`, `package-lock.json`, `pnpm-lock.yaml`, `npm-shrinkwrap.json`, `go.sum`.
- Lines over 1,000 characters: minified code, embedded data, and also a long paragraph kept on one line. The rest of the file is read. Wrap the paragraph to bring it back.

This applies to every verifier and to everything that scans: `memfalsify audit` with or without a file name, the GitHub Action, the web page and `memlab-mcp`. The paths are relative to the evidence root, so pointing `--evidence` at such a directory reads it. A review can quote any file in the evidence root.

## The order of a report

Within a status, findings are ordered by their strongest reason: a named value, then units, numbers, removal, values merely set, opposing terms, modality and negation, and in file order after that. Within a finding, contradictions come first in the same order, then the passages that share the most terms. The same input gives the same order.

## How much to trust a flag

On the agent files of 15 public repositories, these rules took the flagged pairs from 1,371 to 50 and the flagged claims from 645 to 31, and 9 of the 15 labeled pairs still flagged are real (0.35 once scaled for how negation was sampled). A `same subject, but only one side is negated` finding is the weakest: none of those read as real there. The [real-repository benchmark](../benchmarks/memfalsify/wild/README.md) has the numbers and how they can mislead, and [`benchmarks/memfalsify/RESULTS.md`](../benchmarks/memfalsify/RESULTS.md) has the synthetic cases.

Flags are rare by design now, so a clean report says little. The verifier still misses:

- Paraphrases, and anything stated in other words. "Requires Python 3.9" and `requires-python = ">=3.10"` share no key.
- A list of removed items under a heading that says so: each line is read on its own.
- Most negation in code and in instructions. The rule that read every `not` as a contradiction was wrong on all 260 pairs sampled from real repositories, so it now fires rarely.

For those, use a [model verifier](local-setup.md).
