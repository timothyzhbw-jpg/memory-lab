# 在本地开发 memory-lab

[English](local-setup.md) | 中文

## 1. 安装

需要 Git 和 Python 3.10 或更新版本。

```bash
git clone https://github.com/timothyzhbw-jpg/memory-lab && cd memory-lab
./scripts/setup-local.sh            # 或者：./scripts/setup-local.sh --minimal
source .venv/bin/activate
```

脚本会创建 `.venv`，以可编辑模式安装 `memcore`、`memfalsify`、`orgmemory` 和 `memlab-mcp`，同时装上 Claude、Jev 和 OpenAI 的 SDK（加 `--minimal` 则不装），最后跑一遍测试。可以随时重复运行，已有的 `.venv` 会被复用。如果 `python3` 低于 3.10，可以指定更新的解释器：`PYTHON=python3.12 ./scripts/setup-local.sh`。

Windows 上请在 PowerShell 里手动执行同样的步骤：

```powershell
py -3 -m venv .venv
.venv\Scripts\python -m pip install -e packages/memcore -e "packages/memfalsify[all]" -e packages/orgmemory -e packages/memlab-mcp pytest
.venv\Scripts\python -m pytest -q
```

## 2. 模型验证器的 API key

默认的 `lexical` 验证器什么都不需要。模型验证器从环境变量读取凭据：

| 验证器 | 环境变量 | 说明 |
|---|---|---|
| `llm`（Claude） | `ANTHROPIC_API_KEY` | 也可以用 `ant auth login` 登录 |
| `jev`（TypeSafe Jev） | `TYPESAFE_API_KEY` | 在 TypeSafe 控制台获取 |
| `dual`、`cascade` | 两个都要 | |
| `openai` | `--provider` 对应的变量，或 `--api-key-env` 指定的变量 | 见下文；`--provider ollama` 不需要 key |

```bash
export TYPESAFE_API_KEY=...        # 写进 shell 配置文件，或交给密钥管理工具
export ANTHROPIC_API_KEY=...
```

不要把 key 提交进仓库，也不要贴进任何聊天。key 一旦出现在不该出现的地方，就去后台作废它，重新生成一个。

### 兼容 OpenAI 的服务商（`--verifier openai`）

这个验证器使用 OpenAI Chat Completions API，所以 OpenAI 和所有提供 OpenAI 兼容端点的服务商都能用。`--openai-model` 必填：模型名称因服务商而异，也会变化，所以没有默认值。`--provider` 预设 base URL、存放 key 的环境变量和响应格式：

| `--provider` | Base URL | Key 变量 | `--openai-response-format` 默认值 |
|---|---|---|---|
| `openai`（默认） | `https://api.openai.com/v1` | `OPENAI_API_KEY` | `json_schema` |
| `grok`（xAI） | `https://api.x.ai/v1` | `XAI_API_KEY` | `json_schema` |
| `deepseek` | `https://api.deepseek.com` | `DEEPSEEK_API_KEY` | `json_object`（[DeepSeek 只支持 `text` 和 `json_object`](https://api-docs.deepseek.com/api/create-chat-completion)） |
| `gemini` | `https://generativelanguage.googleapis.com/v1beta/openai/`（[Google 的 OpenAI 兼容端点](https://ai.google.dev/gemini-api/docs/openai)） | `GEMINI_API_KEY` | `json_schema` |
| `ollama` | `http://localhost:11434/v1` | 无 | `json_schema` |
| 任意服务商加 `--base-url` | 你给的 URL | 服务商的变量，或 `--api-key-env` | `json_schema` |

```bash
export XAI_API_KEY=...
memfalsify audit MEMORY.md --evidence docs --verifier openai --provider grok --openai-model <model>

# 本地模型：数据不离开本机，也不需要 key
memfalsify audit MEMORY.md --evidence docs --verifier openai --provider ollama --openai-model <model>

# 其他兼容 OpenAI 的服务，key 放在你自己命名的变量里
export GATEWAY_KEY=...
memfalsify audit MEMORY.md --evidence docs --verifier openai --base-url https://llm.example.com/v1 \
  --api-key-env GATEWAY_KEY --openai-model <model>
```

- `--base-url` 和 `--api-key-env` 会覆盖预设。base URL 必须是 `https://`；只有 `localhost`、`127.0.0.1` 和 `::1` 允许明文 `http://`；带用户名、密码、查询参数或片段的 URL 会被拒绝。
- `--api-key-env` 接收的是变量**名**。没有直接传 key 的选项，所以 key 不会出现在 shell 历史或进程列表里。key 只能包含真实 key 使用的字符（字母、数字和 `. _ ~ + / = -`）。
- 端点只由这些选项决定。OpenAI SDK 自己的环境变量（`OPENAI_BASE_URL`、`OPENAI_ORG_ID`、`OPENAI_PROJECT_ID`、`OPENAI_CUSTOM_HEADERS`）不会生效，给一个服务商的设置不会被发给另一个。环境里的代理变量（`HTTP_PROXY`、`HTTPS_PROXY`、`ALL_PROXY`、`NO_PROXY`）同样不生效：请求直接发到 base URL，本地服务也一样。重定向不会被跟随，审计以退出码 2 结束。
- 证书按 OpenAI SDK 和 httpx2 自带的方式校验，即平台的信任库（`truststore`）：macOS 和 Windows 上是操作系统的信任库，Linux 上是 OpenSSL 的，OpenSSL 会读取 `SSL_CERT_FILE` 和 `SSL_CERT_DIR`（这是你自己的配置，Nix 和很多容器都依赖它）。明文 `http://` 的本地服务不涉及证书。
- 记忆和证据片段会发送到配置的端点（本地服务除外）。只对允许发往该服务商的材料使用它。
- `--openai-response-format` 决定让模型以什么格式回答：`json_schema`（用判断的 schema 做严格结构化输出）、`json_object`（任意 JSON 对象）或 `text`（不发 `response_format`，提示里仍然要求同样的 JSON 对象）。默认值见上表。不会自动换格式重试：端点不支持该格式时会返回 HTTP 400 或 422，审计以退出码 2 结束，错误信息会建议更简单的格式（先 `json_object`，再 `text`）。
- 只读取正常结束（`finish_reason` 为 `stop`）的回答：被截断（`length`）、被过滤或其他未正常结束的回答、格式不对的响应，以及不是合法 JSON 判断的回答，一律记为无判断（`unrelated`），绝不会算作支持。
- key 要避开的是服务商或模型能控制的一切内容。服务商的错误只显示 HTTP 状态码、错误的 type、code 和 param（各自必须是简单的标记），以及一条固定提示（例如“OPENAI_API_KEY 里的 key 被拒绝”的英文提示）；服务商自己的消息从不输出。模型的理由是报告里唯一的自由文本：其中的 key（原样、百分号编码或 JSON 转义的形式）会被替换为 `[redacted]`。设置 `OPENAI_LOG=debug` 时，SDK 和 HTTP 库的日志消息、异常和调用栈也按同样方式遮掉。同一个 Python 进程里运行的其他代码被视为可信：它本来就能读取环境变量；自己把 key 写进日志、或替换日志记录工厂的库，不在 MemFalsify 防御的范围内。
- 这个验证器不支持 `--batch`（退出码 2）。`dual` 和 `cascade` 仍然是 Claude 配 Jev。

## 3. 模型审计的成本

每个模型请求只判断一条记忆和一个证据句子。请求数在发送之前就有上限：

| `--verifier` | 每次审计最多发出的请求数 |
|---|---|
| `llm` | 记忆条数 × `--max-judged`（默认 8），发给 Claude |
| `jev` | 同样的数量，发给 Jev |
| `dual` | 两个后端各发这么多 |
| `cascade` | 这么多发给 Jev，再加上 Jev 置信度低于 `--accept-at` 的每个答案各一个 Claude 请求 |
| `openai` | 记忆条数 × `--max-judged`，发给配置的端点 |

记忆与证据句子的共同词语较少时（`--min-coverage`），实际请求会更少；`diff` 只判断新增和修改的记忆。使用 `--batch` 时，在支持拒答回退的模型上，被 Claude 拒答的请求会带着回退再直接发送一次。

每份报告都会写明实际用量：每个后端一行“API usage”（JSON 中的 `verifier_stats`），包括请求数、其中经批处理发送的数量、输入和输出 token 数，以及 Claude 写入和读取提示缓存的 token 数。`openai` 的输入 token 数是服务商报告的 `prompt_tokens`（含服务商自己的缓存部分），缓存计数为空。SDK 在遇到限流或服务端错误后自动重试的请求不计入，以错误结束的请求也不计入。要换算成费用，请使用 [Anthropic 定价页](https://platform.claude.com/docs/en/about-claude/pricing)上的最新价格；Jev 由 TypeSafe AI 计费。本仓库不写具体价格，因为价格会变。

- `--batch` 让 Claude 请求按标准价格的 50% 计费，但一个批次最长可能要 24 小时（多数在一小时内完成）。
- 只有缓存前缀达到模型的最低可缓存长度时，提示缓存才有用。MemFalsify 的系统提示只有几百个 token，所以记忆较短时缓存计数一直是 0，费用不变。
- `--max-concurrency`（默认 4）缩短的是等待时间。请求集合不变；缓存 token 数（因此在前缀被缓存时的费用）以该次运行的 API 报告为准。它在 `--batch` 下不使用。请控制在你的速率限制之内。

## 4. 跑评测

评测在合并之前位于 `eval-action` 分支，见该分支的 `HANDOFF.md`。

```bash
python benchmarks/memfalsify/run.py                      # 词法验证，离线
python benchmarks/memfalsify/run.py --verifier jev       # 需要 TYPESAFE_API_KEY
python benchmarks/memfalsify/run.py --verifier cascade   # 先问 Jev，没把握时才问 Claude
python benchmarks/memfalsify/run.py --verifier dual --json results-dual.json
python benchmarks/memfalsify/run.py --verifier openai --provider ollama --openai-model <model>   # 本地，不需要 key
```

[实测词法评测](../benchmarks/memfalsify/RESULTS.md)对 139 条记忆调用了 136 次 `judge()`。模型验证器使用不同的预筛选参数，每条记忆最多判断 8 个片段；实际调用次数须读取评测输出的 Cost 行或 JSON，不能用词法次数推算。评测只统计 `judge()` 调用次数，不统计 token；要看 token 数，用 `memfalsify audit` 审计某个案例，读报告里的 API usage 行。可以用 `--case <id>` 只跑一部分。[评测说明](../benchmarks/memfalsify/README.md)解释了各项指标。

## 5. 继续用 Agent 开发

项目说明写在 `AGENTS.md` 里。Codex 直接读这个文件，`CLAUDE.md` 也会引入它，所以两边拿到的规则是同一份。

- **Claude Code**：在仓库目录里运行 `claude`。想在手机或网页上的 Claude 应用里操作这个本地会话，就在目录里改为运行 `claude remote-control`。
- **Codex**：在仓库目录里运行 `codex`。

云端会话通过 `.claude/hooks/session-start.sh` 安装依赖；在本地，这件事由 `scripts/setup-local.sh` 完成。

想在你的其他项目里使用这些技能和 MCP 工具，就把它们装进 Agent：见 [install-agents.zh-CN.md](install-agents.zh-CN.md)。
