# Install Memory Lab into Claude Code and Codex

English | [中文](install-agents.zh-CN.md)

Memory Lab gives an agent two skills, [`memfalsify`](../skills/memfalsify/SKILL.md) and [`orgmemory`](../skills/orgmemory/SKILL.md), and the read-only [`memlab` MCP server](mcp.md). For Claude Code this repository is a plugin marketplace: one install adds both skills and the server. For Codex you copy the skills and register the server with one command each.

Neither path installs Python packages. The skills run `memfalsify` and `orgmemory`, and the server is the `memlab-mcp` command, so install those first.

## 1. Install the commands

Clone the repository and set it up:

```bash
git clone https://github.com/timothyzhbw-jpg/memory-lab.git
cd memory-lab
./scripts/setup-local.sh
source .venv/bin/activate
```

`./scripts/setup-local.sh` installs the four packages into `.venv` and runs the tests ([local-setup.md](local-setup.md)). The agent finds `memfalsify`, `orgmemory` and `memlab-mcp` through the `PATH` of the shell it starts in, so start `claude` or `codex` from a shell where the virtualenv is active. Check with `memlab-mcp --help` and `memfalsify --help`.

## 2. Claude Code: install the plugin

```bash
claude plugin marketplace add timothyzhbw-jpg/memory-lab
claude plugin install memory-lab@memory-lab
```

The first command registers this repository as the `memory-lab` marketplace in your user settings. The second installs the `memory-lab` plugin from it at user scope. Inside a session, `/plugin marketplace add timothyzhbw-jpg/memory-lab` registers the marketplace, and `/plugin install memory-lab@memory-lab` opens the plugin's details so you can review it and choose a scope; it does not install by itself. From the shell, Claude Code v2.1.292 or later also accepts both steps as one command, `claude plugin install memory-lab --marketplace timothyzhbw-jpg/memory-lab`.

To install from your checkout instead of GitHub, pass its path: `claude plugin marketplace add ./`, run from the checkout. Claude Code then reads the plugin in place, so edits to the skills apply at the next session or after `/reload-plugins`.

To share the plugin with everyone who works in a repository, run both commands there with `--scope project` and commit the `.claude/settings.json` they write. The plugin source is a relative path, so after a teammate accepts the workspace trust dialog for that folder, Claude Code loads the plugin from the marketplace copy and does not need a separate install record. Each teammate still needs the commands from step 1. Until they trust the folder, Claude Code ignores the marketplace entry silently.

### What the plugin adds

| Component | In Claude Code |
|---|---|
| `skills/memfalsify` | `/memory-lab:memfalsify` |
| `skills/orgmemory` | `/memory-lab:orgmemory` |
| `memlab` MCP server | `plugin:memory-lab:memlab`, with tools named `mcp__plugin_memory-lab_memlab__<tool>`, for example `mcp__plugin_memory-lab_memlab__memfalsify_audit` |

The plugin starts the server as `memlab-mcp --root ${CLAUDE_PROJECT_DIR}`. Claude Code replaces `${CLAUDE_PROJECT_DIR}` with the project root, so tool paths are relative to the project and nothing outside it can be read. The plugin never passes `--allow-model-verifiers`, so `memfalsify_audit` and `memfalsify_diff` refuse `llm`, `jev`, `dual` and `cascade`. Use the CLIs for those, or register a server of your own as in [mcp.md](mcp.md) and toggle the plugin's server off in `/mcp`, so that Claude Code does not connect two servers. The plugin has no hooks, commands, agents or settings. The manifests are [`.claude-plugin/marketplace.json`](../.claude-plugin/marketplace.json) and [`skills/.claude-plugin/plugin.json`](../skills/.claude-plugin/plugin.json).

### Check the install

```bash
claude plugin details memory-lab
claude mcp list
```

With Claude Code 2.1.288, `claude plugin details` lists `Skills (2)  memfalsify, orgmemory` and `MCP servers (1)  memlab`. `claude mcp list` reports `plugin:memory-lab:memlab: memlab-mcp --root <project directory> - ✔ Connected`. When `memlab-mcp` is not on Claude Code's `PATH`, the same line shows `✘ Failed to connect` with `Executable not found in $PATH`. Start Claude Code from a shell with the virtualenv active and run `/reload-plugins` or start a new session.

If you copied the skills into `~/.claude/skills/` before, Claude Code loads both copies, as `/memfalsify` and `/memory-lab:memfalsify`. Delete the copies. If you registered `memlab` with `claude mcp add`, either remove that registration (`claude mcp remove memlab`) or toggle the plugin's server off in `/mcp`.

### Update and remove

Claude Code does not auto-update third-party marketplaces unless you turn that on in `/plugin`. To update:

```bash
claude plugin marketplace update memory-lab
claude plugin update memory-lab@memory-lab
```

The plugin pins no version, so Claude Code versions it by commit and an update moves it to the newest commit of the marketplace. The skills name only the commands and flags of the CLIs in the same commit. Keep the plugin and your installed packages at the same commit, or add the marketplace pinned to a branch or tag as `timothyzhbw-jpg/memory-lab#<ref>`.

To remove: `claude plugin uninstall memory-lab@memory-lab`, then `claude plugin marketplace remove memory-lab`.

### Windows

The MCP server needs POSIX `O_NOFOLLOW` and refuses to start on Windows ([mcp.md](mcp.md)). The skills still load and tell the agent to use the CLIs there. Toggle the plugin's server off in `/mcp` to stop the failed connection.

## 3. Codex: copy the skills, register the server

Codex reads personal skills from `~/.agents/skills` and repository skills from `.agents/skills` in the directories from the working directory up to the repository root. Earlier versions of the README said `~/.codex/skills`. Codex 0.162.1 still reads that directory, but OpenAI's documentation does not list it, and Codex does not merge two skills with the same name, so delete copies there first. From your checkout:

```bash
mkdir -p ~/.agents/skills
cp -R skills/memfalsify skills/orgmemory ~/.agents/skills/
```

For one repository only, copy them into that repository's `.agents/skills/` instead. Copy rather than symlink: Codex 0.162.1 follows a link to `skills/` in this checkout, finds the Claude Code plugin manifest there and lists the skill as `memory-lab:memfalsify`. Codex picks up new skills on its own; restart it if one does not appear. Run `/skills` or type `$memfalsify` to use one explicitly. Copies do not update themselves: copy again after you update the checkout.

Register the server. The command matches `codex mcp add --help`:

```bash
codex mcp add memlab -- /absolute/path/to/memory-lab/.venv/bin/memlab-mcp --root /absolute/path/to/project
```

It writes this table to `~/.codex/config.toml`, which you can also write by hand, or put in a trusted project's `.codex/config.toml`:

```toml
[mcp_servers.memlab]
command = "/absolute/path/to/memory-lab/.venv/bin/memlab-mcp"
args = ["--root", "/absolute/path/to/project"]
```

Codex has no documented placeholder for the project directory, so `--root` is one absolute path. Register the server again under another name for another root. Leave out `--allow-model-verifiers` unless you want `memfalsify_audit` and `memfalsify_diff` to send memory and evidence text to external APIs. Check with `codex mcp list` or `codex mcp get memlab --json`, and with `/mcp` in the Codex TUI.

Codex can also add this repository as a plugin marketplace, because OpenAI documents `.claude-plugin/marketplace.json` as a legacy-compatible marketplace location. That path is not supported here. In a test with Codex 0.162.1, `codex plugin add memory-lab@memory-lab` loaded the skills as `memory-lab:memfalsify` and `memory-lab:orgmemory` but passed `${CLAUDE_PROJECT_DIR}` to `memlab-mcp` unexpanded, so the server refused to start. Use the steps above.

## Sources

The manifest fields, commands and paths on this page were checked against these pages and against the `--help` output of Claude Code 2.1.288 and Codex 0.162.1:

- Claude Code: [Create a marketplace](https://code.claude.com/docs/en/plugin-marketplaces), [Marketplace reference](https://code.claude.com/docs/en/plugins/marketplace-reference), [Plugin manifest reference](https://code.claude.com/docs/en/plugins/manifest-reference), [Plugin loading reference](https://code.claude.com/docs/en/plugins/loading), [Plugin commands reference](https://code.claude.com/docs/en/plugins/cli-reference), [Install and manage plugins](https://code.claude.com/docs/en/plugins/install), [Skills](https://code.claude.com/docs/en/skills), [MCP](https://code.claude.com/docs/en/mcp)
- Codex: [Build skills](https://learn.chatgpt.com/docs/build-skills), [Model Context Protocol](https://learn.chatgpt.com/docs/extend/mcp), [Package your plugin](https://developers.openai.com/plugins/build/plugins)
