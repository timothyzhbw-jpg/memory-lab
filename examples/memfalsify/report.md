# MemFalsify audit report

- Memory: `MEMORY.md` (2 file(s): `MEMORY.md`, `infra.md`)
- Evidence root: `evidence`
- Verifier: `lexical` · as of 2026-10-10 · mode: **read_only** (no memory was modified)

| Status | Memories |
|---|---|
| CHALLENGED | 1 |
| POSSIBLE COUNTEREVIDENCE | 3 |
| NO EVIDENCE | 3 |
| POSSIBLE SUPPORT | 2 |
| SUPPORTED | 1 |

### CHALLENGED · `m-27e874e0`

> Feature A (bulk export) is approved for the November release.

`MEMORY.md:7` · `Product decisions`

**Evidence**
- contradicts (confirmed by demo-reviewer) · `notes/2026-10-07-decision-log.md:3` · sha256 `86dc52dbbfe2` · Decision log records a rejection, not an approval.
  > Product manager rejects Feature A (bulk export) for the November release.

**Issues**
- internal_conflict: may contradict m-8b09698a at MEMORY.md:8: "Feature A was rejected by the product manager."

**Proposed actions** (not applied)
- `review_or_retire`: A reviewer confirmed contrary evidence; correct or remove this memory by hand.
- `resolve_internal_conflict`: Two memories disagree; at most one can stay as written.

### POSSIBLE COUNTEREVIDENCE · `m-9efe3be5`

> The public API rate limit is 100 requests per minute.

`infra.md:6`

**Evidence**
- contradicts (candidate, unconfirmed) · `docs/api.md:3` · sha256 `057549a717f8` · different values: claim ['100'] vs source ['60']
  > The public API rate limit is 60 requests per minute per key.

**Proposed actions** (not applied)
- `verify_counterevidence`: Possible contrary evidence; confirm or dismiss it in reviews.jsonl.

### POSSIBLE COUNTEREVIDENCE · `m-f0a6035e`

> The main database runs PostgreSQL 15.

`infra.md:7`

**Evidence**
- contradicts (candidate, unconfirmed) · `docs/database.md:3` · sha256 `871f23010759` · different values: claim ['15'] vs source ['16']
  > The main database runs PostgreSQL 16 since the September upgrade.

**Proposed actions** (not applied)
- `verify_counterevidence`: Possible contrary evidence; confirm or dismiss it in reviews.jsonl.

### POSSIBLE COUNTEREVIDENCE · `m-6d879fbb`

> Deployments always go through the staging cluster.

`infra.md:8`

**Evidence**
- contradicts (candidate, unconfirmed) · `notes/2026-09-12-incident.md:3` · sha256 `870054974f94` · same subject, but only one side is negated
  > A hotfix was deployed directly to production, skipping the staging cluster.

**Issues**
- overgeneralization: absolute wording 'always'

**Proposed actions** (not applied)
- `verify_counterevidence`: Possible contrary evidence; confirm or dismiss it in reviews.jsonl.
- `narrow_scope`: State the cases the evidence actually covers.

### NO EVIDENCE · `m-88f63934`

> The bulk export will ship next week.

`MEMORY.md:10` · `Product decisions`

**Issues**
- relative_time: 'next week' has no absolute date

**Proposed actions** (not applied)
- `attach_evidence`: No evidence found; add a source or label this memory unverified.
- `add_absolute_date`: Replace relative time with the date it refers to.

### NO EVIDENCE · `m-f19a6676`

> 2026-03-02: Feature B was postponed to Q3.

`MEMORY.md:11` · `Product decisions`

**Issues**
- possibly_stale: latest date 2026-03-02 is over 180 days before 2026-10-10

**Proposed actions** (not applied)
- `attach_evidence`: No evidence found; add a source or label this memory unverified.
- `reverify`: Re-check this against current sources.

### NO EVIDENCE · `m-f71d8f28`

> Priya owns the payments integration.

`MEMORY.md:15` · `Team`

**Proposed actions** (not applied)
- `attach_evidence`: No evidence found; add a source or label this memory unverified.

### POSSIBLE SUPPORT · `m-8b09698a`

> Feature A was rejected by the product manager.

`MEMORY.md:8` · `Product decisions`

**Evidence**
- supports (candidate, unconfirmed) · `notes/2026-10-07-decision-log.md:3` · sha256 `86dc52dbbfe2` · source restates the claim (term coverage 1.00)
  > Product manager rejects Feature A (bulk export) for the November release.

**Issues**
- internal_conflict: may contradict m-27e874e0 at MEMORY.md:7: "Feature A (bulk export) is approved for the November release."

**Proposed actions** (not applied)
- `confirm_support`: Possible supporting evidence; confirm it to mark this memory supported.
- `resolve_internal_conflict`: Two memories disagree; at most one can stay as written.

### POSSIBLE SUPPORT · `m-2c4e9e63`

> We probably use squash merges for pull requests.

`MEMORY.md:16` · `Team`

**Evidence**
- supports (candidate, unconfirmed) · `CONTRIBUTING.md:3` · sha256 `c12fb71d6a80` · source restates the claim (term coverage 1.00)
  > Use squash merges for pull requests.

**Issues**
- inference_marker: 'probably' marks an inference, not an observation

**Proposed actions** (not applied)
- `confirm_support`: Possible supporting evidence; confirm it to mark this memory supported.
- `label_as_inference`: Keep it explicitly marked as an inference, or verify it.

### SUPPORTED · `m-d4ec01d6`

> Bulk export depends on the billing schema migration.

`MEMORY.md:9` · `Product decisions`

**Evidence**
- supports (confirmed by demo-reviewer) · `notes/2026-10-06-standup.md:3` · sha256 `0c7d4e817311` · confirmed by reviewer
  > Bulk export depends on the billing schema migration

**Issues**
- duplicate: also stated at infra.md:9

**Proposed actions** (not applied)
- `deduplicate`: Keep one copy so future edits cannot diverge.

## Skipped evidence

- `media/whiteboard.png`: media file: no usable transcript (.vtt, .srt) or OCR sidecar (.ocr.json) next to it

## Limitations

- Unconfirmed matches are candidates found by term overlap; they can miss paraphrases and misread context.
- Only human reviews (reviews.jsonl) can mark a memory supported or challenged.
- Hashes prove a quote exists in the scanned bytes, not that the source is authentic or current.
- Missing evidence means unverified, not false. No memory was modified.
