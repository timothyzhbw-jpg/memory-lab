# Memory Lab：让 AI Agent 的记忆可验证

[English](README.md) | 中文

现在的 Agent 记忆工具大多只管“存”和“取”。这个项目关心的是**记住的东西是不是真的**：每条记忆都应该有证据、有时间、能追溯来源；未经授权的信息不应该改变当前状态。

`记住 → 验证 → 质疑 → 裁决 → 演进`

项目包含两个相互独立的工具，共用一个证据核心：

| | **MemFalsify** | **可验证的组织记忆**（`orgmemory`） |
|---|---|---|
| 回答的问题 | 这条记忆有证据支撑吗？ | 这是当前的、经过授权的状态吗？ |
| 输入 | `MEMORY.md` + 一个证据文件目录 | 事件日志 + 角色权限策略 + 证据清单 |
| 输出 | 审计报告：引用原文、发现的问题、建议操作 | 每个议题的当前状态、决策来源、审计轨迹、决策流程审计 |
| 会写入什么 | 不写入任何东西（只输出你要求的报告） | 回放和审计时不写入；`append` 只追加 |

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
| 功能A（批量导出）已批准，随11月版本发布。 | 可能有反证 | 决策记录写的是“产品经理**拒绝**了功能A”，“批准”与“拒绝”互斥 |
| 公开 API 的限流是每分钟 100 次请求。 | 可能有反证 | `文档/api.md:3` 写的是 60 |
| 部署总是先经过预发环境。 | 可能有反证、绝对化措辞 | 事故复盘记录了一次“没有经过预发环境”的热修复；“总是”是绝对化措辞 |
| 批量导出下周上线。 | 无证据、相对时间 | “下周”是相对哪一天？ |
| 2026年3月2日：功能B推迟到第三季度。 | 无证据、可能过期 | 距今超过 180 天 |
| 我们可能使用 squash 合并 PR。 | 无证据、推测性措辞 | “可能”说明这是推测，不是观察 |
| 主数据库使用 PostgreSQL 16。 | 可能有支持证据 | `文档/数据库.md:3` 原文复述了这一点 |

每条引用都带 `路径:行号`、字符偏移量和来源文件的 SHA-256。

中文支持是内置的：中文按相邻两个汉字切分匹配（字符二元组，不做词典分词，例如“批量导出”切成“批量”“量导”“导出”）；识别中文反义词对（批准/拒绝、启用/禁用、允许/禁止……）、`2026年3月2日` 这样的中文日期，以及绝对化（总是、所有、一定）、相对时间（下周、最近、目前）和推测性（可能、大概、好像）措辞。否定按单字匹配（不、没、未、无、非、别、勿），所以“未来”“非常”里的字也会被当成否定，可能产生误报的“可能有反证”。英文示例在 [`examples/memfalsify`](examples/memfalsify/)，其中还演示了人工确认（`reviews.jsonl`）和被跳过的图片证据。

**状态是怎么定的。** MemFalsify 会自己找候选证据，但只有人工确认才能给一条记忆下结论：

| 状态 | 含义 |
|---|---|
| `supported` / `challenged` / `conflicted` | `reviews.jsonl` 中经人工确认的证据：支持 / 反驳 / 两者都有 |
| `candidate_counterevidence` | 验证器找到了可能反驳这条记忆的片段（词法层面：反义词如“批准/拒绝”、数字不一致，或只有一方是否定句） |
| `disputed` | 两个验证器意见不一致（见下文“验证后端”） |
| `candidate_support` | 找到了复述这条记忆的片段 |
| `insufficient_evidence` | 什么都没找到。这表示“未经验证”，不表示“是错的” |

不论有没有证据，每条记忆都会检查：`overgeneralization`（绝对化措辞）、没有日期的 `relative_time`（相对时间）、`possibly_stale`（可能过期）、`inference_marker`（推测性措辞）、与其他记忆的 `internal_conflict`（内部矛盾），以及 `duplicate`（重复）。

`verify_counterevidence`、`add_absolute_date`、`narrow_scope` 这类操作只是建议，不会自动执行。`replacement` 字段永远是空的：MemFalsify 从不替你编写事实。

其他命令和选项：`memfalsify claims MEMORY.md` 列出每条记忆的 ID；`--format json`、`--output report.md`、`--strict`（有需要关注的记忆时退出码为 1，适合 CI）、`--stale-days`、`--no-follow-links`。

#### 审查 git 里的记忆改动

`CLAUDE.md`、`AGENTS.md`、`MEMORY.md` 放在 git 里时，可以只看某次提交或某个 PR 改了哪些记忆，并且只审计这些：

```bash
memfalsify diff CLAUDE.md --base main --evidence .        # 与工作区比较；加 --head REV 可比较两个提交
memfalsify history CLAUDE.md --max-commits 50              # 每条记忆由哪个提交引入、最后由哪个提交修改
memfalsify rollback CLAUDE.md --claim m-1a2b3c4d --to main -o restore.patch
git apply restore.patch                                    # 先审阅补丁，再在仓库根目录应用
```

- `diff` 通过 git 读取两个版本的记忆文件及其链接的文件（不会检出任何东西），把记忆分为新增、修改（旧 → 新）、删除和未变。只审计新增和修改的记忆，证据目录按当前状态读取；`audit` 的选项都能用（`--evidence`、`--exclude`、`--reviews`、`--verifier`、`--format`、`--output`、`--strict` 等）。加 `--strict` 时，新增或修改的记忆需要关注就退出码为 1；git 出错或修订版本不存在时退出码为 2。
- 记忆按 ID 识别，所以只改大小写或空白算作未变。修改过的记忆要和旧版本配对，两者须在同一文件、同一小节（未变记忆所在标题被改名时仍算同一小节），并且词项相似度至少 50%；否则显示为一条删除加一条新增。配对结果是确定的。
- `history` 沿 `HEAD` 的第一父提交链追溯，所以通过合并提交并入的改动记在该合并提交名下；它还列出扫描范围内被删除的记忆，未提交的修改记为“uncommitted changes in the working tree”。早于扫描范围的记忆（受 `--max-commits` 限制，默认 100；或到了浅克隆的边界）会如实标为扫描范围之前，不会算到最早那个提交头上。记忆文件改名不会被追踪。
- `rollback` 从不应用任何改动：它打印一个补丁，把这条记忆恢复成 `--to` 时的措辞（所有副本都恢复，并保留原来的 markdown 标记），或把已删除的记忆重新加到它所在小节里前面最近一条仍存在的记忆之后。当前 ID 和旧 ID 都可以用。在 Windows 上请用 `-o`，因为 shell 重定向可能改变补丁的编码。

### 组织记忆：回放决策

```bash
orgmemory replay examples/orgmemory/feature-a   # 在仓库根目录运行
```

| 时间 | 谁（角色） | 事件 |
|---|---|---|
| 周一 | 客户 | 提出需要功能A |
| 周二 | 工程师 | 提出技术风险 |
| 周三 | 产品经理 | **拒绝**功能A |
| 周四 | 客户 | 再次要求功能A |
| 周四 | 客户经理 | 发消息说“功能A已批准” |

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

周四的消息虽然更晚，却没有覆盖周三的决定。客户的再次要求变成了“申请重新考虑”，等待产品经理处理。客户经理的“批准”被记录下来但不生效，因为权限策略不允许这个角色做批准。

**最新的消息不等于当前的状态**，这是 orgmemory 的核心原则。

其他命令：

```bash
orgmemory explain  <数据集> "Feature A"      # 为什么处于当前状态，附带引用的证据
orgmemory replay   <数据集> --as-of 2026-10-07T23:59:59Z   # 某个历史时间点的状态
orgmemory validate <数据集>                  # 校验每个事件、哈希和引用
orgmemory audit    <数据集> --as-of 2026-10-31T00:00:00Z   # 决策流程哪里需要关注
orgmemory evidence add <数据集> --id ... --path ... --quote "..." --source-uri ... --source-kind ... --captured-at ...
orgmemory append   <数据集> --event '{"id": ..., ...}'
```

#### 审计决策流程

`replay` 回答“现在是什么状态”，`audit` 回答“决策流程哪里需要关注”。它用同样的规则只读地回放日志，然后列出发现项。每个发现项包含 `kind`（类型）、`severity`（严重程度：`high`、`medium` 或 `low`）、所涉及的议题 `subject`（如果是针对某个参与者的发现项，则为 `actor`）、涉及的 `subjects` 和 `event_ids`（事件 ID 按回放顺序排列）、一行说明 `detail`，以及每个被引用的事件及其证据原文，引用方式与 `explain` 相同。审计不引入任何新的事件语义：它只复述回放结果、回放记录的冲突和审计轨迹里已有的内容。

```bash
orgmemory audit <数据集> [--as-of ISO8601] [--stale-days 14] [--format markdown|json] [--strict]
```

以 2026-10-31 为时间点审计示例数据（[完整报告](examples/orgmemory/audit-report.md)）：客户的“申请重新考虑”已经等了产品经理超过 14 天（`stale_reconsideration`，medium）；客户经理那次被忽略的“批准”列为 `unauthorized_attempts`（medium）。[GitHub 示例的审计报告](examples/orgmemory/github-acme-web/audit-report.md)按账号列出了长期等待权限策略下决定的请求、被忽略的审查、标签和合并，以及一位先要求修改、后又批准的审查者。等待批准反映的是权限策略下的回放结果，不是 GitHub 上的 issue 或 PR 是否仍然打开：例如，`acme/web#4` 因重复而关闭，但这次关闭记为 `comment`，请求仍在等待决定。

| 类型 | 何时报告 | 严重程度 |
|---|---|---|
| `simultaneous_decisions` | 回放记录了同一生效时间、相互冲突的有权限决定，结果由文件顺序决定 | 由文件顺序选出的决定仍是当前决定时为 high；已被后来的决定取代时为 medium |
| `decision_reversed` | 回放记录了有权限的 `reject` 之后又有有权限的 `approve`（或相反），中间没有 `revoke` | 由另一个参与者推翻时为 medium；同一参与者改变自己的决定时为 low |
| `invalid_supersession` | 回放记录了一次有权限的 `revoke`，但它指向的不是当前决定，所以没有改变任何状态。发现项还会引用它指向的事件和当时生效的决定 | medium |
| `risk_after_approval` | 议题已批准，且记录中有风险；每个风险一个发现项 | 风险在批准之后提出时为 high；在批准之前提出时为 medium，因为日志里没有“风险已解决”这种事件 |
| `unresolved_dependency` | 议题已批准，但它依赖的议题没有被批准；每个依赖一个发现项，只检查直接依赖 | 依赖被拒绝时为 high；依赖仍是 proposed 时为 medium；日志里没有这个依赖的任何事件时为 low |
| `unauthorized_attempts` | 每个参与者一个发现项：被权限策略忽略的事件，按动作计数并列出涉及的议题。`comment` 不计入，因为它从不改变状态 | 被忽略的 `approve` / `reject` / `revoke` 达到 2 次及以上为 high；1 次，或其他被忽略的事件达到 2 次及以上为 medium；只有 1 次其他被忽略的事件为 low |
| `stale_pending_approval` | 在参考时间点，`pending_approval` 已持续超过 `--stale-days`（默认 14 天），且没有进行中的“申请重新考虑”。等待从最后一次 `approve` 或 `reject` 之后的第一个 `request`、`reconsider` 或 `revoke` 开始计算 | medium |
| `stale_reconsideration` | `reconsideration_requested` 已持续超过 `--stale-days`。这样的议题不会再重复报告为 `stale_pending_approval` | medium |
| `self_approval` | 当前批准的参与者在批准之前，也对这个议题提出过已生效的 `request` 或 `reconsider` | low |

- 只计入回放实际生效的内容：没有权限的参与者提出的风险或依赖不会被记录，只会出现在 `unauthorized_attempts` 里。
- `--as-of` 和 `replay` 一样会忽略之后的事件，同时也是计算是否超期的参考时间。不加时审计整个日志，并以当前时间计算超期，所以需要可复现的报告时请加 `--as-of`。
- 发现项按严重程度、类型、议题、参与者和回放顺序排序。Markdown 报告以一张汇总表开头：每种类型一行，每个严重程度一列。`--format json` 包含 `summary`（各严重程度的数量）、`kinds`（每种类型的数量，按名称排序）和 `findings`。
- 退出码：`0`；加 `--strict` 且存在 high 级别的发现项时为 `1`；输入无效时为 `2`，与 `replay` 相同。除了退出码 `2`，其他情况都会输出报告。

#### 导入 GitHub Issues 和 Pull Request

`orgmemory github` 把一个仓库的 issue 和 pull request 转换成数据集里的事件、证据和存档文件。每个 issue 或 PR 是一个议题（subject，例如 `acme/web#12`）。导入过程是确定性的，不使用任何模型。

```bash
orgmemory github sync    <数据集> OWNER/REPO [--full | --since ISO8601] [--issue N]... [--max-wait 3600] [--save-capture f.json] [--dry-run]
orgmemory github convert <数据集> f.json [--dry-run]  # 离线应用之前保存的抓取结果
orgmemory explain        <数据集> "acme/web#12"
```

- token 取 `GITHUB_TOKEN`、`GH_TOKEN` 中第一个非空的值（可以用 `GH_TOKEN=$(gh auth token)`）；没有 `--token` 参数。一个只读的细粒度（fine-grained）token，带 Issues、Pull requests 和 Metadata 的读权限就够了。没有 token 时以未认证方式运行：只能访问公开仓库，每小时 60 次请求。
- 第一次运行读取全部内容；可以用 `--since ISO时间`（必须带时区）限定范围，但仓库一旦有了游标（完成过一次完整同步），这个参数就会被拒绝。之后每次只读取上一次完整同步以来变化的内容，并往前多重叠 5 分钟。`--full` 重新扫描全部，`--issue N`（可重复）只刷新指定编号，不能与 `--full` 或 `--since` 同用。重复运行是安全的：ID 是确定的，日志里已有的内容不会被追加第二次。GitHub 不一定会因为每种变化（标签、依赖关系）都把 issue 标为已更新，所以隔一段时间跑一次 `--full`。
- `sync` 会复用到 `api.github.com` 的 HTTPS 连接，使用系统 CA 存储校验证书，每次请求超时为 30 秒；运行结束（包括出错）时关闭连接。连接在收到任何响应字节之前失败时，会新建连接重试一次 GET；限流和其他重试行为保持原样。设置 `HTTPS_PROXY` / `https_proxy` 或 `ALL_PROXY` / `all_proxy` 时回退到原有 urllib 传输，按 urllib 的代理规则处理，不复用连接。传输层不会自动跟随重定向；客户端只允许经过校验的同源重定向。
- `sync` 抓取期间最多每 5 秒向 stderr 输出一次进度，显示已完整抓取的 issue/PR 数、请求数和剩余限额（不可用时为 `unknown`）。每次因速率限制休眠时还会输出休眠时长和 UTC 唤醒时间。短运行可能不显示进度；stdout 保持不变，包括摘要和 `--dry-run` JSONL。
- `--dry-run` 会实际请求 GitHub（消耗请求次数），打印摘要和计划写入的事件，但不写入数据集或锁文件。`--save-capture` 把抓取到的 issue 和时间线按 API 返回的原样写入一个新文件，之后可以用 `convert` 应用；它可以和 `--dry-run` 一起用，这样就能先审阅抓取结果再应用。文件里是原始的、可能私密的文本。保存路径只能是新文件，不能覆盖导入工具管理的文件或 `policy.json` / `github.json`。
- 退出码：`0` 表示完整完成（包括“没有新内容”）；`2` 表示出错，没有追加任何内容；`3` 表示只完成了一部分：限流等待超过 `--max-wait`（默认 3600 秒），或服务端、网络反复出错，导致抓取中止，已完整抓取的条目已经写入，游标没有移动，稍后再运行一次即可。
- 数据集目录必须已存在。`events.jsonl` 和 `evidence.jsonl` 会在第一次写入时创建。导入不需要 `policy.json`；`replay`、`explain` 和 `validate` 需要。

**导入工具从不读取或写入 `policy.json`：`gh:<login>:<id>` 形式的参与者 ID 对应什么角色，由你自己配置。** 账号配置角色之前，它的事件会被记录，但不会改变任何状态。同步摘要会列出新事件涉及的参与者，`orgmemory validate` 会列出所有不在 `policy.json` 里的参与者。

| GitHub | 事件 |
|---|---|
| 新建 issue 或 PR | `request` |
| 审查（review）批准 / 要求修改 | 审查者的 `approve` / `reject` |
| 批准或要求修改的审查被人撤销（dismiss） | 撤销者对该审查的 `revoke` |
| issue 以“不计划做”（not planned）关闭 | 关闭者的 `reject` |
| PR 合并 / 未合并就关闭 | 合并者的 `approve` / 关闭者的 `reject` |
| 在一次记为 `reject` 的关闭之后重新打开 / 其他重新打开 | 对该次关闭的 `revoke`，然后是 `request` / `request` |
| 向曾经要求修改的审查者重新请求审查 | `request` |
| `github.json` 里列出的标签被添加 / 移除 | 对应的 `approve`、`reject` 或 `raise_risk` / 对该标签所做决定的 `revoke`，否则记为 `comment`（风险无法撤回） |
| 添加“被阻塞于”（blocked by）关系 | `dependency` |
| 评论、只含评论的审查、其他关闭、推送新提交时自动撤销的过期审查、移除“被阻塞于”关系 | `comment` |
| 机器人、应用、Agent 或已删除账号做的任何操作 | 只记为 `comment` |
| 其他一切（指派、提及、交叉引用、其他审查请求、未列出的标签、编辑） | 不产生事件 |

文本从不被解析：任何地方出现的“LGTM”“/approve”“approved by @pm”或“depends on #3”都只是作者的一条 `comment`。被撤销的审查保留原来的结论，所以日志不取决于你什么时候同步。编辑只会增加一个新的证据版本，不产生事件；被删除的评论仍保留在数据集里，并在所属 issue 下次刷新时报告为已消失（gone）。

有权 `revoke` 的人重新打开时，议题回到 `proposed`。否则这次撤销是一次被忽略的越权尝试，后面的 `request` 只有在重新打开者的角色允许 `request` 时才会标记“申请重新考虑”。有两种预期内的噪声：没有批准权限的人合并 PR，即使之前已有授权的批准，这次合并也会被列为被忽略的越权尝试；重新打开一次并非当前决定的关闭（例如一次越权的关闭）时，如果重新打开者有权撤销，会显示 `invalid_supersession`。

示例：[`examples/orgmemory/github-acme-web`](examples/orgmemory/github-acme-web/) 是用 `orgmemory github convert` 从 `acme/web` 的两份合成抓取结果（在 `tests/fixtures/github/` 里）生成的，附有[回放报告](examples/orgmemory/github-acme-web/replay-report.md)。其中 `acme/web#1` 就是 GitHub 上的功能A故事：产品经理以“不计划做”关闭了 issue，客户经理加上了 `approved` 标签，客户又重新打开了它。这个 issue 仍是 `rejected`，`reconsideration_requested: true`，`pending_approval: [product_manager]`；标签和重新打开带来的 `revoke` 都列为被忽略的越权尝试。

## 数据格式

JSON 对象里重复的键是无效输入，嵌套对象里的重复键也一样。这适用于 `reviews.jsonl`、`events.jsonl`、`evidence.jsonl`、`github-sync.jsonl`、`policy.json`、`github.json`、交给 `orgmemory github convert` 的抓取文件、评测用的 `labels.jsonl` 和 `case.json`，以及 GitHub API 的响应。命令以退出码 2 失败，并报告 `<文件>:<行号>: invalid JSON: duplicate key`。解析不会保留后出现的那个值。

**`reviews.jsonl`**（MemFalsify，可选）：人工确认的判断。

```json
{"memory_id": "m-54b237eb", "relation": "contradicts", "path": "记录/2026-10-07-决策记录.md", "quote": "产品经理拒绝了功能A（批量导出），不随11月版本发布。", "reviewer": "张三", "note": "可选"}
```

`memory_id` 由记忆的规范化文本生成。记忆一旦被修改，ID 就会变；`reviews.jsonl` 里仍指向旧 ID 的确认会让审计直接报错（`unknown memory_id`，退出码 2），需要重新确认并更新或删除那一行。`quote` 必须逐字出现在 `path` 指向的文件中，`path` 相对于证据目录。

**组织数据集**：一个包含三个文件的目录。

- `events.jsonl`：每行一个事件，只追加。字段：`id`、`timestamp`（带时区，表示生效时间）、`actor`、`action`、`subject`、`evidence_ids`（至少一个），以及 `note`（动作为 `raise_risk` 时必填）、`dependency`（动作为 `dependency` 时必填）、`supersedes`（动作为 `revoke` 时必填，且必须指向当前生效的决定）。动作：`request`、`reconsider`、`raise_risk`、`dependency`、`approve`、`reject`、`revoke`、`comment`。
- `policy.json`：`{"actors": {人: [角色]}, "permissions": {动作: [角色]}}`。不在名单里的人没有任何角色。`pending_approval` 列出既能批准又能拒绝的角色。
- `evidence.jsonl`：`id`、`path`、原始字节的 `sha256`、逐字的 `quote` 及其 `[start, end)` 字符区间、`source_uri`、`source_kind`、`captured_at`。请用 `orgmemory evidence add` 生成，不要手算哈希。

回放规则：事件按生效时间排序，时间相同时保持文件顺序。回放前会校验整个日志，包括 `--as-of` 之后的事件。有权限的 `approve` / `reject` 会设定状态，并清除“申请重新考虑”、待审批和被忽略的越权尝试。决定被推翻或同一时刻出现相互冲突的决定时，都会记录在 `conflicts` 中。

**GitHub 导入相关文件**（在同一个数据集目录里）：

- `github.json`（可选，归你所有；导入工具只读取它）：`{"labels": {"<标签>": "approve" | "reject" | "raise_risk"}}`。标签名匹配时不区分大小写，未列出的标签不产生任何事件。修改它只影响尚未写入日志的事件（`--full` 可以补全）；已有事件如果按新配置会得到不同结果，会被报告为漂移（drift），但绝不会被改写。
- `github-sync.jsonl`：每次实际写入的运行追加一行（`--dry-run` 不写），字段有 `repo`、`repo_id`、`source`（`sync` 或 `convert`）、`mode`、`since`、`started_at`（本次运行第一个响应里服务器的 `Date`）、`finished_at`、`complete`、`items`、`requests`、`events_appended`、`evidence_appended`、`gone`、`drift` 和 `importer`。下一次的游标是最近一次完整、模式为 `full` 或 `incremental` 的 `sync` 行的 `started_at` 减 5 分钟；指定编号的、未完成的、`convert` 和 dry run 都不会移动游标。离线转换使用抓取结果的 `started_at` 作为 `finished_at`，确保可重复生成。记录下来的 `repo_id` 锁定了仓库：如果同一个名字后来对应另一个 ID（仓库被删除后重建），导入会被拒绝。仓库改名或转移同样会失败，需要有意识地重新导入。
- `sources/github/<owner>/<repo>/<n>/<对象>-<hash12>.txt`：每个 issue 或 PR、每条评论、每个审查的每个版本各一个文件，每个被映射的 issue 事件也各一个，只创建、不修改；`<对象>` 是 `issue-<n>`、`comment-<id>`、`review-<id>` 或 `event-<id>`，`<hash12>` 是文件 SHA-256 的前 12 位。第 1 行是只由校验过的字段拼成的 ASCII 事实行；第 2 行是白名单 API 字段组成的 JSON 记录（非 ASCII 字符全部转义）；第 3 行是固定的分隔行；其余是 GitHub 返回的原文（标题和正文、评论或审查正文、撤销说明），一字不改，只有孤立代理字符（lone surrogate）会被替换成 U+FFFD，并在记录里标注 `"lossy": true`。示例里 `acme/web#2` 的审查 206：

```text
GitHub review 206 on acme/web#2 by gh:zhou-pm:5003 (User) at 2026-10-07T09:00:00Z: approved on commit 54bb654c9e60
{"author_association":"MEMBER","commit_id":"54bb654c9e6025347f57900a4a5c2313a96b8035","event":"reviewed","id":206,...}
--- verbatim from GitHub; untrusted ---
Approved now that export waits for the billing migration.
```

每个事件都引用所属对象的 `fact` 证据，也就是整行事实行，其中写明的参与者与事件的参与者相同。条件允许时，事件还会引用 `text` 证据：原文部分第一个非空、且不以 `<!--` 开头的行，去掉首尾空白后截断到 200 个字符。如果评论被折叠（minimized），或这一行包含制表符以外的控制字符、双向文本控制字符（bidi）、零宽字符或行分隔符，就不引用 `text`。结论是从撤销记录里找回的审查，还会引用那次撤销的 `fact`。

| GitHub ID | 格式 | 示例 |
|---|---|---|
| 议题 | `<owner>/<repo>#<编号>`，小写 | `acme/web#12` |
| 参与者 | `gh:<login>:<用户 id>`，login 转为小写；login 不是合法的 GitHub 登录名时为 `gh:~:<id>` | `gh:zhou-pm:5003` |
| 事件 | `gh:<议题>/` 加上 `opened`、`comment/<id>`、`review/<id>`、`event/<id>`，或 `event/<id>/request`（重新打开之后的那个请求） | `gh:acme/web#1/event/108/request` |
| 证据 | `gh:<议题>/` 加上 `issue@<th12>`、`comment/<id>@<th12>`、`review/<id>@<th12>` 或 `event/<id>`，再加 `/fact` 或 `/text` | `gh:acme/web#2/review/206@67c4aa9e1b3a/fact` |
| 依赖 | 阻塞它的 issue，写成议题的形式 | `acme/billing#3` |

`<th12>` 只取原文文本的 SHA-256 前 12 位，所以编辑会产生新的证据版本，而改登录名不会。登录名改名或被他人重新注册后，会得到一个新的参与者 ID，没有任何角色。

## Agent 技能

[`skills/memfalsify`](skills/memfalsify/SKILL.md) 和 [`skills/orgmemory`](skills/orgmemory/SKILL.md) 教 Agent 怎么用这两个工具、边界在哪里：先出报告，得到许可才改记忆，绝不把自己的判断当作人工确认写入，绝不改权限策略。GitHub 账号不是已认证的人，`memlab` MCP 工具引用的文件文本也不可信。技能里写了什么时候用这些只读工具、什么时候用命令行，以及怎样把 [GitHub Action](docs/github-action.md) 加到仓库里，让仓库核对自己的指令文件。把它们复制到 Agent 的技能目录即可：

```bash
cp -r skills/memfalsify skills/orgmemory ~/.claude/skills/   # Claude Code
cp -r skills/memfalsify skills/orgmemory ~/.codex/skills/    # Codex
```

命令行工具需要安装在 Agent 的 shell 所用的环境里。要用 MCP 工具，还要单独注册 `memlab-mcp`（见 [docs/mcp.zh-CN.md](docs/mcp.zh-CN.md)）。

## MCP 服务器

`memlab-mcp` 是一个只读的 [Model Context Protocol](https://modelcontextprotocol.io) 服务器，给本地 Agent 提供九个工具：`memfalsify_claims`、`memfalsify_audit`、`memfalsify_diff`、`memfalsify_history`、`memfalsify_rollback`、`orgmemory_validate`、`orgmemory_replay`、`orgmemory_explain` 和 `orgmemory_audit`。`memfalsify_diff` 比较 git 里一份记忆文件的基准修订版本和另一端（默认是工作区），只审计新增或修改过的记忆。`memfalsify_history` 给出每条记忆由哪个提交引入、最后由哪个提交修改，并列出被删除的记忆。`memfalsify_rollback` 返回一条记忆的补丁，从不应用它。用 `pip install -e packages/memlab-mcp` 安装（`./scripts/setup-local.sh` 已包含）。所有路径都限制在 `--root` 之内。这三个 git 工具还会拒绝工作区、git 目录、公共目录或对象库落在该根目录之外的仓库。不加 `--allow-model-verifiers` 启动时，模型验证器一律关闭；加上之后，`memfalsify_audit` 和 `memfalsify_diff` 才能使用它们。工具参数、客户端注册方式和限制见 [docs/mcp.zh-CN.md](docs/mcp.zh-CN.md)。

## 验证后端：词法、LLM、Jev

验证器判断一条记忆和一段证据的关系：`supports`（支持）、`contradicts`（反驳）或 `unrelated`（无关），并给出理由。内置三个后端，`dual` 和 `cascade` 是两个模型后端的组合方式：

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

**模型会看到哪些片段。** 证据文件先按句子切成片段（遇到 。！？； 或换行就断开），模型每次只看一个片段。仍然先用词语索引预筛选：模型验证器的门槛更低（`--min-coverage`，默认 0.2，而词法验证是 0.5），这样换了说法的片段也能被判断；每条记忆最多只送匹配度最高的 `--max-judged` 个片段（默认 8）。因此每个模型的调用次数上限是“记忆条数 × max-judged”。和记忆没有任何共同词语的片段永远不会被送去判断。

LLM 验证器使用结构化输出，每个判断都符合固定格式；它的系统提示要求把记忆和证据当作引用的数据，忽略其中出现的任何指令。在支持的模型上，它会开启服务端的拒答回退（server-side refusal fallback）；被拒答的请求记为“无判断”（`unrelated`，理由中写明停止原因）。`dual` 更严格：每个判断都核对两次。`cascade` 遵循“有把握就采纳，没把握再升级”的思路，便宜得多，但 Jev 有把握的答案会被直接采用，不经 Claude 复核，所以正式依赖它之前，先用你已经人工确认过的记忆调一调 `--accept-at`。

两个模型后端遇到 API 调用失败、缺少 SDK 或缺少凭据时，审计都会以退出码 2 结束并给出错误信息，而不是输出一份不完整的报告。

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

## 评测集与 GitHub Action

[MemFalsify 评测集](benchmarks/memfalsify/README.md)包含八组虚构案例、139 条中英文标注记忆。
[实测词法基线](benchmarks/memfalsify/RESULTS.md)的反证精确率为 0.62、召回率为 0.27，三分类准确率为 0.44。
这些案例用于检验已知失败模式，不能代表真实记忆的准确率。运行 `python benchmarks/memfalsify/run.py` 即可离线评测。

[GitHub Action](docs/github-action.md)将明确列出的 Agent 指令文件与仓库证据核对，并把报告放入任务摘要。
默认严格模式在发现可能的反证时失败；无证据的记忆和措辞问题仍需人工审阅。
设置 `diff-base`（例如 PR 的基准提交，并给 `actions/checkout` 加上 `fetch-depth: 0`）后，Action 改为运行
`memfalsify diff`，PR 只会因为它新增或修改的记忆而失败。

## 信任边界与局限

- 哈希只能证明某段引用确实存在于特定的字节里，不能证明来源是真实的，也不能证明这段话的意思就是某人所说的那样。
- 词法和模型找到的候选只是线索，不是结论。只有 `reviews.jsonl` 能给记忆下结论。
- `--verifier llm` 会把记忆和证据发给 Anthropic API，`jev` 会发给 TypeSafe API，`dual` 和 `cascade` 两边都会发。只对允许外发的材料使用它们。
- `orgmemory audit` 的发现项只是复述日志和权限策略里的内容，是供人判断的线索，不是结论。日志无法记录“风险已解决”，所以已批准的议题在批准之前提出的每个风险都会一直对应一个 `risk_after_approval` 发现项。自己批准自己的请求、被忽略的越权尝试也可能是正常的：谁能做什么由权限策略决定。
- 参与者（`actor`）ID 和 `policy.json` 被视为可信输入。目前还没有身份认证、签名溯源或策略版本管理；修改策略会改变每一次回放的结果。
- JSON 对象里重复的键会被拒绝，包括嵌套对象里的重复键。如果保留后出现的值，`events.jsonl` 的同一行可以让读文件的人看到一个 `actor`，回放却使用另一个；`policy.json` 里把同一个人写两次时，也会按后一次的角色授权。
- 证据目前只支持 UTF-8 文本。图片、音频、视频和 PDF 会在报告的 `Skipped evidence` 部分列为跳过。
- `diff`、`history` 和 `rollback` 信任本地 git 仓库：它们按仓库里的对象和引用读取内容，归属结果的可靠程度取决于提交历史本身（变基、压缩合并和第一父提交链都会改变“谁引入了这条记忆”的结论）。
- GitHub 导入：GitHub 账号不等于经过认证的个人。用某人自己的 token 运行的自动化（例如使用维护者个人访问令牌的 workflow）无法与这个人区分；审查不带应用标记；合并队列和自动合并的合并者是 GitHub 记录的那个账号；由 issue 模板添加的标签可能被算在 issue 作者头上。不要在 `github.json` 里列出由模板、机器人或使用个人 token 的 workflow 添加的标签，给账号配置角色时也要谨慎。
- `policy.json` 是全局的：给一个 `gh:` 账号配置角色，等于在所有导入仓库的议题上都授予了这个角色。
- 在公开仓库里，任何人都可以开 issue、评论、审查 PR、关闭自己的 issue。这些都会以真实账号记录下来，只要账号没有配置角色，回放就会忽略它们，所以会出现 `unauthorized_change_attempts`。这是如实记录，不是 bug。
- 存档文件的哈希只能证明导入工具根据 API 响应生成的字节，不能证明 GitHub 实际返回了什么；交给 `convert` 的抓取结果会被当作“GitHub 的原话”，所以只转换你自己抓取的结果。抓取到的 GitHub 文本不可信，issue 正文里出现提示注入是预料之中的。这些文本永远不会进入 ID、备注、参与者或导入工具的输出，但 `replay` 和 `explain` 会原样打印证据引用（单行，不含终端转义序列和双向文本控制字符，但可能包含 markdown 或 HTML）。
- GitHub token 只从环境变量读取，只发送给 `https://api.github.com`（从不跟随重定向到其他主机），也从不写入文件或打印出来。
- 从私有仓库导入的数据集和 `--save-capture` 文件包含私密文本，绝不要提交。目前没有脱敏流程：要从存档文件里删掉泄露的密钥只能手工编辑，之后 `validate` 会按设计报错。
- 这是一个研究用的 MVP，不是生产级基础设施。

## 路线图

| | MemFalsify | 组织记忆 |
|---|---|---|
| **v0.1** | 文本记忆审计、证据追溯、人工确认流程 | 事件日志、状态机、权限规则、决策溯源 |
| **v0.2** | LLM + Jev 双重验证、cascade 模式 ✓ | 导入 GitHub Issues / PR ✓ |
| **v0.3** | 记忆的 Git 历史：diff、审查、回滚；跨 Agent 技能打磨 ✓ | 状态冲突与决策审计 ✓ |
| v0.4 | 多模态证据（带定位的 OCR / 转写） | 跨平台组织记忆 |
| v1.0 | 基准测试、稳定 API | 基准测试、稳定 API |

各个包每个版本的变更见 [CHANGELOG.md](CHANGELOG.md)（英文）。

## 开发

```bash
pip install pytest
python -m pytest     # 直接针对 packages/*/src 运行，无需安装
```

目录结构：`packages/memcore`（证据完整性、JSONL）、`packages/memfalsify`、`packages/orgmemory`、`skills/`、`examples/`、`tests/`。提交贡献时请附上最小的合成复现示例。不要提交私密证据或凭据。

## 许可证

Apache-2.0，见 [LICENSE](LICENSE)。
