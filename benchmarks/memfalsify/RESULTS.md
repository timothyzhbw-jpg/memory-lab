# MemFalsify lexical results

Measured locally on 2026-10-11 after independent Qwen/Grok label audits and lead adjudication,
using MemFalsify 0.3.0 with the Chinese lexical rules (unreleased, see the [changelog](../../CHANGELOG.md)).
Reproduce from the repository root:

```bash
.venv/bin/python benchmarks/memfalsify/run.py --json benchmarks/memfalsify/results-lexical.json
```

[Complete predictions and exact numbers](results-lexical.json) · [Cases and metric definitions](README.md)

These 139 synthetic claims comprise 48 contradicted, 44 supported and 47 unsupported labels.
They exercise known failure modes; the scores do not estimate accuracy on real repositories.
Truth labels are based only on case evidence. Wording labels describe semantic issues independently
of the detectors (including paraphrase duplicates). This is an offline lexical run only.
The benchmark follows linked memory files by default; the GitHub Action audits only explicitly named files.

Contradiction recall is 16/48 and support recall is 7/44. Chinese contradiction recall is 5/19;
negation traps falsely flag 3/30 claims. Two of the 16 correct contradiction flags do not cite the
labeled passage, so a correct flag still needs a human read. Sentence splitting and term preselection
limit what the verifier sees, especially for paraphrases and conclusions requiring multiple sentences.
Elapsed time below is the local audit duration, excluding installation and label validation.

## Chinese lexical rules: before and after

The lexical verifier now reads Chinese numerals, units, percentages and dates as numbers, folds
full-width forms, measures a Chinese claim's coverage per character through bigrams and trigrams,
reads negation per clause (skipping words such as 未来, 非常 and 无论, and a clause-final 非…不可) and
compares modality (所有 against 仅, 必须 against 无需). A year or year-month agrees with a longer calendar
date in that period, in both languages. The labels did not change.

"Before" is the previous `results-lexical.json` (MemFalsify 0.3.0 without these rules, which scored
the same as 0.1.0); "after" is the file linked above. English is the `en-*` and `repo-*` cases,
Chinese the `zh-*` cases (the claims tagged `chinese`); each row sums that language's per-case
metrics from the JSON. False flags are flagged claims among those not labeled contradicted.

| Language | Run | Claims | Contradiction P | Contradiction R | Contradiction F1 | Support P | Support R | False flags | 3-way accuracy | judge() calls |
|---|---|---|---|---|---|---|---|---|---|---|
| English | before | 88 | 0.85 | 11/29 | 0.52 | 0.44 | 4/29 | 2/59 | 0.50 (44/88) | 97 |
| English | after | 88 | 0.85 | 11/29 | 0.52 | 0.44 | 4/29 | 2/59 | 0.50 (44/88) | 97 |
| Chinese | before | 51 | 0.25 | 2/19 | 0.15 | n/a | 0/15 | 6/32 | 0.33 (17/51) | 32 |
| Chinese | after | 51 | 0.56 | 5/19 | 0.36 | 0.75 | 3/15 | 4/32 | 0.43 (22/51) | 39 |

Every English contradiction and support prediction, rationale and cited passage is identical to the
previous run. `internal_conflict` finds 7 of 18 labeled issues (6 before these rules). 12 are reported
(11 before). The English issue that a bare year no longer flags is "shut down at the end of June 2026"
against the 2026-02-10 decision: `2026` agrees with `2026-02-10`. The two Chinese issues added by clause
negation and modality remain.

What changed for Chinese claims:

- Newly flagged, correctly: 新服务一律要接入统一 API 网关 (the source's 暂不强制 clause is about services),
  封版期间禁止任何上线 (禁止 against 允许, now inside the coverage threshold) and the CTO-approval claim
  (无需 CTO 签字; 不仅 is no longer read as a negation).
- Newly supported, correctly: the 8-second timeout, 一成 matched to 10%, and 免费用户也可以使用基础模板,
  which 无论 no longer flags as negated. The NPS claim is no longer flagged because of 非常.
- 报表首屏加载的目标是三秒以内 stays flagged, now for the right reason (3 against 2 seconds) instead of
  the 未 in 未来.
- Two unsupported distractors moved the wrong way. 企业版月费将上调到 399 元 is newly flagged: the
  专业版 price passage shares 版月费 and 上调, and 399 differs from 129, the same mistake an English
  claim would get. 免费版的存储额度会提高到 5GB is now predicted supported instead of flagged: the
  passage's 没有形成结论 sits in a clause that shares no term with the claim.

Most remaining Chinese errors are paraphrases (获批 for 已采纳, 冻结招聘 for 不招), multi-sentence evidence and
superseded plans, which no lexical rule reads.

- Verifier: `lexical` · min_coverage 0.5 · max_judged unbounded
- Cases: 8 (en-decisions-platform, en-decisions-product, en-decisions-security, repo-instructions-cli, repo-instructions-sdk, zh-decisions-platform, zh-decisions-process, zh-decisions-roadmap) · claims: 139
- Cost: 136 judge() calls · 0.1 s

## Contradiction detection

Flagged = predicted contradicted or disputed (the statuses that fail `--strict`); positive = labeled contradicted.

| Precision | Recall | F1 | TP | FP | FN |
|---|---|---|---|---|---|
| 0.73 | 0.33 | 0.46 | 16 | 6 | 32 |

Disputed predictions by label: contradicted 0, supported 0, unsupported 0 (total 0)

## Support detection

| Precision | Recall | F1 | TP | FP | FN |
|---|---|---|---|---|---|
| 0.54 | 0.16 | 0.25 | 7 | 6 | 37 |

## 3-way accuracy

0.47 (66/139; disputed counts as wrong)

| Label \ predicted | contradicted | supported | unsupported | disputed |
|---|---|---|---|---|
| contradicted | 16 | 4 | 28 | 0 |
| supported | 4 | 7 | 33 | 0 |
| unsupported | 2 | 2 | 43 | 0 |

Detections citing the labeled passage: contradicted 14/16 (0.88), supported 7/7 (1.00)

## Issue detection

| Issue | Labeled | Predicted | Precision | Recall |
|---|---|---|---|---|
| overgeneralization | 16 | 15 | 0.87 | 0.81 |
| relative_time | 13 | 9 | 1.00 | 0.69 |
| possibly_stale | 6 | 6 | 1.00 | 1.00 |
| inference_marker | 10 | 11 | 0.91 | 1.00 |
| internal_conflict | 18 | 12 | 0.58 | 0.39 |
| duplicate | 7 | 5 | 1.00 | 0.71 |

## By tag

| Tag | Claims | Contradiction recall | Support recall | False flags |
|---|---|---|---|---|
| paraphrase | 52 | 2/12 (0.17) | 5/40 (0.12) | 2/40 (0.05) |
| numeric | 44 | 5/18 (0.28) | 3/18 (0.17) | 1/26 (0.04) |
| antonym | 8 | 4/8 (0.50) | – | – |
| negation | 14 | 6/14 (0.43) | – | – |
| negation_trap | 30 | – | 1/16 (0.06) | 3/30 (0.10) |
| distractor | 56 | 4/16 (0.25) | 1/1 (1.00) | 2/40 (0.05) |
| temporal | 18 | 3/13 (0.23) | 1/5 (0.20) | 2/5 (0.40) |
| chinese | 51 | 5/19 (0.26) | 3/15 (0.20) | 4/32 (0.12) |
| repo | 47 | 3/14 (0.21) | 2/17 (0.12) | 2/33 (0.06) |
| multi_sentence | 13 | 1/2 (0.50) | 1/11 (0.09) | 0/11 (0.00) |

## By case

| Case | Claims | Accuracy | Contradiction P / R | Support P / R | judge() calls |
|---|---|---|---|---|---|
| en-decisions-platform | 17 | 0.47 | 1.00 / 0.43 | 0.00 / 0.00 | 19 |
| en-decisions-product | 17 | 0.41 | 0.75 / 0.50 | 0.00 / 0.00 | 27 |
| en-decisions-security | 16 | 0.75 | 1.00 / 0.50 | 0.60 / 0.60 | 14 |
| repo-instructions-cli | 21 | 0.48 | 0.67 / 0.33 | n/a / 0.00 | 16 |
| repo-instructions-sdk | 17 | 0.41 | 1.00 / 0.17 | 1.00 / 0.17 | 21 |
| zh-decisions-platform | 16 | 0.44 | 0.50 / 0.17 | 1.00 / 0.25 | 10 |
| zh-decisions-process | 17 | 0.53 | 0.60 / 0.43 | n/a / 0.00 | 11 |
| zh-decisions-roadmap | 18 | 0.33 | 0.50 / 0.17 | 0.67 / 0.29 | 18 |

## Misclassified claims

| Case | Label | Predicted | Tags | Claim |
|---|---|---|---|---|
| en-decisions-platform | supported | unsupported | paraphrase, numeric | Writes were unavailable for roughly 11 minutes during the Postgres 16 upgrade. |
| en-decisions-platform | contradicted | unsupported | numeric, distractor | Production database backups run every 6 hours and are kept for 35 days. |
| en-decisions-platform | supported | unsupported | paraphrase, multi_sentence | We picked Amazon's managed Kafka service to replace RabbitMQ. |
| en-decisions-platform | contradicted | supported | temporal | Marcus Oyelaran owns the MSK migration. |
| en-decisions-platform | supported | unsupported | paraphrase, temporal | Hannah Ilves is the DRI for the move from RabbitMQ to MSK. |
| en-decisions-platform | contradicted | unsupported | numeric, temporal, distractor | The public API allows 1,200 requests per minute per API key. |
| en-decisions-platform | supported | unsupported | repo, numeric, paraphrase, multi_sentence | Per-client-IP traffic is capped at 1200 requests per minute. |
| en-decisions-platform | supported | unsupported | paraphrase | Platform on-call shifts last one week. |
| en-decisions-platform | contradicted | supported | numeric | Every service must move to Vault by 2026-12-31. |
| en-decisions-product | supported | unsupported | paraphrase, numeric | Annual billing on the Team plan is cheaper than paying monthly. |
| en-decisions-product | unsupported | supported | distractor, numeric | Route optimization v2 cut average route time by 12%. |
| en-decisions-product | supported | unsupported | repo, paraphrase | Driver tipping is turned on only for US drivers. |
| en-decisions-product | contradicted | unsupported | antonym, distractor | The customer ETA widget was approved at the 2026-08-21 roadmap review. |
| en-decisions-product | supported | unsupported | paraphrase | Offline mode was cut from the 4.0 mobile release. |
| en-decisions-product | supported | unsupported | paraphrase, negation_trap | Mobile 4.0 will not include offline mode. |
| en-decisions-product | supported | contradicted | temporal | 2026-02-10: we decided to sunset the legacy CSV importer. |
| en-decisions-product | contradicted | unsupported | temporal, paraphrase | The legacy CSV importer was shut down at the end of June 2026. |
| en-decisions-product | contradicted | unsupported | negation, distractor | Customers can no longer export routes as KML. |
| en-decisions-product | supported | unsupported | negation_trap, paraphrase | GPX files can no longer be exported. |
| en-decisions-security | supported | unsupported | paraphrase, numeric | Aggregated route history is retained for just over a year. |
| en-decisions-security | contradicted | supported | paraphrase, multi_sentence | SAML SSO is mandatory for every Enterprise workspace. |
| en-decisions-security | contradicted | supported | antonym, distractor | SCIM provisioning was approved for the Business plan. |
| en-decisions-security | supported | unsupported | paraphrase, numeric | Cancelling the Geoloom contract requires 60 days' notice. |
| repo-instructions-cli | contradicted | unsupported | repo, numeric | Fernhook requires Python 3.11 or newer. |
| repo-instructions-cli | supported | unsupported | repo, numeric, paraphrase | Python 3.10 is the oldest version fernhook supports. |
| repo-instructions-cli | contradicted | unsupported | repo, distractor | just test runs ruff and pyright before the test suite. |
| repo-instructions-cli | supported | unsupported | repo, paraphrase, multi_sentence | just ci runs the linter, the type checker and the tests, in that order. |
| repo-instructions-cli | supported | unsupported | repo, numeric, paraphrase | The docs server listens on 127.0.0.1 port 8000. |
| repo-instructions-cli | contradicted | unsupported | repo, negation, distractor | A normal pytest run rewrites HTTP cassettes. |
| repo-instructions-cli | supported | unsupported | repo, numeric, paraphrase, multi_sentence | push uploads changed notes with 8 pages in parallel by default. |
| repo-instructions-cli | supported | unsupported | repo, numeric, paraphrase, negation_trap, multi_sentence | status never writes files, and it exits with code 1 when something changed. |
| repo-instructions-cli | supported | contradicted | repo, paraphrase, negation_trap | FERNHOOK_TOKEN overrides the token in the config file. |
| repo-instructions-cli | supported | unsupported | repo, paraphrase | The default ignore patterns are .obsidian/*, *.tmp and drafts/*. |
| repo-instructions-cli | contradicted | unsupported | repo | The publish step runs for every tag. |
| repo-instructions-sdk | contradicted | unsupported | repo, numeric, temporal | The SDK requires Python 3.10 or newer. |
| repo-instructions-sdk | supported | unsupported | repo, numeric, paraphrase | Python 3.11 is the oldest version the SDK supports. |
| repo-instructions-sdk | contradicted | unsupported | repo, distractor | make test runs ruff and mypy before the test suite. |
| repo-instructions-sdk | supported | unsupported | repo, numeric, paraphrase, multi_sentence | The nightly live job seems to run at 03:00 UTC. |
| repo-instructions-sdk | contradicted | unsupported | repo, numeric, temporal, paraphrase | The client retries a failed request 3 times before giving up. |
| repo-instructions-sdk | contradicted | unsupported | repo, numeric, negation | The client retries on any 5xx response. |
| repo-instructions-sdk | supported | unsupported | repo, paraphrase, negation_trap | The retry loop parses Retry-After as a number of seconds. |
| repo-instructions-sdk | contradicted | unsupported | repo, paraphrase | Catching QuaysideError handles every failure the SDK can raise. |
| repo-instructions-sdk | supported | unsupported | repo, numeric, paraphrase, multi_sentence | Release 1.8.0, shipped on 2026-02-03, added retries for HTTP 429. |
| repo-instructions-sdk | supported | unsupported | repo, paraphrase, negation_trap | Never import requests in the SDK package. |
| zh-decisions-platform | supported | unsupported | paraphrase, multi_sentence, chinese | 主库从 MySQL 换成 PostgreSQL 的方案已经获批。 |
| zh-decisions-platform | contradicted | unsupported | temporal, chinese | 数据库切换定在今年第四季度。 |
| zh-decisions-platform | unsupported | contradicted | distractor, repo, chinese | 订单服务的数据库连接池上限是 80。 |
| zh-decisions-platform | supported | unsupported | paraphrase, numeric, negation_trap, repo, chinese | 生产发布先切 5% 流量，观察半小时没问题再全量。 |
| zh-decisions-platform | contradicted | unsupported | antonym, temporal, distractor, chinese | 生产环境出错时会自动回滚。 |
| zh-decisions-platform | supported | unsupported | paraphrase, negation_trap, chinese | 这次支付超时事故没有造成资金损失。 |
| zh-decisions-platform | contradicted | unsupported | numeric, temporal, repo, chinese | 支付接口的超时时间是 3 秒。 |
| zh-decisions-platform | contradicted | unsupported | negation, repo, chinese | 支付服务对所有接口都会重试两次。 |
| zh-decisions-platform | contradicted | unsupported | numeric, paraphrase, chinese | 应用日志只保存三个月。 |
| zh-decisions-process | supported | unsupported | paraphrase, negation_trap, chinese | 所有采购都需要财务审批。 |
| zh-decisions-process | contradicted | unsupported | negation, paraphrase, chinese | 提交采购申请时要附三家供应商的报价。 |
| zh-decisions-process | contradicted | unsupported | numeric, paraphrase, distractor, chinese | 代码合并前至少需要两人审查。 |
| zh-decisions-process | supported | unsupported | paraphrase, negation_trap, multi_sentence, chinese | 改动支付模块的代码非得两个人评审通过才能合并。 |
| zh-decisions-process | supported | contradicted | paraphrase, negation_trap, chinese | 第四季度不招前端了。 |
| zh-decisions-process | contradicted | unsupported | negation, paraphrase, chinese | 远程办公的日子可以自己选，没有固定的到岗日。 |
| zh-decisions-process | supported | contradicted | numeric, temporal, chinese | 2025年12月18日：2026年研发预算定为1200万元。 |
| zh-decisions-process | contradicted | unsupported | paraphrase, chinese | 云资源超支估计主要是 GPU 训练任务导致的。 |
| zh-decisions-roadmap | contradicted | unsupported | temporal, numeric, chinese | 批量导出会跟随 11 月版本一起上线。 |
| zh-decisions-roadmap | supported | unsupported | paraphrase, negation_trap, multi_sentence, chinese | 批量导出上线前需要先过隐私评审。 |
| zh-decisions-roadmap | supported | unsupported | paraphrase, temporal, chinese | 2026年3月2日：协作空间推迟到第三季度。 |
| zh-decisions-roadmap | contradicted | unsupported | negation, temporal, chinese | 协作空间目前还在内测，尚未正式发布。 |
| zh-decisions-roadmap | contradicted | unsupported | distractor, negation, numeric, chinese | 新版首页的移动端灰度比例是 10%。 |
| zh-decisions-roadmap | supported | unsupported | paraphrase, negation_trap, chinese | 断网的时候也能新建报表草稿。 |
| zh-decisions-roadmap | contradicted | unsupported | paraphrase, numeric, distractor, chinese | 老客户从2026年11月1日起也要按 129 元付费。 |
| zh-decisions-roadmap | unsupported | contradicted | distractor, negation_trap, chinese | 企业版月费将上调到 399 元。 |
| zh-decisions-roadmap | unsupported | supported | distractor, negation_trap, chinese | 免费版的存储额度会提高到 5GB。 |
| zh-decisions-roadmap | supported | unsupported | paraphrase, negation_trap, chinese | 接下来两个季度的工作重点是报表加载性能。 |
| zh-decisions-roadmap | contradicted | unsupported | antonym, chinese | 路线图评审批准了第三方插件市场。 |
| zh-decisions-roadmap | supported | unsupported | paraphrase, numeric, negation_trap, chinese | 客户对新版筛选器的评价很好，NPS 涨到了 42。 |
