# 把 Memory Lab 装进 Claude Code 和 Codex

[English](install-agents.md) | 中文

Memory Lab 给 Agent 提供两个技能 [`memfalsify`](../skills/memfalsify/SKILL.md) 和 [`orgmemory`](../skills/orgmemory/SKILL.md)，以及只读的 [`memlab` MCP 服务器](mcp.zh-CN.md)。对 Claude Code 来说，本仓库就是一个插件市场：装一次插件，两个技能和服务器都有了。Codex 则分两步：复制技能，再用一条命令注册服务器。

两条路都不会安装 Python 包。技能调用 `memfalsify` 和 `orgmemory`，服务器就是 `memlab-mcp` 命令，所以先把它们装好。

## 1. 安装命令行工具

克隆本仓库并完成安装：

```bash
git clone https://github.com/timothyzhbw-jpg/memory-lab.git
cd memory-lab
./scripts/setup-local.sh
source .venv/bin/activate
```

`./scripts/setup-local.sh` 把四个包装进 `.venv` 并运行测试（见 [local-setup.zh-CN.md](local-setup.zh-CN.md)）。Agent 通过启动它的那个 shell 的 `PATH` 找到 `memfalsify`、`orgmemory` 和 `memlab-mcp`，所以要在已激活虚拟环境的 shell 里启动 `claude` 或 `codex`。用 `memlab-mcp --help` 和 `memfalsify --help` 检查一下。

## 2. Claude Code：安装插件

```bash
claude plugin marketplace add timothyzhbw-jpg/memory-lab
claude plugin install memory-lab@memory-lab
```

第一条命令把本仓库登记为名叫 `memory-lab` 的插件市场，写进你的用户设置。第二条从这个市场安装 `memory-lab` 插件。在会话里，`/plugin marketplace add timothyzhbw-jpg/memory-lab` 和 `/plugin install memory-lab@memory-lab` 效果相同。Claude Code v2.1.292 及以上版本还可以一条命令完成两步：`claude plugin install memory-lab --marketplace timothyzhbw-jpg/memory-lab`。

想从本地检出目录而不是 GitHub 安装，就传它的路径：在检出目录里运行 `claude plugin marketplace add ./`。这时 Claude Code 直接读取目录里的插件，改了技能之后，下一个会话或执行 `/reload-plugins` 后生效。

要让一个仓库的所有协作者都用上，在那个仓库里给两条命令都加上 `--scope project`，再提交它们写出的 `.claude/settings.json`。每个协作者仍然需要第 1 步的命令。

### 插件包含什么

| 组件 | 在 Claude Code 里 |
|---|---|
| `skills/memfalsify` | `/memory-lab:memfalsify` |
| `skills/orgmemory` | `/memory-lab:orgmemory` |
| `memlab` MCP 服务器 | `plugin:memory-lab:memlab`，工具名形如 `mcp__plugin_memory-lab_memlab__<tool>`，例如 `mcp__plugin_memory-lab_memlab__memfalsify_audit` |

插件用 `memlab-mcp --root ${CLAUDE_PROJECT_DIR}` 启动服务器。Claude Code 会把 `${CLAUDE_PROJECT_DIR}` 换成项目根目录，所以工具路径相对于项目，项目外的文件一律读不到。插件从不传 `--allow-model-verifiers`，因此 `memfalsify_audit` 和 `memfalsify_diff` 会拒绝 `llm`、`jev`、`dual` 和 `cascade`。需要这些验证器时请用命令行，或者按 [mcp.zh-CN.md](mcp.zh-CN.md) 自己注册一个服务器，并在 `/mcp` 里关掉插件的服务器，免得 Claude Code 同时连两个。插件没有钩子、命令、子代理或设置。清单文件是 [`.claude-plugin/marketplace.json`](../.claude-plugin/marketplace.json) 和 [`skills/.claude-plugin/plugin.json`](../skills/.claude-plugin/plugin.json)。

### 检查安装

```bash
claude plugin details memory-lab
claude mcp list
```

在 Claude Code 2.1.288 上，`claude plugin details` 列出 `Skills (2)  memfalsify, orgmemory` 和 `MCP servers (1)  memlab`。`claude mcp list` 显示 `plugin:memory-lab:memlab: memlab-mcp --root <项目目录> - ✔ Connected`。如果 Claude Code 的 `PATH` 上没有 `memlab-mcp`，这一行会显示 `✘ Failed to connect` 和 `Executable not found in $PATH`。请在已激活虚拟环境的 shell 里启动 Claude Code，然后执行 `/reload-plugins` 或开一个新会话。

如果之前把技能复制到了 `~/.claude/skills/`，Claude Code 会同时加载两份，分别是 `/memfalsify` 和 `/memory-lab:memfalsify`。把复制的那份删掉。如果之前用 `claude mcp add` 注册过 `memlab`，要么删掉那份注册（`claude mcp remove memlab`），要么在 `/mcp` 里关掉插件的服务器。

### 更新和卸载

除非你在 `/plugin` 里打开自动更新，Claude Code 不会自动更新第三方插件市场。手动更新：

```bash
claude plugin marketplace update memory-lab
claude plugin update memory-lab@memory-lab
```

插件没有固定版本号，Claude Code 按提交给它定版本，更新会把它移到插件市场的最新提交。技能里提到的命令和旗标只来自同一个提交里的命令行工具。让插件和已安装的包保持在同一个提交上，或者用 `timothyzhbw-jpg/memory-lab#<ref>` 把插件市场固定到某个分支或标签。

卸载：先 `claude plugin uninstall memory-lab@memory-lab`，再 `claude plugin marketplace remove memory-lab`。

### Windows

MCP 服务器需要 POSIX 的 `O_NOFOLLOW`，在 Windows 上拒绝启动（见 [mcp.zh-CN.md](mcp.zh-CN.md)）。技能照常加载，并会让 Agent 在 Windows 上改用命令行。在 `/mcp` 里关掉插件的服务器，就不会再看到连接失败。

## 3. Codex：复制技能，注册服务器

Codex 从 `~/.agents/skills` 读取个人技能，从当前工作目录一直到仓库根目录的各级 `.agents/skills` 读取仓库技能。本仓库 README 以前写的是 `~/.codex/skills`。Codex 0.162.1 仍会读取这个目录，但 OpenAI 的文档没有列出它，而且 Codex 不会合并同名技能，所以先删掉那里的副本。在检出目录里：

```bash
mkdir -p ~/.agents/skills
cp -R skills/memfalsify skills/orgmemory ~/.agents/skills/
```

只想在某一个仓库里用，就复制到那个仓库的 `.agents/skills/`。请复制，不要建符号链接：Codex 0.162.1 会顺着链接找到本检出目录里的 `skills/`，发现那里的 Claude Code 插件清单，于是把技能列成 `memory-lab:memfalsify`。Codex 会自动发现新技能；没出现就重启 Codex。用 `/skills` 或输入 `$memfalsify` 显式调用。复制的技能不会自己更新：更新检出目录后要重新复制。

注册服务器。命令与 `codex mcp add --help` 一致：

```bash
codex mcp add memlab -- /absolute/path/to/memory-lab/.venv/bin/memlab-mcp --root /absolute/path/to/project
```

它把下面这张表写进 `~/.codex/config.toml`。你也可以手写这张表，或者放进受信任项目的 `.codex/config.toml`：

```toml
[mcp_servers.memlab]
command = "/absolute/path/to/memory-lab/.venv/bin/memlab-mcp"
args = ["--root", "/absolute/path/to/project"]
```

Codex 没有文档化的项目目录占位符，所以 `--root` 是一个绝对路径。换一个根目录，就用另一个名字再注册一次。除非你希望 `memfalsify_audit` 和 `memfalsify_diff` 把记忆和证据文本发给外部 API，否则不要加 `--allow-model-verifiers`。用 `codex mcp list`、`codex mcp get memlab --json` 或 Codex TUI 里的 `/mcp` 检查。

Codex 也能把本仓库加成插件市场，因为 OpenAI 把 `.claude-plugin/marketplace.json` 列为兼容旧格式的插件市场位置。本仓库不支持这条路。在 Codex 0.162.1 上测试时，`codex plugin add memory-lab@memory-lab` 把技能加载成 `memory-lab:memfalsify` 和 `memory-lab:orgmemory`，但把 `${CLAUDE_PROJECT_DIR}` 原样传给了 `memlab-mcp`，服务器因此拒绝启动。请用上面的步骤。

## 来源

本页的清单字段、命令和路径都对照过下列页面，以及 Claude Code 2.1.288 和 Codex 0.162.1 的 `--help` 输出：

- Claude Code：[Create a marketplace](https://code.claude.com/docs/en/plugin-marketplaces)、[Marketplace reference](https://code.claude.com/docs/en/plugins/marketplace-reference)、[Plugin manifest reference](https://code.claude.com/docs/en/plugins/manifest-reference)、[Plugin loading reference](https://code.claude.com/docs/en/plugins/loading)、[Plugin commands reference](https://code.claude.com/docs/en/plugins/cli-reference)、[Install and manage plugins](https://code.claude.com/docs/en/plugins/install)、[Skills](https://code.claude.com/docs/en/skills)、[MCP](https://code.claude.com/docs/en/mcp)
- Codex：[Build skills](https://learn.chatgpt.com/docs/build-skills)、[Model Context Protocol](https://learn.chatgpt.com/docs/extend/mcp)、[Package your plugin](https://developers.openai.com/plugins/build/plugins)
