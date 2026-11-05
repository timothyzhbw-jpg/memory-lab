# Architecture

English | [中文](architecture.zh-CN.md)

**MemFalsify asks whether a memory is backed by evidence, and orgmemory asks who put it there and whether they were allowed to, because writing a memory is itself an organizational decision.**

A line in an agent's `MEMORY.md` changes what the agent believes, so it works like any other decision: someone proposes it, someone approves it, and only some people may approve it. When MemFalsify finds a line with counterevidence or with no evidence, the next question is who put it there. That question has an answer only if the write was recorded as a decision.

The [closed-loop demo](../examples/closed-loop/README.md) runs everything on this page with the real CLIs, on synthetic data.

## The chain, step by step

`Remember → Verify → Challenge → Resolve → Evolve`

| Step | What happens | MemFalsify | orgmemory |
|---|---|---|---|
| **Remember** | A line lands in `MEMORY.md` | Later, `history` names the commit, author and subject that introduced it | `request` and `approve` events record the write; replay judges each one by the role its actor held |
| **Verify** | Is the line backed by evidence? | `audit` compares each claim with the evidence and reports a status with cited quotes | Takes no part |
| **Challenge** | A finding becomes a question for the organization | The report and its proposed actions, which only a person applies | A `raise_risk` or `comment` event; `audit` and `explain` show who asked, who approved, and whether they were allowed |
| **Resolve** | Someone with the permission decides | `reviews.jsonl` lets a person confirm or dismiss the evidence | `approve`, `reject` or `revoke` by that person; the same action from anyone else is recorded and ignored |
| **Evolve** | A person edits the memory. A change to the words gives the line a new ID; a change of case or whitespace keeps it | `diff` and `history` show the change; `rollback` prints a patch for a person to apply | The new line is a new subject; the old one keeps its decision; `changes` and `export` report them |

```text
 Remember    a commit adds a line to MEMORY.md
    |
    v
 Verify      memfalsify audit --format json     finding: memory_id, text, location, status, evidence
             memfalsify history --format json   claim: memory_id, text, location, introduced
    |
    v
 Challenge   to_events.py
               -> orgmemory evidence add        evidence.jsonl
               -> orgmemory append              events.jsonl: request, approve, raise_risk, comment
    |
    v
 Resolve     orgmemory audit and explain        who asked, who approved, was it allowed, what is open
             orgmemory append                   a person with the permission: approve, reject or revoke
    |
    v
 Evolve      a person edits MEMORY.md and commits; the new line goes back to Verify
```

MemFalsify never writes a fact or edits memory: it writes reports and proposals. orgmemory's replay and audit are read-only, and `append` only adds lines. The one thing between the two tools is a format conversion, [`to_events.py`](../examples/closed-loop/to_events.py), a standard-library script that reads MemFalsify's JSON and prints what the orgmemory commands take.

## From a finding to an event, field by field

### A memory write: two events per line

From `memfalsify history --format json`, `claims[]`:

| History field | Event field | Rule |
|---|---|---|
| `memory_id` | `subject` | `memory:<memory_id>` |
| `memory_id`, `introduced.commit` | `id` | `write-request-<memory_id>-<commit>` and `write-approve-<memory_id>-<commit>`, with the first 12 characters of the commit. A line removed and landed again has a new commit, so it gets new events on its old subject; after a decision, replay reads that as a reconsideration |
| `introduced.author` | `actor` of the `request` | Who proposed the line |
| `introduced.subject` | `actor` of the `approve` | The name in a final `(approved-by: NAME)`: words of letters, digits, `.`, `_`, `@` and `-` in any script, with one space between words. With no such name, the author: landing a line with no other approval is approving it yourself. An `approved-by:` that does not fit leaves the author as the approver, and the script says so on stderr |
| `introduced.date` | `timestamp` of both | The commit's author date |
| `location`, `text` | `note` of the `request` | `Memory write at <location>: <text>` |
| `replaced.memory_id` | `note` of the `request` | Adds `(edits <memory_id>)` when the line replaced an earlier one |
| `location`, `introduced` | `evidence_ids` of both | `<memory_id>-<head>-line` quotes the memory line; `<memory_id>-<commit>-introduced` quotes the claim's `Introduced` line in the Markdown history |

A line older than the scan has no commit to name: it gets no write events, and the script says so on stderr.

Every file a record cites has to be the file at the history's `head`, so that `git:<head>:<path>` is true. The script compares the two with `git cat-file`, line endings aside, and stops at any difference: an uncommitted line, an edited or untracked evidence file, a report older than the memory. Commit, then run the audit and history again. The line at `location` also has to hold the claim once its markup is removed, the way MemFalsify removes it.

The approval is `approve` for the same reason a merged pull request is: [`github_map.py`](../packages/orgmemory/src/orgmemory/github_map.py) maps a GitHub merge to `approve`.

### A finding: one event or none

From `memfalsify audit --format json`, `findings[]`:

| Finding field | Event field | Rule |
|---|---|---|
| `memory_id` | `subject` | `memory:<memory_id>` |
| `memory_id`, `status` | `id` | `finding-<status>-<memory_id>`, so a later audit with the same result adds nothing, evidence records included |
| `status` | `action` | The table below |
| `evidence[].reviewer` | `actor` | The first reviewer who confirmed the evidence; otherwise `memfalsify`, which `policy.json` gives a role that may raise risks and comment |
| `status`, `location`, `text` | `note` | `<status> at <location>: <text>` |
| `evidence[].span` | `evidence_ids` | One evidence record per span, supporting spans left out unless the status is `conflicted`. For `insufficient_evidence`, which has no span, the record quotes the memory line |
| report `as_of` | `timestamp` | `--audited-at` when you give it; otherwise the end of that day in UTC, because the report holds only a date. If the commit that introduced the line is later, its time. See [Limits](#limits) |

| Status | Action | Why |
|---|---|---|
| `challenged` | `raise_risk` | A reviewer confirmed contrary evidence |
| `conflicted` | `raise_risk` | Confirmed evidence points both ways |
| `candidate_counterevidence` | `raise_risk` | Possible contrary evidence: a lead for a person |
| `disputed` | `raise_risk` | Verifiers disagree |
| `insufficient_evidence` | `comment` | Missing evidence means unverified, not false, so the state stays as it was |
| `candidate_support` | none | Nothing to challenge |
| `supported` | none | Nothing to challenge |

No finding becomes `approve`, `reject` or `revoke`. Those stay with a person who holds the permission.

A confirmed contradiction from a reviewer the policy gives no role is judged like any other event: it is recorded and ignored, so the subject has no risk on it and `orgmemory audit` reports an `unauthorized_attempts` finding, while an unreviewed candidate from `memfalsify` does leave a risk. Give your reviewers a role that may raise risks.

### An evidence record

From `span` in a finding, for `orgmemory evidence add`:

| Field | Option | Rule |
|---|---|---|
| `span.path`, report `evidence_root` | `--path` | The file is copied to `sources/<head>/<path>` in the dataset, after its bytes are checked against `span.sha256` and the file at `head`. `--evidence .` is fine: the root is then the repository |
| `span.quote` | `--quote` | Unchanged; orgmemory hashes the copy and finds the quote again |
| `span.sha256`, `span.quote` | `--id` | `<memory_id>-<first 8 of the hash>-<start>`, where `start` is the first occurrence of the quote: the range `orgmemory evidence add` records |
| history `head` | `--source-uri` | `git:<head>:<path>` |
| fixed | `--source-kind` | `evidence_file`, `memory_file` or `memory_history` |
| report `as_of` | `--captured-at` | `--audited-at`, or the end of that day in UTC |

### One line, worked through

The audit reports (trimmed):

```json
{"memory_id": "m-9efe3be5", "text": "The public API rate limit is 100 requests per minute.", "location": "MEMORY.md:5", "status": "candidate_counterevidence", "evidence": [{"relation": "contradicts", "confirmed": false, "reviewer": null, "span": {"path": "api.md", "quote": "The public API rate limit is 60 requests per minute."}}]}
```

The history names the commit: author `agent-7`, subject `Remember the API rate limit (approved-by: wu)`. Three events result:

```json
{"id": "write-request-m-9efe3be5-7ed0c4c7f4b1", "timestamp": "2026-10-07T09:00:00Z", "actor": "agent-7", "action": "request", "subject": "memory:m-9efe3be5", "evidence_ids": ["m-9efe3be5-b17a3549d0ee-line", "m-9efe3be5-7ed0c4c7f4b1-introduced"], "note": "Memory write at MEMORY.md:5: The public API rate limit is 100 requests per minute."}
{"id": "write-approve-m-9efe3be5-7ed0c4c7f4b1", "timestamp": "2026-10-07T09:00:00Z", "actor": "wu", "action": "approve", "subject": "memory:m-9efe3be5", "evidence_ids": ["m-9efe3be5-b17a3549d0ee-line", "m-9efe3be5-7ed0c4c7f4b1-introduced"]}
{"id": "finding-candidate_counterevidence-m-9efe3be5", "timestamp": "2026-10-10T23:59:59Z", "actor": "memfalsify", "action": "raise_risk", "subject": "memory:m-9efe3be5", "evidence_ids": ["m-9efe3be5-f5a6a83d-14"], "note": "candidate_counterevidence at MEMORY.md:5: The public API rate limit is 100 requests per minute."}
```

`wu` holds a role that may not approve, so `orgmemory audit` reports an `unauthorized_attempts` finding, the line stays `proposed`, and approval is still pending from `memory_steward` ([the report](../examples/closed-loop/expected/3-orgmemory-audit.md)). The same finding on a line that `zhou` was allowed to approve leads somewhere else: a `risk_after_approval` finding, because the decision was sound and the facts have moved.

## What each tool does alone

Independence is a feature. `memfalsify` and `orgmemory` each depend on `memlab-core` and neither imports the other, so you can install one and leave out the other.

- **MemFalsify alone** audits a memory or instruction file against a directory of evidence, and with git it shows which claims changed and which commit they came from. It has no notion of roles, approvals or event logs. Its question ends at "is this backed by evidence?".
- **orgmemory alone** replays any decision log under a role policy: product features, GitHub pull requests, Jira issues. It has no notion of a memory file. A subject is any string, which is why `memory:m-9efe3be5` works with no change to it.
- **memcore** is what they share: evidence as an exact quote at a known range of a file with a recorded SHA-256.

Together they answer the question neither answers alone: this line may be wrong, and here is who put it there and whether they were allowed to.

## Should orgmemory gain memory-write event types?

The alternatives: add `memory_write` and `memory_approve` actions to orgmemory, or keep its eight actions and do the connection at the format layer.

**Decision: keep the existing event model.** The connection stays a format conversion and this documentation.

The code supports this:

- Replay has nothing new to do. It gates every event with `permitted[event.action]` and treats a subject as an opaque string ([`replay.py`](../packages/orgmemory/src/orgmemory/replay.py)). The demo gets each behavior it needs from unchanged code: the ignored approval, the pending approval, the risk after approval, the authorized rejection. A `memory_approve` would have the same state transitions as `approve`.
- A new action reaches far for no new state. `Action`, `DECISIONS` and `Decision.action` in [`models.py`](../packages/orgmemory/src/orgmemory/models.py), `_ACTIONS` and the branches in `replay.py`, `periods` and the findings in `audit.py`, `changes.py`, `export.py`, the wording tables in `memlab_ui/decisions.py`, the public `orgmemory.Action` type that [`api.md`](api.md) documents as eight actions, and every `policy.json`, whose permissions name actions.
- It would break older readers. `Event.action` and the keys of `Policy.permissions` are closed sets, so a release of orgmemory from before the change would refuse a log that holds a new action. Those are the guarantees that make a log trustworthy; they should move only for a real need.
- The importers already work this way. GitHub merges and reviews and Jira status changes all map onto the same actions.
- It keeps the tools independent. New actions would put knowledge of memory files into orgmemory's core.

The costs of this decision:

- **Permissions are per action, not per kind of subject.** In one log, whoever may approve a memory write may also approve a product decision. The demo keeps memory writes in a dataset of their own, with its own policy.
- **The link between an edited line and its earlier version is only text.** An edit to the words gives the line a new `memory_id` and so a new subject; the old ID appears in the `note` of the new `request`, and replay does not read it. A change of case or whitespace keeps the `memory_id`, so the line stays the same subject with the same author: the change shows in the history's `last_changed` and adds no event.
- **`to_events.py` is an example you copy and own.** A test runs it against the real CLIs in this repository; in your own setup, its upkeep falls to you.

What would change this decision:

1. One log has to hold memory writes and other decisions with different approvers, and separate datasets prove too awkward. Permissions per subject prefix, or new actions, would then pay for themselves.
2. A memory write needs a state that `request`, `approve`, `reject` and `revoke` cannot express, such as "retired, replaced by this other line", and replay has to act on it.
3. People run this conversion regularly. That would justify a small maintained exporter as its own package, with the two tools still independent. It would not by itself change the event model.

## Limits

- **Names are claims.** A git author name and an `(approved-by: NAME)` in a commit subject are text that anyone can write. orgmemory checks the role of the named actor, not that this person acted. An evidence record proves the text is in the report, not that it is true. Signed commits or captured pull request reviews are stronger sources, which you would have to add to this conversion. `(approved-by: NAME)` is the demo's stand-in for however your team records approval.
- **Candidates are leads.** With the default lexical verifier, `candidate_counterevidence` means the words suggest a contradiction. Only a person's review makes a memory `challenged`, and a `raise_risk` from `memfalsify` is a question for a person.
- **History has edges.** `memfalsify history` follows the first-parent line, so a change merged with a merge commit is attributed to that commit, and a line older than `--max-commits` has no author to name.
- **A risk stays on record.** The log has no event that resolves a risk, so one remains listed after the line is rejected or corrected.
- **An audit has a date, and replay orders by time.** The default timestamp is the end of the `as_of` day in UTC, so a decision made that same day sorts before the finding, and `orgmemory audit` reads it as a risk raised after the approval, which says the facts moved when the approver may have read the audit first. Pass `--audited-at` with the real time, with its zone, when someone may decide on the day of the audit. The demo decides on a later day.
- **A removal leaves no event.** The conversion reads the current claims, so a removed line keeps its last state in orgmemory until it lands again. Rewriting history gives lines new commits, and those read as new landings.
- **A cited range is the first occurrence.** `orgmemory evidence add` finds a quote by searching the file. When a sentence appears twice, the record points at the first. When one commit lands several lines, each `-introduced` record points at the first claim's `Introduced` line, which quotes the same text. The quote and the hash stay true; the line a reader lands on can be an earlier one.
- **Not exercised here.** The demo's evidence is plain text. Transcript and OCR evidence goes through the same commands, but the demo does not cover it.
