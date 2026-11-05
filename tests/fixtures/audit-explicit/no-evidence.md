# MemFalsify audit report

- Memory: `MEMORY.md` (2 file(s): `MEMORY.md`, `infra.md`)
- Evidence root: none supplied
- Verifier: `lexical` · as of 2026-10-10 · mode: **read_only** (no memory was modified)

| Status | Memories |
|---|---|
| NO EVIDENCE | 10 |

### NO EVIDENCE · `m-27e874e0`

> Feature A (bulk export) is approved for the November release.

`MEMORY.md:7` · `Product decisions`

**Issues**
- internal_conflict: may contradict m-8b09698a at MEMORY.md:8: "Feature A was rejected by the product manager."

**Proposed actions** (not applied)
- `attach_evidence`: No evidence found; add a source or label this memory unverified.
- `resolve_internal_conflict`: Two memories disagree; at most one can stay as written.

### NO EVIDENCE · `m-8b09698a`

> Feature A was rejected by the product manager.

`MEMORY.md:8` · `Product decisions`

**Issues**
- internal_conflict: may contradict m-27e874e0 at MEMORY.md:7: "Feature A (bulk export) is approved for the November release."

**Proposed actions** (not applied)
- `attach_evidence`: No evidence found; add a source or label this memory unverified.
- `resolve_internal_conflict`: Two memories disagree; at most one can stay as written.

### NO EVIDENCE · `m-d4ec01d6`

> Bulk export depends on the billing schema migration.

`MEMORY.md:9` · `Product decisions`

**Issues**
- duplicate: also stated at infra.md:9

**Proposed actions** (not applied)
- `attach_evidence`: No evidence found; add a source or label this memory unverified.
- `deduplicate`: Keep one copy so future edits cannot diverge.

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

### NO EVIDENCE · `m-2c4e9e63`

> We probably use squash merges for pull requests.

`MEMORY.md:16` · `Team`

**Issues**
- inference_marker: 'probably' marks an inference, not an observation

**Proposed actions** (not applied)
- `attach_evidence`: No evidence found; add a source or label this memory unverified.
- `label_as_inference`: Keep it explicitly marked as an inference, or verify it.

### NO EVIDENCE · `m-9efe3be5`

> The public API rate limit is 100 requests per minute.

`infra.md:6`

**Proposed actions** (not applied)
- `attach_evidence`: No evidence found; add a source or label this memory unverified.

### NO EVIDENCE · `m-f0a6035e`

> The main database runs PostgreSQL 15.

`infra.md:7`

**Proposed actions** (not applied)
- `attach_evidence`: No evidence found; add a source or label this memory unverified.

### NO EVIDENCE · `m-6d879fbb`

> Deployments always go through the staging cluster.

`infra.md:8`

**Issues**
- overgeneralization: absolute wording 'always'

**Proposed actions** (not applied)
- `attach_evidence`: No evidence found; add a source or label this memory unverified.
- `narrow_scope`: State the cases the evidence actually covers.

## Limitations

- Unconfirmed matches are candidates found by comparing words: support by term overlap, a contradiction by the same setting with another value or two near-identical statements that differ. They can miss paraphrases and misread context, and a report without flags has not ruled anything out.
- Only human reviews (reviews.jsonl) can mark a memory supported or challenged.
- Hashes prove a quote exists in the scanned bytes, not that the source is authentic or current.
- Missing evidence means unverified, not false. No memory was modified.
