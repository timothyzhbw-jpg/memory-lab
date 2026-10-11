# Memory Lab：让 AI Agent 的记忆可验证

[English](README.md) | 中文

现在的 Agent 记忆工具大多只管"存"和"取"。这个项目关心的是**记住的东西是不是真的**：每条记忆都应该有证据、有时间、能追溯来源；没有权限的信息，不应该改变当前状态。

`记住 → 验证 → 质疑 → 裁决 → 演进`

项目包含两个相互独立的工具，共用一个证据核心：

| | **MemFalsify** | **可验证的组织记忆**（`orgmemory`） |
|---|---|---|
| 回答的问题 | *这条记忆有证据支撑吗？* | *这是当前的、经过授权的状态吗？* |
| 输入 | `MEMORY.md` + 一个证据文件目录 | 事件日志 + 角色权限策略 + 证据清单 |
| 输出 | 审计报告：引用原文、发现的问题、建议操作 | 每个议题的当前状态、决策来源、审计轨迹 |
| 会写入什么 | 不写入任何东西（只输出你要求的报告） | 回放时不写入；`append` 只追加 |

默认完全离线运行，不需要任何 API key；可以选择开启模型验证（Claude 和 TypeSafe AI 的 Jev）。需要 Python 3.10+，许可证 Apache-2.0。

```text
        Claude Code / Codex / 其他 Agent  （skills/）
                          │
          ┌───────────────┴───────────────┐
          ▼                               ▼
     MemFalsify                      orgmemory
  记忆有没有证据支撑              组织决策的状态与权限
          └───────────────┬───────────────┘
                          ▼
           memcore：带哈希、逐字引用的证据
```

## 快速上手

```bash
git clone https://github.com/timothyzhbw-jpg/memory-lab && cd memory-lab
python -m venv .venv && source .venv/bin/activate
pip install -e packages/memcore -e packages/memfalsify -e packages/orgmemory
```

按需安装即可：`memcore` 加上任意一个工具就能单独使用。需要模型验证时再装 `pip install -e 'packages/memfalsify[all]'`。

### MemFalsify：审计一份中文 MEMORY.md

```bash
cd examples/memfalsify-zh
memfalsify audit MEMORY.md --evidence evidence --as-of 2026-10-10
```

示例记忆里有 7 条关于一个虚构服务的说法，证据目录里是决策记录、事故复盘和技术文档。审计结果（[完整报告](examples/memfalsify-zh/report.md)）：

| 记忆 | 结果 | 原因 |
|---|---|---|
| 功能A（批量导出）已批准，随11月版本发布。 | 可能有反证 | 决策记录写的是"产品经理**拒绝**了功能A"，"批准"与"拒绝"互斥 |
| 公开 API 的限流是每分钟 100 次请求。 | 可能有反证 | `文档/api.md:3` 写的是 60 |
| 部署总是先经过预发环境。 | 可能有反证、说得太绝对 | 事故复盘记录了一次"没有经过预发环境"的热修复；"总是"是绝对化措辞 |
| 批量导出下周上线。 | 无证据、相对时间 | "下周"是相对哪一天？ |
| 2026年3月2日：功能B推迟到第三季度。 | 无证据、可能过期 | 距今超过 180 天 |
| 我们可能使用 squash 合并 PR。 | 无证据、推测性措辞 | "可能"说明这是推测，不是观察 |
| 主数据库使用 PostgreSQL 16。 | 可能有支持 | `文档/数据库.md:3` 原文复述了这一点 |

每条引用都带 `路径:行号`、字符偏移量和来源文件的 SHA-256。

中文支持是内置的：中文分词（按二字词组匹配）、中文否定词（不、没、未、无……）、中文反义词对（批准/拒绝、启用/禁用、允许/禁止……）、`2026年3月2日` 这样的中文日期，以及"总是、所有、一定""下周、最近、目前""可能、大概、好像"这类措辞。英文示例在 [`examples/memfalsify`](examples/memfalsify/)，其中还演示了人工确认（`reviews.jsonl`）和被跳过的图片证据。

**状态是怎么定的。** MemFalsify 会自己找候选证据，但只有人工确认才能给一条记忆下结论：

| 状态 | 含义 |
|---|---|
| `supported` / `challenged` / `conflicted` | `reviews.jsonl` 中经人工确认的证据：支持 / 反驳 / 两者都有 |
| `candidate_counterevidence` | 验证器找到了可能反驳这条记忆的段落（词法层面：反义词如"批准/拒绝"、数字不一致、或只有一方是否定句） |
| `disputed` | 两个验证器意见不一致（见下文"验证后端"） |
| `candidate_support` | 找到了复述这条记忆的段落 |
| `insufficient_evidence` | 什么都没找到。这表示"未经验证"，不表示"是错的" |

不论有没有证据，每条记忆都会检查：`overgeneralization`（绝对化措辞）、没有日期的 `relative_time`（相对时间）、`possibly_stale`（日期过旧）、`inference_marker`（推测性措辞）、与其他记忆的 `internal_conflict`（内部矛盾），以及 `duplicate`（重复）。

`verify_counterevidence`、`add_absolute_date`、`narrow_scope` 这类建议操作只是建议。`replacement` 字段永远是空的：MemFalsify 从不替你编写事实。

其他命令和选项：`memfalsify claims MEMORY.md` 列出每条记忆的 ID；`--format json`、`--output report.md`、`--strict`（有需要关注的记忆时退出码为 1，适合 CI）、`--stale-days`、`--no-follow-links`。

### 组织记忆：回放决策

```bash
orgmemory replay examples/orgmemory/feature-a
```

| 时间 | 谁（角色） | 事件 |
|---|---|---|
| 周一 | 客户 | 提出需要功能A |
| 周二 | 工程师 | 提出技术风险 |
| 周三 | 产品经理 | **拒绝**功能A |
| 周四 | 客户 | 再次要求功能A |
| 周四 | 客户经理 | 发消息说"功能A已批准" |

结果（[完整报告](examples/orgmemory/replay-report.md)）：

```yaml
subject: Feature A
current_status: rejected
reconsideration_requested: true
pending_approval: [product_manager]
open_risks: ["Bulk export reads the billing schema, which is mid-migration"]
last_authorized_decision: ev-wed-reject (reject by zhou)
unauthorized_change_attempts: [ev-thu-sales-approve]
```

周四更新的消息没有覆盖周三的决定。客户的再次要求变成了"申请重新考虑"，等待产品经理处理。客户经理的"批准"被记录下来但不生效，因为权限策略不允许这个角色做批准。

**最新的消息不等于当前的状态**，这是 orgmemory 的核心原则。

其他命令：

```bash
orgmemory explain  <数据集> "Feature A"      # 为什么处于当前状态，附带引用的证据
orgmemory replay   <数据集> --as-of 2026-10-07T23:59:59Z   # 某个历史时间点的状态
orgmemory validate <数据集>                  # 校验每个事件、哈希和引用
orgmemory evidence add <数据集> --id ... --path ... --quote "..." --source-uri ... --source-kind ... --captured-at ...
orgmemory append   <数据集> --event '{"id": ..., ...}'
```

## 数据格式

**`reviews.jsonl`**（MemFalsify，可选）：人工确认的判断。

```json
{"memory_id": "m-54b237eb", "relation": "contradicts", "path": "记录/2026-10-07-决策记录.md", "quote": "产品经理拒绝了功能A（批量导出），不随11月版本发布。", "reviewer": "张三", "note": "可选"}
```

`memory_id` 由记忆的规范化文本生成。记忆一旦被修改，ID 就会变，旧的确认随之失效，必须重新确认。`quote` 必须逐字出现在 `path` 指向的文件中，`path` 相对于证据目录。

**组织数据集**：一个包含三个文件的目录。

- `events.jsonl`：每行一个事件，只追加。字段：`id`、`timestamp`（带时区，表示生效时间）、`actor`、`action`、`subject`、`evidence_ids`（至少一个），以及 `note`（`raise_risk` 必填）、`dependency`（`dependency` 必填）、`supersedes`（`revoke` 必填，必须指向当前决定）。动作：`request`、`reconsider`、`raise_risk`、`dependency`、`approve`、`reject`、`revoke`、`comment`。
- `policy.json`：`{"actors": {人: [角色]}, "permissions": {动作: [角色]}}`。不在名单里的人没有任何角色。`pending_approval` 列出既能批准又能拒绝的角色。
- `evidence.jsonl`：`id`、`path`、原始字节的 `sha256`、逐字的 `quote` 及其 `[start, end)` 字符区间、`source_uri`、`source_kind`、`captured_at`。请用 `orgmemory evidence add` 生成，不要手算哈希。

回放规则：事件按生效时间排序，时间相同时保持文件顺序。回放前会校验整个日志，包括 `--as-of` 之后的事件。有权限的 `approve` / `reject` 会设定状态，并清除"申请重新考虑"、待审批和被忽略的越权尝试。决定被推翻、或同一时刻出现相互冲突的决定，都会记录在 `conflicts` 中。

## Agent 技能

[`skills/memfalsify`](skills/memfalsify/SKILL.md) 和 [`skills/orgmemory`](skills/orgmemory/SKILL.md) 教 Agent 怎么用这两个工具、边界在哪里：先出报告，得到许可才改记忆，绝不自己给自己做人工确认，绝不改权限策略。把它们复制到 Agent 的技能目录即可：

```bash
cp -r skills/memfalsify skills/orgmemory ~/.claude/skills/   # Claude Code
cp -r skills/memfalsify skills/orgmemory ~/.codex/skills/    # Codex
```

命令行工具需要安装在 Agent 的 shell 所用的环境里。

## 验证后端：词法、LLM、Jev

验证器判断一条记忆和一段证据的关系：`supports`（支持）、`contradicts`（矛盾）或 `unrelated`（无关），并给出理由。内置三个后端，`dual` 和 `cascade` 是两个模型后端的组合方式：

| `--verifier` | 谁来判断 | 需要什么 | 取舍 |
|---|---|---|---|
| `lexical`（默认） | 词语重合、反义词、数字、否定 | 无 | 离线、结果确定、可解释；识别不了换种说法的表述 |
| `llm` | Claude 阅读证据并写出判断理由（`--llm-model`，默认 `claude-opus-5-5`） | `pip install 'memfalsify[llm]'` 和 Anthropic 凭据 | 能理解换说法和上下文；按调用计费，**会把记忆和证据原文发给 Anthropic API** |
| `jev` | [Jev](https://docs.typesafe.ai)：TypeSafe AI 的判断模型，只做决策不生成文字，在三种关系中选一个并给出各自的概率（`--jev-model`，默认 `jev-latest`） | `pip install 'memfalsify[jev]'` 和 `TYPESAFE_API_KEY` | 成本只有 LLM 评委的零头，带校准过的置信度；没有文字解释，**会把记忆和证据原文发给 TypeSafe API** |
| `dual` | `llm` 和 `jev` 各自独立判断 | 两者都要 | 意见一致就照常输出；不一致就标为 `disputed`，报告里列出双方意见 |
| `cascade` | 先问 `jev`；只有 Jev 的置信度低于 `--accept-at`（默认 0.8）时才问 Claude | 两者都要 | Jev 大多有把握时，成本接近只用 Jev；报告里会同时显示 Jev 没把握的意见和 Claude 的判断。Jev 有把握的答案不会再被交叉核对 |

```bash
memfalsify audit MEMORY.md --evidence evidence --verifier dual
```

两个模型后端刻意采用不同的工作方式：Claude 推理出结论并写明原因，Jev 直接输出带概率的决策。两者拿到的是同一套关系定义（`verify.py` 中的 `RELATION_CRITERIA`），所以它们意见不一致时，分歧来自证据本身，而不是题目措辞，这时就该由人去读证据。Jev 置信度低于 `JevVerifier(threshold=0.5)` 的答案按 `unrelated` 处理；理由中会保留三个概率和实际使用的模型版本（例如 `jev-1.13.0`，因为 `jev-latest` 只是别名）。

**模型会看到哪些段落。** 仍然先用词语索引预筛选。模型验证器的门槛更宽（`--min-coverage`，默认 0.2，而词法验证是 0.5），这样换了说法的段落也能被判断；每条记忆最多只送 `--max-judged` 段（默认 8）。因此每个模型的调用次数上限是"记忆条数 × max-judged"。和记忆没有任何共同词语的段落永远不会被送去判断。

LLM 验证器使用结构化输出，每个判断都符合固定格式；它的指令把记忆和证据当作引用的数据，而不是指令。在支持的模型上，它会开启服务端的拒答回退；被拒答的请求记为"无判断"（`unrelated`，理由中写明停止原因）。`dual` 更严格：每个判断都核对两次。`cascade` 遵循"有把握就采纳，没把握再升级"的思路，便宜得多，但会单独信任 Jev 有把握的答案，所以正式依赖它之前，先用你已经人工确认过的记忆调一调 `--accept-at`。

两个模型后端遇到 API 调用失败或缺少密钥时，审计都会以退出码 2 结束，而不是输出一份不完整的报告。

在 Python 里使用：

```python
from memfalsify.audit import audit
from memfalsify.jev import JevVerifier
from memfalsify.llm import LLMVerifier
from memfalsify.verify import DualVerifier

report = audit("MEMORY.md", "evidence", verifier=DualVerifier(LLMVerifier(), JevVerifier()),
               min_coverage=0.2, max_judged=8)
```

任何有 `name` 属性和 `judge(claim, passage) -> Judgment` 方法的对象都可以当验证器，其他模型可以用同样的方式接入。模型的判断始终只是候选：它们永远不能代替人工确认。

## 信任边界与局限

- 哈希只能证明某段引用确实存在于特定的字节里，不能证明来源是真实的，也不能证明这段话的意思就是某人所说的那样。
- 词法和模型找到的候选只是线索，不是结论。只有 `reviews.jsonl` 能给记忆下结论。
- `--verifier llm` 会把记忆和证据发给 Anthropic API，`jev` 会发给 TypeSafe API，`dual` 和 `cascade` 两边都会发。只对允许外发的材料使用它们。
- 参与者 ID 和 `policy.json` 被视为可信输入。目前还没有身份认证、签名溯源或策略版本管理；修改策略会改变每一次回放的结果。
- 证据目前只支持 UTF-8 文本。图片、音频、视频和 PDF 会被列为"已跳过"。
- 这是一个研究用的 MVP，不是生产级基础设施。

## 路线图

| | MemFalsify | 组织记忆 |
|---|---|---|
| **v0.1** | 文本记忆审计、证据追溯、人工确认流程 | 事件日志、状态机、权限规则、决策溯源 |
| **v0.2**（进行中） | LLM + Jev 双重验证、cascade 模式 ✓ | 导入 GitHub Issues / PR（开发中） |
| v0.3 | 记忆的 Git 历史：diff、审查、回滚；跨 Agent 技能打磨 | 状态冲突与决策审计 |
| v0.4 | 多模态证据（带定位的 OCR / 转写） | 跨平台组织记忆 |
| v1.0 | 基准测试、稳定 API | 基准测试、稳定 API |

## 开发

```bash
pip install pytest
python -m pytest     # 直接针对 packages/*/src 运行，无需安装
```

目录结构：`packages/memcore`（证据完整性、JSONL）、`packages/memfalsify`、`packages/orgmemory`、`skills/`、`examples/`、`tests/`。提交贡献时请附上最小的合成复现示例。不要提交私密证据或凭据。

## 许可证

Apache-2.0，见 [LICENSE](LICENSE)。
