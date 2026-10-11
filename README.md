# Memory Lab: Verified Memory Infrastructure for AI Agents

English | [中文](README.zh-CN.md)

Agent memory tools mostly store and retrieve. This project is about **keeping what an agent remembers true**: every memory should have evidence, a place in time, and a traceable origin, and information without authority should not be able to change the current state.

`Remember → Verify → Challenge → Resolve → Evolve`

It contains two independent tools that share one evidence core:

| | **MemFalsify** | **Verified Organizational Memory** (`orgmemory`) |
|---|---|---|
| Question | *Is this memory backed by evidence?* | *Is this the current, authorized state?* |
| Input | `MEMORY.md` + a directory of evidence files | Event log + role policy + evidence manifest |
| Output | Audit report: cited quotes, issues, proposed actions | Current state per subject, decision provenance, audit trail, decision-process audit |
| Writes | Nothing (only the report you ask for) | Nothing on replay or audit; `append` only appends |

By default everything runs offline with no API keys; optional model verifiers (Claude and TypeSafe AI's Jev) can be switched on. Python 3.10+, Apache-2.0.

```text
        Claude Code / Codex / other agents  (skills/)
                          │
          ┌───────────────┴───────────────┐
          ▼                               ▼
     MemFalsify                     orgmemory
  evidence support for          state + authority for
  remembered claims             organizational decisions
          └───────────────┬───────────────┘
                          ▼
           memcore: hashed, exact-quote evidence
```

## Quick start

```bash
git clone <this repo> memory-lab && cd memory-lab
python -m venv .venv && source .venv/bin/activate
pip install -e packages/memcore -e packages/memfalsify -e packages/orgmemory
```

Install only what you need: `memcore` plus either tool works on its own.

### MemFalsify: audit a MEMORY.md

```bash
cd examples/memfalsify
memfalsify audit MEMORY.md --evidence evidence --reviews reviews.jsonl --as-of 2026-10-10
```

The example memory holds ten claims about a fictional service. The audit ([full report](examples/memfalsify/report.md)) finds:

| Memory | Result | Why |
|---|---|---|
| Feature A (bulk export) is approved for the November release. | **challenged** | A reviewer confirmed the decision log: *"Product manager rejects Feature A…"*; also conflicts with another memory |
| The public API rate limit is 100 requests per minute. | possible counterevidence | `docs/api.md:3` says 60 |
| The main database runs PostgreSQL 15. | possible counterevidence | `docs/database.md:3` says 16 |
| Deployments always go through the staging cluster. | possible counterevidence, overgeneralization | An incident note records a hotfix that skipped staging |
| The bulk export will ship next week. | no evidence, relative time | "next week" relative to when? |
| 2026-03-02: Feature B was postponed to Q3. | no evidence, possibly stale | Over 180 days old |
| We probably use squash merges for pull requests. | possible support, inference marker | `CONTRIBUTING.md` agrees, but the memory is phrased as a guess |
| Bulk export depends on the billing schema migration. | **supported**, duplicate | Confirmed by a reviewer; stated in two files |

Every quote carries `path:line`, character offsets and the SHA-256 of the file it came from. A whiteboard photo in the evidence folder is listed as skipped, not guessed at.

Chinese memories work too: Chinese text is matched by overlapping character pairs (no dictionary segmentation), with antonym pairs such as 批准/拒绝, dates like 2026年3月2日, and wording such as 总是, 下周 and 可能. Chinese negation is matched per character (不, 没, 未, 无, 非, 别, 勿), so words like 未来 or 非常 can trigger a false "only one side is negated" candidate. See [`examples/memfalsify-zh`](examples/memfalsify-zh/) and the [Chinese README](README.zh-CN.md).

**How status is decided.** MemFalsify nominates evidence by itself, but only a human review can settle a memory:

| Status | Meaning |
|---|---|
| `supported` / `challenged` / `conflicted` | Human-confirmed evidence in `reviews.jsonl` supports / contradicts / does both |
| `candidate_counterevidence` | The verifier found a passage that may contradict the memory (lexically: opposing terms such as *approved* / *rejected*, different numbers, or negation) |
| `disputed` | Two verifiers disagree (see *Verifier backends*) |
| `candidate_support` | A passage that restates the memory |
| `insufficient_evidence` | Nothing found. That means unverified, not false |

Independent of evidence, each memory is checked for `overgeneralization` (always, all, never…), `relative_time` without a date, `possibly_stale` dates, `inference_marker` (probably, seems…), `internal_conflict` with another memory, and `duplicate`. Internal conflicts use an index of normalized terms, so only claims that can lexically conflict are compared. The report is the same as comparing every pair.

Proposed actions such as `verify_counterevidence`, `add_absolute_date` and `narrow_scope` are suggestions only. The `replacement` field is always empty: MemFalsify never writes facts.

Other commands and options: `memfalsify claims MEMORY.md` lists claim IDs; `--format json`, `--output report.md`, `--strict` (exit 1 when anything needs attention, for CI), `--stale-days`, and `--no-follow-links`.

#### Review memory changes in git

When `CLAUDE.md`, `AGENTS.md` or `MEMORY.md` is versioned, review what a commit or pull request changed and audit only that:

```bash
memfalsify diff CLAUDE.md --base main --evidence .        # vs. the working tree; add --head REV for two commits
memfalsify history CLAUDE.md --max-commits 50              # the commits that introduced and last changed each claim
memfalsify rollback CLAUDE.md --claim m-1a2b3c4d --to main -o restore.patch
git apply restore.patch                                    # only after reviewing it, from the repository root
```

- `diff` reads both versions of the memory file and the files it links to with git (nothing is checked out) and lists claims as added, changed (old → new), removed or unchanged. It audits only the added and changed ones, against the evidence directory as it is now, and takes the `audit` options (`--evidence`, `--exclude`, `--reviews`, `--verifier`, `--format`, `--output`, `--strict`, ...). With `--strict` it exits 1 when an added or changed claim needs attention; git problems and unknown revisions exit 2.
- A claim is identified by its ID, so case and whitespace edits count as unchanged. An edited claim is paired with its old version when both are in the same file and section (a heading that was renamed around unchanged claims counts as the same section) and their terms are at least 50% similar; otherwise the edit shows as removed plus added. Pairing is deterministic.
- `history` follows the first-parent line of `HEAD`, so a change merged with a merge commit is attributed to that commit; it also lists claims removed within the scanned commits, and uncommitted edits as "uncommitted changes in the working tree". Claims older than the scanned commits (`--max-commits`, default 100, or the edge of a shallow clone) are reported as such, not attributed to the oldest commit. A renamed memory file is not followed.
- `rollback` never applies anything: it prints a patch that restores the claim's wording as of `--to` (every copy of it, with the old markup), or re-adds a removed claim after the nearest surviving claim of its section. It accepts the current or the old claim ID. Use `-o` on Windows, where shell redirection can re-encode the patch.

### Organizational Memory: replay decisions

```bash
orgmemory replay examples/orgmemory/feature-a   # from the repository root
```

| When | Who (role) | Event |
|---|---|---|
| Mon | customer | requests Feature A |
| Tue | engineer | raises a technical risk |
| Wed | product manager | **rejects** Feature A |
| Thu | customer | requests Feature A again |
| Thu | account manager | posts "Feature A is approved" |

Result ([full report](examples/orgmemory/replay-report.md)):

```yaml
subject: Feature A
current_status: rejected
reconsideration_requested: true
pending_approval: [product_manager]
open_risks: ["Bulk export reads the billing schema, which is mid-migration"]
last_authorized_decision: ev-wed-reject (reject by zhou)
unauthorized_change_attempts: [ev-thu-sales-approve]
```

Thursday's newer messages do not overwrite Wednesday's decision. The repeated request becomes a reconsideration awaiting the product manager. The account manager's "approval" is logged but ignored, because the policy does not let that role approve.

Other commands:

```bash
orgmemory explain  <dataset> "Feature A"      # why it is in this state, with quoted evidence
orgmemory replay   <dataset> --as-of 2026-10-07T23:59:59Z   # state at a past time
orgmemory validate <dataset>                  # check every event, hash and quote
orgmemory audit    <dataset> --as-of 2026-10-31T00:00:00Z   # where the decision process needs attention
orgmemory evidence add <dataset> --id ... --path ... --quote "..." --source-uri ... --source-kind ... --captured-at ...
orgmemory append   <dataset> --event '{"id": ..., ...}'
```

#### Audit the decision process

`replay` says what the state is; `audit` says where the decision process needs attention. It replays the log under the same rules, read-only, and reports findings. Each finding has a `kind`, a `severity` (`high`, `medium` or `low`), the `subject` it is about (or, for a finding about one actor, the `actor`), the `subjects` and `event_ids` involved (event IDs in replay order), a `detail` line, and every cited event with its evidence quotes, as `explain` cites them. The audit adds no event semantics: it only restates what replay, its conflicts and its audit trail already contain.

```bash
orgmemory audit <dataset> [--as-of ISO8601] [--stale-days 14] [--format markdown|json] [--strict]
```

On the example as of 2026-10-31 ([full report](examples/orgmemory/audit-report.md)), the customer's reconsideration has waited more than 14 days for the product manager (`stale_reconsideration`, medium), and the account manager's ignored "approval" is an `unauthorized_attempts` finding (medium). The [GitHub example's audit](examples/orgmemory/github-acme-web/audit-report.md) lists stale requests awaiting a policy decision, ignored reviews, labels and merges per account, and a reviewer who requested changes and then approved. Pending approval reflects policy replay, not whether an issue or pull request is open on GitHub: for example, `acme/web#4` is closed as duplicate, a `comment` that leaves its request pending.

| Kind | Reported when | Severity |
|---|---|---|
| `simultaneous_decisions` | Replay recorded conflicting authorized decisions at the same effective time, so file order chose the outcome | high while the order-chosen decision is still current; medium once a later decision has replaced it |
| `decision_reversed` | Replay recorded an authorized `approve` after an authorized `reject`, or the reverse, with no `revoke` between | medium if a different actor reversed it; low if the same actor changed their own decision |
| `invalid_supersession` | Replay recorded an authorized `revoke` that named a decision other than the current one, so it changed nothing. The finding also cites the named event and the decision in force at the time | medium |
| `risk_after_approval` | The subject is approved and has a risk on record; one finding per risk | high if the risk was raised after the approval; medium if before it, because the log has no event that resolves a risk |
| `unresolved_dependency` | The subject is approved but depends on a subject that is not approved; one finding per dependency, checked one level deep | high if the dependency is rejected; medium if it is proposed; low if it has no events in the log |
| `unauthorized_attempts` | One finding per actor: the events the policy ignored, with a count per action and the subjects. `comment` is not counted, because it never changes state | high for 2 or more ignored `approve` / `reject` / `revoke`; medium for 1, or for 2 or more other ignored events; low for a single other ignored event |
| `stale_pending_approval` | `pending_approval` has been open for more than `--stale-days` (default 14) at the reference time, and no reconsideration is open. The wait starts at the first `request`, `reconsider` or `revoke` after the last `approve` or `reject` | medium |
| `stale_reconsideration` | `reconsideration_requested` has been open for more than `--stale-days`. The subject is then not also reported as `stale_pending_approval` | medium |
| `self_approval` | The actor of the current approval also made an applied `request` or `reconsider` on the subject before approving it | low |

- Only what replay applied counts: a risk or dependency from an actor without the permission is not on record, and appears only under `unauthorized_attempts`.
- `--as-of` drops later events, as in `replay`, and is also the reference time for staleness. Without it the whole log is audited and staleness is measured at the current time, so pass `--as-of` for a reproducible report.
- Findings are sorted by severity, kind, subject, actor and replay order. The Markdown report starts with a summary table: one row per kind, one column per severity. `--format json` has `summary` (findings per severity), `kinds` (every kind, sorted by name) and `findings`.
- Exit codes: `0`; `1` with `--strict` when any finding is high; `2` on invalid input, as for `replay`. The output is printed in every case except `2`.

#### Ingest GitHub issues and pull requests

`orgmemory github` turns one repository's issues and pull requests into events, evidence and source captures in a dataset. Each issue or PR is one subject (`acme/web#12`). The import is deterministic and uses no model.

```bash
orgmemory github sync    <dataset> OWNER/REPO [--full | --since ISO8601] [--issue N]... [--max-wait 3600] [--save-capture f.json] [--dry-run]
orgmemory github convert <dataset> f.json [--dry-run]  # apply a saved capture offline
orgmemory explain        <dataset> "acme/web#12"
```

- The token is the first non-empty `GITHUB_TOKEN` or `GH_TOKEN` (`GH_TOKEN=$(gh auth token)` works); there is no `--token` flag. A fine-grained, read-only token with Issues, Pull requests and Metadata read access is enough. Without a token the run is unauthenticated: public repositories only, 60 requests per hour.
- The first run reads everything; `--since ISO-TIME` (with timezone) can bound it, and is refused once the repository has a cursor. Later runs read what changed since the last complete sync, with a 5-minute overlap. `--full` rescans everything and `--issue N` (repeatable) refreshes chosen numbers; it cannot be combined with `--full` or `--since`. Rerunning is safe: IDs are deterministic, so nothing already in the log is appended twice. GitHub may not mark an issue as updated for every change (labels, dependency links), so run `--full` now and then.
- `sync` reuses a verified HTTPS connection to `api.github.com` (system CA store, 30-second timeout per request), and closes it when the run ends, including on errors. If the connection fails before any response bytes arrive, the GET is retried once on a fresh connection; rate limits and other retries keep their existing behavior. Setting `HTTPS_PROXY` / `https_proxy` or `ALL_PROXY` / `all_proxy` selects the existing urllib transport instead, with urllib’s proxy rules and no connection reuse. Transports never follow redirects automatically; the client permits only validated same-origin redirects.
- During `sync`, fetch progress goes to stderr at most once every 5 seconds, showing fully fetched issues/PRs, requests and the remaining rate-limit quota (`unknown` if unavailable). Each rate-limit sleep also prints its duration and UTC wake time. Short runs may show no progress; stdout stays unchanged, including the summary and `--dry-run` JSONL.
- `--dry-run` fetches (this spends requests), prints the summary and the planned events, and writes no dataset files or lock. `--save-capture` writes the fetched issues and timelines, as the API returned them, to a new file that `convert` can apply later; it works with `--dry-run`, so you can review a capture before applying it. The file contains raw and possibly private text. The destination is create-only and must not overlap the importer’s managed files or `policy.json` / `github.json`.
- Exit codes: `0` complete, including "nothing new"; `2` error, nothing appended; `3` partial: rate limiting beyond `--max-wait` (default 3600 s) or repeated server or network errors stopped the fetch, fully fetched items were applied, and the cursor did not move, so run it again later.
- The dataset directory must exist. `events.jsonl` and `evidence.jsonl` are created on first use. The import does not need `policy.json`; `replay`, `explain` and `validate` do.

**The importer never reads or writes `policy.json`: map `gh:<login>:<id>` actor IDs to roles yourself.** Until an account is mapped, its events are recorded but change no state. The sync summary lists the actors in new events, and `orgmemory validate` lists every actor missing from `policy.json`.

| GitHub | Event |
|---|---|
| Issue or PR opened | `request` |
| Review approved / changes requested | `approve` / `reject` by the reviewer |
| Approving or rejecting review dismissed by a person | `revoke` of that review by the dismisser |
| Issue closed as not planned | `reject` by the closer |
| PR merged / closed without merging | `approve` by the merger / `reject` by the closer |
| Reopened after a close recorded as `reject` / any other reopen | `revoke` of that close, then `request` / `request` |
| Review re-requested from a reviewer whose review requested changes | `request` |
| Label listed in `github.json` added / removed | its `approve`, `reject` or `raise_risk` / `revoke` of the decision that label made, otherwise `comment` (a risk cannot be withdrawn) |
| Blocked-by link added | `dependency` |
| Comments, commenting reviews, other closes, stale-review auto-dismissals, removed blocked-by links | `comment` |
| Anything done by a bot, an app, an agent or a deleted account | `comment` only |
| Everything else (assignments, mentions, cross-references, other review requests, unlisted labels, edits) | nothing |

Text is never parsed: "LGTM", "/approve", "approved by @pm" or "depends on #3" anywhere is just a `comment` by its author. A dismissed review keeps its original verdict, so the log does not depend on when you synced. An edit adds a new evidence version, not an event; a deleted comment stays in the dataset and is reported as gone when its issue is refreshed.

A reopen by someone who may `revoke` returns the subject to `proposed`. Otherwise the revoke is an ignored attempt, and the `request` after it marks a reconsideration only if the reopener's role permits `request`. Expect two kinds of noise: a merge by someone without approve authority is listed as an ignored attempt even after an authorized approval, and reopening a close that was not the current decision (an unauthorized close, for example) shows `invalid_supersession` when the reopener may revoke.

Example: [`examples/orgmemory/github-acme-web`](examples/orgmemory/github-acme-web/) is built with `orgmemory github convert` from two synthetic captures of `acme/web` (in `tests/fixtures/github/`), with its [replay report](examples/orgmemory/github-acme-web/replay-report.md). In it, `acme/web#1` is the Feature A story on GitHub: the product manager closes the issue as not planned, an account manager adds the `approved` label, and the customer reopens it. The issue stays `rejected` with `reconsideration_requested: true` and `pending_approval: [product_manager]`; the label and the reopen's `revoke` are listed as ignored attempts.

## Data formats

A JSON object with a repeated key is invalid, including inside a nested object. This applies to
`reviews.jsonl`, `events.jsonl`, `evidence.jsonl`, `github-sync.jsonl`, `policy.json`, `github.json`, a
file passed to `orgmemory github convert`, benchmark `labels.jsonl` and `case.json`, and GitHub API
responses. The command exits 2 and reports `<file>:<line>: invalid JSON: duplicate key`. Parsing does not
keep the last value.

**`reviews.jsonl`** (MemFalsify, optional): human-confirmed assertions.

```json
{"memory_id": "m-27e874e0", "relation": "contradicts", "path": "notes/2026-10-07-decision-log.md", "quote": "Product manager rejects Feature A (bulk export) for the November release.", "reviewer": "demo-reviewer", "note": "optional"}
```

`memory_id` is derived from the memory's normalized text. If the memory is edited, the ID changes, and a review line that still names the old ID stops the audit with an `unknown memory_id` error (exit code 2) until the memory is re-reviewed and that line updated or removed. `quote` must appear verbatim in `path`, which is relative to the evidence directory.

**Organizational dataset**: a directory with three files.

- `events.jsonl`: one event per line, append-only. Fields: `id`, `timestamp` (with timezone; it is the effective time), `actor`, `action`, `subject`, `evidence_ids` (at least one), plus `note` (required for `raise_risk`), `dependency` (required for `dependency`) and `supersedes` (required for `revoke`, which must target the current decision). Actions: `request`, `reconsider`, `raise_risk`, `dependency`, `approve`, `reject`, `revoke`, `comment`.
- `policy.json`: `{"actors": {actor: [roles]}, "permissions": {action: [roles]}}`. Unknown actors have no roles. `pending_approval` lists the roles that can both approve and reject.
- `evidence.jsonl`: `id`, `path`, `sha256` of the raw bytes, exact `quote` and its `[start, end)` character span, `source_uri`, `source_kind`, `captured_at`. Use `orgmemory evidence add` instead of hashing by hand.

Replay rules: events are ordered by effective time, and ties keep file order. The whole log is validated before replay, including events after `--as-of`. An authorized `approve` / `reject` sets the status and clears reconsideration, pending approval and ignored attempts. Reversals and same-time conflicting decisions are recorded under `conflicts`.

**GitHub import files** (in the same dataset directory):

- `github.json` (optional, yours; the importer only reads it): `{"labels": {"<label>": "approve" | "reject" | "raise_risk"}}`. Label names match case-insensitively, and unlisted labels produce nothing. A change affects only events not yet in the log (`--full` backfills); an existing event whose mapping would now differ is reported as drift and never rewritten.
- `github-sync.jsonl`: one line per applied run (none for `--dry-run`) with `repo`, `repo_id`, `source` (`sync` or `convert`), `mode`, `since`, `started_at` (the server `Date` of the run's first response), `finished_at`, `complete`, `items`, `requests`, `events_appended`, `evidence_appended`, `gone`, `drift` and `importer`. The next cursor is the latest complete `sync` line in `full` or `incremental` mode’s `started_at` minus 5 minutes; targeted, partial, `convert` and dry runs never move it. Offline conversion uses the capture’s `started_at` as `finished_at` for reproducibility. The recorded `repo_id` pins the repository: if the name later resolves to a different ID (deleted and recreated), the import is refused. A renamed or transferred repository also fails closed and needs a deliberate re-import.
- `sources/github/<owner>/<repo>/<n>/<object>-<hash12>.txt`: one create-only file per version of each issue or PR, comment and review, and per mapped issue event, where `<object>` is `issue-<n>`, `comment-<id>`, `review-<id>` or `event-<id>` and `<hash12>` starts the file's SHA-256. Line 1 is an ASCII fact line built only from validated fields; line 2 is an ASCII-escaped JSON record of whitelisted API fields; line 3 is a fixed separator; the rest is GitHub's text (title and body, comment or review body, dismissal message) exactly as returned, except that a lone surrogate becomes U+FFFD and the record says `"lossy": true`. Review 206 on `acme/web#2` in the example:

```text
GitHub review 206 on acme/web#2 by gh:zhou-pm:5003 (User) at 2026-10-07T09:00:00Z: approved on commit 54bb654c9e60
{"author_association":"MEMBER","commit_id":"54bb654c9e6025347f57900a4a5c2313a96b8035","event":"reviewed","id":206,...}
--- verbatim from GitHub; untrusted ---
Approved now that export waits for the billing migration.
```

Every event cites its object's `fact` evidence, the whole fact line, which names the same actor as the event. When the text allows it, the event also cites `text`: the first non-empty line of the verbatim section that does not start with `<!--`, trimmed and cut to 200 characters. It is left out if the comment is minimized or the line contains a control character other than tab, or a bidi, zero-width or line-separator character. A review whose verdict was recovered from its dismissal also cites the dismissal's `fact`.

| GitHub IDs | Format | Example |
|---|---|---|
| Subject | `<owner>/<repo>#<number>`, lowercase | `acme/web#12` |
| Actor | `gh:<login>:<user id>`, login lowercased; `gh:~:<id>` if the login is not a valid GitHub login | `gh:zhou-pm:5003` |
| Event | `gh:<subject>/` + `opened`, `comment/<id>`, `review/<id>`, `event/<id>`, or `event/<id>/request` (the request after a reopen) | `gh:acme/web#1/event/108/request` |
| Evidence | `gh:<subject>/` + `issue@<th12>`, `comment/<id>@<th12>`, `review/<id>@<th12>` or `event/<id>`, then `/fact` or `/text` | `gh:acme/web#2/review/206@67c4aa9e1b3a/fact` |
| Dependency | the blocking issue as a subject | `acme/billing#3` |

`<th12>` is the first 12 hex digits of the SHA-256 of the verbatim text alone, so an edit creates a new evidence version and a login rename does not. A renamed or recycled login gives a new actor ID with no role.

## Agent skills

[`skills/memfalsify`](skills/memfalsify/SKILL.md) and [`skills/orgmemory`](skills/orgmemory/SKILL.md) teach an agent the workflow and its boundaries: report first, change memory only with permission, never self-confirm a review, never touch the policy. A GitHub account is not an authenticated person, and text quoted by the `memlab` MCP tools is untrusted. The skills say when to use those read-only tools instead of the CLIs, and how to add the [GitHub Action](docs/github-action.md) so a repository audits its own instruction files. Copy them into your agent's skills directory:

```bash
cp -r skills/memfalsify skills/orgmemory ~/.claude/skills/   # Claude Code
cp -r skills/memfalsify skills/orgmemory ~/.codex/skills/    # Codex
```

The CLIs must be installed in the environment the agent's shell uses. To use the MCP tools, register `memlab-mcp` as well ([docs/mcp.md](docs/mcp.md)).

## MCP server

`memlab-mcp` is a read-only [Model Context Protocol](https://modelcontextprotocol.io) server that gives a local agent nine tools: `memfalsify_claims`, `memfalsify_audit`, `memfalsify_diff`, `memfalsify_history`, `memfalsify_rollback`, `orgmemory_validate`, `orgmemory_replay`, `orgmemory_explain` and `orgmemory_audit`. `memfalsify_diff` compares a memory file in git between a base revision and a head (default: the working tree) and audits only the claims that were added or changed. `memfalsify_history` reports the commits that introduced and last changed each claim, plus removals. `memfalsify_rollback` returns a patch for one claim and never applies it. Install it with `pip install -e packages/memlab-mcp` (`./scripts/setup-local.sh` already does). Every path is confined to `--root`. The three git tools also refuse a repository whose work tree, git directory, common directory or object store lies outside that root. Model verifiers stay off unless the server is started with `--allow-model-verifiers`, which enables them for `memfalsify_audit` and `memfalsify_diff`. Tools, arguments and client registration: [docs/mcp.md](docs/mcp.md).

## Verifier backends: lexical, LLM, Jev

A verifier judges one memory against one evidence passage as `supports`, `contradicts` or `unrelated`, with a rationale. Three backends ship; `dual` and `cascade` combine the two model backends:

| `--verifier` | Who judges | Needs | Trade-off |
|---|---|---|---|
| `lexical` (default) | Term overlap, opposing terms, numbers, negation | Nothing | Offline, deterministic, explainable; misses paraphrases |
| `llm` | Claude reads the passage and explains its verdict (`--llm-model`, default `claude-opus-5-5`) | `pip install 'memfalsify[llm]'` and Anthropic credentials | Understands paraphrase and context; costs API calls and **sends memory and evidence text to the API** |
| `jev` | [Jev](https://docs.typesafe.ai), TypeSafe AI's decision-only judgment model, picks one of the three relations and returns a probability for each (`--jev-model`, default `jev-latest`) | `pip install 'memfalsify[jev]'` and `TYPESAFE_API_KEY` | A small fraction of an LLM judge's cost, with a calibrated confidence; no written explanation, and **sends memory and evidence text to the TypeSafe API** |
| `dual` | `llm` and `jev`, independently | Both | Agreement passes through; disagreement becomes `disputed`, and the report shows both opinions |
| `cascade` | `jev` first; Claude only when Jev's confidence is below `--accept-at` (default 0.8) | Both | Close to Jev's cost when Jev is usually sure; the report shows Jev's unsure opinion next to Claude's verdict. No cross-check on answers Jev is confident about |

```bash
memfalsify audit MEMORY.md --evidence evidence --verifier dual
```

The two model backends work differently on purpose: Claude reasons its way to a verdict and writes why, while Jev outputs a decision with probabilities. Both get the same written definitions of the three relations (`RELATION_CRITERIA` in `verify.py`), so when they disagree it is about the evidence, not the wording of the task, and a human reads that evidence. A Jev answer whose confidence is below `JevVerifier(threshold=0.5)` counts as `unrelated`; the rationale keeps all three probabilities and the resolved model version (for example `jev-1.13.0`, since `jev-latest` is an alias).

**Which passages a model sees.** The term index still pre-selects passages. Model verifiers use a looser threshold (`--min-coverage`, default 0.2 instead of 0.5) so paraphrases are judged, and only the best `--max-judged` passages per memory (default 8) are sent. That bounds cost at *memories × max-judged* calls per model. A passage that shares no terms with a memory is never judged.

The LLM verifier asks for structured output, so every verdict fits the schema, and its instructions treat memory and evidence as quoted data. On models that support it, it opts in to the server-side refusal fallback; a refused request is recorded as no verdict (`unrelated`, with the stop reason in the rationale). `dual` is the stricter choice: every judgment is checked twice. `cascade` follows the "accept when confident, escalate when unsure" pattern and is much cheaper, but it trusts Jev's confident answers alone, so tune `--accept-at` on memories you have already reviewed before relying on it.

For both model backends, an API failure, a missing SDK or missing credentials stops the audit with exit code 2 and an error message rather than producing a partial report.

From Python:

```python
from pathlib import Path

from memfalsify import DualVerifier, JevVerifier, LLMVerifier, audit

report = audit(Path("MEMORY.md"), Path("evidence"), verifier=DualVerifier(LLMVerifier(), JevVerifier()),
               min_coverage=0.2, max_judged=8)
```

Any object with a `name` and a `judge(claim, passage) -> Judgment` method is a verifier, so other models plug in the same way. Model judgments stay candidates: they never replace the human review step.

## Benchmark and GitHub Action

The [MemFalsify benchmark](benchmarks/memfalsify/README.md) has 139 labeled English and Chinese claims across
eight synthetic cases. The [measured lexical baseline](benchmarks/memfalsify/RESULTS.md) has contradiction
precision 0.62, recall 0.27 and 3-way accuracy 0.44; these cases exercise failure modes rather than estimate
accuracy on real memories. Run it offline with `python benchmarks/memfalsify/run.py`.

The [GitHub Action](docs/github-action.md) audits explicitly named agent instruction files against repository
evidence and puts the report in the job summary. Its default strict check fails on possible counterevidence;
unsupported claims and wording issues remain review leads. With `diff-base` (for example the pull request's base
commit, and `fetch-depth: 0` on `actions/checkout`), it runs `memfalsify diff`, so a pull request is gated only on
the claims it adds or changes.

Set `persist-credentials: false` on checkout to keep the job token out of `.git/config`. For example:

```yaml
steps:
  - uses: actions/checkout@v4
    with:
      persist-credentials: false
  - uses: timothyzhbw-jpg/memory-lab@<40-character SHA>
    with:
      memory: CLAUDE.md AGENTS.md
```

Memory and evidence inputs skip hidden components and ignored directories in both their original paths
and resolved symlink targets. The action also warns and skips memory symlinks that leave the workspace.

## Trust boundaries and limitations

- A hash proves a quote exists in particular bytes. It does not prove the source is authentic or that the quote means what someone says it means.
- Lexical and model candidates are leads, not findings. Only `reviews.jsonl` settles a memory.
- `--verifier llm` sends memory claims and evidence passages to the Anthropic API, `jev` sends them to the TypeSafe API, and `dual` and `cascade` send them to both. Use them only on material you are allowed to send there.
- `orgmemory audit` findings restate the log and the policy; they are leads for a person, not verdicts. The log cannot record that a risk was resolved, so an approved subject keeps a `risk_after_approval` finding for every risk raised before the approval. A self-approval or an ignored attempt can be legitimate: the policy decides who may act.
- Actor IDs and `policy.json` are trusted input. There is no identity authentication, signed provenance or policy versioning yet, and changing the policy changes every replay.
- A repeated key in a JSON object is rejected, including in a nested object. Keeping the last value would let one `events.jsonl` line show one `actor` to a person reading the file and another to replay, and would let a `policy.json` that names an actor twice grant the second list of roles.
- Evidence is UTF-8 text only. Images, audio, video and PDF are reported as skipped.
- `diff`, `history` and `rollback` trust the local git repository: they read whatever its objects and refs say, and their attribution is only as good as its history (rebases, squashes and first-parent merges change who appears to have introduced a claim).
- GitHub import: a GitHub account is not an authenticated person. Automation that acts with a person's own token (for example a workflow using a maintainer's personal access token) cannot be told apart from that person; reviews carry no app marker; merge-queue and auto-merge merges are attributed to whoever GitHub records; labels applied by issue templates may be attributed to the issue author. Do not list labels in `github.json` that templates, bots or token-backed workflows apply, and be careful which accounts you map.
- `policy.json` is global: mapping a `gh:` account to a role grants that role on the subjects of every imported repository.
- On public repositories anyone can open issues, comment, review pull requests and close their own issues. All of it is recorded under the real account and ignored by replay unless that account is mapped, so expect `unauthorized_change_attempts`. That is accurate, not a bug.
- Capture hashes prove the bytes the importer rendered from the API, not what GitHub served, and a capture given to `convert` is trusted as what GitHub said: convert only captures you made. Captured GitHub text is untrusted, and prompt injection in issue bodies is expected. It never becomes IDs, notes, actors or importer output, but `replay` and `explain` print evidence quotes as they are (single-line, with no terminal escapes or bidi characters, but possibly with markdown or HTML).
- The GitHub token is read from the environment only, sent only to `https://api.github.com` (redirects are never followed to another host), and never written or printed.
- Datasets imported from private repositories, and `--save-capture` files, contain private text: never commit them. There is no redaction workflow: removing a secret from a capture means editing it by hand, after which `validate` fails on purpose.
- This is a research MVP, not production infrastructure.

## Python API

Import library functions and models from `memcore`, `memfalsify`, `orgmemory`, and
`memlab_mcp`. Each package declares its public names in `__all__` and ships `py.typed`.
The [API reference](docs/api.md) covers parsing, auditing, Git history, replay, GitHub ingestion,
and server construction, with examples and the stability policy for 1.0. Existing module import
paths remain available; CLI configuration helpers are internal.

## Roadmap

| | MemFalsify | Organizational Memory |
|---|---|---|
| **v0.1** | Text memory audit, evidence tracing, review workflow | Event log, state machine, authority rules, provenance |
| **v0.2** | LLM + Jev dual verification, cascade mode ✓ | GitHub Issues / PR ingestion ✓ |
| **v0.3** | Git history of memory: diff, review, rollback; cross-agent skill polish ✓ | State conflicts and decision audit ✓ |
| v0.4 | Multimodal evidence (OCR / transcription with locators) | Cross-platform organizational memory |
| v1.0 | Benchmark, stable API | Benchmark, stable API |

What changed in each package, version by version: [CHANGELOG.md](CHANGELOG.md).

## Development

```bash
pip install pytest
python -m pytest   # runs against packages/*/src, no install needed
```

Layout: `packages/memcore` (evidence integrity, JSONL), `packages/memfalsify`, `packages/orgmemory`, `skills/`, `examples/`, `tests/`. Contributions should include a minimal synthetic reproduction. Never commit private evidence or credentials.

## License

Apache-2.0. See [LICENSE](LICENSE).
