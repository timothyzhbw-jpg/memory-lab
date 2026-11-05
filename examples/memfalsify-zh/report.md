# MemFalsify audit report

- Memory: `MEMORY.md` (1 file(s): `MEMORY.md`)
- Evidence root: `evidence`
- Verifier: `lexical` · as of 2026-10-10 · mode: **read_only** (no memory was modified)

| Status | Memories |
|---|---|
| POSSIBLE COUNTEREVIDENCE | 3 |
| NO EVIDENCE | 3 |
| POSSIBLE SUPPORT | 1 |

### POSSIBLE COUNTEREVIDENCE · `m-124e4d91`

> 公开 API 的限流是每分钟 100 次请求。

`MEMORY.md:11` · `基础设施`

**Evidence**
- contradicts (candidate, unconfirmed) · `文档/api.md:3` · sha256 `ab042ee74c57` · different values: claim ['100'] vs source ['60']
  > 公开 API 的限流是每分钟 60 次请求。
  - Does not bear on this claim? Add to reviews.jsonl: `{"memory_id": "m-124e4d91", "relation": "unrelated", "path": "文档/api.md", "quote": "公开 API 的限流是每分钟 60 次请求。", "reviewer": "you"}`

**Proposed actions** (not applied)
- `verify_counterevidence`: Possible contrary evidence; in reviews.jsonl, confirm it with relation contradicts, or dismiss it with relation unrelated.

### POSSIBLE COUNTEREVIDENCE · `m-54b237eb`

> 功能A（批量导出）已批准，随11月版本发布。

`MEMORY.md:5` · `产品`

**Evidence**
- contradicts (candidate, unconfirmed) · `记录/2026-10-07-决策记录.md:3` · sha256 `faf3d9030f65` · opposing terms: claim '批准…' vs source '拒绝…'
  > 产品经理拒绝了功能A（批量导出），不随11月版本发布。
  - Does not bear on this claim? Add to reviews.jsonl: `{"memory_id": "m-54b237eb", "relation": "unrelated", "path": "记录/2026-10-07-决策记录.md", "quote": "产品经理拒绝了功能A（批量导出），不随11月版本发布。", "reviewer": "you"}`

**Proposed actions** (not applied)
- `verify_counterevidence`: Possible contrary evidence; in reviews.jsonl, confirm it with relation contradicts, or dismiss it with relation unrelated.

### POSSIBLE COUNTEREVIDENCE · `m-df062c08`

> 部署总是先经过预发环境。

`MEMORY.md:12` · `基础设施`

**Evidence**
- contradicts (candidate, unconfirmed) · `记录/2026-09-12-事故复盘.md:3` · sha256 `b6c4a629ea08` · same subject, but only one side is negated
  > 为了修复支付回调，值班同学直接把热修复部署到生产环境，没有经过预发环境。
  - Does not bear on this claim? Add to reviews.jsonl: `{"memory_id": "m-df062c08", "relation": "unrelated", "path": "记录/2026-09-12-事故复盘.md", "quote": "为了修复支付回调，值班同学直接把热修复部署到生产环境，没有经过预发环境。", "reviewer": "you"}`

**Issues**
- overgeneralization: absolute wording '总是'

**Proposed actions** (not applied)
- `verify_counterevidence`: Possible contrary evidence; in reviews.jsonl, confirm it with relation contradicts, or dismiss it with relation unrelated.
- `narrow_scope`: State the cases the evidence actually covers.

### NO EVIDENCE · `m-da94e0ad`

> 批量导出下周上线。

`MEMORY.md:6` · `产品`

**Issues**
- relative_time: '下周' has no absolute date

**Proposed actions** (not applied)
- `attach_evidence`: No evidence found; add a source or label this memory unverified.
- `add_absolute_date`: Replace relative time with the date it refers to.

### NO EVIDENCE · `m-cdd26fbb`

> 2026年3月2日：功能B推迟到第三季度。

`MEMORY.md:7` · `产品`

**Issues**
- possibly_stale: latest date 2026-03-02 is over 180 days before 2026-10-10

**Proposed actions** (not applied)
- `attach_evidence`: No evidence found; add a source or label this memory unverified.
- `reverify`: Re-check this against current sources.

### NO EVIDENCE · `m-3e341aa9`

> 我们可能使用 squash 合并 PR。

`MEMORY.md:14` · `基础设施`

**Issues**
- inference_marker: '可能' marks an inference, not an observation

**Proposed actions** (not applied)
- `attach_evidence`: No evidence found; add a source or label this memory unverified.
- `label_as_inference`: Keep it explicitly marked as an inference, or verify it.

### POSSIBLE SUPPORT · `m-23f6337d`

> 主数据库使用 PostgreSQL 16。

`MEMORY.md:13` · `基础设施`

**Evidence**
- supports (candidate, unconfirmed) · `文档/数据库.md:3` · sha256 `097ce7847644` · source restates the claim (term coverage 1.00)
  > 主数据库使用 PostgreSQL 16，每天凌晨备份。

**Proposed actions** (not applied)
- `confirm_support`: Possible supporting evidence; confirm it to mark this memory supported.

## Limitations

- Unconfirmed matches are candidates found by comparing words: support by term overlap, a contradiction by the same setting with another value or two near-identical statements that differ. They can miss paraphrases and misread context, and a report without flags has not ruled anything out.
- Only human reviews (reviews.jsonl) can mark a memory supported or challenged.
- Hashes prove a quote exists in the scanned bytes, not that the source is authentic or current.
- Missing evidence means unverified, not false. No memory was modified.
