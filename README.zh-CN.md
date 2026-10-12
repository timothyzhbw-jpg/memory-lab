<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="assets/logo-dark.png">
    <img src="assets/logo.png" alt="Memory Lab" width="420">
  </picture>
</p>

# Memory Lab：让 AI Agent 的记忆可验证

[English](README.md) | 中文

现在的 Agent 记忆工具大多只管“存”和“取”。这个项目关心的是**记住的东西是不是真的**：每条记忆都应该有证据、有时间、能追溯来源；未经授权的信息不应该改变当前状态。

[15 分钟教程](docs/tutorial.zh-CN.md)在 `examples/` 的临时副本上把两个工具走一遍。

`记住 → 验证 → 质疑 → 裁决 → 演进`

项目包含两个相互独立的工具，共用一个证据核心：

| | **MemFalsify** | **可验证的组织记忆**（`orgmemory`） |
|---|---|---|
| 回答的问题 | 这条记忆有证据支撑吗？ | 这是当前的、经过授权的状态吗？ |
| 输入 | `MEMORY.md` 或各家 Agent 的指令文件（`CLAUDE.md`、`AGENTS.md`、Cursor、Copilot、Gemini、Windsurf）+ 一个证据文件目录 | 事件日志 + 角色权限策略 + 证据清单 |
| 输出 | 审计报告：引用原文、发现的问题、建议操作 | 每个议题的当前状态、决策来源、审计轨迹、决策流程审计、ADR 导出 |
| 会写入什么 | 不写入任何东西（只输出你要求的报告） | 回放和审计时不写入；`append` 和 GitHub、Jira 导入工具只追加；`export` 只在数据集之外新建文件 |

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

更习惯用浏览器？`pip install -e packages/memlab-ui` 后运行 `memlab-ui --root examples`，就会打开一个覆盖两个工具的本地只读网页（见[网页](#网页)）。它需要 macOS 或 Linux：与 MCP 服务器一样，在 Windows 上会以退出码 2 结束。

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

中文支持是内置的，不需要词典分词，也不增加依赖：

- 中文按相邻两个、三个汉字切分匹配（字符二元组和三元组，例如“批量导出”切成“批量”“批量导”“量导”“量导出”“导出”）；中文记忆的覆盖率按字计算，所以记忆和证据不必用同样的方式断词。
- 数量按数值比较：量词前的中文数字（三个月、两名）、万/亿/千（1200万、一千二百万）、百分比（百分之十、一成）、“半”（半小时、一年半）、`2026年3月2日` 这样的日期，以及全角数字和标点（１２０，ＡＰＩ）。“第四季度”按 Q4 处理，与英文一致。因此“三秒”和“2秒”会被判为数值不同的候选反证。
- 否定词（不、没有、未、无、非、别、勿、不再、禁止、不得）只在与另一方共享词语的分句里才算数；“未来”“非常”“无论”“级别”等词，以及“非……不可”这类双重否定，不算否定。
- 情态相反也算候选反证：全称（所有、任何、一律）对限定（仅、只有），必须（必须、非……不可）对无需（无需、不必）。
- 同时识别中文反义词对（批准/拒绝、启用/禁用、允许/禁止……），以及绝对化（总是、所有、一定）、相对时间（下周、最近、目前）和推测性（可能、大概、好像）措辞。

在评测集上，词法验证器能找出 19 条中文反证中的 5 条（英文为 29 条中的 11 条）。英文示例在 [`examples/memfalsify`](examples/memfalsify/)，其中还演示了人工确认（`reviews.jsonl`）和被跳过的图片证据；媒体文件只有配上转写稿或 OCR 旁车文件才算证据（见下文“转写稿与 OCR”）。

**状态是怎么定的。** MemFalsify 会自己找候选证据，但只有人工确认才能给一条记忆下结论：

| 状态 | 含义 |
|---|---|
| `supported` / `challenged` / `conflicted` | `reviews.jsonl` 中经人工确认的证据：支持 / 反驳 / 两者都有 |
| `candidate_counterevidence` | 验证器找到了可能反驳这条记忆的片段（词法层面：反义词如“批准/拒绝”、数字不一致，或只有一方是否定句） |
| `disputed` | 两个验证器意见不一致（见下文“验证后端”） |
| `candidate_support` | 找到了复述这条记忆的片段 |
| `insufficient_evidence` | 什么都没找到。这表示“未经验证”，不表示“是错的” |

不论有没有证据，每条记忆都会检查：`overgeneralization`（绝对化措辞）、没有日期的 `relative_time`（相对时间）、`possibly_stale`（可能过期）、`inference_marker`（推测性措辞）、与其他记忆的 `internal_conflict`（内部矛盾），以及 `duplicate`（重复）。内部矛盾按规范化后的词项建索引，只比较有可能发生词法冲突的记忆。报告与两两全比的结果相同。

`verify_counterevidence`、`add_absolute_date`、`narrow_scope` 这类操作只是建议，不会自动执行。`replacement` 字段永远是空的：MemFalsify 从不替你编写事实。

其他命令和选项：`memfalsify claims MEMORY.md` 列出每条记忆的 ID；`memfalsify discover` 列出仓库里各家 Agent 的指令文件（见下）；`--format json`、`--format html`（一个自包含的网页，见 [HTML 报告](#html-报告)）、`--output report.md`、`--strict`（有需要关注的记忆时退出码为 1，适合 CI）、`--stale-days`、`--no-follow-links`。

#### 其他 Agent 的指令文件

所有命令同样接受其他编程 Agent 的指令文件。每种格式都对照过该 Agent 的官方文档（2026 年 10 月；出处列在 [`formats.py`](packages/memfalsify/src/memfalsify/formats.py)）：

| Agent | 文件 | 保留的 frontmatter 字段 |
|---|---|---|
| Claude Code | `CLAUDE.md`、`CLAUDE.local.md`、`.claude/CLAUDE.md`、`.claude/rules/**/*.md` | `paths` |
| Codex 及其他读取 `AGENTS.md` 的 Agent | `AGENTS.md`、`AGENTS.override.md`、`.claude/AGENTS.md` | |
| Cursor | `.cursor/rules/**/*.mdc`；旧式的 `.cursorrules` | `description`、`globs`、`alwaysApply` |
| GitHub Copilot | `.github/copilot-instructions.md`、`.github/instructions/**/*.instructions.md` | `applyTo`、`excludeAgent` |
| Gemini CLI | `GEMINI.md` | |
| Windsurf（现名 Devin Desktop） | `.windsurf/rules/*.md`、`.devin/rules/*.md`；旧式的 `.windsurfrules` | `trigger`、`globs`、`description` |

```bash
memfalsify discover                                  # 列出这些文件：表格，或 --format json；可加 --exclude 'tests/fixtures/**'
memfalsify audit .cursor/rules/api.mdc --evidence .  # claims、diff、history 也都能用
```

- frontmatter 永远不算记忆。表中的字段会附在该文件的每条记忆上：JSON 里是 `metadata`，报告里是一行 `Frontmatter:`，这样每条发现都能看出这条指令作用于哪些文件。frontmatter 按原样读取，只支持一个小的 YAML 子集（`globs: **/*.ts` 在严格 YAML 下不合法，但会原样保留）；其他字段忽略。
- `diff` 把 frontmatter 改了、措辞没变的记忆列在 “Frontmatter changed” 下（不审计它们）；`history` 把这种改动算作该文件每条记忆的最近一次修改。
- `@file.md` 导入（Claude Code、Gemini CLI）和本地 Markdown 链接一样会被跟进：只跟一层，且不出该文件所在目录。含导入的行不算记忆。
- 位置写作 `path:line`，路径相对于被审计文件所在目录，行号从文件第一行算起（包括 frontmatter）。
- `discover` 只读取文件名。`AGENTS.md`、`CLAUDE.md`、`GEMINI.md` 和各类规则目录在任何目录下都会找；`.cursorrules`、`.windsurfrules` 和 `.github/` 只在顶层找。它不进入其他隐藏目录以及 `node_modules/`、`__pycache__/`、`venv/`；嵌套的 git 仓库列为已跳过；符号链接只有指向本目录内的普通文件时才会列出；同一个文件有两个名字时只列一次（`same_as`）；文件系统没有报告文件索引时，两个名字都会列出。目录不可读时退出码为 2。仓库之外的文件（`~/.claude/CLAUDE.md`、`~/.gemini/GEMINI.md`、Windsurf 的全局规则）以及在 Agent 设置里另行配置的文件名不在搜索范围内。

格式错误的记忆、评审或证据会以退出码 2 结束，并在 stderr 输出一行说明。开头的 BOM 会被忽略；CR、CRLF 和 LF 都视为换行。标准输出会去掉控制字符和双向文本控制符；`--output` 保留报告原文，包括这些字符。

#### 转写稿与 OCR

MemFalsify 自己不做语音转写，也不做 OCR。把你自己的转写或 OCR 结果作为“旁车文件”（sidecar）放在媒体文件旁边，每个字幕条目（cue）或每一行 OCR 文本就成为一段证据，引用时标出媒体文件和其中的位置：

| 旁车文件 | 每段证据对应 | 引用形式 |
|---|---|---|
| `clip.mp4.vtt` 或 `clip.vtt`（WebVTT）、`clip.srt`（SubRip） | 一个字幕条目 | `clip.mp4#t=12.5,17`（00:00:12.500-00:00:17.000） |
| `scan.pdf.ocr.json`（`memlab-ocr/1`：页、行、文本、边框） | 一行文本 | `scan.pdf#page=2&xywh=40,120,570,38` |

```bash
cd examples/memfalsify-media
memfalsify audit MEMORY.md --evidence evidence --reviews reviews.jsonl --as-of 2026-10-10
```

旁车文件要记录媒体文件的 SHA-256（转写稿里写一个 `NOTE source_sha256: <hex>` 块，OCR JSON 里写 `"source_sha256"` 字段）。媒体文件在场时，两者必须一致，否则旁车文件会作为“过期”（stale）或“未绑定”跳过；媒体文件不在时，旁车文件照常使用，报告会注明没有核对绑定。媒体文件名必须与目录项逐字一致，包括大小写（`Clip.mp4.vtt` 不会描述 `clip.mp4`），因此 macOS、Windows 和 Linux 的结果相同。引用依然逐字精确：每条引用都是旁车文件字节中、位于同一个字幕条目或同一行内的一段区间，像其他证据文件一样计算哈希。格式不对的旁车文件会带着原因列为跳过。[示例报告](examples/memfalsify-media/report.md)里，站会转写稿反驳了记忆中的限流数值，白板 OCR 的一行与“已批准”相矛盾，而一份根据录像旧导出版本做的复盘转写稿被判为过期并跳过。格式、命名规则和局限见 [docs/multimodal.md](docs/multimodal.md)（英文）；暂不支持 hOCR。

#### 审查 git 里的记忆改动

`CLAUDE.md`、`AGENTS.md`、`MEMORY.md` 放在 git 里时，可以只看某次提交或某个 PR 改了哪些记忆，并且只审计这些：

```bash
memfalsify diff CLAUDE.md --base main --evidence .        # 与工作区比较；加 --head REV 可比较两个提交
memfalsify history CLAUDE.md --max-commits 50              # 每条记忆由哪个提交引入、最后由哪个提交修改
memfalsify rollback CLAUDE.md --claim m-1a2b3c4d --to main -o restore.patch
git apply restore.patch                                    # 先审阅补丁，再在仓库根目录应用
```

- `diff` 通过 git 读取两个版本的记忆文件及其链接的文件（不会检出任何东西），把记忆分为新增、修改（旧 → 新）、删除和未变。只审计新增和修改的记忆，证据目录按当前状态读取；`audit` 的选项都能用（`--evidence`、`--exclude`、`--reviews`、`--verifier`、`--format`、`--output`、`--strict` 等）。加 `--strict` 时，新增或修改的记忆需要关注就退出码为 1；git 出错或修订版本不存在时退出码为 2。`--staged` 从索引（`git commit` 将要记录的文本）读取待比较的新版本，以及它链接的一层 `.md` 文件。证据仍是工作区。`--staged` 要配合 `--base`，并且不使用 `--head`。仓库还没有任何提交时，`--base HEAD` 会把每条已暂存的记忆都当作新增。
- 记忆按 ID 识别，所以只改大小写或空白算作未变。修改过的记忆要和旧版本配对，两者须在同一文件、同一小节（未变记忆所在标题被改名时仍算同一小节），并且词项相似度至少 50%；否则显示为一条删除加一条新增。配对结果是确定的。
- `history` 沿 `HEAD` 的第一父提交链追溯，所以通过合并提交并入的改动记在该合并提交名下；它还列出扫描范围内被删除的记忆，未提交的修改记为“uncommitted changes in the working tree”。早于扫描范围的记忆（受 `--max-commits` 限制，默认 100；或到了浅克隆的边界）会如实标为扫描范围之前，不会算到最早那个提交头上。记忆文件改名不会被追踪。
- `rollback` 从不应用任何改动：它打印一个补丁，把这条记忆恢复成 `--to` 时的措辞（所有副本都恢复，并保留原来的 markdown 标记），或把已删除的记忆重新加到它所在小节里前面最近一条仍存在的记忆之后。当前 ID 和旧 ID 都可以用。在 Windows 上请用 `-o`，因为 shell 重定向可能改变补丁的编码。补丁本身是原始字节（输出到管道、重定向或 `-o` 时）；只有 stdout 是终端时，`rollback` 才会把控制字符、双向文字控制符和其他不可见字符显示成 `\uXXXX` 转义（并在 stderr 说明），避免恶意修订清屏或改写终端显示：要应用的补丁请用 `-o`。只用 CR 换行的记忆文件会被拒绝（只支持 LF 和 CRLF）。
- git 的输出如果被截断、超过 32MB，或不是正常的记录，退出码为 2。`history` 拒绝不是严格 ISO-8601 的作者日期（`2026-10-01T00:01:00+00:00` 或 `Z`，不带小数秒）。

#### 用 pre-commit 检查提交

[pre-commit](https://pre-commit.com) 可以在提交时做同样的检查。钩子仓库就是本仓库，请固定到完整的提交 SHA。清单文件是 [`.pre-commit-hooks.yaml`](.pre-commit-hooks.yaml)。

```yaml
repos:
  - repo: https://github.com/timothyzhbw-jpg/memory-lab
    rev: <40 位 SHA>
    hooks:
      - id: memfalsify-diff
      - id: memfalsify-audit
```

`memfalsify-diff` 审计暂存的 `CLAUDE.md`、`AGENTS.md`、`MEMORY.md` 里新增或修改的记忆。`memfalsify-audit` 审计这些暂存文件里的每一条记忆。钩子检查过的记忆如果是 challenged、conflicted、disputed，或有可能的反证，提交就会失败。没有证据的记忆，以及措辞问题，只出现在报告里，提交继续。`args: [--no-strict]` 只打印报告，提交照常继续。

两个钩子都从索引读取记忆，包括它链接的一层 `.md` 文件，那些链接文件也从索引读取。证据是工作区。`git commit` 和 `pre-commit run` 会先藏起已跟踪文件上未暂存的修改，所以已跟踪的证据与索引一致，未跟踪的证据文件仍然可见。自己运行 `memfalsify diff --staged` 时不会藏起这些修改，因此未暂存的证据改动会进入那次审计。diff 钩子对应的命令是：

```bash
memfalsify diff CLAUDE.md --base HEAD --staged --evidence . --strict
```

一次提交如果只暂存了被链接的 `.md` 文件，钩子不会运行。请同时暂存链接它的 `CLAUDE.md`、`AGENTS.md` 或 `MEMORY.md`。默认验证器是词法的，并且离线。要用 `llm`、`jev`、`dual`、`cascade` 或 `openai`，把对应的 SDK 写进 `additional_dependencies`（例如 `anthropic>=1.13`、`typesafe-sdk` 或 `openai>=3.0`），并在 `args` 里传入 `--verifier`（用 `openai` 时还要传 `--openai-model` 和服务商）。钩子的安装已经带上本仓库里的 MemFalsify。

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
orgmemory changes  <数据集> --from 2026-10-05T23:59:59Z --to 2026-10-08T23:59:59Z   # 两个时间点之间发生了什么变化
orgmemory export   <数据集> --out docs/adr      # 每个已有决定的议题导出一份架构决策记录（ADR）
orgmemory evidence add <数据集> --id ... --path ... --quote "..." --source-uri ... --source-kind ... --captured-at ...
orgmemory append   <数据集> --event '{"id": ..., ...}'
orgmemory policy grant|revoke <数据集> ...   # 记录从某个时间点起生效的角色变更（见下文）
```

在大日志上，`validate`、`replay`、`explain` 和 `audit` 的报告与原来一致，同时减少重复的证据哈希、权限查找和轨迹扫描。测量见 [benchmarks/perf](benchmarks/perf/README.md)。

#### 随时间变化的角色

修改 `policy.json` 会改变每一次回放的结果，包括已经做出的决定。比如在里面给客户经理加上产品经理角色，示例里周四那次“批准”就会开始生效。要记录从某个时间点起生效的角色变更，请改为追加到可选的 `policy-log.jsonl`：

```bash
orgmemory policy grant  <数据集> --id pl-wu-pm --timestamp 2026-10-09T09:00:00Z --actor chen \
  --subject-actor wu --role product_manager --evidence-id org-wu
orgmemory policy revoke <数据集> --id pl-zhou-leaves --timestamp 2026-10-12T09:00:00Z --actor chen \
  --subject-actor zhou --role product_manager --evidence-id org-zhou --note "moved to the platform team"
```

此后，每个事件都按参与者在该事件生效时间持有的角色来判断。在 [`examples/orgmemory/feature-a-dated`](examples/orgmemory/feature-a-dated/)（[回放报告](examples/orgmemory/feature-a-dated/replay-report.md)、[审计报告](examples/orgmemory/feature-a-dated/audit-report.md)）里，wu 从周五起成为产品经理，zhou 从周一起不再担任产品经理。wu 周四的“批准”仍然不生效，zhou 周三的拒绝仍然有效。`explain` 会显示每个参与者在每个事件发生时持有的角色，以及角色何时变化。审计会报告当前决定的做出者之后失去了相应角色（`role_changed_after_decision`，low）。

- `policy.json` 是基线。其中的角色从时间起点开始生效；如果设置了可选的 `"as_of"`（ISO 8601，带时区），则从该时间开始生效。在 `as_of` 之前，任何参与者都没有角色，任何变更也不能在它之前生效。
- 变更按生效时间排序；生效时间相同的变更按文件顺序应用。在 T 时刻生效的变更，对 T 时刻的事件同样适用。`--as-of` 会应用截止时间之前（含截止时间）的变更。
- 变更的 `--actor` 在变更生效时，必须持有 `policy.json` 中 `manage_roles` 权限所列的某个角色。最初的管理者就是 `policy.json` 里列出的那些人。`grant` 授予的角色必须在 `policy.json` 中出现过，且对方当时尚未持有；`revoke` 移除对方当时持有的角色。
- `orgmemory policy grant` 和 `orgmemory policy revoke` 会连同新条目一起校验整个日志（这样，补记的早期条目不会让后面的条目失效），通过后才追加。日志不成立的数据集会被 `validate`、`replay`、`explain` 和 `audit` 拒绝。
- 补记较早生效的条目可以改变已记录事件的结果，包括使用过去的 `--as-of` 截止时间回放的结果。校验保护的是后续角色变更条目的有效性，不是原来的事件结果。截止时间依据当前日志中的生效时间，不会还原当时已知哪些条目。
- `permissions` 不分时间。既没有 `policy-log.jsonl` 也没有 `as_of` 的数据集，回放结果和以前完全相同。设计说明：[docs/design/dated-policy.md](docs/design/dated-policy.md)。

#### 审计决策流程

`replay` 回答“现在是什么状态”，`audit` 回答“决策流程哪里需要关注”。它用同样的规则只读地回放日志，然后列出发现项。每个发现项包含 `kind`（类型）、`severity`（严重程度：`high`、`medium` 或 `low`）、所涉及的议题 `subject`（如果是针对某个参与者的发现项，则为 `actor`）、涉及的 `subjects` 和 `event_ids`（事件 ID 按回放顺序排列）、一行说明 `detail`，以及每个被引用的事件及其证据原文，引用方式与 `explain` 相同。审计不引入任何新的事件语义：它只复述回放结果、回放记录的冲突和审计轨迹里已有的内容。

```bash
orgmemory audit <数据集> [--as-of ISO8601] [--stale-days 14] [--format markdown|json|html] [--strict]
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
| `role_changed_after_decision` | 当前决定（`approve`、`reject` 或 `revoke`）的做出者后来因 `policy-log.jsonl` 中的 `revoke_role` 失去了授权这个决定的角色。决定仍然有效。只有角色带时间的数据集才会列出这一类型 | low |

- 只计入回放实际生效的内容：没有权限的参与者提出的风险或依赖不会被记录，只会出现在 `unauthorized_attempts` 里。
- `--as-of` 和 `replay` 一样会忽略之后的事件，同时也是计算是否超期的参考时间。不加时审计整个日志，并以当前时间计算超期，所以需要可复现的报告时请加 `--as-of`。
- 发现项按严重程度、类型、议题、参与者和回放顺序排序。Markdown 报告以一张汇总表开头：每种类型一行，每个严重程度一列。`--format json` 包含 `summary`（各严重程度的数量）、`kinds`（每种类型的数量，按名称排序）和 `findings`。角色带时间时，Markdown 报告还会列出每一次角色变更，JSON 会增加 `dated_policy`，引用了角色变更的发现项会增加 `role_changes`。否则，汇总表和 `kinds` 都不会列出 `role_changed_after_decision`。
- 退出码：`0`；加 `--strict` 且存在 high 级别的发现项时为 `1`；输入无效时为 `2`，与 `replay` 相同。除了退出码 `2`，其他情况都会输出报告。无效输入包括错误的事件或策略（缺少字段、类型不对、没有时区的时间戳，或嵌套过深的 JSON）以及错误的证据区间。错误信息只有一行，且不含终端控制字符。

#### 两个时间点之间发生了什么变化

`changes` 分别在 `--from` 和 `--to` 两个时间点回放日志，按议题报告这段时间里发生的变化：时间段包含 `--from` 之后、直到 `--to`（含）的事件，与 `replay --as-of` 的截止规则相同。和审计一样，它是只读的，不引入新的事件语义，并引用每个相关事件及其证据原文。

```bash
orgmemory changes <数据集> --from ISO8601 --to ISO8601 [--format markdown|json]
```

示例数据从周一晚上到周四晚上（[完整报告](examples/orgmemory/changes-report.md)）：产品经理的决定让功能A从 `proposed` 变成 `rejected`；工程师提出了一个风险；客户的第一次请求有了结论，第二次请求则开启了“申请重新考虑”；客户经理的“批准”被忽略。[GitHub 示例的变化报告](examples/orgmemory/github-acme-web/changes-report.md)（2026-10-05T23:59:59Z 到 2026-10-10T23:59:59Z）把审查、合并、关闭和重新打开都按权限策略下的决定列出，并略去没有任何变化的议题，例如 `acme/web#4`：它的关闭记为评论，重新打开只是重复了一个已在等待决定的请求。

| 类型 | 在时间段内何时列出 |
|---|---|
| `decision` | 生效了一次有权限的 `approve`、`reject` 或 `revoke`，列出它之前和之后的状态、参与者、其角色和证据。确认现有状态的决定也会列出，因为它成了新的授权决定 |
| `pending_approval_opened` | `pending_approval` 开启：引用开启它并让它保持开启的事件，并说明到时间段结束时是否仍未关闭 |
| `pending_approval_resolved` | 一次 `approve` 或 `reject` 关闭了等待中的批准：引用开启它的事件和这个决定 |
| `reconsideration_opened` / `reconsideration_resolved` | 对 `reconsideration_requested` 的同样处理 |
| `risk_raised` | 记录了一个风险 |
| `dependency_added` | 新增的依赖，附带该依赖在时间段结束时的状态 |
| `conflict` | 回放记录了一个冲突：`simultaneous_decisions`、`decision_reversed` 或 `invalid_supersession` |
| `unauthorized_attempt` | 权限策略忽略了一个事件，每个事件一项。`comment` 不列出，因为它从不改变状态 |

- Markdown 报告以一张汇总表开头：每个有变化的议题一行，列出它之前和之后的状态、按类型统计的变化，最后是合计行。议题有变化，或者在时间段之前没有任何事件时，才会列出。议题按名称排序，每个议题的变化先按类型（上表顺序）、再按回放顺序排列。
- `--format json` 包含 `from_time`、`to_time`、事件和议题的计数、`summary`（各类型的变化数量，列出所有类型）和 `subjects`；每个议题有 `status_before`（时间段内新出现的议题为 `null`）、`status_after` 和 `changes`。
- `--from` 和 `--to` 都必须提供，并且要带时区；`--from` 不能晚于 `--to`。退出码：`0`；输入无效时为 `2`，与 `replay` 相同。

#### 把决策导出为 ADR

`export` 把每个议题当前的有权限决定写成一份架构决策记录（Architecture Decision Record，ADR），可以放进 `docs/adr` 这样的目录；也可以把所有决定导出成一张 CSV 表。和 `audit` 一样，它只复述回放结果，从不修改数据集。

```bash
orgmemory export <数据集> --out 目录 [--as-of ISO8601] [--force]
orgmemory export <数据集> --format csv [--out 文件] [--as-of ISO8601] [--force]
```

- 回放后有当前有权限决定的议题，每个导出一个 Markdown 文件；没有决定的议题不导出。版式沿用 Michael Nygard 的模板（与 adr-tools 生成的一致）：议题作标题，`Date:`（决定当天的日期），然后是 `Status`、`Context`、`Decision`、`Consequences`，再加一节 `Evidence`。状态用 MADR 的词：`approved` 写作 `Accepted`，`rejected` 写作 `Rejected`，最后一个决定被撤销（`revoke`）的议题写作 `Proposed`。
- **Status**：作出决定的事件、回放截止时间，以及进行中的“申请重新考虑”或待批准，和需要哪些角色来决定。**Context**：已生效的申请、申请重新考虑和风险，按回放顺序排列，发生在决定之后的会标出。**Decision**：谁（及其角色）在什么时间作了什么决定，`revoke` 撤销的是哪个决定，以及更早的有权限决定。**Consequences**：权限策略允许哪些角色改变这个状态、依赖及其状态、被忽略的越权修改，以及回放记录的冲突。**Evidence**：上述事件引用的每段原文，附文件、行号、字符范围、完整 SHA-256、来源 URI、来源类型和采集时间，以及引用它的事件。
- 文件名是 `<slug>-<hash12>.md`：议题转成小写 ASCII 字母、数字和连字符（最多 48 个字符；如果什么都不剩就用 `subject`），后接议题原文 SHA-256 的前 12 位十六进制，因此 slug 相同的议题也会得到不同的文件。`Feature A` 对应 `feature-a-5481a84ec80d.md`。记录里不含任何与本次运行有关的内容，所以对没有变化的日志再导出一次，得到的字节完全相同。
- 只新建文件：只要有一个目标文件已存在，就什么都不写。`--force` 通过重命名替换已存在的普通文件（绝不会顺着符号链接或硬链接写到别处）；目录里的其他文件保持不动，也从不删除文件。目录不存在时会自动创建。
- 绝不写入数据集：`--out` 位于数据集目录之内时会被拒绝，经由符号链接、`..` 或大小写不同的路径也一样。
- 不可信文本（议题、参与者、角色、备注、ID、原文）按 `orgmemory audit` 的方式转义：保持为单行的字面 Markdown。
- `--format csv` 为每个有决定的议题输出一行，默认写到标准输出，加 `--out` 则新建一个文件（同样只新建，规则同上）。列依次为：`subject`、`adr_file`、`status`、`current_status`、`decided_at`、`decision_event`、`decision_action`、`decided_by`、`decided_by_roles`、`supersedes`、`earlier_decisions`、`reconsideration_requested`、`pending_approval`、`dependencies`、`open_risks`、`unauthorized_change_attempts`、`conflicts`，决定所引用证据的 `evidence_ids`、`evidence_sha256`、`evidence_source_uris` 和 `evidence_quotes`，以及 `as_of`。列表列是 JSON 数组，解码后与原值完全一致。每行只占一行：其他单元格里的控制字符、格式字符（双向文本、零宽字符）和分隔符转义为 `\uXXXX`，U+FFFF 以上为 `\U00XXXXXX`；以 `=`、`+`、`-` 或 `@` 开头的单元格前面加一个 `'`，避免电子表格把它当作公式执行。
- 退出码：`0`，包括还没有任何议题有决定的情况（此时不写 ADR 文件）；输入无效或写入被拒绝时为 `2`，什么都不写。

示例（由两个示例数据集导出）：[Feature A 的决策记录](examples/orgmemory/adr/feature-a/feature-a-5481a84ec80d.md)、[GitHub 示例的决策记录](examples/orgmemory/adr/github-acme-web/)（包括被接受、被拒绝和被撤销的 PR 与 issue），以及 [`examples/orgmemory/adr/`](examples/orgmemory/adr/) 里对应的 CSV 表。

#### 导入 GitHub Issues 和 Pull Request

`orgmemory github` 把一个仓库的 issue 和 pull request 转换成数据集里的事件、证据和存档文件。每个 issue 或 PR 是一个议题（subject，例如 `acme/web#12`）。导入过程是确定性的，不使用任何模型。

```bash
orgmemory github sync    <数据集> OWNER/REPO [--full | --since ISO8601] [--issue N]... [--max-wait 3600] [--save-capture f.json] [--dry-run]
orgmemory github convert <数据集> f.json [--dry-run]  # 离线应用之前保存的抓取结果
orgmemory explain        <数据集> "acme/web#12"
```

- token 取 `GITHUB_TOKEN`、`GH_TOKEN` 中第一个非空的值（可以用 `GH_TOKEN=$(gh auth token)`）；没有 `--token` 参数。一个只读的细粒度（fine-grained）token，带 Issues、Pull requests 和 Metadata 的读权限就够了。没有 token 时以未认证方式运行：只能访问公开仓库，每小时 60 次请求。
- 分页链接必须保持原请求的列表端点和查询条件。响应的 JSON 字符串或响应头如果包含 token（包括百分号编码形式），会被拒绝。两种 HTTP 传输都将每个响应体限制为 8 MiB；超过限制时 sync 以退出码 2 结束，不追加数据，也不保存 capture。
- 第一次运行读取全部内容；可以用 `--since ISO时间`（必须带时区）限定范围，但仓库一旦有了游标（完成过一次完整同步），这个参数就会被拒绝。之后每次只读取上一次完整同步以来变化的内容，并往前多重叠 5 分钟。`--full` 重新扫描全部，`--issue N`（可重复）只刷新指定编号，不能与 `--full` 或 `--since` 同用。重复运行是安全的：ID 是确定的，日志里已有的内容不会被追加第二次。GitHub 不一定会因为每种变化（标签、依赖关系）都把 issue 标为已更新，所以隔一段时间跑一次 `--full`。
- `sync` 会复用到 `api.github.com` 的 HTTPS 连接，使用系统 CA 存储校验证书，每次请求超时为 30 秒；运行结束（包括出错）时关闭连接。连接在收到任何响应字节之前失败时，会新建连接重试一次 GET；限流和其他重试行为保持原样。设置 `HTTPS_PROXY` / `https_proxy` 或 `ALL_PROXY` / `all_proxy` 时回退到原有 urllib 传输，按 urllib 的代理规则处理，不复用连接。传输层不会自动跟随重定向；客户端只允许经过校验的同源重定向。
- `sync` 抓取期间最多每 5 秒向 stderr 输出一次进度，显示已完整抓取的 issue/PR 数、请求数和剩余限额（不可用时为 `unknown`）。每次因速率限制休眠时还会输出休眠时长和 UTC 唤醒时间。短运行可能不显示进度；stdout 保持不变，包括摘要和 `--dry-run` JSONL。
- `--dry-run` 会实际请求 GitHub（消耗请求次数），打印摘要和计划写入的事件，但不写入数据集或锁文件。`--save-capture` 把抓取到的 issue 和时间线按 API 返回的原样写入一个新文件，之后可以用 `convert` 应用；它可以和 `--dry-run` 一起用，这样就能先审阅抓取结果再应用。文件里是原始的、可能私密的文本。保存路径只能是新文件，不能覆盖导入工具管理的文件或 `policy.json` / `github.json`。
- 退出码：`0` 表示完整完成（包括“没有新内容”）；`2` 表示出错，没有追加任何内容；`3` 表示只完成了一部分：限流等待超过 `--max-wait`（默认 3600 秒），或服务端、网络反复出错，导致抓取中止，已完整抓取的条目已经写入，游标没有移动，稍后再运行一次即可。格式错误的抓取文件也是退出码 2，且不会追加任何内容，包括嵌套过深的 JSON，或时间线 id 类型不对。
- 数据集目录必须已存在。`events.jsonl` 和 `evidence.jsonl` 会在第一次写入时创建。导入不需要 `policy.json`；`replay`、`explain` 和 `validate` 需要。

**导入工具从不读取或写入 `policy.json`，也从不在 `policy-log.jsonl` 里记录角色变更：`gh:<login>:<id>` 形式的参与者 ID 对应什么角色，由你自己配置。** 账号配置角色之前，它的事件会被记录，但不会改变任何状态。同步摘要会列出新事件涉及的参与者，`orgmemory validate` 会列出所有既不在 `policy.json` 里、也从未在 `policy-log.jsonl` 中被授予角色的参与者。

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

#### 导入 Jira issue

`orgmemory jira convert` 把一次保存下来的 Jira Cloud 搜索结果转换成数据集里的事件、证据和存档文件。每个 issue 是一个议题（subject），用它的 key 表示，例如 `ACME-12`。它完全离线：只读取你保存的文件，从不连接 Jira。导入过程是确定性的，不使用任何模型。设计和权限规则见 [docs/design/jira-import.md](docs/design/jira-import.md)（英文）。

```bash
orgmemory jira convert <数据集> capture.json [--dry-run]
orgmemory explain      <数据集> "ACME-12"
```

**保存抓取结果。** 抓取结果是一次 `GET /rest/api/3/search?expand=changelog`（使用下面固定的字段列表）返回的全部分页，原样保留，外面再包上站点地址和开始抓取的时间。在一个空目录里运行（需要 `curl` 和 `jq`）：

```bash
SITE=https://acme.atlassian.net                  # 你的 Jira Cloud 站点
JQL='project = ACME ORDER BY key ASC'            # 固定排序，结果不会在分页之间移动
FIELDS=summary,description,created,creator,reporter,status,comment
AT=$(date -u +%Y-%m-%dT%H:%M:%SZ)
# ~/.jira-netrc，只有你自己可读：machine acme.atlassian.net login you@example.com password <API token>
start=0; total=1
while [ "$start" -lt "$total" ]; do
  curl -sS --fail --netrc-file ~/.jira-netrc -H 'Accept: application/json' -G "$SITE/rest/api/3/search" \
    --data-urlencode "jql=$JQL" --data-urlencode "fields=$FIELDS" --data-urlencode expand=changelog \
    --data-urlencode maxResults=100 --data-urlencode "startAt=$start" -o "page-$start.json" || break
  total=$(jq .total "page-$start.json"); count=$(jq '.issues | length' "page-$start.json")
  [ "$count" -gt 0 ] || break
  start=$((start + count))
done
jq -s --arg site "$SITE" --arg at "$AT" '{jira_capture: 1, site: $site, captured_at: $at, pages: .}' \
  page-*.json > capture.json
```

得到的文件是这样的结构。分页的顺序无关紧要；同一页保存了两次会被忽略，但同一个 issue 内容不同（来自两次不同的抓取）会被拒绝。

```json
{"jira_capture": 1, "site": "https://acme.atlassian.net", "captured_at": "2026-10-09T12:00:00Z",
 "pages": [{"startAt": 0, "maxResults": 100, "total": 230, "issues": [{"id": "10001", "key": "ACME-1", "self": "https://acme.atlassian.net/rest/api/3/issue/10001", "fields": {"...": "..."}, "changelog": {"startAt": 0, "total": 4, "histories": ["..."]}}]}]}
```

- 退出码：`0` 表示完整；`2` 表示出错，没有追加任何内容；`3` 表示抓取结果不完整，完整的 issue 已经写入。以下情况算不完整：分页没有覆盖全部结果；某个 issue 的变更历史（changelog）或评论缺失或被截断（搜索接口每个 issue 只返回有限条变更历史）；某个 issue 的状态变更串不到它的当前状态。这样的 issue 整个都不导入，因为只导入一部分历史，回放出来的状态是错的。重新保存后再转换一次即可。
- `--dry-run` 打印摘要和计划写入的事件，不写入任何东西。再次转换同一份或有重叠的抓取结果，只会追加新内容。
- 数据集目录必须已存在。`events.jsonl` 和 `evidence.jsonl` 会在第一次写入时创建。导入不需要 `policy.json`。一个数据集只导入一个 Jira 站点。

**只有你在 `jira.json` 里按状态 id 列出的状态转换（transition）才会成为决定：**

```json
{"statuses": {"10003": "approve", "10004": "reject", "10005": "revoke", "10007": "reconsider"}}
```

状态 id 可以用 `GET /rest/api/3/status` 查，也可以看摘要里 `status ids in transitions` 那一行，以及存档文件里的 `toString` 名称。不接受状态名：状态改名后 id 不变，而且两个工作流可以各有一个叫 “Done” 的状态。没有 `jira.json` 时，任何转换都不是决定。

**导入工具从不读取或写入 `policy.json`：`jira:<accountId>` 形式的参与者 ID 对应什么角色，由你自己配置。** accountId 是不透明的字符串。请在 Jira 里核实每个账号（`GET /rest/api/3/user?accountId=...`），不要按显示名判断，因为任何人都可以把显示名改成任何内容。

| Jira | 事件 |
|---|---|
| 创建 issue | `creator`（不是 reporter）的 `request` |
| 评论 | 评论作者的 `comment` |
| 转换到列为 `approve`、`reject` 或 `reconsider` 的状态 | 对应的动作，参与者是变更历史的作者 |
| 转换到列为 `revoke` 的状态 | 撤销这个 issue 上最近一次记为 `approve` 或 `reject` 的转换；没有的话记为 `comment` |
| 其他状态转换 | `comment` |
| 应用（Automation for Jira 等）或类型未知的账号做的任何操作 | 只记为 `comment` |
| 没有作者的变更（系统、匿名或无法使用的账号 id） | 不产生事件，列为跳过（skipped） |
| 其他变更项（经办人、解决结果、优先级、自定义字段，包括名叫 “status” 的自定义字段）、链接、编辑 | 不产生事件 |

文本从不被解析：评论里的 “/approve”“我们 PM 已经批准了” 或对产品经理的 @提及，都只是作者的一条 `comment`。编辑只会增加一个新的证据版本，不产生事件；被删除的评论仍保留在数据集里，并报告为已消失（gone）。已停用或已删除的账号仍保留原来的 accountId，所以它过去的操作仍然归属于这个账号，算不算数由权限策略决定。被移动的 issue（key 变了）会被跳过并报告。

示例：[`examples/orgmemory/jira-example`](examples/orgmemory/jira-example/) 是用 `orgmemory jira convert` 从两份合成抓取结果（在 `tests/fixtures/jira/` 里）生成的，附有[回放报告](examples/orgmemory/jira-example/replay-report.md)和[审计报告](examples/orgmemory/jira-example/audit-report.md)。`ACME-1` 就是 Jira 上的功能A故事：

1. 产品经理把 issue 转到 Rejected。
2. 客户经理把它拖到 Approved，这次操作被忽略。
3. 客户把它转到 “Reconsideration requested”。
4. 两天后产品经理批准了它，记为同一个人推翻自己的决定（`decision_reversed`）。

在 `ACME-3` 里，一个把显示名改成和产品经理一样的账号发了 “/approve”，还修改了一个名叫 “status” 的自定义字段，这些都不构成决定。在 `ACME-2` 里，Automation for Jira 转到 Approved 只是一条评论，产品经理的批准才是决定。

### HTML 报告

`memfalsify audit`、`memfalsify diff`、`orgmemory replay` 和 `orgmemory audit` 都支持 `--format html`，输出一个自包含的网页，方便分享给不运行这些工具的人：用浏览器打开、作为邮件或工单附件，或作为 CI 产物上传。

```bash
memfalsify audit MEMORY.md --evidence evidence --format html -o report.html
orgmemory audit <数据集> --as-of 2026-10-31T00:00:00Z --format html > audit-report.html
```

- 内容与 Markdown 报告相同：先是汇总表，然后按状态（MemFalsify）或严重程度（`orgmemory audit`）分组列出发现项，每条证据引文都带 `路径:行号`、字符范围和完整的 SHA-256；`orgmemory replay` 列出每个议题的状态、授权决定和决策轨迹。有对应小节的汇总项会链接过去（状态、变更分组、议题，或严重程度的合计）。
- 自包含：CSS 内联，没有 JavaScript，不加载任何东西（字体、图片、脚本都没有），离线也能看。`Content-Security-Policy` meta 标签（`default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'`）即使某个值漏了转义，也会阻止加载资源和执行脚本。文件是 7 位 ASCII（其他字符写成 `&#...;` 字符引用），重定向和邮件中转都不会改坏编码。
- 不可信文本只当文本显示：记忆、证据、文件名、审核记录、事件日志、策略里的名字和来源 URI 都做 HTML 转义。控制字符、双向文本（bidi）字符、零宽字符、变体选择符等不可见字符显示为 `U+XXXX` 标记，而不会被渲染。只有 `http` 和 `https` 来源 URI 会变成链接，并带 `rel="noopener noreferrer"`；`javascript:`、`data:` 等其他协议只显示为纯文本。来源 URI 里若藏着不可见字符，也只显示为纯文本。
- 浅色和深色模式跟随系统（`prefers-color-scheme`）；在手机上表格横向滚动，页面本身不会横向滚动；表格带标题（caption）和表头作用域（scope）。
- 输出确定：同样的输入得到同样的字节。示例目录里，HTML 报告就放在 Markdown 报告旁边（[`examples/memfalsify/report.html`](examples/memfalsify/report.html)、[`examples/orgmemory/audit-report.html`](examples/orgmemory/audit-report.html) 等）；GitHub 只显示其源码，下载后再打开查看。
- MemFalsify 的 HTML 报告和它的 Markdown、JSON 报告一样，永远不会被当作证据读取。`memfalsify history` 和 MCP 工具仍只输出 Markdown 或 JSON。

## 数据格式

JSON 对象里重复的键是无效输入，嵌套对象里的重复键也一样。这适用于 `reviews.jsonl`、`events.jsonl`、`evidence.jsonl`、`github-sync.jsonl`、`policy.json`、`github.json`、交给 `orgmemory github convert` 的抓取文件、评测用的 `labels.jsonl` 和 `case.json`，以及 GitHub API 的响应。命令以退出码 2 失败，并报告 `<文件>:<行号>: invalid JSON: duplicate key`。解析不会保留后出现的那个值。

**`reviews.jsonl`**（MemFalsify，可选）：人工确认的判断。

```json
{"memory_id": "m-54b237eb", "relation": "contradicts", "path": "记录/2026-10-07-决策记录.md", "quote": "产品经理拒绝了功能A（批量导出），不随11月版本发布。", "reviewer": "张三", "note": "可选"}
```

`memory_id` 由记忆的规范化文本生成。记忆一旦被修改，ID 就会变；`reviews.jsonl` 里仍指向旧 ID 的确认会让审计直接报错（`unknown memory_id`，退出码 2），需要重新确认并更新或删除那一行。`quote` 必须逐字出现在 `path` 指向的文件中，`path` 相对于证据目录。对转写稿或 OCR 旁车文件，引用必须落在同一个字幕条目或同一行内，可选的 `"locator": "t=12.5,17"` 指定是哪一个。

**组织数据集**：一个包含三个文件（外加一个可选文件）的目录。

- `events.jsonl`：每行一个事件，只追加。字段：`id`、`timestamp`（带时区，表示生效时间）、`actor`、`action`、`subject`、`evidence_ids`（至少一个），以及 `note`（动作为 `raise_risk` 时必填）、`dependency`（动作为 `dependency` 时必填）、`supersedes`（动作为 `revoke` 时必填，且必须指向当前生效的决定）。动作：`request`、`reconsider`、`raise_risk`、`dependency`、`approve`、`reject`、`revoke`、`comment`。
- `policy.json`：`{"actors": {人: [角色]}, "permissions": {动作: [角色]}}`，以及可选的 `"as_of"`（ISO 8601，带时区：`actors` 中的角色从何时生效）。不在名单里的人没有任何角色。`pending_approval` 列出既能批准又能拒绝的角色。`permissions` 还可以列出 `manage_roles`：哪些角色可以在 `policy-log.jsonl` 中记录变更。
- `evidence.jsonl`：`id`、`path`、原始字节的 `sha256`、逐字的 `quote` 及其 `[start, end)` 字符区间、`source_uri`、`source_kind`、`captured_at`；只有引用来自转写稿或 OCR 旁车文件时才有 `locator`（例如 `t=12.5,17`，每次加载都会重新核对它以及旁车文件与媒体文件的绑定）。旁车记录若省略 `locator`，校验失败。请用 `orgmemory evidence add` 生成，不要手算哈希；对旁车文件它会记录 locator，`--locator` 可以指定字幕条目或行。
- `policy-log.jsonl`（可选，只追加）：每行一次角色变更。字段：`id`（唯一，不能与事件 ID 相同，也不能以 `gh:` 开头）、`timestamp`（带时区，表示生效时间）、`actor`（做出变更的人）、`action`（`grant` 或 `revoke_role`）、`subject_actor`（角色发生变化的人）、`role`、`evidence_ids`（至少一个），以及可选的 `note`。请用 `orgmemory policy grant` / `orgmemory policy revoke` 追加。规则见[随时间变化的角色](#随时间变化的角色)。

回放规则：事件按生效时间排序，时间相同时保持文件顺序。每个事件都按参与者在该事件生效时间持有的角色来判断（没有角色变更日志时，就是 `policy.json` 里的角色）。回放前会校验整个日志，包括 `--as-of` 之后的事件和角色变更。有权限的 `approve` / `reject` 会设定状态，并清除“申请重新考虑”、待审批和被忽略的越权尝试。决定被推翻或同一时刻出现相互冲突的决定时，都会记录在 `conflicts` 中。

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

**Jira 导入相关文件**（在同一个数据集目录里）：

- `jira.json`（可选，归你所有；导入工具只读取它）：`{"statuses": {"<状态 id>": "approve" | "reject" | "revoke" | "reconsider"}}`，键是状态 id。修改它只影响尚未写入日志的事件；已有事件如果按新配置会得到不同结果，会被报告为漂移（drift），但绝不会被改写。
- `jira-sync.jsonl`：每次实际写入的运行追加一行（`--dry-run` 不写），字段有 `site`、`source`（`convert`）、`captured_at`、`finished_at`（为了可重复生成，取抓取时间）、`complete`、`pages`、`issues`、`total`、`duplicates`、`events_appended`、`evidence_appended`、`skipped`、`incomplete`、`gone`、`drift` 和 `importer`。记录下来的 `site` 把数据集锁定在一个 Jira 站点上。
- `sources/jira/<KEY>/<对象>-<hash12>.txt`：每个 issue 和每条评论的每个版本各一个文件，每次状态转换也各一个，只创建、不修改；`<对象>` 是 `issue-<issue id>`、`comment-<id>` 或 `transition-<变更历史 id>`。格式与 GitHub 的相同：ASCII 事实行；白名单字段组成的 JSON 记录（非 ASCII 字符全部转义，其中包括账号类型、是否启用和显示名，只供审计参考）；分隔行 `--- text from Jira, flattened to plain text; untrusted ---`；然后是标题和描述，或评论正文。富文本（Atlassian Document Format）会被展开成纯文本：文字、提及、表情和链接地址，每个块一行。示例里 `ACME-1` 的转换 20102：

```text
Jira transition 20102 on ACME-1 by jira:61a0c3e5f2b1d40069a1b2c3 (atlassian) at 2026-10-07T15:00:00.000Z: status 10002 to 10004
{"author":{"accountId":"61a0c3e5f2b1d40069a1b2c3","accountType":"atlassian","active":true,"displayName":"Zhou (Product)"},...,"status":{"from":"10002","fromString":"In Review","to":"10004","toString":"Rejected"}}
--- text from Jira, flattened to plain text; untrusted ---
```

| Jira ID | 格式 | 示例 |
|---|---|---|
| 议题 | issue key | `ACME-12` |
| 参与者 | `jira:<accountId>` | `jira:61a0c3e5f2b1d40069a1b2c3` |
| 事件 | `jira:<key>/` 加上 `created`、`comment/<id>` 或 `transition/<变更历史 id>` | `jira:ACME-1/transition/20102` |
| 证据 | `jira:<key>/` 加上 `issue/<issue id>@<th12>`、`comment/<id>@<th12>` 或 `transition/<变更历史 id>`，再加 `/fact` 或 `/text` | `jira:ACME-1/transition/20102/fact` |

时间一律以 UTC 加毫秒保存，所以在另一个个人时区设置下抓取，得到的 ID 也相同。

## Agent 技能与 Claude Code 插件

[`skills/memfalsify`](skills/memfalsify/SKILL.md) 和 [`skills/orgmemory`](skills/orgmemory/SKILL.md) 教 Agent 怎么用这两个工具、边界在哪里：先出报告，得到许可才改记忆，绝不把自己的判断当作人工确认写入，绝不改权限策略。GitHub 或 Jira 账号不是已认证的人，`memlab` MCP 工具引用的文件文本也不可信。技能里写了什么时候用这些只读工具、什么时候用命令行，以及怎样把 [GitHub Action](docs/github-action.md) 加到仓库里，让仓库核对自己的指令文件。

本仓库同时是一个 Claude Code 插件市场。装一个插件，就同时得到两个技能和只读的 `memlab` MCP 服务器：

```bash
claude plugin marketplace add timothyzhbw-jpg/memory-lab
claude plugin install memory-lab@memory-lab
```

技能变成 `/memory-lab:memfalsify` 和 `/memory-lab:orgmemory`。插件用 `memlab-mcp --root ${CLAUDE_PROJECT_DIR}` 启动服务器，工具只能读项目目录，而且从不传 `--allow-model-verifiers`。插件不安装 Python 包：`memfalsify`、`orgmemory` 和 `memlab-mcp` 必须在 Claude Code 启动时的 `PATH` 上，例如来自 `./scripts/setup-local.sh` 创建的虚拟环境。

Codex 则把技能复制到 OpenAI 文档里的用户技能位置 `~/.agents/skills/`，再用 `codex mcp add` 注册服务器。本 README 以前写的是 `~/.codex/skills/`；请删掉那里的副本，免得 Codex 把每个技能列两次。

```bash
cp -R skills/memfalsify skills/orgmemory ~/.agents/skills/
codex mcp add memlab -- /absolute/path/to/memory-lab/.venv/bin/memlab-mcp --root /absolute/path/to/project
```

检查安装、更新、`config.toml` 写法、Windows 等细节见 [docs/install-agents.zh-CN.md](docs/install-agents.zh-CN.md)。

## MCP 服务器

`memlab-mcp` 是一个只读的 [Model Context Protocol](https://modelcontextprotocol.io) 服务器，给本地 Agent 提供十个工具：`memfalsify_claims`、`memfalsify_audit`、`memfalsify_diff`、`memfalsify_history`、`memfalsify_rollback`、`orgmemory_validate`、`orgmemory_replay`、`orgmemory_explain`、`orgmemory_audit` 和 `orgmemory_changes`。`memfalsify_diff` 比较 git 里一份记忆文件的基准修订版本和另一端（默认是工作区），只审计新增或修改过的记忆。`memfalsify_history` 给出每条记忆由哪个提交引入、最后由哪个提交修改，并列出被删除的记忆。`memfalsify_rollback` 返回一条记忆的补丁，从不应用它。用 `pip install -e packages/memlab-mcp` 安装（`./scripts/setup-local.sh` 已包含）。所有路径都限制在 `--root` 之内。这三个 git 工具还会拒绝工作区、git 目录、公共目录或对象库落在该根目录之外的仓库。不加 `--allow-model-verifiers` 启动时，模型验证器一律关闭；加上之后，`memfalsify_audit` 和 `memfalsify_diff` 才能使用它们。`openai` 验证器的端点和模型只能由服务器的 `--openai-*` 启动参数设定，工具参数无法更改。五个只读资源按 URI 返回同样的文本：`memlab://memory/<path>/claims`（JSON）和 `/audit`（Markdown，一律使用 lexical 验证器），以及 `memlab://dataset/<path>/state`、`/audit` 和 `/explain/<subject>`（Markdown）。三个提示词 `audit_memory`、`review_memory_changes` 和 `explain_status` 引导 Agent 走完技能里“先出报告”的流程。它们把参数当作数据引用，从不让 Agent 写文件。工具、资源、提示词的参数，客户端注册方式和限制见 [docs/mcp.zh-CN.md](docs/mcp.zh-CN.md)。

## 网页

`memlab-ui` 是一个本地只读网页，适合不想学命令行的人。用 `pip install -e packages/memlab-ui` 安装
（`./scripts/setup-local.sh` 已包含），然后对一个项目文件夹启动它：

```bash
memlab-ui --root /path/to/project      # 然后打开 http://127.0.0.1:8765/（用 --port N 更换端口）
```

- “首页”列出项目中的 Agent 文件（`memfalsify discover` 找到的文件，再加上 `MEMORY.md`），一步就能检查其中一个。
- “检查 Agent 文件”把文件与整个项目或某个文件夹做词法审计，可以附带审阅文件、基准日期；根文件夹是 git 仓库时，还可以
  只检查某个修订之后改动的陈述。每条被标出的陈述都与看起来有出入的引文并排显示，不同的数值会被高亮，并附一句建议；
  其余陈述折叠起来。
- “团队决策”显示决策日志中每个主题的状态、由谁依据哪段引文作出决定、哪些事件算数的时间线、用平实语言写出的决策
  审计发现，以及两个日期之间的变化。

它只绑定 127.0.0.1，拒绝 `Host` 头不是 127.0.0.1 或 localhost 的请求（防 DNS 重绑定）以及其他网站发来的请求
（`Sec-Fetch-Site`、`Origin`），只响应 GET 和 HEAD，限制每个请求可用的连接、页面、字节数和文件夹数量，
用与 MCP 服务器相同的基于文件描述符的受限读取把所有路径限制在 `--root` 之内，不写任何文件，除非用
`--allow-model-verifiers` 启动否则不启用模型验证器（启用后也只对它在终端打印的带密钥地址运行模型检查），转义所有不可信内容，并发送严格的 Content-Security-Policy：
没有 JavaScript，也不从其他地方加载任何内容。页面有英文和简体中文两种语言（`?lang=zh`）。与 MCP 服务器一样，它需要
POSIX `O_NOFOLLOW` 和 `dir_fd` 支持，因此在 Windows 上会拒绝启动并以退出码 2 结束。页面、选项、安全规则和局限见
[docs/ui.zh-CN.md](docs/ui.zh-CN.md)。

## 验证后端：词法、LLM、Jev、兼容 OpenAI 的模型

验证器判断一条记忆和一段证据的关系：`supports`（支持）、`contradicts`（反驳）或 `unrelated`（无关），并给出理由。内置四个后端，`dual` 和 `cascade` 是 Claude 与 Jev 的组合方式：

| `--verifier` | 谁来判断 | 需要什么 | 取舍 |
|---|---|---|---|
| `lexical`（默认） | 词语重合、反义词、数字、否定 | 无 | 离线、结果确定、可解释；识别不了换种说法的表述 |
| `llm` | Claude 阅读证据并写出判断理由（`--llm-model`，默认 `claude-opus-5-5`） | `pip install 'memfalsify[llm]'` 和 Anthropic 凭据 | 能理解换说法和上下文；按调用计费，**会把记忆和证据原文发给 Anthropic API** |
| `jev` | [Jev](https://docs.typesafe.ai)：TypeSafe AI 的判断模型，只做决策不生成文字，在三种关系中选一个并给出各自的概率（`--jev-model`，默认 `jev-latest`） | `pip install 'memfalsify[jev]'` 和 `TYPESAFE_API_KEY` | 成本只有 LLM 评委的零头，带校准过的置信度；没有文字解释，**会把记忆和证据原文发给 TypeSafe API** |
| `dual` | `llm` 和 `jev` 各自独立判断 | 两者都要 | 意见一致就照常输出；不一致就标为 `disputed`，报告里列出双方意见 |
| `cascade` | 先问 `jev`；只有 Jev 的置信度低于 `--accept-at`（默认 0.8）时才问 Claude | 两者都要 | Jev 大多有把握时，成本接近只用 Jev；报告里会同时显示 Jev 没把握的意见和 Claude 的判断。Jev 有把握的答案不会再被交叉核对 |
| `openai` | 任何兼容 OpenAI Chat Completions API 的模型阅读证据并写出判断理由：OpenAI、xAI Grok、DeepSeek、Google Gemini 或本地 Ollama（`--provider`），也可以是其他端点（`--base-url`）。`--openai-model` 必填 | `pip install 'memfalsify[openai]'`，以及服务商对应变量里的 key（`--api-key-env` 可指定别的变量）；Ollama 不需要 | 与 `llm` 相同的提示和判断格式，模型由你选；**会把记忆和证据原文发到配置的端点**，用 Ollama 时数据留在本机 |

```bash
memfalsify audit MEMORY.md --evidence evidence --verifier dual
```

两个模型后端刻意采用不同的工作方式：Claude 推理出结论并写明原因，Jev 直接输出带概率的决策。两者拿到的是同一套关系定义（`verify.py` 中的 `RELATION_CRITERIA`），所以它们意见不一致时，分歧来自证据本身，而不是题目措辞，这时就该由人去读证据。Jev 置信度低于 `JevVerifier(threshold=0.5)` 的答案按 `unrelated` 处理；理由中会保留三个概率和实际使用的模型版本（例如 `jev-1.13.0`，因为 `jev-latest` 只是别名）。

**模型会看到哪些片段。** 证据文件先按句子切成片段（遇到 。！？； 或换行就断开），模型每次只看一个片段。仍然先用词语索引预筛选：模型验证器的门槛更低（`--min-coverage`，默认 0.2，而词法验证是 0.5），这样换了说法的片段也能被判断；每条记忆最多只送匹配度最高的 `--max-judged` 个片段（默认 8）。因此每个模型的调用次数上限是“记忆条数 × max-judged”。和记忆没有任何共同词语的片段永远不会被送去判断。

**成本与耗时。** 报告会列出每个模型后端发出的请求数，以及 API 报告的 token 数（JSON 中的 `verifier_stats`）。下面三项能降低成本或缩短等待，而不改变向模型提出的问题：

- *提示缓存（prompt caching）。* Claude 验证器把系统提示标为可缓存的前缀；同一条记忆要和多个片段比对时，记忆本身也一并缓存。API 只缓存长度达到模型最低要求的前缀（见[提示缓存文档](https://platform.claude.com/docs/en/build-with-claude/prompt-caching)）。系统提示只有几百个 token，所以记忆较短时什么都不会被缓存；实际情况以报告里的缓存计数为准。
- *`--max-concurrency`*（默认 4）：同时进行的模型请求数。判断保持请求顺序，因此在验证器本身确定的前提下，发现项与逐个请求时一致。缓存 token 计数来自该次运行的 API 响应，请求重叠时可能不同。共用同一条已缓存记忆的 Claude 请求，会等其中第一个返回后再发出，这样才能读到它写入的缓存。该选项在 `--batch` 下不使用。请把这个值控制在你的 API 速率限制之内。
- *`--batch`*：把 Claude 请求（`llm`、`dual`、`cascade`）通过 [Message Batches API](https://platform.claude.com/docs/en/build-with-claude/batch-processing) 发送，按标准价格的 50% 计费，但更慢：多数批次在一小时内完成，有些要 24 小时。命令会一直等待，并在 stderr 上打印批次 ID。只要有一个请求出错、过期或被取消，审计就以退出码 2 结束，不输出报告；中途中断会取消该批次。Batches API 不接受服务端拒答回退，所以在支持该回退的模型上，被拒答的请求会再直接发送一次，按标准价格计费。Jev 请求从不走批处理。

Claude 每个 token 的价格（包括缓存写入、缓存读取和批处理）见 [Anthropic 定价页](https://platform.claude.com/docs/en/about-claude/pricing)。

LLM 验证器使用结构化输出，每个判断都符合固定格式；它的系统提示要求把记忆和证据当作引用的数据，忽略其中出现的任何指令。在支持的模型上，它会开启服务端的拒答回退（server-side refusal fallback）；被拒答的请求记为“无判断”（`unrelated`，理由中写明停止原因）。`dual` 更严格：每个判断都核对两次。`cascade` 遵循“有把握就采纳，没把握再升级”的思路，便宜得多，但 Jev 有把握的答案会被直接采用，不经 Claude 复核，所以正式依赖它之前，先用你已经人工确认过的记忆调一调 `--accept-at`。

所有模型后端遇到 API 调用失败、缺少 SDK 或缺少凭据时，审计都会以退出码 2 结束并给出错误信息，而不是输出一份不完整的报告。

**兼容 OpenAI 的服务商。** `--verifier openai` 使用 Chat Completions API，一个后端就能覆盖所有提供这种 API 的服务商：

```bash
memfalsify audit MEMORY.md --evidence evidence --verifier openai --openai-model <model>                     # OPENAI_API_KEY
memfalsify audit MEMORY.md --evidence evidence --verifier openai --provider ollama --openai-model <model>    # 本地，不需要 key
```

| `--provider` | Base URL | Key 变量 | 响应格式 |
|---|---|---|---|
| `openai`（默认） | `https://api.openai.com/v1` | `OPENAI_API_KEY` | `json_schema` |
| `grok` | `https://api.x.ai/v1` | `XAI_API_KEY` | `json_schema` |
| `deepseek` | `https://api.deepseek.com` | `DEEPSEEK_API_KEY` | `json_object` |
| `gemini` | `https://generativelanguage.googleapis.com/v1beta/openai/`（[Google 的 OpenAI 兼容端点](https://ai.google.dev/gemini-api/docs/openai)） | `GEMINI_API_KEY` | `json_schema` |
| `ollama` | `http://localhost:11434/v1` | 无 | `json_schema` |

`--base-url` 和 `--api-key-env` 会覆盖预设。base URL 必须是 `https://`（只有 `localhost`、`127.0.0.1`、`::1` 允许 `http://`），不能带用户名、密码、查询参数或片段。`--api-key-env` 接收的是变量名：没有任何选项接收 key 本身。服务商的错误只显示 HTTP 状态码、错误的 type、code 和 param（各自必须是简单的标记）以及一条固定提示；服务商自己的消息从不输出，模型理由里的 key 会被遮掉。OpenAI SDK 自己的 `OPENAI_*` 环境变量不会生效，给一个服务商的设置不会被发给另一个。模型没有默认值，因为各服务商的模型名不同，而且会变。

请求只发往 base URL：不跟随重定向，也不使用环境里的代理设置；证书按 OpenAI SDK 自带的方式校验（平台的信任库）。`--openai-response-format` 可选 `json_schema`（严格结构化输出）、`json_object` 或 `text`，默认值见上表（用 `--base-url` 时为 `json_schema`）。端点不支持该格式时会返回 HTTP 400 或 422，审计结束，错误信息会建议更简单的格式；不会自动换格式重试。只读取正常结束（`finish_reason` 为 `stop`）的回答。被截断或被过滤的回答、不是合法判断的回答、空回答和拒答都记为无判断（`unrelated`），绝不会算作支持。这个后端不支持 `--batch`，`dual` 和 `cascade` 仍然是 Claude 配 Jev。key 和服务商的更多说明见 [docs/local-setup.zh-CN.md](docs/local-setup.zh-CN.md)。

在 Python 里使用：

```python
from pathlib import Path

from memfalsify import DualVerifier, JevVerifier, LLMVerifier, audit

report = audit(Path("MEMORY.md"), Path("evidence"), verifier=DualVerifier(LLMVerifier(), JevVerifier()),
               min_coverage=0.2, max_judged=8, max_concurrency=4)
```

`LLMVerifier(batch=True)` 使用 Message Batches API。`OpenAICompatVerifier("<model>", provider="ollama")` 是兼容 OpenAI 的后端（`base_url` 和 `api_key_env` 覆盖预设）。任何有 `name` 属性和 `judge(claim, passage) -> Judgment` 方法的对象都可以当验证器，其他模型可以用同样的方式接入。还可以选择实现 `judge_many(pairs, max_concurrency)`：它一次拿到整次审计的全部（记忆，片段）对，并按相同顺序返回判断；`usage()` 则为报告返回 `VerifierStats`。模型的判断始终只是候选：它们永远不能代替人工确认。

## 评测集与 GitHub Action

[MemFalsify 评测集](benchmarks/memfalsify/README.md)包含十一组虚构案例、202 条英文、中文和西班牙文标注记忆。
[实测词法基线](benchmarks/memfalsify/RESULTS.md)的反证精确率为 0.71、召回率为 0.33，三分类准确率为 0.42。
这些案例用于检验已知失败模式，不能代表真实记忆的准确率。运行 `python benchmarks/memfalsify/run.py` 即可离线评测。

[组织记忆评测集](benchmarks/orgmemory/README.md)包含十组带标注的数据集、233 条标注（598 项检查），覆盖指定时间点的最终状态、
审计轨迹结果、`orgmemory audit` 发现项和校验错误。这些案例专门攻击权限规则：未知和仿冒的参与者、角色变更、同一时刻的决策、
撤销链、截止时间与文件顺序、伪造证据、格式错误的日志，以及一份 GitHub 导入数据。
[实测结果](benchmarks/orgmemory/RESULTS.md)中 590 项检查通过，8 项失败来自一个发现：写进 `policy.json` 的角色变更
也会作用于过去的事件（请改为记录在 `policy-log.jsonl` 中）。首轮评测的另一个发现，JSON 重复键，现在会被拒绝。运行 `python benchmarks/orgmemory/run.py` 即可离线评测，任何不符都会以退出码 1 结束。

[GitHub Action](docs/github-action.md)将明确列出的 Agent 指令文件（设为 `memory: auto` 时则是 `memfalsify discover`
找到的全部指令文件）与仓库证据核对，并把报告放入任务摘要。
默认严格模式在发现可能的反证时失败；无证据的记忆和措辞问题仍需人工审阅。
设置 `diff-base`（例如 PR 的基准提交，并给 `actions/checkout` 加上 `fetch-depth: 0`）后，Action 改为运行
`memfalsify diff`，PR 只会因为它新增或修改的记忆而失败。本地提交可以用
[pre-commit 钩子](README.md#pre-commit) `memfalsify-diff` 和 `memfalsify-audit` 做同样的检查。

建议给 checkout 设置 `persist-credentials: false`，避免把任务 token 留在 `.git/config` 中：

```yaml
steps:
  - uses: actions/checkout@11d5960a326750d5838078e36cf38b85af677262  # v4.4.0
    with:
      persist-credentials: false
  - uses: timothyzhbw-jpg/memory-lab@<40-character SHA>  # v0.5.0
    with:
      memory: CLAUDE.md AGENTS.md
```

记忆和证据文件的原始路径、符号链接解析后的路径都不能包含隐藏组件或忽略目录；被跳过的输入会注明原因。
Action 还会警告并跳过解析到工作区外的记忆符号链接。
Action 只使用固定到提交 SHA 的 Action，并从带哈希校验的锁文件安装 Python 依赖
（[详情](docs/github-action.md#dependencies-and-pinning)）。

## 信任边界与局限

- 哈希只能证明某段引用确实存在于特定的字节里，不能证明来源是真实的，也不能证明这段话的意思就是某人所说的那样。
- 词法和模型找到的候选只是线索，不是结论。只有 `reviews.jsonl` 能给记忆下结论。
- `--verifier llm` 会把记忆和证据发给 Anthropic API，`jev` 会发给 TypeSafe API，`dual` 和 `cascade` 两边都会发，`openai` 会发到你配置的端点（本地 Ollama 服务则留在本机）。只对允许外发的材料使用它们。使用 `--batch` 时，Anthropic 会把批次的请求和结果保留最多 29 天，该工作区（workspace）里有权限的人都能看到；如果觉得太久，可以通过 API 删除该批次。
- `orgmemory audit` 的发现项和 `orgmemory changes` 只是复述日志和权限策略里的内容，是供人判断的线索，不是结论。日志无法记录“风险已解决”，所以已批准的议题在批准之前提出的每个风险都会一直对应一个 `risk_after_approval` 发现项。自己批准自己的请求、被忽略的越权尝试也可能是正常的：谁能做什么由权限策略决定。
- 导出的 ADR 只是复述回放后的日志。它的 Context 列出的是记录在案的申请和风险，不是任何人的理由；记录也不会自动更新：日志变化后需要重新导出。
- 参与者（`actor`）ID、`policy.json` 和 `policy-log.jsonl` 被视为可信输入。目前还没有身份认证或签名溯源。记录在 `policy-log.jsonl` 里的角色变更带有生效时间，但修改 `policy.json`（包括不分时间的 `permissions`）或改写角色变更日志，仍然会改变每一次回放的结果。
- JSON 对象里重复的键会被拒绝，包括嵌套对象里的重复键。如果保留后出现的值，`events.jsonl` 的同一行可以让读文件的人看到一个 `actor`，回放却使用另一个；`policy.json` 里把同一个人写两次时，也会按后一次的角色授权。
- 证据是 UTF-8 文本。图片、音频、视频和 PDF 只能通过你提供的转写稿或 OCR 旁车文件成为证据，而旁车文件只是某人（或某个模型）对媒体内容的读取结果：`source_sha256` 一致只说明它描述的是哪份字节，不说明它准确。没有可用旁车文件的媒体会在报告的 `Skipped evidence` 部分列为跳过。
- 中文词法判断没有词典：会漏掉同义改写（例如“冻结招聘”与“不招”），否定和情态只按固定的提示词表判断。只有汉字会被切成二元组和三元组，日文假名和韩文不会。
- 不检测硬链接。硬链接是同一个文件的另一个名字，所以同一文件系统上、被硬链接进证据目录的文件会被读取，并可能被引用进报告，不管它的另一个名字在哪里（符号链接会被检查并拒绝；硬链接无法与原文件区分，而跳过所有链接数大于 1 的文件也会误跳合法的链接）。Git 不保存硬链接，所以检出或 PR 无法植入硬链接；能写入被审计目录的人可以。只审计你自己控制的目录（见 [GitHub Action](docs/github-action.md) 的说明）。
- `diff`、`history` 和 `rollback` 信任本地 git 仓库：它们按仓库里的对象和引用读取内容，归属结果的可靠程度取决于提交历史本身（变基、压缩合并和第一父提交链都会改变“谁引入了这条记忆”的结论）。
- GitHub 导入：GitHub 账号不等于经过认证的个人。用某人自己的 token 运行的自动化（例如使用维护者个人访问令牌的 workflow）无法与这个人区分；审查不带应用标记；合并队列和自动合并的合并者是 GitHub 记录的那个账号；由 issue 模板添加的标签可能被算在 issue 作者头上。不要在 `github.json` 里列出由模板、机器人或使用个人 token 的 workflow 添加的标签，给账号配置角色时也要谨慎。
- `policy.json` 是全局的：给一个 `gh:` 账号配置角色，等于在所有导入仓库的议题上都授予了这个角色。
- 在公开仓库里，任何人都可以开 issue、评论、审查 PR、关闭自己的 issue。这些都会以真实账号记录下来，只要账号没有配置角色，回放就会忽略它们，所以会出现 `unauthorized_change_attempts`。这是如实记录，不是 bug。
- 存档文件的哈希只能证明导入工具根据 API 响应生成的字节，不能证明 GitHub 实际返回了什么；交给 `convert` 的抓取结果会被当作“GitHub 的原话”，所以只转换你自己抓取的结果。抓取到的 GitHub 文本不可信，issue 正文里出现提示注入是预料之中的。这些文本永远不会进入 ID、备注、参与者或导入工具的输出，但 `replay` 和 `explain` 会原样打印证据引用（单行，不含终端转义序列和双向文本控制字符，但可能包含 markdown 或 HTML）。
- HTML 报告把不可信文本显示为惰性文本（已转义，不可见字符显示为 `U+XXXX`，没有脚本，不加载任何东西），但文字内容仍然是记忆、证据或日志里写的内容；来源链接指向证据记录的那个 http(s) URL，链接文字就是完整的 URL。
- Jira 导入：Jira 账号不等于经过认证的个人。任何能执行转换、把 issue 转到 `jira.json` 所列状态的人，都会被记录为做出了这个决定，只有 `policy.json` 会把它过滤掉，所以只列出工作流限制了转换权限的状态。以触发者身份运行的自动化规则，会被算在触发者头上；由规则把 Service Management 审批变成的转换，记为应用的 `comment`。只用解决结果（resolution）表达的决定（例如 “Done” 加 “Won't Do”）不会被映射。抓取结果会被当作 “Jira 的原话”，所以只转换你自己保存的抓取结果。来自私有项目的抓取结果和数据集包含私密文本，包括你的账号能看到的、仅对某个角色或组可见的评论，绝不要提交。
- GitHub token 只从环境变量读取，只发送给 `https://api.github.com`（从不跟随重定向到其他主机），也从不写入文件或打印出来。
- 从私有仓库导入的数据集和 `--save-capture` 文件包含私密文本，绝不要提交。目前没有脱敏流程：要从存档文件里删掉泄露的密钥只能手工编辑，之后 `validate` 会按设计报错。
- 这是一个研究用的 MVP，不是生产级基础设施。

## Python API

从 `memcore`、`memfalsify`、`orgmemory`、`memlab_mcp` 和 `memlab_ui` 包根导入库函数与模型。
每个包通过 `__all__` 明确列出公开名称，并附带 `py.typed`。
[API 参考](docs/api.zh-CN.md)介绍解析、审计、Git 历史、回放、GitHub 导入和服务器构建，
包含示例及 1.0 的稳定性政策。原有模块导入路径继续可用；CLI 配置辅助函数属于内部实现。

## 路线图

| | MemFalsify | 组织记忆 |
|---|---|---|
| **v0.1** | 文本记忆审计、证据追溯、人工确认流程 | 事件日志、状态机、权限规则、决策溯源 |
| **v0.2** | LLM + Jev 双重验证、cascade 模式 ✓ | 导入 GitHub Issues / PR ✓ |
| **v0.3** | 记忆的 Git 历史：diff、审查、回滚；跨 Agent 技能打磨 ✓ | 状态冲突与决策审计 ✓ |
| v0.4 | 多模态证据（带定位的 OCR / 转写）：转写稿与 OCR 旁车文件 ✓ | 跨平台组织记忆：Jira 导入（离线，基于保存的抓取结果）✓ |
| v1.0 | 基准测试、稳定 API | 基准测试、稳定 API |

各个包每个版本的变更见 [CHANGELOG.md](CHANGELOG.md)（英文）。

## 开发

本仓库由多个 AI agent 协作开发。角色分工、改动进入 `main` 前要过的关卡，以及报告和日志格式，见 [process/README.zh-CN.md](process/README.zh-CN.md)。

```bash
pip install pytest
python -m pytest     # 直接针对 packages/*/src 运行，无需安装
```

目录结构：`packages/memcore`（证据完整性、JSONL）、`packages/memfalsify`、`packages/orgmemory`、`skills/`、`examples/`、`tests/`。提交贡献时请附上最小的合成复现示例。不要提交私密证据或凭据。

## 许可证

Apache-2.0，见 [LICENSE](LICENSE)。
