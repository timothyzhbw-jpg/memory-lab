# MemFalsify lexical results

Measured locally on 2026-11-05 with MemFalsify 0.5.0, after the lexical rules became stricter ([what they are now](../../docs/lexical-rules.md)). The labels did not change: the original eight cases keep the labels from the
independent Qwen/Grok audits and lead adjudication, and the three cases added after that
(`en-units-scope`, `zh-units-negation`, `es-operacion-alcance`) received an independent Codex audit
of all 63 claims before their first run: it added 12 missing `internal_conflict` tags and clarified 6 ambiguous
claims together with their labels; no verdict was changed for the verifier's sake. Reproduce from the repository root:

```bash
.venv/bin/python benchmarks/memfalsify/run.py --json benchmarks/memfalsify/results-lexical.json
```

[Complete predictions and exact numbers](results-lexical.json) · [Cases and metric definitions](README.md) · [The same verifier on real repositories](wild/README.md)

These 202 synthetic claims comprise 81 contradicted, 72 supported and 49 unsupported labels.
They exercise known failure modes; the scores do not estimate accuracy on real repositories.
Truth labels are based only on case evidence. Wording labels describe semantic issues independently
of the detectors (including paraphrase duplicates). This is an offline lexical run only.
The benchmark follows linked memory files by default; the GitHub Action audits only explicitly named files.

Contradiction recall is 36/81 and support recall is 17/72. Chinese contradiction recall is 13/29;
negation traps falsely flag 2/40 claims. 3 of the 36 correct contradiction flags do not cite the
labeled passage, so a correct flag still needs a human read. Sentence splitting and term preselection
limit what the verifier sees, especially for paraphrases and conclusions that need more than one sentence.
Elapsed time below is the local audit duration, excluding installation and label validation.

Against the previous rules (precision 0.80, recall 0.41, F1 0.54, with 33 correct flags and 8 false ones), these verdicts flipped:

| Case | Claim | Label | Was | Now | Why |
|---|---|---|---|---|---|
| en-units-scope | The billing archive holds 500 GiB. | contradicted | supported | contradicted | new unit rule: 500 GiB against 500 GB |
| es-operacion-alcance | Los índices de búsqueda ocupan 512 GB en el nivel caliente. | contradicted | supported | contradicted | new unit rule |
| es-operacion-alcance | El archivo de facturas ocupa 500 GiB. | contradicted | supported | contradicted | new unit rule |
| es-operacion-alcance | La confirmación de un pedido puede tardar hasta 2 minutos. | contradicted | unsupported | contradicted | new unit rule |
| zh-units-negation | 下单接口的返回时限是 2 分钟。 | contradicted | unsupported | contradicted | new unit rule |
| zh-units-negation | 检索索引的热数据占了 512 GB。 | contradicted | supported | contradicted | new unit rule |
| en-decisions-product | 2026-02-10: we decided to sunset the legacy CSV importer. | supported | contradicted | supported | a date is no longer compared with a percentage |
| en-units-scope | A search query that returned HTTP 200 must not be retried. | supported | contradicted | unsupported | two status codes in different statements |
| repo-instructions-cli | FERNHOOK_TOKEN overrides the token in the config file. | supported | contradicted | supported | the negation reached another word |
| zh-decisions-platform | 订单服务的数据库连接池上限是 80。 | unsupported | contradicted | unsupported | the clauses are too different |
| zh-units-negation | 检索索引的热数据有半个太比字节。 | supported | contradicted | unsupported | no shared word next to the numbers |
| en-decisions-security | The 2026-07-30 tile outage was caused by an expired TLS certificate. | contradicted | contradicted | unsupported | lost: the flag was luck, a date against a time on an unrelated line |
| es-operacion-alcance | El renderizado de facturas no se puede reintentar. | contradicted | contradicted | unsupported | lost: Spanish has no stopwords here, so the long passage covers too little of the claim |
| repo-instructions-cli | On a conflict, `push` always keeps the wiki page and never uploads the local note. | contradicted | contradicted | unsupported | lost: the source is a help string in code, where negation no longer counts |

- Verifier: `lexical` · min_coverage 0.5 · max_judged unbounded
- Cases: 11 (en-decisions-platform, en-decisions-product, en-decisions-security, en-units-scope, es-operacion-alcance, repo-instructions-cli, repo-instructions-sdk, zh-decisions-platform, zh-decisions-process, zh-decisions-roadmap, zh-units-negation) · claims: 202
- Cost: 255 judge() calls · 0.1 s

## Contradiction detection

Flagged = predicted contradicted or disputed (the statuses that fail `--strict`); positive = labeled contradicted.

| Precision | Recall | F1 | TP | FP | FN |
|---|---|---|---|---|---|
| 0.92 | 0.44 | 0.60 | 36 | 3 | 45 |

Disputed predictions by label: contradicted 0, supported 0, unsupported 0 (total 0)

## Support detection

| Precision | Recall | F1 | TP | FP | FN |
|---|---|---|---|---|---|
| 0.63 | 0.24 | 0.34 | 17 | 10 | 55 |

## 3-way accuracy

0.48 (97/202; disputed counts as wrong)

| Label \ predicted | contradicted | supported | unsupported | disputed |
|---|---|---|---|---|
| contradicted | 36 | 6 | 39 | 0 |
| supported | 2 | 17 | 53 | 0 |
| unsupported | 1 | 4 | 44 | 0 |

Detections citing the labeled passage: contradicted 33/36 (0.92), supported 16/17 (0.94)

## Issue detection

| Issue | Labeled | Predicted | Precision | Recall |
|---|---|---|---|---|
| overgeneralization | 19 | 17 | 0.88 | 0.79 |
| relative_time | 14 | 10 | 1.00 | 0.71 |
| possibly_stale | 8 | 8 | 1.00 | 1.00 |
| inference_marker | 11 | 12 | 0.92 | 1.00 |
| internal_conflict | 47 | 26 | 0.85 | 0.47 |
| duplicate | 15 | 5 | 1.00 | 0.33 |

## By tag

| Tag | Claims | Contradiction recall | Support recall | False flags |
|---|---|---|---|---|
| paraphrase | 78 | 2/12 (0.17) | 14/66 (0.21) | 1/66 (0.02) |
| numeric | 90 | 25/47 (0.53) | 7/35 (0.20) | 1/43 (0.02) |
| antonym | 8 | 4/8 (0.50) | – | – |
| negation | 17 | 7/17 (0.41) | – | – |
| negation_trap | 40 | – | 5/24 (0.21) | 2/40 (0.05) |
| distractor | 75 | 14/33 (0.42) | 1/1 (1.00) | 1/42 (0.02) |
| temporal | 29 | 5/17 (0.29) | 3/12 (0.25) | 1/12 (0.08) |
| chinese | 71 | 13/29 (0.45) | 5/24 (0.21) | 3/42 (0.07) |
| repo | 47 | 2/14 (0.14) | 3/17 (0.18) | 0/33 (0.00) |
| multi_sentence | 21 | 1/5 (0.20) | 1/16 (0.06) | 0/16 (0.00) |

## By case

| Case | Claims | Accuracy | Contradiction P / R | Support P / R | judge() calls |
|---|---|---|---|---|---|
| en-decisions-platform | 17 | 0.47 | 1.00 / 0.43 | 0.00 / 0.00 | 19 |
| en-decisions-product | 17 | 0.47 | 1.00 / 0.50 | 0.50 / 0.17 | 27 |
| en-decisions-security | 16 | 0.69 | 1.00 / 0.25 | 0.60 / 0.60 | 14 |
| en-units-scope | 23 | 0.48 | 1.00 / 0.69 | 1.00 / 0.20 | 45 |
| es-operacion-alcance | 20 | 0.45 | 1.00 / 0.50 | 0.67 / 0.44 | 38 |
| repo-instructions-cli | 21 | 0.48 | 1.00 / 0.17 | 1.00 / 0.14 | 16 |
| repo-instructions-sdk | 17 | 0.41 | 1.00 / 0.17 | 1.00 / 0.17 | 21 |
| zh-decisions-platform | 16 | 0.50 | 1.00 / 0.17 | 1.00 / 0.25 | 10 |
| zh-decisions-process | 17 | 0.53 | 0.60 / 0.43 | n/a / 0.00 | 11 |
| zh-decisions-roadmap | 18 | 0.33 | 0.50 / 0.17 | 0.67 / 0.29 | 18 |
| zh-units-negation | 20 | 0.50 | 1.00 / 0.80 | 0.50 / 0.22 | 36 |

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
| en-decisions-product | contradicted | unsupported | temporal, paraphrase | The legacy CSV importer was shut down at the end of June 2026. |
| en-decisions-product | contradicted | unsupported | negation, distractor | Customers can no longer export routes as KML. |
| en-decisions-product | supported | unsupported | negation_trap, paraphrase | GPX files can no longer be exported. |
| en-decisions-security | supported | unsupported | paraphrase, numeric | Aggregated route history is retained for just over a year. |
| en-decisions-security | contradicted | supported | paraphrase, multi_sentence | SAML SSO is mandatory for every Enterprise workspace. |
| en-decisions-security | contradicted | supported | antonym, distractor | SCIM provisioning was approved for the Business plan. |
| en-decisions-security | contradicted | unsupported | distractor | The 2026-07-30 tile outage was caused by an expired TLS certificate. |
| en-decisions-security | supported | unsupported | paraphrase, numeric | Cancelling the Geoloom contract requires 60 days' notice. |
| en-units-scope | supported | unsupported | paraphrase, numeric | Search p99 must stay within a quarter of a second. |
| en-units-scope | supported | unsupported | paraphrase, numeric | The search latency budget at the 99th percentile is 250 milliseconds. |
| en-units-scope | contradicted | unsupported | numeric, distractor | The billing service's p99 latency budget is a quarter of a second. |
| en-units-scope | contradicted | unsupported | numeric | A checkout write may take up to 2 minutes to return. |
| en-units-scope | contradicted | unsupported | numeric, multi_sentence | The billing service is probably allowed a smaller share of failed requests each month than search. |
| en-units-scope | supported | unsupported | paraphrase, numeric | The hot tier holds half a tebibyte of search indexes. |
| en-units-scope | supported | unsupported | paraphrase, numeric | Search indexes on the hot tier take up 512 GiB. |
| en-units-scope | supported | unsupported | numeric, temporal, multi_sentence | 105 days passed between the production debut of release 2.3.1, on 2026-05-20, and the replacement by release 2.4.0. |
| en-units-scope | contradicted | unsupported | numeric, temporal, multi_sentence | Only 30 days passed between the production debut of release 2.3.1, on 2026-05-20, and the replacement by release 2.4.0. |
| en-units-scope | supported | unsupported | paraphrase, negation_trap | A search query that returned HTTP 200 must not be retried. |
| en-units-scope | supported | unsupported | paraphrase, negation_trap, temporal, multi_sentence | On 2026-07-22, ADR-030 was accepted with a rule forbidding clients to retry search queries after HTTP 200. |
| en-units-scope | supported | unsupported | paraphrase | Retrying a billing invoice render is permitted solely when the first try responded with HTTP 503. |
| es-operacion-alcance | supported | unsupported | paraphrase, numeric | La latencia p99 de la búsqueda tiene que mantenerse dentro de un cuarto de segundo. |
| es-operacion-alcance | contradicted | unsupported | numeric, distractor | El presupuesto p99 de facturación es un cuarto de segundo. |
| es-operacion-alcance | supported | unsupported | paraphrase, numeric | El presupuesto mensual de errores de la búsqueda es una décima parte del uno por ciento. |
| es-operacion-alcance | contradicted | unsupported | numeric, distractor | La búsqueda puede fallar en el 1 por ciento de las peticiones cada mes. |
| es-operacion-alcance | supported | unsupported | paraphrase, negation_trap, temporal, multi_sentence | El 2026-07-22 se aceptó la ADR-015 con una regla que prohíbe reintentar consultas de búsqueda después de un HTTP 200. |
| es-operacion-alcance | contradicted | unsupported | negation, distractor | El renderizado de facturas no se puede reintentar. |
| es-operacion-alcance | supported | unsupported | paraphrase | Reintentar el renderizado de una factura está permitido únicamente cuando el primer intento contestó con HTTP 503. |
| es-operacion-alcance | unsupported | supported | negation_trap, distractor | El turno de búsqueda no debe reconocer una alerta de facturación. |
| es-operacion-alcance | contradicted | supported | distractor | Cualquier persona del turno de búsqueda puede silenciar una alerta de búsqueda. |
| es-operacion-alcance | supported | unsupported | numeric, temporal, multi_sentence | Pasaron 105 días entre el debut en producción de la versión 2.3.1, el 2026-05-20, y la sustitución por la versión 2.4.0. |
| es-operacion-alcance | contradicted | unsupported | numeric, temporal, multi_sentence | Solo pasaron 30 días entre el debut en producción de la versión 2.3.1, el 2026-05-20, y la sustitución por la versión 2.4.0. |
| repo-instructions-cli | contradicted | unsupported | repo, numeric | Fernhook requires Python 3.11 or newer. |
| repo-instructions-cli | supported | unsupported | repo, numeric, paraphrase | Python 3.10 is the oldest version fernhook supports. |
| repo-instructions-cli | contradicted | unsupported | repo, distractor | just test runs ruff and pyright before the test suite. |
| repo-instructions-cli | supported | unsupported | repo, paraphrase, multi_sentence | just ci runs the linter, the type checker and the tests, in that order. |
| repo-instructions-cli | supported | unsupported | repo, numeric, paraphrase | The docs server listens on 127.0.0.1 port 8000. |
| repo-instructions-cli | contradicted | unsupported | repo, negation, distractor | A normal pytest run rewrites HTTP cassettes. |
| repo-instructions-cli | supported | unsupported | repo, numeric, paraphrase, multi_sentence | push uploads changed notes with 8 pages in parallel by default. |
| repo-instructions-cli | contradicted | unsupported | repo, negation | On a conflict, push always keeps the wiki page and never uploads the local note. |
| repo-instructions-cli | supported | unsupported | repo, numeric, paraphrase, negation_trap, multi_sentence | status never writes files, and it exits with code 1 when something changed. |
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
| zh-units-negation | supported | unsupported | paraphrase, numeric, chinese | 检索的九九分位时延必须压在四分之一秒以内。 |
| zh-units-negation | supported | unsupported | paraphrase, numeric, chinese | 检索服务每月的错误预算是请求量的千分之一。 |
| zh-units-negation | contradicted | unsupported | numeric, distractor, chinese | 检索服务每个月允许百分之一的请求失败。 |
| zh-units-negation | supported | unsupported | paraphrase, numeric, chinese | 检索索引的热数据有半个太比字节。 |
| zh-units-negation | supported | unsupported | paraphrase, numeric, chinese | 检索索引的热数据占用五百一十二吉比字节。 |
| zh-units-negation | contradicted | supported | numeric, chinese | 账单归档有 500 GiB。 |
| zh-units-negation | supported | unsupported | paraphrase, temporal, numeric, chinese | 2026年3月2日容量组曾打算让检索热数据保留三十天。 |
| zh-units-negation | supported | unsupported | paraphrase, negation_trap, chinese | 已经返回 HTTP 200 的检索查询不得再发一次。 |
| zh-units-negation | supported | unsupported | paraphrase, negation_trap, temporal, multi_sentence, chinese | 2026年7月22日，ADR-011 获得通过，其中规定返回 HTTP 200 的检索查询不得重试。 |
| zh-units-negation | unsupported | supported | negation_trap, distractor, chinese | 检索值班不得确认账单告警。 |
