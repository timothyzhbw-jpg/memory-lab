# MemFalsify audit report

- Memory: `MEMORY.md` (1 file(s): `MEMORY.md`)
- Evidence root: `evidence`
- Verifier: `lexical` · as of 2026-10-10 · mode: **read_only** (no memory was modified)

| Status | Memories |
|---|---|
| CHALLENGED | 1 |
| POSSIBLE COUNTEREVIDENCE | 1 |
| NO EVIDENCE | 1 |
| POSSIBLE SUPPORT | 3 |

### CHALLENGED · `m-9efe3be5`

> The public API rate limit is 100 requests per minute.

`MEMORY.md:7` · `Remembered from meetings, calls and the whiteboard`

**Evidence**
- contradicts (confirmed by demo-reviewer) · `recordings/standup-2026-10-06.mp4#t=12.5,17` (00:00:12.500-00:00:17.000) · transcript `recordings/standup-2026-10-06.mp4.vtt:7` · sha256 `92e1e06a69ee` · media sha256 `4c4369d2dc7b` · Priya states the limit in the standup recording.
  > The public API rate limit is 60 requests

**Proposed actions** (not applied)
- `review_or_retire`: A reviewer confirmed contrary evidence; correct or remove this memory by hand.

### POSSIBLE COUNTEREVIDENCE · `m-27e874e0`

> Feature A (bulk export) is approved for the November release.

`MEMORY.md:8` · `Remembered from meetings, calls and the whiteboard`

**Evidence**
- contradicts (candidate, unconfirmed) · `scans/whiteboard.png#page=1&xywh=40,120,780,38` · OCR `scans/whiteboard.png.ocr.json:12` · sha256 `554f2fba044f` · media sha256 `c50b7ba5f6c2` · opposing terms: claim 'approv…' vs source 'reject…'
  > Feature A (bulk export): rejected for the November release

**Proposed actions** (not applied)
- `verify_counterevidence`: Possible contrary evidence; confirm or dismiss it in reviews.jsonl.

### NO EVIDENCE · `m-801377ea`

> The payments team owns the refund flow.

`MEMORY.md:12` · `Remembered from meetings, calls and the whiteboard`

**Proposed actions** (not applied)
- `attach_evidence`: No evidence found; add a source or label this memory unverified.

### POSSIBLE SUPPORT · `m-e2afa1eb`

> Bulk export waits for the billing schema migration.

`MEMORY.md:9` · `Remembered from meetings, calls and the whiteboard`

**Evidence**
- supports (candidate, unconfirmed) · `recordings/standup-2026-10-06.mp4#t=17.5,21.25` (00:00:17.500-00:00:21.250) · transcript `recordings/standup-2026-10-06.mp4.vtt:12` · sha256 `92e1e06a69ee` · media sha256 `4c4369d2dc7b` · source restates the claim (term coverage 1.00)
  > <v Sam>Bulk export waits for the billing schema migration.

**Proposed actions** (not applied)
- `confirm_support`: Possible supporting evidence; confirm it to mark this memory supported.

### POSSIBLE SUPPORT · `m-6c336206`

> The on-call rotation changes every Monday.

`MEMORY.md:10` · `Remembered from meetings, calls and the whiteboard`

**Evidence**
- supports (candidate, unconfirmed) · `scans/whiteboard.png#page=1&xywh=40,180,520,34` · OCR `scans/whiteboard.png.ocr.json:21` · sha256 `554f2fba044f` · media sha256 `c50b7ba5f6c2` · source restates the claim (term coverage 1.00)
  > On-call rotation changes every Monday

**Issues**
- overgeneralization: absolute wording 'every'

**Proposed actions** (not applied)
- `confirm_support`: Possible supporting evidence; confirm it to mark this memory supported.
- `narrow_scope`: State the cases the evidence actually covers.

### POSSIBLE SUPPORT · `m-d44722e0`

> The retry budget is 3 attempts per request.

`MEMORY.md:11` · `Remembered from meetings, calls and the whiteboard`

**Evidence**
- supports (candidate, unconfirmed) · `calls/customer-call-2026-10-02.srt#t=2,6.5` (00:00:02.000-00:00:06.500) · transcript `calls/customer-call-2026-10-02.srt:3` · sha256 `bde7788fdf3f` · no media file named or found · source restates the claim (term coverage 1.00)
  > Customer: Your retry budget is 3 attempts per request, right?

**Proposed actions** (not applied)
- `confirm_support`: Possible supporting evidence; confirm it to mark this memory supported.

## Skipped evidence

- `recordings/demo.mov`: media file: no usable transcript (.vtt, .srt) or OCR sidecar (.ocr.json) next to it
- `recordings/retro-2026-09-30.mp4`: media file: no usable transcript (.vtt, .srt) or OCR sidecar (.ocr.json) next to it
- `recordings/retro-2026-09-30.srt`: SubRip transcript: stale: its source_sha256 does not match retro-2026-09-30.mp4; the media file changed after the sidecar was made

## Limitations

- Unconfirmed matches are candidates found by term overlap; they can miss paraphrases and misread context.
- Only human reviews (reviews.jsonl) can mark a memory supported or challenged.
- Hashes prove a quote exists in the scanned bytes, not that the source is authentic or current.
- Missing evidence means unverified, not false. No memory was modified.
- Transcript and OCR quotes show what a sidecar says about a media file, not what was said or shown; a matching source_sha256 ties the sidecar to the media file's bytes, not to an accurate reading.
