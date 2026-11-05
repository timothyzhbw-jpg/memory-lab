# MemFalsify audit report

- Memory: `MEMORY.md` (1 file(s): `MEMORY.md`)
- Evidence root: `docs`
- Verifier: `lexical` · as of 2026-10-13 · mode: **read_only** (no memory was modified)

| Status | Memories |
|---|---|
| POSSIBLE COUNTEREVIDENCE | 1 |
| NO EVIDENCE | 1 |
| POSSIBLE SUPPORT | 2 |

### POSSIBLE COUNTEREVIDENCE · `m-f0a6035e`

> The main database runs PostgreSQL 15.

`MEMORY.md:4` · `Memory`

**Evidence**
- contradicts (candidate, unconfirmed) · `database.md:3` · sha256 `78e1f22d39c6` · different values: claim ['15'] vs source ['16']
  > The main database runs PostgreSQL 16.

**Proposed actions** (not applied)
- `verify_counterevidence`: Possible contrary evidence; confirm or dismiss it in reviews.jsonl.

### NO EVIDENCE · `m-314c50bc`

> The nightly export job finishes before 04:00 UTC.

`MEMORY.md:6` · `Memory`

**Proposed actions** (not applied)
- `attach_evidence`: No evidence found; add a source or label this memory unverified.

### POSSIBLE SUPPORT · `m-4966c315`

> Refund requests over 500 dollars need a second reviewer.

`MEMORY.md:3` · `Memory`

**Evidence**
- supports (candidate, unconfirmed) · `refunds.md:3` · sha256 `4182f813c96a` · source restates the claim (term coverage 1.00)
  > Refund requests over 500 dollars need a second reviewer.

**Proposed actions** (not applied)
- `confirm_support`: Possible supporting evidence; confirm it to mark this memory supported.

### POSSIBLE SUPPORT · `m-a332593a`

> The public API rate limit is 60 requests per minute.

`MEMORY.md:5` · `Memory`

**Evidence**
- supports (candidate, unconfirmed) · `api.md:3` · sha256 `f5a6a83d2d9c` · source restates the claim (term coverage 1.00)
  > The public API rate limit is 60 requests per minute.

**Proposed actions** (not applied)
- `confirm_support`: Possible supporting evidence; confirm it to mark this memory supported.

## Limitations

- Unconfirmed matches are candidates found by comparing words: support by term overlap, a contradiction by the same setting with another value or two near-identical statements that differ. They can miss paraphrases and misread context, and a report without flags has not ruled anything out.
- Only human reviews (reviews.jsonl) can mark a memory supported or challenged.
- Hashes prove a quote exists in the scanned bytes, not that the source is authentic or current.
- Missing evidence means unverified, not false. No memory was modified.
