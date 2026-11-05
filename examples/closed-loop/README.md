# Closed loop: from a finding to a decision

English | [中文](README.zh-CN.md)

Writing a line into an agent's `MEMORY.md` is a decision: someone proposes it, someone approves it, and only some people may approve it. This demo takes four memory lines through `Remember → Verify → Challenge → Resolve → Evolve` with the real `memfalsify` and `orgmemory` commands. Everything is synthetic: the people, the service and the documents. [docs/architecture.md](../../docs/architecture.md) explains how the two tools relate and maps every field.

## Run it

From a fresh checkout, on Linux or macOS, from the repository root:

```bash
python3 -m venv .venv && .venv/bin/python -m pip install -q -e packages/memcore -e packages/memfalsify -e packages/orgmemory && .venv/bin/python examples/closed-loop/run_demo.py
```

On Windows, in PowerShell:

```powershell
python -m venv .venv; .venv\Scripts\python -m pip install -q -e packages/memcore -e packages/memfalsify -e packages/orgmemory; .venv\Scripts\python examples/closed-loop/run_demo.py
```

If the two tools are already installed, `python examples/closed-loop/run_demo.py` is enough. It needs git. The install contacts a package index once; the demo itself stays offline and calls no model. It builds a git repository in a new temporary directory, leaves everything there for you to look at, and tells you where. It exits 0 when every report matches [`expected/`](expected/).

## The four lines

| Line in `MEMORY.md` | Proposed by | Approved by | May they approve? | MemFalsify | orgmemory |
|---|---|---|---|---|---|
| Refund requests over 500 dollars need a second reviewer. | `lin` | `zhou` | yes | possible support | approved |
| The main database runs PostgreSQL 15. | `lin` | `zhou` | yes | possible counterevidence: the docs moved to 16 after the approval | approved, with a **high** `risk_after_approval` |
| The public API rate limit is 100 requests per minute. | `agent-7` | `wu` | **no** | possible counterevidence: the docs say 60 | still proposed, with an `unauthorized_attempts` finding |
| The nightly export job finishes before 04:00 UTC. | `agent-7` | `agent-7`, by landing it | **no** | no evidence | still proposed, with an `unauthorized_attempts` finding |

The second and third lines get the same finding from MemFalsify and lead to different questions. The database line was approved properly, so the question is whether the decision still stands. The rate limit line never had a valid approval, so the question is who should have looked at it. MemFalsify cannot tell them apart; orgmemory can. [`policy.json`](policy.json) says who may do what: only `memory_steward` may approve, and `zhou` is the only one who holds it.

## Step by step

**1. Remember.** `run_demo.py` commits each line as its author. A commit subject may end with `(approved-by: NAME)`; without one, the author approved their own line by landing it.

**2. Verify.** MemFalsify audits the memory against `docs/` and traces each line to its commit. It changes nothing.

```text
memfalsify audit MEMORY.md --evidence docs --as-of 2026-10-10 --format json
memfalsify history MEMORY.md --format json
```

Reports: [audit](expected/1-memfalsify-audit.md), [history](expected/2-memfalsify-history.md).

**3. Challenge.** [`to_events.py`](to_events.py), standard library only, turns those reports into evidence records and events. `run_demo.py` passes each one to a real command. Then orgmemory reads the log.

```text
orgmemory evidence add DATASET --id=... --path=... --quote=... --source-uri=... --source-kind=... --captured-at=...
orgmemory append DATASET --event='{"id": "write-approve-m-9efe3be5-7ed0c4c7f4b1", "actor": "wu", "action": "approve", ...}'
orgmemory audit DATASET --as-of 2026-10-11T00:00:00+00:00
orgmemory explain DATASET memory:m-9efe3be5
```

The [audit](expected/3-orgmemory-audit.md) says `wu` made one approval the policy ignored. [`explain`](expected/4-orgmemory-explain.md) shows the rate limit line as `proposed`, with the approval still pending from `memory_steward`, the risk on record, and this line:

```text
- `write-approve-m-9efe3be5-7ed0c4c7f4b1`: wu tried to approve; actor roles ['account_manager'] may not approve
```

**4. Resolve.** `zhou` holds the permission, reads `docs/api.md` and rejects the line. The script plays `zhou` with one more `orgmemory append`, because neither tool decides anything. [`explain`](expected/5-orgmemory-explain-resolved.md) now shows `rejected`, by `zhou`, with the quote from the docs.

**5. Evolve.** `lin` corrects the line by hand and `zhou` approves it. The loop runs again and records only what is new. The new wording has a new ID, so it is a new subject: MemFalsify [finds support](expected/6-memfalsify-audit-corrected.md) for it and orgmemory shows it [approved](expected/7-orgmemory-explain-corrected.md). The [final audit](expected/8-orgmemory-audit-final.md) still lists what is open, and the database line still waits for someone to decide.

## What is here

| File | What it is |
|---|---|
| [`run_demo.py`](run_demo.py) | Builds the repository and runs the loop. `--work DIR` picks the directory; `--update-expected` rewrites `expected/` |
| [`to_events.py`](to_events.py) | The format conversion: reports in, evidence records and events out |
| [`policy.json`](policy.json) | Who may do what |
| [`expected/`](expected/) | Every report, plus the final [`events.jsonl`](expected/events.jsonl) and [`evidence.jsonl`](expected/evidence.jsonl) |

`tests/test_closed_loop.py` runs all of this from scratch and compares it.

## Limits

A git author name and an `(approved-by: NAME)` are text that anyone can write, so orgmemory checks the role of the name it is given, not that this person acted. `(approved-by: NAME)` stands in for however your team records approval; change `APPROVED_BY` in `to_events.py` to fit. The lexical verifier's findings are candidates for a person to read. `to_events.py` cites only what the history's HEAD holds, so commit first, and it stops at an uncommitted change. An audit report holds a date, so pass `--audited-at` when someone may decide on the day of the audit; the demo decides on a later day. [docs/architecture.md](../../docs/architecture.md#limits) has the full list.
