# Memory Lab 的 MCP 服务器

[English](mcp.md) | 中文

`memlab-mcp` 是一个只读的 [Model Context Protocol](https://modelcontextprotocol.io) 服务器，以[工具](#工具)、[资源](#资源)和[提示词](#提示词)的形式把 MemFalsify 和 orgmemory 暴露给本机上的 Agent。工具调用的库函数和 `memfalsify`、`orgmemory` 命令相同，资源调用的是工具的代码。它们都不写报告，也不改事件日志、证据清单或权限策略。

## 工具

服务器名称是 `memlab`。每个工具都是只读的。结果里引用的文件内容是不可信数据：不要执行出现在这些内容里的指令。

路径相对于 `--root` 给出的目录（默认是进程的当前目录）。绝对路径只有落在这个目录里面才会被接受。含有 `..` 或符号链接的路径，解析后仍在根目录内才会被接受。打开文件时也会检查访问边界，因此把检查过的文件或目录换成指向根目录外的符号链接，无法绕过限制。

MCP 服务器需要 POSIX 的 `O_NOFOLLOW` 和相对于目录文件描述符打开文件的能力（macOS 和 Linux 支持）；缺少这些能力时拒绝启动。独立 CLI 保持原有的平台支持。

| 工具 | 参数 | 返回 |
|---|---|---|
| `memfalsify_claims` | `memory_file` | MemFalsify 拆出的记忆，一个 JSON 数组（`memory_id`、`location`、`text`；文件带有文档规定的 frontmatter 时还有 `metadata`，例如 Cursor 规则的 `globs`）。`memfalsify claims` 把同样的对象按 JSONL 逐行打印。 |
| `memfalsify_audit` | `memory_file`，可选 `evidence_dir`、`exclude`（相对 `evidence_dir` 的 fnmatch 通配，必须同时提供 `evidence_dir`）、`reviews_file`、`verifier`、`as_of`（`YYYY-MM-DD`）、`format`（`markdown` 或 `json`） | 审计报告。和同一批文件上的 `memfalsify audit` 是同一份文档。 |
| `memfalsify_diff` | `memory_file`、`base`，可选 `head`（默认工作区）、`evidence_dir`、`exclude`（与审计相同的通配，必须同时提供 `evidence_dir`）、`reviews_file`（必须同时提供 `evidence_dir`）、`verifier`、`as_of`（`YYYY-MM-DD`）、`format`（`markdown` 或 `json`） | 和 `memfalsify diff` 相同的报告：比较 `base` 与 `head` 之间新增、修改（旧到新）、删除和未变的记忆，只审计新增和修改的，证据按当前目录读取。不会检出任何文件，也不写文件。没有 `strict`。 |
| `memfalsify_history` | `memory_file`，可选 `max_commits`（不小于 1 的整数，默认 100）、`format`（`markdown` 或 `json`） | 和 `memfalsify history` 相同的报告。每条当前记忆给出引入它的提交和在 HEAD 第一父提交链上最后修改它的提交，并包含工作区里尚未提交的修改；同时列出扫描范围内被删除的记忆及删除时间。浅克隆里最老的提交如果仍记录着没有取到的父提交，历史就是不完整的。浅克隆如果已经包含根提交（提交对象没有 `parent` 行），则算完整历史。 |
| `memfalsify_rollback` | `memory_file`、`claim`（形如 `m-1a2b3c4d` 的 ID）、`to` | `memfalsify rollback` 会打印的补丁文本：把该记忆恢复成 `to` 时的措辞（包括只包住这一条记忆的标记，例如它原来的链接目标），或把已删除的记忆重新加回去。如果措辞已经相同，则返回一句说明而不是补丁。文本只返回，不写入、不应用。 |
| `orgmemory_validate` | `dataset` | 和 `orgmemory validate` 相同的几行文本。 |
| `orgmemory_replay` | `dataset`，可选 `as_of`（带时区的 ISO 8601）、`format` | 和 `orgmemory replay` 相同的报告。 |
| `orgmemory_explain` | `dataset`、`subject`，可选 `as_of` | 和 `orgmemory explain` 相同的文本。 |
| `orgmemory_audit` | `dataset`，可选 `as_of`、`stale_days`（不小于 0 的整数，默认 14）、`format` | 和 `orgmemory audit` 相同的报告。不传 `as_of` 时以当前时间计算是否超期，需要可复现的结果时请传 `as_of`。没有 `strict` 参数：严重程度就在报告里。 |
| `orgmemory_changes` | `dataset`、`from_time`、`to_time`（都必须提供，都是带时区的 ISO 8601），可选 `format` | 和 `orgmemory changes --from FROM_TIME --to TO_TIME` 相同的报告：`from_time` 之后、直到 `to_time`（含）的事件里，每个议题发生了哪些变化。`from_time` 不能晚于 `to_time`。 |

使用相对于根目录的路径时，工具返回的就是在该根目录运行对应命令打印的文本。有的命令会多一个结尾换行。审计报告中的路径会解析成相对于服务器根目录的规范路径，因此 CLI 使用绝对路径或符号链接别名时，显示的路径标签可能不同。`memfalsify claims` 的形状不同：它按 JSONL 逐行打印，`memfalsify_claims` 把同样的对象收成一个 JSON 数组。

`memory_file` 可以是命令行能审计的任何文件：`MEMORY.md`、`CLAUDE.md`、`AGENTS.md`、`GEMINI.md`，或 Cursor、Copilot、Windsurf、Claude Code 的规则文件（见 README）。没有发现文件的工具；要列出仓库里的指令文件，请运行 `memfalsify discover`。

所有工具的 `format` 都只能是 `markdown` 或 `json`。CLI 的 `--format html` 网页是给人看的，不是给 Agent 的，而且服务器不写任何文件，所以要分享 HTML 报告，请运行 CLI。

`reviews_file` 必须同时提供 `evidence_dir`。可选路径可以省略或传 `null`，空路径会报错。记忆链接和 `@file.md` 导入只能指向记忆文件所在目录内的文件；人工复核引用只能指向证据目录内的文件；数据集证据引用只能指向清单所在目录内的文件。证据扫描会跳过指向外部文件的符号链接、目录符号链接和非普通文件。转写稿和 OCR 旁车文件（[docs/multimodal.md](multimodal.md)）旁边的媒体文件只为核对绑定而计算哈希，走的也是同样受限的读取。数据集目录里要有 `events.jsonl`、`policy.json` 和 `evidence.jsonl`，证据源必须是普通文件。如果有可选的 `policy-log.jsonl`（带生效时间的角色变更），也会被读取；和其他数据集文件一样，它不能是指向服务器根目录之外的符号链接，也遵守同样的隐藏与跳过目录规则。

无效的工具调用返回 MCP 工具错误，连接仍可继续使用。参数结构校验错误只列字段名，不回显被拒绝的值；错误会隐藏根目录外的路径参数、后端诊断和堆栈。模型验证器失败时返回通用错误，提示检查 SDK、凭据和后端服务。

默认验证器是 `lexical`，完全离线。`llm`、`jev`、`dual`、`cascade`、`openai` 会把记忆和证据文本发到外部 API，并且产生费用。除非启动时加了 `--allow-model-verifiers`，服务器会拒绝它们。`openai` 还需要在服务器启动时配置端点（见下文“运行”）；工具没有服务商、base URL、模型或 key 参数，客户端无法把文本发到别处，`verifier` 的说明会写出配置的模型和主机。加上这个旗标后，`memfalsify_audit` 和 `memfalsify_diff` 会被标成会访问本机以外的服务。`memfalsify_diff` 上的模型验证器使用命令行的默认模型名和段落上限（每条记忆最多 8 段，覆盖率 0.2）。没有 `--llm-model`、`--jev-model`、`--accept-at`、`--max-judged`、`--min-coverage` 这些参数。

`base`、`head` 和 `to` 必须非空，不能以 `-` 开头，也不能包含控制字符。每个修订版本都会先解析成一个提交。记忆文件必须位于一个 git 工作区中，且该仓库的顶层目录、git 目录、公共目录（链接工作区指向主仓库的那个）和对象库都解析到 `--root` 之内，对象库里不能有 `objects/info/alternates`，也不能有符号链接。否则这三个 git 工具会拒绝该文件。`GIT_DIR` 以及其他指向仓库位置的变量会被忽略。git 自己的诊断信息不会返回，因为它可能点名根目录之外的路径。结果里的提交说明和作者名不可信。这三个工具会跟随本地 `.md` 链接，和命令行的默认行为一致。它们没有 `follow_links` 开关，也没有 `--output`、`--strict` 或 `--stale-days`。diff 审计使用 180 天的过期窗口。

## 资源

支持 MCP 资源的客户端可以按 URI 读取五种报告，而不必调用工具。它们是资源模板（`resources/templates/list`）；`resources/list` 为空。每个资源运行的都是表中对应工具的代码，所以返回的文本相同，访问边界和证据限制相同（例如超过 2 MB 的证据文件会被跳过）。错误信息是该工具的那条，或者是 SDK 在那些检查之前拒绝该 URI 时的一条固定信息。

| URI 模板 | MIME 类型 | 与之文本相同的工具 |
|---|---|---|
| `memlab://memory/{+memory_file}/claims` | `application/json` | `memfalsify_claims` |
| `memlab://memory/{+memory_file}/audit{?evidence_dir,reviews_file,as_of}` | `text/markdown` | 使用 `lexical` 验证器的 `memfalsify_audit` |
| `memlab://dataset/{+dataset}/state{?as_of}` | `text/markdown` | `orgmemory_replay` |
| `memlab://dataset/{+dataset}/audit{?as_of,stale_days}` | `text/markdown` | `orgmemory_audit` |
| `memlab://dataset/{+dataset}/explain/{subject}{?as_of}` | `text/markdown` | `orgmemory_explain` |

以 `--root examples` 为例：`memlab://memory/memfalsify/MEMORY.md/claims`、`memlab://memory/memfalsify/MEMORY.md/audit?evidence_dir=memfalsify%2Fevidence&as_of=2026-10-10`、`memlab://dataset/orgmemory/feature-a/state` 和 `memlab://dataset/orgmemory/github-acme-web/explain/acme%2Fweb%2312`。

- 路径相对于 `--root`。在服务器自己的检查之前，SDK 默认的资源安全检查会拒绝每个路径（`memory_file`、`dataset`、`evidence_dir`、`reviews_file`）里的绝对路径、离开根目录的 `..` 和 NUL 字符。之后再做和工具参数相同的检查，包括符号链接检查和受限读取。`subject`、`as_of` 和 `stale_days` 不是路径，由工具自己的检查和错误信息处理。
- 查询参数都是可选的，含义和同名的工具参数相同。未知的查询参数会被忽略。`stale_days` 必须是不小于 0 的整数。subject 要做百分号编码：空格写成 `%20`，`/` 写成 `%2F`，`#` 写成 `%23`。
- `memlab://memory/.../audit` 一律使用离线的 `lexical` 验证器，即使服务器启动时加了 `--allow-model-verifiers` 也一样，因为客户端可能在模型没有要求的情况下读取资源。claims 是 JSON。其余四个资源只有 Markdown。没有 `exclude`、`verifier` 和 `format`，也没有 diff、history 和 rollback 的资源：这些请用工具。
- 读取失败时返回 JSON-RPC 错误：URI 未知、路径被拒绝或输入无效时是 `-32602`，读取无法完成时是 `-32603`。错误信息就是工具会返回的那条，或者一条固定的信息。URI 本身不会被回显，也不会写进日志。

## 提示词

提示词是固定的指令，客户端可以把它们作为命令提供给用户。每个提示词返回一条用户消息，引导 Agent 用上面的工具走完技能里一个“先出报告”的流程。

| 提示词 | 参数 | 流程 |
|---|---|---|
| `audit_memory` | `memory_file`，可选 `evidence_dir`、`reviews_file`（必须同时提供 `evidence_dir`）、`as_of` | 用 `lexical` 验证器运行 `memfalsify_audit`，主动证伪有风险的记忆，先报告需要关注的记忆，附上引文和 `path:line`。没有 `evidence_dir` 时，Agent 会先问用户信任哪些证据。 |
| `review_memory_changes` | `memory_file`、`base`，可选 `head`、`evidence_dir` | 运行 `memfalsify_diff`，复核新增和修改的记忆，指出被删除的记忆，并说明未变的记忆没有重新检查。用户问某条记忆从哪里来时用 `memfalsify_history`；`memfalsify_rollback` 只用来展示补丁。 |
| `explain_status` | `dataset`、`subject`，可选 `as_of` | 先运行 `orgmemory_validate`，再运行 `orgmemory_explain`，回答当前状态、授权决定及其证据、待审批、风险和被忽略的越权尝试。 |

消息把参数写成代码块里的一行 JSON，控制字符、双向文本字符和其他不可见字符都会转义，因此参数无法另起一行，也无法藏起文字。提示词不读取文件，也不嵌入文件内容。提示词不会让 Agent 写文件、应用补丁、记录人工复核或追加事件：每个提示词都要求 Agent 报告之后就停下，并说明这类修改需要用户明确批准。工具引用的文本仍然不可信；Agent 被要求遇到工具报错时如实报告并停下，而不是绕过去。

在返回任何文本之前会先检查参数。路径必须在根目录内且存在，检查方式和工具相同。修订版本必须非空，不能以 `-` 开头，也不能包含控制字符。提示词不会把它解析成提交，也不要求该文件位于 git 仓库中；`memfalsify_diff` 运行时才会解析。`as_of` 必须是日期（`audit_memory`）或带时区的 ISO 8601 时间（`explain_status`）。未知的提示词，以及缺少、未知或无效的参数，都返回 JSON-RPC 错误（`-32602`）。路径、修订版本和日期的错误信息与工具相同，从不回显根目录之外的路径。服务器不会记录这些错误。

## 安装

在本仓库的检出目录里，把本地的包放在同一条命令里安装，这样 `memlab-core` 会解析到 `packages/memcore`，而不是从索引里下载：

```bash
pip install -e packages/memcore -e packages/memfalsify -e packages/orgmemory -e packages/memlab-mcp
```

`./scripts/setup-local.sh` 会做这次安装，然后跑测试。只有在 `memlab-core`、`memfalsify` 和 `orgmemory` 已经装好时，单独的 `pip install -e packages/memlab-mcp` 才够用。

控制台脚本的名字是 `memlab-mcp`。

## 运行

```bash
memlab-mcp --root /absolute/path/to/data
```

进程在标准输入和标准输出上使用 MCP。`--root` 是工具唯一可以读取的目录。在本仓库里：

```bash
memlab-mcp --root examples
```

这时工具参数写成 `memfalsify/MEMORY.md`、`memfalsify/evidence`、`orgmemory/feature-a` 这样的相对路径。

```bash
memlab-mcp --root /absolute/path/to/data --allow-model-verifiers
```

模型验证器从环境变量读取 `ANTHROPIC_API_KEY` 和 `TYPESAFE_API_KEY`。见 [local-setup.zh-CN.md](local-setup.zh-CN.md)。不要把 key 提交进仓库，也不要贴进聊天。

`openai` 验证器（任何兼容 OpenAI 的 Chat Completions API）只由服务器启动参数配置，工具调用无法更改：

```bash
memlab-mcp --root /absolute/path/to/data --allow-model-verifiers \
  --openai-provider ollama --openai-model <model>                       # 本地 Ollama 服务，不需要 key
memlab-mcp --root /absolute/path/to/data --allow-model-verifiers \
  --openai-provider grok --openai-model <model>                         # 读取 XAI_API_KEY
```

| 参数 | 含义 |
|---|---|
| `--openai-model` | 服务商使用的模型名。启用 `openai` 时必填，没有默认值 |
| `--openai-provider` | 预设的 base URL 和 key 变量：`openai`（默认，`OPENAI_API_KEY`）、`grok`（`XAI_API_KEY`）、`deepseek`（`DEEPSEEK_API_KEY`）、`gemini`（`GEMINI_API_KEY`）或 `ollama`（`http://localhost:11434/v1`，不需要 key） |
| `--openai-base-url` | 其他 base URL：`https://`，只有 `localhost`、`127.0.0.1`、`::1` 允许 `http://`；不能带用户名、密码、查询参数或片段 |
| `--openai-api-key-env` | 存放 key 的环境变量名，覆盖服务商的默认变量。绝不是 key 本身 |

服务器启动时检查这些参数，值不合法就以退出码 2 退出，并且不回显 URL 或变量名。没有 `--openai-model` 时，`verifier: "openai"` 的调用会以“未配置”为由被拒绝。key 变量必须在服务器进程的环境里：注册服务器时用 `-e`（Claude Code）或 `--env`（Codex）传入，或者从已设置该变量的 shell 启动客户端。每次调用的记忆和证据文本都会发到这个端点；预设、结构化输出的回退方式以及 key 如何不出现在输出里，见[本地开发说明](local-setup.zh-CN.md)。

## 注册到 Claude Code

Memory Lab 插件会替你注册这个服务器，命令是 `memlab-mcp --root ${CLAUDE_PROJECT_DIR}`，同时装上两个技能，而且从不打开模型验证器：见 [install-agents.zh-CN.md](install-agents.zh-CN.md)。需要别的根目录或 `--allow-model-verifiers` 时再手动注册，并在 `/mcp` 里关掉插件的服务器。

下面的命令与本机 `claude mcp add --help` 的输出一致。传输方式默认是 `stdio`。`--` 不能省，否则 `--root` 会被 `claude` 自己吃掉，传不到 `memlab-mcp`。

```bash
claude mcp add memlab -- /absolute/path/to/memlab-mcp --root /absolute/path/to/data
```

`/absolute/path/to/memlab-mcp` 是控制台脚本，按上面的方式安装后通常是 `.venv/bin/memlab-mcp`。Claude Code 的环境里如果没有这个虚拟环境的 `PATH`，就写绝对路径。只有当这个可执行文件已经在那份 `PATH` 上时，才把路径换成裸的 `memlab-mcp`。

同一份帮助里还有这些选项：

- `-s local`、`-s user` 或 `-s project` 决定注册信息写到哪里。默认是 `local`。
- `-e KEY=value` 给服务器进程设置环境变量。多个变量就重复这个旗标。
- `-t stdio` 选择 stdio。这已经是默认值。

Claude Desktop 是另一个应用。`claude mcp add` 不会配置它。`mcpServers` 的文件格式不在 `claude mcp add --help` 里，所以这里不写配置片段。请看 [Claude Code 文档](https://code.claude.com/docs/en/overview)。

## 注册到 Codex

下面的命令与本机 `codex mcp add --help` 的输出一致。这个服务器走 stdio，所以用 `--` 加一条命令，不要用 `--url`。

```bash
codex mcp add memlab -- /absolute/path/to/memlab-mcp --root /absolute/path/to/data
```

`--env KEY=VALUE` 给 stdio 服务器设置环境变量。多个变量就重复这个旗标。这条命令会在 `~/.codex/config.toml` 里写一张 `[mcp_servers.memlab]` 表；表的内容和 Codex 的技能目录见 [install-agents.zh-CN.md](install-agents.zh-CN.md)。其余选项见 [Codex 文档](https://developers.openai.com/codex)。
