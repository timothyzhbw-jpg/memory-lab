# 架构

[English](architecture.md) | 中文

**MemFalsify 问一条记忆有没有证据，orgmemory 问是谁放进去的、有没有这个权限，因为写入记忆本身就是一次组织决策。**

Agent 的 `MEMORY.md` 里多一行，它相信的东西就变了，所以这一行和其他决策一样：有人提议，有人批准，而且只有一部分人有权批准。MemFalsify 发现某一行有反证或没有证据时，接下来要问的就是这行是谁放进去的。只有当初把写入记录成一次决策，这个问题才有答案。

[闭环演示](../examples/closed-loop/README.zh-CN.md)用真实的命令行工具和合成数据，把这一页讲的内容完整跑一遍。

## 逐步看这条链

`记住 → 验证 → 质疑 → 裁决 → 演进`

| 步骤 | 发生了什么 | MemFalsify | orgmemory |
|---|---|---|---|
| **记住** | 一行内容进入 `MEMORY.md` | 之后由 `history` 指出引入它的提交、作者和提交标题 | `request` 和 `approve` 事件记录这次写入；回放时按执行者当时持有的角色判断每个事件 |
| **验证** | 这一行有证据支撑吗？ | `audit` 把每条声明和证据对照，报告状态并引用原文 | 不参与 |
| **质疑** | 一条发现变成抛给组织的问题 | 报告和其中的建议操作，只有人才会去执行 | 一个 `raise_risk` 或 `comment` 事件；`audit` 和 `explain` 显示谁提议、谁批准、有没有权限 |
| **裁决** | 有权限的人作决定 | 人可以用 `reviews.jsonl` 确认或否定证据 | 这个人的 `approve`、`reject` 或 `revoke`；别人做同样的动作只会被记录并忽略 |
| **演进** | 人修改记忆，这一行得到新 ID | `diff` 和 `history` 显示变化；`rollback` 打印补丁，由人决定是否应用 | 新的一行是新议题；旧议题保留它的决定；`changes` 和 `export` 报告这些内容 |

```text
 记住    一次提交把一行加进 MEMORY.md
  |
  v
 验证    memfalsify audit --format json     finding：memory_id、text、location、status、evidence
         memfalsify history --format json   claim：memory_id、text、location、introduced
  |
  v
 质疑    to_events.py
           -> orgmemory evidence add        evidence.jsonl
           -> orgmemory append              events.jsonl：request、approve、raise_risk、comment
  |
  v
 裁决    orgmemory audit 和 explain         谁提议、谁批准、有没有权限、还有什么没解决
         orgmemory append                   有权限的人：approve、reject 或 revoke
  |
  v
 演进    人修改 MEMORY.md 并提交；新的一行回到“验证”
```

MemFalsify 从不写入事实，也从不修改记忆：它只写报告和建议。orgmemory 的回放和审计是只读的，`append` 只追加。两个工具之间唯一的东西是一次格式转换：[`to_events.py`](../examples/closed-loop/to_events.py)，一个只用标准库的脚本，读 MemFalsify 的 JSON，打印 orgmemory 命令需要的内容。

## 从发现到事件，逐字段对应

### 一次记忆写入：每行两个事件

来自 `memfalsify history --format json` 的 `claims[]`：

| history 字段 | 事件字段 | 规则 |
|---|---|---|
| `memory_id` | `subject` | `memory:<memory_id>` |
| `memory_id` | `id` | `write-request-<memory_id>` 和 `write-approve-<memory_id>` |
| `introduced.author` | `request` 的 `actor` | 提议这一行的人 |
| `introduced.subject` | `approve` 的 `actor` | 标题末尾 `(approved-by: NAME)` 里的名字。没有这个名字就是作者本人：没有别人批准就把一行合入，等于自己批准了自己 |
| `introduced.date` | 两个事件的 `timestamp` | 提交的作者时间 |
| `location`、`text` | `request` 的 `note` | `Memory write at <location>: <text>` |
| `replaced.memory_id` | `request` 的 `note` | 这一行替换了更早的版本时，加上 `(edits <memory_id>)` |
| `location`、`introduced` | 两个事件的 `evidence_ids` | `<memory_id>-line` 引用记忆里的这一行；`<memory_id>-introduced` 引用 Markdown 版 history 里这条声明的 `Introduced` 行 |

没有提交可以指认的行（比扫描范围更早，或还没提交）不产生写入事件，脚本会在 stderr 上说明。

批准记成 `approve`，和合并一个 pull request 记成 `approve` 是同一个道理：[`github_map.py`](../packages/orgmemory/src/orgmemory/github_map.py) 把 GitHub 的合并映射成 `approve`。

### 一条发现：一个事件，或者没有

来自 `memfalsify audit --format json` 的 `findings[]`：

| finding 字段 | 事件字段 | 规则 |
|---|---|---|
| `memory_id` | `subject` | `memory:<memory_id>` |
| `memory_id`、`status` | `id` | `finding-<status>-<memory_id>`，所以之后结果相同的审计不会再添加 |
| `status` | `action` | 见下表 |
| `evidence[].reviewer` | `actor` | 第一个确认证据的审阅人；否则是 `memfalsify`，`policy.json` 给它一个可以提出风险和评论的角色 |
| `status`、`location`、`text` | `note` | `<status> at <location>: <text>` |
| `evidence[].span` | `evidence_ids` | 每个 span 一条证据记录，支持性的 span 除非状态是 `conflicted` 否则不计。`insufficient_evidence` 没有 span，记录引用记忆里的这一行 |
| 报告的 `as_of` | `timestamp` | 当天结束时刻（UTC），晚于当天的任何提交 |

| 状态 | 动作 | 原因 |
|---|---|---|
| `challenged` | `raise_risk` | 审阅人确认了相反的证据 |
| `conflicted` | `raise_risk` | 已确认的证据两边都有 |
| `candidate_counterevidence` | `raise_risk` | 可能的反证：一条留给人的线索 |
| `disputed` | `raise_risk` | 验证器意见不一 |
| `insufficient_evidence` | `comment` | 没有证据表示未经验证，不表示是假的，所以状态保持原样 |
| `candidate_support` | 无 | 没有可质疑的 |
| `supported` | 无 | 没有可质疑的 |

没有哪条发现会变成 `approve`、`reject` 或 `revoke`。这些留给有权限的人。

### 一条证据记录

来自 finding 里的 `span`，交给 `orgmemory evidence add`：

| 字段 | 选项 | 规则 |
|---|---|---|
| `span.path`、报告的 `evidence_root` | `--path` | 先核对文件字节和 `span.sha256`，再复制到数据集的 `sources/<head>/<path>` |
| `span.quote` | `--quote` | 原样不变；orgmemory 对副本重新计算哈希并重新定位引文 |
| `span.sha256`、`span.start` | `--id` | `<memory_id>-<哈希前 8 位>-<start>` |
| history 的 `head` | `--source-uri` | `git:<head>:<path>` |
| 固定值 | `--source-kind` | `evidence_file`、`memory_file` 或 `memory_history` |
| 报告的 `as_of` | `--captured-at` | 和事件时间相同 |

### 走一遍一行的完整过程

审计报告（有删节）：

```json
{"memory_id": "m-9efe3be5", "text": "The public API rate limit is 100 requests per minute.", "location": "MEMORY.md:5", "status": "candidate_counterevidence", "evidence": [{"relation": "contradicts", "confirmed": false, "reviewer": null, "span": {"path": "api.md", "quote": "The public API rate limit is 60 requests per minute."}}]}
```

history 指出了提交：作者 `agent-7`，标题 `Remember the API rate limit (approved-by: wu)`。于是得到三个事件：

```json
{"id": "write-request-m-9efe3be5", "timestamp": "2026-10-07T09:00:00Z", "actor": "agent-7", "action": "request", "subject": "memory:m-9efe3be5", "evidence_ids": ["m-9efe3be5-line", "m-9efe3be5-introduced"], "note": "Memory write at MEMORY.md:5: The public API rate limit is 100 requests per minute."}
{"id": "write-approve-m-9efe3be5", "timestamp": "2026-10-07T09:00:00Z", "actor": "wu", "action": "approve", "subject": "memory:m-9efe3be5", "evidence_ids": ["m-9efe3be5-line", "m-9efe3be5-introduced"]}
{"id": "finding-candidate_counterevidence-m-9efe3be5", "timestamp": "2026-10-10T23:59:59Z", "actor": "memfalsify", "action": "raise_risk", "subject": "memory:m-9efe3be5", "evidence_ids": ["m-9efe3be5-f5a6a83d-14"], "note": "candidate_counterevidence at MEMORY.md:5: The public API rate limit is 100 requests per minute."}
```

`wu` 的角色不能批准，所以 `orgmemory audit` 报告一条 `unauthorized_attempts`，这一行仍是 `proposed`，继续等 `memory_steward` 批准（[报告](../examples/closed-loop/expected/3-orgmemory-audit.md)）。同样的发现落在 `zhou` 有权批准的那一行上，结果就不一样：报告的是 `risk_after_approval`，因为当初的决定没有问题，是事实变了。

## 每个工具单独能做什么

相互独立是有意的设计。`memfalsify` 和 `orgmemory` 都依赖 `memlab-core`，彼此都不 import 对方，所以可以只装其中一个。

- **单独用 MemFalsify**：对照一个证据目录审计记忆或指令文件；有 git 时还能显示哪些声明变了、来自哪个提交。它没有角色、批准或事件日志的概念，问题止于“这有证据吗”。
- **单独用 orgmemory**：在角色策略下回放任何决策日志，比如产品功能、GitHub pull request、Jira issue。它没有记忆文件的概念。议题可以是任意字符串，所以 `memory:m-9efe3be5` 不需要改动它就能用。
- **memcore** 是两者共用的部分：证据是文件已知范围内的一段精确引文，并记录文件的 SHA-256。

两者合起来回答了单独哪一个都回答不了的问题：这一行可能是错的，这是放它进来的人，这是他有没有权限。

## orgmemory 要不要增加记忆写入事件类型？

两个选择：给 orgmemory 增加 `memory_write` 和 `memory_approve` 动作；或者保留现有的八个动作，在格式层完成连接。

**决定：保留现有事件模型。** 连接只是一次格式转换和这份文档。

代码层面的依据：

- 回放没有新的事情要做。它用 `permitted[event.action]` 检查每个事件，把议题当作不透明的字符串（[`replay.py`](../packages/orgmemory/src/orgmemory/replay.py)）。演示需要的行为都来自未改动的代码：被忽略的批准、待批准状态、批准之后的风险、有权限的否决。`memory_approve` 的状态转换和 `approve` 完全相同。
- 新动作牵涉很广，却没有带来新状态。[`models.py`](../packages/orgmemory/src/orgmemory/models.py) 里的 `Action`、`DECISIONS` 和 `Decision.action`，`replay.py` 里的 `_ACTIONS` 和各个分支，`audit.py` 里的 `periods` 和各类发现，`changes.py`，`export.py`，`memlab_ui/decisions.py` 里的文案表，公开 API 快照，还有每一份 `policy.json`，它的权限就是按动作命名的。
- 旧版本会读不了。`Event.action` 和 `Policy.permissions` 的键都是封闭集合，改动之前发布的 orgmemory 会拒绝含新动作的日志。日志可信靠的正是这些保证，只有真正需要时才该动。
- 导入工具已经这么做了。GitHub 的合并和评审、Jira 的状态变化，都映射到同一组动作上。
- 这样两个工具才能保持独立。新动作会把记忆文件的知识带进 orgmemory 的核心。

这个决定的代价：

- **权限按动作划分，不按议题种类划分。** 同一份日志里，能批准记忆写入的人也能批准产品决策。演示把记忆写入放在单独的数据集里，用单独的策略。
- **被修改的行和它早先版本之间的联系只是一段文字。** 修改会让这一行得到新的 `memory_id`，也就成了新议题；旧 ID 写在新 `request` 的 `note` 里，回放不读它。
- **`to_events.py` 是示例，不是有人维护的包。** 有测试用真实的命令行工具运行它，但你要把它复制到自己的环境里，由自己负责。

什么情况会改变这个决定：

1. 同一份日志必须同时放记忆写入和其他决策，审批人又不同，而分开数据集确实太别扭。这时按议题前缀划分权限，或者增加新动作，才值得。
2. 记忆写入需要 `request`、`approve`、`reject`、`revoke` 表达不了的状态，比如“已退役，由另一行取代”，而且回放必须据此行动。
3. 大家经常运行这个转换。这样才值得有一个维护中的小型导出包，它自成一个包，两个工具依然独立。这本身不会改变事件模型。

## 局限

- **名字只是声明。** git 作者名和提交标题里的 `(approved-by: NAME)` 都是任何人都能写的文字。orgmemory 检查的是所写名字的角色，不是这个人真的做过。证据记录证明这段文字在报告里，不证明它是真的。签名提交或抓取到的 pull request 评审是更强的来源，这个转换没有检查它们。`(approved-by: NAME)` 只是演示用来代替你们团队记录批准的方式。
- **候选只是线索。** 用默认的词法验证器时，`candidate_counterevidence` 表示字面上看起来矛盾。只有人的审阅才能把记忆标成 `challenged`，`memfalsify` 提出的 `raise_risk` 是留给人的问题。
- **历史有边界。** `memfalsify history` 沿第一父提交走，所以合并提交带来的改动算在合并提交头上；比 `--max-commits` 更早的行没有作者可以指认。
- **风险会一直留在记录里。** 日志没有解除风险的事件，所以这一行被否决或改正之后，风险仍然列着。
- **这里没有覆盖的。** 演示的证据都是纯文本。转录和 OCR 证据走同样的命令，但演示没有覆盖。
